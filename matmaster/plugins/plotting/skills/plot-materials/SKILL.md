---
name: plot-materials
description: "Plot standard materials-science figures with their domain conventions and attach them as answer figures: band structures, DOS/PDOS, XRD patterns, radial distribution functions g(r), MSD, and phase diagrams, from computed results (pymatgen/ASE objects or parsed output files). Use when the requested figure is one of these canonical materials plots; generic numeric charts go to plot-chart."
---

# Plot Materials

## Step 0 — mandatory

Read `${PLUGIN_DIR}/shared/style-contract.md` with the Read tool before doing
anything else. It is the single source of truth for figure delivery and style.
Do not produce or attach any figure without having read it in this session.

## Setup

Same as plot-chart — apply the shared style BEFORE any plotter is built, so
rcParams (fonts, sizes, white background) take effect on plotter output:

```python
import sys

sys.path.insert(0, "${PLUGIN_DIR}/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()
```

## Plotter-first policy

Prefer pymatgen's plotters over hand-drawn axes — they already implement the
domain conventions (k-path labels, Fermi alignment, hull construction):

| Figure | Plotter |
|---|---|
| band structure | `pymatgen.electronic_structure.plotter.BSPlotter(bs).get_plot()` |
| DOS / PDOS | `pymatgen.electronic_structure.plotter.DosPlotter()` — `add_dos()` then `get_plot()` |
| XRD pattern | `pymatgen.analysis.diffraction.xrd.XRDCalculator().get_plot(structure)` |
| phase diagram | `pymatgen.analysis.phase_diagram.PDPlotter(pd, backend="matplotlib").get_plot()` |

- `get_plot` returns a matplotlib Axes; finish with
  `mm_style.save_figure(ax.figure, path)`.
- The sandbox has no plotly — always pass `backend="matplotlib"` to PDPlotter.
- Recolor plotter output to palette colors (`mm_style.stroke("purple")`, …)
  when its defaults clash with the contract.
- Hand-draw with plot-chart techniques only when no plotter covers the figure
  (RDF, MSD, custom parsed outputs).

## Domain conventions per figure

- Band structure: y axis "E − E_F (eV)"; Fermi level as gray dashed hline at
  0; x axis the high-symmetry path with Γ, X, … labels (BSPlotter provides
  them); default window [−4, +4] eV unless asked otherwise; spin channels by
  linestyle, named in the legend.
- DOS / PDOS: energy axis as E − E_F (eV) with the Fermi line at 0; spin-down
  as a negated mirror; PDOS overlaid per element/orbital with a legend; y
  axis "DOS (states/eV)". Paired beside a band structure, energy goes on the
  shared y axis.
- XRD: x axis "2θ (degree)" over the computed range (typically 10–90 for Cu
  Kα); intensities normalized to 100; vertical stems; hkl labels on the ~5
  strongest reflections; state the wavelength in the caption.
- RDF: x "r (Å)", y "g(r)"; gray hline at g = 1; annotate the first-shell
  peak position from the data.
- MSD: x "t (ps)", y "MSD (Å²)"; when a diffusion coefficient was computed,
  draw the fitted line and put D in the legend.
- Phase diagram: hull lines gray; stable entries marked and labelled with
  reduced formulas; put e_above_hull values in the caption when conclusions
  depend on them.
- k-mesh / cutoff convergence: use the convergence idiom from plot-chart.

## Data honesty

Plot only arrays parsed from real outputs in the workspace or values the user
gave. Never synthesize a "typical" band structure, spectrum, or hull — if the
computation is missing, say what must be run instead (contract rule).

## Deliver

AttachFigure the produced PNGs in one batch and reference each as
[[fig:<figure_id>]] with connecting prose (contract rules).
