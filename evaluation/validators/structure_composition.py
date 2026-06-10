"""Composition checks split from structure_general.py."""

from __future__ import annotations

from pathlib import Path

try:
    from pymatgen.core import Structure  # noqa: F401

    _PMG_AVAILABLE = True
except ImportError:
    _PMG_AVAILABLE = False

_IMPORT_MSG = "pymatgen not installed; install with: uv sync --extra calculation"


def check_composition(
    workspace_dir: str | Path,
    *,
    filename: str,
    must_contain_elements: list[str] | None = None,
    must_not_contain_elements: list[str] | None = None,
) -> tuple[bool, str]:
    """Verify structure contains (or excludes) specified elements."""
    from evaluation.validators.structure_general import _load_structure, _resolve_file

    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    actual_elements = {str(el) for el in struct.composition.elements}
    must_contain = set(must_contain_elements or [])
    must_not = set(must_not_contain_elements or [])
    missing = must_contain - actual_elements
    if missing:
        return False, (
            f"{fpath.name}: missing required elements {sorted(missing)}, "
            f"found {sorted(actual_elements)}"
        )
    unwanted = must_not & actual_elements
    if unwanted:
        return False, (
            f"{fpath.name}: contains forbidden elements {sorted(unwanted)}, "
            f"found {sorted(actual_elements)}"
        )
    return True, (
        f"{fpath.name}: composition check passed — "
        f"elements {sorted(actual_elements)} "
        f"(required: {sorted(must_contain)})"
    )
