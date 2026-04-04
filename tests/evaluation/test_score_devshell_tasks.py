"""Tests for evaluation/scripts/devshell/score_devshell_tasks.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from evaluation.scripts.devshell.score_devshell_tasks import (
    _build_evidence,
    _format_score_reason,
    _load_latest_events_log,
    _load_raw_run_rows,
    _score_to_int,
    _update_pending_with_score,
    score_task,
)


@pytest.fixture()
def tmp_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "devshell_eval_20260404_000000"
    (run_dir / "workspaces" / "SC_struct_001_direct_r0").mkdir(parents=True)
    (run_dir / "logs" / "SC_struct_001_direct_r0").mkdir(parents=True)
    (run_dir / "pending_ingest").mkdir(parents=True)
    return run_dir


def _workspace(run_dir: Path, task_id: str = "SC_struct_001_direct_r0") -> Path:
    return run_dir / "workspaces" / task_id


def _log_dir(run_dir: Path, task_id: str = "SC_struct_001_direct_r0") -> Path:
    return run_dir / "logs" / task_id


def _write_summary(
    ws: Path, *, status: str = "completed", final_content: str = "Done."
) -> None:
    summary = {
        "model": "cds/GPT-5.4",
        "profile_key": "devshell",
        "status": status,
        "reason": "natural" if status == "completed" else "error",
        "final_content": final_content,
        "num_turns": 3,
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
        },
    }
    (ws / "_devshell_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_raw_runs(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    (run_dir / "raw_runs.jsonl").write_text(payload, encoding="utf-8")


def _write_events(
    log_dir: Path, filename: str = "events_20260404_000001.jsonl"
) -> Path:
    path = log_dir / filename
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_call",
                        "tool": "bash",
                        "call_id": "tc-1",
                        "args": {"command": "python submit_job.py --foo"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "tool_result",
                        "tool": "bash",
                        "call_id": "tc-1",
                        "content": '{"status":"success","job_id":"42"}',
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "tool_call",
                        "tool": "mat_sg_build_bulk_structure_by_template",
                        "call_id": "tc-2",
                        "args": {"formula": "Si"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "tool_result",
                        "tool": "mat_sg_build_bulk_structure_by_template",
                        "call_id": "tc-2",
                        "content": '{"status":"success","path":"si.cif"}',
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"type": "run_result", "status": "completed", "reason": "natural"},
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class TestLoadRawRunRows:
    def test_loads_rows_by_task_id(self, tmp_run_dir: Path) -> None:
        _write_raw_runs(
            tmp_run_dir,
            [
                {
                    "task_id": "SC_struct_001_direct_r0",
                    "question_id": "SC_test_001",
                    "mode": "direct",
                    "repeat_idx": 0,
                    "duration_ms": 1234,
                    "devshell_summary": {"status": "completed"},
                }
            ],
        )

        rows = _load_raw_run_rows(tmp_run_dir / "raw_runs.jsonl")
        assert "SC_struct_001_direct_r0" in rows
        assert rows["SC_struct_001_direct_r0"]["question_id"] == "SC_test_001"


class TestLoadLatestEventsLog:
    def test_picks_latest_events_file(self, tmp_run_dir: Path) -> None:
        log_dir = _log_dir(tmp_run_dir)
        older = log_dir / "events_20260404_000001.jsonl"
        newer = log_dir / "events_20260404_000002.jsonl"
        older.write_text(
            '{"type":"run_result","status":"completed"}\n', encoding="utf-8"
        )
        newer.write_text('{"type":"run_result","status":"failed"}\n', encoding="utf-8")

        result = _load_latest_events_log(log_dir)
        assert result == newer


class TestBuildEvidence:
    def test_parses_events_into_tool_calls_and_events(self, tmp_run_dir: Path) -> None:
        ws = _workspace(tmp_run_dir)
        _write_summary(ws)
        _write_events(_log_dir(tmp_run_dir))

        evidence = _build_evidence(
            task_id="SC_struct_001_direct_r0",
            workspace=ws,
            summary=json.loads((ws / "_devshell_summary.json").read_text()),
            answer="done",
            duration_ms=1234,
            log_dir=_log_dir(tmp_run_dir),
        )

        tool_names = [tc.tool_name for tc in evidence.tool_calls]
        assert "execute_bash" in tool_names
        assert "mat_sg_build_bulk_structure_by_template" in tool_names
        assert any(
            evt.event_type.value == "calculation_execution" for evt in evidence.events
        )
        assert any(
            evt.event_type.value == "structure_construction" for evt in evidence.events
        )
        assert evidence.duration_ms == 1234
        assert evidence.workspace_dir == str(ws.resolve())

    def test_keeps_tool_result_excerpt(self, tmp_run_dir: Path) -> None:
        ws = _workspace(tmp_run_dir)
        _write_summary(ws)
        _write_events(_log_dir(tmp_run_dir))

        evidence = _build_evidence(
            task_id="SC_struct_001_direct_r0",
            workspace=ws,
            summary=json.loads((ws / "_devshell_summary.json").read_text()),
            answer="done",
            duration_ms=1234,
            log_dir=_log_dir(tmp_run_dir),
        )

        bash_call = next(
            tc for tc in evidence.tool_calls if tc.tool_name == "execute_bash"
        )
        assert '"job_id":"42"' in bash_call.observation_excerpt


class TestFormatters:
    def test_format_score_reason_groups_axes(self) -> None:
        record = MagicMock()
        record.overall_weighted_score = 0.5
        record.passed_count = 1
        record.total_count = 2

        from evaluation.core.schemas import CriterionResult

        record.criteria_results = {
            "used_calc": CriterionResult(
                criterion_id="used_calc",
                axis="grounding",
                passed=True,
                reason="tool called",
                verify_method="tool_called",
            ),
            "token_budget_total": CriterionResult(
                criterion_id="token_budget_total",
                axis="efficiency",
                passed=False,
                reason="too many tokens",
                verify_method="token_budget",
            ),
        }

        reason = _format_score_reason(record)
        assert "### Grounding" in reason
        assert "### Efficiency" in reason
        assert "✓ pass" in reason
        assert "✗ fail" in reason

    def test_score_to_int_rounds(self) -> None:
        record = MagicMock()
        record.overall_weighted_score = 0.755
        assert _score_to_int(record) == 76


class TestUpdatePendingWithScore:
    def test_writes_score_and_marks_auto_scored(self, tmp_run_dir: Path) -> None:
        pending = tmp_run_dir / "pending_ingest" / "SC_struct_001_direct_r0.json"
        pending.write_text(
            json.dumps(
                {
                    "schema": "matmaster_eval_pending_ingest_v1",
                    "ingest_url": "http://example.com/ingest",
                    "run_id": "run-001",
                    "run_kind": "iteration",
                    "task_id": "SC_struct_001_direct_r0",
                    "instructions_zh": "old instructions",
                    "item": {"question_id": "SC_test_001"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        ok = _update_pending_with_score(pending, score=88, score_reason="ok")
        assert ok is True

        updated = json.loads(pending.read_text(encoding="utf-8"))
        assert updated["item"]["score"] == 88
        assert updated["item"]["auto_scored"] is True
        assert updated["item"]["auto_scorer"] == "BinaryEvaluator"
        assert "instructions_zh" not in updated


class TestScoreTask:
    def _make_question(self) -> Any:
        from evaluation.core.schemas import (
            QuestionItem,
            ReferenceAnswer,
            ScoringCheckItem,
        )

        return QuestionItem(
            id="SC_test_001",
            capability="workflow_orchestration",
            domain="struct",
            intent="Test devshell scoring",
            human_prompt_seed="Do the thing.",
            reference_answers=[
                ReferenceAnswer(key="used_calc", value="execute_bash"),
                ReferenceAnswer(key="token_budget_total", value={"max": 999999}),
            ],
            scoring_checklist=[
                ScoringCheckItem(
                    id="used_calc",
                    criterion="Uses bash-backed calc call.",
                    axis="grounding",
                    verify="tool_called",
                ),
                ScoringCheckItem(
                    id="token_budget_total",
                    criterion="Token usage within budget.",
                    axis="efficiency",
                    verify="token_budget",
                ),
            ],
        )

    def test_score_task_uses_event_log_for_tool_called(self, tmp_run_dir: Path) -> None:
        from evaluation.core.evaluator import BinaryEvaluator

        ws = _workspace(tmp_run_dir)
        _write_summary(ws, final_content="Used calculation tool")
        _write_events(_log_dir(tmp_run_dir))

        row = {
            "task_id": "SC_struct_001_direct_r0",
            "question_id": "SC_test_001",
            "mode": "direct",
            "repeat_idx": 0,
            "duration_ms": 1234,
            "devshell_summary": {"status": "completed"},
        }
        question = self._make_question()
        evaluator = BinaryEvaluator(llm_cfg=None)

        result = score_task(
            row=row,
            run_dir=tmp_run_dir,
            question=question,
            evaluator=evaluator,
        )

        assert result["error"] is None
        assert result["score"] == 100
        assert "used_calc" in result["score_reason"]
        assert "✓ pass" in result["score_reason"]
