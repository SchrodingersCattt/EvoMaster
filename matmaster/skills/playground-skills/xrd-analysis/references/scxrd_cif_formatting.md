# SCXRD CIF Formatting & Deliverable Requirements

## CIF File Deliverable Priority

**ALWAYS produce a CIF file as the primary deliverable.** An imperfect CIF is infinitely better than no CIF.

## Required CIF Fields

A complete SCXRD CIF must include these sections:

### Crystal Data
```
_cell_length_a          12.345(1)
_cell_length_b          8.901(1)
_cell_length_c          14.567(2)
_cell_angle_alpha       90.00
_cell_angle_beta        95.23(1)
_cell_angle_gamma       90.00
_cell_volume            1234.5(3)
_cell_formula_units_Z   4
```

### Space Group
```
_space_group_name_H-M_alt    'P 21/c'
_space_group_IT_number       14
_symmetry_cell_setting       monoclinic
```

### Chemical Information
```
_chemical_formula_sum         'C10 H12 N2 O3'
_chemical_formula_moiety      'C10 H12 N2 O3'
_chemical_formula_weight      208.22
```

### Refinement Statistics
```
_refine_ls_R_factor_gt        0.0423
_refine_ls_wR_factor_ref      0.1056
_refine_ls_goodness_of_fit_ref 1.032
```

### Atom Positions (loop)
```
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_U_iso_or_equiv
C1 C 0.1234 0.4567 0.7890 0.0234
...
```

## Formatting Rules

1. **Fractional coordinates**: Must be in range [0, 1). Values outside this range indicate errors.
2. **Uncertainties**: Report as `value(esd)` format, e.g., `12.345(1)`.
3. **Space group notation**: Use Hermann-Mauguin symbols with proper formatting (spaces between elements).
4. **Chemical formula**: Elements in Hill order (C first, then H, then alphabetical).
5. **R-factors**: R1 should be < 0.15 for publishable structures. wR2 ≈ 2-3× R1.

## Workflow: HKL Data → CIF

1. Parse input files (HKL + P4P or INS)
2. Extract: cell parameters, space group, wavelength, elements
3. Run `solve_refine_scxrd.py` with ALL available information:
   ```bash
   python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
     --hkl data.hkl --p4p data.p4p \
     --elements "C H N O" \
     -o refined.cif
   ```
4. Check R1: if > 0.15, try `--trials 5 --grid 128`
5. Run checkCIF validation
6. Fix A-level alerts before delivery

## Common CIF Formatting Errors

| Error | Cause | Fix |
|-------|-------|-----|
| Missing `_chemical_formula_sum` | Script didn't auto-generate | Add manually from known composition |
| Fractional coords > 1.0 | Coordinate wrapping error | Apply modulo 1.0 to all fract_xyz |
| Wrong space group | Auto-detection failed | Pass `--sg` explicitly from P4P/INS |
| Missing hydrogen atoms | H not included in element list | Add `H` to `--elements` flag |
| R1 > 0.15 | Poor solution | Try `--trials 5 --grid 128`; verify space group |

## checkCIF Validation

ALWAYS run checkCIF after generating CIF:
```bash
python matmaster/skills/playground-skills/checkcif-validator/scripts/run_checkcif.py \
  --file refined.cif
```

Address ALL A-level alerts before delivery. B-level alerts should be explained.

## Emergency Fallback

If `solve_refine_scxrd.py` fails completely:
1. Extract cell parameters from P4P/INS manually
2. Write a minimal CIF with just crystal data (cell, space group, formula)
3. Note in the CIF that atom positions are not refined
4. **A partial CIF is always better than no CIF**
