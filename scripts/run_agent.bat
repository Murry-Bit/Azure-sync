@echo off
:: Launcher for the Azure Backup Agent scheduled task.
:: Sets up the PATH and runs the agent using the virtual environment Python.

set "AGENT_ROOT=%~dp0.."
set "PATH=%PATH%;C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"

"%AGENT_ROOT%\.venv\Scripts\python.exe" "%AGENT_ROOT%\main.py" --config "%AGENT_ROOT%\config\config.yaml"
