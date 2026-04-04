"""Tests for evaluation/scripts/baseline/score_baseline_tasks.py.

These tests exercise the helper functions without requiring a real question bank
or a live evaluator LLM.  They mock or stub the parts that touch external
resources (file system, pymatgen, LLM).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from evaluation.scripts.baseline.score_baseline_tasks import (
    _META_FILENAMES,
    _build_answer,
    _build_evidence,
    _build_workspace_file_listing,
    _compute_duration_ms,
    _format_score_reason,
    _load_summary,
    _score_to_int,
    _update_pending_with_score,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    """An empty workspace directory (simulates RUN_DIR/workspaces/<task_id>/)."""
    ws = tmp_path / "SC_struct_001_direct_r0"
    ws.mkdir()
    return ws


def _write_meta(ws: Path, question_id: str = "SC_struct_001_20260401") -> None:
    meta = {
        "schema": "matmaster_eval_task_meta_v1",
        "task_id": ws.name,
        "question_id": question_id,
        "capability": "structure_construction",
        "domain": "struct",
        "mode": "direct",
        "repeat_idx": 0,
        "prompt": "Build a heterostructure.",
    }
    (ws / "_eval_task_meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _write_summary(
    ws: Path, *, status: str = "completed", final_content: str = "Done."
) -> None:
    summary = {
        "model": "claude-opus-4-6",
        "profile_key": "claude_code",
        "status": status,
        "reason": "natural" if status == "completed" else "error",
        "final_content": final_content,
        "num_turns": 3,
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "input_tokens": 50,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 850,
            "output_tokens": 200,
        },
    }
    (ws / "_devshell_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _write_task_start(ws: Path, started_ms: int) -> None:
    payload = {
        "started_at_unix_ms": started_ms,
        "schema": "matmaster_cc_baseline_task_start_v1",
    }
    (ws / "_cc_baseline_task_start.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _load_summary
# ---------------------------------------------------------------------------


class TestLoadSummary:
    def test_returns_dict_on_valid_json(self, tmp_workspace: Path) -> None:
        _write_summary(tmp_workspace)
        result = _load_summary(tmp_workspace)
        assert result["status"] == "completed"
        assert result["model"] == "claude-opus-4-6"

    def test_returns_empty_on_missing_file(self, tmp_workspace: Path) -> None:
        result = _load_summary(tmp_workspace)
        assert result == {}

    def test_returns_empty_on_broken_json(self, tmp_workspace: Path) -> None:
        (tmp_workspace / "_devshell_summary.json").write_text(
            "not-json", encoding="utf-8"
        )
        result = _load_summary(tmp_workspace)
        assert result == {}

    def test_reads_last_line_for_oneliner_json(self, tmp_workspace: Path) -> None:
        """Summary files are written as single-line JSON; we read the last line."""
        data = {"status": "completed", "final_content": "ok"}
        (tmp_workspace / "_devshell_summary.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = _load_summary(tmp_workspace)
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# _compute_duration_ms
# ---------------------------------------------------------------------------


class TestComputeDurationMs:
    def test_computes_delta(self, tmp_workspace: Path) -> None:
        started_ms = int(time.time() * 1000) - 60_000  # 60 seconds ago
        _write_task_start(tmp_workspace, started_ms)
        _write_summary(tmp_workspace)
        summary_path = tmp_workspace / "_devshell_summary.json"
        result = _compute_duration_ms(tmp_workspace, summary_path)
        # Should be roughly 60000ms, allow generous tolerance for mtime precision
        assert result >= 59_000
        assert result <= 120_000

    def test_returns_zero_when_no_start_file(self, tmp_workspace: Path) -> None:
        _write_summary(tmp_workspace)
        summary_path = tmp_workspace / "_devshell_summary.json"
        result = _compute_duration_ms(tmp_workspace, summary_path)
        assert result == 0

    def test_returns_zero_when_no_summary_file(self, tmp_workspace: Path) -> None:
        _write_task_start(tmp_workspace, int(time.time() * 1000) - 1000)
        result = _compute_duration_ms(
            tmp_workspace, tmp_workspace / "_devshell_summary.json"
        )
        assert result == 0


# ---------------------------------------------------------------------------
# _build_workspace_file_listing
# ---------------------------------------------------------------------------


class TestBuildWorkspaceFileListing:
    def test_excludes_meta_files(self, tmp_workspace: Path) -> None:
        for meta_name in _META_FILENAMES:
            (tmp_workspace / meta_name).write_text("{}", encoding="utf-8")
        result = _build_workspace_file_listing(tmp_workspace)
        assert "(no deliverable files found" in result

    def test_includes_deliverable_files(self, tmp_workspace: Path) -> None:
        (tmp_workspace / "output.cif").write_text("data_", encoding="utf-8")
        (tmp_workspace / "POSCAR").write_text("Fe\n1", encoding="utf-8")
        result = _build_workspace_file_listing(tmp_workspace)
        assert "output.cif" in result
        assert "POSCAR" in result

    def test_reports_file_size(self, tmp_workspace: Path) -> None:
        content = "x" * 42
        (tmp_workspace / "structure.cif").write_text(content, encoding="utf-8")
        result = _build_workspace_file_listing(tmp_workspace)
        assert "42 bytes" in result


# ---------------------------------------------------------------------------
# _build_answer
# ---------------------------------------------------------------------------


class TestBuildAnswer:
    def test_includes_final_content(self, tmp_workspace: Path) -> None:
        summary = {"final_content": "I built the structure."}
        answer = _build_answer(tmp_workspace, summary)
        assert "I built the structure." in answer

    def test_includes_file_listing(self, tmp_workspace: Path) -> None:
        (tmp_workspace / "output.cif").write_text("data_", encoding="utf-8")
        summary = {"final_content": "done"}
        answer = _build_answer(tmp_workspace, summary)
        assert "output.cif" in answer
        assert "Workspace deliverable files" in answer

    def test_handles_empty_summary(self, tmp_workspace: Path) -> None:
        answer = _build_answer(tmp_workspace, {})
        # Should still produce something (the file listing section)
        assert isinstance(answer, str)
        assert "Workspace deliverable files" in answer


# ---------------------------------------------------------------------------
# _build_evidence
# ---------------------------------------------------------------------------


class TestBuildEvidence:
    def test_workspace_dir_set(self, tmp_workspace: Path) -> None:
        _write_summary(tmp_workspace)
        summary = _load_summary(tmp_workspace)
        evidence = _build_evidence(
            task_id="test_task",
            workspace=tmp_workspace,
            summary=summary,
            answer="done",
        )
        assert evidence.workspace_dir == str(tmp_workspace.resolve())

    def test_token_usage_from_summary(self, tmp_workspace: Path) -> None:
        _write_summary(tmp_workspace)
        summary = _load_summary(tmp_workspace)
        evidence = _build_evidence(
            task_id="test_task",
            workspace=tmp_workspace,
            summary=summary,
            answer="done",
        )
        # prompt_tokens = input_tokens + cache_creation + cache_read = 50+100+850 = 1000
        assert evidence.token_usage_last_turn.prompt_tokens == 1000

    def test_artifacts_listed_from_workspace(self, tmp_workspace: Path) -> None:
        (tmp_workspace / "output.cif").write_text("data_", encoding="utf-8")
        summary = {}
        evidence = _build_evidence(
            task_id="test_task",
            workspace=tmp_workspace,
            summary=summary,
            answer="",
        )
        artifact_names = [a.path for a in evidence.artifacts]
        assert "output.cif" in artifact_names

    def test_meta_files_not_in_artifacts(self, tmp_workspace: Path) -> None:
        for meta_name in _META_FILENAMES:
            (tmp_workspace / meta_name).write_text("{}", encoding="utf-8")
        summary = {}
        evidence = _build_evidence(
            task_id="test_task",
            workspace=tmp_workspace,
            summary=summary,
            answer="",
        )
        artifact_names = {a.path for a in evidence.artifacts}
        for meta_name in _META_FILENAMES:
            assert meta_name not in artifact_names

    def test_tool_calls_and_events_empty(self, tmp_workspace: Path) -> None:
        evidence = _build_evidence(
            task_id="test_task",
            workspace=tmp_workspace,
            summary={},
            answer="",
        )
        assert evidence.tool_calls == []
        assert evidence.events == []

    def test_model_name_from_summary(self, tmp_workspace: Path) -> None:
        summary = {"model": "claude-sonnet-4-6", "usage": {}}
        evidence = _build_evidence(
            task_id="test_task",
            workspace=tmp_workspace,
            summary=summary,
            answer="",
        )
        assert evidence.model_name == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# _format_score_reason and _score_to_int
# ---------------------------------------------------------------------------


class TestFormatScoreReason:
    def _make_mock_record(
        self, criteria: dict[str, tuple[str, bool, str]]
    ) -> MagicMock:
        """Build a mock EvalRunRecord with the given criteria results."""
        record = MagicMock()
        record.overall_weighted_score = 0.75
        record.passed_count = 3
        record.total_count = 4

        from evaluation.core.schemas import CriterionResult

        record.criteria_results = {
            cid: CriterionResult(
                criterion_id=cid,
                axis=axis,
                passed=passed,
                reason=reason,
                verify_method="struct_file_formula",
            )
            for cid, (axis, passed, reason) in criteria.items()
        }
        return record

    def test_format_contains_criterion_ids(self) -> None:
        record = self._make_mock_record(
            {
                "hetero_formula": ("correctness", True, "formula matches"),
                "grounding_source": ("grounding", False, "no tools used"),
            }
        )
        reason = _format_score_reason(record)
        assert "hetero_formula" in reason
        assert "grounding_source" in reason

    def test_format_shows_pass_fail(self) -> None:
        record = self._make_mock_record(
            {
                "atom_count": ("correctness", True, "ok"),
                "tool_used": ("grounding", False, "no trajectory"),
            }
        )
        reason = _format_score_reason(record)
        assert "✓ pass" in reason
        assert "✗ fail" in reason

    def test_score_to_int_rounds_correctly(self) -> None:
        record = MagicMock()
        record.overall_weighted_score = 0.734
        assert _score_to_int(record) == 73

        record.overall_weighted_score = 0.755
        assert _score_to_int(record) == 76

        record.overall_weighted_score = 1.0
        assert _score_to_int(record) == 100

        record.overall_weighted_score = 0.0
        assert _score_to_int(record) == 0


# ---------------------------------------------------------------------------
# _update_pending_with_score
# ---------------------------------------------------------------------------


class TestUpdatePendingWithScore:
    def test_writes_score_to_pending_json(self, tmp_path: Path) -> None:
        pending = tmp_path / "SC_struct_001_direct_r0.json"
        envelope: dict[str, Any] = {
            "schema": "matmaster_eval_pending_ingest_v1",
            "ingest_url": "http://example.com/ingest",
            "run_id": "run-001",
            "run_kind": "baseline",
            "task_id": "SC_struct_001_direct_r0",
            "item": {
                "question_id": "SC_struct_001_20260401",
                "model": "claude-opus-4-6",
            },
        }
        pending.write_text(json.dumps(envelope), encoding="utf-8")

        ok = _update_pending_with_score(
            pending,
            score=75,
            score_reason="correctness: ✓ formula\ngrounding: ✗ no tool",
        )
        assert ok is True

        updated = json.loads(pending.read_text(encoding="utf-8"))
        assert updated["item"]["score"] == 75
        assert "correctness" in updated["item"]["score_reason"]
        assert updated["item"]["auto_scored"] is True
        assert updated["item"]["auto_scorer"] == "BinaryEvaluator"

    def test_removes_instructions_zh(self, tmp_path: Path) -> None:
        pending = tmp_path / "task.json"
        envelope = {
            "schema": "matmaster_eval_pending_ingest_v1",
            "ingest_url": "http://example.com",
            "run_id": "r1",
            "run_kind": "baseline",
            "task_id": "t1",
            "instructions_zh": "【外部 Baseline】勿随手给 100 分...",
            "item": {"question_id": "Q1"},
        }
        pending.write_text(json.dumps(envelope), encoding="utf-8")
        _update_pending_with_score(pending, score=50, score_reason="ok")
        updated = json.loads(pending.read_text(encoding="utf-8"))
        assert "instructions_zh" not in updated

    def test_returns_false_on_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"
        ok = _update_pending_with_score(missing, score=50, score_reason="x")
        assert ok is False

    def test_score_reason_truncated_at_16384(self, tmp_path: Path) -> None:
        pending = tmp_path / "task.json"
        pending.write_text(
            json.dumps({"item": {"question_id": "Q1"}, "ingest_url": "http://x"}),
            encoding="utf-8",
        )
        long_reason = "x" * 20000
        _update_pending_with_score(pending, score=80, score_reason=long_reason)
        updated = json.loads(pending.read_text(encoding="utf-8"))
        assert len(updated["item"]["score_reason"]) <= 16384


# ---------------------------------------------------------------------------
# Integration: score_task with a stubbed evaluator (no pymatgen needed)
# ---------------------------------------------------------------------------


class TestScoreTask:
    """Integration test for score_task using a real-but-minimal QuestionItem
    with only llm_binary_judge and token_budget verify types — no pymatgen."""

    def _make_question(self) -> Any:
        from evaluation.core.schemas import (
            QuestionItem,
            ReferenceAnswer,
            ScoringCheckItem,
        )

        return QuestionItem(
            id="SC_test_001",
            capability="structure_construction",
            domain="struct",
            intent="Test question",
            human_prompt_seed="Build something.",
            reference_answers=[
                ReferenceAnswer(key="token_budget_total", value={"max": 999999}),
            ],
            scoring_checklist=[
                ScoringCheckItem(
                    id="token_budget_total",
                    criterion="Token usage within budget.",
                    axis="efficiency",
                    verify="token_budget",
                ),
                ScoringCheckItem(
                    id="required_content",
                    criterion="The answer mentions a structure file.",
                    axis="correctness",
                    verify="llm_binary_judge",
                ),
            ],
        )

    def test_score_task_without_llm_gives_deterministic_results(
        self, tmp_workspace: Path
    ) -> None:
        """Without an LLM, llm_binary_judge fails gracefully; token_budget passes."""
        from evaluation.core.evaluator import BinaryEvaluator

        _write_meta(tmp_workspace, question_id="SC_test_001")
        _write_summary(tmp_workspace, final_content="I created structure.cif")

        question = self._make_question()
        evaluator = BinaryEvaluator(llm_cfg=None)  # No LLM

        from evaluation.scripts.baseline.score_baseline_tasks import score_task

        meta = json.loads((tmp_workspace / "_eval_task_meta.json").read_text())
        result = score_task(
            task_id=tmp_workspace.name,
            workspace=tmp_workspace,
            question=question,
            evaluator=evaluator,
            meta=meta,
        )

        assert result["error"] is None
        assert isinstance(result["score"], int)
        assert 0 <= result["score"] <= 100
        assert "token_budget_total" in result["score_reason"]
        # llm_binary_judge fails without LLM
        assert "no evaluator LLM configured" in result["score_reason"]

    def test_score_task_token_budget_passes_for_small_usage(
        self, tmp_workspace: Path
    ) -> None:
        """Token budget check passes when usage is within limit."""
        from evaluation.core.evaluator import BinaryEvaluator

        _write_meta(tmp_workspace, question_id="SC_test_001")
        _write_summary(tmp_workspace, final_content="done")

        question = self._make_question()
        evaluator = BinaryEvaluator(llm_cfg=None)

        from evaluation.scripts.baseline.score_baseline_tasks import score_task

        meta = json.loads((tmp_workspace / "_eval_task_meta.json").read_text())
        result = score_task(
            task_id=tmp_workspace.name,
            workspace=tmp_workspace,
            question=question,
            evaluator=evaluator,
            meta=meta,
        )

        # token_budget_total: prompt_tokens=1000, budget=999999 → pass
        assert "✓ pass" in result["score_reason"]

    def test_score_task_returns_error_on_evaluator_exception(
        self, tmp_workspace: Path
    ) -> None:
        """score_task catches evaluator exceptions and returns error in result."""
        _write_meta(tmp_workspace, question_id="SC_test_001")
        _write_summary(tmp_workspace)

        question = self._make_question()
        bad_evaluator = MagicMock()
        bad_evaluator.evaluate.side_effect = RuntimeError("boom")

        from evaluation.scripts.baseline.score_baseline_tasks import score_task

        meta = json.loads((tmp_workspace / "_eval_task_meta.json").read_text())
        result = score_task(
            task_id=tmp_workspace.name,
            workspace=tmp_workspace,
            question=question,
            evaluator=bad_evaluator,
            meta=meta,
        )

        assert result["error"] is not None
        assert "boom" in result["error"]
        assert result["score"] == 0
