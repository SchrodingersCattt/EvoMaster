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

5. **Hard budget: at most 3 Bohrium jobs per VT-PXRD task.** Plan: (a) RTP-forward batch, (b) HTP-forward batch — submit both in parallel, wait, download. (c) If any RTP point has wR > 10 %, submit one RTP-reverse batch. Merge per contract-5 below. **That is the entire workflow — 2 or 3 jobs total.** Forbidden patterns that waste budget and cause timeouts: re-submitting with tweaked parameters, single-temperature retry jobs, "v2" re-runs, extra HTP-reverse jobs, verification re-runs. Bohrium is deterministic — same input = same output. **Always use an absolute path for `input_dir`** (the tool resolves relative paths from workspace root, not cwd — relative paths cause silent submit failures).

6. **Curation runs by default.** It auto-clips low-2θ artifacts (typical on DFT-simulated patterns), picks peaks, and emits a PASS/WARN/FAIL verdict in the result JSON. **Read `result["curation"]` and `result["warnings"]`** before trusting the cell. See `references/gsas2_refinement_guide.md` § "Curation and the wR trap".

## Workflow

1. **Stage `input_dir/`**: copy the script + `curation.py` + all data files, flat. Working directory inside the container is the unzipped `input_dir`, so use plain relative paths.

2. **Write a `run.sh` wrapper** inside `input_dir/`. Bohrium's job system silently drops commands that contain embedded quotes in the `cmd` field (e.g. `--space-group "P 21"` or `--cell "a=10.8,..."`). The reliable method is to put the full command in a shell script:

   ```bash
   # input_dir/run.sh — write this via Bash(cat <<'RUNEOF' > run.sh ...)
   #!/bin/bash
   python3 gsas2_pawley.py \
     --data ./ \
     --space-group "P 21" \
     --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \
     --wavelength 1.5406 \
     --multi-start 8 --chain-cell \
     --chain-cell-direction both \
     --standardize-cell ref \
     --debug-plot plots \
     -o results.json
   ```

   Then submit with `cmd="bash run.sh > log 2>&1"` — this is the **only** cmd string you should ever pass to the Bohrium tool. Never put the `python3 gsas2_pawley.py ...` invocation directly in the `cmd` field.

   `--multi-start 8` runs Pawley eight times from deterministically-perturbed seeds and keeps the lowest-wR result (cheap insurance against local-minimum traps; see `references/gsas2_refinement_guide.md` § "Multi-start"). 5 is also acceptable for clean lab data; bump to 8 (current default) for noisy / DFT-simulated / cold-start patterns. `--chain-cell` only promotes refinements that pass the wR / volume-jump gates, so it is safe even on patterns that may straddle a phase transition. For VT-PXRD or any multi-pattern run, use `--chain-cell-direction both`; the script will run forward and reverse internally, merge high-wR tied points by reference-volume proximity, and emit `merge_audit` (with `table` and `warnings`) plus `forward_results` / `reverse_results`. `--standardize-cell ref` aligns each refined cell to the initial-cell setting via axis-permutation search (handles monoclinic a↔c/β flip, orthorhombic axis relabelling, etc.); use `--standardize-cell niggli` for triclinic or when the refined cell may be a non-standard reduced cell (requires spglib in the image). **Always pass `--standardize-cell ref` (or `niggli` for triclinic) in VT-PXRD or any multi-pattern run** to ensure consistent cell parameters across patterns. Image: `registry.dp.tech/dptech/dp/native/prod-19853/xrd-app:dev-260119`. Machine: `c32_m128_cpu`. Full templates in `references/bohrium_workflow.md`. Key rules: never use `cd` in `cmd`; always end `cmd` with `> log 2>&1`. **Polling discipline:** `Bash(command="sleep 45")` before **every** poll. Never `sleep` > 60 s. Submit all jobs at once, then loop: sleep 45 → poll all → sleep again until all Finished. A batch takes 3-8 min; 4-5 polls suffice.

3. **Parse `results.json`**: check `success`, `warnings`, `curation.verdict` first; then `wR`/`Rwp` against the contract-3 thresholds; then cell vs. initial cell. On failure, see `references/gsas2_refinement_guide.md` § "Common errors and fixes" and adjust deliberately — do not loop.

4. **Use script-level two-direction chain-cell for high-wR cold-start points.** In a VT-PXRD batch, the coldest temperatures often have noisier data and yield wR > 10% even with multi-start. Run the phase batch once with `--chain-cell-direction both` instead of manually submitting a second reverse job. The output `results` is already merged; inspect `merge_audit.table` before reporting. Do not hand-merge forward/reverse JSON unless the script is unavailable.

5. **Verify the script's per-pattern merge decisions before reporting.** With `--chain-cell-direction both`, the script picks forward vs reverse for each pattern using a refinement-quality rule: lower wR wins, except when both wR > 10% and |wR_fwd − wR_rev| < 3%, in which case it picks the result whose volume is closer to `reference_volume`. Inspect `merge_audit.table` (per pattern: `wR_forward`, `V_forward`, `dV_ref_forward`, `wR_reverse`, `V_reverse`, `dV_ref_reverse`, `chosen`, `reason`, `warning`) and surface it in your answer. **Pay extra attention to `merge_audit.warnings`**: an entry appears for any pattern that is high-wR (>10%) in *both* directions AND > 1% off `reference_volume` in both — that pattern likely converged to a wrong basin in both chains, and the script's choice between them is at best the lesser-evil. Treat its cell as suspect and consider re-refining that single pattern from a different seed (e.g., adjacent low-wR result, or a tighter perturbation). If the script is unavailable and you must merge by hand, follow the same per-pattern rule.

6. **Self-audit the assembled series before reporting (agent-side analysis).** The script returns per-pattern refinements and does not perform any series-level analysis. After assembling results across all patterns (e.g. all temperatures within each phase), you must check: (a) physically expected monotonic / smooth trends hold (tolerance loose enough for measurement noise); (b) no point jumps > 5% relative to its neighbours; (c) any series-level slopes you are asked to report are not dominated by `merge_audit.warnings` patterns. If a check fails, identify the outlier, swap it with the other direction's result from `forward_results` / `reverse_results`, and recheck. If still inconsistent, report the anomaly in the answer. **Never skip this step — a "converged" refinement with plausible wR can silently produce a wrong cell.**

7. **Final-answer JSON contract.** When the question specifies a machine-checked structured-JSON output, end your final message with a single `<eval_results>...</eval_results>` block whose JSON keys follow the question's exact dot-paths. After drafting, scan your own answer for the literal string `<eval_results>` and parse the JSON: if the tag is missing or the JSON is malformed, fix and re-emit before sending. **Never end the message without this self-check** — physically correct values in an unparseable shell score zero on the numeric verifier.

## When `gsas2_autoindex.py` is appropriate

Only when no reference cell exists at all, AND a Bravais hint is available, AND you have already told the user that auto-indexing on simulated / artifact-laden PXRD frequently fails. See `references/autoindex.md` for parameter choice, the GSAS-II Visser failure mode on DFT-simulated data (low-angle artifact prefix kills the (010)/(100) reflections needed to constrain low-symmetry cells), and how to interpret an empty `candidates[]`.

## References (read on demand)

- `references/gsas2_refinement_guide.md` — input format, parameter tables, refinement step strategy, R-factor interpretation, **curation + wR trap**, common errors and fixes
- `references/bohrium_workflow.md` — image / machine / `cmd` templates, parallel submit + interleaved poll patterns, failure triage
- `references/autoindex.md` — when to attempt, GSAS-II Visser limits on DFT-simulated PXRD, parameter selection
