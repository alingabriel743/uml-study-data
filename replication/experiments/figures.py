"""Render the revised manuscript's Results figures from the final tables.

Run from any directory with revision_work/.venv/bin/python experiments/figures.py.
Only the four PNGs in results/figures are produced; no tables are modified.
Acceptance CIs and Spearman statistics are taken as supplied, without refitting.
"""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import tempfile

# Keep matplotlib's font cache out of the repository and the user's config.
_CONFIG = tempfile.TemporaryDirectory(prefix="manuscript-mpl-")
os.environ["MPLCONFIGDIR"] = _CONFIG.name
os.environ["XDG_CACHE_HOME"] = _CONFIG.name

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
TABLES = ROOT / "results" / "tables"
OUTPUT = ROOT / "results" / "figures"
DPI = 300
WIDTH = 6.5
INK = "#222222"
BLUE = "#2a78d6"
LANGUAGES = ("Java", "JavaScript", "PHP", "Python")
COLORS = dict(zip(LANGUAGES, (BLUE, "#eb6834", "#1baf7a", "#eda100")))
MARKERS = dict(zip(LANGUAGES, ("o", "s", "^", "D")))
CONDITIONS = (
    ("single_view", "Ranked view (Sonnet 4.6)"),
    ("single_prefix", "Declaration-order view"),
    ("single_agent", "Full IR, single agent"),
    ("plain_llm_1m", "Full IR, plain LLM (1M)"),
    ("single_view_opus46", "Ranked view (Opus 4.6)"),
    ("single_view_sonnet45", "Ranked view (Sonnet 4.5)"),
)


def read_table(name: str) -> list[dict[str, str]]:
    with (TABLES / name).open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def number(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value and value.strip() else math.nan


def configure_style() -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    font = "Times New Roman" if "Times New Roman" in available else "DejaVu Serif"
    plt.rcParams.update({
        "font.family": font,
        "font.size": 9,
        "axes.labelsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "text.color": INK,
        "axes.labelcolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.edgecolor": "#666666",
        "axes.linewidth": 0.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.axisbelow": True,
        "grid.color": "#e5e5e5",
        "grid.linewidth": 0.45,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "xtick.major.size": 3,
        "ytick.major.size": 3,
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
    })
    print(f"Font: {font}; width: {WIDTH} in; resolution: {DPI} dpi")


def check_layout(fig: plt.Figure, labels: list[Text] | None = None,
                 points: list[tuple[plt.Axes, float, float]] | None = None) -> None:
    """Check rendered text bounds, direct-label collisions, and marker occlusion."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    canvas = fig.bbox
    for text in fig.findobj(Text):
        if not text.get_visible() or not text.get_text().strip():
            continue
        box = text.get_window_extent(renderer)
        if (box.x0 < 1 or box.y0 < 1 or box.x1 > canvas.x1 - 1
                or box.y1 > canvas.y1 - 1):
            raise RuntimeError(f"Clipped text: {text.get_text()!r}")
    # Annotation.get_window_extent includes its leader line. Compare the text
    # rectangles themselves, so a leader's empty bounding area is not a collision.
    boxes = [(text, Text.get_window_extent(text, renderer)) for text in labels or []]
    for index, (text, box) in enumerate(boxes):
        for other, other_box in boxes[index + 1:]:
            if box.padded(3).overlaps(other_box):
                raise RuntimeError(f"Overlapping labels: {text.get_text()!r}, "
                                   f"{other.get_text()!r}")
        for ax, x, y in points or []:
            px, py = ax.transData.transform((x, y))
            if box.padded(7).contains(px, py):
                raise RuntimeError(f"Label obscures a point: {text.get_text()!r}")


def save(fig: plt.Figure, name: str, labels: list[Text] | None = None,
         points: list[tuple[plt.Axes, float, float]] | None = None) -> None:
    check_layout(fig, labels, points)
    path = OUTPUT / name
    # Do not use bbox_inches='tight': the physical width must stay exactly 6.5 in.
    fig.savefig(path, dpi=DPI, metadata={"Software": "matplotlib; experiments/figures.py"})
    with Image.open(path) as image:
        image.load()
        if image.width != round(WIDTH * DPI):
            raise RuntimeError(f"Unexpected PNG width: {path}")
        if any(abs(dpi - DPI) > 0.1 for dpi in image.info["dpi"]):
            raise RuntimeError(f"Unexpected PNG resolution: {path}")
        print(f"{path.relative_to(ROOT)}: {image.width} × {image.height} px; layout OK")
    plt.close(fig)


def compile_heatmap() -> None:
    rows = read_table("archived_heatmap.csv")
    types = ("activity", "class", "component", "deployment", "sequence",
             "system_context", "use_case")
    lookup = {(row["language"], row["type"]): row for row in rows}
    values = []
    for language in LANGUAGES:
        cells = [lookup[language, kind] for kind in types]
        counts = [int(cell["files"]) for cell in cells]
        rates = [number(cell, "compile_validity") for cell in cells]
        # Rates in the final CSV are rounded; weight those supplied rates by files.
        mean = sum(rate * count for rate, count in zip(rates, counts)) / sum(counts)
        values.append([100 * rate for rate in rates] + [100 * mean])
    values = np.asarray(values)
    ramp = LinearSegmentedColormap.from_list(
        "acceptance_blue", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#104281"])
    norm = Normalize(0, 100)
    fig, ax = plt.subplots(figsize=(WIDTH, 2.85))
    fig.subplots_adjust(left=0.14, right=0.98, top=0.96, bottom=0.38)
    ax.imshow(values, cmap=ramp, norm=norm, aspect="auto", interpolation="nearest")
    ax.set_yticks(range(4), LANGUAGES)
    ax.set_xticks(range(8), ["Activity", "Class", "Component", "Deployment", "Sequence",
                                "System\ncontext", "Use case", "All types"])
    ax.tick_params(length=0, pad=6)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for i in range(4):
        for j in range(8):
            r, g, b, _ = ramp(norm(values[i, j]))
            # Relative luminance picks the more legible annotation color.
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055)**2.4
                      for v in (r, g, b)]
            luminance = sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722)))
            color = "white" if luminance < 0.179 else "black"
            ax.text(j, i, f"{values[i, j]:.1f}%", ha="center", va="center", color=color)
    # Fine white cell rules; a dark separator distinguishes the weighted aggregate.
    for x in np.arange(0.5, 7.5, 1):
        ax.axvline(x, color="white", linewidth=0.45)
    for y in np.arange(0.5, 3.5, 1):
        ax.axhline(y, color="white", linewidth=0.45)
    ax.axvline(6.5, color=INK, linewidth=0.75)
    cax = fig.add_axes((0.35, 0.14, 0.42, 0.045))
    bar = fig.colorbar(matplotlib.cm.ScalarMappable(norm=norm, cmap=ramp), cax=cax,
                       orientation="horizontal", ticks=[0, 25, 50, 75, 100])
    bar.outline.set_linewidth(0.4)
    bar.ax.tick_params(length=2, pad=2)
    bar.set_label("Compiler acceptance (%)", labelpad=3)
    save(fig, "fig_compile_heatmap.png")


def direct_label(ax: plt.Axes, x: float, y: float, label: str,
                 dx: float, dy: float, ha: str = "left") -> Text:
    return ax.annotate(
        label, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha,
        va="center", color=INK, fontsize=9, annotation_clip=False,
        arrowprops={"arrowstyle": "-", "color": "#777777", "lw": 0.45,
                    "shrinkA": 3, "shrinkB": 4, "connectionstyle": "arc3"})


def recall_vs_size() -> None:
    rows = read_table("archived_by_project.csv")
    summary = json.loads((ROOT / "results" / "summary_full.json").read_text())
    stats = summary["archived"]["size"]["entity_recall"]
    if len(rows) != 11 or stats["n"] != len(rows):
        raise ValueError("Repository count does not match the supplied correlation")
    fig, ax = plt.subplots(figsize=(WIDTH, 3.95))
    fig.subplots_adjust(left=0.10, right=0.98, bottom=0.15, top=0.88)
    ax.set_xscale("log")
    ax.set_xlim(25, 8500)
    ax.set_ylim(-0.10, 0.91)
    ax.xaxis.set_major_locator(FixedLocator([30, 100, 300, 1000, 3000]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8])
    ax.set_xlabel("IR entities (log scale)")
    ax.set_ylabel("Entity recall (class and sequence)")
    ax.grid(True)
    offsets = {
        "shopping-cart": (8, -1, "left"),
        "composer": (8, 4, "left"),
        "laravel-ai": (8, 4, "left"),
        "GenericAgent": (-8, 22, "right"),
        "impeccable": (-8, -17, "right"),
        "evolver": (7, -17, "left"),
        "Mindustry": (-8, 25, "right"),
        "lyra": (-8, 29, "right"),
        "openai-agents": (10, 22, "left"),
        "debezium": (10, -6, "left"),
        "puter": (8, 4, "left"),
    }
    labels, points = [], []
    for language in LANGUAGES:
        for row in rows:
            if row["language"] != language:
                continue
            x, y = number(row, "ir_entities"), number(row, "entity_recall")
            ax.scatter(x, y, s=24, c=COLORS[language], marker=MARKERS[language],
                       edgecolors=INK, linewidths=0.45, zorder=3)
            labels.append(direct_label(ax, x, y, row["project"], *offsets[row["project"]]))
            points.append((ax, x, y))
    rho = f"{stats['rho']:.3f}".replace("-", "−")
    lo = f"{stats['ci_low']:.3f}".replace("-", "−")
    hi = f"{stats['ci_high']:.3f}".replace("-", "−")
    labels.append(ax.text(0.98, 0.97,
                         f"Spearman ρ = {rho} (95% CI {lo} to {hi}), n = {len(rows)}",
                         transform=ax.transAxes, ha="right", va="top"))
    handles = [Line2D([], [], linestyle="none", marker=MARKERS[language], markersize=4.8,
                      markerfacecolor=COLORS[language], markeredgecolor=INK,
                      markeredgewidth=0.45, label=language) for language in LANGUAGES]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.04),
              ncol=4, frameon=False, handletextpad=0.5, columnspacing=1.8, borderaxespad=0)
    save(fig, "fig_recall_vs_size.png", labels, points)


def condition_rows() -> list[dict[str, str]]:
    by_key = {row["condition"]: row for row in read_table("conditions.csv")}
    return [by_key[key] for key, _ in CONDITIONS]


def conditions() -> None:
    rows = condition_rows()
    fig, axes = plt.subplots(1, 4, figsize=(WIDTH, 3.65), sharey=True)
    fig.subplots_adjust(left=0.31, right=0.985, bottom=0.23, top=0.97, wspace=0.30)
    metrics = (
        ("compile_validity", "(a) Compiler\nacceptance (%)", 100, 105, [0, 50, 100]),
        ("endpoint_grounding", "(b) Endpoint\ngrounding (%)", 100, 105, [0, 50, 100]),
        ("entity_recall", "(c) Entity\nrecall (%)", 100, 12, [0, 5, 10]),
        ("usd_per_run", "(d) Cost per run\n(USD)", 1, 1.6, [0, 0.5, 1, 1.5]),
    )
    labels = []
    for ax, (key, xlabel, scale, xmax, ticks) in zip(axes, metrics):
        ax.set_ylim(5.65, -0.65)
        ax.set_xlim(0, xmax)
        ax.set_xticks(ticks)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
        ax.grid(axis="x")
        ax.tick_params(axis="y", length=0, pad=6)
        ax.spines["left"].set_visible(False)
        ax.set_xlabel(xlabel, labelpad=7)
        for index, row in enumerate(rows):
            value = scale * number(row, key)
            if not math.isfinite(value):
                labels.append(ax.text(0.07 * xmax, index, "n/a", va="center"))
                continue
            ax.barh(index, value, height=0.28, color=BLUE, linewidth=0, zorder=2)
            label = f"{value:.3f}" if key == "usd_per_run" else f"{value:.1f}"
            if key == "compile_validity":
                low, high = (scale * number(row, field) for field in ("ci_low", "ci_high"))
                if math.isfinite(low) and math.isfinite(high):
                    ax.errorbar(value, index, xerr=[[value - low], [high - value]],
                                fmt="none", ecolor=INK, elinewidth=0.6, capsize=2,
                                capthick=0.6, zorder=3)
                # Put the value just above the bar's end, clear of its CI whisker.
                labels.append(ax.annotate(label, (value, index), xytext=(0, 8),
                                           textcoords="offset points", ha="right", va="bottom"))
            else:
                labels.append(ax.annotate(label, (value, index), xytext=(0, 8),
                                           textcoords="offset points", va="bottom",
                                           ha="right" if value > 0.35 * xmax else "left"))
    axes[0].set_yticks(range(len(rows)), [label for _, label in CONDITIONS])
    save(fig, "fig_conditions.png", labels)


def cost_recall() -> None:
    rows = condition_rows()
    fig, ax = plt.subplots(figsize=(WIDTH, 3.7))
    fig.subplots_adjust(left=0.10, right=0.98, bottom=0.16, top=0.96)
    ax.set_xscale("log")
    ax.set_xlim(0.10, 3.3)
    ax.set_ylim(0.59, 0.92)
    ax.set_xticks([0.1, 0.2, 0.5, 1, 2])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:g}"))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_yticks([0.6, 0.7, 0.8, 0.9])
    ax.set_xlabel("Cost per run (USD, log scale)")
    ax.set_ylabel("Endpoint grounding")
    ax.grid(True)
    baseline = number(rows[0], "endpoint_grounding")
    if math.isfinite(baseline):
        ax.axhline(baseline, color="#777777", linewidth=0.55, linestyle=(0, (4, 3)), zorder=1)
    offsets = {
        "single_view": (12, -24, "left"),
        "single_prefix": (12, 5, "left"),
        "single_agent": (10, 9, "left"),
        "plain_llm_1m": (-9, -19, "right"),
        "single_view_opus46": (10, 16, "left"),
        "single_view_sonnet45": (10, 16, "left"),
    }
    labels, points = [], []
    for row, (key, label) in zip(rows, CONDITIONS):
        x, y = number(row, "usd_per_run"), number(row, "endpoint_grounding")
        if not (math.isfinite(x) and x > 0 and math.isfinite(y)):
            # A missing pair cannot be placed on a log scatter; never substitute zero.
            print(f"Cost/grounding point unavailable (n/a): {label}")
            continue
        ax.scatter(x, y, s=24, c=BLUE, marker="o", edgecolors=INK, linewidths=0.45, zorder=3)
        labels.append(direct_label(ax, x, y, label, *offsets[key]))
        points.append((ax, x, y))
    save(fig, "fig_cost_recall.png", labels, points)


def main() -> None:
    configure_style()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    compile_heatmap()
    recall_vs_size()
    conditions()
    cost_recall()


if __name__ == "__main__":
    main()
