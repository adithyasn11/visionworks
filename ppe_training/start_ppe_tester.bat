@echo off
setlocal
title VisionWorks PPE Detection Tester
cd /d "%~dp0\.."
set "PYTHON=%CD%\venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Python virtual environment not found: %PYTHON%
    pause
    exit /b 1
)
"%PYTHON%" "%CD%\ppe_training\test_ppe_gui.py"
if errorlevel 1 (
    echo [ERROR] The PPE tester stopped unexpectedly.
    pause
)
