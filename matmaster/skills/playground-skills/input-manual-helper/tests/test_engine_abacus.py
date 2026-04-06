"""
Unit tests for the ABACUS engine backend (abacus.py) and validator.

Tests cover:
  - parse: INPUT_PARAMETERS key-value parsing
  - render: task-type customization (scf, relax, cell-relax, band, dos, md)
  - render_all: multi-file output (INPUT + STRU + KPT keys)
  - get_diagnostics: physical / consistency rules
  - get_completions: prefix filtering
  - ABACUSValidator: regex-based validation rules

Run from project root:
  uv run pytest matmaster/skills/playground-skills/input-manual-helper/tests/test_engine_abacus.py -v
Or from skill dir:
  uv run pytest tests/test_engine_abacus.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from engine.renderer import RenderIntent  # noqa: E402
from engine.schema import SchemaRegistry  # noqa: E402
from engine.software.abacus import AbacusBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backend() -> AbacusBackend:
    return AbacusBackend()


def _schema() -> SchemaRegistry:
    s = SchemaRegistry()
    s.load_software("abacus")
    return s


def _render(task: str = "scf", **params) -> str:
    b = _backend()
    intent = RenderIntent(
        software="abacus", task_type=task, structure_file=None, params=params
    )
    return b.render(intent)


def _get_diags(text: str):
    b = _backend()
    s = _schema()
    doc = b.parse(text, "<test>")
    return b.get_diagnostics(doc, s)


def _errors(diags):
    return [d for d in diags if d.severity == "error"]


def _warnings(diags):
    return [d for d in diags if d.severity == "warning"]


def _has_param(text: str, key: str) -> bool:
    """Check if a keyword appears in the rendered INPUT text."""
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(key.lower()):
            return True
    return False


def _get_value(text: str, key: str) -> str | None:
    """Extract the value for a keyword from rendered text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(key.lower()):
            parts = stripped.split(None, 1)
            if len(parts) >= 2:
                return parts[1].strip()
    return None


# ---------------------------------------------------------------------------
# Tests: parse
# ---------------------------------------------------------------------------


class TestParse:
    def test_parse_empty_returns_empty_params(self):
        b = _backend()
        doc = b.parse("INPUT_PARAMETERS\n")
        all_params = [p for s in doc.sections for p in s.params]
        assert all_params == []

    def test_parse_basic_keywords(self):
        text = """INPUT_PARAMETERS
ntype           2
calculation     scf
ecutwfc         50
basis_type      lcao
"""
        b = _backend()
        doc = b.parse(text)
        all_params = {p.name: p.value for s in doc.sections for p in s.params}
        assert all_params["ntype"] == 2
        assert all_params["calculation"] == "scf"
        assert all_params["ecutwfc"] == 50
        assert all_params["basis_type"] == "lcao"

    def test_parse_ignores_comments(self):
        text = """INPUT_PARAMETERS
# This is a comment
ecutwfc  50  # inline comment
ntype 1
"""
        b = _backend()
        doc = b.parse(text)
        all_params = {p.name: p.value for s in doc.sections for p in s.params}
        assert all_params["ecutwfc"] == 50
        assert all_params["ntype"] == 1
        assert len(all_params) == 2

    def test_parse_scientific_notation_float(self):
        text = "INPUT_PARAMETERS\nscf_thr  1e-7\n"
        b = _backend()
        doc = b.parse(text)
        all_params = {p.name: p.value for s in doc.sections for p in s.params}
        assert float(all_params["scf_thr"]) == pytest.approx(1e-7)

    def test_parse_line_numbers(self):
        text = "INPUT_PARAMETERS\n\nntype  1\necuwfc 50\n"
        b = _backend()
        doc = b.parse(text, "<test>")
        all_params = {
            p.name: p.range.start_line for s in doc.sections for p in s.params
        }
        assert all_params["ntype"] == 3
        assert all_params["ecuwfc"] == 4

    def test_parse_without_header_tolerant(self):
        """Parse should succeed even if INPUT_PARAMETERS header is missing."""
        text = "ntype  1\necuwfc  50\n"
        b = _backend()
        doc = b.parse(text)
        all_params = {p.name: p.value for s in doc.sections for p in s.params}
        assert all_params["ntype"] == 1

    def test_parse_case_insensitive_keys(self):
        text = "INPUT_PARAMETERS\nNTYPE  2\nECUTWFC  60\n"
        b = _backend()
        doc = b.parse(text)
        all_params = {p.name: p.value for s in doc.sections for p in s.params}
        # Keys stored as-parsed lowercase
        assert "ntype" in all_params
        assert "ecutwfc" in all_params


# ---------------------------------------------------------------------------
# Tests: render (INPUT file only)
# ---------------------------------------------------------------------------


class TestRender:
    def test_render_scf_has_input_parameters(self):
        text = _render("scf")
        assert "INPUT_PARAMETERS" in text

    def test_render_scf_default_values(self):
        text = _render("scf")
        assert _has_param(text, "calculation")
        assert _get_value(text, "calculation") == "scf"
        assert _has_param(text, "ecutwfc")
        assert _has_param(text, "basis_type")
        assert _has_param(text, "scf_thr")

    def test_render_relax_sets_cal_force(self):
        text = _render("relax")
        assert _get_value(text, "calculation") == "relax"
        assert _get_value(text, "cal_force") == "1"

    def test_render_cell_relax_sets_cal_stress(self):
        text = _render("cell-relax")
        assert _get_value(text, "calculation") == "cell-relax"
        assert _get_value(text, "cal_force") == "1"
        assert _get_value(text, "cal_stress") == "1"

    def test_render_band_sets_out_band(self):
        text = _render("band")
        assert _get_value(text, "calculation") == "nscf"
        assert _get_value(text, "out_band") == "1"
        assert _has_param(text, "nbands")

    def test_render_dos_sets_out_dos(self):
        text = _render("dos")
        assert _get_value(text, "calculation") == "nscf"
        assert _get_value(text, "out_dos") == "1"

    def test_render_md_sets_md_params(self):
        text = _render("md")
        assert _get_value(text, "calculation") == "md"
        assert _has_param(text, "md_type")
        assert _has_param(text, "md_nstep")
        assert _has_param(text, "md_dt")
        assert _has_param(text, "md_tfirst")

    def test_render_param_override(self):
        text = _render("scf", ecutwfc=80, nspin=2)
        assert _get_value(text, "ecutwfc") == "80"
        assert _get_value(text, "nspin") == "2"

    def test_render_result_is_parseable(self):
        """Rendered output should parse without errors."""
        for task in ("scf", "relax", "cell-relax", "band", "dos", "md"):
            text = _render(task)
            b = _backend()
            doc = b.parse(text)
            # Should produce at least some params
            all_params = [p for s in doc.sections for p in s.params]
            assert len(all_params) > 0, f"render({task}) produced no parseable params"


# ---------------------------------------------------------------------------
# Tests: render_all (multi-file dict)
# ---------------------------------------------------------------------------


class TestRenderAll:
    def test_render_all_keys(self):
        b = _backend()
        intent = RenderIntent(
            software="abacus", task_type="scf", structure_file=None, params={}
        )
        files = b.render_all(intent)
        assert "INPUT" in files
        assert "STRU" in files
        assert "KPT" in files

    def test_render_all_input_has_header(self):
        b = _backend()
        intent = RenderIntent(
            software="abacus", task_type="scf", structure_file=None, params={}
        )
        files = b.render_all(intent)
        assert "INPUT_PARAMETERS" in files["INPUT"]

    def test_render_all_stru_has_atomic_species(self):
        b = _backend()
        intent = RenderIntent(
            software="abacus", task_type="scf", structure_file=None, params={}
        )
        files = b.render_all(intent)
        assert "ATOMIC_SPECIES" in files["STRU"]

    def test_render_all_kpt_has_k_points(self):
        b = _backend()
        intent = RenderIntent(
            software="abacus", task_type="scf", structure_file=None, params={}
        )
        files = b.render_all(intent)
        assert "K_POINTS" in files["KPT"]

    def test_render_all_band_uses_line_kpath(self):
        b = _backend()
        intent = RenderIntent(
            software="abacus", task_type="band", structure_file=None, params={}
        )
        files = b.render_all(intent)
        assert "Line" in files["KPT"]

    def test_render_all_scf_uses_gamma_kpt(self):
        b = _backend()
        intent = RenderIntent(
            software="abacus", task_type="scf", structure_file=None, params={}
        )
        files = b.render_all(intent)
        assert "Gamma" in files["KPT"]


# ---------------------------------------------------------------------------
# Tests: get_diagnostics
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_clean_input_no_errors(self):
        text = """INPUT_PARAMETERS
ntype           1
calculation     scf
ecutwfc         50
basis_type      lcao
orbital_dir     ./
scf_thr         1e-7
scf_nmax        100
smearing_method gauss
smearing_sigma  0.015
"""
        diags = _get_diags(text)
        errors = _errors(diags)
        assert errors == [], f"Unexpected errors: {[e.message for e in errors]}"

    def test_ecutwfc_too_low_error(self):
        text = "INPUT_PARAMETERS\necutwfc  5\n"
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "ecutwfc" in d.param for d in errors
        ), "Expected ecutwfc error for value 5 Ry"

    def test_ecutwfc_low_warning(self):
        text = "INPUT_PARAMETERS\necutwfc  20\n"
        diags = _get_diags(text)
        warns = _warnings(diags)
        assert any(
            "ecutwfc" in d.param for d in warns
        ), "Expected ecutwfc warning for value 20 Ry"

    def test_unknown_parameter_warning(self):
        text = "INPUT_PARAMETERS\nfakeparam  999\n"
        diags = _get_diags(text)
        warns = _warnings(diags)
        assert any(
            "fakeparam" in d.param.lower() or "fakeparam" in d.message.lower()
            for d in warns
        ), "Expected warning for unknown param 'fakeparam'"

    def test_relax_without_cal_force_error(self):
        text = "INPUT_PARAMETERS\ncalculation  relax\ncal_force  0\n"
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "cal_force" in d.param for d in errors
        ), "Expected error: relax requires cal_force=1"

    def test_cell_relax_without_cal_stress_error(self):
        text = (
            "INPUT_PARAMETERS\ncalculation  cell-relax\ncal_force  1\ncal_stress  0\n"
        )
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "cal_stress" in d.param for d in errors
        ), "Expected error: cell-relax requires cal_stress=1"

    def test_noncolin_without_nspin4_error(self):
        text = "INPUT_PARAMETERS\nnoncolin  1\nnspin  2\n"
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "noncolin" in d.param for d in errors
        ), "Expected error: noncolin=1 requires nspin=4"

    def test_noncolin_with_nspin4_ok(self):
        text = "INPUT_PARAMETERS\nnoncolin  1\nnspin  4\n"
        diags = _get_diags(text)
        # No noncolin error expected
        noncolin_errors = [d for d in _errors(diags) if "noncolin" in d.param]
        assert noncolin_errors == []

    def test_lda_plus_u_missing_hubbard_u_error(self):
        text = "INPUT_PARAMETERS\nlda_plus_u  1\norbital_corr  2 -1\n"
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "hubbard_u" in d.param for d in errors
        ), "Expected error: lda_plus_u=1 requires hubbard_u"

    def test_lda_plus_u_missing_orbital_corr_error(self):
        text = "INPUT_PARAMETERS\nlda_plus_u  1\nhubbard_u  4.0 0.0\n"
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "orbital_corr" in d.param for d in errors
        ), "Expected error: lda_plus_u=1 requires orbital_corr"

    def test_invalid_mixing_beta_error(self):
        text = "INPUT_PARAMETERS\nmixing_beta  1.5\n"
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "mixing_beta" in d.param for d in errors
        ), "Expected error: mixing_beta=1.5 out of range"

    def test_md_without_cal_force_error(self):
        text = "INPUT_PARAMETERS\ncalculation  md\ncal_force  0\n"
        diags = _get_diags(text)
        errors = _errors(diags)
        assert any(
            "cal_force" in d.param for d in errors
        ), "Expected error: md requires cal_force=1"

    def test_nscf_band_missing_nbands_warning(self):
        text = "INPUT_PARAMETERS\ncalculation  nscf\nout_band  1\n"
        diags = _get_diags(text)
        warns = _warnings(diags)
        assert any(
            "nbands" in d.param for d in warns
        ), "Expected warning: nscf + out_band should set nbands"

    def test_render_then_diagnose_no_errors(self):
        """Rendered SCF input should pass diagnostics without errors."""
        text = _render("scf")
        diags = _get_diags(text)
        errors = _errors(diags)
        assert errors == [], f"Render+diagnose errors: {[e.message for e in errors]}"

    def test_render_relax_then_diagnose_no_errors(self):
        """Rendered relax input should pass diagnostics without errors."""
        text = _render("relax")
        diags = _get_diags(text)
        errors = _errors(diags)
        assert (
            errors == []
        ), f"Render+diagnose relax errors: {[e.message for e in errors]}"


# ---------------------------------------------------------------------------
# Tests: get_completions
# ---------------------------------------------------------------------------


class TestCompletions:
    def test_completions_returns_list(self):
        b = _backend()
        s = _schema()
        doc = b.parse("INPUT_PARAMETERS\n")
        completions = b.get_completions(doc, line=2, col=0, schema=s)
        assert isinstance(completions, list)

    def test_completions_schema_must_load(self):
        """SchemaRegistry must load abacus without error."""
        s = SchemaRegistry()
        s.load_software("abacus")
        tags = s.list_tags("abacus")
        assert len(tags) > 50, f"Expected >50 ABACUS params, got {len(tags)}"


# ---------------------------------------------------------------------------
# Tests: ABACUSValidator (regex-based)
# ---------------------------------------------------------------------------


class TestABACUSValidator:
    def _validator(self):
        from validators.abacus_validator import ABACUSValidator

        return ABACUSValidator()

    def test_missing_header_warns(self):
        v = self._validator()
        diags = v.validate_text("ntype  1\necutwfc  50\n")
        assert any(
            "INPUT_PARAMETERS" in d.message for d in diags
        ), "Expected warning about missing INPUT_PARAMETERS header"

    def test_clean_input_no_errors(self):
        v = self._validator()
        text = """INPUT_PARAMETERS
ntype  1
calculation  scf
ecutwfc  50
basis_type  lcao
orbital_dir  ./orbitals/
"""
        diags = v.validate_text(text)
        errors = [d for d in diags if d.severity == "error"]
        assert errors == []

    def test_ecutwfc_low_warning(self):
        v = self._validator()
        text = "INPUT_PARAMETERS\necutwfc  20\n"
        diags = v.validate_text(text)
        warns = [
            d for d in diags if d.severity == "warning" and "ecutwfc" in (d.param or "")
        ]
        assert warns, "Expected ecutwfc warning"

    def test_lcao_missing_orbital_dir_warning(self):
        v = self._validator()
        text = "INPUT_PARAMETERS\nbasis_type  lcao\n"
        diags = v.validate_text(text)
        warns = [
            d
            for d in diags
            if d.severity == "warning" and "orbital_dir" in (d.param or "")
        ]
        assert warns, "Expected orbital_dir warning for basis_type=lcao"

    def test_noncolin_missing_nspin4_error(self):
        v = self._validator()
        text = "INPUT_PARAMETERS\nnoncolin  1\nnspin  2\n"
        diags = v.validate_text(text)
        errors = [
            d for d in diags if d.severity == "error" and "noncolin" in (d.param or "")
        ]
        assert errors, "Expected noncolin error"

    def test_md_large_dt_warning(self):
        v = self._validator()
        text = "INPUT_PARAMETERS\ncalculation  md\ncal_force  1\nmd_dt  5.0\n"
        diags = v.validate_text(text)
        warns = [
            d for d in diags if d.severity == "warning" and "md_dt" in (d.param or "")
        ]
        assert warns, "Expected md_dt warning for value > 3 fs"

    def test_valid_md_no_dt_warning(self):
        v = self._validator()
        text = "INPUT_PARAMETERS\ncalculation  md\ncal_force  1\nmd_dt  1.0\n"
        diags = v.validate_text(text)
        dt_warns = [
            d for d in diags if d.severity == "warning" and "md_dt" in (d.param or "")
        ]
        assert dt_warns == [], "No md_dt warning expected for dt=1.0 fs"
