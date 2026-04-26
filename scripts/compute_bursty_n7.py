"""Compute N=7 bursty statistics from ALL available data rows."""
import statistics, math

# NG data: run1 has 3 rows (original N=3), runs 2-5 have 1 each = 7 total
ng_success  = [24, 19, 27, 16, 21, 14, 16]
ng_partial  = [80, 100, 95, 103, 95, 92, 73]
ng_all_rej  = [96, 81, 78, 81, 84, 94, 108]
ng_cascade  = [143, 177, 172, 190, 173, 168, 116]

# PG data: run1 has 3 rows (original N=3), runs 2-5 have 1 each = 7 total
pg_success  = [13, 19, 20, 19, 20, 10, 9]
pg_partial  = [101, 74, 70, 67, 75, 66, 75]
pg_all_rej  = [86, 107, 110, 114, 105, 124, 116]
pg_cascade  = [193, 146, 142, 138, 140, 112, 135]

n = 7

def ms(arr):
    return statistics.mean(arr), statistics.stdev(arr)

def ttest_ind(a, b):
    """Two-sample pooled t-test."""
    na, nb = len(a), len(b)
    ma, mb = statistics.mean(a), statistics.mean(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    sp = math.sqrt(((na-1)*va + (nb-1)*vb) / (na+nb-2))
    se = sp * math.sqrt(1/na + 1/nb)
    if se == 0:
        return float('inf')
    return (ma - mb) / se

# Derived metrics
ng_abd = [100*ng_partial[i]/(ng_success[i]+ng_partial[i]) for i in range(n)]
pg_abd = [100*pg_partial[i]/(pg_success[i]+pg_partial[i]) for i in range(n)]
ng_sr  = [100*ng_success[i]/200 for i in range(n)]
pg_sr  = [100*pg_success[i]/200 for i in range(n)]

print("=" * 60)
print(f"BURSTY REAL-LLM STATISTICS (N={n})")
print("=" * 60)

for label, ng_arr, pg_arr in [
    ("Partial agents", ng_partial, pg_partial),
    ("Cascade steps", ng_cascade, pg_cascade),
    ("ABD%", ng_abd, pg_abd),
    ("Success rate%", ng_sr, pg_sr),
    ("All rejected", ng_all_rej, pg_all_rej),
]:
    nm, ns = ms(ng_arr)
    pm, ps = ms(pg_arr)
    t = ttest_ind(ng_arr, pg_arr)
    df = 2*n - 2
    pct = 100*(nm-pm)/nm if nm != 0 else 0
    print(f"\n{label}:")
    print(f"  NG:       {nm:.1f} +/- {ns:.1f}")
    print(f"  PlanGate: {pm:.1f} +/- {ps:.1f}")
    print(f"  Diff:     {nm-pm:+.1f} ({pct:+.1f}%)")
    print(f"  t({df})={t:.2f}")

# Variance comparison
print("\n" + "=" * 60)
print("VARIANCE COMPARISON")
print("=" * 60)
print(f"ABD std:     NG={ms(ng_abd)[1]:.1f}  PG={ms(pg_abd)[1]:.1f}  ratio={ms(ng_abd)[1]/ms(pg_abd)[1]:.1f}x")
print(f"SR std:      NG={ms(ng_sr)[1]:.1f}  PG={ms(pg_sr)[1]:.1f}  ratio={ms(ng_sr)[1]/ms(pg_sr)[1]:.1f}x")
print(f"Partial std: NG={ms(ng_partial)[1]:.1f}  PG={ms(pg_partial)[1]:.1f}")
print(f"Cascade std: NG={ms(ng_cascade)[1]:.1f}  PG={ms(pg_cascade)[1]:.1f}")

# For the paper table (rounded)
print("\n" + "=" * 60)
print("PAPER TABLE VALUES (rounded)")
print("=" * 60)
nm, ns = ms(ng_partial)
pm, ps = ms(pg_partial)
print(f"NG:       PARTIAL={nm:.0f}+/-{ns:.0f}  ABD={ms(ng_abd)[0]:.1f}+/-{ms(ng_abd)[1]:.1f}  SR={ms(ng_sr)[0]:.1f}+/-{ms(ng_sr)[1]:.1f}  Std(ABD)={ms(ng_abd)[1]:.1f}")
print(f"PlanGate: PARTIAL={pm:.0f}+/-{ps:.0f}  ABD={ms(pg_abd)[0]:.1f}+/-{ms(pg_abd)[1]:.1f}  SR={ms(pg_sr)[0]:.1f}+/-{ms(pg_sr)[1]:.1f}  Std(ABD)={ms(pg_abd)[1]:.1f}")
t_p = ttest_ind(ng_partial, pg_partial)
print(f"\nPARTIAL t-test: t={t_p:.2f}, df={2*n-2}")
pct_red = 100*(nm-pm)/nm
print(f"PARTIAL reduction: {pct_red:.1f}%")

# p-value lookup (approximate)
# df=12: t=2.179 -> p=0.05, t=2.681 -> p=0.02, t=3.055 -> p=0.01
print(f"\nReference: df=12 critical values:")
print(f"  t=2.179 -> p=0.05")
print(f"  t=2.681 -> p=0.02")
print(f"  t=3.055 -> p=0.01")
