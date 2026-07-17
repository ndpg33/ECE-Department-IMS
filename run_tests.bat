@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The project virtual environment does not exist yet.
    echo Run run.bat once first, then run this file again.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto :error

python -m pytest -q
if errorlevel 1 goto :error

pause
exit /b 0

:error
echo.
echo Tests failed. Review the output above.
pause
exit /b 1
