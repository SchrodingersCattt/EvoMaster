"""
Unit tests for the CP2K engine backend (cp2k.py).

Tests cover:
  - Fix A: render_input.py --structure argument support (via RenderIntent)
  - Fix B: _check_physics_compatibility() in CP2KBackend.get_diagnostics()

Run from project root:
  uv run pytest playground/mat_master/skills/input-manual-helper/tests/test_engine_cp2k.py -v
Or from skill dir:
  uv run pytest tests/test_engine_cp2k.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from engine.schema import SchemaRegistry
from engine.software.cp2k import CP2KBackend
from engine.renderer import RenderIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _backend() -> CP2KBackend:
    return CP2KBackend()


def _schema() -> SchemaRegistry:
    s = SchemaRegistry()
    s.load_software("cp2k")
    return s


def _get_diags(text: str):
    """Parse text and return diagnostics list."""
    b = _backend()
    s = _schema()
    doc = b.parse(text)
    return b.get_diagnostics(doc, s)


def _rule_ids(diags) -> list[str]:
    return [d.rule_id for d in diags if d.rule_id]


def _has_rule(diags, rule_id: str) -> bool:
    return rule_id in _rule_ids(diags)


def _errors(diags):
    return [d for d in diags if d.severity == "error"]


def _warnings(diags):
    return [d for d in diags if d.severity == "warning"]


# ---------------------------------------------------------------------------
# Fix A: RenderIntent.structure_file propagation
# ---------------------------------------------------------------------------


class TestRenderIntentStructureFile:
    """Verify that render_input.py's --structure parameter reaches RenderIntent."""

    def test_render_without_structure_uses_si_placeholder(self):
        """Without structure_file, render() falls back to built-in Si structure."""
        b = _backend()
        intent = RenderIntent(
            software="cp2k",
            task_type="scf",
            structure_file=None,
            params={},
        )
        text = b.render(intent)
        # Built-in Si structure contains silicon coord entries
        assert "Si" in text, "Expected Si atoms in fallback structure"

    def test_render_with_nonexistent_structure_falls_back_to_si(self):
        """If structure_file path doesn't exist, render() gracefully falls back."""
        b = _backend()
        intent = RenderIntent(
            software="cp2k",
            task_type="scf",
            structure_file="/nonexistent/path/SiC.cif",
            params={},
        )
        # Should not raise — graceful degradation
        text = b.render(intent)
        assert len(text) > 0, "render() must return non-empty text even on fallback"
        # Falls back to Si
        assert "Si" in text

    def test_render_intent_structure_file_field_exists(self):
        """RenderIntent dataclass has a structure_file field."""
        intent = RenderIntent(
            software="cp2k",
            task_type="opt",
            structure_file="structure.cif",
            params={"CUTOFF": "400"},
        )
        assert intent.structure_file == "structure.cif"
        assert intent.params["CUTOFF"] == "400"


# ---------------------------------------------------------------------------
# Fix B: _check_physics_compatibility() in engine layer
# ---------------------------------------------------------------------------


class TestPhysicsCompatibilityEngine:
    """Engine-layer physics compatibility rule tests."""

    # ── Rule 1: OT + KPOINTS ────────────────────────────────────────────────

    _OT_WITH_KPOINTS = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT ot_kpoints_test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &KPOINTS
      SCHEME MONKHORST-PACK 4 4 4
    &END KPOINTS
    &SCF
      EPS_SCF 1.0E-6
      MAX_SCF 50
      &OT ON
        MINIMIZER DIIS
        PRECONDITIONER FULL_ALL
      &END OT
    &END SCF
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  3.0860  0.0000  0.0000
      B  0.0000  3.0860  0.0000
      C  0.0000  0.0000  5.0480
      PERIODIC XYZ
    &END CELL
    &COORD
      Si  0.000  0.000  0.000
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

    _OT_WITHOUT_KPOINTS = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT ot_no_kpoints
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &SCF
      EPS_SCF 1.0E-6
      &OT ON
        MINIMIZER DIIS
      &END OT
    &END SCF
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

    def test_ot_kpoints_produces_error(self):
        diags = _get_diags(self._OT_WITH_KPOINTS)
        assert _has_rule(diags, "ot-incompatible-with-kpoints"), (
            "Expected rule 'ot-incompatible-with-kpoints', got rule_ids: "
            + str(_rule_ids(diags))
        )
        # Must be error severity
        errors = [d for d in diags if d.rule_id == "ot-incompatible-with-kpoints"]
        assert all(d.severity == "error" for d in errors)

    def test_ot_without_kpoints_no_error(self):
        diags = _get_diags(self._OT_WITHOUT_KPOINTS)
        assert not _has_rule(diags, "ot-incompatible-with-kpoints"), (
            "Should NOT flag OT error when KPOINTS is absent"
        )

    # ── Rule 2: HFX + KPOINTS without RI ───────────────────────────────────

    _HFX_KPOINTS_NO_RI = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT hfx_kpoints
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &KPOINTS
      SCHEME MONKHORST-PACK 2 2 2
    &END KPOINTS
    &SCF
      EPS_SCF 1.0E-6
      DIAGONALIZATION ON
      ADDED_MOS 5
    &END SCF
    &XC
      &XC_FUNCTIONAL PBE0
      &END XC_FUNCTIONAL
      &HF
        FRACTION 0.25
      &END HF
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

    _HFX_KPOINTS_WITH_RI = """\
&GLOBAL
  RUN_TYPE ENERGY
  PROJECT hfx_kpoints_ri
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &KPOINTS
      SCHEME MONKHORST-PACK 2 2 2
    &END KPOINTS
    &SCF
      EPS_SCF 1.0E-6
      DIAGONALIZATION ON
      ADDED_MOS 5
    &END SCF
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
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

    def test_hfx_kpoints_no_ri_is_error(self):
        diags = _get_diags(self._HFX_KPOINTS_NO_RI)
        assert _has_rule(diags, "hfx-kpoints-requires-ri"), (
            "Expected rule 'hfx-kpoints-requires-ri', got: " + str(_rule_ids(diags))
        )
        errors = [d for d in diags if d.rule_id == "hfx-kpoints-requires-ri"]
        assert all(d.severity == "error" for d in errors)

    def test_hfx_kpoints_with_ri_no_error(self):
        diags = _get_diags(self._HFX_KPOINTS_WITH_RI)
        assert not _has_rule(diags, "hfx-kpoints-requires-ri"), (
            "Should NOT flag HFX error when &RI is present"
        )

    # ── Rule 3: CELL_OPT without STRESS_TENSOR ─────────────────────────────

    _CELL_OPT_NO_STRESS = """\
&GLOBAL
  RUN_TYPE CELL_OPT
  PROJECT cell_opt
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

    _CELL_OPT_WITH_STRESS = """\
&GLOBAL
  RUN_TYPE CELL_OPT
  PROJECT cell_opt_stress
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    STRESS_TENSOR ANALYTICAL
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

    def test_cell_opt_without_stress_tensor_warning(self):
        diags = _get_diags(self._CELL_OPT_NO_STRESS)
        assert _has_rule(diags, "cell-opt-missing-stress-tensor"), (
            "Expected rule 'cell-opt-missing-stress-tensor', got: " + str(_rule_ids(diags))
        )
        warnings = [d for d in diags if d.rule_id == "cell-opt-missing-stress-tensor"]
        assert all(d.severity == "warning" for d in warnings)

    def test_cell_opt_with_stress_tensor_no_warning(self):
        diags = _get_diags(self._CELL_OPT_WITH_STRESS)
        assert not _has_rule(diags, "cell-opt-missing-stress-tensor"), (
            "Should NOT flag STRESS_TENSOR warning when it is set"
        )

    # ── Rule 4: RUN_TYPE BAND without &BAND_STRUCTURE ──────────────────────

    _BAND_NO_BAND_STRUCTURE = """\
&GLOBAL
  RUN_TYPE BAND
  PROJECT band_test
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &KPOINTS
      SCHEME MONKHORST-PACK 4 4 4
    &END KPOINTS
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

    _BAND_WITH_BAND_STRUCTURE = """\
&GLOBAL
  RUN_TYPE BAND
  PROJECT band_full
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &KPOINTS
      SCHEME MONKHORST-PACK 4 4 4
    &END KPOINTS
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
  &PROPERTIES
    &BAND_STRUCTURE
      ADDED_MOS 10
      &KPOINT_SET
        UNITS RECIPROCAL
        SPECIAL_POINT G  0.0 0.0 0.0
        SPECIAL_POINT X  0.5 0.0 0.5
        NPOINTS 20
      &END KPOINT_SET
    &END BAND_STRUCTURE
  &END PROPERTIES
&END FORCE_EVAL
"""

    def test_band_without_band_structure_warning(self):
        diags = _get_diags(self._BAND_NO_BAND_STRUCTURE)
        assert _has_rule(diags, "band-missing-band-structure-section"), (
            "Expected rule 'band-missing-band-structure-section', got: "
            + str(_rule_ids(diags))
        )
        warnings = [d for d in diags if d.rule_id == "band-missing-band-structure-section"]
        assert all(d.severity == "warning" for d in warnings)

    def test_band_with_band_structure_no_warning(self):
        diags = _get_diags(self._BAND_WITH_BAND_STRUCTURE)
        assert not _has_rule(diags, "band-missing-band-structure-section"), (
            "Should NOT flag BAND_STRUCTURE warning when section is present"
        )

    # ── Regression: normal OPT input should have no physics errors ──────────

    def test_normal_opt_no_physics_errors(self):
        """A standard GEO_OPT input (no OT, no KPOINTS) must not trigger
        any physics compatibility errors."""
        text = """\
&GLOBAL
  RUN_TYPE GEO_OPT
  PROJECT si_opt
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &MGRID
      CUTOFF 400
      REL_CUTOFF 60
    &END MGRID
    &SCF
      EPS_SCF 1.0E-6
      MAX_SCF 50
      &OT ON
        MINIMIZER DIIS
      &END OT
    &END SCF
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      A  5.43 0 0
      B  0 5.43 0
      C  0 0 5.43
    &END CELL
    &COORD
      Si 0 0 0
      Si 1.3578 1.3578 1.3578
    &END COORD
    &KIND Si
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q4
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""
        diags = _get_diags(text)
        physics_rules = {
            "ot-incompatible-with-kpoints",
            "hfx-kpoints-requires-ri",
            "cell-opt-missing-stress-tensor",
            "band-missing-band-structure-section",
        }
        triggered = [r for r in _rule_ids(diags) if r in physics_rules]
        assert not triggered, (
            f"Unexpected physics compatibility errors on normal OPT input: {triggered}"
        )
