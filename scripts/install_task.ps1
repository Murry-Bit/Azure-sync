<#
.SYNOPSIS
    Installs the Azure Backup Agent as a Windows Scheduled Task.

.DESCRIPTION
    Creates a per-user Scheduled Task that:
      - Starts the backup agent automatically at logon.
      - Restarts the agent up to 3 times (1-minute interval) if it exits
        unexpectedly.
      - Runs indefinitely (no execution time limit).

    The task runs as the current interactive user (no elevated token required
    for the task itself).  This script must be run elevated once to register
    the task.

.PARAMETER PythonExe
    Path to (or name of) the Python executable.
    Defaults to 'python' (resolved via PATH).

.PARAMETER AgentRoot
    Root directory of the backup agent.
    Defaults to the parent folder of this script.

.PARAMETER ConfigFile
    Path to the YAML config file.
    Defaults to '<AgentRoot>\config\config.yaml'.

.EXAMPLE
    # Run from an elevated PowerShell prompt:
    .\install_task.ps1

.EXAMPLE
    .\install_task.ps1 -AgentRoot "C:\Tools\backup-agent" `
                       -PythonExe "C:\Python311\python.exe"
#>

#Requires -RunAsAdministrator

param(
    [string]$PythonExe  = "",
    [string]$AgentRoot  = (Split-Path -Parent $PSScriptRoot),
    [string]$ConfigFile = ""
)

$TaskName = "AzureBackupAgent"

if (-not $ConfigFile) {
    $ConfigFile = Join-Path $AgentRoot "config\config.yaml"
}
$MainScript = Join-Path $AgentRoot "main.py"

# Default to the venv Python if it exists, otherwise fall back to system python.
if (-not $PythonExe) {
    $VenvPython = Join-Path $AgentRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

# ---- Validate paths -------------------------------------------------------

if (-not (Test-Path $MainScript)) {
    Write-Error "Cannot find main.py at: $MainScript"
    exit 1
}
if (-not (Test-Path $ConfigFile)) {
    Write-Error "Cannot find config file at: $ConfigFile`nCopy config\config.yaml and fill in your values first."
    exit 1
}

# ---- Build task components ------------------------------------------------

$AzCliPath = "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"
$TaskEnvPath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
               [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" +
               $AzCliPath

$Action = New-ScheduledTaskAction `
    -Execute        $PythonExe `
    -Argument       "`"$MainScript`" --config `"$ConfigFile`"" `
    -WorkingDirectory $AgentRoot

# Fire at logon for the current interactive user.
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount           3 `
    -RestartInterval        (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit     ([TimeSpan]::Zero) `
    -MultipleInstances      IgnoreNew `
    -StartWhenAvailable

# Run as current user (limited token – no UAC elevation for the agent).
$Principal = New-ScheduledTaskPrincipal `
    -UserId     $env:USERNAME `
    -LogonType  Interactive `
    -RunLevel   Limited

# ---- Register (replace existing) ------------------------------------------

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $Action `
    -Trigger    $Trigger `
    -Settings   $Settings `
    -Principal  $Principal `
    -Description "Azure Blob Storage Backup Agent - syncs local folder to cloud." `
| Out-Null

Write-Host ""
Write-Host "Scheduled Task '$TaskName' registered successfully."
Write-Host "  Agent root  : $AgentRoot"
Write-Host "  Config      : $ConfigFile"
Write-Host "  Runs as     : $env:USERNAME"
Write-Host ""
Write-Host "The agent will start automatically at your next Windows logon."
Write-Host "To start it right now without logging off, run:"
Write-Host ("    Start-ScheduledTask -TaskName '" + $TaskName + "'")
