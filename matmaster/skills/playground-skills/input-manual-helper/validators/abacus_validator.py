"""
ABACUS INPUT file validator.

Uses regex-based checks since pymatgen does not have a dedicated
ABACUS input parser (as of 2024). Custom rules cover the most common
pitfalls when writing ABACUS INPUT files.

Custom rules:
  - ecutwfc: warn if < 30 Ry or error if < 10 Ry
  - calculation: check known values
  - basis_type=lcao: warn if orbital_dir missing
  - relax/cell-relax: error if cal_force=0
  - md: error if cal_force=0, warn if md_dt > 3 fs
  - noncolin=1: error if nspin != 4
  - lspinorb=1: error if noncolin != 1
  - lda_plus_u=1: error if hubbard_u or orbital_corr missing
  - mixing_beta: error if outside (0, 1]
  - scf_nmax: warn if < 20
"""

from __future__ import annotations

import re

from validators.base import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    BaseValidator,
    Diagnostic,
    find_line,
)

# ---------------------------------------------------------------------------
# Known valid values for enumerated parameters
# ---------------------------------------------------------------------------

_VALID_CALCULATIONS = frozenset(
    {
        "scf",
        "relax",
        "cell-relax",
        "md",
        "nscf",
        "get_s",
    }
)

_VALID_BASIS_TYPES = frozenset({"pw", "lcao", "lcao_in_pw"})

_VALID_SMEARING = frozenset(
    {
        "fixed",
        "gauss",
        "fermi-dirac",
        "methfessel-paxton",
        "marzari-vanderbilt",
    }
)

_VALID_DFT_FUNCTIONAL = frozenset(
    {
        "default",
        "lda",
        "pbe",
        "pbesol",
        "hse",
        "scan",
        "pbe0",
        "revpbe",
        "vdwd3",
    }
)

_VALID_MD_TYPES = frozenset({"nvt", "npt", "nve", "msst", "fire"})

_VALID_RELAX_METHODS = frozenset({"bfgs", "cg", "sd", "fire"})


# ---------------------------------------------------------------------------
# Helper: parse a key from the INPUT file
# ---------------------------------------------------------------------------


def _parse_value(text: str, key: str) -> str | None:
    """Return the raw string value for *key* in an ABACUS INPUT text, or None."""
    pattern = re.compile(
        r"^\s*" + re.escape(key) + r"\s+(\S+)",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _parse_int(text: str, key: str) -> int | None:
    v = _parse_value(text, key)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _parse_float(text: str, key: str) -> float | None:
    v = _parse_value(text, key)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


class ABACUSValidator(BaseValidator):
    """Validator for ABACUS INPUT files."""

    software_name = "abacus"

    def validate_text(self, text: str, source: str = "<string>") -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        diags.extend(self._check_header(text))
        diags.extend(self._check_ecutwfc(text))
        diags.extend(self._check_calculation(text))
        diags.extend(self._check_basis_type(text))
        diags.extend(self._check_relax(text))
        diags.extend(self._check_md(text))
        diags.extend(self._check_spin(text))
        diags.extend(self._check_dft_plus_u(text))
        diags.extend(self._check_mixing(text))
        diags.extend(self._check_scf_nmax(text))
        diags.extend(self._check_smearing(text))
        diags.extend(self._check_nscf_bands(text))
        diags.extend(self._check_efield(text))
        diags.extend(self._check_relax_output(text))
        diags.extend(self._check_scf_for_nscf(text))
        return diags

    # -----------------------------------------------------------------------
    # Individual checks
    # -----------------------------------------------------------------------

    def _check_header(self, text: str) -> list[Diagnostic]:
        """Warn if INPUT_PARAMETERS header is missing."""
        diags: list[Diagnostic] = []
        if not re.search(
            r"^\s*INPUT_PARAMETERS\s*$", text, re.IGNORECASE | re.MULTILINE
        ):
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message="ABACUS INPUT file should start with 'INPUT_PARAMETERS' on its own line.",
                    line=1,
                    suggestion="Add 'INPUT_PARAMETERS' as the first non-comment line.",
                )
            )
        return diags

    def _check_ecutwfc(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        val = _parse_float(text, "ecutwfc")
        if val is None:
            return diags
        line_num = find_line(text, "ecutwfc")
        if val < 10:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=f"ecutwfc = {val} Ry is too low. Minimum recommended: 30 Ry for NCPP.",
                    line=line_num,
                    param="ecutwfc",
                    suggestion="Increase ecutwfc to at least 50 Ry.",
                )
            )
        elif val < 30:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"ecutwfc = {val} Ry may be too low for accurate results. Consider >= 50 Ry.",
                    line=line_num,
                    param="ecutwfc",
                )
            )
        return diags

    def _check_calculation(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        val = _parse_value(text, "calculation")
        if val is None:
            return diags
        calc = val.lower()
        if calc not in _VALID_CALCULATIONS:
            line_num = find_line(text, "calculation")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=f"Unknown calculation type '{val}'. Valid: {', '.join(sorted(_VALID_CALCULATIONS))}.",
                    line=line_num,
                    param="calculation",
                )
            )
        return diags

    def _check_basis_type(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        val = _parse_value(text, "basis_type")
        if val is None:
            return diags
        bt = val.lower()
        if bt not in _VALID_BASIS_TYPES:
            line_num = find_line(text, "basis_type")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=f"Unknown basis_type '{val}'. Valid: {', '.join(sorted(_VALID_BASIS_TYPES))}.",
                    line=line_num,
                    param="basis_type",
                )
            )
        if bt == "lcao":
            orbital_dir = _parse_value(text, "orbital_dir")
            if orbital_dir is None:
                diags.append(
                    Diagnostic(
                        severity=SEVERITY_WARNING,
                        message="basis_type=lcao requires 'orbital_dir' pointing to .orb files.",
                        param="orbital_dir",
                        suggestion="Add: orbital_dir  /path/to/orb/files",
                    )
                )
        return diags

    def _check_relax(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        calc = (_parse_value(text, "calculation") or "scf").lower()
        if calc not in ("relax", "cell-relax"):
            return diags

        cal_force = _parse_int(text, "cal_force")
        # ABACUS defaults cal_force=0; missing or explicit 0 both break relax
        if cal_force is None or cal_force == 0:
            line_num = find_line(text, "cal_force") or 1
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=(
                        f"calculation='{calc}' requires forces but cal_force is "
                        f"{'missing (default 0)' if cal_force is None else '0'}. "
                        "Set cal_force=1. ABACUS never implies cal_force from "
                        "the calculation keyword."
                    ),
                    line=line_num,
                    param="cal_force",
                    suggestion="Add: cal_force  1",
                )
            )

        # force_thr_ev should be present for relax/cell-relax
        if _parse_float(text, "force_thr_ev") is None:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"calculation='{calc}' but force_thr_ev is not set. Default may be too loose or tight. Recommended: force_thr_ev 0.01 (eV/Å). Do NOT use force_thr (Ry/Bohr) — different units!",
                    param="force_thr_ev",
                    suggestion="Add: force_thr_ev  0.01",
                )
            )

        if calc == "cell-relax":
            cal_stress = _parse_int(text, "cal_stress")
            # ABACUS defaults cal_stress=0; missing or explicit 0 breaks cell-relax
            if cal_stress is None or cal_stress == 0:
                line_num = find_line(text, "cal_stress") or 1
                diags.append(
                    Diagnostic(
                        severity=SEVERITY_ERROR,
                        message=(
                            "calculation='cell-relax' requires stress tensor but "
                            f"cal_stress is {'missing (default 0)' if cal_stress is None else '0'}. "
                            "Set cal_stress=1. Without it cell vectors are NOT optimized."
                        ),
                        line=line_num,
                        param="cal_stress",
                        suggestion="Add: cal_stress  1",
                    )
                )

        relax_method = _parse_value(text, "relax_method")
        if relax_method and relax_method.lower() not in _VALID_RELAX_METHODS:
            line_num = find_line(text, "relax_method")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"Unknown relax_method '{relax_method}'. Valid: {', '.join(sorted(_VALID_RELAX_METHODS))}.",
                    line=line_num,
                    param="relax_method",
                )
            )
        return diags

    def _check_md(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        calc = (_parse_value(text, "calculation") or "scf").lower()
        if calc != "md":
            return diags

        cal_force = _parse_int(text, "cal_force")
        # ABACUS defaults cal_force=0; missing or explicit 0 both break MD
        if cal_force is None or cal_force == 0:
            line_num = find_line(text, "cal_force") or 1
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=(
                        "calculation='md' requires forces but cal_force is "
                        f"{'missing (default 0)' if cal_force is None else '0'}. "
                        "Set cal_force=1."
                    ),
                    line=line_num,
                    param="cal_force",
                    suggestion="Add: cal_force  1",
                )
            )

        md_dt = _parse_float(text, "md_dt")
        if md_dt is not None and md_dt > 3.0:
            line_num = find_line(text, "md_dt")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"md_dt = {md_dt} fs is large. Values > 3 fs risk energy drift. Typical: 0.5–2 fs.",
                    line=line_num,
                    param="md_dt",
                )
            )

        md_tfirst = _parse_float(text, "md_tfirst")
        if md_tfirst is not None and md_tfirst < 0:
            line_num = find_line(text, "md_tfirst")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=f"md_tfirst = {md_tfirst} K is negative. Must be > 0.",
                    line=line_num,
                    param="md_tfirst",
                )
            )

        md_type = _parse_value(text, "md_type")
        if md_type and md_type.lower() not in _VALID_MD_TYPES:
            line_num = find_line(text, "md_type")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"Unknown md_type '{md_type}'. Valid: {', '.join(sorted(_VALID_MD_TYPES))}.",
                    line=line_num,
                    param="md_type",
                )
            )
        return diags

    def _check_spin(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        noncolin = _parse_int(text, "noncolin")
        lspinorb = _parse_int(text, "lspinorb")
        nspin = _parse_int(text, "nspin")

        if noncolin == 1 and nspin != 4:
            line_num = find_line(text, "noncolin")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message="noncolin=1 requires nspin=4.",
                    line=line_num,
                    param="noncolin",
                    suggestion="Add or change: nspin  4",
                )
            )

        if lspinorb == 1 and noncolin != 1:
            line_num = find_line(text, "lspinorb")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message="lspinorb=1 requires noncolin=1 and nspin=4.",
                    line=line_num,
                    param="lspinorb",
                    suggestion="Add: noncolin  1  and  nspin  4",
                )
            )
        return diags

    def _check_dft_plus_u(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        lda_plus_u = _parse_int(text, "lda_plus_u")
        if lda_plus_u != 1:
            return diags

        if _parse_value(text, "hubbard_u") is None:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message="lda_plus_u=1 requires 'hubbard_u' (U in eV for each atom type).",
                    param="hubbard_u",
                    suggestion="Add: hubbard_u  <U1> <U2> ...",
                )
            )
        if _parse_value(text, "orbital_corr") is None:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message="lda_plus_u=1 requires 'orbital_corr' (l quantum number for each atom type; -1=none).",
                    param="orbital_corr",
                    suggestion="Add: orbital_corr  <l1> <l2> ...",
                )
            )
        return diags

    def _check_mixing(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        mb = _parse_float(text, "mixing_beta")
        if mb is None:
            return diags
        if mb <= 0 or mb > 1:
            line_num = find_line(text, "mixing_beta")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=f"mixing_beta = {mb} is outside (0, 1]. Typical values: 0.1–0.8.",
                    line=line_num,
                    param="mixing_beta",
                )
            )
        return diags

    def _check_scf_nmax(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        val = _parse_int(text, "scf_nmax")
        if val is None:
            return diags
        if val < 20:
            line_num = find_line(text, "scf_nmax")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"scf_nmax = {val} is low. SCF may not converge. Recommend >= 50.",
                    line=line_num,
                    param="scf_nmax",
                )
            )
        return diags

    def _check_smearing(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        val = _parse_value(text, "smearing_method")
        if val is None:
            return diags
        sm = val.lower()
        if sm not in _VALID_SMEARING:
            line_num = find_line(text, "smearing_method")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"Unknown smearing_method '{val}'. Valid: {', '.join(sorted(_VALID_SMEARING))}.",
                    line=line_num,
                    param="smearing_method",
                )
            )
        if sm not in ("fixed", None) and _parse_float(text, "smearing_sigma") is None:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=f"smearing_method='{val}' set but smearing_sigma is absent. Default (0.015 Ry) will be used.",
                    param="smearing_sigma",
                    suggestion="Add: smearing_sigma  0.015",
                )
            )
        return diags

    def _check_efield(self, text: str) -> list[Diagnostic]:
        """Check electric field / dipole correction parameter consistency."""
        diags: list[Diagnostic] = []
        efield_flag = _parse_int(text, "efield_flag")
        if efield_flag != 1:
            return diags
        # efield_dir should be set
        efield_dir = _parse_int(text, "efield_dir")
        if efield_dir is None:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message="efield_flag=1 but efield_dir not set. Default is 2 (z-axis). Set explicitly for clarity.",
                    param="efield_dir",
                    suggestion="Add: efield_dir  2",
                )
            )
        # gate_flag checks
        gate_flag = _parse_int(text, "gate_flag")
        if gate_flag == 1:
            dip_cor = _parse_int(text, "dip_cor_flag")
            if dip_cor != 1:
                diags.append(
                    Diagnostic(
                        severity=SEVERITY_WARNING,
                        message="gate_flag=1 typically requires dip_cor_flag=1 for proper charged slab treatment.",
                        param="dip_cor_flag",
                        suggestion="Add: dip_cor_flag  1",
                    )
                )
        return diags

    def _check_relax_output(self, text: str) -> list[Diagnostic]:
        """Check that relaxation produces output structure."""
        diags: list[Diagnostic] = []
        calc = (_parse_value(text, "calculation") or "scf").lower()
        if calc not in ("relax", "cell-relax"):
            return diags
        out_stru = _parse_int(text, "out_stru")
        if out_stru is None or out_stru == 0:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=(
                        f"calculation='{calc}' but out_stru is not set to 1. "
                        "Without it the optimized structure (STRU_NOW) is not written."
                    ),
                    param="out_stru",
                    suggestion="Add: out_stru  1",
                )
            )
        return diags

    def _check_scf_for_nscf(self, text: str) -> list[Diagnostic]:
        """Check SCF + out_chg for NSCF workflow readiness."""
        diags: list[Diagnostic] = []
        calc = (_parse_value(text, "calculation") or "scf").lower()
        if calc != "scf":
            return diags
        # If this is a plain SCF with out_chg=0, warn that it won't support followup nscf
        out_chg = _parse_int(text, "out_chg")
        # Only warn if there are signs this might be prep for nscf (e.g. mentions in comments)
        # Actually, always recommend out_chg=1 for SCF as it's cheap and enables restart
        if out_chg is None or out_chg == 0:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message=(
                        "SCF calculation without out_chg=1. Setting out_chg=1 is recommended: "
                        "it enables restart from converged charge and is required for any "
                        "follow-up NSCF (band structure / DOS) calculation."
                    ),
                    param="out_chg",
                    suggestion="Add: out_chg  1",
                )
            )
        return diags

    def _check_nscf_bands(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        calc = (_parse_value(text, "calculation") or "scf").lower()
        if calc != "nscf":
            return diags
        out_band = _parse_int(text, "out_band")
        out_dos = _parse_int(text, "out_dos")
        if (out_band or out_dos) and _parse_int(text, "nbands") is None:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    message="nscf with out_band/out_dos: set 'nbands' explicitly to include empty states above Fermi level.",
                    param="nbands",
                    suggestion="Add: nbands  <number>  # typically nelec/2 + 10..30",
                )
            )
        # symmetry must be 0 for NSCF band/DOS — symmetry=1 folds k-path
        sym = _parse_int(text, "symmetry")
        if out_band or out_dos:
            if sym is None or sym != 0:
                line_num = find_line(text, "symmetry") or 1
                diags.append(
                    Diagnostic(
                        severity=SEVERITY_ERROR,
                        message=(
                            "NSCF with out_band/out_dos requires symmetry=0. "
                            f"{'symmetry is not set (default 1)' if sym is None else f'symmetry={sym}'} — "
                            "line-mode k-paths will be folded/reordered, producing wrong band plots."
                        ),
                        line=line_num,
                        param="symmetry",
                        suggestion="Add: symmetry  0",
                    )
                )
        # init_chg must be 'file' for NSCF
        init_chg = _parse_value(text, "init_chg")
        if init_chg is None or init_chg.lower() != "file":
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    message=(
                        "NSCF calculation requires init_chg=file to read converged "
                        "charge density from a prior SCF step. Without it, NSCF "
                        "re-runs SCF from scratch with atomic charge."
                    ),
                    param="init_chg",
                    suggestion="Add: init_chg  file",
                )
            )
        return diags
