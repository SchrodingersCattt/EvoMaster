"""Shared helpers for workspace file resolution and JSON evidence walking.

Every validator module must resolve workspace files and mine JSON artifacts
through these helpers instead of hand-rolling copies: the record-level and
execution-level halves of one check must agree on which file they are looking
at and which keys count as identifiers, or their verdicts diverge.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


def resolve_file(
    workspace: Path,
    pattern: str,
    *,
    workspace_resolve: str = 'recursive',
) -> Path | None:
    """Resolve *pattern* to a single file under *workspace*.

    *recursive* (default): exact relative-path match first, then the newest
    file anywhere under the workspace whose basename matches the pattern's
    basename (``fnmatch``, so globs like ``*.cif`` work).

    *root*: only ``workspace/<pattern>`` when the pattern is a single path
    segment; no recursive fallback.
    """
    if workspace_resolve == 'root':
        if len(Path(pattern).parts) != 1:
            return None
        direct = workspace / pattern
        return direct if direct.is_file() else None

    direct = workspace / pattern
    if direct.is_file():
        return direct
    basename = Path(pattern).name
    hits = [
        path
        for path in workspace.rglob('*')
        if path.is_file() and fnmatch.fnmatchcase(path.name, basename)
    ]
    if not hits:
        return None
    return max(hits, key=lambda path: path.stat().st_mtime)


def walk_json(value: object):
    """Yield *value* and every nested dict/list element depth-first."""
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def normalise_key(value: object) -> str:
    """Lower-case a JSON key and strip every non-alphanumeric character."""
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def positive_int(value: object) -> int | None:
    """Return *value* as a positive int, or None (bools never count)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def is_positive_id(value: object) -> bool:
    return positive_int(value) is not None


_ID_KEY_EXACT = frozenset({'id', 'ids'})
_ID_KEY_SUFFIXES = (
    'identifier',
    'identifiers',
    'bohrid',
    'bohrids',
    'jobid',
    'jobids',
    'taskid',
    'taskids',
)


def is_identifier_key(key: object) -> bool:
    """Single policy for which JSON keys carry job/task identifiers.

    Group IDs are deliberately excluded: a job-group identifier must never
    satisfy a job/task-identifier requirement.
    """
    normalised = normalise_key(key)
    if 'group' in normalised:
        return False
    return normalised in _ID_KEY_EXACT or normalised.endswith(_ID_KEY_SUFFIXES)


def collect_positive_ids(value: object) -> set[int]:
    """Collect every positive int found under an identifier-like key."""
    identifiers: set[int] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if is_identifier_key(key):
                identifiers.update(
                    candidate
                    for candidate in map(positive_int, walk_json(child))
                    if candidate is not None
                )
            else:
                identifiers.update(collect_positive_ids(child))
    elif isinstance(value, list):
        for child in value:
            identifiers.update(collect_positive_ids(child))
    return identifiers
