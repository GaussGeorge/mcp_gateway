## GLM Real-LLM Refresh

- Date: `2026-05-25`
- Model: `glm-4-flash`
- Workload: ReAct agents with real GLM tool selection
- Scale: `200 agents/run`, `concurrency 10`, `3 repeats`
- Gateways: `ng`, `rajomon`, `pp`, `plangate_real`

This is **live GLM evidence** from a local real-LLM rerun. It is not a mock
experiment and not a CloudLab distributed experiment.

Validated files in this directory:

- `week5_summary.csv`
- `week5_agg.csv`
- `12 x steps_summary_*.csv`
- `validation.json`

Key aggregated results:

- `ng`: `success_rate_mean=97.83`, `ABD=2.17`, `EffGP/s=0.46`, `P95=62894.67ms`
- `rajomon`: `success_rate_mean=96.33`, `ABD=3.50`, `EffGP/s=0.45`, `P95=63736.67ms`
- `pp`: `success_rate_mean=94.50`, `ABD=5.03`, `EffGP/s=0.43`, `P95=67636.67ms`
- `plangate_real`: `success_rate_mean=95.83`, `ABD=3.67`, `EffGP/s=0.43`, `P95=67094.00ms`

Validation summary:

- `rows = 12`
- `gateway_counts = {'ng': 3, 'rajomon': 3, 'pp': 3, 'plangate_real': 3}`
- `steps_summary_count = 12`
- `client_rc = 0` for all rows
- `client_timed_out = 0` for all rows
- `error = 0` for all rows
- `validation.json.errors = []`

Caveat:

This refresh validates that the post-P4 mechanisms still support live GLM
real-LLM ReAct workloads. It should not be over-claimed as PlanGate
outperforming every baseline on every real-LLM metric in this run. In this C10
refresh, `ng` has the highest success rate / GP/s, while `plangate_real`
remains stable with zero client/runtime errors and no timeout.
