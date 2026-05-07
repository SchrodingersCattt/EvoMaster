"""Structure-file verify helpers kept out of evaluator_helpers.py.

The main helper module is close to the project's 1000-line limit, so new
structure-specific wrappers live here and are imported by the verify registry.
"""

from __future__ import annotations

from typing import Any

from evaluation.validators.structure_general import (
    check_all_occupancy_one,
    check_parsable,
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
