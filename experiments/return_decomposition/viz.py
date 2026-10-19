from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import analysis as a
import data

FIG_DIR = Path(__file__).resolve().parent / "figures"
plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False

LAYER_COLORS = {"global": "#4c72b0", "market": "#8c8c8c", "sector": "#dd8452", "group": "#8172b3", "idio": "#55a868"}
LAYER_LABELS = {"global": "미국", "market": "시장", "sector": "업종", "group": "그룹", "idio": "고유"}
METHOD_LABELS = {
    "sequential_4l": "베타 1 (순서대로)",
    "ols_4l": "회귀 베타",
    "ols_4l_reclass": "회귀 베타 + 재분류",
}
METHOD_COLORS = {"sequential_4l": "#c44e52", "ols_4l": "#4c72b0", "ols_4l_reclass": "#55a868"}
STAB_LABELS = {"roll120": "창 120일", "roll250": "창 250일", "ewm_hl60": "지수 가중 (반감기 60일)",
               "roll120_clip": "창 120일 + 범위 제한"}
STAB_COLORS = {"roll120": "#4c72b0", "roll250": "#c44e52", "ewm_hl60": "#55a868", "roll120_clip": "#dd8452"}
STAB_SYMBOLS = ["000660", "006400", "105560", "015760"]
GROUP_LABELS = {"no_group": "그룹 층 없음", "with_group": "그룹 층 있음"}
BETA_SYMBOLS = ["000660", "005930", "006400", "105560", "035420", "015760"]
BETA_COLS = {"spy": "미국(SPY)", "m_res": "시장", "s_res": "업종"}


def _names(dataset: dict[str, pd.DataFrame]) -> dict[str, str]:
    return dict(zip(dataset["sector_map"]["symbol"], dataset["sector_map"]["name"]))


def plot_beta_stability(beta_panel: pd.DataFrame, names: dict[str, str], method: str) -> Path:
    panel = beta_panel[beta_panel["method"] == method]
    fig, axes = plt.subplots(2, 3, figsize=(15, 7.5), sharex=True)
    for ax, symbol in zip(axes.flat, BETA_SYMBOLS):
        sub = panel[panel["symbol"] == symbol].set_index("date")
        for col, label in BETA_COLS.items():
            ax.plot(sub.index, sub[col], label=label, lw=1.6)
        ax.axhline(0, color="black", lw=0.6)
        ax.axhline(1, color="black", lw=0.6, ls=":")
        ax.set_title(f"{names[symbol]} ({symbol})")
        ax.tick_params(axis="x", rotation=30)
    axes.flat[0].legend(loc="upper left", fontsize=9)
    fig.suptitle(f"rolling 베타의 시간 변화 ({METHOD_LABELS[method]}, 창 {a.OLS_WINDOW}일)", fontsize=14)
    fig.tight_layout()
    path = FIG_DIR / f"1_beta_stability_{method}.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_corr_sector_idio(metrics: pd.DataFrame, names: dict[str, str]) -> Path:
    corr = metrics["corr_sector_idio"].unstack("method")
    corr = corr.sort_values("sequential_4l")
    fig, ax = plt.subplots(figsize=(9, 11))
    y = np.arange(len(corr))
    for method in METHOD_LABELS:
        if method in corr:
            ax.scatter(corr[method], y, label=METHOD_LABELS[method], color=METHOD_COLORS[method], s=28)
    ax.axvline(0, color="black", lw=0.8)
    ax.set_yticks(y, [names[s] for s in corr.index], fontsize=8)
    ax.set_xlabel("업종 층과 고유 층의 상관 (0에 가까울수록 업종 층이 노이즈를 덜 섞음)")
    ax.set_title("종목별 업종 층과 고유 층의 상관")
    ax.legend(loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    path = FIG_DIR / "2_corr_sector_idio.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_method_summary(metrics: pd.DataFrame) -> Path:
    summary = metrics.groupby("method").median().reindex([m for m in METHOD_LABELS if m in metrics.index.get_level_values(0)])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.8))

    ax1.bar([METHOD_LABELS[m] for m in summary.index], summary["explained_share"],
            color=[METHOD_COLORS[m] for m in summary.index])
    for i, v in enumerate(summary["explained_share"]):
        ax1.text(i, v + 0.005, f"{v:.2f}", ha="center")
    ax1.set_title("설명 비율 (종목 중앙값)")
    ax1.set_ylim(0, max(summary["explained_share"]) * 1.25)

    left = np.zeros(len(summary))
    for layer in a.LAYERS:
        vals = summary[f"dominant_{layer}"].to_numpy()
        ax2.barh([METHOD_LABELS[m] for m in summary.index], vals, left=left,
                 color=LAYER_COLORS[layer], label=LAYER_LABELS[layer])
        for i, v in enumerate(vals):
            if v > 0.06:
                ax2.text(left[i] + v / 2, i, f"{v:.0%}", ha="center", va="center", color="white", fontsize=9)
        left += vals
    ax2.set_title("큰 움직임 날(|r| >= 1%)의 지배 층 비율 (종목 중앙값)")
    ax2.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    path = FIG_DIR / "3_method_summary.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_reclass_heatmap(gains: pd.DataFrame, betas: pd.DataFrame, choice: pd.DataFrame,
                         dataset: dict[str, pd.DataFrame], names: dict[str, str]) -> Path:
    shown = gains.where(betas > 0, 0.0)
    order = choice.sort_values("kospi_sector").index
    shown = shown.loc[order]
    default_code = dataset["sector_map"].set_index("symbol")["kospi_sector_code"]
    chosen_code = choice["chosen_sector"].map({v: k for k, v in data.KOSPI_SECTOR_NAMES.items()})

    fig, ax = plt.subplots(figsize=(14, 12))
    im = ax.imshow(shown.to_numpy(), cmap="Blues", vmin=0, vmax=0.25, aspect="auto")
    cols = list(shown.columns)
    for i, symbol in enumerate(shown.index):
        d = default_code.get(symbol)
        c = chosen_code.get(symbol)
        if isinstance(d, str) and d in cols:
            ax.scatter(cols.index(d), i, marker="s", facecolors="none", edgecolors="red", s=110, lw=1.6)
        if isinstance(c, str) and c in cols:
            ax.scatter(cols.index(c), i, marker="*", color="orange", s=120)
    ax.set_xticks(range(len(cols)), [data.KOSPI_SECTOR_NAMES[c] for c in cols], rotation=60, ha="right", fontsize=8)
    ax.set_yticks(range(len(shown)), [f"{names[s]} ({choice.loc[s, 'kospi_sector']})" for s in shown.index], fontsize=8)
    ax.set_title("업종 후보별 추가 설명력 (선택 구간 2025-04에서 2025-10)\n빨간 네모: 현재 KOSPI 업종, 주황 별: 데이터로 고른 업종")
    fig.colorbar(im, ax=ax, fraction=0.025, label="r2 증가분 (베타가 음수면 0)")
    fig.tight_layout()
    path = FIG_DIR / "4_reclass_heatmap.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_cases(decomp: pd.DataFrame, cases: list[tuple[str, str]], names: dict[str, str]) -> Path:
    methods = [m for m in METHOD_LABELS if m in decomp["method"].unique()]
    fig, axes = plt.subplots(len(cases), 1, figsize=(11, 2.0 * len(cases)), sharex=True)
    for ax, (symbol, day) in zip(axes, cases):
        rows = decomp[(decomp["symbol"] == symbol) & (decomp["date"] == pd.Timestamp(day))].set_index("method")
        for j, method in enumerate(methods):
            if method not in rows.index:
                continue
            pos, neg = 0.0, 0.0
            for layer in a.LAYERS:
                v = rows.loc[method, layer] * 100
                start = pos if v >= 0 else neg
                ax.barh(j, v, left=start, color=LAYER_COLORS[layer],
                        label=LAYER_LABELS[layer] if (j == 0 and ax is axes[0]) else None)
                if v >= 0:
                    pos += v
                else:
                    neg += v
        r = rows["r"].iloc[0] * 100
        ax.axvline(0, color="black", lw=0.6)
        ax.set_yticks(range(len(methods)), [METHOD_LABELS[m] for m in methods], fontsize=8)
        ax.set_title(f"{names[symbol]} {day}  r = {r:+.1f}%", fontsize=10, loc="left")
    axes[0].legend(ncol=4, loc="upper right", fontsize=8)
    axes[-1].set_xlabel("층별 기여 (%p)")
    fig.tight_layout()
    path = FIG_DIR / "5_cases.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_stability_lines(beta_panel: pd.DataFrame, names: dict[str, str]) -> Path:
    fig, axes = plt.subplots(2, len(STAB_SYMBOLS), figsize=(17, 7), sharex=True)
    for j, symbol in enumerate(STAB_SYMBOLS):
        for i, (col, label) in enumerate([("m_res", "시장 베타"), ("s_res", "업종 베타")]):
            ax = axes[i, j]
            for method in STAB_LABELS:
                sub = beta_panel[(beta_panel["method"] == method) & (beta_panel["symbol"] == symbol)].set_index("date")
                ax.plot(sub.index, sub[col], label=STAB_LABELS[method], color=STAB_COLORS[method], lw=1.4)
            ax.axhline(0, color="black", lw=0.6)
            ax.set_title(f"{names[symbol]}: {label}", fontsize=10)
            ax.tick_params(axis="x", rotation=30)
    axes[0, 0].legend(fontsize=8, loc="best")
    fig.suptitle("베타 추정 방식별 시장 베타와 업종 베타의 시간 변화", fontsize=14)
    fig.tight_layout()
    path = FIG_DIR / "6_stability_lines.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_stability_summary(summary: pd.DataFrame) -> Path:
    panels = [
        ("explained_share", "설명 비율 (높을수록 좋음)"),
        ("corr_sector_idio", "업종 층과 고유 층의 상관 (0에 가까울수록 좋음)"),
        ("s_res_step", "업종 베타의 하루 변화량 중앙값 (작을수록 안정)"),
        ("s_res_range", "업종 베타의 최대-최소 범위 (작을수록 안정)"),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.5))
    methods = list(summary.index)
    for ax, (col, title) in zip(axes, panels):
        vals = summary[col]
        ax.bar([STAB_LABELS[m] for m in methods], vals, color=[STAB_COLORS[m] for m in methods])
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.3f}", ha="center", va="bottom", fontsize=9)
        ax.set_title(title, fontsize=10)
        ax.tick_params(axis="x", rotation=25, labelsize=8)
    fig.tight_layout()
    path = FIG_DIR / "7_stability_summary.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_group_gain(by_symbol: pd.DataFrame) -> Path:
    df = by_symbol.sort_values("gain")
    fig, ax = plt.subplots(figsize=(10, 8))
    y = np.arange(len(df))
    ax.hlines(y, df["no_group"], df["with_group"], color="#bbbbbb", lw=2)
    ax.scatter(df["no_group"], y, color="#4c72b0", label=GROUP_LABELS["no_group"], zorder=3)
    ax.scatter(df["with_group"], y, color="#8172b3", label=GROUP_LABELS["with_group"], zorder=3)
    for yi, (_, row) in zip(y, df.iterrows()):
        ax.text(max(row["no_group"], row["with_group"]) + 0.01, yi, f"{row['gain']:+.2f}  그룹 베타 {row['grp_beta_last']:+.2f}",
                va="center", fontsize=8)
    ax.set_yticks(y, [f"{n} ({g})" for n, g in zip(df["name"], df["group"])], fontsize=9)
    ax.set_xlabel("설명 비율")
    ax.set_xlim(0, 1.05)
    ax.set_title("그룹 층을 넣었을 때 종목별 설명 비율 변화 (같은 그룹의 다른 종목 동일 가중 바스켓)")
    ax.legend(loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    path = FIG_DIR / "8_group_gain.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_group_cases(decomp: pd.DataFrame, symbols: list[str], per_symbol: int, names: dict[str, str]) -> Path:
    cases = []
    base = decomp[decomp["method"] == "with_group"]
    for symbol in symbols:
        sub = base[base["symbol"] == symbol]
        top = sub.reindex(sub["group"].abs().sort_values(ascending=False).index).head(per_symbol)
        cases += [(symbol, d) for d in top["date"]]
    methods = list(GROUP_LABELS)
    fig, axes = plt.subplots(len(cases), 1, figsize=(11, 1.6 * len(cases)), sharex=True)
    for ax, (symbol, day) in zip(axes, cases):
        rows = decomp[(decomp["symbol"] == symbol) & (decomp["date"] == day)].set_index("method")
        for j, method in enumerate(methods):
            pos, neg = 0.0, 0.0
            for layer in a.LAYERS:
                v = rows.loc[method, layer] * 100
                ax.barh(j, v, left=pos if v >= 0 else neg, color=LAYER_COLORS[layer],
                        label=LAYER_LABELS[layer] if (j == 1 and ax is axes[0]) else None)
                if v >= 0:
                    pos += v
                else:
                    neg += v
        ax.axvline(0, color="black", lw=0.6)
        ax.set_yticks(range(len(methods)), [GROUP_LABELS[m] for m in methods], fontsize=8)
        ax.set_title(f"{names[symbol]} {day.date()}  r = {rows['r'].iloc[0] * 100:+.1f}%", fontsize=10, loc="left")
    axes[0].legend(ncol=5, loc="upper right", fontsize=8)
    axes[-1].set_xlabel("층별 기여 (%p)")
    fig.tight_layout()
    path = FIG_DIR / "9_group_cases.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


CASES = [
    ("000660", "2026-04-08"), ("000660", "2026-08-20"), ("005930", "2026-06-08"),
    ("015760", "2026-03-03"), ("005490", "2026-03-03"), ("035420", "2026-06-08"),
    ("011200", "2026-03-03"), ("006400", "2026-03-04"),
]


if __name__ == "__main__":
    FIG_DIR.mkdir(exist_ok=True)
    dataset = data.load_or_fetch(refresh=False)
    names = _names(dataset)
    factors = a.build_factors(dataset)

    candidates, reclass_gains, reclass_betas, reclass_choice, decomp_reclass, beta_reclass = a.reclass_experiment(dataset, factors)
    decomp_seq = a.run_decompositions(factors)
    decomp_seq = decomp_seq[(decomp_seq["variant"] == "kospi_ex") & (decomp_seq["method"] == "sequential_4l")]
    decomp_all = pd.concat([decomp_seq.drop(columns="variant"), decomp_reclass], ignore_index=True)
    metrics = a.method_metrics(decomp_all)

    paths = [
        plot_beta_stability(beta_reclass, names, "ols_4l"),
        plot_beta_stability(beta_reclass, names, "ols_4l_reclass"),
        plot_corr_sector_idio(metrics, names),
        plot_method_summary(metrics),
        plot_reclass_heatmap(reclass_gains, reclass_betas, reclass_choice, dataset, names),
        plot_cases(decomp_all, CASES, names),
    ]
    stab_decomp, stab_beta, stab_metrics, stab_rough, stab_summary = a.stability_experiment(dataset, factors)
    grp_decomp, grp_beta, grp_metrics, grp_by_symbol, grp_summary = a.group_experiment(
        dataset, factors, a.STABILITY_VARIANTS["roll120"]
    )
    paths += [
        plot_stability_lines(stab_beta, names),
        plot_stability_summary(stab_summary),
        plot_group_gain(grp_by_symbol),
        plot_group_cases(grp_decomp, ["005380", "000270", "012330", "003550", "090430"], 2, names),
    ]
    beta_std = (
        beta_reclass.groupby(["method", "symbol"])[list(BETA_COLS)].std().round(2)
    )
    for p in paths:
        print(p)
