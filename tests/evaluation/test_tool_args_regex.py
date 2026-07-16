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


def _scripted_tool_regex_question() -> QuestionItem:
    return QuestionItem(
        id="scripted_tool_regex_test",
        capability="workflow_orchestration",
        domain="agnostic",
        intent="Verify direct or scripted CLI polling.",
        human_prompt_seed="Poll the job.",
        reference_answers=[
            ReferenceAnswer(
                key="polled",
                tool_name="Bash",
                tool_arg="command",
                value={
                    "direct_pattern": BOHR_COMMAND_PATTERN,
                    "script_pattern": (
                        r'["\']bohr["\']\s*,\s*["\']job["\']\s*,\s*'
                        r'["\']describe["\']'
                    ),
                    "min_matches": 1,
                },
            )
        ],
        scoring_checklist=[
            ScoringCheckItem(
                id="polled",
                criterion="Job was polled through the CLI.",
                axis="grounding",
                verify="scripted_tool_args_regex",
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


def test_scripted_tool_args_regex_accepts_written_and_executed_script() -> None:
    record = BinaryEvaluator().evaluate(
        question=_scripted_tool_regex_question(),
        answer="done",
        tool_calls=[
            {
                "tool_name": "Write",
                "tool_args": {
                    "file_path": "/workspace/poll_job.py",
                    "content": (
                        'subprocess.run(["bohr", "job", "describe", "-i", job_id])'
                    ),
                },
            },
            {
                "tool_name": "Bash",
                "tool_args": {"command": "cd /workspace && python3 poll_job.py"},
            },
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is True
    assert "linked_scripts=1" in result.reason


def test_scripted_tool_args_regex_accepts_heredoc_script_executed_later() -> None:
    record = BinaryEvaluator().evaluate(
        question=_scripted_tool_regex_question(),
        answer="done",
        tool_calls=[
            {
                "tool_name": "Bash",
                "tool_args": {
                    "command": (
                        "cat > poll_job.py << 'SCRIPT'\n"
                        "import subprocess\n"
                        'subprocess.run(["bohr", "job", "describe", "-i", job_id])\n'
                        "SCRIPT\n"
                        "chmod +x poll_job.py"
                    )
                },
            },
            {
                "tool_name": "Bash",
                "tool_args": {"command": "python3 poll_job.py"},
            },
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is True
    assert "linked_scripts=1" in result.reason


def test_scripted_tool_args_regex_rejects_unexecuted_heredoc_script() -> None:
    record = BinaryEvaluator().evaluate(
        question=_scripted_tool_regex_question(),
        answer="done",
        tool_calls=[
            {
                "tool_name": "Bash",
                "tool_args": {
                    "command": (
                        "cat <<'SCRIPT' > poll_job.py\n"
                        "import subprocess\n"
                        'subprocess.run(["bohr", "job", "describe", "-i", job_id])\n'
                        "python3 -c 'print(\"helper\")'\n"
                        "SCRIPT"
                    )
                },
            }
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is False
    assert "linked_scripts=0" in result.reason


def test_scripted_tool_args_regex_accepts_direct_command() -> None:
    record = BinaryEvaluator().evaluate(
        question=_scripted_tool_regex_question(),
        answer="done",
        tool_calls=[
            {
                "tool_name": "Bash",
                "tool_args": {"command": "bohr job describe -i 20400713"},
            }
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is True
    assert "direct=1" in result.reason


def test_scripted_tool_args_regex_rejects_unexecuted_script() -> None:
    record = BinaryEvaluator().evaluate(
        question=_scripted_tool_regex_question(),
        answer="done",
        tool_calls=[
            {
                "tool_name": "Write",
                "tool_args": {
                    "file_path": "/workspace/poll_job.py",
                    "content": (
                        'subprocess.run(["bohr", "job", "describe", "-i", job_id])'
                    ),
                },
            }
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is False
    assert "linked_scripts=0" in result.reason


def test_scripted_tool_args_regex_accepts_inline_script_execution() -> None:
    record = BinaryEvaluator().evaluate(
        question=_scripted_tool_regex_question(),
        answer="done",
        tool_calls=[
            {
                "tool_name": "Bash",
                "tool_args": {
                    "command": (
                        "cat > poll_job.py <<'PY'\n"
                        'subprocess.run(["bohr", "job", "describe", "-i", job_id])\n'
                        "PY\npython3 poll_job.py"
                    )
                },
            }
        ],
    )

    result = record.criteria_results["polled"]
    assert result.passed is True
    assert "inline_scripts=1" in result.reason


@pytest.mark.parametrize(
    "value",
    [
        {"script_pattern": "bohr"},
        {"direct_pattern": "bohr"},
        {
            "direct_pattern": "bohr",
            "script_pattern": "describe",
            "min_matches": 0,
        },
        {
            "direct_pattern": "bohr",
            "script_pattern": "describe",
            "unknown": True,
        },
    ],
)
def test_scripted_tool_args_regex_reference_is_validated(value: object) -> None:
    with pytest.raises(
        PydanticValidationError, match="scripted_tool_args_regex reference"
    ):
        QuestionItem(
            id="invalid_scripted_tool_regex",
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
                    verify="scripted_tool_args_regex",
                )
            ],
        )


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


def test_bwo_monitor_v6_uses_execution_receipts_without_prompt_hints() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_monitor_D6_20260715_v6")
    assert all(
        q.id
        not in {
            "BWO_monitor_D6_20260715",
            "BWO_monitor_D6_20260715_v2",
            "BWO_monitor_D6_20260715_v3",
            "BWO_monitor_D6_20260715_v4",
            "BWO_monitor_D6_20260715_v5",
        }
        for q in questions
    )
    assert "receipt" not in question.human_prompt_seed.lower()
    assert "`job_id`" not in question.human_prompt_seed
    assert "`final_status`" not in question.human_prompt_seed
    execution_ref = next(
        ref for ref in question.reference_answers if ref.key == "monitor_execution"
    )
    assert execution_ref.value == {
        "filename": "d6_monitor.json",
        "log_filename": "d6_job.log",
        "image": "registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1",
        "machine_type": "c2_m4_cpu",
        "command": 'echo "hello from bohrium" | tee result.txt && sleep 60',
    }
    execution_check = next(
        item for item in question.scoring_checklist if item.id == "monitor_execution"
    )
    assert execution_check.verify == "bohr_job_monitor_execution"


def test_bwo_lit_db_v3_accepts_text_or_structured_batch_strategy() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_lit_db_D5_20260715_v3")
    assert all(
        q.id
        not in {
            "BWO_lit_db_D5_20260715",
            "BWO_lit_db_D5_20260715_v2",
        }
        for q in questions
    )

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
    validator.validate(
        {
            **valid,
            "batch_strategy": {
                "batch_size": 5,
                "stages": ["normalize", "deduplicate", "persist"],
            },
        }
    )
    with pytest.raises(ValidationError):
        validator.validate({**valid, "papers_found": 19})
    with pytest.raises(ValidationError):
        validator.validate(
            {
                **valid,
                "schema": {"fields": valid["schema"]["fields"][:2]},
            }
        )
    with pytest.raises(ValidationError):
        validator.validate({**valid, "batch_strategy": ""})
    with pytest.raises(ValidationError):
        validator.validate({**valid, "batch_strategy": {}})

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


def test_bwo_param_sweep_v5_uses_execution_receipts_without_prompt_hints() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_param_sweep_003_20260715_v5")
    assert all(
        q.id
        not in {
            "BWO_param_sweep_003_20260715",
            "BWO_param_sweep_003_20260715_v2",
            "BWO_param_sweep_003_20260715_v3",
            "BWO_param_sweep_003_20260715_v4",
        }
        for q in questions
    )
    assert "`job_group_id`" not in question.human_prompt_seed
    assert "`task_group_id`" not in question.human_prompt_seed
    assert "receipt" not in question.human_prompt_seed.lower()
    execution_ref = next(
        ref for ref in question.reference_answers if ref.key == "sweep_execution"
    )
    assert execution_ref.value == {"filename": "b3_jobs.json"}
    execution_check = next(
        item for item in question.scoring_checklist if item.id == "sweep_execution"
    )
    assert execution_check.verify == "bohr_parameter_sweep_execution"


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


def test_bwo_stop_running_v4_is_isolated_and_accepts_stop_fallbacks() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_stop_running_009_20260715_v4")
    assert all(
        q.id
        not in {
            "BWO_stop_running_009_20260715",
            "BWO_stop_running_009_20260715_v2",
            "BWO_stop_running_009_20260715_v3",
        }
        for q in questions
    )
    assert question.tags == ["bohr-cli"]
    prompt = question.human_prompt_seed.lower()
    for leaked_term in (
        "bohrjobid",
        "jobid",
        "jobgroupid",
        "terminate",
        "kill",
        "stopped",
    ):
        assert leaked_term not in prompt

    checklist_by_id = {item.id: item for item in question.scoring_checklist}
    assert set(checklist_by_id) == {"artifact", "stop_execution", "turn_budget"}
    assert (
        checklist_by_id["stop_execution"].verify == "bohr_job_stop_execution"
    )

    refs_by_key = {ref.key: ref for ref in question.reference_answers}
    assert set(refs_by_key) == {"artifact", "stop_execution", "turn_budget"}


def test_bwo_gpu_compare_v4_accepts_forward_compatible_output() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BWO_gpu_compare_004_20260715_v4")
    assert all(
        q.id
        not in {
            "BWO_gpu_compare_004_20260715",
            "BWO_gpu_compare_004_20260715_v2",
            "BWO_gpu_compare_004_20260715_v3",
        }
        for q in questions
    )
    assert question.tags == ["bohr-cli"]
    assert "用于提交计算任务" in question.human_prompt_seed
    assert "machine list" not in question.human_prompt_seed
    for field in (
        "workload",
        "available_machines",
        "recommendation",
        "sku_id",
        "gpu_memory_gb",
        "price_cny_per_hour",
        "has_stock",
    ):
        assert f"`{field}`" in question.human_prompt_seed

    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "comparison_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "workload": {
            "framework": "DeepMD-kit",
            "atom_count": 500,
            "note": "single-GPU training",
        },
        "available_machines": [
            {
                "sku_id": sku_id,
                "machine_type": machine_type,
                "gpu_model": gpu_model,
                "gpu_count": 1,
                "gpu_memory_gb": memory,
                "price_cny_per_hour": price,
                "has_stock": has_stock,
                "tags": ["promotion"] if sku_id == 740 else [],
            }
            for sku_id, machine_type, gpu_model, memory, price, has_stock in (
                (740, "1 * NVIDIA T4_16g", "NVIDIA T4", 16, 2.5, False),
                (738, "1 * NVIDIA V100_32g", "NVIDIA V100", 32, 4.5, True),
                (4675, "1 * NVIDIA A100_80g", "NVIDIA A100", 80, 10, True),
            )
        ],
        "recommendation": {
            "machine_type": "1 * NVIDIA V100_32g",
            "reason": "Balances memory capacity, training throughput, and hourly cost.",
            "sku_id": 738,
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
                    {**machine, "has_stock": False}
                    for machine in valid["available_machines"]
                ],
            }
        )
    with pytest.raises(ValidationError):
        validator.validate(
            {
                "workload": valid["workload"],
                "candidate_machines": valid["available_machines"],
                "recommendation": valid["recommendation"],
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


def test_bsa_tools_docking_v2_requires_search_and_tool_details() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(q for q in questions if q.id == "BSA_tools_docking_012_20260715_v2")
    assert all(q.id != "BSA_tools_docking_012_20260715" for q in questions)
    assert question.tags == ["bohr-cli"]
    assert "tools search" not in question.human_prompt_seed
    assert "tools info" not in question.human_prompt_seed
    assert "docking_py" not in question.human_prompt_seed

    schema_ref = next(
        ref for ref in question.reference_answers if ref.key == "tools_schema"
    )
    schema = schema_ref.value["schema"]
    validator = validator_for(schema)(schema)
    valid = {
        "query": "protein ligand docking",
        "candidates": [
            {
                "name": "Docking Tool A",
                "tool_unique_key": "owner_a_docking-tool",
                "repo_url": "https://example.com/a",
            },
            {
                "name": "Docking Tool B",
                "tool_unique_key": "owner_b_docking-tool",
                "repo_url": "https://example.com/b",
            },
            {
                "name": "Docking Tool C",
                "tool_unique_key": "owner_c_docking-tool",
                "repo_url": "https://example.com/c",
            },
        ],
        "selected_tool": {
            "name": "Docking Tool A",
            "tool_unique_key": "owner_a_docking-tool",
            "version": "v1.2.3",
            "description": "Automates protein-ligand docking and result analysis.",
            "usage_entry_command": "docking-tool --help",
            "help_urls": ["https://example.com/a/docs"],
            "usage_steps": [
                "Prepare the receptor structure.",
                "Prepare the ligand and search box.",
                "Run docking and inspect ranked poses.",
            ],
        },
    }
    validator.validate(valid)
    with pytest.raises(ValidationError):
        validator.validate({**valid, "candidates": valid["candidates"][:2]})
    with pytest.raises(ValidationError):
        validator.validate(
            {
                **valid,
                "selected_tool": {
                    **valid["selected_tool"],
                    "usage_steps": valid["selected_tool"]["usage_steps"][:2],
                },
            }
        )

    refs_by_key = {ref.key: ref for ref in question.reference_answers}
    search_pattern = refs_by_key["tools_searched_via_cli"].value["pattern"]
    assert re.search(
        search_pattern, 'bohr tools search "protein ligand docking" -o json'
    )
    assert re.search(search_pattern, "bohr tools search 小分子对接 --lang zh-CN")
    assert not re.search(search_pattern, "bohr tools search molecular-dynamics -o json")

    info_pattern = refs_by_key["tool_details_queried_via_cli"].value["pattern"]
    assert re.search(info_pattern, "bohr tools info owner_docking-tool -o json")
    assert re.search(info_pattern, 'bohr tools info "owner_docking_tool" -o json')
    assert not re.search(info_pattern, "bohr tools info 28189 -o json")


def test_bec_upgrade_machine_v4_preserves_seed_job_config_without_leaking_ids() -> None:
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        q for q in questions if q.id == "BEC_upgrade_machine_006_20260715_v4"
    )
    assert all(
        q.id
        not in {
            "BEC_upgrade_machine_006_20260715",
            "BEC_upgrade_machine_006_20260715_v2",
            "BEC_upgrade_machine_006_20260715_v3",
        }
        for q in questions
    )
    assert question.tags == ["bohr-cli"]
    prompt = question.human_prompt_seed
    for leaked_term in (
        "job describe",
        "machine list",
        "job submit",
        "bohr_id",
        "job_id",
        "original_job",
        "resubmitted_job",
        "dpmd-cu126-outisli",
        "T4 test for eval E6",
        "T4",
        "被中止",
    ):
        assert leaked_term not in prompt

    checklist_by_id = {item.id: item for item in question.scoring_checklist}
    assert checklist_by_id["upgrade_record"].verify == "bohr_job_upgrade_record"

    refs_by_key = {ref.key: ref for ref in question.reference_answers}
    describe_pattern = refs_by_key["original_job_queried_via_cli"].value["pattern"]
    assert re.search(describe_pattern, "bohr job describe -i 20400341 -o json")
    assert re.search(describe_pattern, "bohr job describe --id=20400341 --output json")
    assert not re.search(describe_pattern, "bohr job describe -i 23052040 -o json")

    machine_pattern = refs_by_key["a100_machines_queried_via_cli"].value["pattern"]
    assert re.search(machine_pattern, "bohr machine list -c gpu -s job -o json")
    assert not re.search(machine_pattern, "bohr machine list -c gpu -s node -o json")

    submit_pattern = refs_by_key["job_resubmitted_via_cli"].value["pattern"]
    valid_submit = (
        "bohr job submit -n e6-upgrade "
        "-m registry.dp.tech/dptech/dpmd-cu126-outisli:v20260712 "
        "-t 'c16_m60_1 * NVIDIA A100_80g' "
        '-c "echo \'T4 test for eval E6\' > result.txt"'
    )
    assert re.search(submit_pattern, valid_submit)
    assert re.search(submit_pattern, "bohr job submit -i job_a100.json -o json")
    assert re.search(submit_pattern, "bohr job submit --input=job_a100.json -o json")
    assert re.search(
        submit_pattern,
        "cat > job_a100.json << 'EOF'\n{}\nEOF\n"
        "bohr job submit -i job_a100.json -o json",
    )
    assert re.search(
        submit_pattern,
        "cd /tmp/e6 && bohr job submit -i job_a100.json -o json",
    )
    assert not re.search(submit_pattern, "bohr job submit --help")
    assert not re.search(submit_pattern, "bohr job submit -i")
    assert not re.search(submit_pattern, valid_submit.replace("A100", "T4"))
    assert not re.search(submit_pattern, valid_submit.replace("v20260712", "v20260713"))
    assert not re.search(
        submit_pattern,
        valid_submit.replace("T4 test for eval E6", "changed command"),
    )
