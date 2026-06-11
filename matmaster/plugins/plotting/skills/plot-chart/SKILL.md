---
name: plot-chart
description: "Plot general-purpose data charts with matplotlib and attach them as answer figures: trends and series over a variable, convergence curves, spectra and signals, scatter/correlation, categorical bar comparisons, histograms and distributions, heatmaps. Use when the user wants computed or provided numeric data visualized and no materials-domain convention applies — band structure, DOS, XRD, RDF, phase diagrams go to plot-materials; multi-panel report compositions go to plot-report; shape-and-label diagrams go to plot-diagram."
---

# Plot Chart

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Setup

Every chart script starts by applying the shared style (palette + CJK-capable
fonts + sizes + white background):

```python
import sys

sys.path.insert(0, "${PLUGIN_DIR}/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()
```

Save every figure with `mm_style.save_figure(fig, "/abs/workspace/name.png")`
— it applies the mandatory white-background savefig settings.

## Chart selection

| Data shape | Chart |
|---|---|
| y over an ordered x (time, step, parameter sweep) | line plot |
| few categories, one value each | vertical bar |
| many categories or long category names | horizontal bar, `figsize=mm_style.hbar_figsize(n)` |
| value distribution | histogram (`bins="auto"`); box plot to compare groups |
| two quantities, correlation question | scatter; add a fit line only if the fit was actually computed |
| matrix / pairwise values | `imshow` heatmap with a colorbar |
| share of a whole | one stacked horizontal bar — never a pie chart |

- Bars, areas and histogram patches use the trio: `facecolor=mm_style.fill(r)`,
  `edgecolor=mm_style.stroke(r)`, `linewidth=0.8`.
- ≤4 series per panel — the full `CATEGORY_ORDER`, one ramp each; more →
  facet into small multiples or several figures.
- The default prop cycle pairs each `CATEGORY_ORDER` color with a distinct
  linestyle — keep both cues; never distinguish series by color alone.

## Axes, legend, grid

- Label every axis with quantity and unit: `ax.set_xlabel("Time (ps)")`.
- Legend: ≤2 series may sit inside a clear corner; otherwise place it outside
  right: `ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1))`. For
  comparison and convergence reads, carry the decisive value into the label:
  `label=f"PBE ({e_pbe:.3f} eV)"` — values computed, never invented.
- Grid and spines come from `mm_style.apply()` (subtle grid, no top/right
  spines) — do not restyle them per figure.
- Use a log scale when the data spans ≥2 decades (`ax.set_yscale("log")`) and
  say so in the axis label or caption.
- Number precision and minus-sign placement follow the contract.

## Scientific idioms

- Convergence curves (SCF energy, forces, k-mesh/cutoff sweeps): plot the
  convergence quantity — |ΔE| on semilog-y when it decays exponentially —
  draw the threshold as a gray dashed hline, and mark the accepted point with
  a marker plus its annotated value.
- Spectra and signals (IR/Raman/XAS, any intensity vs continuous variable):
  line without markers; annotate at most the ~5 principal peaks; y-axis
  "Intensity (arb. units)" when unnormalized.
- Parity plots (predicted vs reference): square aspect
  (`ax.set_aspect("equal")`), y=x gray dashed reference line, computed
  R²/MAE in a corner annotation.

## Deliver

AttachFigure the produced PNGs in one batch and reference each as
[[fig:<figure_id>]] with connecting prose (contract rules).
