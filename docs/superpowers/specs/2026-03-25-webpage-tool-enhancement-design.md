# Webpage Tool Enhancement: Noise Filtering + Markdown + Disk Cache

## Context

`playground/mat_master/tools/webpage.py` (`extract_info_from_webpage`) is the built-in web content extraction tool for MatMaster agents. Current implementation uses BeautifulSoup `get_text()` which loses all document structure (headings, lists, code blocks, links), only strips `script`/`style` tags, and has no caching — causing repeated fetches of the same URL within a single session.

Benchmarking against Claude Code's WebFetch (Turndown + Haiku summarization + 15min LRU cache) revealed three improvement areas with high ROI and low risk.

## Scope

Three changes to `webpage.py` + minimal touch on `playground.py` and `pyproject.toml`:

| ID | Change | Priority | Files |
|----|--------|----------|-------|
| P0 | Expand noise tag filtering | P0 | `webpage.py` |
| P1-a | HTML to Markdown via `markdownify` | P1 | `webpage.py`, `pyproject.toml` |
| P1-b | Per-workspace disk cache (15min TTL) | P1 | `webpage.py`, `playground.py` |

## Design

### P0: Noise Tag Filtering

**Location**: `_fetch_webpage_content()`, HTML parsing branch (current lines 146-157).

**Current**: Only removes `script` and `style`.

**New**:

```python
# Module-level constant
_NOISE_PATTERN = re.compile(r'cookie|banner|sidebar|menu', re.I)
```

```python
# Phase 1: Remove non-content structural tags
for tag in soup(['script', 'style', 'nav', 'footer', 'header',
                  'aside', 'noscript', 'iframe']):
    tag.decompose()

# Phase 2: Remove elements with noisy class/id attributes
for tag in soup.find_all(attrs={'class': _NOISE_PATTERN}):
    tag.decompose()
for tag in soup.find_all(attrs={'id': _NOISE_PATTERN}):
    tag.decompose()
```

**Rationale**: Academic publisher pages (ScienceDirect, Springer, Wiley) and documentation sites have heavy navigation, cookie consent banners, and sidebars. These waste tokens without contributing content. The pattern list is intentionally conservative (no `ad|promo|popup|modal`) to avoid false positives on article content.

### P1-a: HTML to Markdown

**Location**: Same function, immediately after noise cleaning.

**Current**:
```python
lines = (line.strip() for line in soup.get_text().splitlines())
chunks = (phrase.strip() for line in lines for phrase in line.split('  '))
content = ' '.join(chunk for chunk in chunks if chunk)
```

**New**:
```python
_used_markdownify = False
try:
    import markdownify
    content = markdownify.markdownify(
        str(soup),
        heading_style="ATX",
        strip=['img', 'svg'],
    )
    content = re.sub(r'\n{3,}', '\n\n', content)
    _used_markdownify = True
except ImportError:
    logger.warning('markdownify not available; falling back to plain text extraction')
    # existing get_text() logic as fallback
    lines = (line.strip() for line in soup.get_text().splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split('  '))
    content = ' '.join(chunk for chunk in chunks if chunk)
```

**Post-cleaning adjustment** (replaces current lines 159-160):

Current post-cleaning applies to both HTML and PDF paths:
```python
content = re.sub(r'\s+', ' ', content)                    # line 159: collapse all whitespace
content = re.sub(r'[^\x20-\x7E\x0A\x0D]', '', content)   # line 160: strip non-ASCII
```

Both lines must be changed:
- Line 159 collapses `\n` into spaces, destroying Markdown heading/list/code structure. For the Markdown path, skip this; for the plain-text fallback path, keep it.
- Line 160 strips all non-ASCII characters including CJK (Chinese/Japanese/Korean), Greek letters, and Unicode chemical symbols. This is a pre-existing bug that silently destroys content from Chinese journals, formulas with Greek letters (alpha, beta, gamma), etc. Replace with a targeted control-character filter that preserves Unicode text.

```python
# Replace lines 159-160 with:
if not _used_markdownify:
    # Plain-text path: collapse whitespace (Markdown path already handled above)
    content = re.sub(r'\s+', ' ', content)
# Both paths: strip control characters but preserve Unicode text (CJK, Greek, symbols)
content = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', content)
```

The new regex `[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]` removes only C0 control characters while preserving `\x09` (tab), `\x0A` (LF), `\x0D` (CR), and all Unicode text (CJK, Greek, math symbols, etc.).

**New dependency**: `markdownify` added to `pyproject.toml`.

**Graceful degradation**: If `markdownify` import fails, falls back to current `get_text()` logic with a warning log. The `_used_markdownify` flag ensures correct post-cleaning path selection.

**Downstream compatibility**:
- `compact_extract_webpage_observation`: reads `content` as opaque string, truncates to 500 char preview. Compatible.
- `tool_guard._observation_has_content`: checks for `webpage_detailed_contents` key existence. Compatible.
- `auto_save_tool_output`: saves raw JSON. Compatible.
- `collect_evidence.py` (deep-survey): does not consume `extract_info_from_webpage` outputs (only reads `mat_sn_*` and `web-search` subdirs). No impact.
- `_MAX_CONTENT_LENGTH = 50_000` truncation: unchanged, applies to markdown text equally.

### P1-b: Per-Workspace Disk Cache

**New class** in `webpage.py`:

```python
class _WebpageDiskCache:
    """Workspace-scoped disk cache for fetched web pages."""

    TTL = 900              # 15 minutes
    MAX_ENTRIES = 200      # ~10MB at 50KB avg per entry

    def __init__(self, cache_dir: Path):
        self._dir = cache_dir

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def get(self, url: str) -> str | None:
        """Return cached content if exists and not expired, else None."""

    def put(self, url: str, content: str) -> None:
        """Write cache entry via atomic temp-file rename. Evict oldest if over MAX_ENTRIES.
        Eviction uses a threading.Lock to prevent races in ThreadPoolExecutor."""
```

**Cache file format**: `{workspace}/_tmp/web_cache/{url_hash}.json`

```json
{
  "url": "https://example.com/paper",
  "content": "# Paper Title\n\n...",
  "fetched_at": 1711353600.0
}
```

**Integration in `ExtractWebpageTool`**:

```python
class ExtractWebpageTool(BaseTool):
    def __init__(self, cache_dir: Path | None = None):
        super().__init__()
        self._domain_circuit = _DomainCircuitState()
        self._cache = _WebpageDiskCache(cache_dir) if cache_dir else None
```

In `_process(url)`:
1. Check `self._cache.get(url)` first — hit returns immediately, no HTTP request
2. On successful fetch, call `self._cache.put(url, content)`
3. Cache hits do not count toward domain circuit breaker failures

**Factory function signature change**:

```python
def get_extract_webpage_tool(cache_dir: Path | None = None) -> ExtractWebpageTool:
    return ExtractWebpageTool(cache_dir=cache_dir)
```

**Registration in `playground.py`**:

```python
# Current
registry.register(get_extract_webpage_tool())

# New
cache_dir = Path(self.run_dir) / '_tmp' / 'web_cache' if self.run_dir else None
registry.register(get_extract_webpage_tool(cache_dir=cache_dir))
```

**Key decisions**:
- TTL 15 minutes: matches Claude Code; sufficient to cover multi-round deep-survey without re-fetching
- MAX_ENTRIES 200: caps disk usage at ~10MB; evicts oldest entries on overflow
- Only successful fetches are cached; errors are not cached (allows retry)
- Cache directory lives inside `_tmp/` which is already gitignored and treated as ephemeral

## Files Modified

| File | Change |
|------|--------|
| `playground/mat_master/tools/webpage.py` | P0 noise filter + P1-a markdownify + P1-b disk cache class + integration |
| `playground/mat_master/core/playground.py` | Pass `cache_dir` to `get_extract_webpage_tool()` |
| `pyproject.toml` | Add `markdownify` dependency |

## Files NOT Modified (verified compatible)

| File | Why compatible |
|------|---------------|
| `playground/mat_master/core/agent_tool_observation.py` | Reads `content` as opaque string |
| `playground/mat_master/core/agent_tool_execution.py` | Calls compaction functions, no format assumption |
| `playground/mat_master/core/tool_guard.py` | Checks key name prefix only |
| `playground/mat_master/skills/deep-survey/scripts/collect_evidence.py` | Text matching on content field |
| `playground/mat_master/skills/structure-manager/scripts/fetch_web_structure.py` | Only imports `BROWSER_HEADERS` constant |
| `playground/mat_master/tools/__init__.py` | Re-exports unchanged names |

## Risks

| Risk | Mitigation |
|------|-----------|
| `markdownify` output differs from raw text, breaking downstream expectations | Verified all consumers treat content as opaque string; added graceful fallback |
| Noise pattern false positive removes article content | Conservative pattern (only `cookie\|banner\|sidebar\|menu`); no class patterns like `ad` or `promo` |
| Disk cache corruption (e.g. partial write) | JSON write to temp file + atomic rename; malformed cache entries treated as miss |
| Cache directory not writable | Cache is optional (`None` if no `run_dir`); errors logged, never raised |
| Concurrent eviction race in ThreadPoolExecutor | Eviction guarded by `threading.Lock`; concurrent `put()` to different files is safe (different paths) |
| `markdownify` strips inline SVG formulas | Acceptable trade-off: LLM cannot process SVG; formula info usually available as text elsewhere on the page |

## Non-Goals

- LLM pre-summarization (P3 from original analysis) — deferred
- Smart truncation based on Markdown structure (P2) — deferred, depends on P1-a
- Cross-session / cross-workspace cache sharing
- Trusted site fast-path (a la Claude Code)
