@echo off
setlocal
cd /d "%~dp0"

echo Checking Docker Desktop...
docker info >nul 2>nul
if errorlevel 1 goto :docker_error

echo [1/4] Starting project stack...
docker compose up -d --build
if errorlevel 1 goto :error

echo [2/4] Copying Ryazan importer into API container...
docker compose cp scripts\import_ryazan_from_geofabrik.py api:/tmp/import_ryazan_from_geofabrik.py
if errorlevel 1 goto :error

echo [3/4] Downloading/importing real Ryazan OSM data...
docker compose exec api uv run python /tmp/import_ryazan_from_geofabrik.py
if errorlevel 1 goto :error

echo.
echo [4/4] Done.
echo Copy the OPEN= URL printed above and paste it into your browser.
echo.
pause
exit /b 0

:docker_error
echo.
echo Docker Desktop is not running or the Linux engine is not ready.
echo Start Docker Desktop, wait until it says the engine is running, then run this file again.
echo.
pause
exit /b 1

:error
echo.
echo Import failed. Copy the error output into ChatGPT.
pause
exit /b 1
