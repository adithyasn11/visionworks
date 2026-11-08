@echo off
setlocal EnableExtensions
title VisionWorks Combined PPE YOLOv8 Training

cd /d "%~dp0\.."
set "ROOT=%CD%"
set "PYTHON=%ROOT%\venv\Scripts\python.exe"
set "SCRIPT=%ROOT%\ppe_training\train_ppe_combined.py"
set "DATASET=%ROOT%\ppe_training\datasets\combined12\data.yaml"

if not exist "%PYTHON%" (
    echo [ERROR] Virtual environment not found: %PYTHON%
    pause
    exit /b 1
)
if not exist "%DATASET%" (
    echo [ERROR] Combined dataset not found: %DATASET%
    pause
    exit /b 1
)

choice /M "Start combined PPE training now"
if errorlevel 2 exit /b 0

"%PYTHON%" "%SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" echo [SUCCESS] Combined training completed.
if not "%EXIT_CODE%"=="0" echo [ERROR] Training stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
