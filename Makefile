# PlanGate Artifact Reproduction Makefile
# Targets for Linux / macOS / WSL2.
# On Windows (native PowerShell): use scripts/artifact_smoke.ps1 instead.
#
# Quick reference:
#   make smoke               -- Go unit tests (< 1 min, no API key)
#   make reproduce-core      -- Exp1_Core mock smoke (~2 min, no API key)
#   make reproduce-ablation  -- Exp4_Ablation mock smoke (~1 min, no API key)
#   make reproduce-recovery  -- PlanGate-R recovery (Go tests, < 2 min)
#   make pareto-dryrun       -- Pareto sweep dry-run (prints configs only)
#   make figures-from-cache  -- Re-plot paper figures (requires supplementary artifact)
#   make test                -- alias for smoke

.PHONY: help smoke test reproduce-core reproduce-ablation reproduce-recovery \
        pareto-dryrun figures-from-cache

# Gateway binary name (auto-selects by OS; override with GATEWAY_BIN=./gateway_linux)
GATEWAY_BIN := ./gateway

.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "PlanGate artifact reproduction targets"
	@echo "======================================="
	@echo ""
	@echo "No API key required:"
	@echo "  make smoke               Go unit tests only (< 1 min)"
	@echo "  make test                Alias for smoke"
	@echo "  make reproduce-core      Exp1_Core mock smoke (repeats=1, ~2 min)"
	@echo "  make reproduce-ablation  Exp4_Ablation mock smoke (repeats=1, ~1 min)"
	@echo "  make reproduce-recovery  PlanGate-R recovery Go tests (< 2 min)"
	@echo "  make pareto-dryrun       Pareto sweep dry-run (8 selected configs, no run)"
	@echo ""
	@echo "Requires conference supplementary artifact (unpack to artifact_cache/):"
	@echo "  make figures-from-cache  Re-plot paper figures from cached CSVs"
	@echo ""
	@echo "On Windows (native PowerShell):"
	@echo "  .\\scripts\\artifact_smoke.ps1 -Target <target>"
	@echo ""

# ── Level 0: unit tests ──────────────────────────────────────────────────────

smoke: test

test:
	go test ./... -timeout 120s

# ── Level 1: mock re-run (no API key) ────────────────────────────────────────

$(GATEWAY_BIN):
	go build -o $@ ./cmd/gateway

reproduce-core: $(GATEWAY_BIN)
	python scripts/run_all_experiments.py \
	    --exp Exp1_Core --repeats 1 --gateway-binary $(GATEWAY_BIN)

reproduce-ablation: $(GATEWAY_BIN)
	python scripts/run_all_experiments.py \
	    --exp Exp4_Ablation --repeats 1 --gateway-binary $(GATEWAY_BIN)

reproduce-recovery:
	go test ./plangate/... -run "TestRuntime" -v -timeout 120s

pareto-dryrun:
	python scripts/run_pareto_frontier.py --selected --dry-run

# ── Level 2: from supplementary cache ────────────────────────────────────────

figures-from-cache:
	@if [ ! -d artifact_cache ]; then \
	    echo ""; \
	    echo "ERROR: artifact_cache/ not found."; \
	    echo "No cached paper-result package found in this public repository."; \
	    echo "Unpack the conference supplementary artifact to artifact_cache/ first."; \
	    echo ""; \
	    exit 1; \
	fi
	python scripts/gen_paper_figures.py --cache-dir artifact_cache
