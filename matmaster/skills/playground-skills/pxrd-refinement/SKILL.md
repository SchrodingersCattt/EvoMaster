---
name: pxrd-refinement
description: "GSAS-II powder refinement: Pawley (cell), Rietveld (structure), or autoindex from peaks. For PXRD/Pawley/Rietveld/lattice tasks on Bohrium image xrd-app:dev-260119—not SCXRD solution."
---

# PXRD Refinement (Pawley / Rietveld / Auto-index)

Three scripts in `scripts/`, all run on Bohrium:

| Script | Inputs | Output |
|---|---|---|
| `gsas2_pawley.py` | PXRD + space group + initial cell | refined cell + ESDs + wR |
| `gsas2_rietveld.py` | PXRD + CIF | refined cell + atoms + Rwp + updated CIF |
| `gsas2_autoindex.py` | PXRD + Bravais hint | candidate cells (often 0 candidates on noisy data) |

`scripts/curation.py` is a shared dependency (artifact-prefix detection, baseline fitting, peak picking, PASS/WARN/FAIL verdict). All three scripts import it; **always stage it next to the script** in `input_dir/`.

## Script Selection

- User has PXRD pattern + (known or guessable) space group → `gsas2_pawley.py`
- User has PXRD + a CIF model → `gsas2_rietveld.py`
- Multi-pattern PXRD series asking for one cell per pattern → `gsas2_pawley.py × N` (split jobs across phase or structure-setting changes; do **not** chain a single cell through a discontinuity)
- Single-crystal XRD / HKL / SHELX → not this skill

## Hard contracts (load-bearing — read every time)

1. **Use the provided scripts. Never write your own GSAS-II driver, and never write a local-Python "Pawley" / "Rietveld" replacement that uses `scipy.optimize` on peak positions.** No `pawley_all.py`, no `pawley_local.py`, no `import GSASIIscriptable` wrappers. Every known pitfall (the `newgpx=` kwarg, Pawley reflection generation order, fixing histogram Scale to break SVD correlation, profile-function intialization, ESD extraction) is already handled inside `scripts/gsas2_pawley.py` and `scripts/gsas2_rietveld.py`. **Shell wrappers** that call `python3 gsas2_pawley.py ...` with composed flags are fine. **Python wrappers are not, and "I'll just least-squares the peak 2θ values myself" is not refinement** — it is a fabricated number that will fail the eval.

2. **Initial cell must come from a reference.** Priority: user/prompt → CIF → prior refinement → adjacent pattern in the same phase/series → literature → `gsas2_autoindex.py`. **Never invent a cell from `d_max = λ/(2 sinθ_min)` or by eyeballing peak positions.** If autoindex returns 0 candidates and none of the other sources are available, the correct action is **stop and report `"cannot refine: no initial cell available"`** — *not* "I picked a=10.5, b=9.6, c=10.2 and went with it".

3. **Validate the result before reporting.** A refinement can "converge" onto a 2× / 3× / 4× supercell with `wR > 40 %`; the math closes but the cell is wrong. **Reject and report failure if `wR > 0.20` (Pawley) or `Rwp > 0.15` (Rietveld), or if refined volume differs from the initial-cell volume by > 20 %.** Do not paper over a bad refinement by reporting the bad numbers.

4. **Verify the submit actually executed.** After `Bohrium(action="download", ...)` the result directory MUST contain `log` (script stdout/stderr) AND the script's `--output` JSON file. **If `log` is missing or 0 bytes, the cmd never ran**: re-read your cmd string vs. the templates in `references/bohrium_workflow.md`, fix it, and resubmit *with the corrected cmd*. **Do not switch to a local Python solver because Bohrium "isn't working".** If three consecutive cmd-attempts produce empty output, stop and report `"Bohrium submit not executing user cmd"` — that is a platform issue, not a "write your own GSAS-II" cue.

5. **Hard budget: minimize Bohrium jobs per multi-pattern task.** General plan: split data into physically continuous phases/segments; submit one forward batch per segment in parallel; add at most one reverse batch only for a segment whose merged-quality audit shows high-wR or wrong-basin risk. For a two-segment series this is normally 2 jobs, or 3 jobs if one segment needs the reverse chain. Forbidden patterns that waste budget and cause timeouts: re-submitting with tweaked parameters, single-pattern retry jobs, "v2" re-runs, extra reverse jobs without audit evidence, verification re-runs. Bohrium is deterministic — same input = same output. **Always use an absolute path for `input_dir`** (the tool resolves relative paths from workspace root, not cwd — relative paths cause silent submit failures).

6. **Curation runs by default.** It auto-clips low-2θ artifacts (typical on DFT-simulated patterns), picks peaks, and emits a PASS/WARN/FAIL verdict in the result JSON. **Read `result["curation"]` and `result["warnings"]`** before trusting the cell. See `references/gsas2_refinement_guide.md` § "Curation and the wR trap".

## Workflow

1. **Stage `input_dir/`**: copy `gsas2_pawley*.py` + `curation.py` + all data files, flat. Working directory inside the container is the unzipped `input_dir`, so use plain relative paths.

2. **Write a `run.sh` wrapper** inside `input_dir/`. Bohrium's job system silently drops commands that contain embedded quotes in the `cmd` field (e.g. `--space-group "P 21"` or `--cell "a=10.8,..."`). The reliable method is to put the full command in a shell script:

   ```bash
   # input_dir/run.sh — write this via Bash(cat <<'RUNEOF' > run.sh ...)
   #!/bin/bash
   python3 gsas2_pawley.py \
     --data ./ \
     --space-group "P 21" \
     --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \
     --wavelength 1.5406 \
     --multi-start 5 --chain-cell \
     --chain-cell-direction both \
     --standardize-cell ref \
     --debug-plot plots \
     -o results.json
   ```

   Then submit with `cmd="bash run.sh > log 2>&1"` — this is the **only** cmd string you should ever pass to the Bohrium tool. Never put the `python3 gsas2_pawley.py ...` invocation directly in the `cmd` field.

   `--multi-start 5` runs Pawley five times from deterministically-perturbed seeds and keeps the lowest-wR result (cheap insurance against local-minimum traps; see `references/gsas2_refinement_guide.md` § "Multi-start"). **Do not raise `--multi-start` above 5 in any standard run** — every additional start adds ~12 % Bohrium time per pattern, and combined with `--chain-cell-direction both` (2× work) easily blows past the 1200 s online task timeout. Use the cell-distance tiebreak + `merge_audit.warnings` to handle wrong-basin patterns instead of brute-forcing more starts. `--chain-cell` only promotes refinements that pass the wR / volume-jump gates, so it is safe even on patterns that may straddle a phase/setting change. For any multi-pattern run, use `--chain-cell-direction both` within each continuous segment; the script will run forward and reverse internally, merge high-wR tied points by reference-volume proximity, and emit `merge_audit` (with `table` and `warnings`) plus `forward_results` / `reverse_results`. `--standardize-cell ref` aligns each refined cell to the initial-cell setting via axis-permutation search (handles monoclinic a↔c/β flip, orthorhombic axis relabelling, etc.); use `--standardize-cell niggli` for triclinic or when the refined cell may be a non-standard reduced cell (requires spglib in the image). **Always pass `--standardize-cell ref` (or `niggli` for triclinic) in any multi-pattern run** to ensure consistent cell parameters across patterns. After the chain merge, the script also runs `--self-heal-chain` (on by default; threshold `--self-heal-v-jump-threshold` defaults to 2 %, retry budget `--self-heal-multi-start` defaults to 5): any pattern whose volume drifts > 2 % from the average of its successful immediate neighbours is re-refined in-process from the neighbour-average cell, and the rescue is kept only if it lands closer to the neighbour average. The audit lives under `self_heal_audit.outliers`. Disable with `--no-self-heal-chain` if you intentionally want the raw chain output (e.g. studying single-pattern sensitivity) or if a tight wall-clock budget cannot absorb an extra K-start rescue per outlier (`K = --self-heal-multi-start`, default 5). Image: `registry.dp.tech/dptech/dp/native/prod-19853/xrd-app:dev-260119`. Machine: `c32_m128_cpu`. Full templates in `references/bohrium_workflow.md`. Key rules: never use `cd` in `cmd`; always end `cmd` with `> log 2>&1`. **Polling discipline:** `Bash(command="sleep 45")` before **every** poll. Never `sleep` > 60 s. Submit all jobs at once, then loop: sleep 45 → poll all → sleep again until all Finished. A batch takes 3-8 min; 4-5 polls suffice.

3. **Parse `results.json`**: check `success`, `warnings`, `curation.verdict` first; then `wR`/`Rwp` against the contract-3 thresholds; then cell vs. initial cell. On failure, see `references/gsas2_refinement_guide.md` § "Common errors and fixes" and adjust deliberately — do not loop.

4. **Use script-level two-direction chain-cell for high-wR or direction-sensitive points.** In a multi-pattern batch, edge patterns or noisier scans can yield wR > 10% even with multi-start. Run each continuous segment with `--chain-cell-direction both` instead of manually submitting a second reverse job. The output `results` is already merged; inspect `merge_audit.table` before reporting. Do not hand-merge forward/reverse JSON unless the script is unavailable.

5. **Verify the script's per-pattern merge decisions before reporting.** With `--chain-cell-direction both`, the script picks forward vs reverse for each pattern using a refinement-quality rule: lower wR wins, except when both wR > 10% and |wR_fwd − wR_rev| < 3%, in which case it picks the result whose **cell** (Σ relative diffs over a/b/c plus any non-90° angle) is closer to the initial reference cell. Cell distance is the discriminating signal — V proximity alone can be fooled in monoclinic systems where multiple (a, b, c, β) combinations give the same volume. If individual cell parameters are unavailable, the picker falls back to V proximity. Inspect `merge_audit.table` (per pattern: `wR_forward`, `V_forward`, `dV_ref_forward`, `cell_dist_forward`, `wR_reverse`, `V_reverse`, `dV_ref_reverse`, `cell_dist_reverse`, `chosen`, `reason`, `warning`) and surface it in your answer. **Pay extra attention to `merge_audit.warnings`**: an entry appears for any pattern that is high-wR (>10%) in *both* directions AND either > 1% off `reference_volume` in both, or > 1% off `reference_cell` in both — that pattern likely converged to a wrong basin in both chains, and the script's choice between them is at best the lesser-evil. Treat its cell as suspect and consider re-refining that single pattern from a different seed (e.g., adjacent low-wR result, or a tighter perturbation). If the script is unavailable and you must merge by hand, follow the same per-pattern rule.

6. **Self-audit the assembled series before reporting (agent-side analysis).** The script's `self_heal_audit` already replaces single-pattern wrong-basin outliers (V > 2 % off the neighbour average) with a multi-start rescue. After assembling results across all patterns within each continuous segment, still verify: (a) physically expected monotonic / smooth trends hold (tolerance loose enough for measurement noise); (b) no remaining point jumps > 5% relative to its neighbours (the 2 % rescue is a best-effort, not a guarantee); (c) any series-level slopes you are asked to report are not dominated by `merge_audit.warnings` or `self_heal_audit.outliers[*].decision != "replaced"` patterns. If a check fails, identify the outlier, try swapping with the other-direction result from `forward_results` / `reverse_results`, and recheck. If still inconsistent, report the anomaly in the answer. **Never skip this step — a "converged" refinement with plausible wR can silently produce a wrong cell.**

7. **Final-answer JSON contract.** When the question specifies a machine-checked structured-JSON output, end your final message with a single `<eval_results>...</eval_results>` block whose JSON keys follow the question's exact dot-paths. After drafting, scan your own answer for the literal string `<eval_results>` and parse the JSON: if the tag is missing or the JSON is malformed, fix and re-emit before sending. **Never end the message without this self-check** — physically correct values in an unparseable shell score zero on the numeric verifier.

## When `gsas2_autoindex.py` is appropriate

Only when no reference cell exists at all, AND a Bravais hint is available, AND you have already told the user that auto-indexing on simulated / artifact-laden PXRD frequently fails. See `references/autoindex.md` for parameter choice, the GSAS-II Visser failure mode on DFT-simulated data (low-angle artifact prefix kills the (010)/(100) reflections needed to constrain low-symmetry cells), and how to interpret an empty `candidates[]`.

## References (read on demand)

- `references/gsas2_refinement_guide.md` — input format, parameter tables, refinement step strategy, R-factor interpretation, **curation + wR trap**, common errors and fixes
- `references/bohrium_workflow.md` — image / machine / `cmd` templates, parallel submit + interleaved poll patterns, failure triage
- `references/autoindex.md` — when to attempt, GSAS-II Visser limits on DFT-simulated PXRD, parameter selection
