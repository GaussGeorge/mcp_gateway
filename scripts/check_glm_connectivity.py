#!/usr/bin/env python3
"""Minimal GLM connectivity preflight for local smoke tests."""

from __future__ import annotations

import sys
from pathlib import Path

from real_llm_utils import (
    classify_llm_error,
    format_exception_message,
    load_project_dotenv,
    mask_secret,
    resolve_llm_config,
    run_llm_preflight,
)


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    env_path = load_project_dotenv(root_dir)
    cfg = resolve_llm_config()

    print("[GLM preflight] root:", root_dir)
    print("[GLM preflight] .env:", env_path if env_path else "(not found)")
    print("[GLM preflight] base:", cfg.base)
    print("[GLM preflight] model:", cfg.model)
    print("[GLM preflight] key:", mask_secret(cfg.key))

    try:
        result = run_llm_preflight(timeout_seconds=30.0, max_tokens=8)
    except Exception as exc:
        print(f"[GLM preflight] ERROR ({classify_llm_error(exc)}): {format_exception_message(exc)}")
        return 1

    print("[GLM preflight] reply:", result["response_text"] or "(empty)")
    print(
        "[GLM preflight] usage:",
        f"prompt={result['usage']['prompt_tokens']}",
        f"completion={result['usage']['completion_tokens']}",
        f"total={result['usage']['total_tokens']}",
    )
    print(f"[GLM preflight] elapsed: {result['elapsed_seconds']:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
