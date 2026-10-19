param(
    [switch]$Offline,
    [switch]$Full
)

# Project acceptance entry (Windows PowerShell 5.1 / 7).
# Default: offline tests + static checks + compile + version + golden (needs TUSHARE_TOKEN).
# -Offline: skip golden/live checks (suitable for CI or environments without token).
# -Full: additionally run deep-mode and --force live smoke (needs cache/network/token).

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    function Invoke-Step {
        param([string]$Name, [scriptblock]$Command)
        Write-Host ""
        Write-Host "=== $Name ==="
        & $Command
        if ($LASTEXITCODE -ne 0) {
            throw "$Name failed (exit $LASTEXITCODE)"
        }
    }

    Invoke-Step "pytest (offline)" { python -m pytest -q }
    Invoke-Step "ruff F/E9" { python -m ruff check --select F,E9 src tests }
    Invoke-Step "compileall" { python -m compileall -q src tests }
    Invoke-Step "sqa version" { python -m sqa.cli --version }

    if (-not $Offline) {
        Invoke-Step "golden" { python -m sqa.cli golden }
    }
    if ($Full) {
        Invoke-Step "deep mode smoke" {
            python -m sqa.cli minesweeper 600519 --mode deep --json | Out-Null
        }
        Invoke-Step "force smoke" {
            python -m sqa.cli analyze 000725 --force --json | Out-Null
        }
        Invoke-Step "live snapshots" {
            $env:SQA_INTEGRATION = "1"
            python -m pytest -q tests/test_live_snapshots.py
            Remove-Item Env:SQA_INTEGRATION -ErrorAction SilentlyContinue
        }
    }

    Write-Host ""
    Write-Host "Acceptance passed (Offline=$Offline, Full=$Full)"
}
finally {
    Pop-Location
}
