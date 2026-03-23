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
        diags.extend(self._check_physics_compatibility(text))
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

    def _check_physics_compatibility(self, text: str) -> list[Diagnostic]:
        """Check cross-section physical compatibility constraints in CP2K input.

        Rules:
        1. OT + KPOINTS → error  (OT is Γ-point only, incompatible with k-points)
        2. HFX + KPOINTS without RI → error  (only RI-HFX works with k-points)
        3. CELL_OPT without STRESS_TENSOR → warning
        4. RUN_TYPE BAND without &BAND_STRUCTURE section → warning
        """
        diags: list[Diagnostic] = []

        has_ot = bool(re.search(r"^\s*&OT\b", text, re.MULTILINE | re.IGNORECASE))
        has_kpoints = bool(
            re.search(r"^\s*&KPOINTS\b", text, re.MULTILINE | re.IGNORECASE)
        )
        has_hf = bool(re.search(r"^\s*&HF\b", text, re.MULTILINE | re.IGNORECASE))
        # RI section inside &HF (CP2K 8+: &RI; older: &RI_HFX inside &HF is rare)
        has_ri = bool(re.search(r"^\s*&RI\b", text, re.MULTILINE | re.IGNORECASE))

        # Rule 1: OT + KPOINTS
        if has_ot and has_kpoints:
            line = find_line(text, r"^\s*&OT\b")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=line,
                    param="OT",
                    message=(
                        "OT (Orbital Transformation) is incompatible with KPOINTS: "
                        "OT is a Γ-point-only SCF solver. "
                        "CP2K will abort with 'OT not possible with kpoint calculations'."
                    ),
                    suggestion=(
                        "Remove the &OT section and use DIAGONALIZATION instead. "
                        "Add ADDED_MOS to capture unoccupied states for k-point runs."
                    ),
                )
            )

        # Rule 2: HFX + KPOINTS without RI
        if has_hf and has_kpoints and not has_ri:
            line = find_line(text, r"^\s*&HF\b")
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=line,
                    param="HF",
                    message=(
                        "Only RI-HFX is implemented for K-points in CP2K. "
                        "Using &HF without &RI under k-point sampling will abort at runtime."
                    ),
                    suggestion=(
                        "Add a &RI section inside &HF:\n"
                        "  &RI\n"
                        "    KFN_REUSE_NUMBER  1\n"
                        "    NGROUPS           4\n"
                        "  &END RI"
                    ),
                )
            )

        # Rule 3: CELL_OPT without STRESS_TENSOR
        run_type_m = re.search(
            r"^\s*RUN_TYPE\s+(\S+)", text, re.MULTILINE | re.IGNORECASE
        )
        if run_type_m and run_type_m.group(1).upper() == "CELL_OPT":
            stress_m = re.search(
                r"^\s*STRESS_TENSOR\s+(\S+)", text, re.MULTILINE | re.IGNORECASE
            )
            valid_stress = {
                "ANALYTICAL",
                "NUMERICAL",
                "DIAGONAL_ANALYTICAL",
                "DIAGONAL_NUMERICAL",
            }
            if stress_m is None or stress_m.group(1).upper() not in valid_stress:
                line = find_line(text, r"^\s*RUN_TYPE\s+CELL_OPT")
                diags.append(
                    Diagnostic(
                        severity=SEVERITY_WARNING,
                        line=line,
                        param="STRESS_TENSOR",
                        message=(
                            "RUN_TYPE CELL_OPT requires stress tensor calculation, "
                            "but DFT/STRESS_TENSOR is not set to ANALYTICAL or NUMERICAL. "
                            "Cell optimization may fail or produce incorrect results."
                        ),
                        suggestion=(
                            "Add 'STRESS_TENSOR ANALYTICAL' inside the &DFT section."
                        ),
                    )
                )

        # Rule 4: RUN_TYPE BAND without &BAND_STRUCTURE section
        if run_type_m and run_type_m.group(1).upper() == "BAND":
            has_band_structure = bool(
                re.search(
                    r"^\s*&BAND_STRUCTURE\b", text, re.MULTILINE | re.IGNORECASE
                )
            )
            if not has_band_structure:
                line = find_line(text, r"^\s*RUN_TYPE\s+BAND")
                diags.append(
                    Diagnostic(
                        severity=SEVERITY_WARNING,
                        line=line,
                        param="RUN_TYPE",
                        message=(
                            "RUN_TYPE BAND requires a &PROPERTIES/&BAND_STRUCTURE "
                            "section with k-point path definition. "
                            "Without it, no band structure data will be written."
                        ),
                        suggestion=(
                            "Add a &BAND_STRUCTURE section inside &FORCE_EVAL/&PROPERTIES."
                        ),
                    )
                )

        return diags
