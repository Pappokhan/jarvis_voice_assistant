@echo off
rem Runs Jarvis with no console. The Jarvis window opens; closing it leaves Jarvis running in the system tray.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" main.py

