# GSAS-II Powder XRD Refinement Guide

Quick reference for using `gsas2_pawley.py` and `gsas2_rietveld.py` in the pxrd-refinement skill.

---

## 1. Data Preparation

### Accepted input formats

Both scripts accept plain text files with two or three columns:

| Column | Meaning |
|--------|---------|
| 1 | 2θ (degrees) |
| 2 | Intensity (counts or arbitrary) |
| 3 | Sigma (optional; auto-generated as √I if absent) |

Delimiters: space, tab, or comma. Lines beginning with `#` or `!` are ignored.

### Automatic preprocessing

If raw intensity values show a large constant background (dynamic range < 1.2, i.e. max/min < 1.2
— typical of synchrotron or DFT-simulated patterns), the scripts automatically:

1. Subtract the 5th-percentile baseline.
2. Scale by 10000 to bring counts to a GSAS-II-friendly magnitude.
3. Generate sigma = √intensity.

This is transparent to the user; the input file is **not** modified.

---

## 2. Choosing Initial Cell Parameters

Accurate starting parameters are critical. Rules of thumb:

- Cell parameters should be within ±2% of the true values.
- For phase identification, use the `mcp-mat-xrd` tool (`xrd_phase_identification`) first.
- For monoclinic/triclinic cells, the angle parameters (β, α, γ) are often known from literature.
- For multi-pattern data with a phase or structure-setting change, run **separate**
  refinements for each continuous segment with appropriate starting cells — do not chain
  across the discontinuity.

**Typical starting points by crystal system:**

| System | Parameters needed |
|--------|------------------|
| Cubic | `a=<value>` |
| Tetragonal | `a=<value>,c=<value>` |
| Hexagonal / Trigonal | `a=<value>,c=<value>` |
| Orthorhombic | `a=<value>,b=<value>,c=<value>` |
| Monoclinic | `a=<value>,b=<value>,c=<value>,beta=<angle>` |
| Triclinic | `a=<value>,b=<value>,c=<value>,alpha=<ang>,beta=<ang>,gamma=<ang>` |

---

## 3. Instrument Parameters

### Cu Kα1 (default)

If `--instprm` is omitted, a Cu Kα1 template is used automatically:

```
Lam: 1.5406 Å
U=2.0, V=-2.0, W=5.0   (Caglioti FWHM parameters)
Zero: 0.0               (zero-point shift)
SH/L: 0.002             (asymmetry)
Polariz.: 0.99
```

Peak-shape parameters U, V, W and Zero are refined during the fit. Starting values are
intentionally broad so that the pattern can be fit even if the instrument parameters are
not precisely known.

### Other wavelengths

Pass `--wavelength <value>` in Å. Common values:

| Source | Wavelength (Å) |
|--------|----------------|
| Cu Kα1 | 1.54056 |
| Cu Kα (average) | 1.5418 |
| Mo Kα1 | 0.70930 |
| Synchrotron | varies (specify precisely) |

For synchrotron data, also provide a custom `--instprm` file with the correct polarisation
and peak-shape parameters.

---

## 4. Refinement Strategy

### Pawley (gsas2_pawley.py)

The script applies a fixed multi-step strategy:

| Step | Refined parameters |
|------|--------------------|
| 1 | Background (6 Chebyshev terms) |
| 2 | Cell (a, b, c, angles as allowed by symmetry) |
| 3 | Caglioti U, V, W (peak width) |
| 4 | Zero-point shift |
| 5 | Background (12 terms) + 3× convergence |

**Why the histogram Scale is fixed:**
Pawley reflection intensities and the histogram scale factor are 100% correlated. GSAS-II
will produce SVD (singular value decomposition) errors if both are refined simultaneously.
The script fixes the Scale to 1.0 and allows Pawley intensities to absorb all intensity
information. This is the correct approach.

### Multi-start (`--multi-start N`)

A single Pawley run from one initial cell can settle into a wrong local minimum, especially
on noisy or DFT-simulated patterns: the refinement may converge with `wR ≈ 22 %` but place
`a` 0.07 Å away from the true value, which then poisons every downstream slope / volume.

`--multi-start N` runs the same multi-step strategy from `N` initial cells (the user-provided
seed plus `N-1` deterministic perturbations: log-normal ±0.5 % on a/b/c, additive ±0.5 ° on
α/β/γ; tunable via `--multi-start-len-sigma` / `--multi-start-ang-sigma`). The result with
the lowest `wR` wins; every candidate's seed cell + wR + outcome is preserved in
`result["multi_start"]` for audit.

When to use it:

- **Always for noisy / DFT-simulated single patterns** where the user provides only an
  approximate initial cell. `--multi-start 5` is the recommended default. **Do not
  raise above 5 for standard runs**: each extra start adds ~12 % Bohrium time per
  pattern, and combined with `--chain-cell-direction both` (which itself doubles
  work) the total runtime exceeds the 1200 s online task timeout. If 5 starts all
  collapse to the same wrong basin (typical symptom: |V_refined − V_seed| > 1.5 %
  with `wR > 10 %`), prefer the cell-distance tiebreak + `merge_audit.warnings`
  audit path over brute-forcing more starts.
- **Always for noisy multi-pattern series.** Combine with `--chain-cell` (see § 6) so each
  pattern both samples K seeds *and* benefits from the previous accepted cell in the same segment.
- Skip (`--multi-start 1`, the legacy default) only when you already have a CIF / prior
  refinement within < 0.1 % of the truth.

`--multi-start-seed` defaults to 42 and makes perturbations reproducible across runs.
Do **not** raise the perturbation sigmas above 1 % / 1 ° without good reason — too-wide
seeds drift into neighboring basins and slow convergence.

### Rietveld (gsas2_rietveld.py)

| Round | Refined parameters |
|-------|--------------------|
| 1 | Background (6 terms) + phase fraction (Scale) |
| 2 | Cell |
| 3 | Caglioti U, V, W |
| 4 | Zero-point + SH/L asymmetry |
| 5 (standard+) | All atom coordinates + Uiso |
| 6 (full only) | + Occupancy |
| 7 (full only) | + Anisotropic Uani |
| Final | Background (12 terms) + 5× convergence |

---

## 5. Interpreting Results

### R-factors

| R-factor | Formula | Good | Acceptable | Poor |
|----------|---------|------|------------|------|
| wR (Pawley) | √(Σw(Iobs-Icalc)²/ΣwIobs²) | < 20% | 20–35% | > 35% |
| Rwp (Rietveld) | same | < 10% | 10–15% | > 15% |
| GOF | √(χ²) | 1.0–2.0 | 2–3 | > 3 |

**Hard reject thresholds (per SKILL.md contract 3):**
- Pawley `wR > 0.20` → cell is wrong, do not report.
- Rietveld `Rwp > 0.15` → fit is wrong, do not report.
- Refined volume differs from initial-cell volume by > 20% → cell is wrong (likely a
  super-cell), do not report.

**High wR causes and fixes:**

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| wR > 40% from step 1 | Wrong space group | Check phase identification |
| wR > 40% after cell refine | Cell too far off | Use a better initial cell (literature / CIF / adjacent VT point) |
| Sudden divergence | SVD singularity | Increase `--dmin`, narrow 2θ range |
| Peaks misfit by ~0.5° | Wrong wavelength | Check and correct `--wavelength` |
| Background dominates | Missing reflections | Check dmin covers your 2θ range |
| `success=true`, V is 2x / 3x / 4x of initial | Refinement converged on a super-cell | Reject; the true cell needs a better starting point — do **not** report the inflated V |

### ESDs (estimated standard deviations)

ESDs on lattice parameters are extracted from the least-squares covariance matrix by GSAS-II.
They reflect **precision**, not accuracy. A ESD of 0.001 Å means the parameter is internally
consistent to that level, but the true systematic error (from wavelength calibration, zero-point,
etc.) may be larger.

### Curation and the wR trap

`curation.py` runs by default (`--curation-mode auto`) on every Pawley refinement and
attaches its diagnostics to `result["curation"]`:

```json
{
  "tmin_cut":   13.7,
  "verdict":    "PASS" | "WARN" | "FAIL",
  "peak_count": 25,
  "peak_positions": [...],
  "dyn_range":  39.4,
  "coverage":   {"[15,25)": 5, "[25,40)": 15, "[40,+)": 5},
  "baseline_method": "piecewise_linear",
  "baseline_roughness": 1e-5,
  "reasons":    ["clipped 8.7° of artifact prefix (tmin_cut=13.72°)"]
}
```

What curation does, in order:

1. **Artifact-prefix detection** via the derivative of a rolling-min baseline. PXRD
   patterns from DFT simulation (and some real instruments at very low 2θ) carry a
   broad smooth descending hump in roughly 5°–13° 2θ that is **not Bragg signal** —
   it is low-angle scattering / detector smear. Curation detects where the slope
   flattens out and sets `tmin_cut` ~1° past that point.
2. **Low-order baseline fit** on the post-clip range. Default is `piecewise_linear`
   (3 segments × degree-1). `--baseline-method linear` is the most conservative
   single-segment option; `mor` is a morphological baseline good for strong
   curvature but prone to small overshoot. **Do not use polynomial degree > 2** —
   high-order polynomials swallow real peaks.
3. **Peak picking** on the baseline-subtracted intensity, accepting peaks with
   SNR ≥ 3 and prominence ≥ 2% of `I_max`. Counts coverage in
   [15°, 25°) / [25°, 40°) / [40°, +∞).
4. **Verdict**: `PASS` (refine), `WARN` (refine but log reasons), `FAIL`
   (`dyn_range < 10` or `peak_count < 12` — `auto` still refines, `strict` aborts).

**The wR trap.** When curation clips a low-2θ artifact, the GSAS-II background
polynomial no longer has to stretch to absorb it, so wR can rise (e.g. 22% → 30%) —
but the *cell* gets noticeably more accurate (a-error from 0.1 Å down to 0.01 Å),
because the polynomial is no longer trading background freedom for peak-position
freedom. The opposite trap: a fit with `wR ≈ 18%` on un-curated data can hide a cell
that is wrong by several percent. **Trust `curation.tmin_cut` and the cell-vs-initial
delta over the raw wR number.** The hard reject thresholds above still apply, but
`wR = 0.25` with `verdict=PASS` and a cell within 1% of the initial is a much better
result than `wR = 0.18` with the artifact left in.

`--debug-plot DIR` writes `<label>_curation.png` (raw + baseline; subtracted +
peak markers) for every pattern. **Read it whenever a refinement looks suspicious.**

---

## 6. Multi-Pattern PXRD

The Pawley script supports three modes for multi-pattern PXRD data:

### A. Individual xy files (one per pattern)

Replace `<SG>` and the `<A>/<B>/<C>/<BETA>` placeholders with the initial cell and space
group provided by the user / literature / a prior refinement / a CIF model. Do **not**
invent them from peak positions (see SKILL.md § "Hard contracts").

```
# Place all patterns in a directory with consistent naming, then run:
python gsas2_pawley.py --data /path/to/patterns/ \
  --space-group "<SG>" --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \
  --multi-start 5 --chain-cell --chain-cell-direction both --standardize-cell ref \
  -o results.json
```

`--chain-cell` feeds each accepted refinement into the next pattern as its seed; combined
with `--multi-start 5`, this makes intra-phase results internally consistent. Promotion is
gated by `--chain-wr-max` (default 25 %) and `--chain-vol-jump-max` (default 0.05) so a bad
refinement cannot poison downstream patterns — when a pattern is rejected the chain
reverts to the last accepted cell. Sort order follows alphabetical filename order, so name
files consistently (e.g. `pattern_303K.xy`, `pattern_323K.xy`, ...). For data that spans a
phase or structure-setting change use **separate** jobs per segment (see "Phase / setting changes" below); the
quality gate will reject a transition jump but the cleanest pattern is to not chain across
segments at all. For noisy multi-pattern data, `--chain-cell-direction both` runs
forward and reverse chains internally and returns merged `results`; inspect
`merge_audit.table` to see which direction was selected at each pattern, and
`merge_audit.warnings` for any pattern that ended up high-wR + far from `reference_volume`
in both directions (the merge picked the lesser-evil candidate but the cell is suspect).

### B. Wide-table CSV

```
# CSV with paired angle/intensity columns per condition:
# Header: Angle, 25 C, Angle, 40 C, ...
python gsas2_pawley.py --data multi_temp.txt --wide-csv \
  --space-group "<SG>" --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \
  --multi-start 5 --chain-cell --chain-cell-direction both --standardize-cell ref \
  -o results.json
```

Output contains per-pattern results with `temp_c` and `temp_label` fields, plus
`seed_cell` (the cell actually used as initial guess after chain promotion) and
`chain_decision` (`promoted` or `rejected: <reason>`).

### Phase / setting changes

If a structural phase transition, composition jump, pressure/temperature discontinuity, or
setting change occurs between two patterns, run **separate** refinement jobs with different
initial cells (one per continuous segment):

```bash
# Segment A:
python gsas2_pawley.py --data segment_a/ --space-group "<SG_A>" \
  --cell "a=<A_A>,b=<B_A>,c=<C_A>,beta=<BETA_A>" -o segment_a_results.json

# Segment B:
python gsas2_pawley.py --data segment_b/ --space-group "<SG_B>" \
  --cell "a=<A_B>,b=<B_B>,c=<C_B>,beta=<BETA_B>" -o segment_b_results.json
```

---

## 7. Production Execution (Bohrium Job)

In agent production runs, the scripts are executed in a Docker container via bohrium-job.

**Docker image:**
```
registry.dp.tech/dptech/dp/native/prod-19853/xrd-app:dev-260119
```

GSAS-II is pre-installed in this image. The `--gsas2-path` default
`/root/g2full/GSAS-II/GSASII` is correct for this image.

**Typical job command (placeholders `<SG>` / `<A>,<B>,<C>,<BETA>` must be replaced with the
user-provided initial cell + space group):**
```bash
python gsas2_pawley.py \
  --data /input/pattern.xy \
  --space-group "<SG>" \
  --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \
  --multi-start 5 \
  -o /output/result.json
```

For a multi-pattern series, swap `--data` for the directory of xy files and add
`--chain-cell` so the refined cell of each accepted pattern seeds the next one
(quality-gated by default — see § 6).

---

## 8. Common Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: No module named 'GSASIIscriptable'` | GSAS-II not in path | Use `--gsas2-path` to specify GSAS-II directory |
| `SVD problem(s) likely from: :0:Scale` | Scale and Pawley intensities correlated | Already handled by script; if persists, increase `--dmin` |
| `pickle data was truncated` | Too many reflections, large GPX file | Increase `--dmin` from 2.0 to 2.5 |
| `G2ImportException` on add_powder_histogram | Missing instrument parameter file | Ensure `--instprm` path is correct or let script auto-generate |
| `TypeError: unsupported operand type(s) for *: 'NoneType'` | Pawley reflections not generated | This is handled internally; report as a bug if seen in current scripts |
| `wR = None` | Refinement did not converge | Check space group and cell; try wider 2θ range |
