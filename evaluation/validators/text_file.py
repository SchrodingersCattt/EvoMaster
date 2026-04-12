"""Programmatic validators for plain-text workspace artifacts."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


def _resolve_file(
    workspace: Path,
    filename: str,
    *,
    workspace_resolve: str = 'recursive',
) -> Path | None:
    """Resolve *filename* under *workspace*.

    *recursive* (default): exact ``workspace/`` first, then newest basename match
    anywhere under the workspace (rglob).

    *root*: only ``workspace/<basename>`` if *filename* is a single path segment
    (no ``/`` or ``\\``); otherwise no match.
    """
    if workspace_resolve == 'root':
        if len(Path(filename).parts) != 1:
            return None
        candidate = workspace / filename
        if candidate.is_file():
            return candidate
        return None

    exact = workspace / filename
    if exact.is_file():
        return exact
    hits = [
        p
        for p in workspace.rglob("*")
        if p.is_file() and fnmatch.fnmatch(p.name, filename)
    ]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _normalize(text: str, *, case_sensitive: bool, normalize_whitespace: bool) -> str:
    if normalize_whitespace:
        text = re.sub(r'\s+', ' ', text).strip()
    if not case_sensitive:
        text = text.lower()
    return text


def check_text_file_contains_all(
    workspace_dir: str | Path,
    *,
    filename: str,
    tokens: list[str],
    case_sensitive: bool = False,
    normalize_whitespace: bool = True,
    workspace_resolve: str = 'recursive',
) -> tuple[bool, str]:
    """Check that all tokens are present in a text file."""
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        raw = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'failed reading {fpath.name}: {exc}'

    haystack = _normalize(
        raw, case_sensitive=case_sensitive, normalize_whitespace=normalize_whitespace
    )
    missing: list[str] = []
    for token in tokens:
        needle = _normalize(
            str(token),
            case_sensitive=case_sensitive,
            normalize_whitespace=normalize_whitespace,
        )
        if needle and needle not in haystack:
            missing.append(str(token))
    if missing:
        return False, f'{fpath.name}: missing tokens: {missing}'
    return True, f'{fpath.name}: all {len(tokens)} tokens found'


def check_text_file_regex(
    workspace_dir: str | Path,
    *,
    filename: str,
    pattern: str,
    flags: str = '',
    workspace_resolve: str = 'recursive',
) -> tuple[bool, str]:
    """Check whether regex *pattern* matches content of *filename*."""
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        raw = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'failed reading {fpath.name}: {exc}'

    flag_mask = 0
    flag_table = {'i': re.IGNORECASE, 'm': re.MULTILINE, 's': re.DOTALL}
    for ch in str(flags).lower():
        if ch in flag_table:
            flag_mask |= flag_table[ch]
    try:
        compiled = re.compile(pattern, flag_mask)
    except re.error as exc:
        return False, f'invalid regex pattern: {exc}'

    if compiled.search(raw) is None:
        return False, f'{fpath.name}: regex not matched: {pattern!r}'
    return True, f'{fpath.name}: regex matched'
