@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\enable_cpu.ps1"
if errorlevel 1 pause
