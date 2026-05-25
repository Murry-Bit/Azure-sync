<#
.SYNOPSIS
    Removes the Azure Backup Agent Scheduled Task.

.DESCRIPTION
    Stops the running task (if active) and unregisters it from Task Scheduler.

.EXAMPLE
    .\uninstall_task.ps1
#>

#Requires -RunAsAdministrator

$TaskName = "AzureBackupAgent"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $task) {
    Write-Host "Scheduled Task '$TaskName' not found — nothing to remove."
    exit 0
}

# Stop it if currently running.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

Write-Host "Scheduled Task '$TaskName' removed successfully."
