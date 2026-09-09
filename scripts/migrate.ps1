# ============================================================
# migrate.ps1 — Alembic migration helper for Pratikshya Fashon
#
# Usage:
#   .\scripts\migrate.ps1              # runs against .env DATABASE_URL (default / AWS RDS)
#   .\scripts\migrate.ps1 -Target docker  # runs against local Docker DB
#   .\scripts\migrate.ps1 -Target docker -Command "downgrade -1"
#   .\scripts\migrate.ps1 -Command "revision --autogenerate -m 'my change'"
# ============================================================

param(
    [string]$Target  = "env",   # "env" (use .env file) | "docker" (local Docker DB)
    [string]$Command = "upgrade head"
)

$DOCKER_DB_URL = "postgresql+asyncpg://pratikshya:pratikshya%40123@localhost:5432/pratikshya_fashon"

if ($Target -eq "docker") {
    Write-Host "Docker Targeting LOCAL Docker DB (pratikshya_fashon on localhost:5432)" -ForegroundColor Cyan
    $env:DATABASE_URL = $DOCKER_DB_URL
} else {
    Write-Host "Targeting DATABASE_URL from .env" -ForegroundColor Yellow
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}

Write-Host "Running: alembic $Command`n" -ForegroundColor Green
Invoke-Expression "python -m alembic $Command"
