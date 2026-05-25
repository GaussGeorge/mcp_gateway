# Throughput and Latency Summary Evidence

This artifact summarizes existing throughput and latency evidence. It does not
introduce new experiments.

This bundle contains:

- `throughput_latency_summary.csv`
- `throughput_latency_agg.csv`
- `validation.json`

It is a lightweight artifact pack built from existing summary CSVs under
`results/`. It intentionally omits full run directories, raw per-step traces,
logs, `.env`, `.venv`, `.gocache`, and the full `results/` tree.

## Included Experiments

- `Exp1_Core`
- `Exp5_ScaleConc`
- `Exp6_ScaleConcReact`
- `Exp10_Adversarial`

## Metric Interpretation

We report both raw throughput and effective goodput. Raw throughput measures
admitted/processed work rate, while effective goodput discounts cascaded
failures and wasted progress. The main governance claim uses effective goodput
because the objective is useful completed work, not merely higher admission
rate.

- `raw_goodput_s`: raw throughput/goodput rate
- `effective_goodput_s`: useful completed work rate after discounting
  cascaded failures or wasted progress

## Scope Boundary

This is a summary-only artifact derived from existing CSV outputs. It does not
rerun Exp1 / Exp5 / Exp6 / Exp10, and it should not be read as a separate new
throughput experiment.
