param(
    [switch]$Integration,
    [switch]$Live,
    [switch]$Smoke
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot "backend\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment is missing. Create backend\.venv and install .[dev] first."
}

Push-Location (Join-Path $projectRoot "backend")
try {
    & $python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { throw "Backend Ruff checks failed." }

    & $python -m pytest -q -m "not integration and not live"
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }

    if ($Integration) {
        & $python -m pytest -m integration
        if ($LASTEXITCODE -ne 0) { throw "Integration tests failed." }
    }
    if ($Live) {
        & $python -m pytest -m live
        if ($LASTEXITCODE -ne 0) { throw "Live tests failed." }
    }
} finally {
    Pop-Location
}

Push-Location $projectRoot
try {
    & npm.cmd run lint
    if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }

    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed." }

    if ($Smoke) {
        $expectedServices = @(
            "mysql",
            "redis-runtime",
            "redis-cache",
            "qdrant",
            "minio",
            "api",
            "worker",
            "scheduler",
            "frontend"
        )
        $runningServices = @(docker compose ps --services --status running)
        if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Docker Compose." }
        $missing = @($expectedServices | Where-Object { $_ -notin $runningServices })
        if ($missing.Count -gt 0) {
            throw "Docker services are not running: $($missing -join ', ')"
        }
        $health = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health" -TimeoutSec 10
        $readiness = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/readiness" -TimeoutSec 20
        if ($health.status -ne "ok") { throw "API health check failed." }
        if ($readiness.status -ne "ready") {
            throw "Core readiness is $($readiness.status): $($readiness.checks | ConvertTo-Json -Compress)"
        }
        Write-Host "Docker smoke checks passed: all 9 services are running and core readiness is ready."
    }
} finally {
    Pop-Location
}
