@echo off
setlocal enabledelayedexpansion

if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found. Run: python -m venv .venv
    exit /b 1
)

call .venv\Scripts\activate.bat
python -m src.main %*
