"""Tests for regex-based tool argument evidence checks."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validator_for
from pydantic import ValidationError as PydanticValidationError

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.runner import flatten_banks, load_question_banks
from evaluation.core.schemas import QuestionItem, ReferenceAnswer, ScoringCheckItem

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION_BANK_DIR = REPO_ROOT / "evaluation" / "question_bank"
BOHR_COMMAND_PATTERN = (
    r"(?:^|[\n;&|]\s*)(?:(?:env|sudo)(?:\s+[^\s;&|]+)*\s+)?"
    r"(?:[^\s;&|]+/)?bohr\s+job\s+describe\b"
)


def _tool_regex_question(*, min_matches: int = 2) -> QuestionItem:
    return QuestionItem(
        id="tool_regex_test",
        capability="workflow_orchestration",
        domain="agnostic",
        intent="Verify repeated CLI polling.",
        human_prompt_seed="Poll the job.",
        reference_answers=[
            ReferenceAnswer(
                key="polled",
                tool_name="Bash",
                tool_arg="command",
                value={
                    "pattern": BOHR_COMMAND_PATTERN,
                    "min_matches": min_matches,
                },
            )
        ],
        scoring_checklist=[
            ScoringCheckItem(
                id="polled",
                criterion="Job was polled repeatedly.",
                axis="grounding",
                verify="tool_args_regex",
            )
        ],
    )


def test_tool_args_regex_counts_matches_across_calls_without_leaking_args() -> None:
    question = _tool_regex_question()
    record = BinaryEvaluator().evaluate(
        question=question,
        answer="done",
        tool_calls=[
            {
                "tool_name": "Bash",
                "tool_args": {
                    "command": "env BOHRIUM_ACCESS_KEY=secret bohr job describe -i 1"
                },
            },
            {
                "tool_name": "Bash",
                "tool_args": {"command": "sleep 10 && bohr job describe -i 1"},
            },
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is True
    assert "regex matches=2" in result.reason
    assert "secret" not in result.reason


def test_tool_args_regex_fails_below_minimum() -> None:
    record = BinaryEvaluator().evaluate(
        question=_tool_regex_question(),
        answer="done",
        tool_calls=[
            {
                "tool_name": "Bash",
                "tool_args": {"command": "bohr job describe -i 1"},
            }
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is False
    assert "regex matches=1" in result.reason
    assert "expected=>=2" in result.reason


@pytest.mark.parametrize(
    "value",
    [
        {"pattern": "(", "min_matches": 1},
        {"pattern": "bohr", "min_matches": 0},
        {"pattern": "bohr", "min_matches": 2, "max_matches": 1},
        {"pattern": "bohr", "unknown": True},
    ],
)
def test_tool_args_regex_reference_is_validated(value: object) -> None:
    with pytest.raises(PydanticValidationError, match="tool_args_regex reference"):
        QuestionItem(
            id="invalid_tool_regex",
            capability="workflow_orchestration",
            domain="agnostic",
            intent="Invalid verifier config.",
            human_prompt_seed="test",
            reference_answers=[
                ReferenceAnswer(
                    key="polled",
                    tool_name="Bash",
                    tool_arg="command",
                    value=value,
                )
            ],
            scoring_checklist=[
                ScoringCheckItem(
                    id="polled",
                    criterion="Poll.",
                    verify="tool_args_regex",
                )
            ],
        )


def test_bwo_monitor_v3_schema_requires_real_lifecycle() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_monitor_D6_20260715_v3")
    assert all(
        q.id not in {"BWO_monitor_D6_20260715", "BWO_monitor_D6_20260715_v2"}
        for q in questions
    )
    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "monitor_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "job_id": 20400713,
        "image": "registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1",
        "machine_type": "c2_m4_cpu",
        "command": 'echo "hello from bohrium" > result.txt && sleep 60',
        "polls": [
            {"time": "2026-07-15 21:29:58", "status": 1},
            {"time": "2026-07-15 21:31:10", "status": 2},
        ],
        "final_status": 2,
        "log_saved": True,
    }
    validator.validate(valid)

    with pytest.raises(ValidationError):
        validator.validate(
            {
                **valid,
                "polls": [{"time": "2026-07-15 21:31:10", "status": 2}],
            }
        )
    with pytest.raises(ValidationError):
        validator.validate({**valid, "final_status": "Finished"})
    with pytest.raises(ValidationError):
        validator.validate({**valid, "log_saved": False})


def test_bwo_lit_db_v2_requires_complete_design_and_real_search() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_lit_db_D5_20260715_v2")
    assert all(q.id != "BWO_lit_db_D5_20260715" for q in questions)

    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "literature_db_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "schema": {
            "fields": [
                {"name": "material", "type": "string", "description": "Name"},
                {"name": "capacity", "type": "number", "description": "Capacity"},
                {"name": "voltage", "type": "number", "description": "Voltage"},
            ]
        },
        "papers_found": 20,
        "batch_strategy": "Parse papers in bounded batches.",
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate({**valid, "papers_found": 19})
    with pytest.raises(ValidationError):
        validator.validate(
            {
                **valid,
                "schema": {"fields": valid["schema"]["fields"][:2]},
            }
        )

    search_ref = next(
        ref for ref in question.reference_answers if ref.key == "paper_search_via_cli"
    )
    pattern = search_ref.value["pattern"]
    assert re.search(
        pattern,
        'bohr paper search "sodium-ion battery cathode" --size 20 -o json',
    )
    assert not re.search(
        pattern,
        'bohr paper search "sodium-ion battery cathode" --size 10 -o json',
    )


def test_bwo_param_sweep_v2_requires_complete_grouped_sweep() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_param_sweep_003_20260715_v2")
    assert all(q.id != "BWO_param_sweep_003_20260715" for q in questions)

    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "sweep_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "job_group_id": 9876,
        "jobs": [
            {"temperature_K": temperature, "job_id": 1000 + index}
            for index, temperature in enumerate(range(300, 1001, 100))
        ],
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate(
            {
                **valid,
                "jobs": [
                    *valid["jobs"][:-1],
                    {"temperature_K": 900, "job_id": 9999},
                ],
            }
        )
    with pytest.raises(ValidationError):
        validator.validate({**valid, "job_group_id": 0})

    group_ref = next(
        ref for ref in question.reference_answers if ref.key == "group_created_via_cli"
    )
    assert re.search(
        group_ref.value["pattern"],
        'bohr job_group create -n "temperature-sweep" --project_id 123 -o json',
    )
    submit_ref = next(
        ref
        for ref in question.reference_answers
        if ref.key == "group_jobs_submitted_via_cli"
    )
    assert re.search(
        submit_ref.value["pattern"],
        'bohr job submit -i job.json -g "$GROUP_ID" -o json',
    )
    assert not re.search(
        submit_ref.value["pattern"],
        'bohr job submit -i job.json -o json',
    )


def test_bwo_node_ssh_scp_v2_requires_real_lifecycle_and_cleanup() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_node_ssh_scp_D7_20260715_v2")
    assert all(q.id != "BWO_node_ssh_scp_D7_20260715" for q in questions)

    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "node_ops_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "node_id": 1507959,
        "image": "registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1",
        "machine_type": "c16_m62_1 * NVIDIA T4",
        "ssh_command": "ssh -p 22022 root@node.example",
        "remote_path": "/personal/test",
        "file_transferred": True,
        "node_deleted": True,
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate({**valid, "node_id": 0})
    with pytest.raises(ValidationError):
        validator.validate({**valid, "machine_type": "c2_m4_cpu"})
    with pytest.raises(ValidationError):
        validator.validate({**valid, "node_deleted": False})

    commands_by_key = {
        "resources_queried_via_cli": "bohr node resources -o json",
        "node_created_via_cli": (
            "bohr node create -n smoke -P 123 -i image -m machine -o json"
        ),
        "node_inspected_via_cli": "bohr node get 1507959 -o json",
        "file_transferred_via_scp": (
            "scp -P 22022 test root@node.example:/personal/test"
        ),
        "node_deleted_via_cli": "bohr node delete 1507959 -y -o json",
    }
    refs_by_key = {ref.key: ref for ref in question.reference_answers}
    for key, command in commands_by_key.items():
        assert re.search(refs_by_key[key].value["pattern"], command)
    assert not re.search(
        refs_by_key["file_transferred_via_scp"].value["pattern"],
        "scp -P 22022 test root@node.example:/tmp/test",
    )
