"""Structure-file verify helpers kept out of evaluator_helpers.py.

The main helper module is close to the project's 1000-line limit, so
structure-specific wrappers live here and are imported by the verify registry.
"""

from __future__ import annotations

from typing import Any

from evaluation.validators.structure_general import (
    check_all_occupancy_one,
    check_atom_count,
    check_bond_angle,
    check_bond_count,
    check_bond_length,
    check_bond_length_range,
    check_cell_param,
    check_charge_balance,
    check_coordination_number,
    check_file_count,
    check_formula,
    check_layer_count,
    check_min_interatomic_distance,
    check_parsable,
    check_space_group,
    check_stoichiometry_ratio,
    check_surface_termination,
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
        return None, "no EvidenceBundle provided"
    if not evidence.workspace_dir:
        return None, "missing workspace_dir on evidence"
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
        filename=str(cfg.get("filename", "*.cif")),
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
        filename=str(cfg.get("filename", "*.cif")),
        tolerance=float(cfg.get("tolerance", 1e-6)),
    )


def check_struct_file_space_group(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_space_group(
        ws,
        filename=str(cfg.get("filename", "*.cif")),
        expected_number=int(cfg.get("expected_number", cfg.get("expected", 0))),
        symprec=float(cfg.get("symprec", 0.1)),
        angle_tolerance=float(cfg.get("angle_tolerance", 5.0)),
    )


def check_struct_file_min_interatomic_distance(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_elements = cfg.get("elements")
    elements = (
        [str(item) for item in raw_elements] if isinstance(raw_elements, list) else None
    )
    return check_min_interatomic_distance(
        ws,
        filename=str(cfg.get("filename", "*.cif")),
        min_distance_A=float(cfg.get("min_distance_A", cfg.get("expected_min_A", 0))),
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
        filename=str(cfg.get("filename", "*.cif")),
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
        filename=str(cfg.get("filename", "*.cif")),
    )


def check_struct_file_atom_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_atom_count(
        ws,
        filename=cfg.get("filename", "*.cif"),
        expected=int(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
        element=str(cfg.get("element")) if cfg.get("element") else None,
    )


def check_struct_file_formula(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_formula(
        ws,
        filename=cfg.get("filename", "*.cif"),
        formula=str(cfg.get("formula", "")),
    )


def check_struct_file_bond_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_count(
        ws,
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        cutoff_A=float(cfg.get("cutoff_A", 2.0)),
        expected_count=int(cfg.get("expected_count", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
    )


def check_struct_file_bond_length(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_length(
        ws,
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        cutoff_A=float(cfg.get("cutoff_A", 3.0)),
        expected=float(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
    )


def check_struct_file_bond_length_range(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_length_range(
        ws,
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        cutoff_A=float(cfg.get("cutoff_A", 3.0)),
        expected_min=float(cfg.get("expected_min", 0.0)),
        expected_max=float(cfg.get("expected_max", 0.0)),
    )


def check_struct_file_bond_angle(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)

    def _opt(name: str) -> float | None:
        val = cfg.get(name)
        return None if val is None else float(val)

    return check_bond_angle(
        ws,
        filename=cfg.get("filename", "*.cif"),
        triplet=list(cfg.get("triplet", [])),
        expected_deg=float(cfg.get("expected_deg", 0)),
        tolerance_deg=float(cfg.get("tolerance_deg", 5.0)),
        cutoff_A=float(cfg.get("cutoff_A", 3.0)),
        cutoff_a_b_A=_opt("cutoff_a_b_A"),
        cutoff_c_b_A=_opt("cutoff_c_b_A"),
        cutoff_a_b_min_A=float(cfg.get("cutoff_a_b_min_A", 0.0)),
        cutoff_c_b_min_A=float(cfg.get("cutoff_c_b_min_A", 0.0)),
    )


def check_struct_file_cell_param(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_cell_param(
        ws,
        filename=cfg.get("filename", "*.cif"),
        param=str(cfg.get("param", "alpha")),
        expected=float(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
    )


def check_struct_file_stoichiometry_ratio(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_stoichiometry_ratio(
        ws,
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        expected_ratio=float(cfg.get("expected_ratio", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
    )


def check_struct_file_charge_balance(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_charge_balance(
        ws,
        filename=cfg.get("filename", "*.cif"),
        oxidation_states={
            str(k): int(v) for k, v in cfg.get("oxidation_states", {}).items()
        },
        tolerance=float(cfg.get("tolerance", 0.01)),
    )


def check_struct_file_coordination(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_coordination_number(
        ws,
        filename=cfg.get("filename", "*.cif"),
        center_element=str(cfg.get("center_element", "")),
        expected=int(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
        cutoff_A=float(cfg.get("cutoff_A", 2.5)),
    )


def check_struct_file_layer_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    if "layer_tol_A" in cfg:
        layer_tol = float(cfg["layer_tol_A"])
    elif "gap_threshold_A" in cfg:
        # Legacy key from older rubrics; now interpreted as plane-merge tolerance (Å).
        layer_tol = float(cfg["gap_threshold_A"])
    else:
        layer_tol = 0.25
    return check_layer_count(
        ws,
        filename=cfg.get("filename", "*.cif"),
        expected=int(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
        axis=str(cfg.get("axis", "z")),
        layer_tol_A=layer_tol,
        element=str(cfg.get("element")) if cfg.get("element") else None,
    )


def check_struct_file_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_file_count(
        ws,
        pattern=cfg.get("pattern", "*.cif"),
        expected=int(cfg.get("expected", 0)),
        tolerance=int(cfg.get("tolerance", 0)),
    )


def check_checkcif_alerts(
    *,
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Evaluate checkcif_no_a_alerts: find CIF in workspace, run checkCIF.

    ref.value must be a dict with optional keys:
      - filename (str, default '*.cif'): glob pattern to find the CIF
      - max_a_alerts (int, default 0): maximum allowed A-level alerts
    """
    from evaluation.validators.checkcif import check_checkcif_no_a_alerts

    workspace_dir, _ = _get_workspace(evidence)
    if workspace_dir is None:
        return False, "no workspace directory available in evidence"

    val = ref.value or {}
    filename = val.get("filename", "*.cif") if isinstance(val, dict) else "*.cif"
    max_a_alerts = int(val.get("max_a_alerts", 0)) if isinstance(val, dict) else 0

    return check_checkcif_no_a_alerts(
        workspace_dir,
        filename=filename,
        max_a_alerts=max_a_alerts,
    )


def check_struct_file_surface_termination(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_surface_termination(
        ws,
        filename=cfg.get("filename", "*.cif"),
        element=str(cfg.get("element", "")),
        axis=str(cfg.get("axis", "z")),
        side=str(cfg.get("side", "top")),
        layer_tol_A=float(cfg.get("layer_tol_A", 0.5)),
    )
