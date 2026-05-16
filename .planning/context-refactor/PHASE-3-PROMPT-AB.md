# Phase 3 Prompt Shape A/B

Date: 2026-05-16
Decision: keep-merged

## Fixtures

- Single current file
- Two current files
- Current image
- Workspace path
- Mixed old session attachments plus current turn attachments

## Evaluation Criteria

- Correctly identifies current-turn attachments as current material.
- Does not treat old session attachments as current task input.
- Preserves image payload in `UserMessage.images`.
- Tool selection remains stable for file-analysis tasks.
- No nested or duplicated `<current_instruction>` tags.

## Result

`keep-merged`

## Rationale

The offline fixture confirmed both render paths are structurally valid:

- Merged shape keeps current turn text and current attachments inside one
  `<current_instruction>` block, with the existing `[Current attachments]`
  label.
- Split shape renders a concise `<current_instruction>` plus a separate
  `<turn_attachments>` block containing the same files, image, and workspace
  path.
- The split shape is cleaner for parser-like consumers, but this task did not
  include model/tool-selection eval data proving that production behavior stays
  stable for file-analysis tasks.

Phase 3 therefore keeps the Phase 2C production default: merged current-turn
attachments. `ContextRenderOptions(split_turn_attachments=True)` and
`run_meta["split_turn_attachments"]` remain available for controlled follow-up
experiments.
