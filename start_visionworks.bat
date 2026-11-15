@echo off
REM ===========================================================================
REM  VisionWorks - start the backend (port 8001) and the dashboard (port 3000).
REM
REM  Double-click this file, or run it from a terminal:
REM      start_visionworks.bat           normal use: fast production dashboard at http://localhost:3000
REM                                      (built automatically the first time and after code changes)
REM      start_visionworks.bat phone     HTTPS, so a phone can be paired as a camera
REM      start_visionworks.bat dev       development server (live reload while editing code)
REM
REM  The dashboard opens in your browser once it is ready.
REM  Close this window, or press Ctrl+C in it, to stop both servers.
REM ===========================================================================
setlocal
title VisionWorks
cd /d "%~dp0"

REM -- First run on this computer: set everything up (Python packages, dashboard packages, keys, models).
REM    setup.py skips whatever is already done, so a failed or interrupted setup just continues next time.
if not exist "venv\.visionworks-setup-done" (
    echo.
    echo  First run: setting VisionWorks up. This takes 10-20 minutes and downloads a few GB.
    echo.
    set "PY="
    for %%V in (3.11 3.12 3.10 3.13) do (
        if not defined PY (
            py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
        )
    )
    if not defined PY (
        python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
    )
    if not defined PY (
        echo [ERROR] Python 3.10 to 3.13 was not found.
        echo         Install Python 3.11 from https://www.python.org/downloads/
        echo         ^(tick "Add python.exe to PATH" in the installer^), then run this file again.
        pause
        exit /b 1
    )
    call %%PY%% setup.py
    if errorlevel 1 (
        echo.
        echo Setup did not finish. Fix the problem shown above, then run this file again.
        pause
        exit /b 1
    )
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

set "MODE=--prod"
set "URL=http://localhost:3000"
if /i "%~1"=="phone" (
    set "MODE=--https"
    set "URL=https://localhost:3000"
)
if /i "%~1"=="dev" (
    set "MODE="
    set "URL=http://localhost:3000"
)

REM Open the dashboard once it answers (checked every 3 s, up to 5 minutes: the first production
REM build takes about 2 minutes).
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 100;$i++){ $c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1',3000); $c.Close(); break } catch { Start-Sleep 3 } }; Start-Sleep 2; Start-Process '%URL%'"

"venv\Scripts\python.exe" run_system.py %MODE%

echo.
echo VisionWorks has stopped.
pause
endlocal
