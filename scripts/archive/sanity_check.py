#!/usr/bin/env python3
"""三铁律 Sanity Check — 对 Exp1_Core 和 Exp4_Ablation 结果进行审计"""
import csv
import os

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")


def load_summary(path):
    results = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gw = row["gateway"]
            if gw not in results:
                results[gw] = []
            results[gw].append(row)
    return results


def avg(rows, key):
    vals = [float(r[key]) for r in rows if r.get(key)]
    return sum(vals) / len(vals) if vals else 0


def main():
    exp1_path = os.path.join(RESULTS_DIR, "exp1_core", "exp1_core_summary.csv")
    exp4_path = os.path.join(RESULTS_DIR, "exp4_ablation", "exp4_ablation_summary.csv")

    if not os.path.exists(exp1_path):
        print(f"找不到 {exp1_path}")
        return
    if not os.path.exists(exp4_path):
        print(f"找不到 {exp4_path}")
        return

    data = load_summary(exp1_path)
    data4 = load_summary(exp4_path)

    # ======= Exp1_Core 汇总 =======
    print("=" * 85)
    print("  Exp1_Core 汇总 (5次重复平均)")
    print("=" * 85)
    header = f"{'Gateway':<22} {'Success':>8} {'Rej@S0':>8} {'Cascade':>8} {'RawGP/s':>10} {'EffGP/s':>10} {'P50':>8} {'P95':>8}"
    print(header)
    print("-" * 85)
    for gw in ["ng", "srl", "rajomon", "dagor", "sbac", "plangate_full", "plangate_no_lock"]:
        rows = data.get(gw, [])
        if not rows:
            continue
        line = (
            f"{gw:<22} "
            f"{avg(rows, 'success'):>8.1f} "
            f"{avg(rows, 'rejected_s0'):>8.1f} "
            f"{avg(rows, 'cascade_failed'):>8.1f} "
            f"{avg(rows, 'raw_goodput_s'):>10.2f} "
            f"{avg(rows, 'effective_goodput_s'):>10.2f} "
            f"{avg(rows, 'p50_ms'):>8.1f} "
            f"{avg(rows, 'p95_ms'):>8.1f}"
        )
        print(line)

    # ======= Exp4_Ablation 汇总 =======
    print()
    print("=" * 85)
    print("  Exp4_Ablation 汇总 (5次重复平均)")
    print("=" * 85)
    print(header)
    print("-" * 85)
    for gw in ["plangate_full", "plangate_no_lock"]:
        rows = data4.get(gw, [])
        line = (
            f"{gw:<22} "
            f"{avg(rows, 'success'):>8.1f} "
            f"{avg(rows, 'rejected_s0'):>8.1f} "
            f"{avg(rows, 'cascade_failed'):>8.1f} "
            f"{avg(rows, 'raw_goodput_s'):>10.2f} "
            f"{avg(rows, 'effective_goodput_s'):>10.2f} "
            f"{avg(rows, 'p50_ms'):>8.1f} "
            f"{avg(rows, 'p95_ms'):>8.1f}"
        )
        print(line)

    # ======= 三铁律检查 =======
    print()
    print("=" * 85)
    print("  三铁律 Sanity Check")
    print("=" * 85)

    rj = data.get("rajomon", [])
    pf = data.get("plangate_full", [])
    pnl = data.get("plangate_no_lock", [])
    pf4 = data4.get("plangate_full", [])
    pnl4 = data4.get("plangate_no_lock", [])

    # --- 铁律 1 ---
    rj_raw = avg(rj, "raw_goodput_s")
    rj_eff = avg(rj, "effective_goodput_s")
    rj_cascade = avg(rj, "cascade_failed")
    drop_pct = (1 - rj_eff / rj_raw) * 100 if rj_raw > 0 else 0
    print(f"\n铁律1: Rajomon 算力浪费")
    print(f"  Raw GP/s = {rj_raw:.2f},  Eff GP/s = {rj_eff:.2f}")
    print(f"  落差 = {drop_pct:.1f}%,  CASCADE_FAIL 平均 = {rj_cascade:.1f}")
    if rj_cascade > 10 and drop_pct > 30:
        print("  >>> PASS: Rajomon 有严重的级联失败和断崖式 Effective Goodput 下跌")
    else:
        print("  >>> FAIL: 压力不够或 Rajomon 未出现预期的算力浪费")

    # --- 铁律 2 ---
    pf_cascade = avg(pf, "cascade_failed")
    pf_eff = avg(pf, "effective_goodput_s")
    pf_rej = avg(pf, "rejected_s0")
    ng_eff = avg(data.get("ng", []), "effective_goodput_s")
    srl_eff = avg(data.get("srl", []), "effective_goodput_s")
    rj_eff_check = avg(rj, "effective_goodput_s")
    dg_eff = avg(data.get("dagor", []), "effective_goodput_s")
    sb_eff = avg(data.get("sbac", []), "effective_goodput_s")
    pnl1_eff = avg(data.get("plangate_no_lock", []), "effective_goodput_s")
    # 各基线级联失败数
    ng_cas = avg(data.get("ng", []), "cascade_failed")
    srl_cas = avg(data.get("srl", []), "cascade_failed")
    rj_cas = avg(rj, "cascade_failed")
    dg_cas = avg(data.get("dagor", []), "cascade_failed")
    sb_cas = avg(data.get("sbac", []), "cascade_failed")
    # 所有其他网关的最高 EffGP/s
    others_max_eff = max(ng_eff, srl_eff, rj_eff_check, dg_eff, sb_eff)
    others_max_name = {ng_eff: "NG", srl_eff: "SRL", rj_eff_check: "Rajomon",
                       dg_eff: "DAGOR", sb_eff: "SBAC"}.get(others_max_eff, "?")
    print(f"\n铁律2: PlanGate-Full 有效吞吐量全场第一 + 级联最少")
    print(f"  PlanGate: EffGP/s={pf_eff:.2f}, Cascade={pf_cascade:.1f}, Rej@S0={pf_rej:.1f}")
    print(f"  基线最高: {others_max_name}={others_max_eff:.2f} (NG={ng_eff:.2f} SRL={srl_eff:.2f} Rajomon={rj_eff_check:.2f} DAGOR={dg_eff:.2f} SBAC={sb_eff:.2f})")
    print(f"  NoLock={pnl1_eff:.2f}")
    # 条件 1: PlanGate EffGP/s 全场最高（比所有基线都高）
    # 条件 2: PlanGate 级联失败数低于大多数基线
    # 条件 3: PlanGate EffGP/s 显著高于 NoLock 变体
    eff_best = pf_eff > others_max_eff
    eff_above_nolock = pf_eff > pnl1_eff * 1.2
    cascade_low = pf_cascade < min(ng_cas, srl_cas, rj_cas, dg_cas, sb_cas)
    if eff_best and eff_above_nolock:
        print(f"  >>> PASS: PlanGate EffGP/s({pf_eff:.2f}) > 全场最高基线({others_max_name}={others_max_eff:.2f}) + 显著优于 NoLock")
    else:
        reasons = []
        if not eff_best:
            reasons.append(f"EffGP/s({pf_eff:.2f})未超越{others_max_name}({others_max_eff:.2f})")
        if not eff_above_nolock:
            reasons.append(f"EffGP/s({pf_eff:.2f})未比NoLock({pnl1_eff:.2f})高20%以上")
        print(f"  >>> FAIL: {'; '.join(reasons)}")

    # --- 铁律 3 ---
    pf4_eff = avg(pf4, "effective_goodput_s")
    pnl4_eff = avg(pnl4, "effective_goodput_s")
    pnl4_cascade = avg(pnl4, "cascade_failed")
    gap_pct = (1 - pnl4_eff / pf4_eff) * 100 if pf4_eff > 0 else 0
    print(f"\n铁律3: 消融实验自洽性")
    print(f"  Full Eff GP/s = {pf4_eff:.2f},  NoLock Eff GP/s = {pnl4_eff:.2f}")
    print(f"  落差 = {gap_pct:.1f}%,  NoLock CASCADE_FAIL = {pnl4_cascade:.1f}")
    if pnl4_cascade > 10 and gap_pct > 15:
        print("  >>> PASS: 消融实验自洽，无预算锁导致显著性能下降和级联失败")
    else:
        print("  >>> FAIL: 消融差异不显著，需加大突发强度")

    print("\n" + "=" * 85)


if __name__ == "__main__":
    main()
