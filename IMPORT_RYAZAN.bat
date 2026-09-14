@echo off
setlocal
title Urban Development Generator - Ryazan Import

cd /d "%~dp0"

echo.
echo ==========================================
echo Urban Development Generator - Ryazan
echo ==========================================
echo.

echo [1/7] Checking Docker...
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
echo [2/7] Checking environment file...
if not exist .env (
    copy .env.example .env >nul
    if errorlevel 1 goto :error
)

rem Heal old local-only defaults that do not work from inside Docker containers.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='.env'; $c=Get-Content $p; $c=$c -replace '^DATABASE_URL=postgresql\+psycopg://urban:urban@localhost:5432/urban_generator$','DATABASE_URL=postgresql+psycopg://urban:urban@db:5432/urban_generator'; $c=$c -replace '^REDIS_URL=redis://localhost:6379/0$','REDIS_URL=redis://redis:6379/0'; if(-not($c -match '^REDIS_URL=')){$c += 'REDIS_URL=redis://redis:6379/0'}; Set-Content -Encoding ascii $p $c"
if errorlevel 1 goto :error

echo.
echo [3/7] Building and starting backend services...
echo API is started separately so a readiness problem can be diagnosed immediately.
docker compose up --build -d db redis migrate api worker
if errorlevel 1 goto :startup_error

echo.
echo [4/7] Waiting for backend readiness...
set /a READY_TRIES=0

:waitapi
curl --fail --silent http://localhost:8000/api/v1/health/ready >nul 2>&1
if not errorlevel 1 goto :api_ready
set /a READY_TRIES+=1
if %READY_TRIES% GEQ 30 goto :api_error
timeout /t 5 /nobreak >nul
goto :waitapi

:api_ready
curl --fail --silent --show-error http://localhost:8000/api/v1/health/ready
echo.
echo Backend is ready.

echo.
echo [5/7] Starting frontend...
docker compose up --build -d frontend
if errorlevel 1 goto :startup_error

echo.
echo Containers:
docker compose ps

echo.
echo [6/7] Downloading and importing real Ryazan OSM data...
echo This downloads the Geofabrik source only once; later runs reuse storage\imports.
docker compose exec -e PYTHONPATH=/app api uv run python /app/scripts/import_ryazan_from_geofabrik.py
if errorlevel 1 goto :import_error

echo.
echo [7/7] Done.
echo ==========================================
echo Ryazan import finished.
echo ==========================================
echo Copy the OPEN=http://localhost:5173/... URL printed above into your browser.
echo.
pause
exit /b 0

:api_error
echo.
echo Backend did not become ready.
echo.
echo --- Container status ---
docker compose ps
echo.
echo --- Liveness response ---
curl --silent --show-error http://localhost:8000/api/v1/health/live
echo.
echo.
echo --- Readiness response ---
curl --silent --show-error http://localhost:8000/api/v1/health/ready
echo.
echo.
echo --- API logs ---
docker compose logs --tail=160 api
echo.
echo --- Redis logs ---
docker compose logs --tail=80 redis
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

:error
echo.
echo Import failed. The useful diagnostics are printed above.
echo Copy the final error section into ChatGPT if it still fails.
echo.
pause
exit /b 1
