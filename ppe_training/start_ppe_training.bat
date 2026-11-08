@echo off
setlocal EnableExtensions
title VisionWorks PPE YOLOv8 Training

cd /d "%~dp0\.."
set "ROOT=%CD%"
set "DATASET=%ROOT%\ppe_training\datasets\ppe6_roboflow\data.yaml"
set "PYTHON=%ROOT%\venv\Scripts\python.exe"
set "SCRIPT=%ROOT%\ppe_training\train_ppe.py"

echo VisionWorks PPE Detection Training

if not exist "%PYTHON%" (
    echo [ERROR] Python virtual environment not found: %PYTHON%
    pause
    exit /b 1
)
if not exist "%DATASET%" (
    echo [ERROR] Dataset configuration not found: %DATASET%
    pause
    exit /b 1
)

"%PYTHON%" "%SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
    echo [SUCCESS] Training completed.
    echo Model: %ROOT%\ppe_training\runs\ppe_yolov8m_v1\weights\best.pt
) else (
    echo [ERROR] Training stopped with exit code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
