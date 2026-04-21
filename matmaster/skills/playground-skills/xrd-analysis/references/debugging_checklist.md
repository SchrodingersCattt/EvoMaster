# XRD Analysis Debugging Checklist

Quick reference for diagnosing and fixing common failures in SCXRD and PXRD analysis tasks.

## SCXRD: Critical Steps That Must Not Be Skipped

### Before running `solve_refine_scxrd.py`

1. **Identify all input files**: Determine which files are available:
   - HKL file (`.hkl`) — required
   - P4P file (`.p4p`) — Bruker format with cell, space group, wavelength
   - INS file (`.ins`) — SHELX format with cell, space group, elements
   - If neither P4P nor INS: need `--cell`, `--sg`, `--wavelength` manually

2. **Extract element list from task description**:
   - Read the task carefully for any mention of chemical formula, composition, or expected elements.
   - **ALWAYS pass `--elements`** — this is the single most impactful flag for result quality.
   - Without `--elements`, the script defaults to common organic elements and heavy atoms may be misassigned.
   - Example: if the task mentions "organometallic complex with Ru", use `--elements "C H N O Ru"`.

3. **Extract space group**:
   - From P4P: look for `SPGRP` line.
   - From INS: first line after title contains cell + space group.
   - From task description: may mention "monoclinic P21/c" etc.
   - **ALWAYS pass `--sg`** when known — P1 default wastes parameters.

### Running the script

```bash
# BEST: all information available
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl data.hkl --p4p data.p4p \
  --elements "C H N O S" \
  -o refined.cif

# With INS file
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl data.hkl --ins data.ins \
  --elements "C H N O" \
  -o refined.cif

# Manual cell (no P4P/INS)
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl data.hkl \
  --cell "12.5 8.3 14.1 90 95.2 90" \
  --sg P21/c --wavelength 0.71073 \
  --elements "C H N O" \
  -o refined.cif
```

### After running: check output

1. **Check R1**: The JSON output includes R1.
   - R1 < 0.08: Good. Proceed to checkCIF.
   - R1 0.08–0.15: Marginal. Try improvements below.
   - R1 > 0.15: Poor. Must try improvements.

2. **If R1 > 0.15 — improvement steps (in order)**:
   a. Verify `--elements` includes ALL expected elements (including H!)
   b. Try `--trials 5` (more random starts for charge flipping)
   c. Try `--grid 128` (finer density map, especially for large cells V > 2000 ų)
   d. Verify `--sg` is correct (wrong space group → high R-factor)
   e. Try `--cycles 1500` (more charge-flipping iterations)

3. **ALWAYS run checkCIF**:
   ```bash
   python ${CHECKCIF_SKILL}/scripts/run_checkcif.py --file refined.cif
   ```

4. **ALWAYS deliver a CIF file** — even an imperfect one. Partial CIF > no CIF.

### Common SCXRD failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "No cell parameters found" | Missing P4P/INS and no --cell | Provide --cell manually from task data |
| "No atoms found" | Grid too coarse or wrong space group | Use --grid 128 --trials 5 |
| Very high R1 (>0.30) | Missing --elements, wrong space group | Add --elements, verify --sg |
| Missing heavy atoms | Heavy element not in --elements list | Add all expected elements explicitly |
| Fractional coords outside [0,1] | Coordinate wrapping error | Script should handle this; check CIF |
| "Refinement error" | Numerical issue | Script writes unrefined CIF as fallback |

---

## PXRD: Critical Steps

### Before running `refine_lattice_pxrd.py`

1. **Identify crystal system**: From task description, literature, or phase ID.
   - If unknown: run `mat_xrd_xrd_phase_identification` first.

2. **Get initial lattice parameters**: From task description, literature, or phase ID.
   - Parameters must be within ~2% of true values for refinement to converge.
   - If only approximate values given, that's fine — the script refines from there.

3. **Know the wavelength**: Default is Cu Kα1 (1.5406 Å). Use actual wavelength if different (e.g., Mo Kα = 0.71073 Å, synchrotron).

### Running the script

```bash
# Single pattern
python ${SKILL_DIR}/scripts/refine_lattice_pxrd.py \
  --file pattern.xy --crystal-system tetragonal \
  --initial-params "a=10.8,c=6.5" --wavelength 1.5406

# Multi-temperature thermal expansion
python ${SKILL_DIR}/scripts/refine_lattice_pxrd.py \
  --dir /path/to/data/ --crystal-system tetragonal \
  --initial-params "a=10.8,c=6.5" --wavelength 1.5406 --multi-temp
```

### Common PXRD failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Too few peaks" | Noisy data or wrong peak thresholds | Script handles adaptively; check data format |
| "Too few matched peaks" | Initial params too far off | Get better initial params from literature/phase ID |
| Wrong crystal system | Misidentified phase | Re-run phase identification |
| Poor fit at high angles | Initial params inaccurate | Try --tolerance 0.5 for initial matching |
| No temperature extracted | Filename pattern not recognized | Use --temp-pattern "(\\d+)K" or similar regex |

---

## Data Format Verification

Before running any XRD script, verify the data file format:

### HKL file (SCXRD)
- Fixed-width or space-separated: `h  k  l  F²  σ(F²)`
- End-of-data marker: `0  0  0  0.00  0.00`
- Common issue: extra header lines → script should skip non-numeric lines

### PXRD data file
- Two-column: 2θ (degrees) and intensity
- Supported formats: `.xy`, `.csv`, `.dat`, `.txt`
- Delimiter: space, tab, or comma
- Lines starting with `#` or `!` are comments (auto-skipped)
- Common issue: data in different columns → verify column order

### P4P file
- Key lines to extract:
  - `CELL a b c alpha beta gamma` → cell parameters
  - `CELLSD σa σb σc σα σβ σγ` → uncertainties
  - `CTYPE Mo` → wavelength (Mo=0.71073, Cu=1.5406)
  - `SPGRP P21` → space group

### INS file
- Line 1: title
- Line 2: `CELL wavelength a b c alpha beta gamma`
- Line 3: space group equivalents
- `SFAC` line: element list → use for --elements
