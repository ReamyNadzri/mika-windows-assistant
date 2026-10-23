@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0personal-agent\scripts\Manage.ps1" Stop
if errorlevel 1 pause
