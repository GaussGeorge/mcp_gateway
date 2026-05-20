# Queue launcher - loads .env and starts bursty+vLLM queue
$root = "D:\mcp-governance-main - A"
Set-Location $root

# Load .env
if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } | ForEach-Object {
        $p = $_ -split '=', 2
        if ($p.Count -eq 2) {
            [System.Environment]::SetEnvironmentVariable($p[0].Trim(), $p[1].Trim().Trim('"').Trim("'"), 'Process')
        }
    }
}
$env:AGENT_LLM_BASE_URL = "http://127.0.0.1:9999/v1"
$env:AGENT_LLM_BASE = "http://127.0.0.1:9999/v1"
$env:AGENT_LLM_MODEL = "qwen"
$env:AGENT_LLM_KEY = "EMPTY"

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$qlog = "$root\logs\neutral_real_llm\queue_$ts.log"
New-Item -ItemType Directory -Path "$root\logs\neutral_real_llm" -Force | Out-Null

python "$root\scripts\run_all_real_llm_queue.py" 2>&1 | Tee-Object -FilePath $qlog
Write-Host "QUEUE_DONE log=$qlog"
