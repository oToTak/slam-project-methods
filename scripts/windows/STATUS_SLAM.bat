@echo off
chcp 65001 >nul
title SLAM - STATUS
wsl -d Ubuntu-22.04 --cd ~/slam-local -- bash scripts/brother_status.sh
echo.
pause
