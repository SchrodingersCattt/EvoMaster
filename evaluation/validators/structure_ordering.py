"""Verifiers for ordered-replica quality checks (SC_struct_005 and similar)."""

from __future__ import annotations

from pathlib import Path

try:
    from pymatgen.core import Structure

    _PMG_AVAILABLE = True
except ImportError:
    _PMG_AVAILABLE = False

_IMPORT_MSG = "pymatgen not installed"


def _resolve_glob(root: Path, pattern: str) -> list[Path]:
    """Resolve glob pattern to matching files."""
    matches = sorted(root.glob(pattern))
    return [m for m in matches if m.is_file()]


def check_integer_stoichiometry(
    workspace_dir: str | Path,
    *,
    filename: str,
) -> tuple[bool, str]:
    """Verify that all structures matching filename have integer atom counts.

    A properly ordered replica should never have fractional stoichiometry
    like H13.98 or Li0.5.
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpaths = _resolve_glob(root, filename)
    if not fpaths:
        return False, f"no file matching {filename!r} in {root}"

    for fpath in fpaths:
        try:
            struct = Structure.from_file(str(fpath))
        except Exception as exc:
            return False, f"could not parse {fpath.name}: {exc}"
        comp = struct.composition
        for el, amt in comp.items():
            if abs(amt - round(amt)) > 1e-6:
                return (
                    False,
                    f"{fpath.name}: element {el} has non-integer count {amt:.4f}",
                )

    return True, f"all {len(fpaths)} file(s) have integer stoichiometry"


def check_replicas_distinct(
    workspace_dir: str | Path,
    *,
    filename: str,
) -> tuple[bool, str]:
    """Verify that multiple structure files are not identical copies.

    Compares sorted fractional coordinates; if any two files have identical
    species+coordinates they are considered duplicates.
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpaths = _resolve_glob(root, filename)
    if len(fpaths) < 2:
        return True, f"only {len(fpaths)} file(s), nothing to compare"

    fingerprints: list[tuple[str, Path]] = []
    for fpath in fpaths:
        try:
            struct = Structure.from_file(str(fpath))
        except Exception as exc:
            return False, f"could not parse {fpath.name}: {exc}"
        species_str = ",".join(str(s) for s in struct.species)
        coords_str = ",".join(
            f"{c[0]:.4f},{c[1]:.4f},{c[2]:.4f}" for c in struct.frac_coords
        )
        fp = f"{species_str}|{coords_str}"
        for existing_fp, existing_path in fingerprints:
            if fp == existing_fp:
                return (
                    False,
                    f"{fpath.name} is identical to {existing_path.name}",
                )
        fingerprints.append((fp, fpath))

    return True, f"all {len(fpaths)} replicas are structurally distinct"
