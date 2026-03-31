"""
Quantum ESPRESSO (pw.x) input file validator.

Uses ASE's ase.io.espresso.read_espresso_in() as the primary parser.
Falls back to regex-based checks if ASE is unavailable.

Custom rules applied:
  - ecutwfc: warn if < 20 Ry or > 200 Ry
  - ecutrho: warn if ratio to ecutwfc is outside [4, 12]
  - calculation: must be one of known types
  - conv_thr: warn if looser than 1e-6
  - occupations / smearing consistency
"""

from __future__ import annotations

import re

from validators.base import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    BaseValidator,
    Diagnostic,
    find_line,
)

_KNOWN_CALCULATIONS = {
    "scf",
    "nscf",
    "bands",
    "relax",
    "md",
    "vc-relax",
    "vc-md",
}

_KNOWN_SMEARING = {
    "gaussian",
    "methfessel-paxton",
    "m-p",
    "marzari-vanderbilt",
    "cold",
    "fermi-dirac",
    "f-d",
}


class QEValidator(BaseValidator):
    software_name = "qe"

    def validate_text(self, text: str, source: str = "<string>") -> list[Diagnostic]:
        diags: list[Diagnostic] = []

        # Strategy 1: ASE parser
        ase_diags = self._validate_with_ase(text, source)
        if ase_diags is not None:
            diags.extend(ase_diags)

        # Custom rules always applied
        diags.extend(self._custom_rules(text))
        return diags

    # -----------------------------------------------------------------------
    # Strategy 1: ASE
    # -----------------------------------------------------------------------

    def _validate_with_ase(self, text: str, source: str) -> list[Diagnostic] | None:
        try:
            import io

            from ase.io.espresso import read_espresso_in  # type: ignore[import]
        except ImportError:
            return None

        diags: list[Diagnostic] = []
        try:
            read_espresso_in(io.StringIO(text))
            diags.append(
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=0,
                    param="",
                    message="ASE: QE input parsed successfully.",
                )
            )
        except Exception as exc:  # noqa: BLE001
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="",
                    message=f"ASE QE parse error: {exc}",
                    suggestion=(
                        "Check namelists (&CONTROL, &SYSTEM, &ELECTRONS), "
                        "ATOMIC_SPECIES, ATOMIC_POSITIONS, K_POINTS sections."
                    ),
                )
            )
        return diags

    # -----------------------------------------------------------------------
    # Custom physical-range rules
    # -----------------------------------------------------------------------

    def _custom_rules(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        diags.extend(self._check_calculation(text))
        diags.extend(self._check_ecutwfc(text))
        diags.extend(self._check_ecutrho(text))
        diags.extend(self._check_conv_thr(text))
        diags.extend(self._check_occupations(text))
        return diags

    def _check_calculation(self, text: str) -> list[Diagnostic]:
        m = re.search(
            r"\bcalculation\s*=\s*['\"]?(\S+?)['\"]?\s*[,\n]",
            text,
            re.IGNORECASE,
        )
        if not m:
            return []
        val = m.group(1).lower().strip("'\"")
        line = find_line(text, r"\bcalculation\s*=")
        if val not in _KNOWN_CALCULATIONS:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="calculation",
                    message=f"Unknown calculation type: '{val}'.",
                    suggestion=f"Known types: {', '.join(sorted(_KNOWN_CALCULATIONS))}",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="calculation",
                message=f"calculation = '{val}'",
            )
        ]

    def _check_ecutwfc(self, text: str) -> list[Diagnostic]:
        m = re.search(
            r"\becutwfc\s*=\s*([0-9Ee.+-]+)",
            text,
            re.IGNORECASE,
        )
        if not m:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="ecutwfc",
                    message="ecutwfc not found in input.",
                    suggestion="Set ecutwfc in &SYSTEM namelist (typical range: 40–100 Ry).",
                )
            ]
        try:
            val = float(m.group(1))
        except ValueError:
            return []

        line = find_line(text, r"\becutwfc\s*=")
        if val < 20:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="ecutwfc",
                    message=f"ecutwfc = {val} Ry is very low (< 20 Ry).",
                    suggestion="Typical production values: 40–100 Ry; test convergence.",
                )
            ]
        if val > 200:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="ecutwfc",
                    message=f"ecutwfc = {val} Ry is unusually high (> 200 Ry).",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="ecutwfc",
                message=f"ecutwfc = {val} Ry",
            )
        ]

    def _check_ecutrho(self, text: str) -> list[Diagnostic]:
        mwfc = re.search(r"\becutwfc\s*=\s*([0-9Ee.+-]+)", text, re.IGNORECASE)
        mrho = re.search(r"\becutrho\s*=\s*([0-9Ee.+-]+)", text, re.IGNORECASE)
        if not mwfc or not mrho:
            return []
        try:
            wfc = float(mwfc.group(1))
            rho = float(mrho.group(1))
        except ValueError:
            return []
        if wfc <= 0:
            return []

        ratio = rho / wfc
        line = find_line(text, r"\becutrho\s*=")
        if ratio < 4:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="ecutrho",
                    message=f"ecutrho/ecutwfc = {ratio:.1f} is below 4 (may under-converge density).",
                    suggestion="For NCPPs: ecutrho = 4 × ecutwfc; for USPPs/PAW: 8–12×.",
                )
            ]
        if ratio > 12:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="ecutrho",
                    message=f"ecutrho/ecutwfc = {ratio:.1f} is unusually large (> 12).",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="ecutrho",
                message=f"ecutrho/ecutwfc = {ratio:.1f}",
            )
        ]

    def _check_conv_thr(self, text: str) -> list[Diagnostic]:
        m = re.search(r"\bconv_thr\s*=\s*([0-9Ee.+-]+)", text, re.IGNORECASE)
        if not m:
            return []
        try:
            val = float(m.group(1))
        except ValueError:
            return []
        line = find_line(text, r"\bconv_thr\s*=")
        if val > 1e-6:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="conv_thr",
                    message=f"conv_thr = {m.group(1)} is looser than 1e-6.",
                    suggestion="For production: conv_thr = 1.0d-8 or tighter.",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="conv_thr",
                message=f"conv_thr = {m.group(1)}",
            )
        ]

    def _check_occupations(self, text: str) -> list[Diagnostic]:
        """Warn if smearing is set without occupations='smearing', or vice versa."""
        diags: list[Diagnostic] = []
        has_smearing_kw = bool(re.search(r"\bsmearing\s*=", text, re.IGNORECASE))
        has_degauss = bool(re.search(r"\bdegauss\s*=", text, re.IGNORECASE))
        m_occ = re.search(
            r"\boccupations\s*=\s*['\"]?(\S+?)['\"]?\s*[,\n]",
            text,
            re.IGNORECASE,
        )
        occ_val = m_occ.group(1).lower().strip("'\"") if m_occ else None

        if occ_val == "smearing" and not has_degauss:
            line = find_line(text, r"\boccupations\s*=")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="occupations",
                    message="occupations='smearing' but degauss is not set.",
                    suggestion="Add degauss = 0.01 (Ry) or appropriate value.",
                )
            )
        if (has_smearing_kw or has_degauss) and occ_val not in (
            "smearing",
            None,
        ):
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="smearing",
                    message=(
                        f"smearing/degauss is set but occupations='{occ_val}'. "
                        "These may be inconsistent."
                    ),
                )
            )
        return diags
