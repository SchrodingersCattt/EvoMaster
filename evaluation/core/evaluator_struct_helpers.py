"""Structure-file verify helpers imported by the verify registry."""

from __future__ import annotations

from typing import Any

from evaluation.validators.structure_general import (
    check_all_occupancy_one,
    check_min_interatomic_distance,
    check_parsable,
    check_space_group,
)
from evaluation.validators.structure_ordering import (
    check_integer_stoichiometry,
    check_replicas_distinct,
)

from .evidence import EvidenceBundle
from .schemas import ReferenceAnswer


def _get_workspace(evidence: EvidenceBundle | None) -> tuple[str | None, str | None]:
    """Extract workspace_dir from evidence, return (dir, error_msg)."""
    if evidence is None:
        return None, 'no EvidenceBundle provided'
    if not evidence.workspace_dir:
        return None, 'missing workspace_dir on evidence'
    return evidence.workspace_dir, None


def _cfg(ref: ReferenceAnswer) -> dict[str, Any]:
    return ref.value if isinstance(ref.value, dict) else {}


def check_struct_file_parsable(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_parsable(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
    )


def check_struct_file_all_occupancy_one(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_all_occupancy_one(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
        tolerance=float(cfg.get('tolerance', 1e-6)),
    )


def check_struct_file_space_group(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw = cfg.get('expected_number', cfg.get('expected', 0))
    if isinstance(raw, list):
        expected_number = [int(n) for n in raw]
    else:
        expected_number = int(raw)
    return check_space_group(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
        expected_number=expected_number,
        symprec=float(cfg.get('symprec', 0.1)),
        angle_tolerance=float(cfg.get('angle_tolerance', 5.0)),
    )


def check_struct_file_min_interatomic_distance(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_elements = cfg.get('elements')
    elements = (
        [str(item) for item in raw_elements] if isinstance(raw_elements, list) else None
    )
    return check_min_interatomic_distance(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
        min_distance_A=float(cfg.get('min_distance_A', cfg.get('expected_min_A', 0))),
        elements=elements,
    )


def check_struct_file_integer_stoichiometry(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_integer_stoichiometry(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
    )


def check_struct_file_replicas_distinct(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_replicas_distinct(
        ws,
        filename=str(cfg.get('filename', '*.cif')),
    )
