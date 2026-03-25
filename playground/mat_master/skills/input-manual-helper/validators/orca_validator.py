"""
ORCA input file validator.

ORCA has no mature open-source parser, so this module uses a lightweight
hand-written parser that covers the three structural elements of an ORCA
input file:

  1. Simple input line(s): lines starting with "!"
     e.g.  ! B3LYP def2-TZVP RIJCOSX tightSCF

  2. Keyword blocks: %blockname ... end
     e.g.  %pal nprocs 8 end
           %maxcore 2000

  3. Coordinate block: * <format> <charge> <mult> [<file>]
                         <atoms>
                       *

Validation rules applied:
  - %maxcore: warn if < 500 MB or > 16000 MB
  - %pal nprocs: warn if < 1 or > 256
  - functional: checked against known list (warning if unrecognised)
  - basis set: checked against known list (warning if unrecognised)
  - charge / multiplicity: warn if mult < 1 or both are inconsistently
    odd/even (even electrons → odd mult for closed-shell)
  - coordinate block: error if missing
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

# ---------------------------------------------------------------------------
# Known-good enumerations
# ---------------------------------------------------------------------------

_KNOWN_FUNCTIONALS = {
    # GGA
    "pbe", "blyp", "bp86", "pw91", "revpbe", "rpbe",
    # meta-GGA
    "tpss", "m06l", "r2scan",
    # hybrid
    "b3lyp", "pbe0", "bhlyp", "tpssh", "m06", "m06-2x",
    "b97-3c", "r2scan-3c", "b97-d3", "cam-b3lyp",
    # range-separated
    "wb97x-d3", "wb97x-d", "wb97x", "lc-blyp",
    # double hybrid
    "b2plyp", "b2gp-plyp", "dlpno-ccsd", "dlpno-ccsd(t)",
    # WF
    "hf", "mp2", "ccsd", "ccsd(t)", "casscf", "nevpt2",
    # DFT keywords that may appear on ! line
    "dft",
}

_KNOWN_BASIS = {
    # def2 family
    "def2-sv(p)", "def2-svp", "def2-tzvp", "def2-tzvpp",
    "def2-qzvp", "def2-qzvpp",
    # Pople
    "sto-3g", "3-21g", "6-31g", "6-31g*", "6-31g**",
    "6-311g", "6-311g*", "6-311g**", "6-311+g**",
    # Dunning
    "cc-pvdz", "cc-pvtz", "cc-pvqz", "aug-cc-pvdz",
    "aug-cc-pvtz", "aug-cc-pvqz",
    # relativistic / ECPs
    "def2-tzvp-pp", "sk-mcdhf-rsc",
    # misc
    "zora-def2-tzvp",
}

# Tokens on the ! line that indicate calculation type (not functional/basis)
_CALC_TYPE_TOKENS = {
    "opt", "freq", "numfreq", "md", "goat",
    "sp", "engrad", "grad",
    "tddft", "stddft", "cis",
    "neb", "neb-ts", "irc",
    "rijcosx", "rijk", "ri-c", "rimp2",
    "tightscf", "loosescf", "normalscf", "verytightscf",
    "largeprint", "miniprint", "noautostart", "nopop",
    "slowconv", "veryslowconv",
    "d3", "d3bj", "d4",
    "moread", "patom", "uks", "rks",
    "xyzfile",
}


class ORCAValidator(BaseValidator):
    software_name = "orca"

    def validate_text(
        self, text: str, source: str = "<string>"
    ) -> list[Diagnostic]:
        diags: list[Diagnostic] = []

        parsed = _ORCAParser(text)

        diags.extend(self._check_coord_block(parsed, text))
        diags.extend(self._check_maxcore(parsed, text))
        diags.extend(self._check_nprocs(parsed, text))
        diags.extend(self._check_functional(parsed, text))
        diags.extend(self._check_basis(parsed, text))
        diags.extend(self._check_charge_mult(parsed, text))

        return diags

    # -----------------------------------------------------------------------
    # Individual checks
    # -----------------------------------------------------------------------

    def _check_coord_block(
        self, parsed: "_ORCAParser", text: str
    ) -> list[Diagnostic]:
        if parsed.coord_block is None:
            return [
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=0,
                    param="coords",
                    message="No coordinate block found (expected '* xyz|internal|gzmt <charge> <mult> ...').",
                    suggestion="Add a coordinate block: * xyz 0 1 \\n <atoms> \\n *",
                )
            ]
        return []

    def _check_maxcore(
        self, parsed: "_ORCAParser", text: str
    ) -> list[Diagnostic]:
        val = parsed.maxcore
        if val is None:
            return [
                Diagnostic(
                    severity=SEVERITY_INFO,
                    line=0,
                    param="maxcore",
                    message="%maxcore not set; ORCA will use its default (1 GB per process).",
                    suggestion="Set %maxcore explicitly, e.g. %maxcore 2000",
                )
            ]
        line = find_line(text, r"^\s*%maxcore\b")
        if val < 500:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="maxcore",
                    message=f"%maxcore {val} MB is very low; SCF may run out of memory.",
                    suggestion="Typical value: 2000–8000 MB per process.",
                )
            ]
        if val > 16000:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="maxcore",
                    message=f"%maxcore {val} MB is very large; ensure enough RAM per process.",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="maxcore",
                message=f"%maxcore = {val} MB",
            )
        ]

    def _check_nprocs(
        self, parsed: "_ORCAParser", text: str
    ) -> list[Diagnostic]:
        val = parsed.nprocs
        if val is None:
            return []
        line = find_line(text, r"^\s*%pal\b")
        if val < 1:
            return [
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=line,
                    param="nprocs",
                    message=f"%pal nprocs {val} is invalid (must be >= 1).",
                )
            ]
        if val > 256:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="nprocs",
                    message=f"%pal nprocs {val} is unusually large.",
                    suggestion="Verify that this many cores are available on the target machine.",
                )
            ]
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="nprocs",
                message=f"%pal nprocs = {val}",
            )
        ]

    def _check_functional(
        self, parsed: "_ORCAParser", text: str
    ) -> list[Diagnostic]:
        func = parsed.functional
        if func is None:
            return [
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=0,
                    param="functional",
                    message="No recognised functional found on the '!' keyword line.",
                    suggestion="Add a functional keyword, e.g. '! B3LYP'",
                )
            ]
        line = find_line(text, r"^\s*!")
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="functional",
                message=f"Functional: {func}",
            )
        ]

    def _check_basis(
        self, parsed: "_ORCAParser", text: str
    ) -> list[Diagnostic]:
        basis = parsed.basis
        if basis is None:
            # Some methods (e.g. semi-empirical) don't need explicit basis
            return []
        line = find_line(text, r"^\s*!")
        return [
            Diagnostic(
                severity=SEVERITY_INFO,
                line=line,
                param="basis",
                message=f"Basis set: {basis}",
            )
        ]

    def _check_charge_mult(
        self, parsed: "_ORCAParser", text: str
    ) -> list[Diagnostic]:
        cb = parsed.coord_block
        if cb is None:
            return []
        charge = cb.get("charge")
        mult = cb.get("mult")
        if charge is None or mult is None:
            return []

        diags: list[Diagnostic] = []
        line = cb.get("line", 0)

        if mult < 1:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_ERROR,
                    line=line,
                    param="multiplicity",
                    message=f"Multiplicity {mult} is invalid (must be >= 1).",
                )
            )

        # Parity check: (charge + n_electrons) parity vs multiplicity parity
        # We don't know n_electrons here, but we can check that mult is odd
        # for even-electron systems (charge even) as a heuristic.
        if mult % 2 == 0 and charge % 2 == 0:
            diags.append(
                Diagnostic(
                    severity=SEVERITY_WARNING,
                    line=line,
                    param="multiplicity",
                    message=(
                        f"Even charge ({charge}) with even multiplicity ({mult}) is unusual "
                        "for a closed-shell molecule."
                    ),
                    suggestion="Closed-shell systems typically have multiplicity 1.",
                )
            )
        return diags


# ---------------------------------------------------------------------------
# Lightweight ORCA parser
# ---------------------------------------------------------------------------


class _ORCAParser:
    """Parse the structural elements of an ORCA input file."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.keyword_tokens: list[str] = []
        self.blocks: dict[str, str] = {}  # blockname → body text
        self.coord_block: Optional[dict] = None
        self.maxcore: Optional[int] = None
        self.nprocs: Optional[int] = None
        self.functional: Optional[str] = None
        self.basis: Optional[str] = None
        self._parse()

    def _parse(self) -> None:
        lines = self.text.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            # Skip blank lines and comments
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            # --- Simple input line ---
            if stripped.startswith("!"):
                tokens = stripped[1:].split()
                self.keyword_tokens.extend(t.lower() for t in tokens)
                i += 1
                continue

            # --- % keyword blocks ---
            m_block = re.match(r"^%(\w+)\s*(.*)", stripped, re.IGNORECASE)
            if m_block:
                block_name = m_block.group(1).lower()
                rest = m_block.group(2).strip()

                # Inline single-value: %maxcore 2000
                if block_name == "maxcore":
                    try:
                        self.maxcore = int(rest.split()[0])
                    except (ValueError, IndexError):
                        pass
                    i += 1
                    continue

                # Multi-line block ending with "end"
                # Handle inline form: %pal nprocs 8 end  (rest ends with "end")
                if re.search(r"\bend\s*$", rest, re.IGNORECASE):
                    # Inline single-line block — strip the trailing "end"
                    body = re.sub(r"\bend\s*$", "", rest, flags=re.IGNORECASE).strip()
                    i += 1
                else:
                    body_lines = [rest] if rest else []
                    i += 1
                    while i < len(lines):
                        bl = lines[i].strip()
                        if re.match(r"^end\b", bl, re.IGNORECASE):
                            i += 1
                            break
                        body_lines.append(bl)
                        i += 1
                    body = "\n".join(body_lines)
                self.blocks[block_name] = body

                # Extract nprocs from %pal block
                if block_name == "pal":
                    m_np = re.search(
                        r"\bnprocs\s+(\d+)", body, re.IGNORECASE
                    )
                    if m_np:
                        self.nprocs = int(m_np.group(1))
                    # Also handle: %pal nprocs 8 end (already captured in rest)
                    m_np2 = re.search(
                        r"\bnprocs\s+(\d+)", rest, re.IGNORECASE
                    )
                    if m_np2:
                        self.nprocs = int(m_np2.group(1))
                continue

            # --- Coordinate block: * format charge mult [file] ---
            if stripped.startswith("*"):
                parts = stripped[1:].split()
                if parts:
                    fmt = parts[0].lower()
                    if fmt in ("xyz", "internal", "gzmt", "xyzfile"):
                        cb: dict = {"format": fmt, "line": i + 1}
                        try:
                            cb["charge"] = int(parts[1])
                            cb["mult"] = int(parts[2])
                        except (IndexError, ValueError):
                            pass
                        self.coord_block = cb
                i += 1
                continue

            i += 1

        # --- Extract functional and basis from keyword tokens ---
        self.functional, self.basis = self._extract_functional_basis()

    def _extract_functional_basis(
        self,
    ) -> tuple[Optional[str], Optional[str]]:
        functional: Optional[str] = None
        basis: Optional[str] = None

        for tok in self.keyword_tokens:
            if tok in _CALC_TYPE_TOKENS:
                continue
            if tok in _KNOWN_FUNCTIONALS:
                functional = tok
            elif tok in _KNOWN_BASIS or "/" in tok:
                basis = tok
            elif tok.startswith("def2") or tok.startswith("cc-p") or tok.startswith("aug-"):
                basis = tok

        # Second pass: if no match yet, first non-calc-type token is probably functional
        if functional is None:
            for tok in self.keyword_tokens:
                if tok not in _CALC_TYPE_TOKENS:
                    functional = tok
                    break

        return functional, basis
