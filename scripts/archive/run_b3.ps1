$env:NO_PROXY="127.0.0.1,localhost,0.0.0.0"
$env:no_proxy="127.0.0.1,localhost,0.0.0.0"
Get-Content "d:\mcp-governance-main - A\.env" | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } | ForEach-Object {
    $p = $_ -split '=',2
    if ($p.Count -eq 2) {
        $k=$p[0].Trim(); $v=$p[1].Trim().Trim('"').Trim("'")
        if ($k -and $v) { [System.Environment]::SetEnvironmentVariable($k,$v,'Process') }
    }
}
Write-Host "Agent brain: $env:LLM_MODEL at $env:LLM_API_BASE"
Write-Host "NO_PROXY: $env:NO_PROXY"
cd "d:\mcp-governance-main - A"
Write-Host "Starting B3..."
python scripts/run_selfhosted_vllm.py --repeats 3 --out-dir results/neutral_multitool_real_llm/selfhosted_vllm --agents 100 --concurrency 20
Write-Host "B3_DONE"
Read-Host "Press Enter to close"
