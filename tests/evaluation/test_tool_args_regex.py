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


def test_bwo_sandbox_ase_v2_requires_real_lifecycle_and_cleanup() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_sandbox_ase_007_20260715_v2")
    question_ids = {q.id for q in questions}
    assert "BWO_sandbox_ase_007_20260715" not in question_ids
    assert "BEC_no_history_009_20260715" not in question_ids
    assert question.tags == ["bohr-cli"]
    prompt = question.human_prompt_seed.lower()
    assert "清华" not in prompt
    assert "pypi" not in prompt
    assert "tuna" not in prompt
    assert "ase==" not in prompt
    file_refs = {
        ref.key: ref
        for ref in question.reference_answers
        if ref.key
        in {
            "xyz_artifact",
            "log_artifact",
            "xyz_has_atoms",
            "xyz_three_atoms",
            "log_has_energy",
        }
    }
    assert all(ref.workspace_resolve == "root" for ref in file_refs.values())

    script_path = QUESTION_BANK_DIR / question.data_files[0].path
    compile(script_path.read_text(encoding="utf-8"), str(script_path), "exec")

    commands_by_key = {
        "sandbox_created_via_cli": "bohr sandbox create sac-cpu-small -o json",
        "script_uploaded_via_cli": (
            "bohr sandbox files write 123 /tmp/opt_water.py --source opt_water.py"
        ),
        "script_executed_via_cli": (
            "bohr sandbox exec 123 --command 'python /tmp/opt_water.py'"
        ),
        "sandbox_deleted_via_cli": "bohr sandbox delete 123 --yes",
    }
    refs_by_key = {ref.key: ref for ref in question.reference_answers}
    for key, command in commands_by_key.items():
        assert re.search(refs_by_key[key].value["pattern"], command)

    download_pattern = refs_by_key["results_downloaded_via_cli"].value["pattern"]
    assert re.search(
        download_pattern,
        "bohr sandbox files read 123 /tmp/b7_water_optimized.xyz "
        "--destination b7_water_optimized.xyz",
    )
    assert re.search(
        download_pattern,
        "bohr sandbox files read 123 /tmp/b7_log.txt --destination b7_log.txt",
    )
    assert not re.search(
        refs_by_key["sandbox_created_via_cli"].value["pattern"],
        "bohr sandbox create ch4-deepmd -o json",
    )


def test_bwo_stop_running_v2_is_isolated_and_uses_terminate() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_stop_running_009_20260715_v2")
    assert all(q.id != "BWO_stop_running_009_20260715" for q in questions)
    assert question.tags == ["bohr-cli"]
    prompt = question.human_prompt_seed.lower()
    assert "terminate" not in prompt
    assert "kill" not in prompt
    assert "stopped" not in prompt

    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "stop_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "job_id": 20409999,
        "job_name": "b9-stop-running-1721123456",
        "image": "registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1",
        "machine_type": "c2_m4_cpu",
        "command": 'echo "b9 started" > b9_started.txt && sleep 600',
        "polls": [
            {"time": "2026-07-16 15:00:00", "status": 1},
            {"time": "2026-07-16 15:00:10", "status": 4},
            {"time": "2026-07-16 15:00:20", "status": 5},
        ],
        "action": "terminate",
        "final_status": 5,
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate({**valid, "action": "kill"})
    with pytest.raises(ValidationError):
        validator.validate({**valid, "final_status": 4})
    with pytest.raises(ValidationError):
        validator.validate({**valid, "polls": valid["polls"][1:]})

    refs_by_key = {ref.key: ref for ref in question.reference_answers}
    commands_by_key = {
        "submitted_via_cli": (
            "bohr job submit --job_name b9-stop-running-1721123456 "
            "--command 'echo \"b9 started\" > b9_started.txt && sleep 600'"
        ),
        "polled_via_cli": "bohr job describe -j 20409999 --json",
        "one_control_action_via_cli": "bohr job terminate 20409999",
        "terminated_via_cli": "bohr job terminate 20409999",
    }
    for key, command in commands_by_key.items():
        assert re.search(refs_by_key[key].value["pattern"], command)

    control_ref = refs_by_key["one_control_action_via_cli"]
    assert control_ref.value["min_matches"] == 1
    assert control_ref.value["max_matches"] == 1
    assert re.search(control_ref.value["pattern"], "bohr job kill 20409999")
    assert not re.search(
        refs_by_key["terminated_via_cli"].value["pattern"],
        "bohr job kill 20409999",
    )


def test_bwo_gpu_compare_v2_uses_live_job_inventory() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_gpu_compare_004_20260715_v2")
    assert all(q.id != "BWO_gpu_compare_004_20260715" for q in questions)
    assert question.tags == ["bohr-cli"]
    assert "job 场景" not in question.human_prompt_seed
    assert "单卡 NVIDIA" not in question.human_prompt_seed
    assert "machine list" not in question.human_prompt_seed

    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "comparison_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "workload": {"framework": "DeepMD", "atom_count": 500},
        "available_machines": [
            {
                "sku_id": 740,
                "machine_type": "1 * NVIDIA T4_16g",
                "gpu_model": "NVIDIA T4",
                "gpu_count": 1,
                "gpu_memory_gb": 16,
                "price_cny_per_hour": 2.5,
                "has_stock": True,
            },
            {
                "sku_id": 738,
                "machine_type": "1 * NVIDIA V100_32g",
                "gpu_model": "NVIDIA V100",
                "gpu_count": 1,
                "gpu_memory_gb": 32,
                "price_cny_per_hour": 4.5,
                "has_stock": True,
            },
            {
                "sku_id": 4675,
                "machine_type": "1 * NVIDIA A100_80g",
                "gpu_model": "NVIDIA A100",
                "gpu_count": 1,
                "gpu_memory_gb": 80,
                "price_cny_per_hour": 10,
                "has_stock": True,
            },
        ],
        "recommendation": {
            "machine_type": "1 * NVIDIA V100_32g",
            "reason": "Balances memory capacity, training throughput, and hourly cost.",
        },
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate(
            {**valid, "workload": {"framework": "DeepMD", "atom_count": 499}}
        )
    with pytest.raises(ValidationError):
        validator.validate(
            {
                **valid,
                "available_machines": [
                    *valid["available_machines"][:2],
                    {**valid["available_machines"][2], "gpu_count": 0},
                ],
            }
        )
    with pytest.raises(ValidationError):
        validator.validate(
            {
                **valid,
                "available_machines": [
                    *valid["available_machines"][:2],
                    {**valid["available_machines"][2], "has_stock": False},
                ],
            }
        )

    query_ref = next(
        ref
        for ref in question.reference_answers
        if ref.key == "machines_queried_via_cli"
    )
    pattern = query_ref.value["pattern"]
    assert re.search(pattern, "bohr machine list -c gpu -s job -o json")
    assert re.search(
        pattern,
        "bohr machine list --scene=job --chooseType=gpu --output json",
    )
    assert not re.search(pattern, "bohr machine list -c cpu -s job -o json")
    assert not re.search(pattern, "bohr machine list -c gpu -s node -o json")
