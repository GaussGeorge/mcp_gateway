# scripts/optional_live/

These scripts are for **optional live re-runs** of PlanGate experiments.
They are **not required** for basic artifact verification (Tier A/B).

## When to use

- You have an LLM API key (GLM-4-Flash / DeepSeek-V3) and want to reproduce the real-LLM tables from scratch.
- You want to re-run mock experiments locally (requires Go + gateway binary).
- You want to reproduce sensitivity ablations.

## Quick guide

```bash
# Re-run core mock experiments (no API key, ~30–45 min)
bash scripts/optional_live/reproduce_mock_core.sh

# Re-run real-LLM experiments (requires LLM_API_KEY in .env)
bash scripts/optional_live/reproduce_real_llm_live.sh

# Bursty real-LLM
python scripts/optional_live/run_real_llm_bursty.py --repeats 3 --burst-size 30

# Appendix re-runs
bash scripts/optional_live/reproduce_appendix_from_cache.sh
bash scripts/optional_live/reproduce_sensitivity.sh
```

After re-running, regenerate tables/figures with the canonical entry points:

```bash
python scripts/reproduce_main_paper_from_cache.sh   # tables
python scripts/gen_paper_figures.py                  # figures
```

## For Tier A/B verification (no API key)

Use the commands in the top-level README Quick Check section instead:

```bash
python scripts/setup_frozen_results.py
python scripts/_verify_paper_data.py
python scripts/_compute_bursty_stats.py
python scripts/_compute_tput_latency_stats.py --show-crossings
python scripts/gen_paper_figures.py
```
