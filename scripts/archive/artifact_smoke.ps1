# PlanGate Artifact Smoke Script (Windows PowerShell)
# Equivalent to Makefile targets; no make/bash required.
#
# Usage:
#   .\scripts\artifact_smoke.ps1 -Target smoke
#   .\scripts\artifact_smoke.ps1 -Target reproduce-core
#   .\scripts\artifact_smoke.ps1 -Target reproduce-ablation
#   .\scripts\artifact_smoke.ps1 -Target reproduce-recovery
#   .\scripts\artifact_smoke.ps1 -Target pareto-dryrun
#   .\scripts\artifact_smoke.ps1 -Target figures-from-cache
#
# On Linux/macOS/WSL2: use 'make <target>' instead.

param(
    [ValidateSet(
        "smoke",
        "test",
        "reproduce-core",
        "reproduce-ablation",
        "reproduce-recovery",
        "pareto-dryrun",
        "figures-from-cache",
        "help"
    )]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"

# Resolve repo root (two levels up from scripts/)
$Root = Split-Path $PSScriptRoot -Parent
$GatewayBin = Join-Path $Root "gateway.exe"

function Invoke-Step([string]$Msg, [scriptblock]$Block) {
    Write-Host ""
    Write-Host "==> $Msg" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        Write-Host "FAILED (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

function Invoke-Build {
    Invoke-Step "go build -o gateway.exe ./cmd/gateway" {
        Set-Location $Root
        go build -o gateway.exe ./cmd/gateway
    }
}

function Invoke-Smoke {
    Invoke-Step "go test ./... -timeout 120s" {
        Set-Location $Root
        go test ./... -timeout 120s
    }
    Write-Host ""
    Write-Host "PASSED: smoke test" -ForegroundColor Green
}

function Invoke-ReproduceCore {
    Invoke-Build
    Invoke-Step "python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1 --gateway-binary gateway.exe" {
        Set-Location $Root
        python scripts/run_all_experiments.py --exp Exp1_Core --repeats 1 --gateway-binary gateway.exe
    }
    Write-Host ""
    Write-Host "PASSED: reproduce-core" -ForegroundColor Green
    Write-Host "Sanity: plangate_full.cascade_failed should be 0; effective_goodput should be highest."
}

function Invoke-ReproduceAblation {
    Invoke-Build
    Invoke-Step "python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1 --gateway-binary gateway.exe" {
        Set-Location $Root
        python scripts/run_all_experiments.py --exp Exp4_Ablation --repeats 1 --gateway-binary gateway.exe
    }
    Write-Host ""
    Write-Host "PASSED: reproduce-ablation" -ForegroundColor Green
    Write-Host "Sanity: wo_budgetlock.effective_goodput should be ~83% lower than plangate_full."
}

function Invoke-ReproduceRecovery {
    Invoke-Step "go test ./plangate/... -run TestRuntime -v -timeout 120s" {
        Set-Location $Root
        go test ./plangate/... -run "TestRuntime" -v -timeout 120s
    }
    Write-Host ""
    Write-Host "PASSED: reproduce-recovery" -ForegroundColor Green
    Write-Host "Sanity: PlanGate-R tool calls < naive retry; completed steps not replayed."
}

function Invoke-ParetoDryrun {
    Invoke-Step "python scripts/run_pareto_frontier.py --selected --dry-run" {
        Set-Location $Root
        python scripts/run_pareto_frontier.py --selected --dry-run
    }
    Write-Host ""
    Write-Host "PASSED: pareto-dryrun (8 configs printed, no experiments run)" -ForegroundColor Green
}

function Invoke-FiguresFromCache {
    $CacheDir = Join-Path $Root "artifact_cache"
    if (-not (Test-Path $CacheDir)) {
        Write-Host ""
        Write-Host "ERROR: artifact_cache/ not found." -ForegroundColor Red
        Write-Host "No cached paper-result package found in this public repository." -ForegroundColor Red
        Write-Host "Unpack the conference supplementary artifact to artifact_cache/ first." -ForegroundColor Red
        Write-Host ""
        exit 1
    }
    Invoke-Step "python scripts/gen_paper_figures.py --cache-dir artifact_cache" {
        Set-Location $Root
        python scripts/gen_paper_figures.py --cache-dir artifact_cache
    }
    Write-Host ""
    Write-Host "PASSED: figures-from-cache" -ForegroundColor Green
}

function Show-Help {
    Write-Host @"

PlanGate artifact reproduction script (Windows PowerShell)
Equivalent to Makefile targets.

Usage: .\scripts\artifact_smoke.ps1 [-Target <target>]

Targets (no API key required):
  smoke               Go unit tests (< 1 min)
  test                Alias for smoke
  reproduce-core      Exp1_Core mock smoke (repeats=1, ~2 min)
  reproduce-ablation  Exp4_Ablation mock smoke (repeats=1, ~1 min)
  reproduce-recovery  PlanGate-R recovery Go tests (< 2 min)
  pareto-dryrun       Pareto sweep dry-run (8 configs, no experiments run)

Requires conference supplementary artifact (unpack to artifact_cache/):
  figures-from-cache  Re-plot paper figures from cached CSVs

On Linux/macOS/WSL2: use 'make <target>' instead.
"@
}

switch ($Target) {
    "smoke"              { Invoke-Smoke }
    "test"               { Invoke-Smoke }
    "reproduce-core"     { Invoke-ReproduceCore }
    "reproduce-ablation" { Invoke-ReproduceAblation }
    "reproduce-recovery" { Invoke-ReproduceRecovery }
    "pareto-dryrun"      { Invoke-ParetoDryrun }
    "figures-from-cache" { Invoke-FiguresFromCache }
    "help"               { Show-Help }
    default              { Show-Help }
}
