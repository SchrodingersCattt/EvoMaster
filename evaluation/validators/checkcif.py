"""evaluation/validators/checkcif.py

checkCIF/PLATON validation via the IUCr web service.

Provides:
  - CheckCIFResult dataclass
  - run_checkcif(cif_content) -> CheckCIFResult   (HTTP POST to IUCr)
  - check_checkcif_no_a_alerts(workspace_dir, ...)  (evaluation validator)

The IUCr checkCIF endpoint accepts a multipart/form-data POST with the CIF
file content and returns an HTML report.  We parse the summary lines:

    0<font color="red"><b> ALERT level A</b></font> = Most likely a serious problem
    0<font color="orange"><b> ALERT level B</b></font> = A potentially serious problem
    1<font color="#F7D358"><b> ALERT level C</b></font> = Check.
   12<font color="#555555"><b> ALERT level G</b></font> = General information

Endpoint: https://checkcif.iucr.org/cgi-bin/checkcif_hkl.pl
Form field: filecif (multipart file upload)
"""

from __future__ import annotations

import glob
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CHECKCIF_URL = 'https://checkcif.iucr.org/cgi-bin/checkcif_hkl.pl'

# Regex to extract alert counts from the HTML response.
# Matches lines like:
#    0<font color="red"><b> ALERT level A</b></font> = ...
#   12<font color="#555555"><b> ALERT level G</b></font> = ...
# The count appears immediately before the <font> tag (possibly with leading spaces).
_ALERT_RE = re.compile(
    r'(\d+)<font[^>]*><b>\s*ALERT\s+level\s+([ABCG])\s*</b>',
    re.IGNORECASE,
)


@dataclass
class CheckCIFResult:
    """Parsed result from the IUCr checkCIF service."""

    a_alerts: int = 0
    b_alerts: int = 0
    c_alerts: int = 0
    g_alerts: int = 0
    raw_text: str = field(default='', repr=False)
    success: bool = False
    error: str = ''

    @property
    def alert_summary(self) -> str:
        return (
            f'A={self.a_alerts} B={self.b_alerts} C={self.c_alerts} G={self.g_alerts}'
        )


def run_checkcif(
    cif_content: str,
    *,
    cif_filename: str = 'structure.cif',
    timeout: float = 180.0,
) -> CheckCIFResult:
    """POST CIF content to IUCr checkCIF and return parsed alert counts.

    Args:
        cif_content: Full text of the CIF file.
        cif_filename: Filename to use in the multipart upload (cosmetic).
        timeout: HTTP request timeout in seconds.

    Returns:
        CheckCIFResult with alert counts and raw response text.
    """
    try:
        import requests
    except ImportError as e:
        return CheckCIFResult(
            success=False,
            error=f'requests library not available: {e}',
        )

    try:
        resp = requests.post(
            _CHECKCIF_URL,
            files={
                'filecif': (
                    cif_filename,
                    cif_content.encode('utf-8'),
                    'application/octet-stream',
                ),
            },
            data={
                'runtype': 'symmonly',
                'outputtype': 'HTML',
            },
            timeout=timeout,
            headers={
                'User-Agent': 'matmaster-eval/1.0 (evaluation validator)',
            },
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning('checkCIF HTTP request failed: %s', e)
        return CheckCIFResult(success=False, error=str(e))

    raw = resp.text
    result = CheckCIFResult(success=True, raw_text=raw)

    for m in _ALERT_RE.finditer(raw):
        count = int(m.group(1))
        level = m.group(2).upper()
        if level == 'A':
            result.a_alerts = count
        elif level == 'B':
            result.b_alerts = count
        elif level == 'C':
            result.c_alerts = count
        elif level == 'G':
            result.g_alerts = count

    logger.debug('checkCIF result: %s', result.alert_summary)
    return result


# ---------------------------------------------------------------------------
# Evaluation validator
# ---------------------------------------------------------------------------


def check_checkcif_no_a_alerts(
    workspace_dir: str,
    *,
    filename: str = '*.cif',
    max_a_alerts: int = 0,
) -> tuple[bool, str]:
    """Find a CIF file in workspace_dir, run checkCIF, verify A-alert count.

    Args:
        workspace_dir: Agent workspace directory to search for CIF files.
        filename: Glob pattern to find the CIF file (default ``*.cif``).
        max_a_alerts: Maximum allowed A-level alerts (default 0).

    Returns:
        (passed, reason) tuple.
    """
    ws = Path(workspace_dir)
    if not ws.is_dir():
        return False, f'workspace_dir not found: {workspace_dir}'

    # Find CIF files matching the pattern
    pattern = str(ws / filename)
    matches = sorted(glob.glob(pattern))
    if not matches:
        return False, f'no CIF file matching {filename!r} found in {workspace_dir}'

    # Use the first (or only) match; prefer the most recently modified if multiple
    if len(matches) > 1:
        matches.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
        logger.debug('Multiple CIF files found, using most recent: %s', matches[0])

    cif_path = Path(matches[0])
    try:
        cif_content = cif_path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return False, f'failed to read CIF file {cif_path}: {e}'

    result = run_checkcif(cif_content, cif_filename=cif_path.name)

    if not result.success:
        return False, f'checkCIF request failed: {result.error}'

    passed = result.a_alerts <= max_a_alerts
    reason = (
        f'checkCIF {result.alert_summary} '
        f'(file={cif_path.name}, max_a_alerts={max_a_alerts})'
    )
    return passed, reason
