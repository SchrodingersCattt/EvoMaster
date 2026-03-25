"""Tests for webpage.py: noise filtering, markdownify conversion, post-cleaning."""

import json
import re
import time

import pytest

from playground.mat_master.tools.webpage import _fetch_webpage_content


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
    return _fetch_webpage_content('https://example.com/test')


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
        """<header> must NOT be removed."""
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


class TestMarkdownConversion:
    """P1-a: HTML to Markdown via markdownify with fallback."""

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
        """If markdownify raises, fall back to get_text()."""
        import markdownify as md
        original = md.markdownify
        monkeypatch.setattr(md, 'markdownify', lambda *a, **kw: (_ for _ in ()).throw(ValueError('bad')))
        html = '<html><body><h1>Title</h1><p>Content</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Title' in result
        assert 'Content' in result
        monkeypatch.setattr(md, 'markdownify', original)


class TestPostCleaning:
    """Post-cleaning: conditional whitespace collapse + control char filter."""

    def test_markdown_preserves_newlines(self, monkeypatch):
        html = '<html><body><h1>Title</h1><p>Paragraph</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert '\n' in result

    def test_cjk_content_preserved(self, monkeypatch):
        html = '<html><body><p>氧化锌的晶体结构研究</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert '氧化锌' in result

    def test_greek_letters_preserved(self, monkeypatch):
        html = '<html><body><p>The α-phase and β-phase of ZnO</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'α' in result
        assert 'β' in result

    def test_control_chars_removed(self, monkeypatch):
        html = '<html><body><p>Clean\x00text\x01here\x02end</p></body></html>'
        result = _fetch_html(html, monkeypatch)
        assert 'Cleantexthere' in result or 'Clean text here' in result
        assert '\x00' not in result
        assert '\x01' not in result
