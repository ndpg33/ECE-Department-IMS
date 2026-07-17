@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or newer from python.org.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
echo Installing or checking required packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"
echo.
echo ECE Storeroom IMS is running at http://127.0.0.1:8000
echo Press Ctrl+C to stop the server.
echo.
python -m uvicorn app:app --reload
exit /b 0

:error
echo.
echo Setup failed. Review the error above.
pause
exit /b 1
