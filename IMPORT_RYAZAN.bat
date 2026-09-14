@echo off
setlocal
title Urban Development Generator - Ryazan Import

cd /d "%~dp0"

echo.
echo ==========================================
echo Urban Development Generator - Ryazan
echo ==========================================
echo.

echo [1/6] Checking Docker...
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is not running.
    echo Starting Docker Desktop...
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
        start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    ) else (
        echo Docker Desktop was not found in the default location.
        echo Start Docker Desktop manually and run this file again.
        goto :error
    )

    echo Waiting for Docker...
    :waitdocker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 goto waitdocker
)

echo Docker is ready.

echo.
echo [2/6] Checking environment file...
if not exist .env (
    copy .env.example .env >nul
    if errorlevel 1 goto :error
) else (
    findstr /B /C:"DATABASE_URL=postgresql+psycopg://urban:urban@db:5432/urban_generator" .env >nul
    if errorlevel 1 goto :repair_env
    findstr /B /C:"REDIS_URL=redis://redis:6379/0" .env >nul
    if errorlevel 1 goto :repair_env
)
goto :env_ready

:repair_env
echo Existing .env is not compatible with the Docker Compose network.
echo Saving it as .env.before_ryazan_import.bak and restoring .env.example...
copy /Y .env .env.before_ryazan_import.bak >nul
if errorlevel 1 goto :error
copy /Y .env.example .env >nul
if errorlevel 1 goto :error

:env_ready
echo Environment is ready.

echo.
echo [3/6] Building and starting application...
docker compose up --build -d
if errorlevel 1 goto :compose_error

echo.
echo [4/6] Containers:
docker compose ps
if errorlevel 1 goto :compose_error

echo.
echo [5/6] Checking backend...
curl --fail --silent --show-error http://localhost:8000/api/v1/health/ready
if errorlevel 1 goto :compose_error
echo.

echo.
echo [6/6] Downloading and importing real Ryazan OSM data...
echo This downloads the Geofabrik source only once; later runs reuse storage\imports.
docker compose exec -e PYTHONPATH=/app api uv run python /app/scripts/import_ryazan_from_geofabrik.py
if errorlevel 1 goto :compose_error

echo.
echo ==========================================
echo Ryazan import finished.
echo ==========================================
echo Copy the OPEN=http://localhost:5173/... URL printed above into your browser.
echo.
pause
exit /b 0

:compose_error
echo.
echo ==========================================
echo Automatic diagnostics
echo ==========================================
echo.
echo --- docker compose ps ---
docker compose ps
echo.
echo --- API logs ---
docker compose logs --tail=200 api
echo.
echo --- migration logs ---
docker compose logs --tail=100 migrate
goto :error

:error
echo.
echo Import failed. Copy the error output into ChatGPT.
echo.
pause
exit /b 1
