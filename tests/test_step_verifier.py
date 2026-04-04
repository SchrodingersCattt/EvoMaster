"""Step verifier (shared by direct and planner)."""

import tempfile
from pathlib import Path

from playground.mat_master.core.step_verifier import (
    StepContract,
    verify_step_deterministic,
)


def test_verify_step_deterministic_missing_artifacts():
    contract = StepContract(
        expected_artifacts=["results/table.csv", "scripts/build_dataset.py"]
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "other.txt").write_text("x")
        ver = verify_step_deterministic(contract, tmp, produced_files=[])
        assert ver["artifact_match"] is False
        assert "results/table.csv" in ver["missing_artifacts"]
        assert ver["completion_ratio"] == 0.0
        assert ver["drift_reason"] == ""


def test_verify_step_deterministic_delivered():
    contract = StepContract(expected_artifacts=["results/table.csv"])
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "results"
        out_dir.mkdir()
        (out_dir / "table.csv").write_text("a,b\n1,2\n")
        ver = verify_step_deterministic(contract, tmp, produced_files=[])
        assert ver["artifact_match"] is True
        assert ver["missing_artifacts"] == []
        assert ver["completion_ratio"] == 1.0


def test_verify_step_deterministic_without_explicit_contract():
    contract = StepContract()
    with tempfile.TemporaryDirectory() as tmp:
        ver = verify_step_deterministic(contract, tmp, produced_files=[])
        assert ver["artifact_match"] is True
        assert ver["missing_artifacts"] == []
        assert ver["completion_ratio"] == 1.0
