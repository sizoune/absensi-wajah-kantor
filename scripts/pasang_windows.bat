@echo off
setlocal
cd /d "%~dp0.."
set "ABSENSI_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "ABSENSI_POWERSHELL=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
"%ABSENSI_POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0pasang_windows.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
