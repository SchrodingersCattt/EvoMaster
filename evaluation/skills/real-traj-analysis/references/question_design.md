# Eval Question Design Rules

## Prompt Rules
- Write as a natural user would ask — no internal terminology
- Never mention tool names, file formats, or method names that would hint at the solution
- Never mention platform names (Bohrium) unless testing general user intent
- Don't specify output filenames unless the user naturally would

## Scope Classification
- `knowledge`: Tests domain knowledge + correct execution (no platform specifics in checklist)
- `platform`: Tests Bohrium runtime integration (paths, images, manifest, submission)

## Checklist Rules
- Prefer deterministic verifiers (`text_file_regex`, `struct_file_*`, `artifact_exists`)
- Avoid `llm_binary_judge` unless no deterministic alternative exists
- Don't duplicate checks already covered by existing questions
- Use `verify_params` with glob patterns when filenames are not user-specified

## Deduplication
- Before adding a question, check if the same *capability point* is already tested
- Different scenarios of the same capability (e.g., TBG vs CNT) are valid if they test different physical knowledge
- Same scenario with different parameters (e.g., Si SCF vs Ge SCF) is NOT worth duplicating
