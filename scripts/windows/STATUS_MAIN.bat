@echo off
chcp 65001 >nul
title SLAM MAIN - STATUS
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
wsl -d Ubuntu-22.04 --cd "%REPO%" -- bash scripts/main_status.sh
echo.
pause
