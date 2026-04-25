---
name: xrd-analysis
description: "Single-crystal XRD (SCXRD) structure solution from HKL reflections — produces refined CIF via charge-flipping or SHELX. Also: PXRD Pawley refinement for lattice parameters, thermal expansion, and phase transitions. Load when the task involves HKL/HKLF4 data, crystal structure determination, crystallographic refinement, single-crystal diffraction, or powder XRD analysis."
skill_type: operator
depends_on: mcp-mat-xrd, checkcif-validator
---

# XRD Analysis Skill

Handles powder XRD (PXRD) lattice parameter refinement and single-crystal XRD (SCXRD) structure solution, refinement, and CIF generation.

## Trigger Conditions

- Task involves extracting lattice parameters from PXRD data (Pawley/Le Bail refinement)
- Task involves temperature-dependent PXRD analysis (thermal expansion, phase transitions)
- Task involves solving a crystal structure from single-crystal HKL data
- Task involves generating a CIF from diffraction data
- Task mentions SHELX, Rietveld, Pawley, or structure refinement

## PXRD Analysis Workflow

> **MANDATORY**: Use `refine_lattice_pxrd.py` for ALL Pawley refinement. Do NOT write custom refinement code.

1. **Load data**: Read PXRD file(s) — supported formats: XY, CSV, DAT (two-column: 2θ, intensity).
2. **Phase identification** (optional): Use `mat_xrd_xrd_phase_identification` from mcp-mat-xrd skill to identify the phase and get approximate cell parameters.
3. **Pawley refinement**: Run `refine_lattice_pxrd.py` with crystal system and initial lattice parameters. **Always use this script** — it handles peak search, background subtraction, and fitting internally.
4. **Multi-temperature analysis**: For T-dependent data, use `--multi-temp` mode — the script automatically fits thermal expansion, detects phase transitions, and reports linear fit parameters (slope, intercept, R²) per phase.
5. **Report**: Lattice parameters (a, b, c, α, β, γ), unit cell volume V, fit statistics at each temperature, and thermal expansion analysis.

See `references/pxrd_thermal_expansion.md` for detailed guidance on interpreting results.

### Script: refine_lattice_pxrd.py

**Single pattern:**
```
python ${SKILL_DIR}/scripts/refine_lattice_pxrd.py \
  --file pattern.xy --crystal-system tetragonal \
  --initial-params "a=10.8,c=6.5" --wavelength 1.5406
```

**Multi-temperature (thermal expansion + phase transition detection):**
```
python ${SKILL_DIR}/scripts/refine_lattice_pxrd.py \
  --dir /path/to/data/ --crystal-system tetragonal \
  --initial-params "a=10.8,c=6.5" --wavelength 1.5406 --multi-temp
```

**Output JSON** (single): `{a, c, volume, a_sigma, c_sigma, residual, n_peaks_matched, ...}`

**Output JSON** (multi-temp): `{per_temperature: [...], thermal_expansion: {phase_transition: bool, phase_1: {V_slope, V_intercept, V_R_squared, a_slope, ...}, phase_2: {...}, transition_temperature_K}}`

## SCXRD Workflow

> **MANDATORY**: Use `solve_refine_scxrd.py` for ALL single-crystal XRD tasks. Do NOT write custom charge-flipping or refinement code. Do NOT spawn sub-agents to analyze or rewrite the script.

> **TIME BUDGET**: The entire SCXRD workflow (script run + checkCIF + report) should complete in **< 8 minutes**. The script internally performs iterative difference Fourier refinement to recover missing atoms — do NOT attempt to replicate this manually.

1. **Parse data**: Identify the HKL file (SHELX HKLF4) and any P4P/INS file in the workspace.
2. **Run the script immediately** — pass all available info (`--elements`, `--sg`, `--p4p`/`--ins`). The script handles SHELX (if installed) or Python charge-flipping + iterative ΔF refinement.
3. **CIF output**: Script writes CIF + prints JSON summary. See `references/scxrd_cif_formatting.md`.
4. **Quality check**: If R1 > 0.15 → retry **once** with `--trials 5 --grid 96`. Accept the result regardless — an imperfect CIF is the deliverable.
5. **Validate**: Run `checkcif-validator` on the CIF. Fix A-level alerts if possible.
6. **Report and finish**: Present the R-factors and CIF. Do NOT enter exploration loops trying to improve the solution further.

Script path: `${SKILL_DIR}/scripts/solve_refine_scxrd.py` (companion `solve_refine_scxrd_lib.py` must be co-located).

### Script: solve_refine_scxrd.py

```
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl reflections.hkl --p4p crystal.p4p --elements "C H N O" -o refined.cif

# With INS file:
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl reflections.hkl --ins crystal.ins -o refined.cif

# Manual cell:
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl reflections.hkl --cell "12.5 8.3 14.1 90 95.2 90" \
  --sg P21 --wavelength 0.71073 --elements "C H N O" -o refined.cif
```

**Key flags** (always use when available):
- `--elements "C H N O S"` — **required** for correct atom-type assignment.
- `--sg <space_group>` — always pass if known; P1 default wastes parameters.
- `--grid 96` or `--grid 128` for large unit cells (V > 2000 ų), only if first attempt gives R1 > 0.15.
- `--trials 5` if R1 > 0.15 on first attempt.

**Timing**: Defaults (grid=72, trials=2) complete in ~2-3 min. Early convergence detection stops when phases stabilise. Run the script as your **first action** to maximise time for validation.

## Hard Constraints

- **Use provided scripts**: `refine_lattice_pxrd.py` for PXRD, `solve_refine_scxrd.py` for SCXRD. Do NOT write custom refinement code. Do NOT read/analyze script source code. Do NOT spawn sub-agents to explore script internals.
- **Deliverables first**: For SCXRD, ALWAYS produce a CIF. An imperfect CIF beats no CIF. If R1 > 0.15 after `--trials 5 --grid 96`, accept the CIF and move on.
- **No exploration loops**: Run the script at most twice (default, then with enhanced parameters). Report the best result. Do NOT attempt iterative improvement, custom refinement, or sub-agent delegation.
- **No fabrication**: Do NOT invent lattice parameters or R-factors.
- **Validate**: Run checkCIF on any generated CIF before finishing.
- **Uncertainties**: Report PXRD lattice parameters with estimated standard deviations.
- **Phase-separate fits**: For thermal expansion with transitions, fit EACH phase separately (slope, intercept, R²).

## When to Use

- "Single-crystal XRD / SCXRD data" → `solve_refine_scxrd.py`
- "Solve this crystal structure from HKL data" → `solve_refine_scxrd.py`
- ".hkl file with .p4p or .ins file" → `solve_refine_scxrd.py`
- "Generate a CIF from these diffraction files" → `solve_refine_scxrd.py`
- "Refine lattice parameters from this PXRD data" → `refine_lattice_pxrd.py`
- "Analyze thermal expansion from temperature-dependent PXRD" → `refine_lattice_pxrd.py --multi-temp`
- "Identify the phase from XRD pattern" → mcp-mat-xrd `xrd_phase_identification` (then this skill for quantitative analysis)
