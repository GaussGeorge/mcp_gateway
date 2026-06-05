from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifact_results"
OUT = ROOT / "paper" / "JSS" / "figures"

VARIANT_ORDER = [
    "ng",
    "plangate_strict",
    "plangate_adaptive",
    "plangate_no_capacity_step0",
    "plangate_adaptive_react_recovery",
]

LABELS = {
    "ng": "No gateway",
    "plangate_strict": "Strict",
    "plangate_adaptive": "Adaptive",
    "plangate_no_capacity_step0": "No capacity\nStep-0",
    "plangate_adaptive_react_recovery": "Adaptive\n+ recovery",
    "baseline_gateway": "Baseline\ngateway",
    "plangate_adaptive_default": "Adaptive\ndefault",
    "plangate_adaptive_light": "Adaptive\nlight",
    "plangate_adaptive_strict": "Adaptive\nstrict",
    "plangate_adaptive_react_recovery_default": "Recovery\ndefault",
    "plangate_adaptive_react_recovery_light": "Recovery\nlight",
    "plangate_adaptive_react_recovery_strict": "Recovery\nstrict",
}

COLORS = {
    "ng": "#777777",
    "baseline_gateway": "#777777",
    "plangate_strict": "#4C78A8",
    "plangate_adaptive": "#F58518",
    "plangate_no_capacity_step0": "#E45756",
    "plangate_adaptive_react_recovery": "#54A24B",
    "redis": "#54A24B",
    "memory": "#E45756",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def num(row: dict[str, str], key: str, default: float = 0.0) -> float:
    value = row.get(key, "")
    if value in ("", None):
        return default
    return float(value)


def pick(rows: list[dict[str, str]], **conds: str) -> dict[str, str]:
    for row in rows:
        if all(str(row.get(k)) == str(v) for k, v in conds.items()):
            return row
    raise KeyError(f"No row for {conds}")


def save(fig: plt.Figure, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    with path.open("wb") as f:
        fig.savefig(f, format="png", bbox_inches="tight", dpi=300)
    plt.close(fig)


def setup_style() -> None:
    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        plt.style.use("default")
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def plot_core_tradeoff() -> str:
    rows = read_csv(ART / "business_workflow_service_v3" / "business_workflow_service_v3_agg.csv")
    variants = [
        "plangate_strict",
        "plangate_adaptive",
        "plangate_no_capacity_step0",
        "plangate_adaptive_react_recovery",
    ]
    subset = [
        pick(rows, variant=v, concurrency="100", failure_rate="0.2")
        for v in variants
        if any(
            r.get("variant") == v and r.get("concurrency") == "100" and r.get("failure_rate") == "0.2"
            for r in rows
        )
    ]
    labels = [LABELS[r["variant"]] for r in subset]
    x = list(range(len(subset)))

    fig, axes = plt.subplots(2, 2, figsize=(7.6, 4.6))
    metrics = [
        ("success_mean", "success_std", "Completed sessions", 1.0, "A. Completed sessions"),
        ("rejected_s0_mean", "rejected_s0_std", "Step-0 rejections", 1.0, "B. Step-0 capacity rejections"),
        ("cascade_failed_mean", "cascade_failed_std", "Cascade failures", 1.0, "C. Cascade failures"),
        ("wasted_service_ms_mean", "wasted_service_ms_std", "Wasted service time (s)", 1000.0, "D. Wasted service time"),
    ]
    for ax, (mean_key, std_key, ylabel, scale, title) in zip(axes.ravel(), metrics):
        vals = [num(r, mean_key) / scale for r in subset]
        errs = [num(r, std_key) / scale for r in subset]
        colors = [COLORS.get(r["variant"], "#999999") for r in subset]
        ax.bar(x, vals, yerr=errs, capsize=3, color=colors, edgecolor="#333333", linewidth=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=25, ha="right")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.35)
    fig.suptitle("Business workflow trade-off at C=100, failure=0.2", y=0.99)
    fig.subplots_adjust(top=0.88, hspace=0.58, wspace=0.28)
    save(fig, "fig_jss_core_tradeoff")
    return "fig_jss_core_tradeoff"


def plot_recovery_effectiveness() -> str:
    business = read_csv(ART / "business_workflow_service_v3" / "business_workflow_service_v3_agg.csv")
    vllm = read_csv(ART / "vllm_react_recovery_arm_v2" / "agg.csv")

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.25))

    variants = ["plangate_adaptive", "plangate_adaptive_react_recovery"]
    variant_colors = {
        "plangate_adaptive": "#F58518",
        "plangate_adaptive_react_recovery": "#2E7D32",
    }
    metrics = [
        ("resume_recovered_mean", "Recovered"),
        ("avoided_replay_steps_mean", "Avoided\nreplay steps"),
        ("post_resume_success_mean", "Post-resume\nsuccess"),
    ]
    width = 0.36
    pos = list(range(len(metrics)))
    for offset, variant in [(-width / 2, variants[0]), (width / 2, variants[1])]:
        row = pick(business, variant=variant, concurrency="100", failure_rate="0.2")
        vals = [num(row, key) for key, _ in metrics]
        axes[0].bar(
            [p + offset for p in pos],
            vals,
            width=width,
            label=(
                "HTTP: adaptive only"
                if variant == "plangate_adaptive"
                else "HTTP: adaptive + recovery"
            ),
            color=variant_colors[variant],
            edgecolor="#333333",
            linewidth=0.6,
        )
    axes[0].set_xticks(pos)
    axes[0].set_xticklabels([label for _, label in metrics])
    axes[0].set_ylabel("Sessions or steps / run")
    axes[0].set_title("HTTP service workflow recovery")
    axes[0].grid(axis="y", alpha=0.35)
    axes[0].legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=1,
        frameon=False,
        fontsize=8,
        handlelength=1.2,
        borderaxespad=0.0,
    )

    cs = ["2", "4"]
    x = list(range(len(cs)))
    recovered = [num(pick(vllm, variant="plangate_adaptive_react_recovery", concurrency=c), "react_recovered_success_mean") for c in cs]
    avoided = [num(pick(vllm, variant="plangate_adaptive_react_recovery", concurrency=c), "avoided_replay_steps_mean") for c in cs]
    adaptive_recovered = [num(pick(vllm, variant="plangate_adaptive", concurrency=c), "react_recovered_success_mean") for c in cs]
    axes[1].bar([i - width / 2 for i in x], recovered, width=width, color="#4C78A8", label="vLLM: recovered sessions", edgecolor="#333333", linewidth=0.6)
    axes[1].bar([i + width / 2 for i in x], avoided, width=width, color="#9467BD", label="vLLM: avoided replay steps", edgecolor="#333333", linewidth=0.6)
    axes[1].plot(x, adaptive_recovered, color="#D62728", marker="o", linewidth=1.5, linestyle="--", label="vLLM: adaptive baseline")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([f"C={c}" for c in cs])
    axes[1].set_ylabel("Mean count / run")
    axes[1].set_title("vLLM-backed recovery arm")
    axes[1].grid(axis="y", alpha=0.35)
    axes[1].legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=1,
        frameon=False,
        fontsize=8,
        handlelength=1.2,
        borderaxespad=0.0,
    )
    fig.suptitle("Client-cooperative ReAct recovery restores progress without replay", y=0.98)
    fig.subplots_adjust(top=0.78, bottom=0.34, wspace=0.28)
    save(fig, "fig_jss_recovery_effectiveness")
    return "fig_jss_recovery_effectiveness"


def plot_distributed_correctness() -> str:
    rows = read_csv(
        ART
        / "cloudlab_business_workflow_distributed_deterministic_v1"
        / "cloudlab_business_workflow_distributed_deterministic_agg.csv"
    )
    stores = ["redis", "memory"]
    labels = ["Redis shared\nstate", "Memory-local\ncontrol"]
    x = list(range(len(stores)))
    metrics = [
        ("resume_recovered_rate", "Resume recovered rate"),
        ("state_miss_rate", "State miss rate"),
        ("passed_rate", "Probe pass rate"),
    ]

    fig, ax = plt.subplots(figsize=(5.4, 2.8))
    width = 0.24
    for j, (key, label) in enumerate(metrics):
        vals = [num(pick(rows, store=s), key) for s in stores]
        ax.bar(
            [i + (j - 1) * width for i in x],
            vals,
            width=width,
            label=label,
            color=["#4C78A8", "#F58518", "#54A24B"][j],
            edgecolor="#333333",
            linewidth=0.6,
        )
    for i, store in enumerate(stores):
        row = pick(rows, store=store)
        ax.text(i, 1.06, f"{int(num(row, 'resume_recovered_count'))}/{int(num(row, 'probe_count'))} recovered", ha="center", fontsize=8)
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("Rate")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_title("CloudLab deterministic cross-gateway recovery")
    ax.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    ax.grid(axis="y", alpha=0.35)
    save(fig, "fig_jss_distributed_correctness")
    return "fig_jss_distributed_correctness"


def plot_overhead_sensitivity() -> str:
    overhead = read_csv(ART / "operator_overhead_v1" / "operator_overhead_agg.csv")
    sensitivity = read_csv(ART / "config_sensitivity_v1" / "config_sensitivity_agg.csv")

    fig, axes = plt.subplots(1, 3, figsize=(9.2, 2.8))

    concs = ["20", "50", "100"]
    for variant, color in [("baseline_gateway", COLORS["baseline_gateway"]), ("plangate_adaptive", COLORS["plangate_adaptive"])]:
        rows = [pick(overhead, variant=variant, concurrency=c) for c in concs]
        axes[0].errorbar(
            [int(c) for c in concs],
            [num(r, "p95_ms_mean") for r in rows],
            yerr=[num(r, "p95_ms_std") for r in rows],
            marker="o",
            label=LABELS[variant].replace("\n", " "),
            color=color,
            capsize=3,
        )
        axes[1].errorbar(
            [int(c) for c in concs],
            [num(r, "throughput_workflows_s_mean") for r in rows],
            yerr=[num(r, "throughput_workflows_s_std") for r in rows],
            marker="o",
            label=LABELS[variant].replace("\n", " "),
            color=color,
            capsize=3,
        )
    axes[0].set_title("Operator latency overhead")
    axes[0].set_xlabel("Concurrency")
    axes[0].set_ylabel("P95 latency (ms)")
    axes[1].set_title("Operator throughput")
    axes[1].set_xlabel("Concurrency")
    axes[1].set_ylabel("Workflows / s")
    axes[0].legend(frameon=False)
    axes[1].legend(frameon=False)

    intensities = ["light", "default", "strict"]
    rows = [
        pick(
            sensitivity,
            variant=f"plangate_adaptive_react_recovery_{g}",
            governance_intensity=g,
            concurrency="100",
            failure_rate="0.2",
        )
        for g in intensities
    ]
    axes[2].bar(
        range(len(intensities)),
        [num(r, "react_recovered_success_mean") for r in rows],
        color=["#A0CBE8", "#54A24B", "#F58518"],
        edgecolor="#333333",
        linewidth=0.6,
    )
    axes[2].set_xticks(range(len(intensities)))
    axes[2].set_xticklabels([g.capitalize() for g in intensities])
    axes[2].set_ylabel("Recovered sessions / run")
    axes[2].set_title("Governance-intensity sensitivity")
    for ax in axes:
        ax.grid(axis="y", alpha=0.35)
    fig.suptitle("PlanGate overhead is bounded and recovery signal persists across settings", y=1.06)
    save(fig, "fig_jss_overhead_sensitivity")
    return "fig_jss_overhead_sensitivity"


def plot_backend_boundary() -> str:
    cloud = read_csv(ART / "cloud_backend_smoke_v1" / "cloud_backend_smoke_agg.csv")
    full = read_csv(ART / "adaptive_step0_react_recovery_full_v1" / "adaptive_step0_react_recovery_full_agg.csv")
    variants = ["plangate_strict", "plangate_adaptive", "plangate_no_capacity_step0", "plangate_adaptive_react_recovery"]
    labels = [LABELS[v] for v in variants]

    fig, axes = plt.subplots(1, 2, figsize=(7.8, 3.2), sharey=False)
    x = list(range(len(variants)))
    width = 0.24

    cloud_rows = [pick(cloud, variant=v) for v in variants]
    cloud_metrics = [
        ("success_mean", "Success", "#4C78A8"),
        ("cascade_failed_mean", "Cascade", "#E45756"),
        ("rejected_s0_capacity_mean", "Step-0 rejected", "#F58518"),
    ]
    for j, (key, label, color) in enumerate(cloud_metrics):
        axes[0].bar(
            [i + (j - 1) * width for i in x],
            [num(r, key) for r in cloud_rows],
            width=width,
            label=label,
            color=color,
            edgecolor="#333333",
            linewidth=0.6,
        )
    axes[0].set_title("Cloud backend smoke (single repeat)")
    axes[0].set_ylabel("Sessions / run")

    vllm_rows = [
        pick(
            full,
            variant=v,
            layer="vllm_full",
            concurrency="16",
            synthetic_recoverable_failure="False",
        )
        for v in variants
    ]
    vllm_metrics = [
        ("success_mean", "Success", "#4C78A8"),
        ("cascade_failed_mean", "Cascade", "#E45756"),
        ("abd_total_mean", "ABD%", "#9467BD"),
    ]
    for j, (key, label, color) in enumerate(vllm_metrics):
        axes[1].bar(
            [i + (j - 1) * width for i in x],
            [num(r, key) for r in vllm_rows],
            width=width,
            label=label,
            color=color,
            edgecolor="#333333",
            linewidth=0.6,
        )
    axes[1].set_title("Local vLLM full, C=16 (3 repeats)")
    axes[1].set_ylabel("Mean count or percentage")

    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=25, ha="right")
        ax.grid(axis="y", alpha=0.35)
        ax.legend(frameon=False, fontsize=7.5, ncol=1)
    fig.suptitle("Backend elasticity changes the admission-control trade-off", y=1.02)
    fig.subplots_adjust(top=0.82, wspace=0.28)
    save(fig, "fig_jss_backend_boundary")
    return "fig_jss_backend_boundary"


def main() -> None:
    setup_style()
    names = [
        plot_core_tradeoff(),
        plot_recovery_effectiveness(),
        plot_distributed_correctness(),
        plot_overhead_sensitivity(),
        plot_backend_boundary(),
    ]
    print(f"wrote {len(names)} JSS figures to {OUT}")
    for name in names:
        print(f"  {name}.png")


if __name__ == "__main__":
    main()
