@echo off
chcp 65001 >nul
title SLAM - STATUS
rem ONE status window for BOTH PCs: ~/slam-runner/scripts/start.sh --status (safe while the queue runs).
wsl -d Ubuntu-22.04 --cd ~ -- test -f slam-runner/scripts/start.sh
if errorlevel 1 goto noclone
wsl -d Ubuntu-22.04 --cd ~ -- bash slam-runner/scripts/start.sh --status
echo.
pause
exit /b 0
:noclone
echo.
echo !! U WSL nema ~/slam-runner: odyn raz zroby perekhid, dyvys scripts/RUNBOOK.md, rozdil "Pershyi raz".
pause
exit /b 1
