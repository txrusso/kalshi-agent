@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"
if not exist logs mkdir logs

set "LOCKFILE=%PROJECT_ROOT%\poller.lock"

if exist "%LOCKFILE%" (
    echo %date% %time% lock file present, poller likely already running - skipping >> logs\poller_scheduler.log
    exit /b 0
)

echo running > "%LOCKFILE%"

set "STAMP=%date:~-4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "STAMP=%STAMP: =0%"
set "LOGFILE=logs\poller_%STAMP%.log"

echo %date% %time% starting poller, logging to %LOGFILE% >> logs\poller_scheduler.log
py -m uv run python -m kalshi_agent.data.poller >> "%LOGFILE%" 2>&1

del "%LOCKFILE%"
