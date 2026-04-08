"""matmaster/tools/builtin/checkcif_tool.py

CheckCIF builtin tool — submits a CIF file to the IUCr checkCIF web service
and returns the alert summary plus the full HTML report text.

The Agent can use this tool to validate a refined CIF before reporting results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult

logger = logging.getLogger(__name__)


class CheckCIFTool(BuiltinTool):
    """Submit a CIF file to the IUCr checkCIF web service.

    Sends the CIF to https://checkcif.iucr.org/cgi-bin/checkcif_hkl.pl and
    returns the alert summary (A/B/C/G counts) together with the full HTML
    report text so the Agent can inspect individual alerts.

    The tool reads the CIF from the local filesystem (relative to the Agent
    workspace) and POSTs it to the IUCr endpoint.  A network connection is
    required; the request may take up to 3 minutes for large structures.
    """

    name: ClassVar[str] = 'checkcif'
    description: ClassVar[str] = (
        'Submit a CIF file to the IUCr checkCIF/PLATON web service and return '
        'the alert summary (A/B/C/G alert counts) and the full HTML report. '
        'Use this after SHELXL refinement to validate the structure before '
        'reporting R-factors. A-level alerts indicate serious problems that '
        'must be resolved or explained.'
    )
    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'cif_path': {
                'type': 'string',
                'description': (
                    'Path to the CIF file to validate. '
                    'Can be absolute or relative to the current working directory.'
                ),
            },
            'timeout': {
                'type': 'number',
                'description': (
                    'HTTP request timeout in seconds (default: 180). '
                    'Large CIFs with embedded structure factors may take longer.'
                ),
                'default': 180,
            },
        },
        'required': ['cif_path'],
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        cif_path_str = (arguments.get('cif_path') or '').strip()
        if not cif_path_str:
            return ToolResult(error='cif_path is required')

        timeout = float(arguments.get('timeout') or 180)

        cif_path = Path(cif_path_str)
        if not cif_path.is_absolute():
            # Try relative to workdir if set
            workdir = getattr(self, '_workdir', None)
            if workdir is not None:
                cif_path = Path(workdir) / cif_path

        if not cif_path.exists():
            return ToolResult(error=f'CIF file not found: {cif_path}')

        try:
            cif_content = cif_path.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            return ToolResult(error=f'Failed to read CIF file: {e}')

        # Import here to avoid hard dependency at module load time
        try:
            from evaluation.validators.checkcif import run_checkcif
        except ImportError:
            # Fallback: inline the HTTP call so the tool works without the
            # evaluation package installed
            return self._run_checkcif_inline(cif_content, cif_path.name, timeout)

        result = run_checkcif(cif_content, cif_filename=cif_path.name, timeout=timeout)

        if not result.success:
            return ToolResult(
                error=f'checkCIF request failed: {result.error}',
            )

        summary = (
            f'checkCIF alert summary for {cif_path.name}:\n'
            f'  A-alerts (serious):  {result.a_alerts}\n'
            f'  B-alerts (moderate): {result.b_alerts}\n'
            f'  C-alerts (check):    {result.c_alerts}\n'
            f'  G-alerts (info):     {result.g_alerts}\n'
        )
        if result.a_alerts == 0:
            summary += '\nNo A-level alerts — structure passes basic validation.\n'
        else:
            summary += (
                f'\n{result.a_alerts} A-level alert(s) found — '
                'these must be resolved or explained before publication.\n'
            )

        # Append the full HTML report (truncated to 20 KB to avoid token overflow)
        max_report_chars = 20_000
        report_text = result.raw_text
        if len(report_text) > max_report_chars:
            report_text = (
                report_text[:max_report_chars] + '\n... [report truncated] ...'
            )

        full_output = summary + '\n--- Full checkCIF Report ---\n' + report_text
        return ToolResult(output=full_output)

    def _run_checkcif_inline(
        self, cif_content: str, cif_filename: str, timeout: float
    ) -> ToolResult:
        """Inline HTTP call used when evaluation package is not available."""
        import re

        try:
            import requests
        except ImportError:
            return ToolResult(error='requests library not available')

        _CHECKCIF_URL = 'https://checkcif.iucr.org/cgi-bin/checkcif_hkl.pl'
        _ALERT_RE = re.compile(
            r'(\d+)<font[^>]*><b>\s*ALERT\s+level\s+([ABCG])\s*</b>',
            re.IGNORECASE,
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
                data={'runtype': 'symmonly', 'outputtype': 'HTML'},
                timeout=timeout,
                headers={'User-Agent': 'matmaster-eval/1.0'},
            )
            resp.raise_for_status()
        except Exception as e:
            return ToolResult(error=f'checkCIF HTTP request failed: {e}')

        raw = resp.text
        counts: dict[str, int] = {'A': 0, 'B': 0, 'C': 0, 'G': 0}
        for m in _ALERT_RE.finditer(raw):
            level = m.group(2).upper()
            if level in counts:
                counts[level] = int(m.group(1))

        summary = (
            f'checkCIF alert summary for {cif_filename}:\n'
            f'  A-alerts (serious):  {counts["A"]}\n'
            f'  B-alerts (moderate): {counts["B"]}\n'
            f'  C-alerts (check):    {counts["C"]}\n'
            f'  G-alerts (info):     {counts["G"]}\n'
        )
        max_report_chars = 20_000
        report_text = raw[:max_report_chars] if len(raw) > max_report_chars else raw
        return ToolResult(
            output=summary + '\n--- Full checkCIF Report ---\n' + report_text
        )
