@echo off
REM ===================================================================
REM Menjalankan layanan kamera kios (MENCATAT ABSENSI).
REM Jika berhenti karena galat, otomatis dijalankan ulang setelah 10 detik.
REM Tutup jendela ini untuk menghentikan layanan kamera.
REM ===================================================================
title Absensi Wajah - Layanan kamera
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Runtime belum tersedia. Jalankan scripts\pasang_windows.bat dahulu.
  exit /b 1
)
:ulang
if defined ABSENSI_LAUNCHER_STOP_FILE if exist "%ABSENSI_LAUNCHER_STOP_FILE%" exit /b 0
".venv\Scripts\python.exe" -m flask --app app kiosk-run --no-preview
if defined ABSENSI_LAUNCHER_STOP_FILE if exist "%ABSENSI_LAUNCHER_STOP_FILE%" exit /b 0
echo Layanan kamera berhenti. Dijalankan ulang dalam 10 detik (tutup jendela untuk batal)...
timeout /t 10 /nobreak >nul
goto ulang
