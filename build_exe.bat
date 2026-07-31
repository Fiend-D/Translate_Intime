@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [1/3] Creating Python 3.12 virtual environment...
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv
    ) else (
        py -3.12 -m venv .venv
    )
    if errorlevel 1 goto :failed
)

echo [2/3] Installing or checking build dependencies...
echo [3/3] Building dist\SoundFerry.exe...
"%VENV_PY%" build.py --clean --install-deps %*
if errorlevel 1 goto :failed

echo.
echo Build succeeded. Output: dist\SoundFerry.exe
pause
exit /b 0

:failed
echo.
echo Build failed. Review the error output above.
pause
exit /b 1
