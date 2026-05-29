#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = REPO_ROOT / "artifact_results"
FIG_ROOT = REPO_ROOT / "paper" / "figures"

CORE_SUMMARY = ARTIFACT_ROOT / "mock_regression_p4_refresh_v1" / "exp1_core_summary.csv"
P3_ABLATION_AGG = (
    ARTIFACT_ROOT / "p3_failure_mechanism_ablation_v1" / "p3_failure_mechanism_ablation_agg.csv"
)
EXP11_AGG = (
    ARTIFACT_ROOT / "exp11_newmechanismablation_v1" / "exp11_newmechanismablation_agg.csv"
)
TPUT_AGG = ARTIFACT_ROOT / "throughput_latency_summary_v1" / "throughput_latency_agg.csv"
GLM_AGG = ARTIFACT_ROOT / "glm_real_llm_c10_refresh_v1" / "week5_agg.csv"
DEEPSEEK_AGG = ARTIFACT_ROOT / "deepseek_v4_flash_smoke_v1" / "week5_agg.csv"
SELFHOSTED_VLLM_AGG = (
    ARTIFACT_ROOT
    / "selfhosted_vllm_stress_c16w8_tuned_5gw_v1"
    / "selfhosted_vllm_stress_agg.csv"
)
SELFHOSTED_VLLM_PROFILE_SWEEP_AGG = (
    ARTIFACT_ROOT
    / "selfhosted_vllm_profile_sweep_v1"
    / "selfhosted_vllm_profile_sweep_agg.csv"
)
P3_FAILURE_AMENDMENT_GRID_AGG = (
    ARTIFACT_ROOT
    / "p3_failure_amendment_grid_v1"
    / "p3_failure_amendment_grid_agg.csv"
)

GATEWAY_LABELS = {
    "ng": "NoGov",
    "srl": "SRL",
    "sbac": "SBAC",
    "plangate_full": "PlanGate",
    "plangate_real": "PlanGate",
    "wo_commitment": "w/o-Commitment",
    "wo_amendment": "w/o-Amendment",
    "wo_recovery": "w/o-Recovery",
    "rajomon": "Rajomon",
    "pp": "PP",
    "static": "Static",
    "plangate_relaxed": "PlanGate (tuned)",
}

ORDER = [
    "ng",
    "srl",
    "sbac",
    "plangate_full",
    "wo_commitment",
    "wo_amendment",
    "wo_recovery",
    "rajomon",
    "pp",
    "plangate_real",
    "static",
    "plangate_relaxed",
]

BAR_FACE = {
    "ng": "#f2f2f2",
    "srl": "#d9d9d9",
    "sbac": "#bdbdbd",
    "plangate_full": "#6c7a89",
    "plangate_real": "#6c7a89",
    "wo_commitment": "#e0e0e0",
    "wo_amendment": "#cfcfcf",
    "wo_recovery": "#9e9e9e",
    "rajomon": "#c7d4dd",
    "pp": "#d8c7dd",
    "static": "#ece6c8",
    "plangate_relaxed": "#5b6c5d",
}

BAR_HATCH = {
    "ng": "",
    "srl": "//",
    "sbac": "..",
    "plangate_full": "xx",
    "plangate_real": "xx",
    "wo_commitment": "\\\\",
    "wo_amendment": "--",
    "wo_recovery": "++",
    "rajomon": "oo",
    "pp": "**",
    "static": "||",
    "plangate_relaxed": "xx",
}

LINE_STYLE = {
    "ng": ("#4d4d4d", "o", "-"),
    "srl": ("#7f7f7f", "s", "--"),
    "sbac": ("#9e9e9e", "^", "-."),
    "plangate_full": ("#1f3b4d", "D", "-"),
    "plangate_real": ("#1f3b4d", "D", "-"),
    "static": ("#8a7d3a", "v", "--"),
    "rajomon": ("#617d8a", "P", "-."),
    "pp": ("#8b5e8b", "X", ":"),
    "plangate_relaxed": ("#2d4a34", "D", "-"),
}


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"required artifact missing: {path}")


def read_csv(path: Path) -> list[dict[str, str]]:
    require_file(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = []
        for row in csv.DictReader(f):
            rows.append({(key or "").strip(): value for key, value in row.items()})
        return rows


def gateway_label(name: str) -> str:
    return GATEWAY_LABELS.get(name, name)


def order_key(name: str) -> tuple[int, str]:
    try:
        return (ORDER.index(name), name)
    except ValueError:
        return (len(ORDER), name)


def gateway_style(name: str) -> tuple[str, str]:
    return BAR_FACE.get(name, "#d9d9d9"), BAR_HATCH.get(name, "")


def save_figure(fig: plt.Figure, stem: str, tight_rect: tuple[float, float, float, float] | None = None) -> None:
    FIG_ROOT.mkdir(parents=True, exist_ok=True)
    pdf = FIG_ROOT / f"{stem}.pdf"
    png = FIG_ROOT / f"{stem}.png"
    fig.tight_layout(rect=tight_rect)
    with pdf.open("wb") as pdf_fh:
        fig.savefig(pdf_fh, format="pdf", bbox_inches="tight")
    with png.open("wb") as png_fh:
        fig.savefig(png_fh, format="png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def grouped_means(rows: list[dict[str, str]], key: str, value_fields: list[str]) -> list[dict[str, float | str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    out: list[dict[str, float | str]] = []
    for group_key in sorted(grouped.keys(), key=order_key):
        bucket = grouped[group_key]
        record: dict[str, float | str] = {key: group_key}
        for field in value_fields:
            record[field] = mean(float(item[field]) for item in bucket)
        out.append(record)
    return out


def bar_panel(ax: plt.Axes, names: list[str], values: list[float], ylabel: str, title: str) -> None:
    x = list(range(len(names)))
    labels = [gateway_label(name) for name in names]
    for idx, name in enumerate(names):
        face, hatch = gateway_style(name)
        ax.bar(
            idx,
            values[idx],
            width=0.72,
            color=face,
            edgecolor="black",
            linewidth=0.9,
            hatch=hatch,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", linestyle=":", linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)


def make_core_effgp_cascade() -> None:
    rows = read_csv(CORE_SUMMARY)
    agg = grouped_means(rows, "gateway", ["effective_goodput_s", "cascade_failed"])
    names = [str(row["gateway"]) for row in agg]
    effgp = [float(row["effective_goodput_s"]) for row in agg]
    cascade = [float(row["cascade_failed"]) for row in agg]

    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.5))
    bar_panel(axes[0], names, effgp, "EffGP/s", "Core Mock Effective Goodput")
    bar_panel(axes[1], names, cascade, "Cascade Failed Sessions", "Core Mock Cascade Failure")
    save_figure(fig, "fig_core_effgp_cascade")


def make_p3_mechanism_ablation() -> None:
    rows = sorted(read_csv(P3_ABLATION_AGG), key=lambda row: order_key(row["gateway"]))
    names = [row["gateway"] for row in rows]
    metrics = [
        ("success_mean", "Successful Sessions", "Success"),
        ("cascade_failed_mean", "Cascade Failed Sessions", "Cascade Failure"),
        ("recovery_success_mean", "Recovery Successes", "Recovery Success"),
        ("amendment_success_mean", "Amendment Successes", "Amendment Success"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(9.0, 6.2))
    for ax, (field, ylabel, title) in zip(axes.flatten(), metrics):
        values = [float(row[field]) for row in rows]
        bar_panel(ax, names, values, ylabel, title)
    fig.suptitle("P3 Failure/Amendment Mechanism Ablation", y=1.02, fontsize=12)
    save_figure(fig, "fig_p3_mechanism_ablation")


def make_exp11_ablation() -> None:
    rows = sorted(read_csv(EXP11_AGG), key=lambda row: order_key(row["gateway"]))
    names = [row["gateway"] for row in rows]
    metrics = [
        ("success_mean", "Successful Sessions", "Success"),
        ("effective_goodput_s_mean", "EffGP/s", "Effective Goodput"),
        ("cascade_failed_mean", "Cascade Failed Sessions", "Cascade Failure"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.5))
    for ax, (field, ylabel, title) in zip(axes, metrics):
        values = [float(row[field]) for row in rows]
        bar_panel(ax, names, values, ylabel, title)
    fig.suptitle("Exp11 New Mechanism Ablation (Mock Diagnostic)", y=1.02, fontsize=12)
    save_figure(fig, "fig_exp11_new_mechanism_ablation")


def make_throughput_vs_effective() -> None:
    rows = read_csv(TPUT_AGG)
    experiments = ["Exp1_Core", "Exp5_ScaleConc", "Exp6_ScaleConcReact", "Exp10_Adversarial"]
    fig, axes = plt.subplots(2, 2, figsize=(10.2, 6.6), sharey=False)
    width = 0.36
    for ax, experiment in zip(axes.flatten(), experiments):
        subset = [row for row in rows if row["experiment"] == experiment]
        grouped = grouped_means(subset, "gateway", ["raw_goodput_s_mean", "effective_goodput_s_mean"])
        names = [str(row["gateway"]) for row in grouped]
        x = list(range(len(names)))
        raw_vals = [float(row["raw_goodput_s_mean"]) for row in grouped]
        eff_vals = [float(row["effective_goodput_s_mean"]) for row in grouped]
        for idx, name in enumerate(names):
            face, hatch = gateway_style(name)
            ax.bar(
                idx - width / 2,
                raw_vals[idx],
                width=width,
                color="white",
                edgecolor="black",
                linewidth=0.9,
                hatch=hatch,
                label="RawGP/s" if idx == 0 else None,
            )
            ax.bar(
                idx + width / 2,
                eff_vals[idx],
                width=width,
                color=face,
                edgecolor="black",
                linewidth=0.9,
                hatch=hatch,
                label="EffGP/s" if idx == 0 else None,
            )
        ax.set_xticks(x)
        ax.set_xticklabels([gateway_label(name) for name in names], rotation=20, ha="right")
        ax.set_ylabel("GP/s")
        ax.set_title(experiment.replace("_", " "))
        ax.grid(axis="y", linestyle=":", linewidth=0.6, alpha=0.8)
        ax.set_axisbelow(True)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.98), ncol=2, frameon=False)
    fig.suptitle("Raw Throughput vs Effective Goodput (Existing Evidence)", y=1.02, fontsize=12)
    save_figure(fig, "fig_throughput_effective_goodput", tight_rect=(0, 0, 1, 0.93))


def plot_scale_panel(ax: plt.Axes, rows: list[dict[str, str]], metric: str, ylabel: str, title: str) -> None:
    gateways = sorted({row["gateway"] for row in rows}, key=order_key)
    for gateway in gateways:
        subset = sorted(
            (row for row in rows if row["gateway"] == gateway),
            key=lambda row: float(row["sweep_val"]),
        )
        xs = [float(row["sweep_val"]) for row in subset]
        ys = [float(row[metric]) for row in subset]
        color, marker, linestyle = LINE_STYLE.get(gateway, ("#6c6c6c", "o", "-"))
        ax.plot(
            xs,
            ys,
            color=color,
            marker=marker,
            linestyle=linestyle,
            linewidth=1.5,
            markersize=5,
            label=gateway_label(gateway),
        )
    ax.set_xlabel("Concurrency")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)


def make_scale_concurrency() -> None:
    rows = read_csv(TPUT_AGG)
    exp5 = [row for row in rows if row["experiment"] == "Exp5_ScaleConc" and row["sweep_key"] == "conc"]
    exp6 = [row for row in rows if row["experiment"] == "Exp6_ScaleConcReact" and row["sweep_key"] == "conc"]

    fig, axes = plt.subplots(2, 2, figsize=(10.0, 6.4), sharex=True)
    plot_scale_panel(
        axes[0, 0],
        exp5,
        "effective_goodput_s_mean",
        "EffGP/s",
        "Exp5 ScaleConc Effective Goodput",
    )
    plot_scale_panel(
        axes[0, 1],
        exp5,
        "e2e_p95_ms_mean",
        "E2E P95 ms",
        "Exp5 ScaleConc Latency",
    )
    plot_scale_panel(
        axes[1, 0],
        exp6,
        "effective_goodput_s_mean",
        "EffGP/s",
        "Exp6 ScaleConcReact Effective Goodput",
    )
    plot_scale_panel(
        axes[1, 1],
        exp6,
        "e2e_p95_ms_mean",
        "E2E P95 ms",
        "Exp6 ScaleConcReact Latency",
    )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.99), ncol=4, frameon=False)
    save_figure(fig, "fig_scale_concurrency_effgp_latency", tight_rect=(0, 0, 1, 0.93))


def make_real_llm_smoke() -> None:
    glm_rows = sorted(read_csv(GLM_AGG), key=lambda row: order_key(row["gateway"]))
    deepseek_rows = sorted(read_csv(DEEPSEEK_AGG), key=lambda row: order_key(row["gateway"]))
    providers = [
        ("GLM-4-Flash C10", glm_rows),
        ("DeepSeek V4 Flash C5", deepseek_rows),
    ]
    metrics = [
        ("success_rate_mean", "Success Rate (%)", "Success"),
        ("abd_mean", "ABD", "Abandonment Burden"),
        ("eff_gps_mean", "EffGP/s", "Effective Goodput"),
    ]

    fig, axes = plt.subplots(2, 3, figsize=(11.0, 6.4))
    for row_idx, (provider_label, rows) in enumerate(providers):
        names = [row["gateway"] for row in rows]
        for col_idx, (field, ylabel, short_title) in enumerate(metrics):
            ax = axes[row_idx, col_idx]
            values = [float(row[field]) for row in rows]
            bar_panel(ax, names, values, ylabel, f"{provider_label}: {short_title}")
    fig.suptitle("Real-LLM Smoke / Support Evidence", y=1.02, fontsize=12)
    save_figure(fig, "fig_real_llm_smoke")


def make_selfhosted_vllm_stress() -> None:
    rows = read_csv(SELFHOSTED_VLLM_AGG)
    display_order = ["ng", "static", "pp", "rajomon", "plangate_relaxed"]
    row_map = {row["gateway"]: row for row in rows}
    display_subset = [row_map[name] for name in display_order if name in row_map]
    names = [row["gateway"] for row in display_subset]

    metrics = [
        ("success_rate_mean", "Success Rate (%)", "Success Rate"),
        ("abd_total_mean", "ABD (%)", "Admitted-but-Doomed Rate"),
        ("all_rejected_mean", "Rejected Sessions", "All-Rejected Sessions"),
        ("p95_ms_mean", "P95 (s)", "Tail Latency"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(9.2, 6.0))
    for ax, (field, ylabel, title) in zip(axes.flatten(), metrics):
        values = [float(row[field]) for row in display_subset]
        if field == "p95_ms_mean":
            values = [value / 1000.0 for value in values]
        bar_panel(ax, names, values, ylabel, title)
    fig.suptitle(
        "Self-Hosted vLLM Stress (Main-Paper Display Subset)",
        y=1.02,
        fontsize=12,
    )
    save_figure(fig, "fig_selfhosted_vllm_stress")


def make_selfhosted_vllm_profile_sweep() -> None:
    rows = read_csv(SELFHOSTED_VLLM_PROFILE_SWEEP_AGG)
    display_order = ["ng", "static", "pp", "rajomon", "plangate_relaxed"]
    metrics = [
        ("success_rate_mean", "Success Rate (%)", "Success Rate (higher is better)"),
        ("abd_total_mean", "ABD (%)", "Admitted-but-Doomed (lower is better)"),
        ("cascade_agents_mean", "Cascade Agents", "Cascade Pressure (lower is better)"),
    ]

    # Keep only the paper display subset and known concurrency points.
    filtered = [
        row
        for row in rows
        if row["gateway"] in display_order and str(row["concurrency"]).strip() in {"8", "12", "16", "20"}
    ]
    x_values = [8, 12, 16, 20]

    fig, axes = plt.subplots(1, 3, figsize=(11.2, 3.8), sharex=True)
    for ax, (field, ylabel, title) in zip(axes, metrics):
        for gateway in display_order:
            subset = sorted(
                (row for row in filtered if row["gateway"] == gateway),
                key=lambda row: int(str(row["concurrency"]).strip()),
            )
            if not subset:
                continue
            xs = [int(str(row["concurrency"]).strip()) for row in subset]
            ys = [float(row[field]) for row in subset]
            color, marker, linestyle = LINE_STYLE.get(gateway, ("#6c6c6c", "o", "-"))
            ax.plot(
                xs,
                ys,
                color=color,
                marker=marker,
                linestyle=linestyle,
                linewidth=1.7,
                markersize=5,
                label=gateway_label(gateway),
            )
        ax.set_xticks(x_values)
        ax.set_xlabel("Concurrency")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.8)
        ax.set_axisbelow(True)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.02), ncol=5, frameon=False)
    fig.suptitle(
        "Self-Hosted vLLM Multi-Intensity Boundary Characterization",
        y=1.10,
        fontsize=12,
    )
    save_figure(fig, "fig_selfhosted_vllm_profile_sweep", tight_rect=(0, 0, 1, 0.9))


def make_p3_failure_amendment_grid() -> None:
    rows = read_csv(P3_FAILURE_AMENDMENT_GRID_AGG)
    display_order = ["plangate_full", "wo_commitment", "wo_amendment", "wo_recovery"]
    gateway_rows = [row for row in rows if row["gateway"] in display_order]
    cell_order = [("0.1", "0.1"), ("0.1", "0.2"), ("0.2", "0.1"), ("0.2", "0.2"), ("0.3", "0.1"), ("0.3", "0.2")]
    x = list(range(len(cell_order)))
    xlabels = [f"f={fr}\na={ar}" for fr, ar in cell_order]
    metrics = [
        ("success_mean", "Successful Sessions", "Success"),
        ("cascade_failed_mean", "Cascade Failed Sessions", "Cascade Failure"),
        ("recovery_success_mean", "Recovery Successes", "Recovery Success"),
        ("amendment_success_mean", "Amendment Successes", "Amendment Success"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(10.2, 6.4), sharex=True)
    for ax, (field, ylabel, title) in zip(axes.flatten(), metrics):
        for gateway in display_order:
            subset = {
                (row["failure_rate"], row["amendment_rate"]): row
                for row in gateway_rows
                if row["gateway"] == gateway
            }
            ys = [float(subset[(fr, ar)][field]) for fr, ar in cell_order if (fr, ar) in subset]
            xs = [idx for idx, pair in enumerate(cell_order) if pair in subset]
            color, marker, linestyle = LINE_STYLE.get(gateway, ("#6c6c6c", "o", "-"))
            ax.plot(
                xs,
                ys,
                color=color,
                marker=marker,
                linestyle=linestyle,
                linewidth=1.6,
                markersize=5,
                label=gateway_label(gateway),
            )
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels(xlabels)
        ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.8)
        ax.set_axisbelow(True)

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.99), ncol=4, frameon=False)
    fig.suptitle("P3 Failure/Amendment Grid Boundary Characterization", y=1.03, fontsize=12)
    save_figure(fig, "fig_p3_failure_amendment_grid", tight_rect=(0, 0, 1, 0.92))


def main() -> None:
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    FIG_ROOT.mkdir(parents=True, exist_ok=True)
    make_core_effgp_cascade()
    make_p3_mechanism_ablation()
    make_exp11_ablation()
    make_throughput_vs_effective()
    make_scale_concurrency()
    make_real_llm_smoke()
    make_selfhosted_vllm_stress()
    make_selfhosted_vllm_profile_sweep()
    make_p3_failure_amendment_grid()
    print(f"wrote figures to paper/figures: {FIG_ROOT}")


if __name__ == "__main__":
    main()
