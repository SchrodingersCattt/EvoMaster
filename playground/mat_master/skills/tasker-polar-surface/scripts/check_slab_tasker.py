"""
Check a built slab structure against Tasker polar surface rules.

Reads the slab file (POSCAR/CIF), infers layers along the surface normal,
and verifies: Type 1 = all layers stoichiometric; Type 2/3 = symmetric
termination (top and bottom layers mirror composition).

Optionally cross-checks with a literature lookup table (--lookup + --formula + --miller):
if the (material, surface) is in the table, adds literature_expected_type, literature_note,
literature_ref, and literature_consistent to the output.

Usage:
  python check_slab_tasker.py --file POSCAR --tasker_type 1
  python check_slab_tasker.py --file slab.cif --tasker_type 3
  python check_slab_tasker.py --file slab.cif --tasker_type 3 --formula ZnO --miller "0 0 0 1" --lookup ../reference/tasker_lookup.yaml

Output: JSON to stdout with compliant, tasker_type, symmetric, reason, layer_summary,
        and optionally literature_* fields.

Requires: pymatgen, numpy. Optional: PyYAML for --lookup.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _load_structure(filepath: Path):
    """Load periodic structure with pymatgen. Raises if not a 3D structure."""
    from pymatgen.core import Structure

    suffix = filepath.suffix.lower()
    if suffix == '.xyz':
        from pymatgen.core import Molecule

        Molecule.from_file(str(filepath))
        raise ValueError('XYZ is typically a molecule; use POSCAR or CIF for slab')
    return Structure.from_file(str(filepath))


def _slab_normal(struct):
    """Out-of-plane direction: use lattice c-axis (convention for slab)."""
    return np.array(struct.lattice.matrix[2]) / struct.lattice.c


def _layer_compositions(struct, normal, layer_threshold_angstrom: float = 0.8):
    """
    Project sites onto normal, cluster into layers, return list of composition dicts.
    Each composition is {element: count} for that layer (bottom to top).
    """
    coords = np.array(struct.cart_coords)
    species = [str(s.specie) for s in struct]
    proj = coords @ normal
    order = np.argsort(proj)
    proj_sorted = proj[order]
    species_sorted = [species[i] for i in order]

    layers: list[dict[str, int]] = []
    current_layer: dict[str, int] = {}
    last_p = None

    for p, sp in zip(proj_sorted, species_sorted):
        if last_p is not None and (p - last_p) > layer_threshold_angstrom:
            if current_layer:
                layers.append(dict(current_layer))
                current_layer = {}
        current_layer[sp] = current_layer.get(sp, 0) + 1
        last_p = p
    if current_layer:
        layers.append(current_layer)
    return layers


def _composition_match(c1: dict, c2: dict) -> bool:
    """True if both have same element counts."""
    if set(c1) != set(c2):
        return False
    return all(c1[k] == c2[k] for k in c1)


def _layer_stoichiometry_ratio(comp: dict) -> tuple:
    """Reduced ratio as sorted tuple of (element, count) for comparison."""
    total = sum(comp.values())
    if total == 0:
        return ()
    gcd = total
    for v in comp.values():
        a, b = gcd, v
        while b:
            a, b = b, a % b
        gcd = a
    return tuple(sorted((k, v // gcd) for k, v in comp.items()))


def _normalize_miller(s: str) -> str:
    """Normalize miller indices to space-separated string (e.g. '1 0 0' or '0 0 0 1')."""
    s = s.strip().replace(',', ' ')
    parts = s.split()
    return ' '.join(parts)


def _load_lookup(lookup_path: Path) -> list:
    """Load tasker_lookup.yaml; return list of entries or [] if missing/invalid."""
    if not lookup_path.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(lookup_path.read_text(encoding='utf-8'))
        return (data or {}).get('entries', [])
    except Exception:
        return []


def _lookup_entry(entries: list, formula: str, miller: str) -> dict | None:
    """Find an entry matching formula and miller (after normalizing). Returns first match or None."""
    formula = formula.strip()
    miller_n = _normalize_miller(miller)
    for e in entries:
        if e.get('formula', '').strip() != formula:
            continue
        entry_miller = _normalize_miller(e.get('miller', ''))
        if entry_miller == miller_n:
            return e
    return None


def _maybe_add_lookup(
    result: dict,
    lookup_path: Path | None,
    formula: str | None,
    miller: str | None,
) -> dict:
    """If lookup table and formula+miller given, add literature_* fields to result."""
    if not lookup_path or not formula or not miller:
        return result
    entries = _load_lookup(lookup_path)
    entry = _lookup_entry(entries, formula, miller) if entries else None
    if not entry:
        return result
    out = dict(result)
    out['literature_expected_type'] = entry['tasker_type']
    out['literature_note'] = entry.get('note', '')
    out['literature_ref'] = entry.get('ref', '')
    out['literature_consistent'] = result.get('tasker_type') == entry['tasker_type']
    return out


def check_slab(
    filepath: Path,
    tasker_type: int,
    lookup_path: Path | None = None,
    formula: str | None = None,
    miller: str | None = None,
) -> dict:
    """
    Check slab file against Tasker type. Returns dict with compliant, reason, etc.
    If lookup_path + formula + miller are given, adds literature_* fields from reference table.
    """
    struct = _load_structure(filepath)
    normal = _slab_normal(struct)
    layers = _layer_compositions(struct, normal)

    if len(layers) < 2:
        r = {
            'compliant': False,
            'tasker_type': tasker_type,
            'symmetric': False,
            'reason': 'Too few layers (need at least 2); is this a slab?',
            'layer_summary': [
                {'n_sites': sum(c.values()), 'composition': c} for c in layers
            ],
            'n_layers': len(layers),
        }
        return _maybe_add_lookup(r, lookup_path, formula, miller)

    n = len(layers)
    layer_summary = [
        {'layer_index': i, 'n_sites': sum(c.values()), 'composition': c}
        for i, c in enumerate(layers)
    ]

    if tasker_type == 1:
        # Type 1: every layer should be stoichiometric (same ratio as bulk)
        bulk_ratio = _layer_stoichiometry_ratio(
            {
                k: sum(layers[i].get(k, 0) for i in range(n))
                for k in set().union(*[set(c) for c in layers])
            }
        )
        for i, c in enumerate(layers):
            r = _layer_stoichiometry_ratio(c)
            if r != bulk_ratio:
                out = {
                    'compliant': False,
                    'tasker_type': 1,
                    'symmetric': False,
                    'reason': f"Type 1 requires stoichiometric layers; layer {i} has ratio {r} vs bulk {bulk_ratio}",
                    'layer_summary': layer_summary,
                    'n_layers': n,
                }
                return _maybe_add_lookup(out, lookup_path, formula, miller)
        out = {
            'compliant': True,
            'tasker_type': 1,
            'symmetric': True,
            'reason': 'All layers stoichiometric (Type 1).',
            'layer_summary': layer_summary,
            'n_layers': n,
        }
        return _maybe_add_lookup(out, lookup_path, formula, miller)

    if tasker_type in (2, 3):
        # Type 2/3: symmetric termination — top and bottom layers mirror
        for i in range((n + 1) // 2):
            if not _composition_match(layers[i], layers[n - 1 - i]):
                out = {
                    'compliant': False,
                    'tasker_type': tasker_type,
                    'symmetric': False,
                    'reason': f"Type {tasker_type} requires symmetric termination; layer {i} (bottom) and layer {n-1-i} (top) have different composition: {layers[i]} vs {layers[n-1-i]}",
                    'layer_summary': layer_summary,
                    'n_layers': n,
                }
                return _maybe_add_lookup(out, lookup_path, formula, miller)
        out = {
            'compliant': True,
            'tasker_type': tasker_type,
            'symmetric': True,
            'reason': f"Symmetric termination (Type {tasker_type}): top and bottom layers mirror.",
            'layer_summary': layer_summary,
            'n_layers': n,
        }
        return _maybe_add_lookup(out, lookup_path, formula, miller)

    out = {
        'compliant': False,
        'tasker_type': tasker_type,
        'symmetric': False,
        'reason': f"Unknown tasker_type {tasker_type}; use 1, 2, or 3.",
        'layer_summary': layer_summary,
        'n_layers': n,
    }
    return _maybe_add_lookup(out, lookup_path, formula, miller)


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Check built slab against Tasker polar surface (layer symmetry / stoichiometry).'
    )
    ap.add_argument(
        '--file', required=True, help='Path to slab structure (POSCAR, CIF, etc.).'
    )
    ap.add_argument(
        '--tasker_type',
        type=int,
        required=True,
        choices=[1, 2, 3],
        help='Tasker type for this surface: 1=non-polar, 2=stacking dipole, 3=polar.',
    )
    ap.add_argument(
        '--lookup',
        type=Path,
        default=None,
        help='Path to tasker_lookup.yaml. Default: ../reference/tasker_lookup.yaml relative to script.',
    )
    ap.add_argument(
        '--formula',
        type=str,
        default=None,
        help='Material formula (e.g. ZnO) for literature lookup. Use with --miller.',
    )
    ap.add_argument(
        '--miller',
        type=str,
        default=None,
        help='Miller indices (e.g. "0 0 0 1" or "1 0 0") for literature lookup. Use with --formula.',
    )
    args = ap.parse_args()
    path = Path(args.file)

    lookup_path = args.lookup
    if lookup_path is None and (args.formula and args.miller):
        script_dir = Path(__file__).resolve().parent
        lookup_path = script_dir / '..' / 'reference' / 'tasker_lookup.yaml'

    if not path.exists():
        out = {
            'compliant': False,
            'tasker_type': args.tasker_type,
            'symmetric': False,
            'reason': f"File not found: {path}",
            'layer_summary': [],
            'n_layers': 0,
        }
        print(json.dumps(out, indent=2))
        sys.exit(1)

    try:
        result = check_slab(
            path,
            args.tasker_type,
            lookup_path=lookup_path,
            formula=args.formula,
            miller=args.miller,
        )
    except Exception as e:
        result = {
            'compliant': False,
            'tasker_type': args.tasker_type,
            'symmetric': False,
            'reason': f"Check failed: {e}",
            'layer_summary': [],
            'n_layers': 0,
        }
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get('compliant') else 1)


if __name__ == '__main__':
    main()
