"""
LAMMPS input script validator.

LAMMPS uses a command-line style input (no nested sections). This validator
uses a lightweight regex-based parser since pymatgen.io.lammps only handles
the .data file format, not input scripts.

Custom rules:
  - units: must be one of the known unit styles
  - atom_style: must be one of the known styles
  - timestep: range check depending on units
  - pair_style + pair_coeff: warn if pair_style is set without pair_coeff
  - run or minimize: warn if neither is present
  - read_data / read_restart: info if found
"""

from __future__ import annotations

import re
from typing import Optional

from validators.base import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    BaseValidator,
    Diagnostic,
    find_line,
)

_KNOWN_UNITS = {
    "lj", "real", "metal", "si", "cgs", "electron", "micro", "nano",
}

_KNOWN_ATOM_STYLES = {
    "angle", "atomic", "body", "bond", "charge", "dipole", "dpd",
    "edpd", "electron", "ellipsoid", "full", "line", "mdpd", "molecular",
    "peri", "smd", "sphere", "spin", "template", "tri", "wavepacket",
    "hybrid",
}

# Typical timestep ranges (in native time units): (min_warn, max_warn)
# metal: ps; real: fs; si/cgs/micro/nano: s; electron: fs; lj: dimensionless
_TIMESTEP_RANGES: dict[str, tuple[float, float]] = {
    "metal": (1e-4, 0.1),      # ps
    "real": (0.1, 5.0),        # fs
    "lj": (1e-4, 0.05),        # dimensionless
    "si": (1e-16, 1e-11),      # s
    "cgs": (1e-16, 1e-11),     # s
    "electron": (0.001, 10.0), # fs
    "micro": (1e-6, 1.0),      # μs
    "nano": (1e-6, 1.0),       # ns
}


class LAMMPSValidator(BaseValidator):
    software_name = "lammps"

    def validate_text(
        self, text: str, source: str = "<string>"
    ) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        commands = _parse_lammps_commands(text)

        diags.extend(self._check_units(commands, text))
        diags.extend(self._check_atom_style(commands, text))
        diags.extend(self._check_timestep(commands, text))
        diags.extend(self._check_pair_style(commands, text))
        diags.extend(self._check_run_or_minimize(commands, text))
        diags.extend(self._check_read_data(commands, text))

        return diags

    # -----------------------------------------------------------------------
    # Individual checks
    # -----------------------------------------------------------------------

    def _check_units(
        self, commands: dict[str, list[str]], text: str
    ) -> list[Diagnostic]:
        if "units" not in commands:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="units",
                    message="'units' command not found.",
                    suggestion=(
                        "Add 'units metal' (for DFT-parameterised potentials) "
                        "or 'units real' (for molecular force fields)."
                    ),
                )
            ]
        val = commands["units"][0].lower() if commands["units"] else ""
        line = find_line(text, r"^\s*units\b")
        if val not in _KNOWN_UNITS:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="units",
                    message=f"Unknown units style: '{val}'.",
                    suggestion=f"Known styles: {', '.join(sorted(_KNOWN_UNITS))}",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="units",
                message=f"units = {val}",
            )
        ]

    def _check_atom_style(
        self, commands: dict[str, list[str]], text: str
    ) -> list[Diagnostic]:
        if "atom_style" not in commands:
            return []
        val = commands["atom_style"][0].lower() if commands["atom_style"] else ""
        line = find_line(text, r"^\s*atom_style\b")
        base = val.split()[0] if val else ""
        if base and base not in _KNOWN_ATOM_STYLES:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="atom_style",
                    message=f"Unrecognised atom_style: '{val}'.",
                    suggestion=f"Common styles: {', '.join(sorted(_KNOWN_ATOM_STYLES)[:8])} ...",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="atom_style",
                message=f"atom_style = {val}",
            )
        ]

    def _check_timestep(
        self, commands: dict[str, list[str]], text: str
    ) -> list[Diagnostic]:
        if "timestep" not in commands:
            return []
        args = commands["timestep"]
        if not args:
            return []
        try:
            val = float(args[0])
        except ValueError:
            return []

        line = find_line(text, r"^\s*timestep\b")
        units_style = "metal"  # default
        if "units" in commands and commands["units"]:
            units_style = commands["units"][0].lower()

        rng = _TIMESTEP_RANGES.get(units_style)
        if rng is None:
            return [
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=line,
                    param="timestep",
                    message=f"timestep = {val} (unit style '{units_style}' — range check not available)",
                )
            ]
        lo, hi = rng
        if val < lo:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="timestep",
                    message=f"timestep = {val} is very small for '{units_style}' units (typical min: {lo}).",
                )
            ]
        if val > hi:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="timestep",
                    message=f"timestep = {val} is large for '{units_style}' units (typical max: {hi}).",
                    suggestion="Large timesteps may cause instability. Test with smaller value first.",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="timestep",
                message=f"timestep = {val} ({units_style} units)",
            )
        ]

    def _check_pair_style(
        self, commands: dict[str, list[str]], text: str
    ) -> list[Diagnostic]:
        has_pair_style = "pair_style" in commands
        has_pair_coeff = "pair_coeff" in commands
        if has_pair_style and not has_pair_coeff:
            line = find_line(text, r"^\s*pair_style\b")
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="pair_style",
                    message="pair_style is set but no pair_coeff command found.",
                    suggestion="Add pair_coeff to define potential parameters.",
                )
            ]
        if has_pair_style:
            line = find_line(text, r"^\s*pair_style\b")
            style = commands["pair_style"][0] if commands["pair_style"] else ""
            return [
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=line,
                    param="pair_style",
                    message=f"pair_style = {style}",
                )
            ]
        return []

    def _check_run_or_minimize(
        self, commands: dict[str, list[str]], text: str
    ) -> list[Diagnostic]:
        has_run = "run" in commands
        has_min = "minimize" in commands
        if not has_run and not has_min:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="run/minimize",
                    message="Neither 'run' nor 'minimize' command found.",
                    suggestion="Add 'run <N>' or 'minimize' to actually execute the simulation.",
                )
            ]
        return []

    def _check_read_data(
        self, commands: dict[str, list[str]], text: str
    ) -> list[Diagnostic]:
        if "read_data" not in commands:
            return []
        args = commands["read_data"]
        path = args[0] if args else ""
        line = find_line(text, r"^\s*read_data\b")
        # Check for placeholder
        if "__" in path or path.startswith("<"):
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="read_data",
                    message=f"read_data path appears to be a placeholder: '{path}'.",
                    suggestion=(
                        "Replace the placeholder with the actual .data file path "
                        "(generated by prepare_lammps_job)."
                    ),
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="read_data",
                message=f"read_data = {path}",
            )
        ]


# ---------------------------------------------------------------------------
# LAMMPS command parser
# ---------------------------------------------------------------------------


def _parse_lammps_commands(text: str) -> dict[str, list[str]]:
    """Parse LAMMPS input into a dict mapping command → list of first-line args.

    Only the first occurrence of each command is stored (sufficient for
    checking presence and first argument value).
    Comments (#) and blank lines are skipped.
    Line continuation with '&' is handled.
    """
    commands: dict[str, list[str]] = {}
    # Join line continuations
    joined = re.sub(r"&\s*\n", " ", text)

    for raw_line in joined.splitlines():
        # Strip inline comment
        line = re.sub(r"#.*", "", raw_line).strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0].lower()
        args = parts[1:]
        if cmd not in commands:
            commands[cmd] = args

    return commands
