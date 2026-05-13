"""Validators for ABACUS STRU file structural checks."""

from __future__ import annotations

import re
from pathlib import Path

from .text_file import _resolve_file


def _parse_stru_magnetic_moments(content: str) -> list[float]:
    """Extract all atomic magnetic moments from a STRU file.

    Handles both syntaxes:
    - Species-level: bare number on its own line below species label
    - Per-atom: `mag <value>` or `magmom <value>` on coordinate lines
    """
    moments: list[float] = []
    lines = content.split('\n')

    in_atomic_positions = False
    expect_moment_line = False
    expect_natom_line = False
    atoms_remaining = 0

    for line in lines:
        stripped = line.strip()

        if stripped == 'ATOMIC_POSITIONS':
            in_atomic_positions = True
            continue

        if not in_atomic_positions:
            continue

        if not stripped:
            continue

        if stripped in ('Direct', 'Cartesian', 'Cartesian_angstrom', 'Cartesian_au'):
            continue

        if expect_moment_line:
            try:
                mag = float(stripped)
                moments.append(mag)
            except ValueError:
                pass
            expect_moment_line = False
            expect_natom_line = True
            continue

        if expect_natom_line:
            expect_natom_line = False
            try:
                atoms_remaining = int(stripped)
            except ValueError:
                atoms_remaining = 0
            continue

        if atoms_remaining > 0:
            mag_match = re.search(r'\bmag(?:mom)?\s+([-+]?\d+\.?\d*)', stripped)
            if mag_match:
                moments.append(float(mag_match.group(1)))
            atoms_remaining -= 1
            if atoms_remaining == 0:
                expect_moment_line = False
            continue

        # Must be a species label line — next line is the moment
        expect_moment_line = True

    return moments


def _classify_magnetic_order(moments: list[float]) -> str:
    """Classify magnetic order from a list of moments.

    Returns: 'afm', 'fm', or 'nonmagnetic'
    """
    if not moments:
        return 'nonmagnetic'

    has_positive = any(m > 0 for m in moments)
    has_negative = any(m < 0 for m in moments)

    if has_positive and has_negative:
        return 'afm'
    elif has_positive or has_negative:
        return 'fm'
    else:
        return 'nonmagnetic'


def check_stru_file(
    workspace_dir: str | Path,
    *,
    filename: str,
    check: str,
    expected: str | int | None = None,
    workspace_resolve: str = 'recursive',
) -> tuple[bool, str]:
    """Run a structural check on an ABACUS STRU file.

    Supported checks:
    - magnetic_order: expected = 'afm' | 'fm' | 'nonmagnetic'
    - species_count: expected = int (number of species)
    - total_atoms: expected = int (total atom count)
    """
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        content = fpath.read_text(encoding='utf-8')
    except Exception as exc:
        return False, f'failed reading {fpath.name}: {exc}'

    if check == 'magnetic_order':
        moments = _parse_stru_magnetic_moments(content)
        actual = _classify_magnetic_order(moments)
        if actual == expected:
            return True, f'{fpath.name}: magnetic_order={actual} (moments: {moments})'
        return False, (
            f'{fpath.name}: magnetic_order={actual}, expected {expected} '
            f'(moments: {moments})'
        )

    elif check == 'species_count':
        species_section = re.search(
            r'ATOMIC_SPECIES\s*\n(.*?)(?=\n\s*(?:NUMERICAL_ORBITAL|LATTICE_CONSTANT|LATTICE_VECTORS|\Z))',
            content,
            re.DOTALL,
        )
        if not species_section:
            return False, f'{fpath.name}: ATOMIC_SPECIES section not found'
        species_lines = [
            l for l in species_section.group(1).strip().split('\n')
            if l.strip()
        ]
        actual_count = len(species_lines)
        if actual_count == int(expected or 0):
            return True, f'{fpath.name}: species_count={actual_count}'
        return False, f'{fpath.name}: species_count={actual_count}, expected {expected}'

    elif check == 'total_atoms':
        total = 0
        lines = content.split('\n')
        in_ap = False
        # States: 'label' -> 'moment' -> 'count' -> 'coords'
        state = 'label'
        atoms_remaining = 0
        for line in lines:
            s = line.strip()
            if s == 'ATOMIC_POSITIONS':
                in_ap = True
                continue
            if not in_ap or not s:
                continue
            if s in ('Direct', 'Cartesian', 'Cartesian_angstrom', 'Cartesian_au'):
                continue
            if state == 'label':
                state = 'moment'
            elif state == 'moment':
                state = 'count'
            elif state == 'count':
                try:
                    atoms_remaining = int(s)
                    total += atoms_remaining
                except ValueError:
                    atoms_remaining = 0
                state = 'coords'
            elif state == 'coords':
                atoms_remaining -= 1
                if atoms_remaining <= 0:
                    state = 'label'

        if total == int(expected or 0):
            return True, f'{fpath.name}: total_atoms={total}'
        return False, f'{fpath.name}: total_atoms={total}, expected {expected}'

    else:
        return False, f'unknown stru_file_check check type: {check!r}'
