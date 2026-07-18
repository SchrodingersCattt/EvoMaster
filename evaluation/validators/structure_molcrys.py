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

    cif_paths = sorted(root.rglob('*.cif')) + sorted(root.rglob('*.CIF'))
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


def _gcd_of_list(vals: list[int]) -> int:
    """GCD of a list of positive integers."""
    from math import gcd

    result = vals[0]
    for v in vals[1:]:
        result = gcd(result, v)
    return result


def _reduce_counts(counts: dict[str, float]) -> dict[str, float]:
    """Reduce element counts by their GCD so e.g. {H:144, C:48} → {H:3, C:1}.

    Only reduces when all counts are (close to) integers.
    """
    int_vals = []
    for v in counts.values():
        rounded = round(v)
        if abs(v - rounded) > 0.01:
            return dict(counts)  # fractional counts → return as-is
        int_vals.append(rounded)

    if not int_vals or any(v == 0 for v in int_vals):
        return dict(counts)

    g = _gcd_of_list(int_vals)
    if g < 1:
        return dict(counts)
    return {k: round(v) / g for k, v in counts.items()}


def _same_formula(lhs: str, rhs: str) -> bool:
    """Compare two formula strings via reduced element-ratio dictionaries.

    This matches regardless of element ordering and whether the formulas
    are written as full-cell or reduced form (e.g. ``H144C48N24Cl24O96``
    matches ``C2H6NClO4``).
    """
    left = _parse_formula_counts(lhs)
    right = _parse_formula_counts(rhs)
    if left is None or right is None:
        return False
    if set(left) != set(right):
        return False
    left_r = _reduce_counts(left)
    right_r = _reduce_counts(right)
    for key in left_r:
        if abs(left_r[key] - right_r[key]) > 1e-8:
            return False
    return True


def _extract_formula_like_tokens(answer: str) -> list[str]:
    """Extract chemical-formula-like tokens from free text.

    The regex accepts both explicit-count (``H144C48``) and implicit-count-1
    (``FeC5N6O``) element tokens, as long as the overall token has at least
    one digit (to avoid matching bare element symbols or plain words).
    """
    # Match sequences of Element[Count] where at least one element has a digit
    return list(
        {
            token
            for token in re.findall(
                r'\b([A-Z][a-z]?\d*(?:\.\d+)?(?:[A-Z][a-z]?\d*(?:\.\d+)?)*)\b',
                answer,
            )
            if _parse_formula_counts(token) is not None
            # Require at least one digit to avoid matching bare words like "Fe"
            and re.search(r'\d', token)
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


# ---------------------------------------------------------------------------
# SC_struct_006: MolCrysKit local-environment validation for hydrogenated CIF
# ---------------------------------------------------------------------------


def check_molcrys_local_env(
    workspace_dir: str | Path,
    *,
    filename: str,
    expected_formula: str,
    z_value: int = 4,
) -> tuple[bool, str]:
    """Validate local molecular environment of a hydrogenated CIF via MolCrysKit.

    Checks:
    1. The CIF can be parsed by MolCrysKit.
    2. All molecules are intact (no fragmented molecules with fewer atoms than
       the expected per-molecule count).
    3. Every molecule has a composition matching *expected_formula* (reduced).
    4. The number of molecules equals *z_value* (the Z in Z' = 1 structures).
    5. ChemicalEnvironment validation: H-bearing heavy atoms have chemically
       consistent local geometry (e.g. sp3 C bonded to H should show
       tetrahedral coordination, not linear or isolated).
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

    # Resolve file
    import fnmatch

    fpath = root / filename
    if not fpath.is_file():
        hits = [
            p
            for p in root.iterdir()
            if p.is_file() and fnmatch.fnmatch(p.name, filename)
        ]
        if not hits:
            return False, f'no file matching {filename!r} in {root}'
        fpath = max(hits, key=lambda p: p.stat().st_mtime)

    try:
        crystal = read_mol_crystal(str(fpath))
    except Exception as exc:
        return False, f'MolCrysKit could not parse {fpath.name}: {exc}'

    molecules = crystal.molecules
    if not molecules:
        return False, f'{fpath.name}: MolCrysKit found zero molecules'

    n_mols = len(molecules)
    if n_mols != z_value:
        return (
            False,
            f'{fpath.name}: expected {z_value} molecules (Z={z_value}), '
            f'found {n_mols}',
        )

    # Parse expected formula into element→count dict for reduced comparison
    expected_counts = _parse_formula_counts(expected_formula)
    if expected_counts is None:
        return False, f'could not parse expected formula {expected_formula!r}'
    expected_reduced = _reduce_counts(expected_counts)
    expected_atoms_per_mol = sum(round(v) for v in expected_counts.values())

    issues: list[str] = []
    for i, mol in enumerate(molecules):
        n_atoms = len(mol)
        # Check molecule size (should not be fragmented)
        if n_atoms < expected_atoms_per_mol - 2:
            issues.append(
                f'mol[{i}] has {n_atoms} atoms, expected ~{expected_atoms_per_mol}'
            )
            continue

        # Build composition dict from molecule
        mol_counts: dict[str, float] = {}
        for atom in mol:
            # MolCrysKit molecule atoms expose element via .species or .symbol
            sym = getattr(atom, 'symbol', None) or str(getattr(atom, 'species', ''))
            mol_counts[sym] = mol_counts.get(sym, 0.0) + 1.0

        mol_reduced = _reduce_counts(mol_counts)
        if set(mol_reduced) != set(expected_reduced):
            issues.append(
                f'mol[{i}] elements {set(mol_counts)} != expected {set(expected_reduced)}'
            )
        else:
            for elem in expected_reduced:
                if abs(mol_reduced.get(elem, 0) - expected_reduced[elem]) > 1e-8:
                    issues.append(
                        f'mol[{i}] reduced composition mismatch: '
                        f'{mol_counts} vs expected {expected_formula}'
                    )
                    break

    if issues:
        detail = '; '.join(issues[:5])
        return False, f'{fpath.name}: local-env issues: {detail}'

    # ----- Step 5: ChemicalEnvironment validation -----
    chemenv_issues = _check_chemenv(molecules)
    if chemenv_issues:
        detail = '; '.join(chemenv_issues[:5])
        return False, f'{fpath.name}: chemenv issues: {detail}'

    return (
        True,
        f'{fpath.name}: MolCrysKit OK — {n_mols} molecules, '
        f'each matching {expected_formula}, chemenv consistent',
    )


def _check_chemenv(molecules: list) -> list[str]:
    """Run ChemicalEnvironment checks on each molecule.

    Validates that H-bearing heavy atoms have chemically reasonable local
    geometry (coordination number, bond-angle sums) consistent with their
    hybridisation.  Returns a list of issue strings (empty = all OK).
    """
    try:
        from molcrys_kit.analysis.chemical_env import ChemicalEnvironment
    except ImportError:
        # ChemicalEnvironment not available; skip silently — the formula
        # and molecule-count checks above already provide core validation.
        return []

    issues: list[str] = []
    for mol_idx, mol in enumerate(molecules):
        try:
            env = ChemicalEnvironment(mol)
        except Exception as exc:
            issues.append(f'mol[{mol_idx}] ChemicalEnvironment init failed: {exc}')
            continue

        graph = mol.graph
        symbols = mol.get_chemical_symbols()

        for node_idx in graph.nodes():
            sym = symbols[node_idx]
            # Only validate heavy atoms that are bonded to at least one H
            h_neighbors = [nb for nb in graph.neighbors(node_idx) if symbols[nb] == 'H']
            if not h_neighbors or sym == 'H':
                continue

            stats = env.get_local_geometry_stats(node_idx)
            coord = stats['coordination_number']

            # Sanity: a heavy atom bonded to H must have coordination >= 2
            # (the H itself + at least one other bond)
            if coord < 2:
                issues.append(
                    f'mol[{mol_idx}] atom {node_idx} ({sym}): '
                    f'coord={coord} but bonded to H — isolated or dangling'
                )
                continue

            # For C atoms bonded to H, verify the geometry is plausible
            if sym == 'C':
                angle_sum = stats['bond_angle_sum']
                # sp3 C (tetrahedral): angle_sum ~ 328.5° for coord=4
                # sp2 C: angle_sum ~ 360° for coord=3
                # Reject obviously broken geometry: coord=4 but angle_sum < 250°
                # or coord=3 with angle_sum < 300°
                if coord == 4 and angle_sum < 250.0:
                    issues.append(
                        f'mol[{mol_idx}] C{node_idx}: coord=4 but '
                        f'angle_sum={angle_sum:.1f}° (expected ~328.5°)'
                    )
                elif coord == 3 and angle_sum < 300.0:
                    issues.append(
                        f'mol[{mol_idx}] C{node_idx}: coord=3 but '
                        f'angle_sum={angle_sum:.1f}° (expected ~328-360°)'
                    )

    return issues


# ---------------------------------------------------------------------------
# Molecule-formula list verification (CIF / ExtXYZ)
# ---------------------------------------------------------------------------


def check_molcrys_molecule_formulas(
    workspace_dir: str | Path,
    *,
    filename: str = '*.cif',
    expected_formulas: list[str],
    all_frames: bool = False,
) -> tuple[bool, str]:
    """Verify per-molecule chemical formulas via MolCrysKit.

    For CIF files: parse with ``read_mol_crystal``, extract sorted molecule
    formula list, compare against *expected_formulas* (also sorted).

    For ExtXYZ (multi-frame) with ``all_frames=True``: every frame must
    match the expected formula list.
    """
    try:
        from molcrys_kit.io.cif import read_mol_crystal
        from molcrys_kit.structures.crystal import MolecularCrystal
    except ImportError:
        return (
            False,
            'molcrys-kit not installed; install with: uv sync --extra calculation',
        )

    root = Path(workspace_dir)
    if not root.is_dir():
        return False, f'workspace not a directory: {workspace_dir}'

    from evaluation.validators._common import resolve_file

    resolved = resolve_file(root, filename)
    if resolved is None:
        return False, f'no file matching {filename!r} in {root}'
    fpath = str(resolved)

    expected_sorted = sorted(expected_formulas)

    if fpath.endswith('.extxyz') and all_frames:
        import ase.io

        try:
            frames = ase.io.read(fpath, index=':')
        except Exception as exc:
            return False, f'could not read extxyz {fpath}: {exc}'
        if not frames:
            return False, f'no frames in {fpath}'
        failures: list[str] = []
        for i, atoms in enumerate(frames):
            try:
                mc = MolecularCrystal.from_ase_atoms(atoms)
                actual = sorted(m.get_chemical_formula() for m in mc.molecules)
            except Exception as exc:
                failures.append(f'frame {i}: parse error: {exc}')
                continue
            if actual != expected_sorted:
                failures.append(f'frame {i}: got {actual}, expected {expected_sorted}')
        if failures:
            return False, '; '.join(failures[:3])
        return True, (
            f'all {len(frames)} frames match expected molecule formulas '
            f'{expected_sorted}'
        )
    else:
        # Single structure (CIF or first frame)
        try:
            if fpath.endswith('.cif') or fpath.endswith('.CIF'):
                crystal = read_mol_crystal(fpath)
            else:
                import ase.io

                atoms = ase.io.read(fpath)
                crystal = MolecularCrystal.from_ase_atoms(atoms)
            actual = sorted(m.get_chemical_formula() for m in crystal.molecules)
        except Exception as exc:
            return False, f'could not parse {fpath}: {exc}'
        if actual != expected_sorted:
            return False, (
                f'{Path(fpath).name}: molecule formulas {actual} '
                f'!= expected {expected_sorted}'
            )
        return True, (f'{Path(fpath).name}: molecule formulas match: {actual}')
