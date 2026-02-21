"""Schemas for MATTER evaluation workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ModeLiteral = Literal["direct", "planner"]
VerifyLiteral = Literal["exact_match", "numerical_range", "contains_all", "llm_judge"]


class TouchpointBands(BaseModel):
    """Detailed score touchpoints for one question."""

    full: list[str] = Field(default_factory=list, description="Touchpoints for top band.")
    partial: list[str] = Field(default_factory=list, description="Touchpoints for middle band.")
    fail: list[str] = Field(default_factory=list, description="Hard-fail indicators.")


class Rubric(BaseModel):
    """Rubric definition for a question level."""

    id: str
    level: Literal["L1", "L2", "L3", "L4", "Safety"]
    score_bands: list[float] = Field(default_factory=list)
    pass_threshold: float = 0.0
    description: str = ""
    criteria: dict[str, str] = Field(default_factory=dict)

    @field_validator("score_bands")
    @classmethod
    def _validate_bands(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("score_bands cannot be empty")
        if sorted(value) != value:
            raise ValueError("score_bands must be ascending")
        return value


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

    @field_validator("tolerance")
    @classmethod
    def _validate_tolerance(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("tolerance must be >= 0")
        return value


class ScoringCheckItem(BaseModel):
    """One verifiable scoring criterion."""

    id: str
    criterion: str
    weight: float = 1.0
    verify: VerifyLiteral

    @field_validator("weight")
    @classmethod
    def _validate_weight(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("weight must be > 0")
        return value


class QuestionItem(BaseModel):
    """Single MATTER question entry."""

    id: str
    level: Literal["L1", "L2", "L3", "L4", "Safety"]
    intent: str
    human_prompt_seed: str
    rubric_id: str
    tags: list[str] = Field(default_factory=list)
    mode_scope: list[ModeLiteral] = Field(default_factory=lambda: ["direct", "planner"])
    repeat_override: int | None = None
    touchpoints: TouchpointBands = Field(default_factory=TouchpointBands)
    data_files: list[DataFileRef] = Field(default_factory=list)
    reference_answers: list[ReferenceAnswer] = Field(default_factory=list)
    scoring_checklist: list[ScoringCheckItem] = Field(default_factory=list)

    @field_validator("repeat_override")
    @classmethod
    def _validate_repeat_override(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1:
            raise ValueError("repeat_override must be >= 1")
        return value

    @field_validator("mode_scope")
    @classmethod
    def _validate_mode_scope(cls, value: list[ModeLiteral]) -> list[ModeLiteral]:
        if not value:
            raise ValueError("mode_scope cannot be empty")
        deduped: list[ModeLiteral] = []
        for mode in value:
            if mode not in deduped:
                deduped.append(mode)
        return deduped

    @model_validator(mode="after")
    def _validate_scoring_contract(self) -> "QuestionItem":
        if not self.scoring_checklist:
            raise ValueError("question must include at least one scoring_checklist entry")
        ref_keys = {item.key for item in self.reference_answers}
        for item in self.scoring_checklist:
            if item.verify in ("exact_match", "numerical_range", "contains_all") and item.id not in ref_keys:
                raise ValueError(
                    f"scoring_checklist item '{item.id}' requires a matching reference_answers key"
                )
        if self.level != "Safety" and not self.reference_answers:
            raise ValueError("non-safety questions must include reference_answers")
        return self


class QuestionBank(BaseModel):
    """Question bank file model."""

    version: str = "v1"
    level: Literal["L1", "L2", "L3", "L4", "Safety"]
    rubric: Rubric
    questions: list[QuestionItem]

    @model_validator(mode="after")
    def _validate_refs(self) -> "QuestionBank":
        if self.rubric.level != self.level:
            raise ValueError("rubric.level must match question bank level")
        if not self.questions:
            raise ValueError("questions cannot be empty")
        for question in self.questions:
            if question.level != self.level:
                raise ValueError(f"question {question.id} level mismatch")
            if question.rubric_id != self.rubric.id:
                raise ValueError(f"question {question.id} rubric_id mismatch")
        return self


class LLMRuntimeConfig(BaseModel):
    """LLM runtime config for simulator/evaluator."""

    provider: Literal["openai", "anthropic", "deepseek", "openrouter"]
    model: str
    api_key: str
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = 4096
    timeout: int = 180


class EvalConfig(BaseModel):
    """Top-level evaluation config."""

    modes: list[ModeLiteral] = Field(default_factory=lambda: ["direct", "planner"])
    k: int = 1
    question_bank_dir: str = "playground/mat_master/evaluation/question_bank"
    output_dir: str = "runs/mat_master_eval"
    run_label: str = "matter_eval"
    random_seed: int = 7
    use_seed_prompt: bool = True
    max_workers: int = 1
    mat_config_path: str = "configs/mat_master/config.yaml"
    simulator_llm: LLMRuntimeConfig | None = None
    evaluator_llm: LLMRuntimeConfig | None = None

    @field_validator("k")
    @classmethod
    def _validate_k(cls, value: int) -> int:
        if value < 1:
            raise ValueError("k must be >= 1")
        return value

    @field_validator("modes")
    @classmethod
    def _validate_modes(cls, value: list[ModeLiteral]) -> list[ModeLiteral]:
        if not value:
            raise ValueError("modes cannot be empty")
        deduped: list[ModeLiteral] = []
        for mode in value:
            if mode not in deduped:
                deduped.append(mode)
        return deduped


class SafetyVetoRecord(BaseModel):
    """Safety refusal verdict for a single run."""

    triggered: bool = False
    reason: str = ""
    risk_not_detected: bool = True
    detail_non_leakage: bool = True
    safe_redirection: bool = True


class EvalRunRecord(BaseModel):
    """Atomic run record: one question, one mode, one repeat."""

    question_id: str
    level: str
    mode: ModeLiteral
    repeat_idx: int
    prompt: str
    answer: str
    run_status: str
    band_score: float
    touchpoints: dict[str, dict[str, Any]] = Field(default_factory=dict)
    deductions: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    safety_veto: SafetyVetoRecord = Field(default_factory=SafetyVetoRecord)
    raw_result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EvaluationSummary(BaseModel):
    """Aggregated result object used by reporter."""

    total_runs: int
    by_question: dict[str, Any]
    by_level: dict[str, Any]
    by_mode: dict[str, Any]
    overall: dict[str, Any]
    safety: dict[str, Any]

