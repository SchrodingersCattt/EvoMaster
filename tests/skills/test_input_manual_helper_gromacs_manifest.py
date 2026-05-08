from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "matmaster" / "skills" / "input-manual-helper"


def test_diagnose_input_supports_gromacs_json_out(tmp_path: Path) -> None:
    mdp = tmp_path / "em.mdp"
    diagnosis = tmp_path / "diagnosis.json"
    mdp.write_text(
        "\n".join(
            [
                "integrator = steep",
                "nsteps = 50000",
                "emtol = 1000.0",
                "emstep = 0.01",
                "cutoff-scheme = Verlet",
                "coulombtype = PME",
                "rcoulomb = 1.0",
                "rvdw = 1.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "diagnose_input.py"),
            "--software",
            "gromacs",
            "--input",
            str(mdp),
            "--json_out",
            str(diagnosis),
        ],
        cwd=SKILL_DIR,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(diagnosis.read_text(encoding="utf-8"))
    assert payload["diagnostics"] == []


def test_render_input_supports_gromacs_energy_minimization(tmp_path: Path) -> None:
    output = tmp_path / "em.mdp"

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "render_input.py"),
            "--software",
            "gromacs",
            "--task",
            "em",
            "--output",
            str(output),
            "--param",
            "nsteps=50000",
            "--param",
            "emtol=1000.0",
            "--param",
            "emstep=0.01",
        ],
        cwd=SKILL_DIR,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert "integrator              = steep" in text
    assert "nsteps                  = 50000" in text
    assert "emtol                   = 1000.0" in text
    assert "emstep                  = 0.01" in text


def test_write_manifest_normalizes_diagnostics_and_keeps_command_key(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_cp2k_bad"
    run_dir.mkdir()
    diagnosis = run_dir / "diagnosis.json"
    diagnosis.write_text(
        json.dumps(
            {
                "diagnostics": [
                    {"severity": "error", "param": "CUTOFF", "message": "too low"},
                    {"severity": "warning", "param": "EPS_SCF", "message": "loose"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "scripts" / "write_manifest.py"),
            "--software",
            "cp2k",
            "--task",
            "scf",
            "--input-dir",
            str(run_dir),
            "--diagnosis",
            str(diagnosis),
            "--user-provided-file",
            "input.inp",
            "--submit-ready",
            "false",
        ],
        cwd=SKILL_DIR,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads((run_dir / "input_prep_manifest.json").read_text())
    assert manifest["diagnostics"] == {
        "file": "diagnosis.json",
        "errors": 1,
        "warnings": 1,
        "blockers": 1,
    }
    assert manifest["submit_ready"] is False
    assert manifest["bohrium_command"] == ""
