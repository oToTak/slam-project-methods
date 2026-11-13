@echo off
chcp 65001 >nul
title SLAM - CHERGA (ne zakryvay vikno! Zupynka: odyn raz Ctrl+C)
wsl -d Ubuntu-22.04 --cd ~/slam-local -- bash scripts/brother_start.sh
echo.
echo === Cherga zupynena. Mozhna zakryty vikno. Shchob prodovzhyty - znovu START_SLAM.bat ===
pause
