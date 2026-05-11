# Preflight Current Input Compaction Risk Addendum

This addendum updates `2026-05-11-preflight-current-input-compaction.md`.
Apply it before executing the original plan. The original plan already captures
most of the current-input split, but the current checkout shows two remaining
closure risks that must be covered explicitly.

## Goal

Close the critical path from API-captured `CurrentInputContext` to the kernel
and prevent current-query attachments from leaking into checkpoint base
rehydration.

## Addendum Task 1: Propagate Current Input Into Kernel Spec

**Files:**

- Modify: `matmaster/core/exp.py`
- Modify: `tests/matmaster/core/test_exp_runtime_v2.py`

### Steps

1. Add a failing test that builds an `Exp` runtime from a `PlaygroundContext`
   whose `run_meta` contains `current_input_context`.
2. Assert `runtime.spec.meta["current_input_context"]` equals the original
   context.
3. Update `Exp.build_runtime()` so the kernel meta copied from `run_meta`
   includes `current_input_context`.
4. Prefer a small helper if needed to keep the meta-copy boundary explicit.

### Verification

```bash
uv run pytest tests/matmaster/core/test_exp_runtime_v2.py::test_build_runtime_passes_current_input_context_to_kernel_meta -q
```

## Addendum Task 2: Add Attachment Range Filtering

**Files:**

- Modify: `matmaster/manifests/attachment.py`
- Modify: `matmaster/manifests/rehydrator.py`
- Modify: `matmaster/core/context_compactor.py`
- Modify: `tests/services/test_attachment_manifest_service.py`
- Modify: `tests/matmaster/manifests/test_rehydrator.py`
- Modify: `tests/matmaster/core/test_context_compactor.py`

### Steps

1. Add `filter_entries_in_event_range(entries, *, after_id, until_id)`.
2. Preserve existing `filter_entries_after_event_id()` by delegating to the
   range helper with `until_id=None`.
3. When either boundary is set, drop entries with `source_event_id is None`.
4. Add `until_event_id: int | None = None` to `CompactionRehydrator.build()`.
5. In rehydrator attachment construction, filter with both:
   - `after_id=latest_checkpoint_covered_until_event_id`
   - `until_id=until_event_id`
6. In `ContextCompactor.apply_compaction_plan()`, when preflight current split
   is active, call `rehydrator.build(until_event_id=pre_query_scope_event_id)`.
7. Keep non-current-split and runtime compaction behavior unchanged by leaving
   `until_event_id` unset.

### Required Assertions

- Rehydrator with events `old(id=10)` and `current(id=20)` plus
  `until_event_id=10` includes `old` only, not `current`.
- Rehydrator with `after_id=10` and no upper bound includes `current`.
- Preflight current split compact bundle does not include current attachments
  inside `<rehydrated_context>`.
- Current attachments still appear inside `<current_instruction>`.
- Checkpoint `base_snapshot` does not contain `<current_instruction>`, current
  image parts, or current-query attachment entries.

### Verification

```bash
uv run pytest \
  tests/services/test_attachment_manifest_service.py \
  tests/matmaster/manifests/test_rehydrator.py \
  tests/matmaster/core/test_context_compactor.py::TestPreflightCurrentInputSplit \
  -q
```

## Final Verification

After applying both addendum tasks and the original plan, run:

```bash
uv run pytest \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/services/test_attachment_manifest_service.py \
  tests/matmaster/manifests/test_rehydrator.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/test_chat_stream_direct.py \
  -q
```

Then run touched-file hooks:

```bash
uv run pre-commit run --files <touched files>
```
