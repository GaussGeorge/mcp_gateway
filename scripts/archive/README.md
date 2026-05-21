# scripts/archive — Development & Diagnostic Utilities

These scripts are development-phase utilities, diagnostic tools, or deprecated
plotting scripts that are **not part of the paper artifact reproducibility
pipeline** for submission v7.

They are preserved here for completeness and scientific transparency, but
reviewers do not need to run them to verify the paper's tables and figures.

## What to use instead

For paper verification, use the scripts in `scripts/` (parent directory):

```
python scripts/_verify_paper_data.py          # verify all tables
python scripts/_compute_bursty_stats.py       # bursty N=7 table
python scripts/_compute_tput_latency_stats.py # tput-latency table
python scripts/gen_paper_figures.py           # regenerate all figures
```

See `TABLE_FIGURE_MAPPING.md` (repo root) for the full item-to-script mapping.

## Categories of archived scripts

| Category | Examples |
|----------|---------|
| Deprecated figure generators | `plot_paper_charts.py`, `update_paper_figures.py`, `plot_*.py` |
| Diagnostic scripts with hardcoded local paths | `_audit_reallm.py`, `_bursty_check.py`, `_bursty_detail*.py` |
| Old analysis pipelines (superseded) | `analyze_*.py`, `stats_significance.py` |
| Smoke / sanity tests | `smoke_test_*.py`, `sanity_check.py`, `verify_api.py` |
| Superseded experiment runners | `run_alpha_sweep.py`, `run_pareto_frontier.py`, `run_b2_mt_medium_sweep.py` |
| Miscellaneous utilities | `compute_b2_stats.py`, `process_mock_results.py`, `benchmark_overhead.py` |
