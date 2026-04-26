---
name: xrd-analysis
description: "Single-crystal XRD (SCXRD) structure solution & refinement from HKL/P4P/INS data, and powder XRD (PXRD) Pawley lattice parameter refinement. Triggers on: SCXRD structure solution, HKL reflections, HKLF4 data, crystal structure determination, crystallographic refinement, CIF generation from diffraction data, PXRD lattice refinement, powder diffraction analysis, variable-temperature PXRD cell extraction, data formatting for XRD files."
---

# XRD Analysis (SCXRD + PXRD)

Two scripts in `scripts/`:

| Script | Inputs | Output |
|---|---|---|
| `solve_refine_scxrd.py` | HKL (HKLF4) + INS/P4P | CIF + R-factors + JSON |
| `refine_lattice_pxrd.py` | PXRD pattern + SG + cell | Refined cell + JSON |

## When to Use

- **SCXRD / single-crystal diffraction**: User has .hkl + .p4p or .ins files → `solve_refine_scxrd.py`
- **Data formatting for SCXRD**: User has HKL/P4P/INS and needs CIF conversion → `solve_refine_scxrd.py`
- **Crystal structure determination**: User provides reflection data and asks for structure → `solve_refine_scxrd.py`
- **PXRD lattice refinement**: User has powder pattern + known space group → `refine_lattice_pxrd.py`
- **Variable-temperature PXRD**: Multiple patterns at different T → `refine_lattice_pxrd.py --multi-temp`
- **Single-crystal XRD / HKL / SHELX → CIF**: this skill, NOT `pxrd-refinement`

## SCXRD Workflow

> **⚠ MANDATORY**: Use `solve_refine_scxrd.py` from this skill. NEVER write custom charge-flipping, Patterson, or least-squares code from scratch.

1. **Locate script**: `matmaster/skills/xrd-analysis/scripts/solve_refine_scxrd.py`
2. **Gather metadata** from .p4p/.ins: cell, space group, wavelength, elements
3. **Run**:
   ```
   python solve_refine_scxrd.py --hkl data.hkl --ins data.ins --p4p data.p4p \
       --sg "P21" --elements Fe C H N O -o result.cif --json result.json
   ```
   Key flags: `--elements` **MANDATORY**, `--sg` **MANDATORY**
4. **Check** result.json: R1 < 0.15 → accept; R1 > 0.15 → re-examine SG/elements
5. **Post-process CIF** if needed: verify all mandatory fields present

## PXRD Workflow

1. **Locate script**: `matmaster/skills/xrd-analysis/scripts/refine_lattice_pxrd.py`
2. **Run**:
   ```
   python refine_lattice_pxrd.py --data pattern.xy --sg "P21/c" \
       --cell "a=10.5,b=12.3,c=8.7,beta=105.2" --wavelength 1.5406 -o result.json
   ```
3. **Check** result.json: `wR < 0.20` → accept

## Hard Constraints

1. **Use the provided scripts.** Never write custom SCXRD solution / refinement code. The scripts handle SHELX fallback, charge-flipping, least-squares, and CIF formatting.
2. **Do not fabricate crystallographic parameters.** If refinement fails or R-factors are unacceptable, report the failure — do not invent numbers.
3. **Elements and space group are mandatory.** Always pass `--elements` and `--sg` to `solve_refine_scxrd.py`.
4. For PXRD: initial cell must come from a reference (user/prompt, CIF, literature). Never invent cell from d-spacing guesses.
