---
name: pxrd-refinement
description: "Powder XRD refinement via GSAS-II. Pawley extracts a refined unit cell from PXRD + space group + initial cell; Rietveld refines a full structure from PXRD + CIF; gsas2_autoindex is a last-resort cell guesser from peak positions only. Triggers on: PXRD/powder XRD refinement, Pawley, Rietveld, lattice parameter extraction, variable-temperature PXRD per-temperature cell. All execution goes to Bohrium image xrd-app:dev-260119."
---

# PXRD Refinement (Pawley / Rietveld / Auto-index)

Three scripts in `scripts/`, all run on Bohrium:

| Script | Inputs | Output |
|---|---|---|
| `gsas2_pawley.py` | PXRD + space group + initial cell | refined cell + ESDs + wR |
| `gsas2_rietveld.py` | PXRD + CIF | refined cell + atoms + Rwp + updated CIF |
| `gsas2_autoindex.py` | PXRD + Bravais hint | candidate cells (often 0 candidates on noisy data) |

`scripts/curation.py` is a shared dependency (artifact-prefix detection, baseline fitting, peak picking, PASS/WARN/FAIL verdict). All three scripts import it; **always stage it next to the script** in `input_dir/`.

## When to trigger

- User has PXRD pattern + (known or guessable) space group → `gsas2_pawley.py`
- User has PXRD + a CIF model → `gsas2_rietveld.py`
- Variable-temperature PXRD asking for per-temperature cell → `gsas2_pawley.py × N` (split jobs across phase transitions; do **not** chain a single cell through a transition)
- Single-crystal XRD / HKL / SHELX → not this skill

## Hard contracts (load-bearing — read every time)

1. **Use the provided scripts. Never write your own GSAS-II driver, and never write a local-Python "Pawley" / "Rietveld" replacement that uses `scipy.optimize` on peak positions.** No `pawley_all.py`, no `pawley_local.py`, no `import GSASIIscriptable` wrappers. Every known pitfall (the `newgpx=` kwarg, Pawley reflection generation order, fixing histogram Scale to break SVD correlation, profile-function intialization, ESD extraction) is already handled inside `scripts/gsas2_pawley.py` and `scripts/gsas2_rietveld.py`. **Shell wrappers** that call `python3 gsas2_pawley.py ...` with composed flags are fine. **Python wrappers are not, and "I'll just least-squares the peak 2θ values myself" is not refinement** — it is a fabricated number that will fail the eval.

2. **Initial cell must come from a reference.** Priority: user/prompt → CIF → prior refinement → adjacent VT-PXRD point → literature → `gsas2_autoindex.py`. **Never invent a cell from `d_max = λ/(2 sinθ_min)` or by eyeballing peak positions.** If autoindex returns 0 candidates and none of the other sources are available, the correct action is **stop and report `"cannot refine: no initial cell available"`** — *not* "I picked a=10.5, b=9.6, c=10.2 and went with it".

3. **Validate the result before reporting.** A refinement can "converge" onto a 2× / 3× / 4× supercell with `wR > 40 %`; the math closes but the cell is wrong. **Reject and report failure if `wR > 0.20` (Pawley) or `Rwp > 0.15` (Rietveld), or if refined volume differs from the initial-cell volume by > 20 %.** Do not paper over a bad refinement by reporting the bad numbers.

4. **Verify the submit actually executed.** After `Bohrium(action="download", ...)` the result directory MUST contain `log` (script stdout/stderr) AND the script's `--output` JSON file. **If `log` is missing or 0 bytes, the cmd never ran**: re-read your cmd string vs. the templates in `references/bohrium_workflow.md`, fix it, and resubmit *with the corrected cmd*. **Do not switch to a local Python solver because Bohrium "isn't working".** If three consecutive cmd-attempts produce empty output, stop and report `"Bohrium submit not executing user cmd"` — that is a platform issue, not a "write your own GSAS-II" cue.

5. **One submit per task.** Never re-submit the same `input_dir + cmd` hoping for different output — Bohrium is deterministic. Multi-temperature: prefer one job with `--data ./` (directory batch) over N parallel jobs. Across a phase transition: one job per phase, submitted in parallel, polled in interleaved fashion (no `Bash(sleep)`).

6. **Curation runs by default.** It auto-clips low-2θ artifacts (typical on DFT-simulated patterns), picks peaks, and emits a PASS/WARN/FAIL verdict in the result JSON. **Read `result["curation"]` and `result["warnings"]`** before trusting the cell. See `references/gsas2_refinement_guide.md` § "Curation and the wR trap".

## Workflow

1. **Stage `input_dir/`**: copy the script + `curation.py` + all data files, flat. Working directory inside the container is the unzipped `input_dir`, so use plain relative paths.

2. **Submit / poll / download** via the `Bohrium` builtin tool. Pawley template (single line, copy verbatim — replace `<...>` placeholders only):

   ```
   python3 gsas2_pawley.py --data ./ --space-group "<SG>" --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" --wavelength 1.5406 --multi-start 5 --chain-cell --debug-plot plots -o results.json > log 2>&1
   ```

   `--multi-start 5` runs Pawley five times from deterministically-perturbed seeds and keeps the lowest-wR result (cheap insurance against local-minimum traps; see `references/gsas2_refinement_guide.md` § "Multi-start"). `--chain-cell` only promotes refinements that pass the wR / volume-jump gates, so it is safe even on patterns that may straddle a phase transition. Image: `registry.dp.tech/dptech/dp/native/prod-19853/xrd-app:dev-260119`. Machine: `c8_m32_cpu` (`c16_m64_cpu` for batches ≥5 patterns; raise to `c16_m64_cpu` whenever `--multi-start ≥ 5` or input has > 5 patterns). Full templates for Rietveld and autoindex, plus the parallel-submit + interleaved-poll pattern, are in `references/bohrium_workflow.md`. Key rules: never use `cd` in `cmd`; always end `cmd` with `> log 2>&1`; never block with `Bash(sleep)` while polling.

3. **Parse `result.json`**: check `success`, `warnings`, `curation.verdict` first; then `wR`/`Rwp` against the contract-3 thresholds; then cell vs. initial cell. On failure, see `references/gsas2_refinement_guide.md` § "Common errors and fixes" and adjust deliberately — do not loop.

## When `gsas2_autoindex.py` is appropriate

Only when no reference cell exists at all, AND a Bravais hint is available, AND you have already told the user that auto-indexing on simulated / artifact-laden PXRD frequently fails. See `references/autoindex.md` for parameter choice, the GSAS-II Visser failure mode on DFT-simulated data (low-angle artifact prefix kills the (010)/(100) reflections needed to constrain low-symmetry cells), and how to interpret an empty `candidates[]`.

## References (read on demand)

- `references/gsas2_refinement_guide.md` — input format, parameter tables, refinement step strategy, R-factor interpretation, **curation + wR trap**, common errors and fixes
- `references/bohrium_workflow.md` — image / machine / `cmd` templates, parallel submit + interleaved poll patterns, failure triage
- `references/autoindex.md` — when to attempt, GSAS-II Visser limits on DFT-simulated PXRD, parameter selection
