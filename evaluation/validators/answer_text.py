"""Deterministic verifiers operating on the agent's free-form answer text.

These checks complement ``llm_binary_judge`` for numerical criteria where an
LLM judge is overkill (and can be inconsistent). The agent emits a structured
JSON block in its answer; we parse it programmatically and apply
``target ± tolerance`` checks.

Why a JSON block instead of regex-from-prose:
- Regex over arbitrary prose is brittle: agents reformat tables/units
  unpredictably, and ``numerical_range`` (closest-number heuristic) is
  context-blind so it can hit a value that belongs to a different key.
- A fenced JSON block makes scoring fully deterministic and
  machine-auditable, while still letting the agent write a normal prose
  explanation around it.

Block discovery (in order):
1. ``<eval_results>...</eval_results>`` (preferred — scoped tag, hard to
   collide with other content)
2. Last fenced ``` ```json ... ``` `` block in the answer
"""

from __future__ import annotations

import json
import re
from typing import Any

_TAG_PATTERN = re.compile(
    r'<eval_results\b[^>]*>(.*?)</eval_results>',
    re.IGNORECASE | re.DOTALL,
)
_FENCE_PATTERN = re.compile(
    r'```\s*json\s*\n(.*?)\n\s*```',
    re.IGNORECASE | re.DOTALL,
)


def extract_json_block(answer: str) -> tuple[Any | None, str]:
    """Find and parse the answer's eval-results JSON block.

    Returns a tuple ``(obj, reason)`` where ``obj`` is the parsed value
    (typically a ``dict``) when extraction succeeds, otherwise ``None`` and
    ``reason`` describes why.
    """
    if not isinstance(answer, str) or not answer.strip():
        return None, 'answer is empty'

    candidates: list[str] = []
    tag_matches = list(_TAG_PATTERN.finditer(answer))
    if tag_matches:
        candidates.append(tag_matches[-1].group(1))
    fence_matches = list(_FENCE_PATTERN.finditer(answer))
    if fence_matches:
        candidates.append(fence_matches[-1].group(1))

    if not candidates:
        return (
            None,
            "no <eval_results>...</eval_results> tag or ```json fence found in answer",
        )

    last_err: str = ''
    for raw in candidates:
        text = raw.strip()
        try:
            return json.loads(text), 'ok'
        except json.JSONDecodeError as exc:
            last_err = f'{exc.msg} at line {exc.lineno} col {exc.colno}'
            continue

    return None, f'eval_results block is not valid JSON: {last_err}'


def navigate_json_path(obj: Any, path: str) -> tuple[Any, str]:
    """Navigate a dot-separated path on a parsed JSON object.

    ``path`` segments are matched as dict string keys (so numeric segments
    like ``"303K"`` work without quoting). Array indexing is intentionally
    not supported — emit a flat dict instead.

    Returns ``(value, '')`` on success or ``(None, reason)`` on failure.
    """
    if not path:
        return None, "json_path is empty"

    cur: Any = obj
    visited: list[str] = []
    for seg in path.split('.'):
        visited.append(seg)
        if not isinstance(cur, dict):
            crumb = '.'.join(visited[:-1]) or '<root>'
            return None, f"path stops at non-dict at '{crumb}'"
        if seg not in cur:
            available = sorted(cur.keys()) if cur else []
            return (
                None,
                f"missing key '{seg}' at '{'.'.join(visited)}' "
                f'(available: {available})',
            )
        cur = cur[seg]
    return cur, ''


def check_answer_json_numeric(
    answer: str,
    *,
    json_path: str,
    target: float,
    tolerance: float,
) -> tuple[bool, str]:
    """Check that ``answer[json_path]`` is within ``target ± tolerance``.

    Steps:
    1. Locate the eval-results JSON block in ``answer``.
    2. Navigate ``json_path`` (dot-separated dict keys).
    3. Coerce the value to ``float`` and compare against ``target`` with
       ``tolerance``.
    """
    if tolerance < 0:
        return False, 'tolerance must be >= 0'

    obj, reason = extract_json_block(answer)
    if obj is None:
        return False, reason

    value, nav_err = navigate_json_path(obj, json_path)
    if nav_err:
        return False, nav_err

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, f"value at '{json_path}' is not numeric: {value!r}"

    found = float(value)
    hit = abs(found - target) <= tolerance
    return hit, (
        f"target={target}, found={found}, tol={tolerance}, " f"path='{json_path}'"
    )
