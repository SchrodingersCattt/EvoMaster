"""
CP2K input file validator.

Strategy (in priority order):
1. Try cp2k-input-tools (cp2k_input_tools) — official CP2K parser + linter.
   Provides full schema-based validation against cp2k_input.xml.
2. Fallback to pymatgen.io.cp2k — parses input into Python objects;
   weaker validation but no extra dependency.
3. If both unavailable, do lightweight regex-based checks only.

In all cases, custom physical-range rules are applied on top.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from validators.base import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    BaseValidator,
    Diagnostic,
    find_line,
)


class CP2KValidator(BaseValidator):
    software_name = "cp2k"

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def validate_text(
        self, text: str, source: str = "<string>"
    ) -> list[Diagnostic]:
        diags: list[Diagnostic] = []

        # --- Try cp2k-input-tools first ---
        cp2k_tools_diags = self._validate_with_cp2k_input_tools(text, source)
        if cp2k_tools_diags is not None:
            diags.extend(cp2k_tools_diags)
        else:
            # --- Fallback: pymatgen ---
            pymatgen_diags = self._validate_with_pymatgen(text, source)
            if pymatgen_diags is not None:
                diags.extend(pymatgen_diags)
            else:
                # --- Fallback: regex only ---
                diags.extend(self._validate_regex(text, source))

        # --- Always apply custom physical-range rules ---
        diags.extend(self._custom_rules(text))

        return diags

    # -----------------------------------------------------------------------
    # Strategy 1: cp2k-input-tools
    # -----------------------------------------------------------------------

    def _validate_with_cp2k_input_tools(
        self, text: str, source: str
    ) -> Optional[list[Diagnostic]]:
        """Return diagnostics from cp2k-input-tools, or None if unavailable."""
        try:
            from cp2k_input_tools.parser import CP2KInputParser  # type: ignore[import]
            from cp2k_input_tools.parser_errors import (  # type: ignore[import]
                CP2KInputParserError,
            )
        except ImportError:
            return None

        diags: list[Diagnostic] = []
        import io

        try:
            parser = CP2KInputParser()
            # CP2KInputParser.parse() accepts a file-like object
            parser.parse(io.StringIO(text))
            # If parse succeeds with no exception, add an info note
            diags.append(
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=0,
                    param="",
                    message="cp2k-input-tools: input parsed successfully (no schema errors).",
                )
            )
        except CP2KInputParserError as exc:
            # exc carries line number in exc.line_nr (1-based)
            line_nr = getattr(exc, "line_nr", 0) or 0
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=line_nr,
                    param="",
                    message=f"CP2K parse error: {exc}",
                    suggestion="Check section/keyword spelling against https://manual.cp2k.org",
                )
            )
        except Exception as exc:  # noqa: BLE001
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="",
                    message=f"cp2k-input-tools raised unexpected error: {exc}",
                )
            )

        return diags

    # -----------------------------------------------------------------------
    # Strategy 2: pymatgen
    # -----------------------------------------------------------------------

    def _validate_with_pymatgen(
        self, text: str, source: str
    ) -> Optional[list[Diagnostic]]:
        """Return diagnostics from pymatgen.io.cp2k, or None if unavailable."""
        try:
            from pymatgen.io.cp2k.inputs import Cp2kInput  # type: ignore[import]
        except ImportError:
            return None

        diags: list[Diagnostic] = []
        try:
            Cp2kInput.from_str(text)
            diags.append(
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=0,
                    param="",
                    message="pymatgen: CP2K input parsed without errors.",
                )
            )
        except Exception as exc:  # noqa: BLE001
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="",
                    message=f"pymatgen CP2K parse error: {exc}",
                    suggestion="Check section structure and keyword spelling.",
                )
            )
        return diags

    # -----------------------------------------------------------------------
    # Strategy 3: regex-only (last resort)
    # -----------------------------------------------------------------------

    def _validate_regex(self, text: str, source: str) -> list[Diagnostic]:
        """Minimal structural checks via regex when no library is available."""
        diags: list[Diagnostic] = []

        # Check balanced &SECTION / &END SECTION
        opens = re.findall(r"^\s*&(\w+)", text, re.MULTILINE)
        ends = re.findall(r"^\s*&END(?:\s+(\w+))?", text, re.MULTILINE | re.IGNORECASE)
        n_open = len([o for o in opens if o.upper() != "END"])
        n_end = len(ends)
        if n_open != n_end:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="",
                    message=(
                        f"Unbalanced sections: {n_open} section opens vs "
                        f"{n_end} &END statements."
                    ),
                    suggestion="Every &SECTION must have a matching &END SECTION.",
                )
            )

        # Must have &GLOBAL
        if not re.search(r"^\s*&GLOBAL\b", text, re.MULTILINE | re.IGNORECASE):
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="GLOBAL",
                    message="Missing required &GLOBAL section.",
                )
            )

        # Must have &FORCE_EVAL
        if not re.search(r"^\s*&FORCE_EVAL\b", text, re.MULTILINE | re.IGNORECASE):
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="FORCE_EVAL",
                    message="Missing required &FORCE_EVAL section.",
                )
            )

        return diags

    # -----------------------------------------------------------------------
    # Custom physical-range rules (always applied)
    # -----------------------------------------------------------------------

    def _custom_rules(self, text: str) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        diags.extend(self._check_cutoff(text))
        diags.extend(self._check_kpoints(text))
        diags.extend(self._check_scf_convergence(text))
        diags.extend(self._check_run_type(text))
        return diags

    def _check_cutoff(self, text: str) -> list[Diagnostic]:
        """Warn if CUTOFF is outside typical range [200, 1200] Ry."""
        diags: list[Diagnostic] = []
        m = re.search(r"^\s*CUTOFF\s+(\d+(?:\.\d*)?)", text, re.MULTILINE | re.IGNORECASE)
        if not m:
            return diags
        try:
            value = float(m.group(1))
        except ValueError:
            return diags

        line = find_line(text, r"^\s*CUTOFF\s+\d")
        if value < 200:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="CUTOFF",
                    message=f"CUTOFF={value:.0f} Ry is below the typical minimum of 200 Ry.",
                    suggestion="Use CUTOFF >= 300 Ry for production runs; test convergence.",
                )
            )
        elif value > 1200:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="CUTOFF",
                    message=f"CUTOFF={value:.0f} Ry is unusually high (> 1200 Ry).",
                    suggestion="Verify whether such a large cutoff is needed for your basis set.",
                )
            )
        else:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=line,
                    param="CUTOFF",
                    message=f"CUTOFF={value:.0f} Ry is within typical range [200, 1200] Ry.",
                )
            )
        return diags

    def _check_kpoints(self, text: str) -> list[Diagnostic]:
        """Info: report KPOINTS scheme being used."""
        diags: list[Diagnostic] = []
        m = re.search(
            r"^\s*SCHEME\s+(\S+(?:\s+\d+\s+\d+\s+\d+)?)",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if m:
            line = find_line(text, r"^\s*SCHEME\s+")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=line,
                    param="KPOINTS",
                    message=f"K-point scheme: {m.group(1).strip()}",
                )
            )
        return diags

    def _check_scf_convergence(self, text: str) -> list[Diagnostic]:
        """Warn if EPS_SCF is looser than 1e-4 (likely unconverged)."""
        diags: list[Diagnostic] = []
        m = re.search(
            r"^\s*EPS_SCF\s+([0-9Ee.+-]+)",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if not m:
            return diags
        try:
            value = float(m.group(1))
        except ValueError:
            return diags

        line = find_line(text, r"^\s*EPS_SCF\s+")
        if value > 1e-4:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="EPS_SCF",
                    message=f"EPS_SCF={m.group(1)} is looser than 1e-4; SCF may not be well converged.",
                    suggestion="For production: EPS_SCF 1.0E-6 or tighter.",
                )
            )
        return diags

    def _check_run_type(self, text: str) -> list[Diagnostic]:
        """Info: report RUN_TYPE."""
        m = re.search(
            r"^\s*RUN_TYPE\s+(\S+)",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if m:
            line = find_line(text, r"^\s*RUN_TYPE\s+")
            return [
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=line,
                    param="RUN_TYPE",
                    message=f"RUN_TYPE = {m.group(1).upper()}",
                )
            ]
        return []
