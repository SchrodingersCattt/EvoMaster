"""Validators for ABACUS STRU file structural checks."""

from __future__ import annotations

import re
from pathlib import Path

from .text_file import _resolve_file

_MAG_MOMENT_LINE = re.compile(
    r"\bmag(?:mom)?\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)
_MAG_VECTOR_LINE = re.compile(
    r"\bmag(?:mom)?\s+"
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+"
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)
_MAG_EPS = 1e-8


def _parse_stru_site_magnetic_moments(content: str) -> list[float]:
    """Per-site moments from `mag` / `magmom` on ATOMIC_POSITIONS coordinate lines."""
    moments: list[float] = []
    lines = content.split("\n")

    in_atomic_positions = False
    expect_moment_line = False
    expect_natom_line = False
    atoms_remaining = 0

    for line in lines:
        stripped = line.strip()

        if stripped == "ATOMIC_POSITIONS":
            in_atomic_positions = True
            continue

        if not in_atomic_positions:
            continue

        if not stripped:
            continue

        if stripped in ("Direct", "Cartesian", "Cartesian_angstrom", "Cartesian_au"):
            continue

        if expect_moment_line:
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
            mag_match = _MAG_MOMENT_LINE.search(stripped)
            if mag_match:
                moments.append(float(mag_match.group(1)))
            atoms_remaining -= 1
            continue

        expect_moment_line = True

    return moments


def _parse_stru_site_vector_magnetic_moments(
    content: str,
) -> list[tuple[float, float, float]]:
    """Per-site 3-component mag/magmom vectors on ATOMIC_POSITIONS coordinate lines."""
    vectors: list[tuple[float, float, float]] = []
    lines = content.split("\n")

    in_atomic_positions = False
    expect_moment_line = False
    expect_natom_line = False
    atoms_remaining = 0

    for line in lines:
        stripped = line.strip()

        if stripped == "ATOMIC_POSITIONS":
            in_atomic_positions = True
            continue

        if not in_atomic_positions:
            continue

        if not stripped:
            continue

        if stripped in ("Direct", "Cartesian", "Cartesian_angstrom", "Cartesian_au"):
            continue

        if expect_moment_line:
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
            vec_match = _MAG_VECTOR_LINE.search(stripped)
            if vec_match:
                vectors.append(
                    (
                        float(vec_match.group(1)),
                        float(vec_match.group(2)),
                        float(vec_match.group(3)),
                    )
                )
            atoms_remaining -= 1
            continue

        expect_moment_line = True

    return vectors


def _vector_mag_norm(vec: tuple[float, float, float]) -> float:
    return (vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2) ** 0.5


def _parse_stru_species_level_moments(content: str) -> list[float]:
    """Parse species-level InitialMagneticMoment, expand to per-atom list.

    Format: Label / Moment / NumberOfAtoms / coords...
    Each species moment is repeated for all atoms of that species.
    """
    lines = content.split("\n")
    in_atomic_positions = False
    expect_moment_line = False
    expect_natom_line = False
    atoms_remaining = 0
    pending_species_moment: float | None = None
    collected: list[float] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "ATOMIC_POSITIONS":
            in_atomic_positions = True
            continue
        if not in_atomic_positions:
            continue
        if not stripped:
            continue
        if stripped in ("Direct", "Cartesian", "Cartesian_angstrom", "Cartesian_au"):
            continue

        if expect_moment_line:
            try:
                pending_species_moment = float(stripped)
            except ValueError:
                pending_species_moment = None
            expect_moment_line = False
            expect_natom_line = True
            continue

        if expect_natom_line:
            expect_natom_line = False
            try:
                natom = int(stripped)
            except ValueError:
                natom = 0
            atoms_remaining = natom
            if pending_species_moment is not None and natom > 0:
                collected.extend([pending_species_moment] * natom)
            pending_species_moment = None
            continue

        if atoms_remaining > 0:
            atoms_remaining -= 1
            continue

        expect_moment_line = True

    return collected


def _site_moments_for_magnetic_order(content: str) -> list[float]:
    """Moments used for FM/AFM classification.

    Priority: per-site mag/magmom on coordinate lines first,
    then species-level InitialMagneticMoment expanded to per-atom.
    """
    site = _parse_stru_site_magnetic_moments(content)
    if site:
        return site
    return _parse_stru_species_level_moments(content)


def _parse_stru_magnetic_moments(content: str) -> list[float]:
    """Backward-compatible alias."""
    return _site_moments_for_magnetic_order(content)


def _classify_magnetic_order(moments: list[float], *, min_sites: int = 2) -> str:
    """Classify initial magnetic order from site moments.

    Returns:
        ``fm`` — >= min_sites nonzero moments, all same sign
        ``afm`` — >= min_sites nonzero moments, at least one + and one -
        ``nonmagnetic`` — no nonzero moments
        ``insufficient`` — fewer than min_sites nonzero moments
    """
    nonzero = [m for m in moments if abs(m) > _MAG_EPS]
    if not nonzero:
        return "nonmagnetic"
    if len(nonzero) < min_sites:
        return "insufficient"

    n_pos = sum(1 for m in nonzero if m > 0)
    n_neg = sum(1 for m in nonzero if m < 0)

    if n_pos > 0 and n_neg > 0:
        return "afm"
    return "fm"


def _parse_lattice_constant(content: str) -> float | None:
    """Extract LATTICE_CONSTANT value from STRU."""
    match = re.search(
        r"LATTICE_CONSTANT\s*\n\s*([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)",
        content,
    )
    if not match:
        return None
    return float(match.group(1))


def _parse_lattice_parameters(content: str) -> list[float]:
    """Extract LATTICE_PARAMETERS values from STRU (may be on one or multiple lines)."""
    match = re.search(
        r"LATTICE_PARAMETERS\s*\n(.*?)(?=\n\s*(?:ATOMIC_POSITIONS|\Z))",
        content,
        re.DOTALL,
    )
    if not match:
        return []
    nums = re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", match.group(1))
    return [float(x) for x in nums]


def _parse_lattice_vectors(content: str) -> list[float] | None:
    """Extract 9 lattice vector components from STRU LATTICE_VECTORS section."""
    match = re.search(
        r"LATTICE_VECTORS\s*\n\s*([-+\d.eE\s]+)",
        content,
    )
    if not match:
        return None
    nums = re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", match.group(1))
    if len(nums) < 9:
        return None
    return [float(x) for x in nums[:9]]


def check_stru_file(
    workspace_dir: str | Path,
    *,
    filename: str,
    check: str,
    expected: str | int | None = None,
    min_sites: int = 2,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Run a structural check on an ABACUS STRU file.

    Supported checks:
    - magnetic_order: expected = 'afm' | 'fm' | 'nonmagnetic'
    - site_magmom_count_min: expected = int
    - site_vector_magmom_count_min: expected = int
    - species_count: expected = int
    - total_atoms: expected = int
    - lattice_constant_range: expected = {min, max}
    - lattice_parameters_range: expected = [{min, max}, ...]
    """
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {fpath.name}: {exc}"

    if check == "magnetic_order":
        expected_order = str(expected or "")
        moments = _site_moments_for_magnetic_order(content)
        actual = _classify_magnetic_order(moments, min_sites=min_sites)
        if actual == expected_order:
            return True, (
                f"{fpath.name}: magnetic_order={actual} "
                f"(site moments: {moments}, min_sites={min_sites})"
            )
        return False, (
            f"{fpath.name}: magnetic_order={actual}, expected {expected_order!r} "
            f"(site moments: {moments}, min_sites={min_sites})"
        )

    elif check == "site_magmom_count_min":
        site_mags = _parse_stru_site_magnetic_moments(content)
        required = int(expected or 0)
        if len(site_mags) >= required:
            return True, (
                f"{fpath.name}: {len(site_mags)} site mag/magmom lines (>={required})"
            )
        return False, (
            f"{fpath.name}: {len(site_mags)} site mag/magmom lines, expected >={required}"
        )

    elif check == "site_vector_magmom_count_min":
        vectors = _parse_stru_site_vector_magnetic_moments(content)
        nonzero = [v for v in vectors if _vector_mag_norm(v) > _MAG_EPS]
        required = int(expected or 0)
        if len(nonzero) >= required:
            return True, (
                f"{fpath.name}: {len(nonzero)} site vector mag/magmom lines "
                f"(>={required})"
            )
        return False, (
            f"{fpath.name}: {len(nonzero)} site vector mag/magmom lines, "
            f"expected >={required} (parsed vectors: {vectors})"
        )

    elif check == "species_count":
        species_section = re.search(
            r"ATOMIC_SPECIES\s*\n(.*?)(?=\n\s*(?:NUMERICAL_ORBITAL|LATTICE_CONSTANT|LATTICE_VECTORS|\Z))",
            content,
            re.DOTALL,
        )
        if not species_section:
            return False, f"{fpath.name}: ATOMIC_SPECIES section not found"
        species_lines = [
            line
            for line in species_section.group(1).strip().split("\n")
            if line.strip()
        ]
        actual_count = len(species_lines)
        if actual_count == int(expected or 0):
            return True, f"{fpath.name}: species_count={actual_count}"
        return False, f"{fpath.name}: species_count={actual_count}, expected {expected}"

    elif check == "total_atoms":
        total = 0
        lines = content.split("\n")
        in_ap = False
        # States: 'label' -> 'moment' -> 'count' -> 'coords'
        state = "label"
        atoms_remaining = 0
        for line in lines:
            s = line.strip()
            if s == "ATOMIC_POSITIONS":
                in_ap = True
                continue
            if not in_ap or not s:
                continue
            if s in ("Direct", "Cartesian", "Cartesian_angstrom", "Cartesian_au"):
                continue
            if state == "label":
                state = "moment"
            elif state == "moment":
                state = "count"
            elif state == "count":
                try:
                    atoms_remaining = int(s)
                    total += atoms_remaining
                except ValueError:
                    atoms_remaining = 0
                state = "coords"
            elif state == "coords":
                atoms_remaining -= 1
                if atoms_remaining <= 0:
                    state = "label"

        if total == int(expected or 0):
            return True, f"{fpath.name}: total_atoms={total}"
        return False, f"{fpath.name}: total_atoms={total}, expected {expected}"

    elif check == "lattice_matches":
        ref_filename = str(expected or "")
        if not ref_filename:
            return (
                False,
                "stru_file_check lattice_matches: 'expected' must be the reference STRU filename",
            )
        ref_path = _resolve_file(
            root, ref_filename, workspace_resolve=workspace_resolve
        )
        if ref_path is None:
            return False, f"no file matching {ref_filename!r} in {root}"
        try:
            ref_content = ref_path.read_text(encoding="utf-8")
        except Exception as exc:
            return False, f"failed reading {ref_path.name}: {exc}"
        vecs_a = _parse_lattice_vectors(content)
        vecs_b = _parse_lattice_vectors(ref_content)
        if vecs_a is None:
            return False, f"{fpath.name}: LATTICE_VECTORS not found"
        if vecs_b is None:
            return False, f"{ref_path.name}: LATTICE_VECTORS not found"
        diff = max(abs(a - b) for a, b in zip(vecs_a, vecs_b))
        if diff < 0.01:
            return True, (
                f"{fpath.name} matches reference {ref_path.name} "
                f"(max component diff={diff:.6f} Å)"
            )
        return False, (
            f"{fpath.name} does NOT match reference {ref_path.name} "
            f"(max component diff={diff:.4f} Å) — wrong structure selected"
        )

    elif check == "lattice_differs_from":
        other_filename = str(expected or "")
        if not other_filename:
            return (
                False,
                "stru_file_check lattice_differs_from: 'expected' must be the other STRU filename",
            )
        other_path = _resolve_file(
            root, other_filename, workspace_resolve=workspace_resolve
        )
        if other_path is None:
            return False, f"no file matching {other_filename!r} in {root}"
        try:
            other_content = other_path.read_text(encoding="utf-8")
        except Exception as exc:
            return False, f"failed reading {other_path.name}: {exc}"
        vecs_a = _parse_lattice_vectors(content)
        vecs_b = _parse_lattice_vectors(other_content)
        if vecs_a is None:
            return False, f"{fpath.name}: LATTICE_VECTORS not found"
        if vecs_b is None:
            return False, f"{other_path.name}: LATTICE_VECTORS not found"
        diff = max(abs(a - b) for a, b in zip(vecs_a, vecs_b))
        if diff > 0.01:
            return True, (
                f"{fpath.name} vs {other_path.name}: lattice vectors differ "
                f"(max component diff={diff:.4f} Å)"
            )
        return False, (
            f"{fpath.name} vs {other_path.name}: lattice vectors are identical "
            f"(max component diff={diff:.6f} Å) — structures appear to be the same phase"
        )

    elif check == "lattice_constant_range":
        val = _parse_lattice_constant(content)
        if val is None:
            return False, f"{fpath.name}: LATTICE_CONSTANT not found"
        cfg = expected if isinstance(expected, dict) else {}
        lo = float(cfg.get("min", 0))
        hi = float(cfg.get("max", 1e9))
        if lo <= val <= hi:
            return True, f"{fpath.name}: LATTICE_CONSTANT={val} in [{lo}, {hi}]"
        return False, f"{fpath.name}: LATTICE_CONSTANT={val} outside [{lo}, {hi}]"

    elif check == "lattice_parameters_range":
        params = _parse_lattice_parameters(content)
        if not params:
            return False, f"{fpath.name}: LATTICE_PARAMETERS not found"
        checks = expected if isinstance(expected, list) else []
        for i, rule in enumerate(checks):
            if i >= len(params):
                return (
                    False,
                    f"{fpath.name}: LATTICE_PARAMETERS has {len(params)} values, need {i+1}",
                )
            lo = float(rule.get("min", 0))
            hi = float(rule.get("max", 1e9))
            if not (lo <= params[i] <= hi):
                return False, (
                    f"{fpath.name}: LATTICE_PARAMETERS[{i}]={params[i]} "
                    f"outside [{lo}, {hi}]"
                )
        return True, f"{fpath.name}: LATTICE_PARAMETERS={params} all within range"

    elif check == "cubic_box_range":
        lc = _parse_lattice_constant(content)
        if lc is None:
            lc = 1.0
        vecs = _parse_lattice_vectors(content)
        if vecs is None or len(vecs) < 9:
            return False, f"{fpath.name}: LATTICE_VECTORS not found"
        lengths = [
            lc * (vecs[0] ** 2 + vecs[1] ** 2 + vecs[2] ** 2) ** 0.5,
            lc * (vecs[3] ** 2 + vecs[4] ** 2 + vecs[5] ** 2) ** 0.5,
            lc * (vecs[6] ** 2 + vecs[7] ** 2 + vecs[8] ** 2) ** 0.5,
        ]
        cfg = expected if isinstance(expected, dict) else {}
        lo = float(cfg.get("min", 0))
        hi = float(cfg.get("max", 1e9))
        if all(lo <= v <= hi for v in lengths):
            return True, (
                f"{fpath.name}: box edges {[f'{v:.2f}' for v in lengths]} "
                f"all within [{lo}, {hi}]"
            )
        return False, (
            f"{fpath.name}: box edges {[f'{v:.2f}' for v in lengths]} "
            f"not all within [{lo}, {hi}]"
        )

    else:
        return False, f"unknown stru_file_check check type: {check!r}"
