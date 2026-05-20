# PlanGate Artifact Reproduction Makefile
# Targets for Linux / macOS / WSL2.
# On Windows (native PowerShell), run commands individually (see README).
#
# Quick reference:
#   make smoke               -- Go unit tests (< 1 min, no API key)
#   make reproduce-recovery  -- PlanGate-R recovery (Go tests, < 2 min)
#   make figures-from-cache  -- Populate results/ and regenerate all paper figures
#   make test                -- alias for smoke

.PHONY: help smoke test reproduce-recovery figures-from-cache

.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "PlanGate artifact reproduction targets"
	@echo "======================================="
	@echo ""
	@echo "No API key required:"
	@echo "  make smoke               Go unit tests only (< 1 min)"
	@echo "  make test                Alias for smoke"
	@echo "  make reproduce-recovery  PlanGate-R recovery Go tests (< 2 min)"
	@echo ""
	@echo "From frozen artifact_cache/ (included in this repo):"
	@echo "  make figures-from-cache  Populate results/ and regenerate all paper figures"
	@echo ""

smoke: test

test:
	go test ./... -timeout 120s

reproduce-recovery:
	go test ./plangate/... -run "TestRuntime" -v -timeout 120s

figures-from-cache:
	python scripts/setup_frozen_results.py
	python scripts/_verify_paper_data.py
	python scripts/_compute_bursty_stats.py
	python scripts/_compute_tput_latency_stats.py --show-crossings
	python scripts/gen_paper_figures.py
	python scripts/plot_rajomon_sensitivity.py \
		--dir results/exp_rajomon_sensitivity \
		--output results/paper_figures/PDF/rajomon_sensitivity.pdf 2>/dev/null || true
