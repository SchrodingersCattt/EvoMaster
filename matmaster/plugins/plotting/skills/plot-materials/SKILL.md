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

Same as plot-chart — apply the shared style before plotting:

```python
import sys

sys.path.insert(0, "${PLUGIN_DIR}/shared")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mm_style

mm_style.apply()
```

`apply()` covers fonts (CJK), the white background, and axes you create
yourself. pymatgen plotters do NOT inherit it: they route through
`pretty_plot`, which re-imposes 30-48pt text, its own figure size, and a
Set1 palette after `apply()` ran. Every plotter-returned Axes therefore goes
through `mm_style.restyle(ax)` and a recolor pass before saving.

## Plotter-first policy

Prefer pymatgen's plotters over hand-drawn axes — they already implement the
domain conventions (k-path labels, Fermi alignment, hull construction):

| Figure | Plotter |
|---|---|
| band structure | `pymatgen.electronic_structure.plotter.BSPlotter(bs).get_plot()` |
| DOS / PDOS | `pymatgen.electronic_structure.plotter.DosPlotter()` — `add_dos()` then `get_plot()` |
| XRD pattern | `pymatgen.analysis.diffraction.xrd.XRDCalculator().get_plot(structure)` |
| phase diagram | `pymatgen.analysis.phase_diagram.PDPlotter(pd, backend="matplotlib").get_plot()` |

- `get_plot` returns a matplotlib Axes; finish with `mm_style.restyle(ax)`,
  a recolor pass, then `mm_style.save_figure(ax.figure, path)`.
- The sandbox has no plotly — always pass `backend="matplotlib"` to PDPlotter.
- Recolor plotter output to palette colors (`mm_style.stroke("purple")`, …):
  pymatgen's own defaults (Set1 palette, black hull and stems) clash with the
  contract.
- Hand-draw with plot-chart techniques only when no plotter covers the figure
  (RDF, MSD, custom parsed outputs).

## Domain conventions per figure

Plotter defaults are marked (default); everything else is your restyle work.

- Band structure: high-symmetry path labels Γ, X, … (default); spin channels
  by linestyle — solid up, dashed down (default); y axis "E − E_F (eV)";
  the Fermi line arrives colored dash-dot — restyle it gray dashed at 0; set
  the window to [−4, +4] eV via `ax.set_ylim` unless asked otherwise; name
  spin channels in the legend yourself when spin-polarized.
- DOS / PDOS: energy lands on the x axis with the Fermi line black dashed at
  0 and spin-down mirrored negative (defaults); gray the Fermi line; y axis
  "DOS (states/eV)"; PDOS overlaid per element/orbital with a legend. Paired
  beside a band structure, pass `get_plot(invert_axes=True)` so energy goes
  on the shared y axis.
- XRD: x axis 2θ in degrees, vertical stems, intensities normalized to 100
  (defaults); the default annotates every reflection — pass
  `annotate_peaks=False` and label only the ~5 strongest yourself; recolor
  the black stems to a palette stroke; state the wavelength in the caption
  (Cu Kα unless you chose otherwise).
- RDF: x "r (Å)", y "g(r)"; gray hline at g = 1; annotate the first-shell
  peak position from the data. (Hand-drawn — plot-chart techniques.)
- MSD: x "t (ps)", y "MSD (Å²)"; when a diffusion coefficient was computed,
  draw the fitted line and put D in the legend. (Hand-drawn.)
- Phase diagram: stable entries arrive labelled with reduced formulas
  (default); the hull arrives black with Set1 markers — restyle hull lines
  gray and markers to a palette stroke; put e_above_hull values in the
  caption when conclusions depend on them.
- k-mesh / cutoff convergence: use the convergence idiom from plot-chart.

## Data honesty

Plot only arrays parsed from real outputs in the workspace or values the user
gave. Never synthesize a "typical" band structure, spectrum, or hull — if the
computation is missing, say what must be run instead (contract rule).

## Deliver

AttachFigure the produced PNGs in one batch and reference each as
[[fig:<figure_id>]] with connecting prose (contract rules).
