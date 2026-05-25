"""
Admin utility: generate a per-customer SAS token, container, and config file.

Run this once per customer from the org admin's machine.
The admin needs the 'Storage Blob Data Owner' role (or equivalent) so that
a User Delegation Key can be obtained.  No account keys are used.

Usage::

    python scripts/generate_customer_sas.py \\
        --account-name  mystorageaccount \\
        --customer-name customerA \\
        --days          365

Output: a complete ``config.yaml`` written to ``output/<customer>/config.yaml``
that can be sent directly to the customer.  They drop it into their
``config/`` folder and run the agent — zero editing required.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import (
    BlobServiceClient,
    ContainerSasPermissions,
    generate_container_sas,
)


def _safe_container_name(raw: str) -> str:
    name = raw.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = name.strip("-")
    return name[:63].rstrip("-") or "backup"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a SAS token + container for a customer."
    )
    parser.add_argument(
        "--account-name",
        required=True,
        help="Azure Storage Account name (not the full URL).",
    )
    parser.add_argument(
        "--customer-name",
        required=True,
        help="Customer identifier (used to derive the container name).",
    )
    parser.add_argument(
        "--container-prefix",
        default="backup",
        help="Container name prefix (default: backup).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="How many days the SAS token is valid (default: 365).",
    )
    parser.add_argument(
        "--watch-folder",
        default="",
        help="Override watch_folder in the generated config. "
             "Leave empty to use the default (~/Documents/Backup).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory where customer config files are written (default: output/).",
    )
    args = parser.parse_args()

    account_url = f"https://syncstoragev1.blob.core.windows.net/"
    container_name = f"{_safe_container_name(args.container_prefix)}-{_safe_container_name(args.customer_name)}"

    print(f"Account URL   : {account_url}")
    print(f"Container     : {container_name}")
    print()

    # Authenticate as the org admin.
    credential = DefaultAzureCredential()
    service = BlobServiceClient(account_url=account_url, credential=credential)

    # Create the container if it doesn't exist.
    container_client = service.get_container_client(container_name)
    try:
        container_client.create_container()
        print(f"Created container: {container_name}")
    except Exception:
        print(f"Container already exists: {container_name}")

    # Use a User Delegation Key to sign the SAS token.
    # This is more secure than using the account key — no shared secret on disk.
    # Requires the admin to have 'Storage Blob Data Owner' on the account.
    start_time = datetime.now(timezone.utc)
    expiry_time = start_time + timedelta(days=args.days)

    # Azure limits User Delegation Keys to a maximum of 7 days.
    # The SAS token itself can have a longer expiry — we just need the key
    # to be valid long enough to sign the token (a few seconds is fine).
    key_expiry_time = start_time + timedelta(days=7)

    user_delegation_key = service.get_user_delegation_key(
        key_start_time=start_time,
        key_expiry_time=key_expiry_time,
    )

    sas_token = generate_container_sas(
        account_name=args.account_name,
        container_name=container_name,
        user_delegation_key=user_delegation_key,
        permission=ContainerSasPermissions(
            read=True,
            write=True,
            delete=True,
            list=True,
        ),
        expiry=expiry_time,
        start=start_time,
    )

    # Build the complete config.yaml for this customer.
    watch_folder_line = (
        f'watch_folder: "{args.watch_folder}"'
        if args.watch_folder
        else '# watch_folder not set — defaults to ~/Documents/Backup'
    )

    config_content = textwrap.dedent(f"""\
        # Azure Backup Agent — config for {args.customer_name}
        # Generated: {start_time.strftime('%Y-%m-%d %H:%M UTC')}
        # Token expires: {expiry_time.strftime('%Y-%m-%d %H:%M UTC')}
        #
        # Drop this file into  config/config.yaml  and run the agent.
        # No Azure login required — the SAS token handles authentication.

        {watch_folder_line}

        azure:
          account_url: "{account_url}"
          sas_token: "{sas_token}"
          container_name: "{container_name}"

        sync:
          debounce_seconds: 2.0
          max_retries: 5
          worker_threads: 2
          initial_sync_on_start: true

        logging:
          level: "INFO"
          log_dir: "logs"
    """)

    # Write to output/<customer>/config.yaml
    out_dir = Path(args.output_dir) / _safe_container_name(args.customer_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "config.yaml"
    out_file.write_text(config_content, encoding="utf-8")

    print()
    print("=" * 60)
    print(f"Config file written to: {out_file}")
    print(f"Token valid until:      {expiry_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    print()
    print("Send this file to the customer.")
    print("They copy it to  config/config.yaml  and run:  python main.py")
    print("No other configuration needed.")
    print()


if __name__ == "__main__":
    main()
