# SCXRD Structure Solution & Refinement — Reference

## Software Priority

1. **SHELX** (shelxs + shelxl): Industry standard. `solve_refine_scxrd.py` tries
   this automatically if SHELX is on PATH.
   - Install: `conda install -c conda-forge shelx` (free for academics).
2. **Python charge-flipping** (built-in): Automatic fallback in the script. Works
   for most small-molecule structures.

## Typical Workflow

```
python ${SKILL_DIR}/scripts/solve_refine_scxrd.py \
  --hkl data.hkl --p4p data.p4p -o refined.cif
```

Then validate:
```
python ${CHECKCIF_SKILL_DIR}/scripts/run_checkcif.py --file refined.cif
```

## HKL File Format (SHELX HKLF 4)

Fixed-width or space-separated: `h  k  l   F²    σ(F²)`

The end-of-data marker is a line `  0  0  0  0.00  0.00`.

## P4P File Format (Bruker APEX)

Key lines:
- `CELL a b c alpha beta gamma`
- `CELLSD σa σb σc σα σβ σγ`
- `CTYPE Mo` / `CTYPE Cu` — radiation type → wavelength
- `SPGRP P21` — space group

## Charge-Flipping Algorithm (built-in)

1. Random phases → F(hkl) = |F_obs| · exp(iφ)
2. IFFT → electron density ρ(xyz)
3. Flip: if ρ < δ, negate ρ
4. FFT → new F(hkl); keep calc phases, restore obs amplitudes
5. Repeat 500–1000 cycles

Multiple trials with different random seeds are run; best is selected by R-factor.

## Improving Results

- **Increase grid**: `--grid 128` for large cells (V > 2000 ų).
- **More cycles**: `--cycles 1500` if convergence is slow.
- **More trials**: `--trials 5` for better sampling.
- **Element list**: `--elements "C H N O S"` helps atom-type assignment.

## Disorder Modeling

For structures with orientational or positional disorder (split positions):

1. Run the initial solution+refinement to get a baseline CIF.
2. Identify disordered sites from checkCIF alerts or anomalous displacement
   parameters (high U_iso).
3. For split-atom models, edit the CIF to add a second position for the
   disordered atom with partial occupancies summing to 1.0.
4. In SHELX: use `PART 1` / `PART 2` instructions with `SUMP` for occupancy
   constraints.

## Performance & Timeout Avoidance

The charge-flipping pipeline is computationally intensive. Plan your time budget:

| Step | Typical time | Notes |
|------|-------------|-------|
| Script run (grid 96) | 30–120 s | Default; fine for most structures |
| Script run (grid 72) | 10–40 s | Use for quick initial attempt |
| Script run (grid 128) | 60–300 s | Only for large cells (V > 2000 ų) |
| checkCIF validation | 5–15 s | Always run after CIF generation |

**Avoid timeout**: Run the script ONCE with default settings. If R1 < 0.15, proceed directly to CIF validation and delivery. Do NOT:
- Re-run the script multiple times with different parameters unless R1 > 0.15
- Write custom refinement code (the script handles everything)
- Spend turns debugging if the CIF already looks reasonable

**If the script is slow**: Use `--grid 72 --cycles 500 --trials 2` for a faster initial attempt. Upgrade to `--grid 96 --trials 3` only if R1 > 0.15.

## Expected R-Factor Ranges

| Structure quality | R1      | wR2     | GOOF    |
|-------------------|---------|---------|---------|
| Excellent         | < 0.03  | < 0.08  | ~1.0    |
| Good              | 0.03–0.05 | 0.08–0.12 | 0.95–1.10 |
| Acceptable        | 0.05–0.08 | 0.12–0.20 | 0.90–1.20 |
| Marginal          | 0.08–0.15 | 0.20–0.30 | > 1.20  |

If R1 > 0.15, the model likely has significant errors — check for missing atoms,
wrong space group, or unmodeled disorder.

## CheckCIF A-Level Alert Resolution

| Alert code | Meaning                        | Fix                                |
|------------|--------------------------------|------------------------------------|
| PLAT029    | _diffrn_measured_fraction low  | Report data completeness honestly  |
| PLAT041    | Calc & reported SumFormula     | Correct `_chemical_formula_sum`    |
| PLAT043    | Calculated & Reported mol wt   | Correct molecular weight           |
| PLAT340    | Bond precision on C-C          | Improve refinement or use restraints|
| PLAT601    | Structure has solvent-accessible voids | SQUEEZE or model solvent  |
