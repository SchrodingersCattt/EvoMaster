---
name: xrd-analysis
description: "SCXRD solution from HKL/INS/P4P and PXRD lattice refinement without GSAS-II (charge-flipping, SHELX, Pawley from peaks). Not GSAS-II Rietveld (pxrd-refinement) or phase ID (mcp-mat-xrd)."
---

# XRD Analysis — SCXRD Structure Solution & PXRD Lattice Refinement

Local Python scripts for crystallographic analysis. No Bohrium submission required — runs directly in the workspace.

## Scenario → Script

| Scenario | Script | Inputs |
|----------|--------|--------|
| SCXRD structure solution from HKL | `scripts/solve_refine_scxrd.py` | `.hkl` + space group + elements |
| HKL → CIF conversion | `scripts/solve_refine_scxrd.py` | `.hkl` (+ `.ins` / `.p4p` auto-discovered) |
| PXRD lattice refinement (no GSAS-II) | `scripts/refine_lattice_pxrd.py` | XY pattern + space group + initial cell |
| Multi-temperature lattice evolution | `scripts/refine_lattice_pxrd.py --multi-temp` | directory of XY patterns |

**NOT this skill:** GSAS-II Pawley/Rietveld → `pxrd-refinement`; phase ID → `mcp-mat-xrd`.

## SCXRD Workflow — MANDATORY

1. **Use the provided script. Never write your own SCXRD solver from scratch.**

   ```bash
   python3 scripts/solve_refine_scxrd.py \
       --hkl data.hkl --sg "P2_1/c" --elements C H N O \
       --grid 72 --trials 2 --cycles 400 \
       --output result.cif --json result.json
   ```

2. The script auto-discovers companion `.ins` / `.p4p` files from the HKL stem.

3. Pipeline: parse HKL (SHELX HKLF4) → try SHELX if installed → charge-flipping fallback → least-squares refinement → CIF output.

4. **CIF completeness check** — verify the output CIF contains all of:
   `_cell_length_a/b/c`, `_cell_angle_alpha/beta/gamma`, `_cell_volume`,
   `_space_group_name_H-M_alt`, `_space_group_IT_number`,
   `_space_group_crystal_system`, `_cell_formula_units_Z`,
   `_chemical_formula_sum`, `_chemical_formula_weight`,
   `_exptl_crystal_density_diffrn`, `_diffrn_radiation_type`,
   `_refine_ls_R_factor_gt`, `_refine_ls_wR_factor_ref`,
   `_refine_ls_goodness_of_fit_ref`,
   `_space_group_symop_operation_xyz`, `_atom_site_*` loop.

5. **Parse `result.json`** for R1, wR2, GooF. Report if R1 > 0.15 — the structure may be unreliable.

## PXRD Lattice Refinement Workflow

```bash
python3 scripts/refine_lattice_pxrd.py \
    --data pattern.xy --sg "Pm-3m" \
    --cell "a=3.905" --wavelength 1.5406 \
    -o result.json
```

Multi-temperature:
```bash
python3 scripts/refine_lattice_pxrd.py \
    --data ./ --sg "Pm-3m" \
    --cell "a=3.905" --wavelength 1.5406 --multi-temp \
    -o result.json
```

## Hard Constraints

1. **Use the provided scripts.** Do not write ad-hoc SCXRD code, charge-flipping implementations, or CIF generators from scratch. The scripts handle all known pitfalls (HKL parsing edge cases, symmetry operation generation, CIF field completeness, Hill formula ordering).

2. **Elements and space group are mandatory** for SCXRD. If not in the prompt, extract from INS/P4P companion files. Never guess elements.

3. **Do not fabricate atomic positions.** If charge-flipping finds no atoms, report failure — do not invent coordinates.

4. **Validate R-factors.** R1 > 0.15 or wR2 > 0.40 should be flagged as potentially unreliable. Report the numbers; let the user decide.
