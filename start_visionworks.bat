@echo off
REM ===========================================================================
REM  VisionWorks - start the backend (port 8001) and the dashboard (port 3000).
REM
REM  Double-click this file, or run it from a terminal:
REM      start_visionworks.bat          HTTPS (default: a phone can be used as a camera)
REM      start_visionworks.bat http     plain HTTP, laptop only
REM
REM  The dashboard opens in your browser once it is ready.
REM  Close this window, or press Ctrl+C in it, to stop both servers.
REM ===========================================================================
setlocal
title VisionWorks
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] The Python environment was not found at venv\Scripts\python.exe
    echo         Create it first:  python -m venv venv
    echo         then install:     venv\Scripts\pip install -r backend\requirements.txt
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [SETUP] Installing the dashboard's packages ^(first run only^)...
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        echo [ERROR] npm install failed. Is Node.js installed?
        pause
        exit /b 1
    )
    popd
)

set "MODE=--https"
set "SCHEME=https"
if /i "%~1"=="http" (
    set "MODE="
    set "SCHEME=http"
)

REM Open the dashboard once the servers have had time to start (the launcher waits for both).
start "" /b cmd /c "timeout /t 25 /nobreak >nul & start "" %SCHEME%://localhost:3000"

"venv\Scripts\python.exe" run_system.py %MODE%

echo.
echo VisionWorks has stopped.
pause
endlocal
