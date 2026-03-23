"""
Base classes for input file validators.

Defines:
  - Diagnostic: structured diagnostic message (error/warning/info)
  - BaseValidator: abstract base for per-software validators
  - ValidatorRegistry: maps software name → validator instance
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------


@dataclass
class Diagnostic:
    """A single diagnostic message produced by a validator.

    Attributes:
        severity:   "error" | "warning" | "info"
        line:       1-based line number, or 0 if not applicable
        param:      parameter / keyword name, or empty string
        message:    human-readable description of the issue
        suggestion: optional remediation hint
    """

    severity: str
    message: str
    line: int = 0
    param: str = ""
    suggestion: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "severity": self.severity,
            "line": self.line,
            "param": self.param,
            "message": self.message,
        }
        if self.suggestion is not None:
            d["suggestion"] = self.suggestion
        return d

    def to_human(self) -> str:
        icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(self.severity, "?")
        loc = f" line {self.line}" if self.line else ""
        param = f" [{self.param}]" if self.param else ""
        hint = f" → {self.suggestion}" if self.suggestion else ""
        return f"{icon}{loc}{param}: {self.message}{hint}"


# ---------------------------------------------------------------------------
# BaseValidator
# ---------------------------------------------------------------------------


class BaseValidator(ABC):
    """Abstract base class for per-software input file validators.

    Subclasses must implement :meth:`validate_text`.
    """

    #: Short software name used as registry key (e.g. "cp2k", "orca").
    software_name: str = ""

    def validate_file(self, path: Path) -> list[Diagnostic]:
        """Validate a file on disk.

        Reads *path*, then delegates to :meth:`validate_text`.
        Returns a list that starts with an error Diagnostic if the file
        cannot be read.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="",
                    message=f"Cannot read file: {exc}",
                )
            ]
        return self.validate_text(text, source=str(path))

    @abstractmethod
    def validate_text(
        self, text: str, source: str = "<string>"
    ) -> list[Diagnostic]:
        """Validate input given as a string.

        Args:
            text:   Full content of the input file.
            source: Optional label used in messages (file path or '<string>').

        Returns:
            List of :class:`Diagnostic` objects (may be empty on clean input).
        """


# ---------------------------------------------------------------------------
# Helper: find line number of a token in text
# ---------------------------------------------------------------------------


def find_line(text: str, pattern: str, flags: int = re.IGNORECASE) -> int:
    """Return the 1-based line number of the first occurrence of *pattern*.

    Returns 0 if not found.
    """
    try:
        m = re.search(pattern, text, flags)
    except re.error:
        return 0
    if m is None:
        return 0
    return text[: m.start()].count("\n") + 1


# ---------------------------------------------------------------------------
# ValidatorRegistry
# ---------------------------------------------------------------------------


class ValidatorRegistry:
    """Registry that maps software names to validator instances.

    Validators are registered lazily: the first call to
    :meth:`get_validator` for a given software name will import and
    instantiate the corresponding validator class.
    """

    # Map: lowercase software name → (module_path, class_name)
    _REGISTRY: dict[str, tuple[str, str]] = {
        "cp2k": ("validators.cp2k_validator", "CP2KValidator"),
        "orca": ("validators.orca_validator", "ORCAValidator"),
        "qe": ("validators.qe_validator", "QEValidator"),
        "abinit": ("validators.abinit_validator", "ABINITValidator"),
        "lammps": ("validators.lammps_validator", "LAMMPSValidator"),
    }

    # Canonical alias map (e.g. user may type "quantum espresso")
    _ALIASES: dict[str, str] = {
        "quantum espresso": "qe",
        "quantum_espresso": "qe",
        "espresso": "qe",
        "pwscf": "qe",
        "pw": "qe",
        "pw.x": "qe",
        "cp2k": "cp2k",
        "orca": "orca",
        "abinit": "abinit",
        "lammps": "lammps",
    }

    def __init__(self) -> None:
        self._cache: dict[str, BaseValidator] = {}

    def _normalize(self, software: str) -> str:
        key = software.strip().lower()
        return self._ALIASES.get(key, key)

    def get_validator(self, software: str) -> Optional[BaseValidator]:
        """Return the validator for *software*, or None if unsupported.

        The validator module is imported on first use.
        """
        key = self._normalize(software)
        if key in self._cache:
            return self._cache[key]

        if key not in self._REGISTRY:
            return None

        module_path, class_name = self._REGISTRY[key]
        try:
            import importlib

            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            instance: BaseValidator = cls()
        except Exception as exc:  # noqa: BLE001
            # Return a stub validator that reports the import error
            return _ImportErrorValidator(software_name=key, error=str(exc))

        self._cache[key] = instance
        return instance

    @property
    def supported_software(self) -> list[str]:
        """Return sorted list of supported software names."""
        return sorted(self._REGISTRY.keys())


# ---------------------------------------------------------------------------
# Fallback: validator that reports an import error
# ---------------------------------------------------------------------------


class _ImportErrorValidator(BaseValidator):
    def __init__(self, software_name: str, error: str) -> None:
        self.software_name = software_name
        self._error = error

    def validate_text(
        self, text: str, source: str = "<string>"
    ) -> list[Diagnostic]:
        return [
            Diagnostic(
                severity=SEVERITY_ERROR,
                line=0,
                param="",
                message=(
                    f"Validator for '{self.software_name}' could not be loaded: "
                    f"{self._error}"
                ),
                suggestion=(
                    "Check that required dependencies are installed "
                    "(see pyproject.toml calculation extras)."
                ),
            )
        ]
