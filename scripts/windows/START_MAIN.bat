@echo off
chcp 65001 >nul
title SLAM MAIN - CHERGA (ne zakryvay vikno! Zupynka: odyn raz Ctrl+C)
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
wsl -d Ubuntu-22.04 --cd "%REPO%" -- bash scripts/main_start.sh
echo.
echo === Cherga zupynena. Mozhna zakryty vikno. Shchob prodovzhyty - znovu START_MAIN.bat ===
pause
