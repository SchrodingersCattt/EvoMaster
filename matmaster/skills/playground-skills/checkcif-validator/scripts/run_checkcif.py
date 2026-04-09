"""
Submit a CIF file to the IUCr checkCIF/PLATON web service and return the
alert summary plus the full HTML report.

Usage:
  python run_checkcif.py --file structure.cif [--timeout 180]

Output: JSON to stdout.
  Success: {"success": true, "file": "...", "a_alerts": N, "b_alerts": N,
            "c_alerts": N, "g_alerts": N, "summary": "A=N B=N C=N G=N",
            "report": "... HTML (truncated to 20 KB) ..."}
  Failure: {"success": false, "file": "...", "error": "..."}

Requires: requests
"""

import argparse
import json
import re
import sys
from pathlib import Path

_CHECKCIF_URL = 'https://checkcif.iucr.org/cgi-bin/checkcif_hkl.pl'

# Matches lines like:
#    0<font color="red"><b> ALERT level A</b></font> = ...
#   12<font color="#555555"><b> ALERT level G</b></font> = ...
_ALERT_RE = re.compile(
    r'(\d+)<font[^>]*><b>\s*ALERT\s+level\s+([ABCG])\s*</b>',
    re.IGNORECASE,
)

_MAX_REPORT_CHARS = 20_000


def run_checkcif(
    cif_content: str,
    cif_filename: str,
    timeout: float = 180.0,
) -> dict:
    """POST CIF to IUCr checkCIF and return parsed result dict."""
    try:
        import requests
    except ImportError:
        return {
            'success': False,
            'file': cif_filename,
            'error': 'requests library not available; install with: pip install requests',
        }

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
            headers={'User-Agent': 'matmaster-checkcif-skill/1.0'},
        )
        resp.raise_for_status()
    except Exception as exc:
        return {
            'success': False,
            'file': cif_filename,
            'error': f'HTTP request failed: {exc}',
        }

    raw = resp.text
    counts: dict[str, int] = {'A': 0, 'B': 0, 'C': 0, 'G': 0}
    for m in _ALERT_RE.finditer(raw):
        level = m.group(2).upper()
        if level in counts:
            counts[level] = int(m.group(1))

    report = (
        raw
        if len(raw) <= _MAX_REPORT_CHARS
        else raw[:_MAX_REPORT_CHARS] + '\n... [report truncated] ...'
    )

    return {
        'success': True,
        'file': cif_filename,
        'a_alerts': counts['A'],
        'b_alerts': counts['B'],
        'c_alerts': counts['C'],
        'g_alerts': counts['G'],
        'summary': f'A={counts["A"]} B={counts["B"]} C={counts["C"]} G={counts["G"]}',
        'report': report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Submit a CIF file to IUCr checkCIF and return alert summary.'
    )
    parser.add_argument(
        '--file', required=True, help='Path to the CIF file to validate.'
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=180.0,
        help='HTTP request timeout in seconds (default: 180).',
    )
    args = parser.parse_args()

    cif_path = Path(args.file)
    if not cif_path.exists():
        result = {
            'success': False,
            'file': args.file,
            'error': f'CIF file not found: {cif_path}',
        }
        print(json.dumps(result))
        sys.exit(1)

    try:
        cif_content = cif_path.read_text(encoding='utf-8', errors='replace')
    except Exception as exc:
        result = {
            'success': False,
            'file': args.file,
            'error': f'Failed to read CIF file: {exc}',
        }
        print(json.dumps(result))
        sys.exit(1)

    result = run_checkcif(cif_content, cif_path.name, timeout=args.timeout)
    print(json.dumps(result))
    if not result['success']:
        sys.exit(1)


if __name__ == '__main__':
    main()
