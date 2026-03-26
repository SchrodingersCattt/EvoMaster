# Webpage Tool Enhancement Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve `extract_info_from_webpage` tool with noise filtering, HTML→Markdown conversion, and per-workspace disk cache.

**Architecture:** Three incremental changes to `_fetch_webpage_content()` and `ExtractWebpageTool` in `webpage.py`. P0 (noise filter) and P1-a (markdownify) modify the HTML parsing branch. P1-b (disk cache) adds a new `_WebpageDiskCache` class and integrates it at the `_process()` level. Each change is independently testable and commits separately.

**Tech Stack:** Python 3.10+, BeautifulSoup4, markdownify, requests, hashlib, threading

**Spec:** `docs/superpowers/specs/2026-03-25-webpage-tool-enhancement-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `playground/mat_master/tools/webpage.py` | Modify | P0 noise filter + P1-a markdownify + P1-b disk cache class + integration |
| `playground/mat_master/core/playground.py` | Modify (line 123) | Pass workspace-scoped `cache_dir` to factory |
| `pyproject.toml` | Modify (after `pymupdf`, before `tiktoken`) | Add `markdownify` dependency |
| `tests/playground/mat_master/tools/test_webpage.py` | Create | All tests for P0, P1-a, P1-b |

---

## Chunk 1: P0 Noise Tag Filtering + P1-a HTML→Markdown

### Task 1: Add `markdownify` dependency

**Files:**
- Modify: `pyproject.toml:34`

- [ ] **Step 1: Add markdownify to dependencies**

In `pyproject.toml`, after `"pymupdf",` (line 32), add:

```python
    "markdownify>=0.14.1",
```

- [ ] **Step 2: Install**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv sync`
Expected: resolves and installs markdownify

- [ ] **Step 3: Verify import**

Run: `uv run python -c "import markdownify; print(markdownify.__version__)"`
Expected: prints version number

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add markdownify for HTML-to-Markdown conversion"
```

---

### Task 2: Create test file with P0 + P1-a tests

**Files:**
- Create: `tests/playground/mat_master/tools/test_webpage.py`

- [ ] **Step 1: Create test directory structure**

Run: `mkdir -p tests/playground/mat_master/tools && touch tests/playground/mat_master/tools/__init__.py`

Also ensure parent `__init__.py` files exist:

Run: `touch tests/playground/__init__.py tests/playground/mat_master/__init__.py`

- [ ] **Step 2: Write P0 + P1-a tests**

Create `tests/playground/mat_master/tools/test_webpage.py`:

```python
"""Tests for webpage.py: noise filtering, markdownify conversion, post-cleaning."""

import json
import re
import time

import pytest

from playground.mat_master.tools.webpage import _fetch_webpage_content


# ---------------------------------------------------------------------------
# Helper: feed raw HTML into the parsing branch of _fetch_webpage_content
# without making an HTTP request.  We monkeypatch requests.Session.get to
# return a fake Response.
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal requests.Response stub for testing HTML parsing."""

    def __init__(self, text: str, content_type: str = 'text/html', status_code: int = 200):
        self.text = text
        self.content = text.encode()
        self.status_code = status_code
        self.headers = {'Content-Type': content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f'HTTP {self.status_code}')


def _fetch_html(html: str, monkeypatch) -> str:
    """Helper: run _fetch_webpage_content with fake HTTP returning *html*."""
    fake = _FakeResponse(html)
    monkeypatch.setattr(
        'playground.mat_master.tools.webpage.requests.Session',
        lambda: type('S', (), {'get': lambda self, *a, **kw: fake, '__enter__': lambda s: s, '__exit__': lambda *a: None})(),
    )
    # _fetch_webpage_content creates a new Session internally when session=None
    return _fetch_webpage_content('https://example.com/test')


# ---------------------------------------------------------------------------
# P0: Noise tag filtering
# ---------------------------------------------------------------------------

class TestNoiseFiltering:
    """P0: structural tags and class/id noise patterns."""

    def test_removes_nav_footer_aside(self, monkeypatch):
        html = '<html><body><nav>Navigation</nav><article>Content</article><footer>Footer</footer><aside>Sidebar</aside></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Content' in result
        assert 'Navigation' not in result
        assert 'Footer' not in result
        assert 'Sidebar' not in result

    def test_preserves_header_tag(self, monkeypatch):
        """<header> must NOT be removed — article title/abstract often lives here."""
        html = '<html><body><header><h1>Crystal Structure of ZnO</h1><p>Abstract: important text</p></header><nav>Home</nav></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Crystal Structure of ZnO' in result
        assert 'important text' in result
        assert 'Home' not in result

    def test_removes_cookie_banner_class(self, monkeypatch):
        html = '<html><body><div class="cookie-consent">Accept cookies</div><p>Real content</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Real content' in result
        assert 'Accept cookies' not in result

    def test_removes_sidebar_id(self, monkeypatch):
        html = '<html><body><div id="sidebar-nav">Links</div><p>Article</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Article' in result
        assert 'Links' not in result

    def test_removes_noscript_iframe(self, monkeypatch):
        html = '<html><body><noscript>Enable JS</noscript><iframe src="ad.html"></iframe><p>Body</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Body' in result
        assert 'Enable JS' not in result


# ---------------------------------------------------------------------------
# P1-a: Markdownify conversion
# ---------------------------------------------------------------------------

class TestMarkdownConversion:
    """P1-a: HTML → Markdown via markdownify with fallback."""

    def test_headings_preserved_as_atx(self, monkeypatch):
        html = '<html><body><h1>Title</h1><h2>Section</h2><p>Paragraph</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert '# Title' in result
        assert '## Section' in result
        assert 'Paragraph' in result

    def test_links_preserved(self, monkeypatch):
        html = '<html><body><p>See <a href="https://example.com">this link</a></p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert '[this link]' in result
        assert 'https://example.com' in result

    def test_lists_preserved(self, monkeypatch):
        html = '<html><body><ul><li>Item A</li><li>Item B</li></ul></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Item A' in result
        assert 'Item B' in result

    def test_images_stripped(self, monkeypatch):
        html = '<html><body><p>Text</p><img src="photo.jpg" alt="Photo"/></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Text' in result
        assert 'photo.jpg' not in result

    def test_markdownify_failure_falls_back_to_plain_text(self, monkeypatch):
        """If markdownify raises, fall back to get_text() without propagating."""
        import markdownify as md

        original = md.markdownify
        monkeypatch.setattr(md, 'markdownify', lambda *a, **kw: (_ for _ in ()).throw(ValueError('bad')))
        html = '<html><body><h1>Title</h1><p>Content</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        # Should still contain the text (plain text fallback)
        assert 'Title' in result
        assert 'Content' in result
        # Restore
        monkeypatch.setattr(md, 'markdownify', original)


# ---------------------------------------------------------------------------
# Post-cleaning: whitespace + control chars
# ---------------------------------------------------------------------------

class TestPostCleaning:
    """Post-cleaning: conditional whitespace collapse + control char filter."""

    def test_markdown_preserves_newlines(self, monkeypatch):
        """Markdown path must NOT collapse newlines into spaces."""
        html = '<html><body><h1>Title</h1><p>Paragraph</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert '\n' in result  # newlines preserved

    def test_cjk_content_preserved(self, monkeypatch):
        """Chinese/Japanese/Korean characters must NOT be stripped."""
        html = '<html><body><p>氧化锌的晶体结构研究</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert '氧化锌' in result

    def test_greek_letters_preserved(self, monkeypatch):
        """Greek letters (alpha, beta, gamma) must NOT be stripped."""
        html = '<html><body><p>The α-phase and β-phase of ZnO</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'α' in result
        assert 'β' in result

    def test_control_chars_removed(self, monkeypatch):
        """C0 control characters (except tab, LF, CR) must be removed."""
        html = '<html><body><p>Clean\x00text\x01here\x02end</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Cleantexthere' in result or 'Clean text here' in result
        assert '\x00' not in result
        assert '\x01' not in result
```

- [ ] **Step 3: Run tests — expect failures (TDD red phase)**

Run: `uv run pytest tests/playground/mat_master/tools/test_webpage.py -v`
Expected: Most tests FAIL (noise tags not filtered, no markdown output, CJK stripped)

- [ ] **Step 4: Commit test file**

```bash
git add tests/playground/mat_master/tools/
git commit -m "test: add failing tests for webpage P0 noise filter + P1-a markdown"
```

---

### Task 3: Implement P0 noise filter + P1-a markdownify + post-cleaning fix

**Files:**
- Modify: `playground/mat_master/tools/webpage.py:29,146-160`

- [ ] **Step 1: Add module-level noise pattern constant**

After `_DEFAULT_DOMAIN_FAILURE_THRESHOLD = 3` (line 59), add:

```python
# P0: class/id patterns for noise elements (cookie banners, sidebars, menus).
# Intentionally conservative — avoids generic words like 'ad', 'promo', 'popup'.
_NOISE_PATTERN = re.compile(r'cookie|banner|sidebar|menu', re.I)
```

- [ ] **Step 2: Replace HTML parsing branch (lines 146-157)**

Replace lines 134-157: insert `_used_markdownify = False` right after `is_pdf = ...` (before `if is_pdf:`), then replace the HTML branch.

The variable must be visible to both PDF and HTML paths so the post-cleaning in Step 3 can reference it without `NameError`.

```python
    # Old lines 134-157 become:

    _used_markdownify = False  # Must be before if/else so post-cleaning can see it

    if is_pdf:
        if fitz is None:
            raise RuntimeError(
                'PyMuPDF (fitz) is not available; cannot extract PDF content.'
            )
        doc = fitz.open(stream=response.content, filetype='pdf')
        text = ''.join(page.get_text() for page in doc)
        doc.close()
        content = text
    elif raw.strip().startswith('<'):
            try:
                soup = BeautifulSoup(raw, 'lxml')
            except Exception:
                soup = BeautifulSoup(raw, 'html.parser')

            # P0: noise tag removal — structural tags
            # NOTE: <header> intentionally excluded (wraps article title/abstract on publisher sites)
            for tag in soup(['script', 'style', 'nav', 'footer',
                              'aside', 'noscript', 'iframe']):
                tag.decompose()
            # P0: noise class/id removal
            for tag in soup.find_all(attrs={'class': _NOISE_PATTERN}):
                tag.decompose()
            for tag in soup.find_all(attrs={'id': _NOISE_PATTERN}):
                tag.decompose()

            # P1-a: HTML → Markdown (with plain-text fallback)
            try:
                import markdownify as _md
                content = _md.markdownify(
                    str(soup),
                    heading_style="ATX",
                    strip=['img', 'svg'],
                )
                content = re.sub(r'\n{3,}', '\n\n', content)
                _used_markdownify = True
            except Exception as exc:
                if isinstance(exc, ImportError):
                    logger.warning('markdownify not available; falling back to plain text')
                else:
                    logger.warning('markdownify conversion failed, falling back to plain text: %s', exc)
                lines = (line.strip() for line in soup.get_text().splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split('  '))
                content = ' '.join(chunk for chunk in chunks if chunk)
        else:
            content = raw
```

- [ ] **Step 3: Replace post-cleaning (lines 159-160)**

Replace:

```python
    content = re.sub(r'\s+', ' ', content)
    content = re.sub(r'[^\x20-\x7E\x0A\x0D]', '', content)
```

With:

```python
    # Post-cleaning: conditional whitespace + universal control-char filter
    if not _used_markdownify:
        # Plain-text / PDF path: collapse whitespace (markdown path handled above)
        content = re.sub(r'\s+', ' ', content)
    # Both paths: strip C0 control chars, preserve tab/LF/CR and all Unicode (CJK, Greek, etc.)
    content = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', content)
```

Note: `_used_markdownify` was already initialized before the `if is_pdf:` block in Step 2, so it is `False` for both PDF and non-HTML paths. Only the HTML+markdownify success path sets it to `True`.

- [ ] **Step 4: Run tests — expect all P0 + P1-a tests pass**

Run: `uv run pytest tests/playground/mat_master/tools/test_webpage.py -v`
Expected: All tests in `TestNoiseFiltering`, `TestMarkdownConversion`, `TestPostCleaning` PASS

- [ ] **Step 5: Commit**

```bash
git add playground/mat_master/tools/webpage.py
git commit -m "feat: P0 noise filter + P1-a markdownify + fix CJK post-cleaning"
```

---

## Chunk 2: P1-b Disk Cache

### Task 4: Write disk cache tests

**Files:**
- Modify: `tests/playground/mat_master/tools/test_webpage.py`

- [ ] **Step 1: Append cache tests to test file**

Add to `tests/playground/mat_master/tools/test_webpage.py`. Note: `json` and `time` are already imported at the top of the file (added in Task 2). Only add the new imports:

```python
from playground.mat_master.tools.webpage import (
    ExtractWebpageTool,
    _WebpageDiskCache,
)


# ---------------------------------------------------------------------------
# P1-b: Disk cache
# ---------------------------------------------------------------------------

class TestWebpageDiskCache:
    """P1-b: _WebpageDiskCache unit tests."""

    def test_put_and_get(self, tmp_path):
        cache = _WebpageDiskCache(tmp_path / 'cache')
        cache.put('https://example.com', '# Hello')
        assert cache.get('https://example.com') == '# Hello'

    def test_miss_returns_none(self, tmp_path):
        cache = _WebpageDiskCache(tmp_path / 'cache')
        assert cache.get('https://never-stored.com') is None

    def test_expired_entry_returns_none(self, tmp_path):
        cache = _WebpageDiskCache(tmp_path / 'cache')
        cache.put('https://example.com', 'old content')
        # Manually backdate the fetched_at timestamp
        key = cache._key('https://example.com')
        path = cache._dir / f'{key}.json'
        data = json.loads(path.read_text())
        data['fetched_at'] = time.time() - cache.TTL - 10
        path.write_text(json.dumps(data))
        assert cache.get('https://example.com') is None

    def test_eviction_removes_oldest(self, tmp_path):
        cache = _WebpageDiskCache(tmp_path / 'cache')
        cache.MAX_ENTRIES = 3  # Small limit for testing
        # Fill cache
        for i in range(3):
            cache.put(f'https://example.com/{i}', f'content_{i}')
            time.sleep(0.01)  # Ensure distinct fetched_at
        # Add one more — should evict the oldest (i=0)
        cache.put('https://example.com/new', 'new_content')
        assert cache.get('https://example.com/0') is None
        assert cache.get('https://example.com/1') is not None
        assert cache.get('https://example.com/new') == 'new_content'

    def test_malformed_cache_file_treated_as_miss(self, tmp_path):
        cache = _WebpageDiskCache(tmp_path / 'cache')
        cache.put('https://example.com', 'content')
        # Corrupt the file
        key = cache._key('https://example.com')
        path = cache._dir / f'{key}.json'
        path.write_text('NOT VALID JSON{{{')
        assert cache.get('https://example.com') is None

    def test_cache_isolation_between_workspaces(self, tmp_path):
        """Two tasks under the same run_dir must NOT share cache."""
        cache_a = _WebpageDiskCache(tmp_path / 'ws_a' / '_tmp' / 'web_cache')
        cache_b = _WebpageDiskCache(tmp_path / 'ws_b' / '_tmp' / 'web_cache')
        cache_a.put('https://example.com', 'content_a')
        assert cache_b.get('https://example.com') is None


class TestCacheIntegration:
    """P1-b: cache integration in ExtractWebpageTool."""

    def test_tool_with_no_cache_dir(self):
        """Tool instantiated without cache_dir should work (cache=None)."""
        tool = ExtractWebpageTool(cache_dir=None)
        assert tool._cache is None

    def test_tool_with_cache_dir(self, tmp_path):
        """Tool instantiated with cache_dir should have a cache."""
        tool = ExtractWebpageTool(cache_dir=tmp_path / 'cache')
        assert tool._cache is not None


class TestMarkdownifyCircuitBreaker:
    """Spec verification #3: markdownify exception must NOT trip circuit breaker."""

    def test_markdownify_exception_no_circuit_breaker(self, monkeypatch):
        """If markdownify raises during conversion, fall back to plain text
        without incrementing domain circuit breaker failure count."""
        from unittest.mock import Mock

        import markdownify as md

        monkeypatch.setattr(md, 'markdownify', Mock(side_effect=ValueError('bad HTML')))

        # Mock HTTP to return valid HTML
        fake = _FakeResponse('<html><body><p>Hello World</p></body></html>')
        monkeypatch.setattr(
            'playground.mat_master.tools.webpage.requests.Session',
            lambda: type('S', (), {
                'get': lambda self, *a, **kw: fake,
                '__enter__': lambda s: s,
                '__exit__': lambda *a: None,
            })(),
        )

        tool = ExtractWebpageTool()
        result, info = tool.execute(
            session=Mock(), args_json='{"url": ["https://example.com"]}'
        )
        parsed = json.loads(result)
        # Content should be present (plain text fallback worked)
        content_found = any(
            isinstance(v, dict) and 'content' in v
            for v in parsed.values()
        )
        assert content_found, f"Expected content in result, got: {parsed}"
        # Circuit breaker must NOT have been tripped
        assert len(tool._domain_circuit.open_circuits) == 0
        assert tool._domain_circuit.failures == {}
```

- [ ] **Step 2: Run tests — expect cache tests fail**

Run: `uv run pytest tests/playground/mat_master/tools/test_webpage.py::TestWebpageDiskCache -v`
Expected: FAIL (`_WebpageDiskCache` not defined, `ExtractWebpageTool.__init__` doesn't accept `cache_dir`)

- [ ] **Step 3: Commit**

```bash
git add tests/playground/mat_master/tools/test_webpage.py
git commit -m "test: add failing tests for P1-b webpage disk cache"
```

---

### Task 5: Implement `_WebpageDiskCache`

**Files:**
- Modify: `playground/mat_master/tools/webpage.py` (add class + imports)

- [ ] **Step 1: Add imports at top of webpage.py**

Insert after line 16 (`from urllib.parse import urlparse`), keeping stdlib imports grouped together. Note: `json`, `re`, `time` are already imported — do not duplicate.

```python
import hashlib
import tempfile
import threading
from pathlib import Path
```

- [ ] **Step 2: Add `_WebpageDiskCache` class**

Add after `_NOISE_PATTERN` constant (before `_fetch_webpage_content`):

```python
class _WebpageDiskCache:
    """Workspace-scoped disk cache for fetched web pages.

    Each cache entry is a JSON file at ``{cache_dir}/{url_hash}.json``.
    TTL-based expiry; oldest-first eviction when MAX_ENTRIES exceeded.
    Thread-safe eviction via ``_evict_lock``.
    """

    TTL: int = 900  # 15 minutes
    MAX_ENTRIES: int = 200

    def __init__(self, cache_dir: Path) -> None:
        self._dir = Path(cache_dir)
        self._evict_lock = threading.Lock()

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def get(self, url: str) -> str | None:
        """Return cached content if entry exists and is not expired, else None."""
        path = self._dir / f'{self._key(url)}.json'
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if _time.time() - data.get('fetched_at', 0) > self.TTL:
                return None
            return data.get('content')
        except Exception:
            # Malformed / corrupt file → treat as miss
            return None

    def put(self, url: str, content: str) -> None:
        """Write cache entry via atomic temp-file rename.

        Evicts oldest entries if cache exceeds MAX_ENTRIES.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        entry = {
            'url': url,
            'content': content,
            'fetched_at': _time.time(),
        }
        target = self._dir / f'{self._key(url)}.json'
        try:
            fd = tempfile.NamedTemporaryFile(
                mode='w',
                dir=str(self._dir),
                suffix='.tmp',
                delete=False,
                encoding='utf-8',
            )
            try:
                json.dump(entry, fd, ensure_ascii=False)
                fd.flush()
            finally:
                fd.close()
            Path(fd.name).replace(target)
        except Exception:
            logger.warning('Failed to write cache entry for %s', url, exc_info=True)
            return
        # Evict if over limit
        self._maybe_evict()

    def _maybe_evict(self) -> None:
        """Remove oldest entries if cache exceeds MAX_ENTRIES."""
        with self._evict_lock:
            try:
                entries = sorted(self._dir.glob('*.json'), key=lambda p: p.stat().st_mtime)
            except Exception:
                return
            excess = len(entries) - self.MAX_ENTRIES
            if excess <= 0:
                return
            for path in entries[:excess]:
                try:
                    path.unlink()
                except Exception:
                    pass
```

- [ ] **Step 3: Run cache unit tests**

Run: `uv run pytest tests/playground/mat_master/tools/test_webpage.py::TestWebpageDiskCache -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add playground/mat_master/tools/webpage.py
git commit -m "feat: add _WebpageDiskCache with TTL, eviction, atomic writes"
```

---

### Task 6: Integrate cache into ExtractWebpageTool + update playground.py

**Files:**
- Modify: `playground/mat_master/tools/webpage.py:187-191,210-235,378-381`
- Modify: `playground/mat_master/core/playground.py:117-123`

- [ ] **Step 1: Update `ExtractWebpageTool.__init__` to accept `cache_dir`**

Replace lines 187-190:

```python
    def __init__(self) -> None:
        super().__init__()
        # Per-run state kept on the tool instance (no globals).
        self._domain_circuit = _DomainCircuitState()
```

With:

```python
    def __init__(self, cache_dir: Path | None = None) -> None:
        super().__init__()
        self._domain_circuit = _DomainCircuitState()
        self._cache = _WebpageDiskCache(cache_dir) if cache_dir else None
```

- [ ] **Step 2: Add cache check in `_process()` (inside `execute()`)**

In the `_process(u)` closure (around line 210-235), add cache lookup right after the circuit-breaker check (after line 231) and cache write after successful fetch (after line 235).

Replace lines 210-235:

```python
            def _process(u: str):
                t0 = _time.time()
                try:
                    domain = _extract_domain(u)
                    if self._domain_circuit.is_open(domain):
                        reason = self._domain_circuit.open_circuits.get(
                            domain, 'blocked'
                        )
                        return (
                            u,
                            None,
                            _time.time() - t0,
                            {
                                'blocked': True,
                                'domain': domain,
                                'reason': reason,
                                'message': (
                                    'Domain circuit is open due to repeated failures; '
                                    'skip further fetches for this domain.'
                                ),
                            },
                        )
                    content = _fetch_webpage_content(
                        u
                    )  # uses BROWSER_HEADERS + Session
                    return u, content, _time.time() - t0, None
```

With:

```python
            def _process(u: str):
                t0 = _time.time()
                try:
                    domain = _extract_domain(u)
                    if self._domain_circuit.is_open(domain):
                        reason = self._domain_circuit.open_circuits.get(
                            domain, 'blocked'
                        )
                        return (
                            u,
                            None,
                            _time.time() - t0,
                            {
                                'blocked': True,
                                'domain': domain,
                                'reason': reason,
                                'message': (
                                    'Domain circuit is open due to repeated failures; '
                                    'skip further fetches for this domain.'
                                ),
                            },
                        )
                    # P1-b: check disk cache before HTTP fetch
                    if self._cache is not None:
                        cached = self._cache.get(u)
                        if cached is not None:
                            logger.info('Cache hit for %s', u)
                            return u, cached, _time.time() - t0, None
                    content = _fetch_webpage_content(u)
                    # P1-b: store successful fetch in cache
                    if self._cache is not None:
                        self._cache.put(u, content)
                    return u, content, _time.time() - t0, None
```

- [ ] **Step 3: Update factory function**

Replace lines 378-381:

```python
def get_extract_webpage_tool() -> ExtractWebpageTool:
    """Return a single ExtractWebpageTool instance for registration."""
    return ExtractWebpageTool()
```

With:

```python
def get_extract_webpage_tool(cache_dir: Path | None = None) -> ExtractWebpageTool:
    """Return an ExtractWebpageTool instance for registration."""
    return ExtractWebpageTool(cache_dir=cache_dir)
```

- [ ] **Step 4: Update playground.py to pass workspace-scoped cache_dir**

In `playground/mat_master/core/playground.py`, replace lines 117-123. Note: `Path` is already imported at line 12 (`from pathlib import Path`) — no additional import needed.

```python
        from ..tools import (
            get_aissq_download_tool,
            get_aissq_search_tool,
            get_extract_webpage_tool,
        )

        registry.register(get_extract_webpage_tool())
```

With:

```python
        from ..tools import (
            get_aissq_download_tool,
            get_aissq_search_tool,
            get_extract_webpage_tool,
        )

        # P1-b: derive workspace-scoped cache dir (not run_dir, to isolate batch tasks)
        _cache_dir = None
        if self.run_dir is not None:
            _run_path = Path(self.run_dir)
            if self.task_id:
                _ws = _run_path / 'workspaces' / self.task_id
            else:
                _ws = _run_path / 'workspace'
            _cache_dir = _ws / '_tmp' / 'web_cache'
        registry.register(get_extract_webpage_tool(cache_dir=_cache_dir))
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/playground/mat_master/tools/test_webpage.py -v`
Expected: ALL tests PASS (P0 + P1-a + P1-b)

- [ ] **Step 6: Verify import still works end-to-end**

Run: `uv run python -c "from playground.mat_master.tools.webpage import ExtractWebpageTool, get_extract_webpage_tool; t = get_extract_webpage_tool(); print(type(t), t._cache)"`
Expected: `<class '...ExtractWebpageTool'> None` (no cache when called without cache_dir)

Run: `uv run python -c "from pathlib import Path; from playground.mat_master.tools.webpage import get_extract_webpage_tool; t = get_extract_webpage_tool(cache_dir=Path('/tmp/test_cache')); print(type(t._cache))"`
Expected: `<class '...._WebpageDiskCache'>`

- [ ] **Step 7: Commit**

```bash
git add playground/mat_master/tools/webpage.py playground/mat_master/core/playground.py
git commit -m "feat: P1-b integrate disk cache into ExtractWebpageTool + playground"
```

---

## Chunk 3: Final Verification

### Task 7: Run full test suite + verify no regressions

**Files:** None (verification only)

- [ ] **Step 1: Run the full webpage test suite**

Run: `uv run pytest tests/playground/mat_master/tools/test_webpage.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Run existing test suite to check for regressions**

Run: `uv run pytest tests/ -x --tb=short -q`
Expected: No new failures

- [ ] **Step 3: Verify backward compatibility — old callers without cache_dir**

Run: `uv run python -c "from playground.mat_master.tools import get_extract_webpage_tool; t = get_extract_webpage_tool(); print('OK:', t.name, t._cache is None)"`
Expected: `OK: extract_info_from_webpage True`

- [ ] **Step 4: Verify BROWSER_HEADERS export unchanged**

Run: `uv run python -c "from playground.mat_master.tools.webpage import BROWSER_HEADERS; print('OK:', 'User-Agent' in BROWSER_HEADERS)"`
Expected: `OK: True`
