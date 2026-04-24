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
- For multi-temperature data with a phase transition, run **separate** refinements for each phase
  with appropriate starting cells — do not chain across the transition.

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

**High wR causes and fixes:**

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| wR > 40% from step 1 | Wrong space group | Check phase identification |
| wR > 40% after cell refine | Cell too far off | Refine manually or use better starting values |
| Sudden divergence | SVD singularity | Increase `--dmin`, narrow 2θ range |
| Peaks misfit by ~0.5° | Wrong wavelength | Check and correct `--wavelength` |
| Background dominates | Missing reflections | Check dmin covers your 2θ range |

### ESDs (estimated standard deviations)

ESDs on lattice parameters are extracted from the least-squares covariance matrix by GSAS-II.
They reflect **precision**, not accuracy. A ESD of 0.001 Å means the parameter is internally
consistent to that level, but the true systematic error (from wavelength calibration, zero-point,
etc.) may be larger.

---

## 6. Multi-Temperature PXRD

The Pawley script supports three modes for multi-temperature data:

### A. Individual xy files (one per temperature)

```
# Place all patterns in a directory with consistent naming:
python gsas2_pawley.py --data /path/to/patterns/ \
  --space-group "P 21/c" --cell "a=10.83,b=10.2,c=9.2,beta=99.0" \
  -o results.json
```

The script chains refinements: the refined cell from one file becomes the starting cell for
the next. Sort order follows filename alphabetical order, so name files consistently
(e.g. `T303K.xy`, `T323K.xy`, ...).

### B. Wide-table CSV

```
# CSV with paired angle/intensity columns per temperature:
# Header: Angle, 25 C, Angle, 40 C, ...
python gsas2_pawley.py --data multi_temp.txt --wide-csv \
  --space-group "P 21/c" --cell "a=10.83,b=10.2,c=9.2,beta=99.0" \
  -o results.json
```

Output contains per-pattern results with `temp_c` and `temp_label` fields.

### Phase transitions

If a structural phase transition occurs between two temperatures, run two **separate**
refinement jobs with different initial cells:

```bash
# Low-temperature phase (e.g. 303–363 K):
python gsas2_pawley.py --data ltp_data/ --space-group "P 21/c" \
  --cell "a=10.83,b=10.2,c=9.2,beta=99.0" -o ltp_results.json

# High-temperature phase (e.g. 383–413 K):
python gsas2_pawley.py --data htp_data/ --space-group "P 21/c" \
  --cell "a=11.05,b=10.15,c=9.17,beta=99.0" -o htp_results.json
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

**Typical job command:**
```bash
python gsas2_pawley.py \
  --data /input/pattern.xy \
  --space-group "P 21/c" \
  --cell "a=10.83,b=10.2,c=9.2,beta=99.0" \
  -o /output/result.json
```

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
