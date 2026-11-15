@echo off
setlocal
where powershell >nul 2>nul
if errorlevel 1 (
  echo PowerShell not found.
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
  pause
  exit /b 1
)
if "%~1"=="" pause
endlocal
