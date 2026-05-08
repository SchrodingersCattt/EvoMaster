from __future__ import annotations

from evaluation.scripts.coverage.extract_and_match import (
    apply_actionability,
    build_report,
    load_actionability_config,
)


def _rule(
    *,
    source_name: str,
    source_type: str = "skill",
    rule_type: str = "general",
    text: str = "rule text",
    covered: bool = False,
) -> dict:
    return {
        "id": f"{source_name}/{rule_type}",
        "source_name": source_name,
        "source_type": source_type,
        "section": "General",
        "rule_type": rule_type,
        "text": text,
        "is_covered": covered,
        "covered_by": ["Q1"] if covered else [],
    }


def test_actionable_summary_counts_only_testable_rules() -> None:
    rules = apply_actionability(
        [
            _rule(source_name="vasp", rule_type="hard_guard", covered=True),
            _rule(source_name="cp2k", rule_type="physical_check", covered=False),
            _rule(
                source_name="system_prompt",
                source_type="system_prompt",
                text="Output is displayed to the user in GitHub-flavored Markdown.",
            ),
            _rule(
                source_name="read_tool",
                source_type="tool",
                text="Parameter 'file_path': File path to read.",
            ),
            _rule(
                source_name="orca",
                rule_type="config_default",
                text="image | `registry.dp.tech/dptech/dp/native/prod-19853/orca:v6.1.1`",
            ),
        ],
        config={},
    )

    report = build_report(rules)
    summary = report["summary"]

    assert summary["total_rules"] == 5
    assert summary["covered_rules"] == 1
    assert summary["coverage_pct"] == 20.0

    assert summary["actionable_total"] == 2
    assert summary["actionable_covered"] == 1
    assert summary["actionable_uncovered"] == 1
    assert summary["actionable_pct"] == 50.0

    assert summary["by_actionability"]["testable"]["total"] == 2
    assert summary["by_actionability"]["policy_only"]["total"] == 1
    assert summary["by_actionability"]["tool_schema"]["total"] == 1
    assert summary["by_actionability"]["runtime_dependent"]["total"] == 1


def test_uncovered_critical_defaults_to_actionable_rules() -> None:
    rules = apply_actionability(
        [
            _rule(source_name="cp2k", rule_type="physical_check", covered=False),
            _rule(
                source_name="system_prompt",
                source_type="system_prompt",
                rule_type="hard_guard",
                text="Policy-only hard guard.",
            ),
            _rule(
                source_name="orca",
                rule_type="config_default",
                text="machine | `c32_m128_cpu`",
            ),
        ],
        config={},
    )

    report = build_report(rules)

    assert [item["source_name"] for item in report["uncovered_critical"]] == ["cp2k"]
    assert {item["actionability"] for item in report["excluded_from_actionable"]} == {
        "policy_only",
        "runtime_dependent",
    }


def test_actionability_config_can_override_exact_rule_ids() -> None:
    rules = apply_actionability(
        [
            _rule(
                source_name="system_prompt",
                source_type="system_prompt",
                text="Validate that parameters match the physical system.",
            )
        ],
        config={
            "id_overrides": {
                "system_prompt/general": {
                    "actionability": "testable",
                    "reason": "explicitly enforced by physical-consistency tasks",
                }
            }
        },
    )

    assert rules[0]["actionability"] == "testable"
    assert rules[0]["is_actionable"] is True
    assert rules[0]["actionability_reason"] == (
        "explicitly enforced by physical-consistency tasks"
    )


def test_internal_skill_workflow_rules_are_not_actionable_question_targets() -> None:
    config = load_actionability_config()
    rules = apply_actionability(
        [
            _rule(
                source_name="input-manual-helper",
                rule_type="workflow_step",
                text="For rendered engines, run: `uv run python <skill_dir>/scripts/render_input.py`",
            ),
            _rule(
                source_name="input-manual-helper",
                rule_type="workflow_step",
                text="Write `input_prep_manifest.json` with `scripts/write_manifest.py`.",
            ),
            _rule(
                source_name="input-manual-helper",
                rule_type="workflow_step",
                text="Resolve `skill_dir` to this skill directory.",
            ),
            _rule(
                source_name="input-manual-helper",
                rule_type="decision_tree",
                text="Generate or adapt input files for ABACUS, CP2K, QE, ABINIT, LAMMPS, ORCA, GROMACS, or PySCF.",
            ),
            _rule(
                source_name="input-manual-helper",
                rule_type="workflow_step",
                text="For rendered engines, run:",
            ),
        ],
        config=config,
    )

    assert {rule["actionability"] for rule in rules} == {"policy_only"}
    assert all(not rule["is_actionable"] for rule in rules)
