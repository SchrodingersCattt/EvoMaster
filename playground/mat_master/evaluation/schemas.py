"""Schemas for MATTER v5 evaluation workflows.

Current v5 schema changes:
- Rubric class REMOVED — binary scoring needs no rubric
- ScoringCheckItem: weight field REMOVED, dimension renamed to axis
- New literals: CapabilityLiteral, DomainLiteral, AxisLiteral
- QuestionItem: added capability/domain/required_tools/optional_tools; removed level/rubric_id/touchpoints/repeat_override
- New CriterionResult model (per-criterion pass/fail + reason)
- EvalRunRecord: replaces float scores with pass counts + criteria_results dict
- EvaluationSummary: pass-rate oriented with AxisPassRates
- QuestionBank: no longer requires rubric field
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Literal type aliases
# ---------------------------------------------------------------------------

ModeLiteral = Literal['direct', 'planner']

VerifyLiteral = Literal[
    'exact_match',
    'numerical_range',
    'contains_all',
    'tool_called',
    'tool_args_match',
    'event_type_called',
    'source_type_used',
    'call_count_range',
    'no_retries',
    'artifact_exists',
    'token_budget',
    'llm_binary_judge',
    'batch_single_variable_sweep',
    'batch_tool_args_constant',
    'batch_consistent_calls',
]

AxisLiteral = Literal['correctness', 'grounding', 'efficiency']

CapabilityLiteral = Literal[
    'knowledge_recall',
    'structure_construction',
    'property_prediction',
    'workflow_orchestration',
    'data_diagnosis',
    'batch_processing',
    'safety_refusal',
]

DomainLiteral = Literal[
    'struct',
    'elec',
    'mech',
    'thermo',
    'kinetic',
    'optical',
    'general',
]


# ---------------------------------------------------------------------------
# Shared small models
# ---------------------------------------------------------------------------


class DataFileRef(BaseModel):
    """Reference to a concrete input file used by a question."""

    key: str
    path: str
    oss_url: str = ''
    description: str = ''

    @field_validator('path')
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('data file path cannot be empty')
        return value


class ReferenceAnswer(BaseModel):
    """Ground-truth value used by checklist scoring."""

    key: str
    value: Any
    tolerance: float | None = None
    unit: str = ''
    tool_name: str | None = None
    tool_arg: str | None = None

    @field_validator('tolerance')
    @classmethod
    def _validate_tolerance(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError('tolerance must be >= 0')
        return value


# ---------------------------------------------------------------------------
# v5 core scoring models
# ---------------------------------------------------------------------------


class ScoringCheckItem(BaseModel):
    """One verifiable scoring criterion (v5: binary, no weight).

    Every criterion counts as exactly 1 point and is attached to one scoring
    axis: correctness, grounding, or efficiency.
    """

    id: str
    criterion: str
    axis: AxisLiteral = Field(
        default='correctness',
        description=(
            "Which scoring axis this criterion belongs to: "
            "'correctness' (is the answer right?), "
            "'grounding' (did it use the right tools/sources?), "
            "'efficiency' (was the process efficient?)."
        ),
    )
    verify: VerifyLiteral


class CriterionResult(BaseModel):
    """Per-criterion pass/fail result stored inside EvalRunRecord."""

    criterion_id: str
    axis: AxisLiteral
    passed: bool
    reason: str = ''          # one-sentence evidence / explanation
    verify_method: str = ''   # which verifier produced this result


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
    intent: str
    human_prompt_seed: str
    tags: list[str] = Field(default_factory=list)
    mode_scope: list[ModeLiteral] = Field(default_factory=lambda: ['direct', 'planner'])
    required_tools: list[str] = Field(
        default_factory=list,
        description='MCP tools that must be in the whitelist for this question to run.',
    )
    optional_tools: list[str] = Field(
        default_factory=list,
        description='Nice-to-have tools; question is runnable without them.',
    )
    data_files: list[DataFileRef] = Field(default_factory=list)
    reference_answers: list[ReferenceAnswer] = Field(default_factory=list)
    scoring_checklist: list[ScoringCheckItem] = Field(default_factory=list)

    @field_validator('mode_scope')
    @classmethod
    def _validate_mode_scope(cls, value: list[ModeLiteral]) -> list[ModeLiteral]:
        if not value:
            raise ValueError('mode_scope cannot be empty')
        deduped: list[ModeLiteral] = []
        for mode in value:
            if mode not in deduped:
                deduped.append(mode)
        return deduped

    @model_validator(mode='after')
    def _validate_scoring_contract(self) -> 'QuestionItem':
        if not self.scoring_checklist:
            raise ValueError('question must include at least one scoring_checklist entry')
        # For deterministic check types that need a reference answer, verify it exists.
        ref_keys = {item.key for item in self.reference_answers}
        _needs_ref = {
            'exact_match',
            'numerical_range',
            'contains_all',
            'tool_called',
            'tool_args_match',
            'batch_single_variable_sweep',
            'batch_tool_args_constant',
            'batch_consistent_calls',
        }
        for item in self.scoring_checklist:
            if item.verify in _needs_ref and item.id not in ref_keys:
                raise ValueError(
                    f"scoring_checklist item '{item.id}' (verify={item.verify}) "
                    "requires a matching reference_answers entry with the same key"
                )
        # Safety questions (capability='safety_refusal') may skip reference_answers
        if self.capability != 'safety_refusal' and not self.reference_answers:
            raise ValueError('non-safety questions must include reference_answers')
        return self


# ---------------------------------------------------------------------------
# v5 QuestionBank
# ---------------------------------------------------------------------------


class QuestionBank(BaseModel):
    """Question bank file model (v5 format)."""

    version: str = 'v5'
    capability: CapabilityLiteral | None = None   # optional top-level hint
    domain: DomainLiteral | None = None           # optional top-level hint
    questions: list[QuestionItem]

    @model_validator(mode='after')
    def _validate_questions(self) -> 'QuestionBank':
        if not self.questions:
            raise ValueError('questions cannot be empty')
        return self


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class LLMRuntimeConfig(BaseModel):
    """LLM runtime config for simulator/evaluator."""

    provider: Literal['openai', 'anthropic', 'deepseek', 'openrouter']
    model: str
    api_key: str
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = 4096
    timeout: int = 180


class EvalConfig(BaseModel):
    """Top-level evaluation config."""

    modes: list[ModeLiteral] = Field(default_factory=lambda: ['direct', 'planner'])
    k: int = 1
    question_bank_dir: str = 'playground/mat_master/evaluation/question_bank'
    output_dir: str = 'runs/mat_master_eval'
    run_label: str = 'matter_eval'
    random_seed: int = 7
    use_seed_prompt: bool = True
    max_workers: int = 1
    mat_config_path: str = 'configs/mat_master/config.yaml'
    simulator_llm: LLMRuntimeConfig | None = None
    evaluator_llm: LLMRuntimeConfig | None = None
    include_capabilities: list[str] | None = None
    include_question_ids: list[str] | None = None

    @field_validator('k')
    @classmethod
    def _validate_k(cls, value: int) -> int:
        if value < 1:
            raise ValueError('k must be >= 1')
        return value

    @field_validator('modes')
    @classmethod
    def _validate_modes(cls, value: list[ModeLiteral]) -> list[ModeLiteral]:
        if not value:
            raise ValueError('modes cannot be empty')
        deduped: list[ModeLiteral] = []
        for mode in value:
            if mode not in deduped:
                deduped.append(mode)
        return deduped


# ---------------------------------------------------------------------------
# Run record models
# ---------------------------------------------------------------------------


class SafetyVetoRecord(BaseModel):
    """Safety refusal verdict for a single run."""

    triggered: bool = False
    reason: str = ''
    risk_not_detected: bool = True
    detail_non_leakage: bool = True
    safe_redirection: bool = True


class TokenUsageRecord(BaseModel):
    """Serialisable token usage summary stored in EvalRunRecord."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class EvalRunRecord(BaseModel):
    """Atomic run record: one question, one mode, one repeat."""

    question_id: str
    capability: str = ''     # mirrors QuestionItem.capability
    domain: str = ''         # mirrors QuestionItem.domain
    mode: ModeLiteral
    repeat_idx: int
    prompt: str
    answer: str
    run_status: str

    # v5: binary pass counts
    criteria_results: dict[str, CriterionResult] = Field(
        default_factory=dict,
        description='Mapping of criterion_id -> CriterionResult (pass/fail + reason)',
    )
    passed_count: int = 0
    total_count: int = 0
    correctness_passed: int = 0
    correctness_total: int = 0
    grounding_passed: int = 0
    grounding_total: int = 0
    efficiency_passed: int = 0
    efficiency_total: int = 0

    # Meta
    model_name: str | None = None
    token_usage: TokenUsageRecord = Field(default_factory=TokenUsageRecord)
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

    correctness: tuple[int, int] = (0, 0)   # (passed, total)
    grounding: tuple[int, int] = (0, 0)
    efficiency: tuple[int, int] = (0, 0)
    overall: tuple[int, int] = (0, 0)

    def pass_rate(self, axis: str = 'overall') -> float:
        pair = getattr(self, axis, self.overall)
        passed, total = pair
        return passed / total if total > 0 else 0.0

    def fmt(self, axis: str = 'overall') -> str:
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


class ToolContribution(BaseModel):
    """How much a single tool contributed across all questions."""

    tool_name: str
    questions_requiring: int = 0   # questions where this tool is in required_tools
    criteria_delta: int = 0        # additional criteria passed when this tool is present
    accepted: bool = False


class EvaluationSummary(BaseModel):
    """Aggregated result object (v5): pass-rate oriented."""

    total_runs: int
    total_criteria: int = 0
    total_passed: int = 0
    pass_rate: float = 0.0

    by_capability: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_domain: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_question: dict[str, QuestionPassRate] = Field(default_factory=dict)
    by_mode: dict[str, AxisPassRates] = Field(default_factory=dict)
    by_model: dict[str, AxisPassRates] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    tool_contribution: dict[str, ToolContribution] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# HumanSimulator schemas
# ---------------------------------------------------------------------------


class ExpectedResult(BaseModel):
    """One expected numerical result for scoring."""

    key: str
    value: float
    tolerance: float
    unit: str = ''

    @field_validator('tolerance')
    @classmethod
    def _validate_tolerance(cls, value: float) -> float:
        if value < 0:
            raise ValueError('tolerance must be >= 0')
        return value


class TaskSpec(BaseModel):
    """Lightweight task specification for literature reproduction."""

    id: str
    paper_id: str = ''
    doi: str = ''
    calc_type: str
    formula: str
    space_group: str = ''
    mp_id: str = ''
    difficulty: int = 1
    expected: list[ExpectedResult]
    cif_path: str = ''
    tags: list[str] = Field(default_factory=list)

    @field_validator('difficulty')
    @classmethod
    def _validate_difficulty(cls, value: int) -> int:
        return max(1, min(value, 3))

    def template_vars(self) -> dict[str, str]:
        return {
            'formula': self.formula,
            'space_group': self.space_group or '?',
            'mp_id': self.mp_id or '?',
            'expected_keys': ', '.join(
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
