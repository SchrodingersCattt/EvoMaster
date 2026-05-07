# PXRD Refinement Skill Evaluation Coverage

This document maps the load-bearing rules in `SKILL.md` to the long-form
evaluation task `PXRD_thermal_expansion_001_20260506`. When a new rule is added
to the skill, update this table and either map it to an existing checklist item
or add a new one.

| Skill rule | Evaluation coverage |
|---|---|
| HC1: Use the provided GSAS-II scripts; do not write local Pawley/Rietveld replacements. | `local_solver_avoided` checks the tool-call trace and answer for use of `gsas2_pawley.py` on Bohrium and absence of local scipy/numpy replacements. |
| HC2: Initial cell must come from a reference source. | `grounding_source` plus the prompt-provided RTP/HTP reference cells cover this. The long task does not intentionally trip the no-reference failure path. |
| HC3: Reject bad refinements (`wR`/`Rwp`/volume drift). | Numeric cell and slope checks (`rtp_*`, `htp_*`) catch wrong cells; `required_fields` and `phase_transition_identified` catch implausible merged reporting. |
| HC4: Verify Bohrium submit executed and produced `log` plus JSON output. | `bohrium_submit_executed` checks that submit/download evidence or explicit answer text confirms both files existed before numeric reporting. |
| HC5: Keep Bohrium job count within the hard budget and avoid redundant reruns. | `bohrium_job_budget`, `efficiency_judge`, `turn_budget`, and `duration_budget` cover job count, redundant sweeps, step count, and wall time. |
| HC6: Read `curation` and `warnings` before trusting cells. | `curation_inspected` checks that the agent inspected or reported curation diagnostics. |
| W1: Stage `input_dir` flat with `gsas2_pawley*.py` / `curation.py` next to data. | Indirectly covered by successful Bohrium execution and numeric outputs; missing helper modules or `curation.py` fails the run. |
| W2: Use `run.sh`; do not inline quoted Python commands in Bohrium `cmd`. | `runsh_wrapper_used` checks submit command shape in the tool-call trace. |
| W3: Parse `results.json` before reporting. | Numeric JSON checks and `bohrium_submit_executed` require values to come from downloaded result JSON, not prompt approximations. |
| W4: Use two-direction chain-cell for multi-pattern runs. | Slope/cell numeric checks indirectly require robust chain results; prompt recommends `--chain-cell-direction both`. |
| W5: Inspect `merge_audit` when reporting merged chain results. | `merge_audit_surfaced` checks final answer or reasoning references `merge_audit` or `self_heal_audit`. |
| W6: Self-audit assembled series for smoothness/outliers before reporting. | Slope checks and `merge_audit_surfaced`/`curation_inspected` cover this in the current long task. |
| W7: End with parseable `<eval_results>` JSON matching exact dot paths. | `answer_json_numeric` items and `required_fields` cover parseability and required paths. |
