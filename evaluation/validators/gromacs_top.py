"""Validators for GROMACS topology files (.top / .itp).

GROMACS topology files use section headers like [ bonds ], [ angles ],
[ dihedrals ], etc. Each section contains entries (one per line) after
the header until the next section or EOF.
"""

from __future__ import annotations

import re
from pathlib import Path

from .text_file import _resolve_file

_SECTION_RE = re.compile(r"^\s*\[\s*(\w+)\s*\]")


def _parse_sections(content: str) -> dict[str, list[str]]:
    """Parse a GROMACS top/itp file into {section_name: [lines...]}."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in content.splitlines():
        stripped = raw_line.split(";", 1)[0].strip()
        m = _SECTION_RE.match(stripped)
        if m:
            current = m.group(1).lower()
            sections.setdefault(current, [])
            continue
        if current and stripped:
            sections[current].append(stripped)
    return sections


def _count_atoms(sections: dict[str, list[str]]) -> int:
    """Count atoms from [ atoms ] section."""
    return len(sections.get("atoms", []))


def check_gromacs_top(
    workspace_dir: str | Path,
    *,
    filename: str,
    check: str,
    expected: str | list[str] | None = None,
    allowed: list[str] | None = None,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Run a semantic check on a GROMACS topology file.

    Supported checks:
    - has_section: verify a named section exists and is non-empty
    - section_count_range: verify a section has entry count within [min, max]
    - bond_completeness: verify bonds count ≈ atom_count × ratio (±tolerance)
    """
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {fpath.name}: {exc}"

    sections = _parse_sections(content)

    if check == "has_section":
        return _check_has_section(fpath, sections, expected)
    elif check == "section_count_range":
        return _check_section_count_range(fpath, sections, expected, allowed)
    elif check == "bond_completeness":
        return _check_bond_completeness(fpath, sections, expected, allowed)
    else:
        return False, f"unknown gromacs_top_check check type: {check!r}"


def _check_has_section(
    fpath: Path,
    sections: dict[str, list[str]],
    expected: str | list[str] | None,
) -> tuple[bool, str]:
    """Verify named section(s) exist and are non-empty."""
    if not expected:
        return False, "gromacs_top_check has_section: 'expected' must be provided"

    names = [expected] if isinstance(expected, str) else list(expected)
    missing = []
    empty = []
    for name in names:
        key = name.lower()
        if key not in sections:
            missing.append(name)
        elif not sections[key]:
            empty.append(name)

    if missing:
        return False, f"{fpath.name}: missing sections: {missing}"
    if empty:
        return False, f"{fpath.name}: empty sections: {empty}"
    return True, f"{fpath.name}: all sections present and non-empty: {names}"


def _check_section_count_range(
    fpath: Path,
    sections: dict[str, list[str]],
    expected: str | list[str] | None,
    allowed: list[str] | None,
) -> tuple[bool, str]:
    """Verify a section has entry count within [min, max].

    expected: section name (str)
    allowed: ["<min>", "<max>"]
    """
    if not expected or not isinstance(expected, str):
        return False, "gromacs_top_check section_count_range: 'expected' must be section name"
    if not allowed or len(allowed) < 2:
        return False, "gromacs_top_check section_count_range: 'allowed' must be ['<min>', '<max>']"

    try:
        min_count = int(allowed[0])
        max_count = int(allowed[1])
    except (TypeError, ValueError):
        return False, f"gromacs_top_check section_count_range: invalid range {allowed}"

    key = expected.lower()
    if key not in sections:
        return False, f"{fpath.name}: section [{expected}] not found"

    count = len(sections[key])
    if min_count <= count <= max_count:
        return True, (
            f"{fpath.name}: [{expected}] has {count} entries "
            f"(allowed [{min_count}, {max_count}])"
        )
    return False, (
        f"{fpath.name}: [{expected}] has {count} entries, "
        f"expected [{min_count}, {max_count}]"
    )


def _check_bond_completeness(
    fpath: Path,
    sections: dict[str, list[str]],
    expected: str | list[str] | None,
    allowed: list[str] | None,
) -> tuple[bool, str]:
    """Verify bonds count ≈ atom_count × ratio within tolerance.

    expected: ratio as string (e.g. "1.5" for sp2 carbon)
    allowed: ["<tolerance_fraction>"] (e.g. ["0.05"] means ±5%)
    """
    if not expected:
        return False, "gromacs_top_check bond_completeness: 'expected' ratio required"

    try:
        ratio = float(expected if isinstance(expected, str) else expected[0])
    except (TypeError, ValueError):
        return False, f"gromacs_top_check bond_completeness: invalid ratio '{expected}'"

    tolerance = 0.05
    if allowed and len(allowed) >= 1:
        try:
            tolerance = float(allowed[0])
        except (TypeError, ValueError):
            pass

    atom_count = _count_atoms(sections)
    if atom_count == 0:
        return False, f"{fpath.name}: no [ atoms ] section or empty"

    bond_count = len(sections.get("bonds", []))
    if bond_count == 0:
        return False, f"{fpath.name}: no [ bonds ] section or empty"

    expected_bonds = atom_count * ratio
    lower = expected_bonds * (1 - tolerance)
    upper = expected_bonds * (1 + tolerance)

    if lower <= bond_count <= upper:
        return True, (
            f"{fpath.name}: {bond_count} bonds for {atom_count} atoms "
            f"(ratio {bond_count/atom_count:.3f}, expected ~{ratio} ±{tolerance*100:.0f}%)"
        )
    return False, (
        f"{fpath.name}: {bond_count} bonds for {atom_count} atoms "
        f"(ratio {bond_count/atom_count:.3f}), expected ~{expected_bonds:.0f} "
        f"[{lower:.0f}, {upper:.0f}]"
    )
