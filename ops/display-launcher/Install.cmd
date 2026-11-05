@echo off
setlocal
rem Developer setup only. Staff use the desktop shortcut created by Install.ps1.
if "%~1"=="" (
  echo Usage: Install.cmd "https://YOUR-SERVICE-DOMAIN" [additional Install.ps1 options]
  echo No installation was performed. A real service URL is required.
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1" -DisplayUrl %*
exit /b %errorlevel%
