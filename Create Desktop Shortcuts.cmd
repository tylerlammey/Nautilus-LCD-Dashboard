@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create_shortcuts.ps1" -Desktop
if errorlevel 1 pause
