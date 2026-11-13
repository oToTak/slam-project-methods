@echo off
chcp 65001 >nul
title SLAM - START (ne zakryvay vikno! Zupynka: odyn raz Ctrl+C)
rem ONE launcher for BOTH PCs: runs ~/slam-runner/scripts/start.sh in WSL
rem (update code from GitHub main, check installation, show what is done, run the queue).
rem Settings of this PC live in WSL: ~/.config/slam/runner.conf. This file never changes: copy it anywhere.
wsl -d Ubuntu-22.04 --cd ~ -- test -f slam-runner/scripts/start.sh
if errorlevel 1 goto noclone
wsl -d Ubuntu-22.04 --cd ~ -- bash slam-runner/scripts/start.sh %*
echo.
echo === Cherga zupynena. Mozhna zakryty vikno. Shchob prodovzhyty - znovu START.bat ===
pause
exit /b 0
:noclone
echo.
echo !! U WSL nema ~/slam-runner: odyn raz zroby perekhid, dyvys scripts/RUNBOOK.md, rozdil "Pershyi raz".
pause
exit /b 1
