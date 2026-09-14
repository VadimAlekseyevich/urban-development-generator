@echo off
setlocal
title Urban Development Generator - Ryazan Import

cd /d "%~dp0"

set "PBF_URL=https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
set "PBF_DIR=storage\imports"
set "PBF_PATH=%PBF_DIR%\central-fed-district-latest.osm.pbf"
set "PBF_PART=%PBF_PATH%.part"
set "CONTEXT_PATH=%PBF_DIR%\last_ryazan_context.env"

echo.
echo ==========================================
echo Urban Development Generator - Ryazan
echo ==========================================
echo.

echo [1/9] Checking Docker...
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ready

echo Docker is not running.
echo Starting Docker Desktop...
if not exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
    echo Docker Desktop was not found in the default location.
    echo Start Docker Desktop manually and run this file again.
    goto :error
)
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
echo Waiting for Docker...

:waitdocker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if errorlevel 1 goto :waitdocker

:docker_ready
echo Docker is ready.

echo.
echo [2/9] Checking environment file...
if not exist .env (
    copy .env.example .env >nul
    if errorlevel 1 goto :error
)

rem Heal old local-only defaults that do not work from inside Docker containers.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='.env'; $c=Get-Content $p; $c=$c -replace '^DATABASE_URL=postgresql\+psycopg://urban:urban@localhost:5432/urban_generator$','DATABASE_URL=postgresql+psycopg://urban:urban@db:5432/urban_generator'; $c=$c -replace '^REDIS_URL=redis://localhost:6379/0$','REDIS_URL=redis://redis:6379/0'; if(-not($c -match '^REDIS_URL=')){$c += 'REDIS_URL=redis://redis:6379/0'}; Set-Content -Encoding ascii $p $c"
if errorlevel 1 goto :error

echo.
echo [3/9] Building and starting backend services...
echo API is started separately so a readiness problem can be diagnosed immediately.
docker compose up --build -d db redis migrate api worker
if errorlevel 1 goto :startup_error

echo.
echo [4/9] Waiting for backend readiness...
set /a READY_TRIES=0

:waitapi
curl.exe --fail --silent http://localhost:8000/api/v1/health/ready >nul 2>&1
if not errorlevel 1 goto :api_ready
set /a READY_TRIES+=1
if %READY_TRIES% GEQ 30 goto :api_error
timeout /t 5 /nobreak >nul
goto :waitapi

:api_ready
curl.exe --fail --silent --show-error http://localhost:8000/api/v1/health/ready
echo.
echo Backend is ready.

echo.
echo [5/9] Starting frontend...
docker compose up --build -d frontend
if errorlevel 1 goto :startup_error

set /a FRONTEND_TRIES=0
:waitfrontend
curl.exe --fail --silent http://localhost:5173/ >nul 2>&1
if not errorlevel 1 goto :frontend_ready
set /a FRONTEND_TRIES+=1
if %FRONTEND_TRIES% GEQ 30 goto :frontend_error
timeout /t 2 /nobreak >nul
goto :waitfrontend

:frontend_ready
echo Frontend is ready.
echo.
echo Containers:
docker compose ps

echo.
echo [6/9] Preparing real Ryazan OSM source on Windows host...
if not exist "%PBF_DIR%" mkdir "%PBF_DIR%"
if errorlevel 1 goto :download_error

if exist "%PBF_PATH%" (
    for %%I in ("%PBF_PATH%") do set "PBF_SIZE=%%~zI"
    call :validate_existing_pbf
    if not errorlevel 1 goto :pbf_ready
    echo Existing PBF is too small or incomplete. Removing it.
    del /Q "%PBF_PATH%" >nul 2>&1
)

where curl.exe >nul 2>&1
if errorlevel 1 goto :curl_missing

if exist "%PBF_PART%" (
    echo Resuming previous PBF download...
) else (
    echo Downloading Geofabrik Central Federal District PBF...
    echo %PBF_URL%
)

curl.exe -L --fail --retry 8 --retry-delay 5 --retry-all-errors -C - --output "%PBF_PART%" "%PBF_URL%"
if errorlevel 1 goto :download_error

for %%I in ("%PBF_PART%") do set "PBF_SIZE=%%~zI"
call :validate_downloaded_pbf
if errorlevel 1 goto :download_error

move /Y "%PBF_PART%" "%PBF_PATH%" >nul
if errorlevel 1 goto :download_error

:pbf_ready
echo OSM source is ready:
echo %PBF_PATH%

echo.
echo [7/9] Importing real Ryazan OSM data...
echo The container reads the already-downloaded file from /app/storage/imports.
docker compose exec -e PYTHONPATH=/app api uv run python /app/scripts/import_ryazan_from_geofabrik.py
if errorlevel 1 goto :import_error

echo.
echo [8/9] Validating viewer context and opening Ryazan...
docker compose exec -e PYTHONPATH=/app api uv run python /app/scripts/ryazan_latest_context.py
if errorlevel 1 goto :viewer_error

if not exist "%CONTEXT_PATH%" (
    echo Viewer context file was not created: %CONTEXT_PATH%
    goto :viewer_error
)

for /f "usebackq tokens=1,* delims==" %%A in ("%CONTEXT_PATH%") do set "%%A=%%B"
if not defined PROJECT_ID goto :viewer_error
if not defined DATASET_VERSION_ID goto :viewer_error
if not defined OPEN_URL goto :viewer_error

echo Checking project boundary through HTTP API...
curl.exe --fail --silent --show-error "http://localhost:8000/api/v1/projects/%PROJECT_ID%/boundary/geojson" >nul
if errorlevel 1 goto :viewer_error

echo Checking a real building through HTTP API...
curl.exe --fail --silent --show-error "http://localhost:8000/api/v1/projects/%PROJECT_ID%/dataset-versions/%DATASET_VERSION_ID%/source-layers/buildings/geojson?bbox=39.50,54.50,39.95,54.75&limit=1" >nul
if errorlevel 1 goto :viewer_error

echo Viewer API preflight is OK.
echo Opening:
echo %OPEN_URL%
start "" "%OPEN_URL%"

echo.
echo [9/9] Done.
echo ==========================================
echo Ryazan import finished and viewer opened.
echo ==========================================
echo If the browser reused an old tab, refresh it with Ctrl+F5.
echo.
pause
exit /b 0

:validate_existing_pbf
if %PBF_SIZE% LSS 100000000 exit /b 1
exit /b 0

:validate_downloaded_pbf
if %PBF_SIZE% LSS 100000000 (
    echo Download finished but the file is unexpectedly small: %PBF_SIZE% bytes.
    exit /b 1
)
exit /b 0

:curl_missing
echo.
echo curl.exe was not found on Windows.
echo Install/enable the Windows curl command and run this file again.
goto :error

:download_error
echo.
echo OSM source download failed on the Windows host.
echo Partial data is kept in:
echo %PBF_PART%
echo Run this BAT again to resume the download.
goto :error

:api_error
echo.
echo Backend did not become ready.
echo.
echo --- Container status ---
docker compose ps
echo.
echo --- Liveness response ---
curl.exe --silent --show-error http://localhost:8000/api/v1/health/live
echo.
echo.
echo --- Readiness response ---
curl.exe --silent --show-error http://localhost:8000/api/v1/health/ready
echo.
echo.
echo --- API logs ---
docker compose logs --tail=160 api
echo.
echo --- Redis logs ---
docker compose logs --tail=80 redis
goto :error

:frontend_error
echo.
echo Frontend did not become ready.
docker compose ps
echo.
echo --- Frontend logs ---
docker compose logs --tail=160 frontend
goto :error

:startup_error
echo.
echo Application startup failed.
docker compose ps
echo.
echo --- Migration logs ---
docker compose logs --tail=120 migrate
echo.
echo --- API logs ---
docker compose logs --tail=160 api
goto :error

:import_error
echo.
echo Ryazan data import failed after the application became healthy.
echo --- API logs ---
docker compose logs --tail=120 api
goto :error

:viewer_error
echo.
echo Ryazan data exists, but viewer preflight failed.
echo --- Container status ---
docker compose ps
echo.
echo --- API logs ---
docker compose logs --tail=120 api
goto :error

:error
echo.
echo Import failed. The useful diagnostics are printed above.
echo Copy the final error section into ChatGPT if it still fails.
echo.
pause
exit /b 1
