# Azure Backup Agent

A Python-based backup and synchronisation agent for Windows.  
Monitors a local folder and automatically keeps it mirrored in **Azure Blob Storage** — with retry logic, SHA-256 integrity checks, per-user isolation, and always-on Windows scheduling.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Project Structure](#project-structure)
3. [Prerequisites](#prerequisites)
4. [Azure Setup](#azure-setup)
5. [Python Setup](#python-setup)
6. [Configuration](#configuration)
7. [Running the Agent](#running-the-agent)
8. [Windows Scheduled Task (always-on)](#windows-scheduled-task)
9. [Running Tests](#running-tests)
10. [Security Model](#security-model)
11. [Extending the Agent](#extending-the-agent)

---

## Architecture

```
Local folder (watchdog)
       │  file events
       ▼
DebouncedEventQueue          ← coalesces rapid events per path
       │
       ▼  (N worker threads)
  SyncEngine
  ├─ initial_sync()          ← diff on startup (SHA-256 comparison)
  ├─ on_created_or_modified()
  ├─ on_deleted()
  └─ on_moved()
       │
       ▼
BlobStorageClient            ← retries with exponential back-off (tenacity)
       │
       ▼
Azure Blob Storage
  └─ container: {prefix}-{windows-username}
       └─ blob metadata: sha256=<hex>
```

**Key design decisions:**

| Concern | Solution |
|---|---|
| Always running | Windows Scheduled Task (at-logon, restart on failure) |
| Debounce / race | Per-path timer; only the *last* event within the quiet window is processed |
| Retries | `tenacity` exponential back-off on all Azure calls |
| Integrity | SHA-256 computed before upload, stored as blob metadata |
| User isolation | One container per Windows user; RBAC scoped to that container |
| Authentication | `DefaultAzureCredential` — no hard-coded secrets |

---

## Project Structure

```
azure-backup-agent/
├── main.py                   # Entry point
├── requirements.txt
├── pyproject.toml
├── config/
│   └── config.yaml           # Edit this before first run
├── src/
│   ├── config.py             # AppConfig dataclasses + YAML loader
│   ├── logger_setup.py       # Rotating file + console logging
│   ├── integrity.py          # SHA-256 utilities
│   ├── event_queue.py        # Debounced thread-safe event queue
│   ├── watcher.py            # watchdog wrapper → FileEvent objects
│   ├── blob_client.py        # Azure Blob Storage wrapper (with retries)
│   ├── sync_engine.py        # Upload / delete / move logic
│   └── agent.py              # Orchestrator (wires everything together)
├── tests/
│   ├── test_integrity.py
│   ├── test_event_queue.py
│   ├── test_sync_engine.py
│   └── test_config.py
└── scripts/
    ├── install_task.ps1              # Register Windows Scheduled Task
    ├── uninstall_task.ps1            # Remove the task
    └── generate_customer_sas.py      # Admin: generate SAS token per customer
```

---

## Prerequisites

- **Python 3.11+**
- **Azure subscription** with a Storage Account
- **Windows 10/11** (for the Scheduled Task scripts; the Python code itself is cross-platform)

---

## Azure Setup

### Azure Portal — Step by Step (GUI)

#### Step 1: Create a Resource Group

1. Go to [portal.azure.com](https://portal.azure.com)
2. Search for **"Resource groups"** in the top search bar
3. Click **+ Create**
4. Fill in:
   - **Subscription**: your subscription
   - **Resource group**: `rg-backup`
   - **Region**: pick one close to your customers (e.g. West Europe)
5. Click **Review + create** → **Create**

#### Step 2: Create a Storage Account

1. Search for **"Storage accounts"** in the top search bar
2. Click **+ Create**
3. Fill in:
   - **Resource group**: `rg-backup`
   - **Storage account name**: pick something unique (lowercase, no spaces), e.g. `backupstorageXYZ`
   - **Region**: same as above
   - **Performance**: Standard
   - **Redundancy**: LRS (cheapest) or GRS (safer)
4. Click **Next: Advanced**
5. **IMPORTANT**: Under "Security" find **"Allow enabling anonymous access on individual containers"** → set it to **Disabled**
6. Click **Review + create** → **Create**

#### Step 3: Enable Versioning + Soft Delete (recommended)

1. Open your new Storage Account
2. In the left menu, under **Data management**, click **Data protection**
3. Check these boxes:
   - ✅ **Enable versioning for blobs**
   - ✅ **Enable soft delete for blobs** → set to **30 days**
   - ✅ **Enable soft delete for containers** → set to **7 days**
4. Click **Save**

#### Step 4: Give yourself the right role

1. Still in your Storage Account, click **Access Control (IAM)** in the left menu
2. Click **+ Add** → **Add role assignment**
3. Search for **"Storage Blob Data Owner"** → select it → click **Next**
4. Click **+ Select members** → search for your own email → select yourself
5. Click **Review + assign**

Wait ~1-2 minutes for the role to propagate.

#### Step 5: Note your account URL

1. In your Storage Account, click **Endpoints** in the left menu (under "Settings")
2. Copy the **Blob service** URL — it looks like:
   ```
   https://backupstorageXYZ.blob.core.windows.net
   ```
   This is your `account_url` for the config.

#### Step 6: Generate customer configs

On your local machine, with Python set up:

```powershell
cd C:\Users\arsel\Projects\azure
.venv\Scripts\Activate.ps1

python scripts/generate_customer_sas.py `
  --account-name backupstorageXYZ `
  --customer-name "CustomerA"
```

The script will:
- Log you in via browser (first time)
- Create a container `backup-customera`
- Generate a SAS token
- Write a complete config file to `output/customera/config.yaml`

Send that file to the customer — they're done.

---

### Azure CLI alternative (optional)

<details>
<summary>Click to expand CLI commands</summary>

#### 1. Create a Storage Account

```powershell
az group create --name rg-backup --location westeurope

az storage account create `
  --name <your-storage-account> `
  --resource-group rg-backup `
  --sku Standard_LRS `
  --kind StorageV2 `
  --allow-blob-public-access false
```

#### 2. Enable versioning and soft-delete

```powershell
az storage account blob-service-properties update `
  --account-name <your-storage-account> `
  --resource-group rg-backup `
  --enable-versioning true `
  --enable-delete-retention true `
  --delete-retention-days 30
```

#### 3. Assign yourself Storage Blob Data Owner

```powershell
$storageId = az storage account show `
  --name <your-storage-account> `
  --resource-group rg-backup `
  --query id -o tsv

az role assignment create `
  --assignee your-email@domain.com `
  --role "Storage Blob Data Owner" `
  --scope $storageId
```

#### 4. (Optional) Restrict network access

```powershell
az storage account update `
  --name <your-storage-account> `
  --resource-group rg-backup `
  --default-action Deny `
  --bypass AzureServices
```

</details>

---

## Python Setup

```powershell
# Clone / navigate to the project folder
cd C:\Tools\azure-backup-agent

# Create a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Edit `config/config.yaml`:

```yaml
watch_folder: "C:/Users/YourName/Documents/Backup"

azure:
  account_url: "https://<your-storage-account>.blob.core.windows.net"
  container_prefix: "backup"   # container = backup-<windows-username>

sync:
  debounce_seconds: 2.0
  max_retries: 5
  retry_backoff_base: 2.0
  worker_threads: 2
  initial_sync_on_start: true

logging:
  level: "INFO"
  log_dir: "logs"
```

---

## Running the Agent

### Interactive (for testing)

```powershell
# Activate venv first
.venv\Scripts\Activate.ps1

# First run — browser popup for Azure login (token is cached afterwards)
python main.py

# Custom config path
python main.py --config C:\path\to\my-config.yaml
```

The agent will:
1. Authenticate via `DefaultAzureCredential` (interactive browser login on first run; cached token on subsequent runs).
2. Perform an initial diff-sync.
3. Watch the folder and upload/delete blobs as files change.

Press **Ctrl+C** to stop.

---

## Windows Scheduled Task

Register the agent to start automatically at logon and restart on unexpected exit:

```powershell
# Open an elevated PowerShell prompt
cd C:\Tools\azure-backup-agent\scripts

.\install_task.ps1

# Start immediately without logging off:
Start-ScheduledTask -TaskName "AzureBackupAgent"
```

To remove the task:

```powershell
.\uninstall_task.ps1
```

---

## Running Tests

```powershell
# From the project root, with the venv active
pytest -v
```

Tests cover:

| Test file | What is tested |
|---|---|
| `test_integrity.py` | SHA-256 computation, large files, mismatch detection |
| `test_event_queue.py` | Debounce coalescing, independent paths, MOVED events |
| `test_sync_engine.py` | Initial sync (new / changed / unchanged / orphan), incremental events |
| `test_config.py` | YAML loading, container name derivation, Azure naming rules |

No Azure connection is required — all Azure calls are mocked.

---

## Security Model

The agent supports two authentication modes:

### Mode A — Customer mode (SAS token)

For organisations that provide backup as a service to their customers.
The org admin generates a per-customer SAS token and container. The customer
only needs the token — no Azure login, no Azure account.

| Requirement | Implementation |
|---|---|
| No public access | `--allow-blob-public-access false` on the Storage Account |
| User isolation | One container per customer; SAS token scoped to that single container |
| No account keys on client | Customer receives a time-limited SAS token — not the storage key |
| Data integrity | SHA-256 stored as blob metadata; verifiable independently |

### Mode B — Admin mode (Azure AD)

For org-internal use or testing where the user has an Azure AD identity.

| Requirement | Implementation |
|---|---|
| No public access | `--allow-blob-public-access false` on the Storage Account |
| User isolation | One container per user; RBAC scoped to that container |
| No hard-coded secrets | `DefaultAzureCredential` — supports interactive, managed identity, env vars |
| Data integrity | SHA-256 stored as blob metadata; verifiable independently |

---

## Customer Onboarding (org admin workflow)

As the org admin, run this once per customer to create their container and SAS token:

```powershell
# From the project root, with the venv active and logged into Azure
python scripts/generate_customer_sas.py `
    --account-name  mystorageaccount `
    --customer-name "CompanyA" `
    --days 365
```

This creates a **complete, ready-to-use config file** at `output/companya/config.yaml`:

```yaml
# Azure Backup Agent — config for CompanyA
# Token expires: 2027-03-31 12:00 UTC

# watch_folder not set — defaults to ~/Documents/Backup

azure:
  account_url: "https://mystorageaccount.blob.core.windows.net"
  sas_token: "sv=2023-11-03&ss=b&srt=co&sp=rwdlac&se=2027-01-01..."
  container_name: "backup-companya"

sync:
  debounce_seconds: 2.0
  max_retries: 5
  worker_threads: 2
  initial_sync_on_start: true

logging:
  level: "INFO"
  log_dir: "logs"
```

**Send this file to the customer.** They drop it into `config/config.yaml` and run `python main.py`. Zero editing needed — the watch folder defaults to `~/Documents/Backup` and creates itself automatically.

### What happens when two customers each get a SAS token?

Each customer gets a **different** SAS token scoped to a **different** container:

| Customer | Container | SAS token scope |
|---|---|---|
| CompanyA | `backup-companya` | Read/write/delete/list on `backup-companya` only |
| CompanyB | `backup-companyb` | Read/write/delete/list on `backup-companyb` only |

- CompanyA's token **cannot** access `backup-companyb` — Azure rejects it with HTTP 403.
- CompanyB's token **cannot** access `backup-companya` — same thing.
- Neither can list other containers on the storage account.
- The tokens are completely independent: revoking one does not affect the other.

The SAS token:
- Is scoped to **only that customer's container** — they cannot see other containers
- Has a configurable expiry (default: 1 year)
- Uses a User Delegation Key (signed by Azure AD, not the account key)
- Can be revoked by rotating the delegation key or deleting the container

---

## Extending the Agent

The codebase is structured so each concern in a separate class, making extensions straightforward:

| Extension | Where to add it |
|---|---|
| Exclude rules (temp files, max size) | `SyncEngine.on_created_or_modified` / `initial_sync` |
| Restore / download command | New `RestoreEngine` class using `BlobStorageClient` |
| SQLite manifest (faster diff) | Replace `blob_client.list_blobs()` call in `SyncEngine.initial_sync` |
| Client-side encryption | Wrap file reads in `BlobStorageClient.upload_file` |
| Multi-folder profiles | Multiple `BackupAgent` instances with different configs |
| System tray UI | Additional thread calling `agent.stop()` from a tray icon |

---

## Klasse-ansvar (oversigt)

| Klasse | Fil | Ansvar |
|---|---|---|
| `AppConfig` | `src/config.py` | Loader YAML, validerer konfiguration, udleder container-navn pr. Windows-bruger |
| `BlobStorageClient` | `src/blob_client.py` | Upload / delete / list med automatisk retry (tenacity eksponentiel backoff); gemmer SHA-256 i blob-metadata |
| `DebouncedEventQueue` | `src/event_queue.py` | Coalescer hurtige events per sti via per-sti timere; leverer kun det *seneste* event efter en stille periode |
| `FolderWatcher` | `src/watcher.py` | Watchdog-handler der oversætter OS-events til `FileEvent`-objekter |
| `SyncEngine` | `src/sync_engine.py` | Initial diff-sync ved opstart + incremental handlers for create / modify / delete / move |
| `BackupAgent` | `src/agent.py` | Orchestrator: starter watcher, N worker-tråde og håndterer graceful shutdown |

---

## Kom i gang

```powershell
# 1. Opret og aktiver et virtuelt miljø
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Installer afhængigheder
pip install -r requirements.txt

# 3. Udfyld config/config.yaml med din watch_folder og account_url

# 4. Kør agenten (første gang åbner en browser til Azure-login; token caches herefter)
python main.py

# 5. Kør tests – ingen Azure-forbindelse nødvendig (alle Azure-kald er mocket)
pytest -v

# 6. Installer som Windows Scheduled Task (kræver at PowerShell køres som administrator)
.\scripts\install_task.ps1

# Start agenten med det samme uden at logge ud:
Start-ScheduledTask -TaskName "AzureBackupAgent"
```

---

## Before you run — what you need to change

### If you are a CUSTOMER (your admin gave you a config file)

- [ ] **Install Python 3.11+** on Windows — download from [python.org](https://www.python.org/downloads/) and check "Add Python to PATH".
- [ ] **Run `pip install -r requirements.txt`** inside an activated virtual environment.
- [ ] **Drop the `config.yaml` file from your admin** into the `config/` folder — replacing the example file.
- [ ] (Optional) Change `watch_folder` in `config.yaml` if you want to back up a different folder than `~/Documents/Backup`.
- [ ] **Run `python main.py`** — done. No Azure login, no editing, no account needed.

That's it. 3 steps.

### If you are the ORG ADMIN (setting up the Azure side)

- [ ] **Create an Azure Storage Account** with `--allow-blob-public-access false`.
- [ ] **Assign yourself** at least **Storage Blob Data Owner** on the Storage Account (needed to create containers and generate delegation keys).
- [ ] **Enable blob versioning and soft-delete** (recommended — see Azure Setup above).
- [ ] For each customer, run:
  ```powershell
  python scripts/generate_customer_sas.py --account-name <name> --customer-name "CustomerX"
  ```
  This creates `output/customerx/config.yaml` — send it to the customer.
- [ ] (Optional) Install the **Windows Scheduled Task** on customer machines via `scripts\install_task.ps1`.

### You can optionally tweak

- [ ] `sync.debounce_seconds` — increase if you see excessive uploads during large file writes (default: `2.0`).
- [ ] `sync.max_retries` — how many times to retry a failed upload/delete (default: `5`).
- [ ] `sync.worker_threads` — number of parallel upload workers (default: `2`).
- [ ] `logging.level` — set to `"DEBUG"` for troubleshooting, `"WARNING"` for quieter logs.
