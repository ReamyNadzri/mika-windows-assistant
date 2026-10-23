@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0personal-agent\scripts\Manage.ps1" Start
if errorlevel 1 pause
