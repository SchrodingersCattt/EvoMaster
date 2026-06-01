# Eval Question Design Rules

## Prompt & Fixture Rules (avoid 透题)
Reconstruct the real user task — write the prompt as a natural user would, never reverse-engineer it from the hidden checklist.

- Write as a natural user would ask; no internal terminology, tool names, file formats, or method names that hint at the solution.
- Don't mention platform names (Bohrium) unless the question tests general user intent.
- `human_prompt_seed` must not expose the expected failure pattern, quality gate, source-grounding requirement, forbidden shortcut, or desired diagnosis — unless the real user explicitly asked for it.
- Don't specify output filenames/JSON keys unless a user naturally would; when you must, make them sound like normal deliverables (`summary`, `notes`, `n_kept`), not the rubric (`qc_report`, `grounding_report`, `n_experimental_kept`).
- Keep evidence in realistic artifacts (logs, run records, source/provenance notes, messy tables). Avoid answer-shaped fixture fields like `is_valid`, `expected_*`, `*_converged`, `value_type=experimental/estimated` when a real log line or note carries the same signal.
- Don't add reference bundles, source snippets, or extra output artifacts just to ease judging. If the real task only needs `INPUT`/`STRU`/`KPT`, don't require `source_report.md`.
- Put evaluation intent in `scoring_checklist`, `reference_answers`, and judge criteria — the prompt/fixtures provide evidence, not the answer.

Self-check before adding a trajectory-derived question:
1. If all hidden checklist text were removed, would the prompt still look like a real user request?
2. Do any fixture field names, file names, or requested output keys reveal the answer?
3. Did we add a curated reference the real user/session did not have?
4. Is every extra deliverable something the user would naturally want?
5. Is the failure point discoverable from realistic evidence instead of being stated in the prompt?

## Scope Classification
- `knowledge`: Tests domain knowledge + correct execution (no platform specifics in checklist)
- `platform`: Tests Bohrium runtime integration (paths, images, manifest, submission)

## Checklist Rules
- Prefer deterministic verifiers (`text_file_regex`, `struct_file_*`, `artifact_exists`)
- Avoid `llm_binary_judge` unless no deterministic alternative exists
- Don't duplicate checks already covered by existing questions
- Put verifier params in the matching `reference_answers` entry (key == checklist `id`); use a `filename` basename glob when the user didn't name the output file

## Deduplication
- Before adding a question, check if the same *capability point* is already tested
- Different scenarios of the same capability (e.g., TBG vs CNT) are valid if they test different physical knowledge
- Same scenario with different parameters (e.g., Si SCF vs Ge SCF) is NOT worth duplicating
