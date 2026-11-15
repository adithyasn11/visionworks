@echo off
REM VisionWorks in phone mode: the same as start_visionworks.bat, but over HTTPS, so a phone on the same Wi-Fi
REM can be paired as a camera (phone browsers only open the camera on a secure address).
REM Open https://localhost:3000 on this computer once it starts.
call "%~dp0start_visionworks.bat" phone
