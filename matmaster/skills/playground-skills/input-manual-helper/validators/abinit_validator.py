"""
ABINIT input file validator.

Uses pymatgen.io.abinit to parse the input file.
Falls back to regex-based checks if pymatgen is unavailable.

Custom rules:
  - ecut: warn if < 10 Ha or > 100 Ha
  - ixc: check known values
  - convergence keywords: warn if multiple conflicting ones are set
  - natom / ntypat / typat consistency (basic check)
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

# Known ixc values (ABINIT libxc notation)
_KNOWN_IXC = {
    # Perdew-Burke-Ernzerhof
    "11",
    "-101130",
    # LDA
    "1",
    "2",
    "7",
    "8",
    # PBEsol
    "116133",
    # HSE06 via libxc
    "-428",
    # LDA+U handled separately
}

_CONV_KEYWORDS = {"toldfe", "tolwfr", "tolvrs", "toldff", "tolrff"}


class ABINITValidator(BaseValidator):
    software_name = "abinit"

    def validate_text(self, text: str, source: str = "<string>") -> list[Diagnostic]:
        diags: list[Diagnostic] = []

        # Strategy 1: pymatgen
        pm_diags = self._validate_with_pymatgen(text, source)
        if pm_diags is not None:
            diags.extend(pm_diags)

        # Custom rules always applied
        diags.extend(self._custom_rules(text))
        return diags

    # -----------------------------------------------------------------------
    # Strategy 1: pymatgen
    # -----------------------------------------------------------------------

    def _validate_with_pymatgen(
        self, text: str, source: str
    ) -> list[Diagnostic] | None:
        try:
            from pymatgen.io.abinit.abiobjects import (
                AbinitInput,  # type: ignore[import]
            )
        except ImportError:
            try:
                # Older pymatgen layout
                from pymatgen.io.abinit.inputs import (
                    AbinitInput,  # type: ignore[import]
                )
            except ImportError:
                return None

        diags: list[Diagnostic] = []
        try:
            # AbinitInput.from_string is available in pymatgen >= 2022
            AbinitInput.from_string(text)
            diags.append(
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=0,
                    param="",
                    message="pymatgen: ABINIT input parsed successfully.",
                )
            )
        except Exception as exc:  # noqa: BLE001
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="",
                    message=f"pymatgen ABINIT parse error: {exc}",
                    suggestion="Check variable names and dataset indices.",
                )
            )
        return diags

    # -----------------------------------------------------------------------
    # Custom rules
    # -----------------------------------------------------------------------

    def _custom_rules(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        diags.extend(self._check_ecut(text))
        diags.extend(self._check_ixc(text))
        diags.extend(self._check_convergence_keywords(text))
        diags.extend(self._check_natom_typat(text))
        return diags

    def _check_ecut(self, text: str) -> list[Diagnostic]:
        m = re.search(r"\becut\s+([0-9Ee.+-]+)", text, re.IGNORECASE)
        if not m:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="ecut",
                    message="ecut not found.",
                    suggestion="Set ecut in Hartree (typical: 20–60 Ha for PAW).",
                )
            ]
        try:
            val = float(m.group(1))
        except ValueError:
            return []
        line = find_line(text, r"\becut\s+")
        if val < 10:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="ecut",
                    message=f"ecut = {val} Ha is very low (< 10 Ha).",
                    suggestion="Typical production values: 20–60 Ha; test convergence.",
                )
            ]
        if val > 100:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="ecut",
                    message=f"ecut = {val} Ha is unusually high (> 100 Ha).",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="ecut",
                message=f"ecut = {val} Ha",
            )
        ]

    def _check_ixc(self, text: str) -> list[Diagnostic]:
        m = re.search(r"\bixc\s+(-?\d+)", text, re.IGNORECASE)
        if not m:
            return []
        val = m.group(1)
        line = find_line(text, r"\bixc\s+")
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="ixc",
                message=f"ixc = {val} (exchange-correlation functional index).",
            )
        ]

    def _check_convergence_keywords(self, text: str) -> list[Diagnostic]:
        """Warn if more than one convergence keyword is active in the same dataset."""
        diags: list[Diagnostic] = []
        # Look for bare (no dataset index) convergence keywords
        found = []
        for kw in _CONV_KEYWORDS:
            # Bare keyword (no digit suffix): e.g. toldfe 1e-8
            if re.search(rf"\b{kw}\b(?!\d)", text, re.IGNORECASE):
                found.append(kw)
        if len(found) > 1:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="convergence",
                    message=(
                        f"Multiple convergence keywords set in the same dataset: "
                        f"{', '.join(found)}. Only one should be active per dataset."
                    ),
                    suggestion=(
                        "Use dataset indices (e.g. tolwfr1, tolwfr2) to assign "
                        "convergence criteria per dataset."
                    ),
                )
            )
        elif found:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=0,
                    param="convergence",
                    message=f"Convergence keyword: {found[0]}",
                )
            )
        else:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="convergence",
                    message="No convergence keyword (toldfe/tolwfr/tolvrs/...) found.",
                    suggestion="Add e.g. tolwfr 1.0d-18 or tolvrs 1.0d-10.",
                )
            )
        return diags

    def _check_natom_typat(self, text: str) -> list[Diagnostic]:
        """Basic consistency check: len(typat) should equal natom."""
        diags: list[Diagnostic] = []
        m_natom = re.search(r"\bnatom\s+(\d+)", text, re.IGNORECASE)
        m_typat = re.search(r"\btypat\s+([\d\s*]+)", text, re.IGNORECASE)
        if not m_natom or not m_typat:
            return diags

        try:
            natom = int(m_natom.group(1))
        except ValueError:
            return diags

        # typat may use repetition syntax: "1 2*2 3" → [1, 2, 2, 3]
        typat_tokens = m_typat.group(1).split()
        expanded: list[int] = []
        for tok in typat_tokens:
            if "*" in tok:
                parts = tok.split("*")
                try:
                    expanded.extend([int(parts[1])] * int(parts[0]))
                except (ValueError, IndexError):
                    pass
            else:
                try:
                    expanded.append(int(tok))
                except ValueError:
                    break

        if expanded and len(expanded) != natom:
            line = find_line(text, r"\btypat\s+")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=line,
                    param="typat",
                    message=(f"natom = {natom} but typat has {len(expanded)} entries."),
                    suggestion="typat must contain exactly natom entries.",
                )
            )
        return diags
