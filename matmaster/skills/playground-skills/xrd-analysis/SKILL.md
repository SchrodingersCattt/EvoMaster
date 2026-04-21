---
name: xrd-analysis
description: "PXRD Pawley refinement (lattice parameter extraction, thermal expansion, phase transitions) and SCXRD structure solution/refinement/CIF generation. Use for any crystallographic data analysis task involving raw diffraction data."
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

> **MANDATORY**: Use `solve_refine_scxrd.py` for ALL SCXRD tasks. Do NOT write custom charge-flipping or refinement code.

1. **Gather all metadata FIRST**: Before running the script, extract from the input files:
   - **Cell parameters**: from P4P (`CELL` line), INS (`CELL` line), or task description.
   - **Space group**: from P4P (`SPGRP` line), INS (from `LATT`+`SYMM`), or task description.
   - **Element list**: from INS (`SFAC` line), task description, or chemical formula.
   - **Wavelength**: from P4P (`CTYPE` → Mo=0.71073, Cu=1.54178), INS, or task description.
2. **Run solution + refinement** with ALL available flags:
   ```bash
   python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
     --hkl reflections.hkl --p4p crystal.p4p \
     --elements "C H N O" --sg P21/c \
     --trials 5 --grid 128 -o refined.cif
   ```
   The script tries SHELX first (if installed), then falls back to Python charge-flipping + least-squares refinement. **Always pass `--elements`** — without it, atom-type assignment defaults to generic organic elements and heavy atoms will be misassigned.
3. **Evaluate output JSON**: Check `R1`, `wR2`, `GOOF`, `n_atoms_asym`, `formula`.
4. **If R1 > 0.15 on first attempt**:
   - Verify `--sg` is correct (try alternative settings if uncertain).
   - Increase `--grid 128` or `--grid 192` for large cells (V > 2000 ų).
   - Increase `--trials 10`.
   - Verify `--elements` includes ALL expected elements.
   - Re-run with updated flags. Do NOT write custom code.
5. **CIF generation**: The script writes a CIF file with cell parameters, space group, atom positions, R-factors, and GOOF. See `references/scxrd_cif_formatting.md` for required fields and formatting rules.
6. **Validation**: Run `checkcif-validator` skill on the generated CIF. Fix any A-level alerts.
7. **Disorder modeling** (if needed): See `references/scxrd_solution_refinement.md`.

### Script: solve_refine_scxrd.py

```
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl reflections.hkl --p4p crystal.p4p -o refined.cif

# With INS file:
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl reflections.hkl --ins crystal.ins -o refined.cif

# Manual cell:
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl reflections.hkl --cell "12.5 8.3 14.1 90 95.2 90" \
  --sg P21 --wavelength 0.71073 -o refined.cif
```

**Output**: CIF file + JSON summary printed to stdout with R1, wR2, GOOF, cell volume, atom count.

**Best-practice flags** (always use when information is available):
- `--elements "C H N O S"` — **strongly recommended**; provide expected element list for accurate atom-type assignment. Without it, defaults to common organic elements and heavy atoms may be misassigned.
- `--sg <space_group>` — always pass the space group if known (from P4P/INS or literature). The script uses symmetry operations to constrain the solution; P1 default wastes parameters.
- `--grid 128` for large unit cells (V > 2000 ų). `--trials 5` if R1 > 0.15 on first attempt.

## Hard Constraints

- **USE PROVIDED SCRIPTS — MANDATORY**: You **must** use `refine_lattice_pxrd.py` for PXRD and `solve_refine_scxrd.py` for SCXRD. **Do NOT write custom Pawley refinement, charge-flipping, or least-squares code from scratch.** The provided scripts are tested and validated; hand-written replacements produce poor results (high R-factors, wrong atom assignments, fractional coordinates outside [0,1]). If a script fails, debug its inputs (file format, parameters) — do not replace it.
- **Deliverables first**: For SCXRD tasks, ALWAYS produce a CIF file. An imperfect CIF is infinitely better than no CIF.
- **No fabrication**: If refinement fails, report the failure honestly. Do NOT invent lattice parameters or R-factors.
- **Validate**: ALWAYS run checkCIF on any generated CIF before finishing.
- **Uncertainties**: For PXRD, always report lattice parameters with estimated standard deviations.
- **Phase-separate fits**: For thermal expansion with phase transitions, fit EACH phase separately. Report slope, intercept, R² for each.
- **Script-first debugging**: If `solve_refine_scxrd.py` gives poor R-factors: (1) check `--elements` flag — always pass expected elements; (2) try `--trials 5`; (3) try `--grid 128` for large cells; (4) verify space group is correct. Do NOT fall back to writing your own refinement code.
- **SCXRD flag discipline**: ALWAYS pass ALL available information to `solve_refine_scxrd.py`:
  - `--elements` — **MANDATORY** when element list is known (from P4P, INS, or task description).
  - `--sg` — **MANDATORY** when space group is known.
  - `--grid 128` — use for any unit cell with V > 1500 ų.
  - `--trials 5` — default to 5 trials unless cell is very small (V < 500 ų).

## When to Use

- "Refine lattice parameters from this PXRD data" → `refine_lattice_pxrd.py`
- "Analyze thermal expansion from temperature-dependent PXRD" → `refine_lattice_pxrd.py --multi-temp`
- "Solve this crystal structure from HKL data" → `solve_refine_scxrd.py`
- "Generate a CIF from these diffraction files" → `solve_refine_scxrd.py`
- "Identify the phase from XRD pattern" → mcp-mat-xrd `xrd_phase_identification` (then this skill for quantitative analysis)
