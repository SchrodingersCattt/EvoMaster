# Builtin Web Tools Design

## Summary

Implement `web_search` and `web_fetch` as matmaster native builtin tools, migrating core functionality from the legacy `playground/mat_master/tools/` (evomaster BaseTool system) to the matmaster `BuiltinTool` ABC.

## Context

The legacy playground tools (`WebSearchTool`, `ExtractWebpageTool`) are registered only through the old evomaster `BasePlayground` system. The matmaster package (`matmaster/tools/builtin/`) has no web capability -- it only registers file/shell/task tools. These two web tools are general-purpose capabilities needed by the Agent.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| HTTP origin | Control plane (direct httpx) | No session dependency; avoids remote env network/dep constraints |
| Sync/async | Sync (`httpx.Client`) | Current Kernel/ToolRegistry/BuiltinTool chain is all sync; async migration is Phase 13 future work |
| Search API | SearchApi.io (unchanged) | Already in use, API key via env var |
| Web fetch scope | Streamlined (B) | Cache + PDF + HTML-to-markdown; no circuit breaker or domain throttling |
| Naming | `web_search` + `web_fetch` | Underscore-consistent, clean break from legacy MCP names |
| Registration | TOML declares intent, `_init_builtin_tools` instantiates | TOML list is declarative config; actual registration is in native_tools list |
| Legacy dropped | Circuit breaker, domain throttling, request delay | Simplify first version; Agent single-run doesn't hit same domain enough to need these |
| Legacy dropped | `page`/`location` params (web_search) | Agent rarely paginates search results; can add back if needed |

## Architecture

### WebSearchTool

- File: `matmaster/tools/builtin/web_search_tool.py`
- Inherits: `BuiltinTool` (no session/workdir needed)
- API: SearchApi.io (`https://www.searchapi.io/api/v1/search`, engine=google)
- API key: `SEARCHAPI_API_KEY` or `SEARCHAPI_KEY` from env
- HTTP client: `httpx.Client` (sync, matches current execution chain)

Input schema:

```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string", "description": "Search query."},
    "top_k": {"type": "integer", "description": "Max results to return.", "default": 10},
    "gl": {"type": "string", "description": "Country code (e.g. us, cn).", "default": "us"},
    "hl": {"type": "string", "description": "Language code (e.g. en, zh-cn).", "default": "en"}
  },
  "required": ["query"]
}
```

Output: `ToolResult` directly. Success: `ToolResult(status="success", content=json_results)`. Error: `ToolResult(status="error", content=message)`. Using `ToolResult` instead of raw strings ensures error status is correctly propagated (raw JSON strings like `{"status":"error",...}` would be misclassified as success by `normalize_tool_result`).

### WebFetchTool

- File: `matmaster/tools/builtin/web_fetch_tool.py`
- Inherits: `BuiltinTool` (uses `workdir` for cache directory)
- Constructor: `WebFetchTool(workdir=ctx.workdir)` -- cache at `{workdir}/.web_cache/`
- HTTP client: `httpx.Client` (sync) with browser-like headers

Input schema:

```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "array",
      "items": {"type": "string"},
      "description": "List of URLs to fetch. Single URL also accepted as list with one element."
    }
  },
  "required": ["url"]
}
```

Schema uses `array` only (no `oneOf`) for LLM function-calling compatibility. `_execute` normalizes a bare string to `[string]` defensively.

Processing pipeline:

1. Check disk cache (SHA256 URL hash, 15min TTL, 200 entry cap)
2. Fetch via `httpx.Client.get` with browser-like User-Agent
3. Content-Type dispatch:
   - HTML: BeautifulSoup noise removal (script/style/nav/footer + cookie/banner class/id patterns) then markdownify to markdown (fallback: plain text extraction)
   - PDF: PyMuPDF text extraction (optional dep; missing = error message)
   - Other: raw text
4. Truncate to 50,000 chars
5. Write to cache
6. Multi-URL: `ThreadPoolExecutor` concurrent fetch (sync context)

Output:
- Single URL: content string directly
- Multi URL: JSON `{"<url>": {"content": "..."}, ...}` (errors inlined as `{"error": "..."}`)
- Overall status: "success" if at least one URL succeeded, "error" if all failed

### Disk Cache (`_WebpageDiskCache`)

Defined inside `web_fetch_tool.py` (private, single consumer).

- Location: `{workdir}/.web_cache/`
- Key: `sha256(url)[:16]`
- Entry format: JSON `{"url", "content", "fetched_at"}`
- TTL: 900s (15 min)
- Max entries: 200 (oldest-first eviction)
- Thread safety: lock on eviction
- Atomic writes via tempfile rename

### Error Handling

Both tools return `ToolResult` directly from `_execute` (bypassing `normalize_tool_result` ambiguity). Base class `execute()` catches uncaught exceptions as fallback.

Tool-specific:
- `web_search`: missing API key returns `ToolResult(status="error", content="Missing SearchApi key...")`
- `web_fetch`: per-URL errors in multi-URL mode are inlined in the result dict, not raised
- 403/429: one retry with alternate User-Agent, then error

## Registration

### Session gate note

`_init_builtin_tools` is guarded by `ctx.session is not None`. Web tools don't need session, but they are registered inside this guard alongside other builtin tools. This is acceptable: if there's no session, the Agent has no workspace and web tools alone aren't useful. No need to extract web tools out of the session gate.

### TOML config (`matmaster/exps/direct.toml`)

```toml
[tools]
builtin = [
    "execute_bash",
    "list_dir",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "task_complete",
    "spawn",
    "web_search",
    "web_fetch",
]
```

### Exp._init_builtin_tools

Add to the `native_tools` list:

```python
from matmaster.tools.builtin import WebSearchTool, WebFetchTool

native_tools = [
    # ... existing tools ...
    WebSearchTool(),
    WebFetchTool(workdir=ctx.workdir),
]
```

### `__init__.py` export

Add `WebSearchTool` and `WebFetchTool` to `matmaster/tools/builtin/__init__.py`.

## Dependencies

| Package | Status | Notes |
|---------|--------|-------|
| httpx | Already in project | Used by providers layer |
| beautifulsoup4 | Already in project | HTML parsing |
| lxml | Already in project | BS4 parser backend |
| markdownify | Already in project | HTML-to-markdown; fallback to plain text if missing |
| PyMuPDF (fitz) | Already in project (optional extra) | PDF extraction; import guarded with try/except |

## Files Changed

| File | Change |
|------|--------|
| `matmaster/tools/builtin/web_search_tool.py` | New file |
| `matmaster/tools/builtin/web_fetch_tool.py` | New file |
| `matmaster/tools/builtin/__init__.py` | Add exports |
| `matmaster/core/exp.py` | Add to native_tools in `_init_builtin_tools` |
| `matmaster/exps/direct.toml` | Add tool names to builtin list |
| `matmaster/exps/explore.toml` | Add `web_search` and `web_fetch` (information gathering fits explore mode) |
| `tests/matmaster/tools/test_web_search_tool.py` | New test file |
| `tests/matmaster/tools/test_web_fetch_tool.py` | New test file |
