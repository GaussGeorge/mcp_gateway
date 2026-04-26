"""Compute N=5 bursty statistics and t-tests."""
import statistics, math

ng_success = [24, 16, 21, 14, 16]
ng_partial = [80, 103, 95, 92, 73]
ng_all_rej = [96, 81, 84, 94, 108]
ng_casc = [143, 190, 173, 168, 116]

pg_success = [13, 19, 20, 10, 9]
pg_partial = [101, 67, 75, 66, 75]
pg_all_rej = [86, 114, 105, 124, 116]
pg_casc = [193, 138, 140, 112, 135]

n = 5

def mean_std(arr):
    return statistics.mean(arr), statistics.stdev(arr)

def ttest(a, b):
    na = nb = len(a)
    ma, sa = statistics.mean(a), statistics.variance(a)
    mb, sb = statistics.mean(b), statistics.variance(b)
    sp = math.sqrt(((na-1)*sa + (nb-1)*sb) / (na+nb-2))
    se = sp * math.sqrt(1/na + 1/nb)
    t = (ma - mb) / se
    return t

ng_abd = [100*ng_partial[i]/(ng_success[i]+ng_partial[i]) for i in range(n)]
pg_abd = [100*pg_partial[i]/(pg_success[i]+pg_partial[i]) for i in range(n)]
ng_sr = [100*ng_success[i]/200 for i in range(n)]
pg_sr = [100*pg_success[i]/200 for i in range(n)]

print("=== N=5 Bursty Statistics ===")
print(f"NG success:  {mean_std(ng_success)[0]:.1f} +/- {mean_std(ng_success)[1]:.1f}")
print(f"PG success:  {mean_std(pg_success)[0]:.1f} +/- {mean_std(pg_success)[1]:.1f}")
print(f"NG partial:  {mean_std(ng_partial)[0]:.1f} +/- {mean_std(ng_partial)[1]:.1f}")
print(f"PG partial:  {mean_std(pg_partial)[0]:.1f} +/- {mean_std(pg_partial)[1]:.1f}")
print(f"NG all_rej:  {mean_std(ng_all_rej)[0]:.1f} +/- {mean_std(ng_all_rej)[1]:.1f}")
print(f"PG all_rej:  {mean_std(pg_all_rej)[0]:.1f} +/- {mean_std(pg_all_rej)[1]:.1f}")
print(f"NG casc:     {mean_std(ng_casc)[0]:.1f} +/- {mean_std(ng_casc)[1]:.1f}")
print(f"PG casc:     {mean_std(pg_casc)[0]:.1f} +/- {mean_std(pg_casc)[1]:.1f}")
print(f"NG ABD:      {mean_std(ng_abd)[0]:.1f} +/- {mean_std(ng_abd)[1]:.1f}")
print(f"PG ABD:      {mean_std(pg_abd)[0]:.1f} +/- {mean_std(pg_abd)[1]:.1f}")
print(f"NG SR:       {mean_std(ng_sr)[0]:.1f} +/- {mean_std(ng_sr)[1]:.1f}")
print(f"PG SR:       {mean_std(pg_sr)[0]:.1f} +/- {mean_std(pg_sr)[1]:.1f}")

print("\n=== t-tests (df=8, two-tailed) ===")
t_partial = ttest(ng_partial, pg_partial)
t_casc = ttest(ng_casc, pg_casc)
t_abd = ttest(ng_abd, pg_abd)
t_sr = ttest(ng_sr, pg_sr)
print(f"PARTIAL agents: t={t_partial:.2f}")
print(f"CASCADE steps:  t={t_casc:.2f}")
print(f"ABD%:           t={t_abd:.2f}")
print(f"Success rate:   t={t_sr:.2f}")

# Reduction percentages
ng_casc_m = mean_std(ng_casc)[0]
pg_casc_m = mean_std(pg_casc)[0]
print(f"\nCascade reduction: {ng_casc_m:.1f} -> {pg_casc_m:.1f} = {100*(ng_casc_m-pg_casc_m)/ng_casc_m:.1f}%")
ng_partial_m = mean_std(ng_partial)[0]
pg_partial_m = mean_std(pg_partial)[0]
print(f"Partial reduction: {ng_partial_m:.1f} -> {pg_partial_m:.1f} = {100*(ng_partial_m-pg_partial_m)/ng_partial_m:.1f}%")

# Std comparison (variance stability)
print(f"\nABD std: NG={mean_std(ng_abd)[1]:.1f}  PG={mean_std(pg_abd)[1]:.1f}")
print(f"Success std: NG={mean_std(ng_sr)[1]:.1f}  PG={mean_std(pg_sr)[1]:.1f}")
