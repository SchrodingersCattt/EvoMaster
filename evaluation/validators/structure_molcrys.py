"""MolCrysKit-backed checks for molecular-crystal evaluation items.

Requires optional dependency group ``calculation`` (includes ``molcrys-kit``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# SC_struct_002: slab molecular integrity via layer / molecule scaling
# ---------------------------------------------------------------------------


def verify_molecular_slab_layer_scaling(
    workspace_dir: str | Path,
    *,
    unit_cell_atoms: int = 144,
    slab_atoms: int = 576,
    layers: int = 4,
) -> tuple[bool, str]:
    """Use MolCrysKit graph-based molecules to validate slab vs bulk scaling.

    Pass when a ``bulk-like`` structure (~``unit_cell_atoms``) and a
    ``slab-like`` structure (~``slab_atoms``) exist, molecule counts scale by
    ``layers``, and no slab molecule is smaller than the smallest bulk
    molecule (heuristic against cutting through molecules).
    """
    try:
        from molcrys_kit.io.cif import read_mol_crystal
    except ImportError:
        return (
            False,
            'molcrys-kit not installed; install with: uv sync --extra calculation',
        )

    root = Path(workspace_dir)
    if not root.is_dir():
        return False, f'workspace not a directory: {workspace_dir}'

    cif_paths = sorted(root.glob('*.cif')) + sorted(root.glob('*.CIF'))
    if not cif_paths:
        return False, 'no .cif files in workspace'

    parsed: list[tuple[str, Any, int, int]] = []
    for p in cif_paths:
        try:
            crystal = read_mol_crystal(str(p))
            nm = len(crystal.molecules)
            na = sum(len(m) for m in crystal.molecules)
            parsed.append((p.name, crystal, nm, na))
        except Exception:  # noqa: BLE001
            continue

    if len(parsed) < 1:
        return False, 'no CIF could be parsed by MolCrysKit'

    # Pick best bulk candidate (atom count closest to unit_cell_atoms)
    bulk = min(parsed, key=lambda t: abs(t[3] - unit_cell_atoms))
    slab_candidates = [
        t for t in parsed if t[3] >= slab_atoms - 8 and t[3] <= slab_atoms + 8
    ]
    if not slab_candidates:
        slab_candidates = [max(parsed, key=lambda t: t[3])]
    slab = min(slab_candidates, key=lambda t: abs(t[3] - slab_atoms))

    b_name, b_cry, b_nm, b_na = bulk
    s_name, s_cry, s_nm, s_na = slab

    if b_na == s_na and len(parsed) == 1:
        return False, 'only one distinct structure found; need bulk+slab outputs'

    bulk_sizes = sorted(len(m) for m in b_cry.molecules)
    slab_sizes = sorted(len(m) for m in s_cry.molecules)
    if not bulk_sizes or not slab_sizes:
        return False, 'empty molecule list after parsing'

    min_bulk = bulk_sizes[0]
    min_slab = slab_sizes[0]
    if min_slab < min_bulk:
        return (
            False,
            f'smallest slab molecule ({min_slab} atoms) < smallest bulk molecule '
            f'({min_bulk} atoms); likely fragmented / cut molecules ({s_name})',
        )

    if abs(b_na - unit_cell_atoms) > 4:
        return (
            False,
            f'bulk candidate {b_name} has {b_na} atoms, expected ~{unit_cell_atoms}',
        )
    if abs(s_na - slab_atoms) > 8:
        return (
            False,
            f'slab candidate {s_name} has {s_na} atoms, expected ~{slab_atoms}',
        )

    if b_nm == 0:
        return False, 'bulk has zero molecules'
    ratio = s_nm / b_nm
    if abs(ratio - layers) > 0.6:
        return (
            False,
            f'molecule count ratio slab/bulk = {ratio:.2f}, expected ~{layers} '
            f'({s_name}: {s_nm} mols vs {b_name}: {b_nm} mols)',
        )

    return (
        True,
        f'MolCrysKit: {s_name} vs {b_name}: atoms {s_na}/{b_na}, '
        f'molecules {s_nm}/{b_nm}, min mol sizes {min_slab}/{min_bulk}',
    )


# ---------------------------------------------------------------------------
# SC_struct_005: four exact formulas + DAN-2 integer (no fractional occupancy)
# ---------------------------------------------------------------------------

_OTHER_FORMULAS_SC005 = (
    'H144C48N24Cl24O96',
    'H288C80N48Cl48O192',
    'Ag8H112C40N16Cl24O96',
    'Fe2H40C24N16O2',
)


_FORMULA_TOKEN_RE = re.compile(r'([A-Z][a-z]?)(\d+(?:\.\d+)?)?')


def _parse_formula_counts(formula: str) -> dict[str, float] | None:
    text = formula.strip()
    if not text or not re.fullmatch(r'(?:[A-Z][a-z]?\d*(?:\.\d+)?)+', text):
        return None

    counts: dict[str, float] = {}
    consumed = ''
    for element, raw_count in _FORMULA_TOKEN_RE.findall(text):
        consumed += f'{element}{raw_count}'
        count = float(raw_count) if raw_count else 1.0
        counts[element] = counts.get(element, 0.0) + count
    if consumed != text:
        return None
    return counts


def _same_formula(lhs: str, rhs: str) -> bool:
    left = _parse_formula_counts(lhs)
    right = _parse_formula_counts(rhs)
    if left is None or right is None:
        return False
    if set(left) != set(right):
        return False
    for key in left:
        if abs(left[key] - right[key]) > 1e-8:
            return False
    return True


def _extract_formula_like_tokens(answer: str) -> list[str]:
    return list(
        {
            token
            for token in re.findall(r'\b(?:[A-Z][a-z]?\d+(?:\.\d+)?)+\b', answer)
            if _parse_formula_counts(token) is not None
        }
    )


def check_sc005_other_formulas_in_answer(answer: str) -> tuple[bool, str]:
    """Four non-DAN-2 reference formula strings must appear in the answer modulo formula normalization."""
    found_tokens = _extract_formula_like_tokens(answer)
    missing = []
    for expected in _OTHER_FORMULAS_SC005:
        if not any(_same_formula(expected, actual) for actual in found_tokens):
            missing.append(expected)
    if missing:
        return False, f'missing expected formulas (normalized match): {missing}'
    return True, 'all four non-DAN-2 reference formulas found in answer'


def _extract_dan2_formula_region(answer: str) -> str | None:
    """Heuristic: text after DAN-2 / disorder_DAN-2 up to next disorder_ or EOF."""
    lower = answer.lower()
    keys = ('disorder_dan-2', 'disorder_dan-2.cif', 'dan-2.cif', 'dan-2')
    start = -1
    for k in keys:
        idx = lower.find(k)
        if idx >= 0:
            start = idx
            break
    if start < 0:
        return None
    rest = answer[start:]
    # cut at next disorder_ line (another file)
    m = re.search(r'\n\s*[-*]?\s*disorder_[^\n]+', rest[1:], re.I)
    if m:
        rest = rest[: m.start() + 1]
    return rest


def check_disorder_dan2_integer_formula(answer: str) -> tuple[bool, str]:
    """Ordered-replica reporting for DAN-2 must not use fractional stoichiometry."""
    block = _extract_dan2_formula_region(answer)
    if not block:
        return False, 'no DAN-2 / disorder_DAN-2 section found in answer'

    # chemical_formula value: allow "chemical_formula: ..." or backtick formula
    cf = re.search(
        r'chemical_formula\s*[:=]\s*`?([A-Za-z0-9.]+)`?',
        block,
        re.I,
    )
    if not cf:
        cf = re.search(r'`([A-Z][A-Za-z0-9.]{3,})`', block)
    formula = cf.group(1) if cf else None
    if not formula:
        # fallback: first token that looks like a formula with digits
        m2 = re.search(r'\b([A-Z][a-z]?\d+(?:[A-Z][a-z]?\d+)+)\b', block)
        formula = m2.group(1) if m2 else None
    if not formula:
        return False, 'could not extract a chemical_formula for DAN-2 block'

    # Fractional occupancy style: digit.decimal in counts (e.g. H13.98)
    if re.search(r'\d\.\d', formula):
        return (
            False,
            f'DAN-2 formula contains fractional stoichiometry: {formula!r}',
        )
    return True, f'DAN-2 reported formula has integer-only counts: {formula!r}'


def run_sc005_formula_checks(answer: str) -> tuple[bool, str]:
    """Combined SC005 formula checks (for tests / single entry point)."""
    ok, r1 = check_sc005_other_formulas_in_answer(answer)
    if not ok:
        return ok, r1
    return check_disorder_dan2_integer_formula(answer)
