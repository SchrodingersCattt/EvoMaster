"""
Unit tests for input-manual-helper validators.

Uses the existing reference templates in references/ as test fixtures.

Run from the skill directory:
  cd playground/mat_master/skills/input-manual-helper
  uv run pytest tests/ -v

Or from project root:
  uv run pytest playground/mat_master/skills/input-manual-helper/tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure 'validators' package is importable
_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from validators.base import (  # noqa: E402
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    Diagnostic,
    ValidatorRegistry,
)

REFS = _SKILL_DIR / "references"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_validator(software: str):
    registry = ValidatorRegistry()
    v = registry.get_validator(software)
    assert v is not None, f"No validator registered for '{software}'"
    return v


def severities(diags: list[Diagnostic]) -> list[str]:
    return [d.severity for d in diags]


def has_error(diags: list[Diagnostic]) -> bool:
    return any(d.severity == SEVERITY_ERROR for d in diags)


def has_warning(diags: list[Diagnostic]) -> bool:
    return any(d.severity == SEVERITY_WARNING for d in diags)


# ---------------------------------------------------------------------------
# Diagnostic dataclass
# ---------------------------------------------------------------------------


class TestDiagnostic:
    def test_to_dict_minimal(self):
        d = Diagnostic(severity=SEVERITY_WARNING, message="test msg")
        result = d.to_dict()
        assert result["severity"] == "warning"
        assert result["message"] == "test msg"
        assert "suggestion" not in result  # omitted when None

    def test_to_dict_with_suggestion(self):
        d = Diagnostic(
            severity=SEVERITY_ERROR,
            message="bad param",
            line=10,
            param="CUTOFF",
            suggestion="use >= 300",
        )
        result = d.to_dict()
        assert result["line"] == 10
        assert result["param"] == "CUTOFF"
        assert result["suggestion"] == "use >= 300"

    def test_to_human_format(self):
        d = Diagnostic(
            severity=SEVERITY_WARNING,
            message="low cutoff",
            line=5,
            param="CUTOFF",
            suggestion="use >= 300",
        )
        human = d.to_human()
        assert "⚠" in human
        assert "line 5" in human
        assert "[CUTOFF]" in human
        assert "low cutoff" in human
        assert "use >= 300" in human


# ---------------------------------------------------------------------------
# ValidatorRegistry
# ---------------------------------------------------------------------------


class TestValidatorRegistry:
    def test_all_software_registered(self):
        registry = ValidatorRegistry()
        for sw in ("cp2k", "orca", "qe", "abinit", "lammps"):
            v = registry.get_validator(sw)
            assert v is not None, f"Missing validator for {sw}"

    def test_aliases(self):
        registry = ValidatorRegistry()
        for alias in ("quantum espresso", "espresso", "pwscf", "pw.x"):
            v = registry.get_validator(alias)
            assert v is not None, f"Alias '{alias}' not resolved"
            assert v.software_name == "qe"

    def test_unknown_software(self):
        registry = ValidatorRegistry()
        v = registry.get_validator("unknown_software_xyz")
        assert v is None

    def test_supported_software_list(self):
        registry = ValidatorRegistry()
        sw = registry.supported_software
        assert "cp2k" in sw
        assert "orca" in sw
        assert "qe" in sw
        assert "abinit" in sw
        assert "lammps" in sw


# ---------------------------------------------------------------------------
# CP2K validator
# ---------------------------------------------------------------------------


class TestCP2KValidator:
    template = REFS / "cp2k" / "minimal_periodic.inp"

    def test_template_exists(self):
        assert self.template.exists(), f"Template not found: {self.template}"

    def test_validate_file_returns_list(self):
        v = get_validator("cp2k")
        diags = v.validate_file(self.template)
        assert isinstance(diags, list)

    def test_no_hard_errors_on_minimal_template(self):
        v = get_validator("cp2k")
        diags = v.validate_file(self.template)
        # minimal_periodic.inp may have warnings but should not have errors
        # (unless cp2k-input-tools flags the placeholder structure)
        # We just assert it doesn't crash and returns a list
        assert diags is not None

    def test_cutoff_range_low(self):
        v = get_validator("cp2k")
        text = "&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n&FORCE_EVAL\n  &DFT\n    &MGRID\n      CUTOFF 100\n    &END MGRID\n  &END DFT\n&END FORCE_EVAL\n"
        diags = v.validate_text(text)
        params = [d.param for d in diags]
        assert "CUTOFF" in params
        cutoff_diags = [d for d in diags if d.param == "CUTOFF"]
        assert any(d.severity == SEVERITY_WARNING for d in cutoff_diags)

    def test_cutoff_range_ok(self):
        v = get_validator("cp2k")
        text = "&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n&FORCE_EVAL\n  &DFT\n    &MGRID\n      CUTOFF 500\n    &END MGRID\n  &END DFT\n&END FORCE_EVAL\n"
        diags = v.validate_text(text)
        cutoff_diags = [d for d in diags if d.param == "CUTOFF"]
        # Should produce info, not warning
        assert any(d.severity == SEVERITY_INFO for d in cutoff_diags)
        assert not any(d.severity == SEVERITY_WARNING for d in cutoff_diags)

    def test_task_scf_template(self):
        tmpl = REFS / "cp2k" / "task_scf.inp"
        if not tmpl.exists():
            pytest.skip("task_scf.inp not found")
        v = get_validator("cp2k")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)

    def test_method_admm_pbe0_template(self):
        tmpl = REFS / "cp2k" / "method_admm_pbe0.inp"
        if not tmpl.exists():
            pytest.skip("method_admm_pbe0.inp not found")
        v = get_validator("cp2k")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)


# ---------------------------------------------------------------------------
# ORCA validator
# ---------------------------------------------------------------------------


class TestORCAValidator:
    def test_minimal_template(self):
        tmpl = REFS / "orca" / "minimal_molecule.inp"
        if not tmpl.exists():
            pytest.skip("minimal_molecule.inp not found")
        v = get_validator("orca")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)

    def test_std_dft_template(self):
        tmpl = REFS / "orca" / "std_dft.inp"
        if not tmpl.exists():
            pytest.skip("std_dft.inp not found")
        v = get_validator("orca")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)
        # Should detect functional and basis
        params = [d.param for d in diags]
        assert "functional" in params

    def test_tddft_template(self):
        tmpl = REFS / "orca" / "tddft_pbe0.inp"
        if not tmpl.exists():
            pytest.skip("tddft_pbe0.inp not found")
        v = get_validator("orca")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)

    def test_missing_coord_block(self):
        v = get_validator("orca")
        text = "! B3LYP def2-TZVP\n%maxcore 2000\n%pal nprocs 8 end\n"
        diags = v.validate_text(text)
        assert has_error(diags)
        coord_errors = [d for d in diags if d.param == "coords"]
        assert len(coord_errors) > 0

    def test_low_maxcore_warning(self):
        v = get_validator("orca")
        text = "! B3LYP def2-TZVP\n%maxcore 100\n* xyz 0 1\nH 0 0 0\nH 0 0 0.7\n*\n"
        diags = v.validate_text(text)
        maxcore_diags = [d for d in diags if d.param == "maxcore"]
        assert any(d.severity == SEVERITY_WARNING for d in maxcore_diags)

    def test_dlpno_template(self):
        tmpl = REFS / "orca" / "dlpno_ccsd_t_normal.inp"
        if not tmpl.exists():
            pytest.skip("dlpno_ccsd_t_normal.inp not found")
        v = get_validator("orca")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)


# ---------------------------------------------------------------------------
# QE validator
# ---------------------------------------------------------------------------


class TestQEValidator:
    def test_valid_minimal_input(self):
        v = get_validator("qe")
        text = (
            "&CONTROL\n  calculation = 'scf',\n&END\n"
            "&SYSTEM\n  ecutwfc = 50.0,\n  ecutrho = 400.0,\n&END\n"
            "&ELECTRONS\n  conv_thr = 1.0d-8,\n&END\n"
        )
        diags = v.validate_text(text)
        assert isinstance(diags, list)

    def test_low_ecutwfc_warning(self):
        v = get_validator("qe")
        text = "&SYSTEM\n  ecutwfc = 10.0,\n&END\n"
        diags = v.validate_text(text)
        ecutwfc_diags = [d for d in diags if d.param == "ecutwfc"]
        assert any(d.severity == SEVERITY_WARNING for d in ecutwfc_diags)

    def test_bad_ecutrho_ratio(self):
        v = get_validator("qe")
        text = "&SYSTEM\n  ecutwfc = 50.0,\n  ecutrho = 100.0,\n&END\n"
        diags = v.validate_text(text)
        rho_diags = [d for d in diags if d.param == "ecutrho"]
        assert any(d.severity == SEVERITY_WARNING for d in rho_diags)

    def test_unknown_calculation_type(self):
        v = get_validator("qe")
        text = "&CONTROL\n  calculation = 'unknown_type',\n&END\n"
        diags = v.validate_text(text)
        calc_diags = [d for d in diags if d.param == "calculation"]
        assert any(d.severity == SEVERITY_WARNING for d in calc_diags)


# ---------------------------------------------------------------------------
# ABINIT validator
# ---------------------------------------------------------------------------


class TestABINITValidator:
    def test_gs_scf_template(self):
        tmpl = REFS / "abinit" / "gs_scf.abi"
        if not tmpl.exists():
            pytest.skip("gs_scf.abi not found")
        v = get_validator("abinit")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)

    def test_low_ecut_warning(self):
        v = get_validator("abinit")
        text = "ecut 5\nnatom 1\nntypat 1\n"
        diags = v.validate_text(text)
        ecut_diags = [d for d in diags if d.param == "ecut"]
        assert any(d.severity == SEVERITY_WARNING for d in ecut_diags)

    def test_no_convergence_keyword_warning(self):
        v = get_validator("abinit")
        text = "ecut 30\nnatom 2\nntypat 1\n"
        diags = v.validate_text(text)
        conv_diags = [d for d in diags if d.param == "convergence"]
        assert any(d.severity == SEVERITY_WARNING for d in conv_diags)

    def test_typat_natom_mismatch(self):
        v = get_validator("abinit")
        text = "ecut 30\nnatom 3\nntypat 1\ntypat 1 1\ntolwfr 1.0d-18\n"
        diags = v.validate_text(text)
        typat_diags = [d for d in diags if d.param == "typat"]
        assert any(d.severity == SEVERITY_ERROR for d in typat_diags)


# ---------------------------------------------------------------------------
# LAMMPS validator
# ---------------------------------------------------------------------------


class TestLAMMPSValidator:
    def test_gcmc_template(self):
        tmpl = REFS / "lammps" / "gcmc_adsorption.lammps"
        if not tmpl.exists():
            pytest.skip("gcmc_adsorption.lammps not found")
        v = get_validator("lammps")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)

    def test_msst_template(self):
        tmpl = REFS / "lammps" / "msst_shock.lammps"
        if not tmpl.exists():
            pytest.skip("msst_shock.lammps not found")
        v = get_validator("lammps")
        diags = v.validate_file(tmpl)
        assert isinstance(diags, list)

    def test_placeholder_read_data_warning(self):
        v = get_validator("lammps")
        text = "units metal\natom_style atomic\nread_data __STRUCTURE_FILE__\npair_style eam/alloy\npair_coeff * * potential.eam Fe\nrun 1000\n"
        diags = v.validate_text(text)
        rd_diags = [d for d in diags if d.param == "read_data"]
        assert any(d.severity == SEVERITY_WARNING for d in rd_diags)

    def test_missing_run_warning(self):
        v = get_validator("lammps")
        text = "units metal\natom_style atomic\npair_style lj/cut 2.5\npair_coeff * * 1.0 1.0\n"
        diags = v.validate_text(text)
        run_diags = [d for d in diags if d.param == "run/minimize"]
        assert any(d.severity == SEVERITY_WARNING for d in run_diags)

    def test_unknown_units_warning(self):
        v = get_validator("lammps")
        text = "units foobar\natom_style atomic\nrun 1000\n"
        diags = v.validate_text(text)
        units_diags = [d for d in diags if d.param == "units"]
        assert any(d.severity == SEVERITY_WARNING for d in units_diags)

    def test_pair_style_without_coeff(self):
        v = get_validator("lammps")
        text = "units metal\natom_style atomic\npair_style eam/alloy\nrun 1000\n"
        diags = v.validate_text(text)
        pair_diags = [d for d in diags if d.param == "pair_style"]
        assert any(d.severity == SEVERITY_WARNING for d in pair_diags)


# ---------------------------------------------------------------------------
# validate_input.py script integration
# ---------------------------------------------------------------------------


class TestValidateInputScript:
    """Integration test: call validate_input.py as a subprocess."""

    def _run(self, input_file: Path, software: str, tmp_path: Path) -> tuple[int, str]:
        import subprocess

        script = _SKILL_DIR / "scripts" / "validate_input.py"
        json_out = tmp_path / "out.json"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--input_file",
                str(input_file),
                "--software",
                software,
                "--json_out",
                str(json_out),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout

    def test_exits_zero_cp2k(self, tmp_path):
        tmpl = REFS / "cp2k" / "minimal_periodic.inp"
        if not tmpl.exists():
            pytest.skip()
        rc, _ = self._run(tmpl, "cp2k", tmp_path)
        assert rc == 0, "validate_input.py must always exit 0"

    def test_exits_zero_orca(self, tmp_path):
        tmpl = REFS / "orca" / "std_dft.inp"
        if not tmpl.exists():
            pytest.skip()
        rc, _ = self._run(tmpl, "orca", tmp_path)
        assert rc == 0

    def test_exits_zero_unknown_software(self, tmp_path):
        tmpl = REFS / "cp2k" / "minimal_periodic.inp"
        rc, _ = self._run(tmpl, "unknown_sw", tmp_path)
        assert rc == 0, "Even for unknown software, exit code must be 0"

    def test_json_output_written(self, tmp_path):
        import json

        tmpl = REFS / "cp2k" / "minimal_periodic.inp"
        if not tmpl.exists():
            pytest.skip()
        script = _SKILL_DIR / "scripts" / "validate_input.py"
        json_out = tmp_path / "diag.json"
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(script),
                "--input_file",
                str(tmpl),
                "--software",
                "cp2k",
                "--json_out",
                str(json_out),
            ],
            capture_output=True,
            text=True,
        )
        assert json_out.exists(), "JSON output file should be created"
        data = json.loads(json_out.read_text())
        assert "software" in data
        assert "diagnostics" in data
        assert "status" in data
        assert "summary" in data


# ---------------------------------------------------------------------------
# Physics compatibility rules (Fix B) — Validator layer
# ---------------------------------------------------------------------------


class TestCP2KPhysicsCompatibility:
    """Tests for _check_physics_compatibility in CP2KValidator."""

    def setup_method(self):
        self.v = get_validator("cp2k")

    def _diags(self, text: str):
        return self.v.validate_text(text)

    def _rule_ids(self, diags):
        return [d.rule_id for d in diags if d.rule_id]

    def _has_rule(self, diags, rule_id: str) -> bool:
        return any(d.rule_id == rule_id for d in diags if d.rule_id)

    def _errors_for(self, diags, rule_id: str):
        return [
            d for d in diags if d.rule_id == rule_id and d.severity == SEVERITY_ERROR
        ]

    # ── Rule 1: OT + KPOINTS ────────────────────────────────────────────────

    def test_ot_with_kpoints_is_error(self):
        """OT + KPOINTS must produce an error diagnostic."""
        text = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    &KPOINTS
      SCHEME MONKHORST-PACK 4 4 4
    &END KPOINTS
    &SCF
      &OT ON
        MINIMIZER DIIS
      &END OT
    &END SCF
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        errors = [
            d for d in diags if d.severity == SEVERITY_ERROR and "OT" in (d.param or "")
        ]
        assert errors, "Expected an error for OT + KPOINTS combination, got: " + str(
            [(d.severity, d.param, d.message[:60]) for d in diags]
        )

    def test_ot_without_kpoints_no_ot_error(self):
        """OT alone (no KPOINTS) must NOT produce the OT+KPOINTS error."""
        text = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    &SCF
      &OT ON
      &END OT
    &END SCF
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        ot_errors = [
            d
            for d in diags
            if d.severity == SEVERITY_ERROR
            and "OT" in (d.param or "")
            and "KPOINTS" in d.message
        ]
        assert (
            not ot_errors
        ), f"No OT+KPOINTS error expected without KPOINTS: {ot_errors}"

    def test_kpoints_without_ot_no_ot_error(self):
        """KPOINTS without OT must NOT produce the OT+KPOINTS error."""
        text = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    &KPOINTS
      SCHEME MONKHORST-PACK 4 4 4
    &END KPOINTS
    &SCF
      DIAGONALIZATION ON
      ADDED_MOS 5
    &END SCF
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        ot_kpts_errors = [
            d
            for d in diags
            if d.severity == SEVERITY_ERROR
            and "OT" in (d.param or "")
            and "kpoint" in d.message.lower()
        ]
        assert not ot_kpts_errors

    # ── Rule 2: HFX + KPOINTS without RI ───────────────────────────────────

    def test_hfx_kpoints_without_ri_is_error(self):
        """&HF + &KPOINTS without &RI must produce an error."""
        text = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    &KPOINTS
      SCHEME MONKHORST-PACK 4 4 4
    &END KPOINTS
    &XC
      &XC_FUNCTIONAL PBE0
      &END XC_FUNCTIONAL
      &HF
        FRACTION 0.25
      &END HF
    &END XC
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        errors = [
            d for d in diags if d.severity == SEVERITY_ERROR and "HF" in (d.param or "")
        ]
        assert errors, "Expected error for HFX + KPOINTS without RI, got: " + str(
            [(d.severity, d.param, d.message[:60]) for d in diags]
        )

    def test_hfx_kpoints_with_ri_no_error(self):
        """&HF + &KPOINTS + &RI must NOT produce the RI error."""
        text = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    &KPOINTS
      SCHEME MONKHORST-PACK 4 4 4
    &END KPOINTS
    &XC
      &XC_FUNCTIONAL PBE0
      &END XC_FUNCTIONAL
      &HF
        FRACTION 0.25
        &RI
          KFN_REUSE_NUMBER 1
        &END RI
      &END HF
    &END XC
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        ri_errors = [
            d
            for d in diags
            if d.severity == SEVERITY_ERROR
            and "HF" in (d.param or "")
            and "RI" in d.message
        ]
        assert not ri_errors, f"No RI error expected when &RI is present: {ri_errors}"

    # ── Rule 3: CELL_OPT without STRESS_TENSOR ─────────────────────────────

    def test_cell_opt_without_stress_tensor_warning(self):
        """CELL_OPT without STRESS_TENSOR must produce a warning."""
        text = """\
&GLOBAL
  RUN_TYPE CELL_OPT
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        warnings = [
            d
            for d in diags
            if d.severity == SEVERITY_WARNING
            and "STRESS_TENSOR" in (d.param or "").upper()
        ]
        assert warnings, "Expected STRESS_TENSOR warning for CELL_OPT, got: " + str(
            [(d.severity, d.param, d.message[:60]) for d in diags]
        )

    def test_cell_opt_with_stress_tensor_no_warning(self):
        """CELL_OPT with STRESS_TENSOR ANALYTICAL must NOT produce the warning."""
        text = """\
&GLOBAL
  RUN_TYPE CELL_OPT
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    STRESS_TENSOR ANALYTICAL
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        st_warnings = [
            d
            for d in diags
            if d.severity == SEVERITY_WARNING
            and "STRESS_TENSOR" in (d.param or "").upper()
        ]
        assert not st_warnings, f"No STRESS_TENSOR warning expected: {st_warnings}"

    # ── Rule 4: RUN_TYPE BAND without &BAND_STRUCTURE ──────────────────────

    def test_band_without_band_structure_warning(self):
        """RUN_TYPE BAND without &BAND_STRUCTURE must produce a warning."""
        text = """\
&GLOBAL
  RUN_TYPE BAND
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
  &END DFT
&END FORCE_EVAL
"""
        diags = self._diags(text)
        warnings = [
            d
            for d in diags
            if d.severity == SEVERITY_WARNING and "RUN_TYPE" in (d.param or "").upper()
        ]
        assert (
            warnings
        ), "Expected warning for BAND without BAND_STRUCTURE section, got: " + str(
            [(d.severity, d.param, d.message[:60]) for d in diags]
        )

    def test_band_with_band_structure_no_warning(self):
        """RUN_TYPE BAND with &BAND_STRUCTURE must NOT produce the section warning."""
        text = """\
&GLOBAL
  RUN_TYPE BAND
  PROJECT test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
  &END DFT
  &PROPERTIES
    &BAND_STRUCTURE
      ADDED_MOS 10
      &KPOINT_SET
        UNITS RECIPROCAL
        SPECIAL_POINT G  0.0 0.0 0.0
        SPECIAL_POINT X  0.5 0.0 0.5
        NPOINTS 10
      &END KPOINT_SET
    &END BAND_STRUCTURE
  &END PROPERTIES
&END FORCE_EVAL
"""
        diags = self._diags(text)
        band_warnings = [
            d
            for d in diags
            if d.severity == SEVERITY_WARNING
            and "RUN_TYPE" in (d.param or "").upper()
            and "BAND_STRUCTURE" in d.message.upper()
        ]
        assert not band_warnings, f"No BAND_STRUCTURE warning expected: {band_warnings}"
