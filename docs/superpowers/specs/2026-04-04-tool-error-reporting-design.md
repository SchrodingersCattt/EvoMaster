# Tool Error Reporting Fix

## Problem

BashTool returns `str` regardless of exit code. `normalize_tool_result` uses an `"Error:"` prefix heuristic that misses tracebacks and other non-prefixed error output, marking them as `status="success"`. Additionally, `ToolMessage.to_api_dict()` does not carry `status` to the OpenAI-compatible API (which lacks an `is_error` field), so the LLM has no reliable signal that a tool call failed.

Example: a Python script fails with `ModuleNotFoundError` (exit code 1), but the framework reports success and the LLM receives no explicit error indicator.

## Design

Two targeted changes that together fix both the framework layer and the LLM-visible layer.

### Change 1: BashTool exit code check

**File:** `matmaster/tools/builtin/bash_tool.py`

`_execute()` currently returns a plain `str` with the exit code embedded in the text. After this change, when `exit_code != 0`, it returns `ToolResult(status="error", content=obs)` instead.

The content string itself is unchanged (still includes output, working directory, and `[Command finished with exit code N]`). The only difference is the return type switches from `str` to `ToolResult` with the correct status.

When `exit_code == 0`, the method continues to return `str`, which `normalize_tool_result` handles as before.

### Change 2: Framework-level error content wrapping

**File:** `matmaster/core/tool_runner.py`

In `_execute_one()`, after `normalize_tool_result()` and before truncation, inject an `<error>` wrapper around the content of any result with `status="error"`:

```python
if tr.status == "error" and not tr.content.lstrip().startswith("<error>\n"):
    tr = tr.model_copy(update={
        "content": f"<error>\n{tr.content}\n</error>"
    })
```

**Position in the pipeline:**

```
execute -> normalize -> error_wrap (NEW) -> truncate -> hook -> results
```

Rationale for this position:
- Before truncate: the `<error>` tag adds ~16 bytes, negligible impact on truncation thresholds.
- Before hooks: hooks see the raw `status` field and can act on it; the `<error>` tag in content is a presentation concern for the LLM, not a hook concern.
- Idempotency guard: `startswith("<error>\n")` prevents double-wrapping if a tool already includes the tag.

### What is NOT changed

- `tool_result.py` / `normalize_tool_result`: the `"Error:"` prefix heuristic stays as a fallback for legacy string returns.
- `base.py` exception handler: continues to return `f"Error: {e}"`, which normalize correctly detects.
- `messages.py` / `ToolMessage`: no protocol-layer change. The OpenAI API has no `is_error` field; the `<error>` content wrapper is the LLM-facing signal.
- Other builtin tools: most already return `ToolResult(status="error")` or raise exceptions caught by `base.py`. No changes needed for this fix. Note: EditTool has two "no match" return paths that return plain strings without an `"Error:"` prefix (e.g., `"No replacement was performed, old_string did not appear verbatim in ..."`). These are pre-existing and out of scope for this fix.
- Bohrium tool: already uses explicit `ToolResult(status="error")` for failure cases. It now additionally benefits from the `<error>` content wrapper. Note: Bohrium error results contain JSON in content (e.g., `{"success": false, ...}`). After wrapping, the LLM sees `<error>\n{...json...}\n</error>`. Modern LLMs handle this fine, but downstream scripts that parse `ToolResultEvent.result` for raw JSON will need to account for the outer tags.

## Effect Matrix

| Layer | Before | After |
|-------|--------|-------|
| `ToolResult.status` (BashTool, exit!=0) | `"success"` | `"error"` |
| `ToolResultEvent` (SSE / persistence) | wrong status | correct status |
| PostToolCall hooks | wrong status | correct status |
| LLM-visible content | raw text, no error marker | `<error>...</error>` wrapped |

## Backward Compatibility

- All existing tools that return `str` continue through `normalize_tool_result` unchanged.
- Tools that already return `ToolResult(status="error")` gain the `<error>` wrapper in content (additive, not breaking).
- `ToolResultEvent.result` content changes for error results (gains `<error>` wrapper). SSE consumers or scripts that parse this field for raw JSON should be aware.
- No API protocol changes. No new dependencies.

## Known Limitations (out of scope)

- **EditTool "no match" paths**: return strings like `"No replacement was performed..."` without `"Error:"` prefix. `normalize_tool_result` marks these as `status="success"`. This is pre-existing and not addressed here.
- **exit_code=0 with `"Error:"` in output**: some programs output `Error: ...` to stderr but exit 0 (e.g., linters in warning mode). `normalize_tool_result` will mark these as `status="error"`, triggering the `<error>` wrapper. This is pre-existing behavior from the `"Error:"` heuristic, not introduced by this change.
