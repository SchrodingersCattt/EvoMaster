from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "matmaster" / "skills" / "input-manual-helper"
SKILL_MD = SKILL_DIR / "SKILL.md"


def test_skill_lives_in_top_level_skills_directory() -> None:
    assert SKILL_MD.exists()
    assert not (
        REPO_ROOT / "matmaster" / "skills" / "playground-skills" / "input-manual-helper"
    ).exists()


def test_registry_discovers_skill_from_top_level_root() -> None:
    from matmaster.skills.registry import SkillRegistry

    registry = SkillRegistry(REPO_ROOT / "matmaster" / "skills")
    skill = registry.get_skill("input-manual-helper")

    assert skill is not None
    assert skill.skill_path == SKILL_DIR


def _skill_parts() -> tuple[dict, str]:
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, body = text.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_description_only_describes_trigger_conditions() -> None:
    frontmatter, _ = _skill_parts()
    description = frontmatter["description"]

    assert description.startswith("Use when ")
    assert len(description) <= 500
    assert "render_input.py" not in description
    assert "diagnose_input.py" not in description
    assert "Do not hand-write" not in description


def test_skill_is_thin_router_with_reference_contracts() -> None:
    _, body = _skill_parts()

    assert "references/workflow_contract.md" in body
    assert "references/engine_routes.md" in body
    assert "input_prep_manifest.json" in body
    assert len(body.splitlines()) <= 120

    duplicated_engine_sections = re.findall(
        r"^### .+ input generation", body, flags=re.MULTILINE
    )
    assert duplicated_engine_sections == []


def test_workflow_contract_defines_manifest_and_diagnostic_gate() -> None:
    contract = (SKILL_DIR / "references" / "workflow_contract.md").read_text(
        encoding="utf-8"
    )

    for field in (
        "software",
        "task",
        "input_dir",
        "generated_files",
        "user_provided_files",
        "diagnostics",
        "auxiliary_files",
        "assumptions",
        "submit_ready",
        "bohrium_command",
    ):
        assert field in contract

    assert "--json_out" in contract
    assert "error" in contract.lower()
    assert "blocker" in contract.lower()


def test_engine_routes_keep_engine_specific_rules_out_of_skill_body() -> None:
    routes = (SKILL_DIR / "references" / "engine_routes.md").read_text(encoding="utf-8")

    for engine in (
        "ABACUS",
        "CP2K",
        "QE",
        "ABINIT",
        "LAMMPS",
        "ORCA",
        "GROMACS",
        "PySCF",
    ):
        assert engine in routes

    assert "Engine-specific physical rules stay in the engine skill" in routes


def test_diagnose_input_writes_json_out(tmp_path: Path) -> None:
    input_file = tmp_path / "pw.in"
    json_out = tmp_path / "diagnosis.json"
    input_file.write_text(
        "&CONTROL\n"
        "  calculation = 'scf'\n"
        "/\n"
        "&SYSTEM\n"
        "  ibrav = 1\n"
        "  celldm(1) = 10.0\n"
        "  nat = 1\n"
        "  ntyp = 1\n"
        "  ecutwfc = 40\n"
        "/\n"
        "&ELECTRONS\n"
        "/\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "diagnose_input.py"),
            "--software",
            "qe",
            "--input",
            str(input_file),
            "--json_out",
            str(json_out),
        ],
        cwd=SKILL_DIR,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode in (0, 1)
    assert json_out.exists()
    parsed = yaml.safe_load(json_out.read_text(encoding="utf-8"))
    assert "diagnostics" in parsed
