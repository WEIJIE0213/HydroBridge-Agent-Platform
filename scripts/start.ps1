param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination $envFile
    Write-Host "Created .env from .env.example. Add model and Tavily keys before live-agent testing."
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "models\manifest.json"))) {
        throw "BGE models are missing. Run backend\.venv\Scripts\python.exe scripts\download-bge-models.py first."
    }
    docker info | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is not running."
    }
    if ($Build) {
        docker compose up --build -d
    } else {
        docker compose up -d
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to start the platform."
    }

    $ready = $false
    $lastStatus = "starting"
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $readiness = Invoke-RestMethod `
                -Uri "http://localhost:8000/api/v1/readiness" `
                -TimeoutSec 5
            $lastStatus = $readiness.status
            if ($readiness.status -eq "ready") {
                $ready = $true
                break
            }
        } catch {
            $lastStatus = "API starting"
        }
        Start-Sleep -Seconds 2
    }
    docker compose ps
    if (-not $ready) {
        throw "Platform did not become ready within 3 minutes. Last status: $lastStatus"
    }
    Write-Host ""
    Write-Host "HydroBridge is ready:"
    Write-Host "  Web:     http://localhost:3000"
    Write-Host "  API:     http://localhost:8000/docs"
    Write-Host "  Account: admin@hydrobridge.cn / ChangeMe123!"
} finally {
    Pop-Location
}
