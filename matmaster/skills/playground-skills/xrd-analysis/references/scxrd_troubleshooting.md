# SCXRD Troubleshooting & Debugging Checklist

Quick-reference for diagnosing and fixing SCXRD structure solution and CIF generation failures.

## Before Running the Script — Input Verification

### 1. Gather ALL metadata first (do NOT skip)

Before calling `solve_refine_scxrd.py`, extract every piece of information from the input files:

| Info needed | Source | CLI flag |
|-------------|--------|----------|
| Cell parameters (a,b,c,α,β,γ) | P4P (`CELL` line) or INS (`CELL` line) | `--p4p` / `--ins` / `--cell` |
| Space group | P4P (`SPGRP`), INS (`SYMM`+`LATT`), or literature | `--sg` |
| Wavelength | P4P (`CTYPE Mo`→0.71073, `Cu`→1.54178), INS (`CELL λ ...`) | `--wavelength` |
| Elements | Known from chemistry, INS (`SFAC`), or task description | `--elements` |

### 2. MANDATORY flags — always pass these

```bash
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl data.hkl --p4p crystal.p4p \
  --elements "C H N O"   \  # ← ALWAYS pass expected elements
  --sg "P 21/c"          \  # ← ALWAYS pass space group if known
  --grid 128             \  # ← Use 128 for any cell V > 1500 ų
  --trials 5             \  # ← More trials = better sampling
  -o refined.cif
```

**Critical**: Without `--elements`, the script defaults to common organic elements and will miss metals, heavy atoms, or unusual heteroatoms. Without `--sg`, the script defaults to P1 which wastes parameters and gives worse results.

## Common Failure Modes

### R1 > 0.15 — Poor structure solution

**Diagnosis steps** (try in order):
1. Check `--elements`: Did you include ALL expected elements? Missing a heavy atom is the #1 cause of high R-factors.
2. Check `--sg`: Is the space group correct? Wrong symmetry wastes parameters and produces artifacts.
3. Try `--grid 128 --trials 5 --cycles 1000`: More computational effort often helps.
4. Verify HKL data: Are reflections reasonable? Check that the file parses correctly (script reports reflection count to stderr).
5. Try `--sigma-thresh 2.5`: Lower threshold finds more atom peaks (useful for light-atom structures).

### Missing atoms / wrong atom count

1. Lower `--sigma-thresh` (default 3.5): Try 2.5 or even 2.0 for structures with many light atoms.
2. Check `--elements`: The atom-type assignment depends on having the correct element list.
3. For large cells (V > 2000 ų): Use `--grid 128` or `--grid 192`.

### Wrong atom types (e.g., N assigned as C)

1. Always pass `--elements "C H N O ..."` with the correct expected element list.
2. The algorithm assigns types by matching electron density peak heights to expected Z values — having the right element list is critical.

### CIF missing required fields

The script generates a comprehensive CIF. If specific fields are missing:
- Cell parameters, space group, formula, R-factors, atom positions are always included.
- Run `checkcif-validator` to identify missing fields.
- For fields the script cannot compute, add them manually (e.g., `_exptl_crystal_description`, `_diffrn_ambient_temperature`).

## Data Format Issues

### HKL file format
Expected: SHELX HKLF 4 format (h k l F² σ(F²)), fixed-width or space-separated.
End marker: `0  0  0  0.00  0.00`

**Common problem**: file has a header or extra columns → script skips unparseable lines.
**Fix**: Verify the file starts with reflection data. If it has a header, remove it.

### P4P file format (Bruker)
Key lines the parser looks for:
- `CELL a b c alpha beta gamma` — cell parameters
- `CTYPE Mo` or `CTYPE Cu` — wavelength
- `SPGRP P21/c` or `SPTS ...` — space group

**Common problem**: Non-standard P4P with `CELL` on a different keyword.
**Fix**: Pass cell parameters manually with `--cell "a b c alpha beta gamma"`.

### INS file format (SHELX)
Key lines: `CELL λ a b c α β γ`, `SFAC element_list`, `LATT N`, `SYMM ...`

## After Solution — Quality Checks

### 1. Check R-factors
| Quality | R1 | wR2 | Action |
|---------|-----|------|--------|
| Good | < 0.08 | < 0.20 | Proceed to validation |
| Marginal | 0.08–0.15 | 0.20–0.30 | Try improvements; proceed if no better |
| Poor | > 0.15 | > 0.30 | Try `--trials 5 --grid 128`; check inputs |

### 2. Validate with checkCIF
```bash
python ${CHECKCIF_SKILL_DIR}/scripts/run_checkcif.py --file refined.cif
```
Fix A-level alerts. Explain B-level alerts.

### 3. Verify atom positions
- All fractional coordinates should be in [0, 1) — the script wraps automatically.
- Atom count should match expected formula.
- No unreasonably short interatomic distances (< 0.5 Å indicates overlapping atoms).

## Emergency: Script Fails Completely

If `solve_refine_scxrd.py` cannot produce a refined structure:

1. **Write a minimal CIF manually** with cell parameters from P4P/INS:
   ```
   data_structure
   _cell_length_a   12.345
   _cell_length_b   8.901
   _cell_length_c   14.567
   _cell_angle_alpha 90.00
   _cell_angle_beta  95.23
   _cell_angle_gamma 90.00
   _cell_volume      1234.5
   _space_group_name_H-M_alt 'P 21/c'
   _space_group_IT_number     14
   _chemical_formula_sum      'C10 H12 N2 O3'
   ```
2. **A partial CIF is always better than no CIF**.
3. Report what failed and what was attempted.
