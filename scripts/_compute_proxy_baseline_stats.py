#!/usr/bin/env python3
import argparse
import csv
from collections import defaultdict
from pathlib import Path


def mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def std(vals):
    if len(vals) <= 1:
        return 0.0
    m = mean(vals)
    return (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5


def t_critical_95(n):
    # Two-sided 95% CI t-critical value by sample size n (df=n-1).
    table = {
        2: 12.706,
        3: 4.303,
        4: 3.182,
        5: 2.776,
        6: 2.571,
        7: 2.447,
        8: 2.365,
        9: 2.306,
        10: 2.262,
        11: 2.228,
        12: 2.201,
        13: 2.179,
        14: 2.160,
        15: 2.145,
        16: 2.131,
        17: 2.120,
        18: 2.110,
        19: 2.101,
        20: 2.093,
        21: 2.086,
        22: 2.080,
        23: 2.074,
        24: 2.069,
        25: 2.064,
        26: 2.060,
        27: 2.056,
        28: 2.052,
        29: 2.048,
        30: 2.045,
    }
    if n <= 1:
        return 0.0
    if n in table:
        return table[n]
    return 1.96


def ci95(vals):
    n = len(vals)
    if n <= 1:
        return 0.0
    return t_critical_95(n) * std(vals) / (n ** 0.5)


def main():
    ap = argparse.ArgumentParser(description="Aggregate proxy baseline summary CSV")
    ap.add_argument("--input", default="results/exp_proxy_baselines/mock/proxy_baseline_summary.csv")
    ap.add_argument("--output", default=None,
        help="Output agg CSV. Defaults to <input-dir>/proxy_baseline_agg.csv")
    ap.add_argument("--expected-gateways", type=int, default=None,
        help="Expected number of distinct gateways. Error if actual count differs.")
    ap.add_argument("--expected-conc", type=int, default=None,
        help="Expected number of distinct concurrency levels. Error if actual count differs.")
    ap.add_argument("--expected-repeats", type=int, default=None,
        help="Expected number of repeats per gateway×conc combination. Error if any combo differs.")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        raise FileNotFoundError(f"summary csv not found: {inp}")

    out_path = Path(args.output) if args.output else inp.parent / "proxy_baseline_agg.csv"

    groups = defaultdict(list)
    with inp.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            key = (row["gateway"], row["concurrency"])
            groups[key].append(row)

    # ── Defensive validation ──────────────────────────────────────────────────
    actual_gateways = len({k[0] for k in groups})
    actual_conc = len({k[1] for k in groups})
    actual_total = sum(len(v) for v in groups.values())
    repeat_counts = {k: len(v) for k, v in groups.items()}
    inconsistent_repeats = {k: n for k, n in repeat_counts.items() if n != list(repeat_counts.values())[0]}

    errors = []
    if args.expected_gateways is not None and actual_gateways != args.expected_gateways:
        errors.append(
            f"  gateway count mismatch: expected {args.expected_gateways}, got {actual_gateways} "
            f"({sorted({k[0] for k in groups})})"
        )
    if args.expected_conc is not None and actual_conc != args.expected_conc:
        errors.append(
            f"  concurrency level count mismatch: expected {args.expected_conc}, got {actual_conc} "
            f"({sorted({k[1] for k in groups})})"
        )
    if args.expected_repeats is not None:
        bad = {k: n for k, n in repeat_counts.items() if n != args.expected_repeats}
        if bad:
            errors.append(
                f"  repeat count mismatch for {len(bad)} combo(s): expected {args.expected_repeats} each\n"
                + "\n".join(f"    {k}: {n}" for k, n in sorted(bad.items()))
            )
        expected_total = actual_gateways * actual_conc * args.expected_repeats
        if actual_total != expected_total:
            errors.append(
                f"  total row count mismatch: expected {expected_total} "
                f"({actual_gateways}gw x {actual_conc}conc x {args.expected_repeats}rep), "
                f"got {actual_total}"
            )
    if errors:
        raise ValueError(
            f"VALIDATION FAILED for {inp}\n" + "\n".join(errors)
        )
    # ── End validation ────────────────────────────────────────────────────────

    out_rows = []
    for (gateway, conc), rows in sorted(groups.items()):
        succ = [float(r["success"]) for r in rows]
        partial = [float(r["partial"]) for r in rows]
        rej0 = [float(r["all_rejected"]) for r in rows]
        casc = [float(r["cascade_failed"]) for r in rows]
        casc_steps = [float(r["cascade_steps"]) for r in rows]
        abd = [float(r["abd_total"]) for r in rows]
        casc_adm = [float(r["cascade_admitted_pct"]) for r in rows]
        success_s = [float(r.get("success_sessions_per_s", 0) or 0) for r in rows]
        gps = [float(r["effective_goodput_s"]) for r in rows]
        p95 = [float(r["p95_success_s"]) for r in rows]

        out_rows.append({
            "gateway": gateway,
            "concurrency": conc,
            "runs": len(rows),
            "success_mean": round(mean(succ), 4),
            "success_std": round(std(succ), 4),
            "partial_mean": round(mean(partial), 4),
            "rej0_mean": round(mean(rej0), 4),
            "cascade_mean": round(mean(casc), 4),
            "cascade_steps_mean": round(mean(casc_steps), 4),
            "abd_mean": round(mean(abd), 4),
            "abd_ci95": round(ci95(abd), 4),
            "cascade_admitted_pct_mean": round(mean(casc_adm), 4),
            "cascade_admitted_pct_ci95": round(ci95(casc_adm), 4),
            "success_sessions_per_s_mean": round(mean(success_s), 6),
            "success_sessions_per_s_std": round(std(success_s), 6),
            "success_sessions_per_s_ci95": round(ci95(success_s), 6),
            "effective_goodput_s_mean": round(mean(gps), 6),
            "effective_goodput_s_std": round(std(gps), 6),
            "effective_goodput_s_ci95": round(ci95(gps), 6),
            "p95_success_s_mean": round(mean(p95), 6),
            "p95_success_s_std": round(std(p95), 6),
            "p95_success_s_ci95": round(ci95(p95), 6),
        })

    out = out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "gateway", "concurrency", "runs",
        "success_mean", "success_std", "partial_mean", "rej0_mean",
        "cascade_mean", "cascade_steps_mean", "abd_mean", "cascade_admitted_pct_mean",
        "abd_ci95", "cascade_admitted_pct_ci95",
        "success_sessions_per_s_mean", "success_sessions_per_s_std",
        "success_sessions_per_s_ci95",
        "effective_goodput_s_mean", "effective_goodput_s_std",
        "effective_goodput_s_ci95",
        "p95_success_s_mean", "p95_success_s_std",
        "p95_success_s_ci95",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    print(f"wrote: {out}")
    print("\nProxy baseline aggregated table:")
    print("gateway,conc,runs,success_mean,rej0_mean,cascade_mean,abd_mean+/-ci95,success_sessions_per_s+/-ci95,gp_s+/-ci95,p95_s+/-ci95")
    for r in out_rows:
        print(
            f"{r['gateway']},{r['concurrency']},{r['runs']},"
            f"{r['success_mean']:.2f},{r['rej0_mean']:.2f},{r['cascade_mean']:.2f},"
            f"{r['abd_mean']:.2f}+/-{r['abd_ci95']:.2f},"
            f"{r['success_sessions_per_s_mean']:.4f}+/-{r['success_sessions_per_s_ci95']:.4f},"
            f"{r['effective_goodput_s_mean']:.4f}+/-{r['effective_goodput_s_ci95']:.4f},"
            f"{r['p95_success_s_mean']:.4f}+/-{r['p95_success_s_ci95']:.4f}"
        )

    # sanity checks
    for r in out_rows:
        if r["rej0_mean"] > 0 and r["success_mean"] == 0:
            print(f"WARN: {r['gateway']}@C={r['concurrency']} appears to reject at step-0 almost always")
        if r["rej0_mean"] == 0 and r["cascade_mean"] == 0:
            print(f"WARN: {r['gateway']}@C={r['concurrency']} has neither reject nor cascade; check stress level")


if __name__ == "__main__":
    main()
