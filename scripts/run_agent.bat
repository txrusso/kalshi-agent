@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"
if not exist logs mkdir logs

set "LOCKFILE=%PROJECT_ROOT%\agent.lock"

if exist "%LOCKFILE%" (
    echo %date% %time% lock file present, agent likely already running - skipping >> logs\agent_scheduler.log
    exit /b 0
)

echo running > "%LOCKFILE%"

set "STAMP=%date:~-4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "STAMP=%STAMP: =0%"
set "LOGFILE=logs\agent_%STAMP%.log"

echo %date% %time% starting agent, logging to %LOGFILE% >> logs\agent_scheduler.log
py -m uv run python -m kalshi_agent.orchestrator >> "%LOGFILE%" 2>&1

del "%LOCKFILE%"
