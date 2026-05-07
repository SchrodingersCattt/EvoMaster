from __future__ import annotations

from pathlib import Path

from evaluation.core import mat_runner
from evaluation.core.runner import expand_run_plan
from evaluation.core.schemas import EvalConfig, QuestionItem
from matmaster.config.loader import load_exp_config


def _question() -> QuestionItem:
    return QuestionItem.model_validate(
        {
            "id": "TEST_mode_001",
            "capability": "scientific_analysis",
            "domain": "agnostic",
            "intent": "Verify mode propagation",
            "human_prompt_seed": "Say hello.",
            "reference_answers": [{"key": "presence", "value": "hello"}],
            "scoring_checklist": [
                {
                    "id": "presence",
                    "criterion": "Answer says hello.",
                    "axis": "correctness",
                    "verify": "llm_binary_judge",
                }
            ],
        }
    )


def test_expand_run_plan_direct_default():
    plan = expand_run_plan(questions=[_question()], config=EvalConfig(k=2))

    assert [item["mode"] for item in plan] == ["direct", "direct"]
    assert [item["repeat_idx"] for item in plan] == [0, 1]


def test_expand_run_plan_planner_mode():
    plan = expand_run_plan(
        questions=[_question()], config=EvalConfig(k=2, exp="planner")
    )

    assert [item["mode"] for item in plan] == ["planner", "planner"]
    assert [item["repeat_idx"] for item in plan] == [0, 1]


def test_planner_exp_config_resolves():
    exp_config = load_exp_config("planner")

    assert exp_config is not None


def test_run_mat_task_passes_planner_mode_to_single_attempt(monkeypatch, tmp_path):
    seen: dict[str, str] = {}

    def fake_run_once(*, prompt, mode, task_id, run_dir, mat_config_path):
        seen["mode"] = mode
        return {
            "task_id": task_id,
            "mode": mode,
            "answer": "ok",
            "tool_calls": [],
            "status": "completed",
            "result": {"status": "completed", "reason": "natural"},
            "duration_ms": 1,
        }

    monkeypatch.setattr(mat_runner, "_run_mat_task_once", fake_run_once)

    result = mat_runner.run_mat_task(
        prompt="hello",
        mode="planner",
        task_id="TEST_mode_001",
        run_dir=tmp_path,
        mat_config_path=Path("configs/mat_master/config.yaml"),
        empty_completion_max_retries=0,
    )

    assert seen["mode"] == "planner"
    assert result["mode"] == "planner"
