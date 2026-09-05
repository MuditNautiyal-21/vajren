@echo off
rem Vajren — double-click to run. Opens a real window, not a browser.
rem pythonw.exe: no console window behind the app.
title VAJREN
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\20-ui.ps1"
if errorlevel 1 pause
