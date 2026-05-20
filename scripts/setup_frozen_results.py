#!/usr/bin/env python3
"""
setup_frozen_results.py — Copy frozen artifact_cache/ CSVs into results/

Run this once before executing any verification or figure-generation scripts.
No API key or network access required.

Usage:
    python scripts/setup_frozen_results.py

After running, the following commands will work against the frozen data:
    python scripts/_verify_paper_data.py
    python scripts/_compute_bursty_stats.py
    python scripts/_compute_tput_latency_stats.py --show-crossings
    python scripts/gen_paper_figures.py
"""

import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "artifact_cache")
RESULTS = os.path.join(BASE, "results")

# Directories to copy from artifact_cache/ → results/
# Each entry: (source_subdir, dest_subdir)
# Most map 1-to-1; a few have different names in results/.
COPY_MAP = [
    ("exp1_core",                    "exp1_core"),
    ("exp4_ablation",                "exp4_ablation"),
    ("exp8_discountablation",        "exp8_discountablation"),
    ("exp9_scalestress",             "exp9_scalestress"),
    ("exp10_adversarial",            "exp10_adversarial"),
    ("exp_rajomon_sensitivity",      "exp_rajomon_sensitivity"),
    ("exp_alpha_sweep",              "exp_alpha_sweep"),
    ("beta_ablation",                "beta_ablation"),
    ("exp_week5_C10",                "exp_week5_C10"),
    ("exp_week5_C40",                "exp_week5_C40"),
    ("exp_bursty_C20_B30",           "exp_bursty_C20_B30"),
    ("exp_selfhosted_vllm_C20_W8",   "exp_selfhosted_vllm_C20_W8"),
    ("exp_real3_glm",                "exp_real3_glm"),
    ("exp_real3_deepseek",           "exp_real3_deepseek"),
    ("pareto_frontier_selected",     "pareto_frontier_selected"),
    ("manifests",                    "manifests"),
    # Pre-generated figures (used as reference; overwritten by gen_paper_figures.py)
    ("paper_figures",                "paper_figures"),
]


def copy_dir(src, dst, label):
    if not os.path.isdir(src):
        print(f"  SKIP  {label}  (source not found: {src})")
        return 0
    if os.path.isdir(dst):
        # Merge: only copy files that are missing in dst (don't overwrite local results)
        copied = 0
        for root, _, files in os.walk(src):
            rel = os.path.relpath(root, src)
            dst_root = os.path.join(dst, rel)
            os.makedirs(dst_root, exist_ok=True)
            for f in files:
                dst_f = os.path.join(dst_root, f)
                src_f = os.path.join(root, f)
                if not os.path.exists(dst_f):
                    shutil.copy2(src_f, dst_f)
                    copied += 1
        if copied:
            print(f"  MERGE {label}  ({copied} new files)")
        else:
            print(f"  OK    {label}  (already up-to-date)")
        return copied
    else:
        shutil.copytree(src, dst)
        n = sum(len(fs) for _, _, fs in os.walk(dst))
        print(f"  COPY  {label}  ({n} files)")
        return n


def main():
    os.makedirs(RESULTS, exist_ok=True)
    total = 0
    print(f"Setting up frozen results: {CACHE} → {RESULTS}\n")
    for src_sub, dst_sub in COPY_MAP:
        src = os.path.join(CACHE, src_sub)
        dst = os.path.join(RESULTS, dst_sub)
        total += copy_dir(src, dst, src_sub)

    print(f"\nDone — {total} files copied/merged into results/")
    print("\nNext steps:")
    print("  python scripts/_verify_paper_data.py")
    print("  python scripts/_compute_bursty_stats.py")
    print("  python scripts/_compute_tput_latency_stats.py --show-crossings")
    print("  python scripts/gen_paper_figures.py")


if __name__ == "__main__":
    main()
