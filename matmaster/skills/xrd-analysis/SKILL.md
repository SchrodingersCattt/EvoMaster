---
name: xrd-analysis
description: "When the user needs to solve/refine a single-crystal XRD (SCXRD) structure from HKL/INS/P4P files and produce a CIF, or refine lattice parameters from powder XRD data locally (without Bohrium/GSAS-II). Triggers on: SCXRD structure solution, charge flipping, HKL→CIF, PXRD lattice parameter extraction (local scipy-based), variable-temperature PXRD thermal expansion."
skill_type: operator
---

# XRD Analysis — SCXRD Structure Solution & Local PXRD Refinement

## When to Use

- **SCXRD**: User provides HKL (SHELX HKLF4) + optional INS/P4P files and wants a solved/refined crystal structure as CIF
- **PXRD (local)**: User wants lattice parameters from powder XRD data using local Python (scipy) — *not* GSAS-II on Bohrium (use `pxrd-refinement` skill for GSAS-II)
- **Variable-temperature PXRD**: Multi-temperature lattice parameter extraction with thermal expansion fitting

## SCXRD Workflow — MANDATORY: Use Provided Scripts

> ⚠ **MANDATORY**: Always use `scripts/solve_refine_scxrd.py` first.
> Do NOT write your own charge-flipping, structure solution, or CIF writer from scratch.
> The script handles SHELX fallback, Cromer-Mann scattering factors, 12 space groups,
> and IUCr-compliant CIF output. Writing custom code wastes turns and produces poor R-factors.

### Step 1 — Identify Input Files
- `.hkl` — reflection data (SHELX HKLF4 format)
- `.ins` — SHELX instruction file (cell, SG, elements, wavelength)
- `.p4p` — Bruker instrument file (cell, wavelength)
- The script auto-discovers companion files from the HKL stem

### Step 2 — Run the Script
```bash
python3 scripts/solve_refine_scxrd.py \
  --hkl data.hkl \
  --ins data.ins \
  --sg "P2_1/c" \
  --elements "C H N O" \
  --grid 72 --trials 2 --cycles 400 \
  --output result.cif --json result.json
```

Key flags:
- `--sg` — space group (Hermann-Mauguin); overrides INS if given
- `--elements` — expected elements; overrides INS if given
- `--grid` — FFT grid size (default 72; increase for large cells)
- `--trials` — number of charge-flipping trials (default 2)
- `--cycles` — refinement cycles (default 400)
- `--json` — machine-readable output with R1, wR2, GooF, atoms

### Step 3 — Verify CIF Completeness
The output CIF must contain ALL of these field categories:
- Cell parameters (`_cell_length_a/b/c`, `_cell_angle_alpha/beta/gamma`, `_cell_volume`)
- Symmetry (`_symmetry_space_group_name_H-M`, `_symmetry_Int_Tables_number`, `_symmetry_equiv_pos_as_xyz`, `_space_group_crystal_system`)
- Formula (`_chemical_formula_sum`, `_chemical_formula_moiety`, `_chemical_formula_weight`, `_cell_formula_units_Z`)
- Quality (`_refine_ls_R_factor_gt`, `_refine_ls_wR_factor_ref`, `_refine_ls_goodness_of_fit_ref`)
- Atoms (`_atom_site_label`, `_atom_site_type_symbol`, `_atom_site_fract_x/y/z`, `_atom_site_U_iso_or_equiv`, `_atom_site_occupancy`)
- Metadata (`_exptl_crystal_density_diffrn`, `_diffrn_radiation_type`, `_audit_creation_method`)

If any fields are missing, add them manually to the CIF.

### Step 4 — Quality Check
- R1 < 0.15 is acceptable; R1 < 0.08 is good
- If R1 > 0.20, try: more trials (`--trials 4`), finer grid (`--grid 96`), more cycles (`--cycles 800`)
- Check fractional coordinates are in [0, 1) range
- Verify element assignments match expected composition

## PXRD Workflow (Local Refinement)

> For GSAS-II based refinement on Bohrium, use the `pxrd-refinement` skill instead.

```bash
python3 scripts/refine_lattice_pxrd.py \
  --data pattern.xy \
  --space-group "Pm-3m" \
  --cell "a=3.905,b=3.905,c=3.905" \
  --wavelength 1.5406 \
  -o results.json
```

For multi-temperature:
```bash
python3 scripts/refine_lattice_pxrd.py \
  --data-dir ./patterns/ \
  --space-group "Pm-3m" \
  --cell "a=3.905,b=3.905,c=3.905" \
  --wavelength 1.5406 \
  --multi-temp \
  -o results.json
```

## Hard Constraints

1. **USE PROVIDED SCRIPTS** — Do not write custom charge-flipping, Pawley refinement, or CIF writers
2. **No fabrication** — Never invent atom positions, cell parameters, or R-factors
3. **Elements + space group are MANDATORY** — Must be provided or extracted from INS/P4P
4. **Deliverables first** — Produce CIF/JSON output before any analysis discussion
