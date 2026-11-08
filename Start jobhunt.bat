@echo off
rem Double-click to start jobhunt. Your browser opens http://127.0.0.1:8000 when it's ready.
rem Close this window (or press Ctrl+C in it) to stop the site.
cd /d "%~dp0"
title jobhunt - http://127.0.0.1:8000
if not exist ".venv\Scripts\python.exe" ( echo Run setup.bat first. & pause & exit /b 1 )
".venv\Scripts\python.exe" run.py
pause
