"""Compare latency distributions between quick test and formal experiments."""
import pandas as pd

# Quick test (20s)
qt = pd.read_csv("results/quick_test_dp.csv")
print("=== Quick Test (20s) ===")
print(f"Total: {len(qt)}")
print(f"Status: {qt['status'].value_counts().to_dict()}")
for s in ["success", "error", "rejected"]:
    sub = qt[qt["status"] == s]
    if len(sub) > 0:
        print(f"  {s}: n={len(sub)}, P50={sub['latency_ms'].median():.0f}ms, P95={sub['latency_ms'].quantile(0.95):.0f}ms, max={sub['latency_ms'].max():.0f}ms")

print()

# Exp2 hr30 dp run1 (60s)
e2 = pd.read_csv("results/exp2_heavy_ratio/exp2_hr30_dp_run1.csv")
print("=== Exp2_hr30 DP Run1 (60s) ===")
print(f"Total: {len(e2)}")
print(f"Status: {e2['status'].value_counts().to_dict()}")
for s in ["success", "error", "rejected"]:
    sub = e2[e2["status"] == s]
    if len(sub) > 0:
        print(f"  {s}: n={len(sub)}, P50={sub['latency_ms'].median():.0f}ms, P95={sub['latency_ms'].quantile(0.95):.0f}ms, max={sub['latency_ms'].max():.0f}ms")

# Time-windowed analysis for quick test
print()
print("=== Quick Test: Status by 5s window ===")
qt["t"] = qt["timestamp"] - qt["timestamp"].min()
for i in range(0, 20, 5):
    w = qt[(qt["t"] >= i) & (qt["t"] < i + 5)]
    if len(w) > 0:
        counts = w["status"].value_counts()
        print(f"  [{i:2d}-{i+5:2d}s] total={len(w)}, " + ", ".join(f"{k}={v}" for k, v in counts.items()))

# Time-windowed analysis for exp2
print()
print("=== Exp2_hr30 DP: Status by 10s window ===")
e2["t"] = e2["timestamp"] - e2["timestamp"].min()
for i in range(0, 60, 10):
    w = e2[(e2["t"] >= i) & (e2["t"] < i + 10)]
    if len(w) > 0:
        counts = w["status"].value_counts()
        print(f"  [{i:2d}-{i+10:2d}s] total={len(w)}, " + ", ".join(f"{k}={v}" for k, v in counts.items()))
