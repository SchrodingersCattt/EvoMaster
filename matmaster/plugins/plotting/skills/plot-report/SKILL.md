---
name: plot-report
description: "Compose a multi-panel summary figure with matplotlib subplot_mosaic and attach it as an answer figure: headline metric cards on top, aligned supporting charts below, (a)(b)(c) panel labels. Use when the user asks for a combined overview, summary board, report figure, or a publication-style multi-panel figure assembling several computed results into one image; single charts go to plot-chart or plot-materials."
---

# Plot Report

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Compose or separate?

Compose one multi-panel figure when the panels answer a single question at a
glance — final summary, side-by-side comparison, paper-style figure. Keep
separate figures with prose between when the answer walks through steps
(contract narrative rules). ≤6 panels per figure; more → split.

## Layout recipe

Apply the shared style first, then build the mosaic:

```python
import sys

sys.path.insert(0, "${PLUGIN_DIR}/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()

fig, axs = plt.subplot_mosaic(
    [["m0", "m1", "m2"], ["main", "main", "side"]],
    figsize=(10, 6),
    height_ratios=[1, 3],
    layout="constrained",
)
```

- The mosaic spec mirrors the visual layout: metric cards across the top row,
  the main chart spanning columns below, side panels beside it. Adapt names,
  spans and `height_ratios` to the content; keep cards on top.
- Metric cards — computed headline numbers only, borderless:

```python
def metric_card(ax, value, label, ramp="purple"):
    ax.axis("off")
    ax.text(0.5, 0.58, value, ha="center", va="center", fontsize=22,
            fontweight="medium", color=mm_style.title_color(ramp))
    ax.text(0.5, 0.22, label, ha="center", va="center", fontsize=12,
            color=mm_style.stop("gray", 600))
```

- Panel labels on every chart panel (skip the cards), same corner everywhere:

```python
ax.text(0.02, 0.98, "(a)", transform=ax.transAxes, fontsize=14,
        fontweight="medium", va="top")
```

- Title hierarchy: the figure-level message lives only in `fig.suptitle(...)`
  (14, medium — mm_style default); panel titles use
  `ax.set_title(..., fontsize=12)`; axis labels stay at 14.
- Alignment: share axes (`sharex`/`sharey`) wherever panels show the same
  quantity; call `fig.align_labels()` before saving.
- One color mapping across all panels: the same series or quantity keeps the
  same ramp everywhere in the figure.

## Deliver

Save with `mm_style.save_figure(fig, path)` and attach. The caption walks the
panels in order — "(a) …, (b) …, (c) …" — at most one sentence per panel,
self-contained, in the user's language (contract rules).
