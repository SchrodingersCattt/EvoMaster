"""Verify-handler registrations for :class:`BinaryEvaluator`.

The verify-registry refactor moved per-verify-type checks from a giant
``if/elif`` to a registry decorated via ``@_R(...)``. Handler implementations
live here so ``evaluator.py`` can focus on orchestration.

Importing this module has the side effect of populating
``BinaryEvaluator._VERIFY_REGISTRY``. ``evaluator.py`` performs that import
at module bottom; do not import this file from anywhere else.
"""

import re
from pathlib import PurePath

from evaluation.validators.dpgen_dargs import check_dpgen_dargs
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
    check_bohr_gpu_comparison_record,
    check_bohr_job_stop_record,
    check_bohr_job_upgrade_record,
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


@_R("tool_call_exists")
def _h_tool_call_exists(ctx):
    """Check that at least one tool call with the given name(s) exists."""
    evidence = ctx["evidence"]
    ref = ctx["ref"]
    if evidence is None:
        return False, "no tool call evidence available"
    value = ref.value
    if isinstance(value, str):
        names = [value]
    elif isinstance(value, list):
        names = [str(v) for v in value]
    elif isinstance(value, dict):
        names = [str(value.get("tool_name", ""))]
    else:
        return False, f"tool_call_exists: invalid ref value type: {type(value)}"
    names_lower = [n.lower() for n in names if n]
    if not names_lower:
        return False, "tool_call_exists: no tool name specified"
    found = [
        tc.tool_name
        for tc in evidence.tool_calls
        if tc.tool_name.lower() in names_lower
    ]
    if found:
        return True, f"tool call found: {found[0]} ({len(found)} call(s))"
    return False, f"no tool call matching {names} in {len(evidence.tool_calls)} calls"


@_R("tool_args_regex")
def _h_tool_args_regex(ctx):
    """Count regex matches in one argument across matching tool calls."""
    ref = ctx["ref"]
    if not ref.tool_name or not ref.tool_arg:
        return False, "tool_args_regex requires tool_name and tool_arg"
    names = [name.strip() for name in ref.tool_name.split("|") if name.strip()]
    config = ref.value
    if isinstance(config, str):
        pattern = config
        min_matches, max_matches = 1, None
    elif isinstance(config, dict):
        pattern = config.get("pattern")
        min_matches = config.get("min_matches", 1)
        max_matches = config.get("max_matches")
    else:
        return False, "tool_args_regex value must be a string or object"
    try:
        regex = re.compile(str(pattern))
        minimum = int(min_matches)
        maximum = int(max_matches) if max_matches is not None else None
    except (re.error, TypeError, ValueError) as exc:
        return False, f"invalid tool_args_regex configuration: {exc}"

    count = 0
    calls_inspected = 0
    for call in ctx["tool_calls"]:
        if call.get("tool_name") not in names:
            continue
        args = call.get("tool_args", {})
        actual = args.get(ref.tool_arg) if isinstance(args, dict) else None
        if not isinstance(actual, str):
            continue
        calls_inspected += 1
        count += sum(1 for _ in regex.finditer(actual))

    passed = count >= minimum and (maximum is None or count <= maximum)
    expected = f">={minimum}" if maximum is None else f"[{minimum},{maximum}]"
    return (
        passed,
        f"regex matches={count}, expected={expected}, "
        f"matching tool calls inspected={calls_inspected}",
    )


_SCRIPT_EXECUTOR_TEMPLATE = (
    r"(?:^|[\n;&|]\s*)(?:[^\s;&|]+/)?"
    r"(?:python(?:3(?:\.\d+)*)?|bash|sh)\s+"
    r"(?:[^\s;&|]+/)?{script}(?=$|[\s;&|])"
)
_INLINE_SCRIPT_EXECUTOR_RE = re.compile(
    r"(?:^|[\n;&|]\s*)(?:[^\s;&|]+/)?" r"(?:python(?:3(?:\.\d+)*)?|bash|sh)\s+[^\s;&|]+"
)


@_R("scripted_tool_args_regex")
def _h_scripted_tool_args_regex(ctx):
    """Accept direct commands or a written script that is subsequently executed.

    Repeated work performed inside a helper script is intentionally counted as one
    grounded execution path.  Output artifacts remain responsible for proving the
    number and semantics of the repeated operations.
    """
    ref = ctx["ref"]
    if not ref.tool_name or not ref.tool_arg:
        return False, "scripted_tool_args_regex requires tool_name and tool_arg"
    config = ref.value
    if not isinstance(config, dict):
        return False, "scripted_tool_args_regex value must be an object"
    try:
        direct_regex = re.compile(str(config.get("direct_pattern")))
        script_regex = re.compile(str(config.get("script_pattern")))
        minimum = int(config.get("min_matches", 1))
        raw_maximum = config.get("max_matches")
        maximum = int(raw_maximum) if raw_maximum is not None else None
    except (re.error, TypeError, ValueError) as exc:
        return False, f"invalid scripted_tool_args_regex configuration: {exc}"

    names = {name.strip() for name in ref.tool_name.split("|") if name.strip()}
    tool_calls = ctx["tool_calls"]
    direct_matches = 0
    inline_scripts = 0
    executor_calls: list[tuple[int, str]] = []
    written_scripts: list[tuple[int, str, str]] = []

    for index, call in enumerate(tool_calls):
        tool_name = call.get("tool_name")
        args = call.get("tool_args", {})
        if not isinstance(args, dict):
            continue
        if tool_name in names:
            command = args.get(ref.tool_arg)
            if not isinstance(command, str):
                continue
            executor_calls.append((index, command))
            # Here-doc bodies are data until an interpreter executes them.  Do not
            # count command-looking text inside them as a direct invocation.
            if "<<" not in command:
                direct_matches += sum(1 for _ in direct_regex.finditer(command))
            if script_regex.search(command) and _INLINE_SCRIPT_EXECUTOR_RE.search(
                command
            ):
                inline_scripts += 1
        elif tool_name == "Write":
            path = args.get("file_path")
            content = args.get("content")
            if (
                isinstance(path, str)
                and isinstance(content, str)
                and script_regex.search(content)
            ):
                written_scripts.append((index, path, content))

    linked_scripts = 0
    for write_index, path, _content in written_scripts:
        basename = PurePath(path.replace("\\", "/")).name
        if not basename:
            continue
        executor_regex = re.compile(
            _SCRIPT_EXECUTOR_TEMPLATE.replace("{script}", re.escape(basename))
        )
        if any(
            call_index > write_index and executor_regex.search(command)
            for call_index, command in executor_calls
        ):
            linked_scripts += 1

    count = direct_matches + inline_scripts + linked_scripts
    passed = count >= minimum and (maximum is None or count <= maximum)
    expected = f">={minimum}" if maximum is None else f"[{minimum},{maximum}]"
    return (
        passed,
        f"grounded paths={count}, expected={expected}, direct={direct_matches}, "
        f"linked_scripts={linked_scripts}, inline_scripts={inline_scripts}",
    )


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


@_R("molcrys_molecule_formulas")
def _h_molcrys_mol_formulas(ctx):
    from evaluation.core.evaluator_wiring import _cfg, _get_workspace
    from evaluation.validators.structure_molcrys import (
        check_molcrys_molecule_formulas,
    )

    ws, err = _get_workspace(ctx["evidence"])
    if err:
        return False, err
    cfg = _cfg(ctx["ref"])
    return check_molcrys_molecule_formulas(
        ws,
        filename=cfg.get("filename", "*.cif"),
        expected_formulas=list(cfg.get("expected_formulas", [])),
        all_frames=bool(cfg.get("all_frames", False)),
    )


@_R("checkcif_no_a_alerts")
def _h_checkcif(ctx):
    return check_checkcif_alerts(evidence=ctx["evidence"], ref=ctx["ref"])


# ---------------------------------------------------------------------------
# Domain-specific validators wired via the factory in evaluator_wiring.
# vasp_incar and gpumd_run_in are generated here to keep the wiring module
# focused on shared adapters.
# ---------------------------------------------------------------------------

check_vasp_incar_from_evidence = _make_domain_check_handler(
    "vasp_incar_check",
    check_vasp_incar,
    cfg_keys=(
        "param",
        "expected",
        "allowed",
        "min",
        "max",
        "atom_count",
        "species_index",
    ),
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

check_dpgen_dargs_from_evidence = _make_domain_check_handler(
    "dpgen_dargs_check",
    check_dpgen_dargs,
    cfg_keys=("kind", "strict"),
)


def check_struct_file_planarity(*, evidence, ref):
    """Run the specialized conjugated-core planarity validator."""
    from evaluation.core.evaluator_wiring import _cfg, _get_workspace
    from evaluation.validators.structure_planarity import check_planarity

    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_planarity(
        ws,
        filename=cfg.get("filename", "*.xyz"),
        max_rms_A=float(cfg.get("max_rms_A", 0.3)),
        aromatic_cc_cutoff_A=float(cfg.get("aromatic_cc_cutoff_A", 1.46)),
        min_core_atoms=int(cfg.get("min_core_atoms", 8)),
        element=str(cfg.get("element", "C")),
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
    ("struct_file_planarity", check_struct_file_planarity),
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
    ("bohr_gpu_comparison_record", check_bohr_gpu_comparison_record),
    ("bohr_job_stop_record", check_bohr_job_stop_record),
    ("bohr_job_upgrade_record", check_bohr_job_upgrade_record),
    ("json_file_key_values", check_json_file_key_values),
    ("json_file_numeric_range", check_json_file_numeric_range),
    ("json_file_artifacts", check_json_file_artifacts),
    ("stru_file_check", check_stru_file_from_evidence),
    ("abacus_input_check", check_abacus_input_from_evidence),
    ("kpt_line_check", check_kpt_line_from_evidence),
    ("vasp_incar_check", check_vasp_incar_from_evidence),
    ("gpumd_run_in_check", check_gpumd_run_in_from_evidence),
    ("gromacs_top_check", check_gromacs_top_from_evidence),
    ("dpgen_dargs_check", check_dpgen_dargs_from_evidence),
]:
    BinaryEvaluator._VERIFY_REGISTRY[_name] = (_evidence_ref_handler(_fn), True)


@_R("answer_json_numeric")
def _h_answer_json_numeric(ctx):
    return check_answer_json_numeric_from_ref(answer=ctx["answer"], ref=ctx["ref"])


del _R, _name, _fn, _evidence_ref_handler
