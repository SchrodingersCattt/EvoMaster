"""Schemas for MATTER v5 evaluation workflows.

Current v5 schema changes:
- Rubric class REMOVED — binary scoring needs no rubric
- ScoringCheckItem: optional weight field added (default 1.0), dimension renamed to axis
- New literals: CapabilityLiteral, DomainLiteral, AxisLiteral
- QuestionItem: added capability/domain/required_tools/optional_tools; removed level/rubric_id/touchpoints/repeat_override
- New CriterionResult model (per-criterion pass/fail + reason)
- EvalRunRecord: binary pass counts + weighted scores (axis_weights from config applied)
- EvaluationSummary: pass-rate oriented with AxisPassRates + weighted equivalents
- QuestionBank: no longer requires rubric field
- EvalConfig: axis_weights; ``include_slices`` (OR-of capability + optional domains + optional tags)

Scoring model:
- LLM / deterministic verifiers produce binary (pass/fail) verdicts per checklist item
- Each checklist item has optional weight (default 1.0)
- Axis score = Σ(pass_i * weight_i) / Σ(weight_i) for items in that axis
- Overall score = Σ(axis_weight_a * axis_score_a) / Σ(active_axis_weight_a)
- Raw pass counts preserved for backward compatibility and debugging
"""

import re
from datetime import datetime, timezone
from typing import Any, Literal

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, Field, field_validator, model_validator

from evaluation.core.question_tags import QuestionTag

# ---------------------------------------------------------------------------
# Literal type aliases
# ---------------------------------------------------------------------------

ModeLiteral = Literal["direct", "planner"]
WorkspaceResolveLiteral = Literal["recursive", "root"]

VerifyLiteral = Literal[
    "exact_match",
    "numerical_range",
    "contains_all",
    "tool_args_match",
    "tool_args_regex",
    "scripted_tool_args_regex",
    "tool_observation_field",
    "event_type_called",
    "call_count_range",
    "no_retries",
    "artifact_exists",
    "token_budget",
    "turn_budget",
    "duration_budget",
    "molcrys_slab_molecular_integrity",
    "molcrys_local_env",
    "molcrys_molecule_formulas",
    "sc005_disorder_formulas",
    "llm_binary_judge",
    # pymatgen-backed structure-file checks
    "struct_file_atom_count",
    "struct_file_formula",
    "struct_file_elements_present",
    "struct_file_bond_count",
    "struct_file_bond_length",
    "struct_file_bond_length_range",
    "struct_file_bond_angle",
    "struct_file_cell_param",
    "struct_file_density",
    "struct_file_stoichiometry_ratio",
    "struct_file_charge_balance",
    "struct_file_coordination",
    "struct_file_layer_count",
    "struct_file_planarity",
    "struct_file_parsable",
    "struct_file_all_occupancy_one",
    "struct_file_space_group",
    "struct_file_min_interatomic_distance",
    "md_submit_structure_min_dist",
    # file-system check (no pymatgen needed)
    "struct_file_count",
    # surface termination check
    "struct_file_surface_termination",
    "struct_file_integer_stoichiometry",
    "struct_file_replicas_distinct",
    # composition & bond-range checks
    "struct_file_composition",
    "struct_file_bond_range",
    # IUCr checkCIF web service (single-crystal XRD validation)
    "checkcif_no_a_alerts",
    # plain-text file checks
    "text_file_contains_all",
    "text_file_excludes_all",
    "text_file_kpt_path",
    "text_file_numeric_range",
    "text_file_regex",
    "text_file_regex_absent",
    # deterministic numeric check on a JSON block emitted in the agent's answer
    "answer_json_numeric",
    # JSON file checks
    "json_file_schema",
    "bohr_gpu_comparison_record",
    "bohr_cli_operation_invoked",
    "bohr_job_monitor_execution",
    "bohr_job_stop_execution",
    "bohr_job_upgrade_execution",
    "bohr_parameter_sweep_execution",
    "bohr_parameter_sweep_record",
    "bohr_job_stop_record",
    "bohr_job_upgrade_record",
    "json_file_key_values",
    "json_file_numeric_range",
    "json_file_artifacts",
    # STRU file checks
    "stru_file_check",
    # ABACUS INPUT resolution checks
    "abacus_input_check",
    # ABACUS KPT Line-mode checks
    "kpt_line_check",
    # VASP INCAR semantic checks
    "vasp_incar_check",
    # GPUMD run.in semantic checks
    "gpumd_run_in_check",
    # GROMACS topology semantic checks
    "gromacs_top_check",
    # DP-GEN official dargs schema checks
    "dpgen_dargs_check",
    # CSV row count check
    "csv_row_count",
    # Tool call existence check
    "tool_call_exists",
]

AxisLiteral = Literal["correctness", "grounding", "efficiency"]

CapabilityLiteral = Literal[
    "structure_construction",
    "structure_retrieval",
    "scientific_analysis",
    "workflow_orchestration",
    "execution_contract",
    "data_diagnosis",
    "batch_processing",
    "safety_refusal",
    "input_generation",
]

DomainLiteral = Literal[
    "battery",
    "catalysis",
    "polymer",
    "alloy",
    "semiconductor",
    "agnostic",
]

ScopeLiteral = Literal["platform", "knowledge"]

GENERIC_PROCESS_TAGS = {
    "workflow",
    "workflow_acceleration",
    "workflow_closure",
    "loop_oriented",
    "plotting",
    "structure_build",
}

CANONICAL_TAG_ALIASES = {
    "HEA": "hea",
    "SrTiO3": "srtio3",
    "srti03": "srtio3",
    "Al2O3": "al2o3",
    "Li2O": "li2o",
    "MgO": "mgo",
    "CeO2": "ceo2",
    "MoS2": "mos2",
    "hBN": "hbn",
    "DACMOR": "dacmor",
    "Ag111": "ag111",
    "Si100": "si100",
    "CuCrZr": "cucrzr",
}


# ---------------------------------------------------------------------------
# Shared small models
# ---------------------------------------------------------------------------


class DataFileRef(BaseModel):
    """Reference to a concrete input file used by a question."""

    key: str
    path: str
    oss_url: str = ""
    description: str = ""

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("data file path cannot be empty")
        return value


class ReferenceAnswer(BaseModel):
    """Ground-truth value used by checklist scoring."""

    key: str
    value: Any
    tolerance: float | None = None
    unit: str = ""
    tool_name: str | None = None
    tool_arg: str | None = None
    workspace_resolve: WorkspaceResolveLiteral | None = Field(
        default=None,
        description=(
            "Where to resolve plain filenames for artifact_exists / text_file_* checks. "
            'None or "recursive" = match under workspace by basename (legacy). '
            '"root" = only a direct child of workspace_dir (exact path).'
        ),
    )

    @field_validator("tolerance")
    @classmethod
    def _validate_tolerance(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("tolerance must be >= 0")
        return value


# ---------------------------------------------------------------------------
# v5 core scoring models
# ---------------------------------------------------------------------------


class ScoringCheckItem(BaseModel):
    """One verifiable scoring criterion (v5: binary with optional weight).

    Every criterion produces a pass/fail verdict and is attached to one scoring
    axis: correctness, grounding, or efficiency.
    Weights are applied during aggregation; default weight is 1.0 if not specified.
    """

    id: str
    criterion: str
    axis: AxisLiteral = Field(
        default="correctness",
        description=(
            "Which scoring axis this criterion belongs to: "
            "'correctness' (is the answer right?), "
            "'grounding' (did it use the right tools/sources?), "
            "'efficiency' (was the process efficient?)."
        ),
    )
    verify: VerifyLiteral
    weight: float = Field(
        default=1.0,
        ge=0.0,
        description="Optional weight for this criterion in axis/overall score calculation. Default 1.0.",
    )


class CriterionResult(BaseModel):
    """Per-criterion pass/fail result stored inside EvalRunRecord."""

    criterion_id: str
    axis: AxisLiteral
    passed: bool
    reason: str = ""  # one-sentence evidence / explanation
    verify_method: str = ""  # which verifier produced this result


# ---------------------------------------------------------------------------
# v5 Question model
# ---------------------------------------------------------------------------


class QuestionItem(BaseModel):
    """Single MATTER v5 question entry.

    Required structure for one runnable question in the v5 bank format.
    """

    id: str
    capability: CapabilityLiteral
    domain: DomainLiteral
    scope: ScopeLiteral = "knowledge"
    intent: str
    human_prompt_seed: str
    tags: list[QuestionTag] = Field(default_factory=list)
    priority: str | None = Field(
        default=None,
        description='Gate priority. "P0" = regression gate (run first, block on regression).',
    )
    data_files: list[DataFileRef] = Field(default_factory=list)
    reference_answers: list[ReferenceAnswer] = Field(default_factory=list)
    scoring_checklist: list[ScoringCheckItem] = Field(default_factory=list)
    inject_bohrium_failure: bool = Field(
        default=False,
        description="When true, eval runner patches BohriumTool._submit to always return inject_failure_message.",
    )
    inject_failure_message: str = Field(
        default="job/create failed: Insufficient account balance, please recharge.",
        description="Error message returned by patched Bohrium submit.",
    )

    @field_validator("tags", mode="before")
    @classmethod
    def _validate_tags_before(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("tags must be a list")
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw_tag in value:
            tag = str(raw_tag).strip()
            if not tag:
                raise ValueError("tags must not contain empty strings")
            canonical = CANONICAL_TAG_ALIASES.get(tag)
            if canonical is not None:
                raise ValueError(
                    f"tag {tag!r} is not canonical; use canonical tag {canonical!r}"
                )
            if tag in GENERIC_PROCESS_TAGS:
                raise ValueError(
                    f"tag {tag!r} is a generic process tag; use a topic/tool/method tag instead"
                )
            if tag in seen:
                raise ValueError(f"tags must be unique within a question: {tag!r}")
            seen.add(tag)
            cleaned.append(tag)
        return cleaned

    @model_validator(mode="after")
    def _validate_scoring_contract(self) -> "QuestionItem":
        tag_values = {t.value for t in self.tags}
        if self.capability in tag_values:
            raise ValueError(
                f"tag {self.capability!r} must not repeat question capability"
            )
        if self.domain in tag_values:
            raise ValueError(f"tag {self.domain!r} must not repeat question domain")
        if not self.scoring_checklist:
            raise ValueError(
                "question must include at least one scoring_checklist entry"
            )
        # For deterministic check types that need a reference answer, verify it exists.
        ref_keys = {item.key for item in self.reference_answers}
        _needs_ref = {
            "exact_match",
            "numerical_range",
            "contains_all",
            "tool_args_match",
            "tool_args_regex",
            "scripted_tool_args_regex",
            "tool_observation_field",
            "event_type_called",
            "call_count_range",
            "duration_budget",
            "turn_budget",
            "molcrys_slab_molecular_integrity",
            "molcrys_local_env",
            "molcrys_molecule_formulas",
            "struct_file_parsable",
            "struct_file_all_occupancy_one",
            "struct_file_space_group",
            "struct_file_min_interatomic_distance",
            "md_submit_structure_min_dist",
            "text_file_contains_all",
            "text_file_excludes_all",
            "text_file_kpt_path",
            "text_file_numeric_range",
            "text_file_regex",
            "answer_json_numeric",
            "json_file_schema",
            "bohr_gpu_comparison_record",
            "bohr_cli_operation_invoked",
            "bohr_job_monitor_execution",
            "bohr_job_stop_execution",
            "bohr_job_upgrade_execution",
            "bohr_parameter_sweep_record",
            "bohr_job_stop_record",
            "bohr_job_upgrade_record",
            "json_file_numeric_range",
            "json_file_artifacts",
            "stru_file_check",
            "abacus_input_check",
            "kpt_line_check",
            "vasp_incar_check",
            "gpumd_run_in_check",
            "gromacs_top_check",
            "csv_row_count",
            "tool_call_exists",
        }
        for item in self.scoring_checklist:
            if item.verify in _needs_ref and item.id not in ref_keys:
                raise ValueError(
                    f"scoring_checklist item '{item.id}' (verify={item.verify}) "
                    "requires a matching reference_answers entry with the same key"
                )
        refs_by_key = {item.key: item for item in self.reference_answers}
        for item in self.scoring_checklist:
            if item.verify != "json_file_schema":
                continue
            value = refs_by_key[item.id].value
            if not isinstance(value, dict):
                raise ValueError(
                    f"json_file_schema reference '{item.id}' must be an object"
                )
            unknown_keys = set(value) - {"filename", "schema"}
            if unknown_keys:
                raise ValueError(
                    f"json_file_schema reference '{item.id}' has unsupported keys: "
                    f"{sorted(unknown_keys)}"
                )
            filename = value.get("filename")
            if not isinstance(filename, str) or not filename:
                raise ValueError(
                    f"json_file_schema reference '{item.id}' requires filename"
                )
            schema = value.get("schema")
            if not isinstance(schema, (dict, bool)):
                raise ValueError(
                    f"json_file_schema reference '{item.id}' requires a JSON Schema"
                )
            validator_cls = validator_for(schema)
            try:
                validator_cls.check_schema(schema)
            except SchemaError as exc:
                raise ValueError(
                    f"json_file_schema reference '{item.id}' has an invalid "
                    f"JSON Schema: {exc.message}"
                ) from exc
        for item in self.scoring_checklist:
            if item.verify != "bohr_gpu_comparison_record":
                continue
            value = refs_by_key[item.id].value
            if not isinstance(value, dict):
                raise ValueError(
                    f"bohr_gpu_comparison_record reference '{item.id}' must be an object"
                )
            unknown_keys = set(value) - {"filename"}
            if unknown_keys:
                raise ValueError(
                    f"bohr_gpu_comparison_record reference '{item.id}' has unsupported "
                    f"keys: {sorted(unknown_keys)}"
                )
            filename = value.get("filename")
            if not isinstance(filename, str) or not filename:
                raise ValueError(
                    f"bohr_gpu_comparison_record reference '{item.id}' requires filename"
                )
        for item in self.scoring_checklist:
            if item.verify != "bohr_parameter_sweep_record":
                continue
            value = refs_by_key[item.id].value
            if not isinstance(value, dict):
                raise ValueError(
                    f"bohr_parameter_sweep_record reference '{item.id}' must be an object"
                )
            unknown_keys = set(value) - {"filename"}
            if unknown_keys:
                raise ValueError(
                    f"bohr_parameter_sweep_record reference '{item.id}' has unsupported "
                    f"keys: {sorted(unknown_keys)}"
                )
            filename = value.get("filename")
            if not isinstance(filename, str) or not filename:
                raise ValueError(
                    f"bohr_parameter_sweep_record reference '{item.id}' requires filename"
                )
        for item in self.scoring_checklist:
            if item.verify != "bohr_job_monitor_execution":
                continue
            value = refs_by_key[item.id].value
            if not isinstance(value, dict):
                raise ValueError(
                    f"bohr_job_monitor_execution reference '{item.id}' "
                    "must be an object"
                )
            expected_keys = {
                "filename",
                "log_filename",
                "image",
                "machine_type",
                "command",
            }
            unknown_keys = set(value) - expected_keys
            if unknown_keys:
                raise ValueError(
                    f"bohr_job_monitor_execution reference '{item.id}' has "
                    f"unsupported keys: {sorted(unknown_keys)}"
                )
            missing_keys = [
                key
                for key in sorted(expected_keys)
                if not isinstance(value.get(key), str) or not value[key]
            ]
            if missing_keys:
                raise ValueError(
                    f"bohr_job_monitor_execution reference '{item.id}' requires "
                    f"non-empty string values for: {missing_keys}"
                )
        for item in self.scoring_checklist:
            if item.verify != "bohr_parameter_sweep_execution":
                continue
            value = refs_by_key[item.id].value
            if not isinstance(value, dict):
                raise ValueError(
                    f"bohr_parameter_sweep_execution reference '{item.id}' "
                    "must be an object"
                )
            unknown_keys = set(value) - {"filename"}
            if unknown_keys:
                raise ValueError(
                    f"bohr_parameter_sweep_execution reference '{item.id}' has "
                    f"unsupported keys: {sorted(unknown_keys)}"
                )
            filename = value.get("filename")
            if not isinstance(filename, str) or not filename:
                raise ValueError(
                    f"bohr_parameter_sweep_execution reference '{item.id}' "
                    "requires filename"
                )
        for item in self.scoring_checklist:
            if item.verify not in {
                "bohr_job_stop_execution",
                "bohr_job_stop_record",
            }:
                continue
            value = refs_by_key[item.id].value
            if not isinstance(value, dict):
                raise ValueError(
                    f"{item.verify} reference '{item.id}' must be an object"
                )
            expected_keys = {
                "filename",
                "image",
                "machine_type",
                "command",
                "job_name_prefix",
            }
            unknown_keys = set(value) - expected_keys
            if unknown_keys:
                raise ValueError(
                    f"{item.verify} reference '{item.id}' has unsupported "
                    f"keys: {sorted(unknown_keys)}"
                )
            missing_keys = [
                key
                for key in sorted(expected_keys)
                if not isinstance(value.get(key), str) or not value[key]
            ]
            if missing_keys:
                raise ValueError(
                    f"{item.verify} reference '{item.id}' requires non-empty "
                    f"string values for: {missing_keys}"
                )
        for item in self.scoring_checklist:
            if item.verify not in {
                "bohr_job_upgrade_execution",
                "bohr_job_upgrade_record",
            }:
                continue
            value = refs_by_key[item.id].value
            if not isinstance(value, dict):
                raise ValueError(
                    f"{item.verify} reference '{item.id}' must be an object"
                )
            expected_keys = {
                "filename",
                "seed_id",
                "source_machine_pattern",
                "target_machine_pattern",
                "image",
                "command",
            }
            unknown_keys = set(value) - expected_keys
            if unknown_keys:
                raise ValueError(
                    f"{item.verify} reference '{item.id}' has unsupported "
                    f"keys: {sorted(unknown_keys)}"
                )
            string_keys = expected_keys - {"seed_id"}
            missing_keys = [
                key
                for key in sorted(string_keys)
                if not isinstance(value.get(key), str) or not value[key]
            ]
            if (
                missing_keys
                or type(value.get("seed_id")) is not int
                or value["seed_id"] <= 0
            ):
                raise ValueError(
                    f"{item.verify} reference '{item.id}' requires a positive "
                    f"seed_id and non-empty strings for: {sorted(string_keys)}"
                )
            for key in ("source_machine_pattern", "target_machine_pattern"):
                try:
                    re.compile(value[key])
                except re.error as exc:
                    raise ValueError(
                        f"{item.verify} reference '{item.id}' has invalid "
                        f"{key}: {exc}"
                    ) from exc
        for item in self.scoring_checklist:
            if item.verify != "tool_args_regex":
                continue
            ref = refs_by_key[item.id]
            if not ref.tool_name or not ref.tool_arg:
                raise ValueError(
                    f"tool_args_regex reference '{item.id}' requires "
                    "tool_name and tool_arg"
                )
            config = ref.value
            if isinstance(config, str):
                pattern = config
                min_matches, max_matches = 1, None
            elif isinstance(config, dict):
                unknown = set(config) - {
                    "pattern",
                    "min_matches",
                    "max_matches",
                }
                if unknown:
                    raise ValueError(
                        f"tool_args_regex reference '{item.id}' has unsupported "
                        f"keys: {sorted(unknown)}"
                    )
                pattern = config.get("pattern")
                min_matches = config.get("min_matches", 1)
                max_matches = config.get("max_matches")
            else:
                raise ValueError(
                    f"tool_args_regex reference '{item.id}' must be a regex "
                    "string or configuration object"
                )
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(
                    f"tool_args_regex reference '{item.id}' requires pattern"
                )
            if type(min_matches) is not int or min_matches < 1:
                raise ValueError(
                    f"tool_args_regex reference '{item.id}' requires "
                    "min_matches >= 1"
                )
            if max_matches is not None and (
                type(max_matches) is not int or max_matches < min_matches
            ):
                raise ValueError(
                    f"tool_args_regex reference '{item.id}' requires "
                    "max_matches >= min_matches"
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"tool_args_regex reference '{item.id}' has invalid regex: {exc}"
                ) from exc
        for item in self.scoring_checklist:
            if item.verify != "scripted_tool_args_regex":
                continue
            ref = refs_by_key[item.id]
            if not ref.tool_name or not ref.tool_arg:
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' requires "
                    "tool_name and tool_arg"
                )
            config = ref.value
            if not isinstance(config, dict):
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' must be an object"
                )
            unknown = set(config) - {
                "direct_pattern",
                "script_pattern",
                "min_matches",
                "max_matches",
            }
            if unknown:
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' has unsupported "
                    f"keys: {sorted(unknown)}"
                )
            direct_pattern = config.get("direct_pattern")
            script_pattern = config.get("script_pattern")
            min_matches = config.get("min_matches", 1)
            max_matches = config.get("max_matches")
            if not isinstance(direct_pattern, str) or not direct_pattern:
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' requires "
                    "direct_pattern"
                )
            if not isinstance(script_pattern, str) or not script_pattern:
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' requires "
                    "script_pattern"
                )
            if type(min_matches) is not int or min_matches < 1:
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' requires "
                    "min_matches >= 1"
                )
            if max_matches is not None and (
                type(max_matches) is not int or max_matches < min_matches
            ):
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' requires "
                    "max_matches >= min_matches"
                )
            try:
                re.compile(direct_pattern)
                re.compile(script_pattern)
            except re.error as exc:
                raise ValueError(
                    f"scripted_tool_args_regex reference '{item.id}' has invalid "
                    f"regex: {exc}"
                ) from exc
        for item in self.scoring_checklist:
            if item.verify != "bohr_cli_operation_invoked":
                continue
            config = refs_by_key[item.id].value
            if not isinstance(config, dict):
                raise ValueError(
                    f"bohr_cli_operation_invoked reference '{item.id}' must be an object"
                )
            unknown = set(config) - {"operations", "min_matches", "require_ok"}
            if unknown:
                raise ValueError(
                    f"bohr_cli_operation_invoked reference '{item.id}' has unsupported "
                    f"keys: {sorted(unknown)}"
                )
            operations = config.get("operations")
            if isinstance(operations, str):
                operations = [operations]
            if (
                not isinstance(operations, list)
                or not operations
                or not all(isinstance(op, str) and op.strip() for op in operations)
            ):
                raise ValueError(
                    f"bohr_cli_operation_invoked reference '{item.id}' requires "
                    "a non-empty 'operations' list of operation names"
                )
            min_matches = config.get("min_matches", 1)
            if type(min_matches) is not int or min_matches < 1:
                raise ValueError(
                    f"bohr_cli_operation_invoked reference '{item.id}' requires "
                    "min_matches >= 1"
                )
        # Safety questions (capability='safety_refusal') may skip reference_answers
        if self.capability != "safety_refusal" and not self.reference_answers:
            raise ValueError("non-safety questions must include reference_answers")
        return self


# ---------------------------------------------------------------------------
# v5 QuestionBank
# ---------------------------------------------------------------------------


class QuestionBank(BaseModel):
    """Question bank file model (v5 format)."""

    version: str = "v5"
    capability: CapabilityLiteral | None = None
    domain: DomainLiteral | None = None
    questions: list[QuestionItem]

    @model_validator(mode="after")
    def _validate_questions(self) -> "QuestionBank":
        if not self.questions:
            raise ValueError("questions cannot be empty")
        if self.capability is None:
            raise ValueError("top-level capability is required for every bank")
        mismatched_capabilities = sorted(
            q.id for q in self.questions if q.capability != self.capability
        )
        if mismatched_capabilities:
            raise ValueError(
                "top-level capability must match every question capability; "
                f"mismatched question ids: {mismatched_capabilities}"
            )
        if self.domain is None:
            raise ValueError("top-level domain is required for every bank")
        mismatched_domains = sorted(
            q.id for q in self.questions if q.domain != self.domain
        )
        if mismatched_domains:
            raise ValueError(
                "top-level domain must match every question domain; "
                f"mismatched question ids: {mismatched_domains}"
            )
        return self


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class LLMRuntimeConfig(BaseModel):
    """LLM runtime config for simulator/evaluator."""

    provider: Literal["openai", "anthropic", "deepseek", "openrouter"]
    model: str
    api_key: str
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout: int = 180


class CapabilitySlice(BaseModel):
    """One OR-branch in ``include_slices``: capability plus optional domain/tag filters."""

    capability: str | None = None
    domains: list[str] | None = None
    tags: list[str] | None = None
    scope: str | None = None

    @field_validator("capability")
    @classmethod
    def _capability_normalize(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped if stripped else None

    @field_validator("domains")
    @classmethod
    def _domains_non_empty_when_set(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not value:
            raise ValueError("domains must be omitted or a non-empty list")
        return value

    @field_validator("tags")
    @classmethod
    def _tags_non_empty_when_set(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("tags must be omitted or a non-empty list")
        cleaned = [t.strip() for t in value]
        if any(not t for t in cleaned):
            raise ValueError("tag entries cannot be empty")
        return cleaned


class EvalConfig(BaseModel):
    """Top-level evaluation config."""

    k: int = 1
    exp: ModeLiteral = Field(
        default="direct",
        description=(
            "Experiment / mode name passed to ``load_exp_config()``. "
            "Maps to ``matmaster/exps/{exp}.toml``."
        ),
    )
    question_bank_dir: str = "evaluation/question_bank"
    output_dir: str = "runs/mat_master_eval"
    run_label: str = "matter_eval"
    random_seed: int = 7
    use_seed_prompt: bool = True
    max_workers: int = 1
    mat_config_path: str = "configs/mat_master/config.yaml"
    empty_completion_max_retries: int = Field(
        default=1,
        ge=0,
        description=(
            "Re-run a task when the kernel reports completed/natural with no tools and "
            "an empty answer (transient empty LLM stream). Total attempts = 1 + this value."
        ),
    )
    simulator_llm: LLMRuntimeConfig | None = None
    evaluator_llm: LLMRuntimeConfig | None = None
    include_slices: list[CapabilitySlice] | None = None
    include_question_ids: list[str] | None = None
    exclude_question_ids: list[str] | None = None

    # Axis weights for aggregation (default 1.0 each, normalized during calculation)
    axis_weights: dict[AxisLiteral, float] = Field(
        default_factory=lambda: {
            "correctness": 1.0,
            "grounding": 1.0,
            "efficiency": 1.0,
        },
        description="Relative weights for correctness, grounding, efficiency axes. Will be normalized.",
    )

    @field_validator("k")
    @classmethod
    def _validate_k(cls, value: int) -> int:
        if value < 1:
            raise ValueError("k must be >= 1")
        return value


# ---------------------------------------------------------------------------
# Run record models
# ---------------------------------------------------------------------------


class SafetyVetoRecord(BaseModel):
    """Safety refusal verdict for a single run."""

    triggered: bool = False
    reason: str = ""
    risk_not_detected: bool = True
    detail_non_leakage: bool = True
    safe_redirection: bool = True


class TokenUsageRecord(BaseModel):
    """Serialisable token usage summary stored in EvalRunRecord."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens_effective: int = 0


class EvalRunRecord(BaseModel):
    """Atomic run record: one question, one mode, one repeat."""

    question_id: str
    capability: str = ""  # mirrors QuestionItem.capability
    domain: str = ""  # mirrors QuestionItem.domain
    mode: ModeLiteral
    repeat_idx: int
    prompt: str
    answer: str
    run_status: str

    # v5: binary pass counts
    criteria_results: dict[str, CriterionResult] = Field(
        default_factory=dict,
        description="Mapping of criterion_id -> CriterionResult (pass/fail + reason)",
    )
    passed_count: int = 0
    total_count: int = 0
    correctness_passed: int = 0
    correctness_total: int = 0
    grounding_passed: int = 0
    grounding_total: int = 0
    efficiency_passed: int = 0
    efficiency_total: int = 0

    # v5+: weighted scores (axis_weights from config applied)
    correctness_weighted_score: float = 0.0
    grounding_weighted_score: float = 0.0
    efficiency_weighted_score: float = 0.0
    overall_weighted_score: float = 0.0

    # Meta
    model_name: str | None = None
    duration_ms: int = Field(
        default=0,
        description="Wall-clock milliseconds for the agent run (mat task).",
    )
    token_usage: TokenUsageRecord = Field(default_factory=TokenUsageRecord)
    per_call_usage: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-LLM-call usage breakdown (root + subagent + compaction). Each "
            "item: call_index, spawn_id, kind, model, usage (scalar token dict "
            "incl. cache_read/cache_write/reasoning when reported)."
        ),
    )
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    safety_veto: SafetyVetoRecord = Field(default_factory=SafetyVetoRecord)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # raw_result kept for debugging; not used in aggregation
    raw_result: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# v5 summary models
# ---------------------------------------------------------------------------


class AxisPassRates(BaseModel):
    """Pass counts for each axis within a group."""

    correctness: tuple[int, int] = (0, 0)  # (passed, total)
    grounding: tuple[int, int] = (0, 0)
    efficiency: tuple[int, int] = (0, 0)
    overall: tuple[int, int] = (0, 0)

    def pass_rate(self, axis: str = "overall") -> float:
        pair = getattr(self, axis, self.overall)
        passed, total = pair
        return passed / total if total > 0 else 0.0

    def fmt(self, axis: str = "overall") -> str:
        pair = getattr(self, axis, self.overall)
        passed, total = pair
        pct = f"{100 * passed / total:.1f}%" if total > 0 else "—"
        return f"{passed}/{total} ({pct})"


class QuestionPassRate(BaseModel):
    """Per-question pass rate summary."""

    question_id: str
    capability: str
    domain: str
    runs: int = 0
    overall: tuple[int, int] = (0, 0)
    correctness: tuple[int, int] = (0, 0)
    grounding: tuple[int, int] = (0, 0)
    efficiency: tuple[int, int] = (0, 0)
    safety_veto_count: int = 0


class EvaluationSummary(BaseModel):
    """Aggregated result object (v5+): both raw pass-rates and weighted scores.

    Raw pass-rate fields (backward compatible):
    - total_criteria, total_passed, pass_rate
    - by_capability/domain/question/mode/model: AxisPassRates with integer tuples

    Weighted score fields (new):
    - weighted_pass_rate, weighted_by_* (computed using axis_weights from config)
    """

    total_runs: int
    total_criteria: int = 0
    total_passed: int = 0
    pass_rate: float = 0.0

    # Weighted equivalents (v5+)
    weighted_pass_rate: float = 0.0

    by_capability: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_domain: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_question: dict[str, QuestionPassRate] = Field(default_factory=dict)
    by_mode: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_model: dict[str, AxisPassRates] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# HumanSimulator schemas
# ---------------------------------------------------------------------------


class ExpectedResult(BaseModel):
    """One expected numerical result for scoring."""

    key: str
    value: float
    tolerance: float
    unit: str = ""

    @field_validator("tolerance")
    @classmethod
    def _validate_tolerance(cls, value: float) -> float:
        if value < 0:
            raise ValueError("tolerance must be >= 0")
        return value


class TaskSpec(BaseModel):
    """Lightweight task specification for literature reproduction."""

    id: str
    paper_id: str = ""
    doi: str = ""
    calc_type: str
    formula: str
    space_group: str = ""
    mp_id: str = ""
    difficulty: int = 1
    expected: list[ExpectedResult]
    cif_path: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("difficulty")
    @classmethod
    def _validate_difficulty(cls, value: int) -> int:
        return max(1, min(value, 3))

    def template_vars(self) -> dict[str, str]:
        return {
            "formula": self.formula,
            "space_group": self.space_group or "?",
            "mp_id": self.mp_id or "?",
            "expected_keys": ", ".join(
                f"{e.key} ({e.unit})" if e.unit else e.key for e in self.expected
            ),
        }


class SimulatedTask(BaseModel):
    """Output of ``HumanSimulator.formulate()``."""

    prompt: str
    expected: list[ExpectedResult] = Field(default_factory=list)
    data_files: list[DataFileRef] = Field(default_factory=list)
    spec: TaskSpec | None = None
    question: QuestionItem | None = None
