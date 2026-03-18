"""Built-in tool: fetch and extract text content from web pages.

Replaces the remote mat_doc MCP `extract_info_from_webpage` tool with a
local implementation so that webpage fetching never depends on an external
server connection.
"""

import json
import logging
import re
import time as _time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import Field

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

from evomaster.agent.tools.base import BaseTool, BaseToolParams

_MAX_CONTENT_LENGTH = 50_000

# Browser-like headers to reduce 403/anti-bot; exported for reuse (e.g. structure-manager).
BROWSER_HEADERS: dict[str, str] = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
}

# Alternate UA for single retry on 403/429 (no extra retries, no proxy).
ALTERNATE_UA_HEADERS: dict[str, str] = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) '
        'Gecko/20100101 Firefox/121.0'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
}

logger = logging.getLogger(__name__)

# 防 403/反爬：同域请求间隔(秒)、同域并发数、域名熔断阈值；写死在代码中，不读 config。
REQUEST_DELAY_SECONDS = 0.5
MAX_CONCURRENT_PER_DOMAIN = 1
_DEFAULT_DOMAIN_FAILURE_THRESHOLD = 3


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return (parsed.hostname or '').strip().lower()
    except Exception:
        return ''


@dataclass
class _DomainCircuitState:
    """Run-scoped circuit breaker state for webpage domains."""

    failure_threshold: int = _DEFAULT_DOMAIN_FAILURE_THRESHOLD
    failures: dict[str, int] = field(default_factory=dict)
    open_circuits: dict[str, str] = field(default_factory=dict)

    def is_open(self, domain: str) -> bool:
        return bool(domain) and domain in self.open_circuits

    def record_failure(self, domain: str, reason: str) -> tuple[bool, int]:
        if not domain:
            return False, 0
        n = int(self.failures.get(domain, 0)) + 1
        self.failures[domain] = n
        if n >= int(self.failure_threshold):
            self.open_circuits.setdefault(domain, reason or 'unknown')
            return True, n
        return False, n

    def summary(self) -> dict[str, Any]:
        return {
            'failure_threshold': int(self.failure_threshold),
            'failures': dict(self.failures),
            'open_circuits': dict(self.open_circuits),
        }


def _fetch_webpage_content(
    url: str,
    *,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """Fetch and extract plain text from a URL.

    Handles HTML pages (via BeautifulSoup) and PDF responses (via PyMuPDF).
    Output is cleaned and truncated to `_MAX_CONTENT_LENGTH` characters.
    Uses a Session per call if not provided (reuses TCP/cookies for redirects).
    On 403/429, retries once after 1.5s with alternate User-Agent (Firefox).
    """
    logger.info('Fetching content from URL: %s', url)
    hdrs = headers or BROWSER_HEADERS

    def _do_get(h: dict[str, str]) -> requests.Response:
        if session is not None:
            return session.get(url, headers=h, timeout=15)
        with requests.Session() as sess:
            return sess.get(url, headers=h, timeout=15)

    response = _do_get(hdrs)
    if response.status_code in (403, 429):
        logger.warning(
            'Got %s for %s; retrying once with alternate UA.', response.status_code, url
        )
        _time.sleep(1.5)
        response = _do_get(ALTERNATE_UA_HEADERS)
    response.raise_for_status()

    content_type = response.headers.get('Content-Type', '').lower()
    is_pdf = 'application/pdf' in content_type or (
        'application/octet-stream' in content_type and url.lower().endswith('.pdf')
    )

    if is_pdf:
        if fitz is None:
            raise RuntimeError(
                'PyMuPDF (fitz) is not available; cannot extract PDF content.'
            )
        doc = fitz.open(stream=response.content, filetype='pdf')
        text = ''.join(page.get_text() for page in doc)
        doc.close()
        content = text
    else:
        raw = response.text
        if raw.strip().startswith('<'):
            try:
                soup = BeautifulSoup(raw, 'lxml')
            except Exception:
                soup = BeautifulSoup(raw, 'html.parser')
            for tag in soup(['script', 'style']):
                tag.decompose()
            lines = (line.strip() for line in soup.get_text().splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split('  '))
            content = ' '.join(chunk for chunk in chunks if chunk)
        else:
            content = raw

    content = re.sub(r'\s+', ' ', content)
    content = re.sub(r'[^\x20-\x7E\x0A\x0D]', '', content)
    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH]
        logger.warning('Webpage content truncated to %d chars', _MAX_CONTENT_LENGTH)
    return content


class ExtractWebpageToolParams(BaseToolParams):
    """Fetch and extract plain-text content from one or more web page URLs.

    Handles both HTML pages and URLs that return a PDF. Results are returned
    keyed by URL; all URLs are processed concurrently.
    """

    name: ClassVar[str] = 'extract_info_from_webpage'

    url: list[str] = Field(
        description='List of web page URLs to fetch and extract text from.',
    )


class ExtractWebpageTool(BaseTool):
    """Built-in tool: extract plain-text content from web pages."""

    name: ClassVar[str] = 'extract_info_from_webpage'
    params_class: ClassVar[type[BaseToolParams]] = ExtractWebpageToolParams

    def __init__(self) -> None:
        super().__init__()
        # Per-run state kept on the tool instance (no globals).
        self._domain_circuit = _DomainCircuitState()

    def execute(self, session: Any, args_json: str) -> tuple[str, dict]:
        try:
            params = self.parse_params(args_json)
            assert isinstance(params, ExtractWebpageToolParams)
            urls = params.url

            if not urls:
                result = {
                    'message': 'url list is empty',
                    'total_processing_time_seconds': 0.0,
                    'time_saving_json_seconds': 0.0,
                }
                return json.dumps(result), {'result': result}

            start = _time.time()
            results: dict[str, Any] = {}
            request_delay = REQUEST_DELAY_SECONDS

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
                except requests.HTTPError as exc:
                    status = None
                    try:
                        status = int(
                            getattr(getattr(exc, 'response', None), 'status_code', None)
                            or 0
                        )
                    except Exception:
                        status = None
                    domain = _extract_domain(u)
                    if status in (401, 403):
                        reason = 'forbidden'
                    elif status == 404:
                        reason = 'not_found'
                    elif status == 429:
                        reason = 'rate_limited'
                    else:
                        reason = 'http_error'
                    opened, count = self._domain_circuit.record_failure(domain, reason)
                    logger.warning(
                        'Web fetch failed url=%s status=%s domain=%s reason=%s count=%d opened=%s',
                        u,
                        status,
                        domain,
                        reason,
                        count,
                        opened,
                    )
                    return (
                        u,
                        None,
                        _time.time() - t0,
                        {
                            'error_class': 'HTTPError',
                            'http_status': status,
                            'domain': domain,
                            'reason': reason,
                            'domain_failure_count': count,
                            'domain_circuit_opened': opened,
                            'message': str(exc),
                        },
                    )
                except requests.Timeout as exc:
                    domain = _extract_domain(u)
                    opened, count = self._domain_circuit.record_failure(
                        domain, 'timeout'
                    )
                    logger.warning(
                        'Web fetch timeout url=%s domain=%s count=%d opened=%s',
                        u,
                        domain,
                        count,
                        opened,
                    )
                    return (
                        u,
                        None,
                        _time.time() - t0,
                        {
                            'error_class': 'Timeout',
                            'domain': domain,
                            'reason': 'timeout',
                            'domain_failure_count': count,
                            'domain_circuit_opened': opened,
                            'message': str(exc),
                        },
                    )
                except Exception as exc:
                    domain = _extract_domain(u)
                    opened, count = self._domain_circuit.record_failure(
                        domain, 'exception'
                    )
                    logger.error('Failed to fetch %s: %s', u, exc, exc_info=True)
                    return (
                        u,
                        None,
                        _time.time() - t0,
                        {
                            'error_class': type(exc).__name__,
                            'domain': domain,
                            'reason': 'exception',
                            'domain_failure_count': count,
                            'domain_circuit_opened': opened,
                            'message': str(exc),
                        },
                    )

            # Group by domain to limit concurrency per domain (reduce 403/429).
            by_domain: dict[str, list[str]] = defaultdict(list)
            for u in urls:
                by_domain[_extract_domain(u) or '__unknown__'].append(u)

            def run_domain_urls(
                domain_urls: list[str],
            ) -> list[tuple[str, Any, float, dict | None]]:
                out: list[tuple[str, Any, float, dict | None]] = []
                for u in domain_urls:
                    if request_delay > 0:
                        _time.sleep(request_delay)
                    out.append(_process(u))
                return out

            num_domains = len(by_domain)
            workers = min(max(1, num_domains), 32)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(run_domain_urls, domain_urls): domain_urls
                    for domain_urls in by_domain.values()
                }
                for future in as_completed(futures):
                    for u, content, elapsed, err in future.result():
                        if err is not None:
                            results[f"webpage_detailed_contents from {u}"] = {
                                'error': err,
                                'processing_time_seconds': round(elapsed, 3),
                            }
                        else:
                            results[f"webpage_detailed_contents from {u}"] = {
                                'content': content,
                                'processing_time_seconds': round(elapsed, 3),
                            }

            total = round(_time.time() - start, 3)
            results['total_processing_time_seconds'] = total
            results['time_saving_json_seconds'] = 0.0

            # Provide circuit summary and guidance to avoid infinite paywall retries.
            results['web_fetch_circuit'] = self._domain_circuit.summary()
            if self._domain_circuit.open_circuits:
                results['web_fetch_guidance'] = (
                    'Some domains are blocked (paywall/forbidden/not-found/rate-limit). '
                    'Do NOT keep retrying those domains; use alternative open sources '
                    '(e.g., arXiv/PMC/Crossref metadata) or proceed with caveats and finish.'
                )

            obs = json.dumps(results, ensure_ascii=False)
            return obs, {'result': results}
        except Exception as exc:
            self.logger.warning('extract_info_from_webpage failed: %s', exc)
            return f"Error: {exc}", {'error': str(exc)}


def get_extract_webpage_tool() -> ExtractWebpageTool:
    """Return a single ExtractWebpageTool instance for registration."""
    return ExtractWebpageTool()
