from __future__ import annotations

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
