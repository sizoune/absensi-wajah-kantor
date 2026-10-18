@echo off
setlocal
title Absensi Wajah - Peluncur
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Runtime belum tersedia. Jalankan scripts\pasang_windows.bat dahulu.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m maintenance.windows_launcher
