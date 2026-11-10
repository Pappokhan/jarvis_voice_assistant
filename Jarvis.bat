@echo off
rem Voice mode with a visible console (shows what Jarvis hears and does).
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py %*
pause
