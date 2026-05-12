$base = "d:\mcp-governance-main - A\results\exp_selfhosted_vllm_C10_W8"
foreach ($gw in @("ng", "plangate_real")) {
    for ($i = 1; $i -le 3; $i++) {
        $f = "$base\$gw\run$i\steps_summary.csv"
        if (Test-Path $f) {
            $data = Get-Content $f | Select-Object -Last 1
            Write-Host "$gw run$i = $data"
        } else {
            Write-Host "$gw run$i = NOT DONE"
        }
    }
}
