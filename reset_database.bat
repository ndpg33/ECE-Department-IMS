@echo off
cd /d "%~dp0"
if exist "data\ims.db" del /q "data\ims.db"
if exist "data\ims.db-shm" del /q "data\ims.db-shm"
if exist "data\ims.db-wal" del /q "data\ims.db-wal"
echo Prototype database deleted. It will be recreated with demo data next time the application starts.
pause
