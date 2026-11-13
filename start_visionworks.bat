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
