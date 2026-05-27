"""Verify-handler registrations for :class:`BinaryEvaluator`.

The verify-registry refactor moved per-verify-type checks from a giant
``if/elif`` to a registry decorated via ``@_R(...)``. The handler block was
originally inlined in ``evaluator.py`` but pushed that file past the
1000-line limit enforced by ``.pre-commit/check_file_lines.py``.

Importing this module has the side effect of populating
``BinaryEvaluator._VERIFY_REGISTRY``. ``evaluator.py`` performs that import
at module bottom; do not import this file from anywhere else.
"""

from evaluation.validators.gpumd_run_in import check_gpumd_run_in
from evaluation.validators.gromacs_top import check_gromacs_top
from evaluation.validators.vasp_incar import check_vasp_incar

from .evaluator import BinaryEvaluator
from .evaluator_struct_helpers import (
    check_struct_file_all_occupancy_one,
    check_struct_file_integer_stoichiometry,
    check_struct_file_min_interatomic_distance,
    check_struct_file_parsable,
    check_struct_file_replicas_distinct,
    check_struct_file_space_group,
)
from .evaluator_wiring import (
    _make_domain_check_handler,
    check_abacus_input_from_evidence,
    check_answer_json_numeric_from_ref,
    check_checkcif_alerts,
    check_csv_row_count_from_evidence,
    check_duration_budget,
    check_json_file_artifacts,
    check_json_file_key_values,
    check_json_file_numeric_range,
    check_json_file_schema,
    check_kpt_line_from_evidence,
    check_md_submit_structure_min_dist,
    check_molcrys_local_env_from_evidence,
    check_molcrys_slab_integrity,
    check_sc005_disorder_formulas,
    check_stru_file_from_evidence,
    check_struct_file_atom_count,
    check_struct_file_bond_angle,
    check_struct_file_bond_count,
    check_struct_file_bond_length,
    check_struct_file_bond_length_range,
    check_struct_file_bond_range,
    check_struct_file_cell_param,
    check_struct_file_charge_balance,
    check_struct_file_composition,
    check_struct_file_coordination,
    check_struct_file_count,
    check_struct_file_density,
    check_struct_file_elements_present,
    check_struct_file_formula,
    check_struct_file_layer_count,
    check_struct_file_stoichiometry_ratio,
    check_struct_file_surface_termination,
    check_text_file_contains_all_from_evidence,
    check_text_file_excludes_all_from_evidence,
    check_text_file_kpt_path_from_evidence,
    check_text_file_numeric_range_from_evidence,
    check_text_file_regex_absent_from_evidence,
    check_text_file_regex_from_evidence,
    check_token_budget,
    check_turn_budget,
)

_R = BinaryEvaluator._register_verify


@_R("exact_match")
def _h_exact_match(ctx):
    return BinaryEvaluator._check_exact_match(
        answer=ctx["answer"],
        expected=ctx["ref"].value,
        tolerance=ctx["ref"].tolerance,
    )


@_R("numerical_range")
def _h_numerical_range(ctx):
    return BinaryEvaluator._check_numerical_range(
        answer=ctx["answer"],
        expected=ctx["ref"].value,
        tolerance=ctx["ref"].tolerance,
    )


@_R("contains_all")
def _h_contains_all(ctx):
    return BinaryEvaluator._check_contains_all(
        answer=ctx["answer"],
        expected=ctx["ref"].value,
    )


@_R("no_retries", needs_ref=False)
def _h_no_retries(ctx):
    return BinaryEvaluator._check_no_retries(evidence=ctx["evidence"])


@_R("artifact_exists")
def _h_artifact_exists(ctx):
    return BinaryEvaluator._check_artifact_exists(
        evidence=ctx["evidence"],
        ref=ctx["ref"],
    )


@_R("token_budget")
def _h_token_budget(ctx):
    return check_token_budget(evidence=ctx["evidence"], expected=ctx["ref"].value)


@_R("turn_budget")
def _h_turn_budget(ctx):
    return check_turn_budget(evidence=ctx["evidence"], expected=ctx["ref"].value)


@_R("duration_budget")
def _h_duration_budget(ctx):
    return check_duration_budget(evidence=ctx["evidence"], expected=ctx["ref"].value)


@_R("call_count_range")
def _h_call_count_range(ctx):
    return BinaryEvaluator._check_call_count_range(
        evidence=ctx["evidence"],
        expected=ctx["ref"].value,
    )


@_R("molcrys_slab_molecular_integrity")
def _h_molcrys_slab(ctx):
    return check_molcrys_slab_integrity(evidence=ctx["evidence"], ref=ctx["ref"])


@_R("sc005_disorder_formulas")
def _h_sc005(ctx):
    return check_sc005_disorder_formulas(answer=ctx["answer"])


@_R("molcrys_local_env")
def _h_molcrys_env(ctx):
    return check_molcrys_local_env_from_evidence(
        evidence=ctx["evidence"],
        ref=ctx["ref"],
    )


@_R("checkcif_no_a_alerts")
def _h_checkcif(ctx):
    return check_checkcif_alerts(evidence=ctx["evidence"], ref=ctx["ref"])


# ---------------------------------------------------------------------------
# Domain-specific validators wired via the factory in evaluator_wiring.
# vasp_incar and gpumd_run_in are generated here (not in evaluator_wiring)
# to keep that file under the 1000-line limit.
# ---------------------------------------------------------------------------

check_vasp_incar_from_evidence = _make_domain_check_handler(
    "vasp_incar_check",
    check_vasp_incar,
    cfg_keys=("param", "expected", "min", "max", "atom_count", "species_index"),
)

check_gpumd_run_in_from_evidence = _make_domain_check_handler(
    "gpumd_run_in_check",
    check_gpumd_run_in,
    cfg_keys=("expected", "allowed"),
)

check_gromacs_top_from_evidence = _make_domain_check_handler(
    "gromacs_top_check",
    check_gromacs_top,
    cfg_keys=("expected", "allowed"),
)


# Bulk-register (evidence, ref) handlers
def _evidence_ref_handler(fn):
    return lambda ctx: fn(evidence=ctx["evidence"], ref=ctx["ref"])


for _name, _fn in [
    ("struct_file_atom_count", check_struct_file_atom_count),
    ("struct_file_formula", check_struct_file_formula),
    ("struct_file_elements_present", check_struct_file_elements_present),
    ("struct_file_bond_count", check_struct_file_bond_count),
    ("struct_file_bond_length", check_struct_file_bond_length),
    ("struct_file_bond_length_range", check_struct_file_bond_length_range),
    ("struct_file_bond_angle", check_struct_file_bond_angle),
    ("struct_file_cell_param", check_struct_file_cell_param),
    ("struct_file_density", check_struct_file_density),
    ("struct_file_stoichiometry_ratio", check_struct_file_stoichiometry_ratio),
    ("struct_file_charge_balance", check_struct_file_charge_balance),
    ("struct_file_coordination", check_struct_file_coordination),
    ("struct_file_layer_count", check_struct_file_layer_count),
    ("struct_file_parsable", check_struct_file_parsable),
    ("struct_file_all_occupancy_one", check_struct_file_all_occupancy_one),
    ("struct_file_integer_stoichiometry", check_struct_file_integer_stoichiometry),
    ("struct_file_replicas_distinct", check_struct_file_replicas_distinct),
    ("struct_file_space_group", check_struct_file_space_group),
    (
        "struct_file_min_interatomic_distance",
        check_struct_file_min_interatomic_distance,
    ),
    (
        "md_submit_structure_min_dist",
        check_md_submit_structure_min_dist,
    ),
    ("struct_file_count", check_struct_file_count),
    ("struct_file_surface_termination", check_struct_file_surface_termination),
    ("struct_file_composition", check_struct_file_composition),
    ("struct_file_bond_range", check_struct_file_bond_range),
    ("csv_row_count", check_csv_row_count_from_evidence),
    ("text_file_contains_all", check_text_file_contains_all_from_evidence),
    ("text_file_excludes_all", check_text_file_excludes_all_from_evidence),
    ("text_file_kpt_path", check_text_file_kpt_path_from_evidence),
    ("text_file_numeric_range", check_text_file_numeric_range_from_evidence),
    ("text_file_regex", check_text_file_regex_from_evidence),
    ("text_file_regex_absent", check_text_file_regex_absent_from_evidence),
    ("json_file_schema", check_json_file_schema),
    ("json_file_key_values", check_json_file_key_values),
    ("json_file_numeric_range", check_json_file_numeric_range),
    ("json_file_artifacts", check_json_file_artifacts),
    ("stru_file_check", check_stru_file_from_evidence),
    ("abacus_input_check", check_abacus_input_from_evidence),
    ("kpt_line_check", check_kpt_line_from_evidence),
    ("vasp_incar_check", check_vasp_incar_from_evidence),
    ("gpumd_run_in_check", check_gpumd_run_in_from_evidence),
    ("gromacs_top_check", check_gromacs_top_from_evidence),
]:
    BinaryEvaluator._VERIFY_REGISTRY[_name] = (_evidence_ref_handler(_fn), True)


@_R("answer_json_numeric")
def _h_answer_json_numeric(ctx):
    return check_answer_json_numeric_from_ref(answer=ctx["answer"], ref=ctx["ref"])


del _R, _name, _fn, _evidence_ref_handler
