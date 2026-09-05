@echo off
rem Double-click to start Vajren and open the face.
title VAJREN
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\20-ui.ps1"
if errorlevel 1 pause
