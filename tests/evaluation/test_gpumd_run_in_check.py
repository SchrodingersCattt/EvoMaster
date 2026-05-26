"""Tests for evaluation/validators/gpumd_run_in.py — GPUMD run.in semantic checks."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from evaluation.validators.gpumd_run_in import check_gpumd_run_in


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _write(workspace: Path, content: str) -> None:
    (workspace / "run.in").write_text(content)


# ---------------------------------------------------------------------------
# ensemble_type
# ---------------------------------------------------------------------------


class TestEnsembleType:
    def test_pass_npt_scr(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble npt_scr 300 300 100 0 100 2000\nrun 100000\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="ensemble_type", allowed=["npt_ber", "npt_scr"])
        assert ok
        assert "npt_scr" in msg

    def test_pass_heat_nhc(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble heat_nhc 300 300 100 source 1 sink 2\nrun 100\n")
        ok, _ = check_gpumd_run_in(workspace, filename="run.in", check="ensemble_type", allowed=["heat_lan", "heat_nhc", "heat_bdp"])
        assert ok

    def test_fail_wrong_type(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nvt_nhc 300 300 100\nrun 100\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="ensemble_type", allowed=["npt_ber", "npt_scr"])
        assert not ok
        assert "nvt_nhc" in msg

    def test_fail_no_ensemble(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nrun 100\n")
        ok, _ = check_gpumd_run_in(workspace, filename="run.in", check="ensemble_type", allowed=["nve"])
        assert not ok

    def test_multiple_blocks_any_match(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nvt_nhc 300 300 100\nrun 100\nensemble nve\nrun 200\n")
        ok, _ = check_gpumd_run_in(workspace, filename="run.in", check="ensemble_type", allowed=["nve"])
        assert ok

    def test_missing_allowed_arg(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nve\nrun 100\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="ensemble_type", allowed=None)
        assert not ok
        assert "'allowed' list" in msg


# ---------------------------------------------------------------------------
# has_keyword
# ---------------------------------------------------------------------------


class TestHasKeyword:
    def test_pass_all_present(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nvelocity 300\ntime_step 1\nensemble nve\nrun 100\n")
        ok, _ = check_gpumd_run_in(workspace, filename="run.in", check="has_keyword", expected=["potential", "velocity", "run"])
        assert ok

    def test_fail_missing(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nve\nrun 100\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="has_keyword", expected=["velocity", "run"])
        assert not ok
        assert "velocity" in msg

    def test_ignores_comments(self, workspace: Path) -> None:
        _write(workspace, "# velocity 300\npotential nep.txt\nrun 100\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="has_keyword", expected=["velocity"])
        assert not ok

    def test_single_keyword_string(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nrun 100\n")
        ok, _ = check_gpumd_run_in(workspace, filename="run.in", check="has_keyword", expected="potential")
        assert ok


# ---------------------------------------------------------------------------
# has_any_keyword_set
# ---------------------------------------------------------------------------


class TestHasAnyKeywordSet:
    def test_pass_first_set(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nreplicate 4 4 4\ncompute_phonon 0.01\n")
        ok, msg = check_gpumd_run_in(
            workspace, filename="run.in", check="has_any_keyword_set",
            allowed=[["compute_phonon", "replicate"], ["compute_dos"]],
        )
        assert ok
        assert "compute_phonon" in msg

    def test_pass_second_set(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nve\ncompute_dos 5 200 50\nrun 100\n")
        ok, msg = check_gpumd_run_in(
            workspace, filename="run.in", check="has_any_keyword_set",
            allowed=[["compute_phonon", "replicate"], ["compute_dos"]],
        )
        assert ok
        assert "compute_dos" in msg

    def test_fail_neither(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nve\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="has_any_keyword_set",
            allowed=[["compute_phonon", "replicate"], ["compute_dos"]],
        )
        assert not ok

    def test_partial_set_not_enough(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nreplicate 4 4 4\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="has_any_keyword_set",
            allowed=[["compute_phonon", "replicate"]],
        )
        assert not ok


# ---------------------------------------------------------------------------
# first_keyword
# ---------------------------------------------------------------------------


class TestFirstKeyword:
    def test_pass_with_comments_before(self, workspace: Path) -> None:
        _write(workspace, "# GPUMD simulation\n# More comments\npotential nep.txt\nrun 100\n")
        ok, _ = check_gpumd_run_in(workspace, filename="run.in", check="first_keyword", expected="potential")
        assert ok

    def test_fail_velocity_first(self, workspace: Path) -> None:
        _write(workspace, "velocity 300\npotential nep.txt\nrun 100\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="first_keyword", expected="potential")
        assert not ok
        assert "velocity" in msg

    def test_empty_file(self, workspace: Path) -> None:
        _write(workspace, "# only comments\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="first_keyword", expected="potential")
        assert not ok
        assert "no commands" in msg


# ---------------------------------------------------------------------------
# min_keyword_count
# ---------------------------------------------------------------------------


class TestMinKeywordCount:
    def test_pass_two_potentials(self, workspace: Path) -> None:
        _write(workspace, "potential nep1.txt\npotential nep2.txt\nensemble nve\nrun 100\n")
        ok, msg = check_gpumd_run_in(
            workspace, filename="run.in", check="min_keyword_count",
            expected="potential", allowed=["2"],
        )
        assert ok
        assert "2 time(s)" in msg

    def test_fail_only_one_potential(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nve\nrun 100\n")
        ok, msg = check_gpumd_run_in(
            workspace, filename="run.in", check="min_keyword_count",
            expected="potential", allowed=["2"],
        )
        assert not ok
        assert "1 time(s)" in msg

    def test_pass_three_for_min_two(self, workspace: Path) -> None:
        _write(workspace, "potential a.txt\npotential b.txt\npotential c.txt\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="min_keyword_count",
            expected="potential", allowed=["2"],
        )
        assert ok

    def test_comments_not_counted(self, workspace: Path) -> None:
        _write(workspace, "# potential fake.txt\npotential real.txt\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="min_keyword_count",
            expected="potential", allowed=["2"],
        )
        assert not ok


# ---------------------------------------------------------------------------
# keyword_before
# ---------------------------------------------------------------------------


class TestKeywordBefore:
    def test_pass_potential_before_ensemble(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble nve\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="keyword_before",
            expected=["potential", "ensemble"],
        )
        assert ok

    def test_fail_reversed(self, workspace: Path) -> None:
        _write(workspace, "ensemble nve\npotential nep.txt\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="keyword_before",
            expected=["potential", "ensemble"],
        )
        assert not ok

    def test_fail_missing_before(self, workspace: Path) -> None:
        _write(workspace, "ensemble nve\nrun 100\n")
        ok, msg = check_gpumd_run_in(
            workspace, filename="run.in", check="keyword_before",
            expected=["potential", "ensemble"],
        )
        assert not ok
        assert "not found" in msg


# ---------------------------------------------------------------------------
# param_count
# ---------------------------------------------------------------------------


class TestParamCount:
    def test_pass_npt_scr_6_params(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble npt_scr 300 300 100 0 100 2000\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="param_count",
            expected="ensemble", allowed=["7"],
        )
        assert ok

    def test_fail_wrong_count(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\nensemble npt_scr 300 300 100 0 100 2000\nrun 100\n")
        ok, _ = check_gpumd_run_in(
            workspace, filename="run.in", check="param_count",
            expected="ensemble", allowed=["6", "10", "16"],
        )
        assert not ok


# ---------------------------------------------------------------------------
# Edge cases: file not found, unknown check type
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_file_not_found(self, workspace: Path) -> None:
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="has_keyword", expected=["potential"])
        assert not ok
        assert "no file" in msg

    def test_unknown_check_type(self, workspace: Path) -> None:
        _write(workspace, "potential nep.txt\n")
        ok, msg = check_gpumd_run_in(workspace, filename="run.in", check="nonexistent_check")
        assert not ok
        assert "unknown" in msg
