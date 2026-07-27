@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    echo Virtual environment not found.
    echo Run setup_venv.ps1 first.
    pause
    exit /b 1
)
