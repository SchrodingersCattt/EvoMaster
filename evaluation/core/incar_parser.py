"""VASP INCAR parser and comparator for evaluation.

Parses INCAR text files into normalized dicts and compares agent-generated
INCARs against reference INCARs from the question bank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Tag classification — used for type-aware normalization & comparison
# ---------------------------------------------------------------------------

_INT_TAGS: set[str] = {
    "NSW", "IBRION", "ISPIN", "ISMEAR", "ISIF", "NFREE", "ICHARG", "LORBIT",
    "ISYM", "NELM", "NELMIN", "IMAGES", "LDAUTYPE", "LMAXMIX", "MDALGO",
    "NCORE", "KPAR", "NWRITE", "NPAR", "NSIM", "IDIPOL", "NBANDS", "NEDOS",
    "ISTART", "NUPDOWN", "IVDW", "LMAXTAU", "MAXMIX", "IOPT", "NELMDL",
    "NBLOCK", "IALGO", "INIWAV", "LDAUPRINT", "IMIX",
}

_FLOAT_TAGS: set[str] = {
    "EDIFF", "EDIFFG", "SIGMA", "ENCUT", "ENAUG", "HFSCREEN", "POTIM",
    "AEXX", "AMIX", "BMIX", "AMIX_MAG", "BMIX_MAG", "SMASS", "TEBEG",
    "TEEND", "EMAX", "EMIN", "PSTRESS", "SYMPREC", "VDW_S8", "VDW_A1",
    "VDW_A2", "Zab_vdW", "AGGAC", "SPRING", "MAXMOVE", "TIME", "EPSILON",
}

_BOOL_TAGS: set[str] = {
    "LDAU", "LHFCALC", "LSORBIT", "LASPH", "LREAL", "LWAVE", "LCHARG",
    "LVTOT", "LVHAR", "LELF", "LAECHG", "LPLANE", "LSCALU", "LNONCOLLINEAR",
    "LEPSILON", "LPEAD", "ADDGRID", "LDIPOL", "LCLIMB", "LTANGENTOLD",
    "LDNEB", "LUSE_VDW", "GGA_COMPAT",
}

_ARRAY_TAGS: set[str] = {
    "LDAUL", "LDAUU", "LDAUJ", "MAGMOM", "SAXIS",
}

_STRING_TAGS: set[str] = {
    "ALGO", "PREC", "GGA", "METAGGA", "SYSTEM",
}

# ---------------------------------------------------------------------------
# Alias mappings for string-valued tags
# ---------------------------------------------------------------------------

_ALGO_ALIASES: dict[str, str] = {
    "A": "ALL", "ALL": "ALL",
    "D": "DAMPED", "DAMPED": "DAMPED",
    "N": "NORMAL", "NORMAL": "NORMAL",
    "F": "FAST", "FAST": "FAST",
    "CONJUGATE": "CONJUGATE",
    "SUBROT": "SUBROT",
}

_PREC_ALIASES: dict[str, str] = {
    "ACC": "ACCURATE", "ACCURATE": "ACCURATE",
    "H": "HIGH", "HIGH": "HIGH",
    "M": "MEDIUM", "MEDIUM": "MEDIUM",
    "L": "LOW", "LOW": "LOW",
    "N": "NORMAL", "NORMAL": "NORMAL",
    "SINGLE": "SINGLE",
}


# ---------------------------------------------------------------------------
# INCAR parsing
# ---------------------------------------------------------------------------

def parse_incar(text: str) -> dict[str, str]:
    """Parse VASP INCAR text into ``{TAG: raw_value_str}``."""
    result: dict[str, str] = {}
    last_tag: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Skip blank & pure-comment lines
        if not line or line.startswith("#") or line.startswith("!"):
            continue

        # Strip inline comments:  VALUE # comment  or  VALUE ! comment
        # Be careful: `#` inside a value like SYSTEM is rare, but `!` is VASP
        # standard inline-comment delimiter.
        for delim in ("!", "#"):
            idx = line.find(delim)
            if idx > 0:
                line = line[:idx].strip()

        if not line:
            continue

        if "=" in line:
            tag, _, val = line.partition("=")
            tag = tag.strip().upper()
            val = val.strip()
            # Special case: Zab_vdW keeps mixed case internally but we
            # uppercase for dict keys
            result[tag] = val
            last_tag = tag
        elif last_tag is not None:
            # Continuation line (no '=')
            result[last_tag] += " " + line

    return result


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

_BOOL_TRUE = {".TRUE.", ".TRUE", "T", "TRUE"}
_BOOL_FALSE = {".FALSE.", ".FALSE", "F", "FALSE"}


def _expand_repeat(token: str) -> list[str]:
    """Expand ``N*VAL`` repeat notation into a list of N copies of VAL."""
    m = re.match(r"^(\d+)\*(.+)$", token)
    if m:
        return [m.group(2)] * int(m.group(1))
    return [token]


def _try_float(s: str) -> float | None:
    try:
        return float(s)
    except (ValueError, OverflowError):
        return None


def _try_int(s: str) -> int | None:
    try:
        return int(s)
    except ValueError:
        # Handle "520.000000" → 520 for known int tags
        f = _try_float(s)
        if f is not None and f == int(f):
            return int(f)
        return None


def normalize_value(tag: str, raw: str) -> Any:
    """Normalize a raw VASP parameter value based on tag type."""
    tag_upper = tag.upper()
    raw = raw.strip()

    if not raw:
        return raw

    # ---- Bool ----
    if tag_upper in _BOOL_TAGS:
        if raw.upper() in _BOOL_TRUE:
            return True
        if raw.upper() in _BOOL_FALSE:
            return False
        # Fallthrough: leave as string

    # ---- LREAL special case (can be bool OR string "Auto") ----
    if tag_upper == "LREAL":
        up = raw.upper()
        if up in _BOOL_TRUE:
            return True
        if up in _BOOL_FALSE:
            return False
        return up  # "AUTO" etc.

    # ---- Int ----
    if tag_upper in _INT_TAGS:
        v = _try_int(raw)
        if v is not None:
            return v

    # ---- Float ----
    if tag_upper in _FLOAT_TAGS:
        v = _try_float(raw)
        if v is not None:
            return v

    # ---- Array ----
    if tag_upper in _ARRAY_TAGS:
        tokens: list[str] = []
        for part in raw.split():
            tokens.extend(_expand_repeat(part))
        # Try to convert each element to float (or int for LDAUL)
        result: list[float | int] = []
        for t in tokens:
            f = _try_float(t)
            if f is not None:
                if tag_upper == "LDAUL" and f == int(f):
                    result.append(int(f))
                else:
                    result.append(f)
            else:
                # Can't convert — return raw string list
                return tokens
        return result

    # ---- String with aliases ----
    if tag_upper == "ALGO":
        canonical = _ALGO_ALIASES.get(raw.upper())
        return canonical if canonical else raw.upper()
    if tag_upper == "PREC":
        canonical = _PREC_ALIASES.get(raw.upper())
        return canonical if canonical else raw.upper()
    if tag_upper in _STRING_TAGS:
        return raw.upper()

    # ---- Fallback: try numeric, else string ----
    v = _try_float(raw)
    if v is not None:
        if v == int(v) and "." not in raw and "e" not in raw.lower():
            return int(v)
        return v

    return raw


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

@dataclass
class IncarCompareResult:
    """Result of comparing agent INCAR against reference."""

    matched: list[str] = field(default_factory=list)
    mismatched: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    key_matched: list[str] = field(default_factory=list)
    key_mismatched: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    key_missing: list[str] = field(default_factory=list)

    @property
    def key_score(self) -> float:
        """Fraction of key tags that matched (0.0 – 1.0)."""
        total = len(self.key_matched) + len(self.key_mismatched) + len(self.key_missing)
        return len(self.key_matched) / total if total else 1.0

    @property
    def full_score(self) -> float:
        """Fraction of all reference tags that matched (0.0 – 1.0)."""
        total = len(self.matched) + len(self.mismatched) + len(self.missing)
        return len(self.matched) / total if total else 1.0

    def summary(self) -> str:
        """Human-readable markdown summary for score_reason."""
        lines: list[str] = []
        total_ref = len(self.matched) + len(self.mismatched) + len(self.missing)
        lines.append(
            f"**INCAR 参数对比**: {len(self.matched)}/{total_ref} 匹配 "
            f"(full_score={self.full_score:.0%})"
        )
        if self.key_matched or self.key_mismatched or self.key_missing:
            total_key = (
                len(self.key_matched)
                + len(self.key_mismatched)
                + len(self.key_missing)
            )
            lines.append(
                f"**核心参数**: {len(self.key_matched)}/{total_key} 匹配 "
                f"(key_score={self.key_score:.0%})"
            )

        if self.key_mismatched:
            lines.append("\n**核心参数不匹配**:")
            for tag, (ref_v, agent_v) in sorted(self.key_mismatched.items()):
                lines.append(f"- **{tag}**: ref={ref_v}, agent={agent_v}")

        if self.key_missing:
            lines.append("\n**核心参数缺失**:")
            for tag in sorted(self.key_missing):
                lines.append(f"- **{tag}**: agent 未设置")

        if self.mismatched:
            lines.append("\n**其他不匹配**:")
            for tag, (ref_v, agent_v) in sorted(self.mismatched.items()):
                lines.append(f"- {tag}: ref={ref_v}, agent={agent_v}")

        if self.missing:
            non_key_missing = [t for t in self.missing if t not in self.key_missing]
            if non_key_missing:
                lines.append(
                    f"\n**其他缺失** ({len(non_key_missing)}): "
                    + ", ".join(sorted(non_key_missing))
                )

        return "\n".join(lines)


def _values_match(
    tag: str, ref_val: Any, agent_val: Any, float_tol: float
) -> bool:
    """Check if two normalized values match for a given tag."""
    # Same type, same value — easy case
    if ref_val == agent_val:
        return True

    # Bool
    if isinstance(ref_val, bool) or isinstance(agent_val, bool):
        return ref_val == agent_val

    # Both numeric (int or float)
    if isinstance(ref_val, (int, float)) and isinstance(agent_val, (int, float)):
        tag_upper = tag.upper()
        if tag_upper in _INT_TAGS:
            return int(ref_val) == int(agent_val)
        # Float tolerance
        denom = max(abs(ref_val), 1e-12)
        return abs(ref_val - agent_val) / denom <= float_tol

    # Both lists (arrays)
    if isinstance(ref_val, list) and isinstance(agent_val, list):
        if len(ref_val) != len(agent_val):
            return False
        for rv, av in zip(ref_val, agent_val):
            if isinstance(rv, (int, float)) and isinstance(av, (int, float)):
                if tag.upper() == "LDAUL":
                    if int(rv) != int(av):
                        return False
                else:
                    denom = max(abs(rv), 1e-12)
                    if abs(rv - av) / denom > float_tol:
                        return False
            elif rv != av:
                return False
        return True

    # Both strings
    if isinstance(ref_val, str) and isinstance(agent_val, str):
        return ref_val.upper() == agent_val.upper()

    # Type mismatch — try string comparison
    return str(ref_val).upper() == str(agent_val).upper()


def compare_incar(
    ref: dict[str, str],
    agent: dict[str, str],
    *,
    key_tags: list[str] | None = None,
    float_tol: float = 0.15,
) -> IncarCompareResult:
    """Compare agent INCAR against reference.

    Parameters
    ----------
    ref, agent:
        Raw parsed dicts from :func:`parse_incar`.
    key_tags:
        Core parameters to track separately (uppercase).
    float_tol:
        Relative tolerance for float comparisons (default 15%).
    """
    key_set = {t.upper() for t in (key_tags or [])}
    result = IncarCompareResult()

    # Normalize both
    ref_norm = {tag: normalize_value(tag, val) for tag, val in ref.items()}
    agent_norm = {tag: normalize_value(tag, val) for tag, val in agent.items()}

    # Compare each ref tag
    for tag in ref_norm:
        if tag in agent_norm:
            if _values_match(tag, ref_norm[tag], agent_norm[tag], float_tol):
                result.matched.append(tag)
                if tag in key_set:
                    result.key_matched.append(tag)
            else:
                result.mismatched[tag] = (ref_norm[tag], agent_norm[tag])
                if tag in key_set:
                    result.key_mismatched[tag] = (ref_norm[tag], agent_norm[tag])
        else:
            result.missing.append(tag)
            if tag in key_set:
                result.key_missing.append(tag)

    # Extra tags in agent but not ref
    for tag in agent_norm:
        if tag not in ref_norm:
            result.extra.append(tag)

    return result


# ---------------------------------------------------------------------------
# Extract INCAR from agent text output (final_content or file)
# ---------------------------------------------------------------------------

# Common VASP tags used to score candidate code blocks
_INCAR_INDICATOR_TAGS = {
    "ENCUT", "PREC", "NSW", "EDIFF", "IBRION", "ISPIN", "ISMEAR", "ALGO",
    "SIGMA", "LORBIT", "ISIF", "LREAL", "LWAVE", "LCHARG", "ICHARG",
    "LDAU", "LHFCALC", "LSORBIT", "METAGGA", "POTIM",
}


def extract_incar_from_text(text: str) -> str | None:
    """Extract INCAR content from agent markdown / plain-text output.

    Looks for fenced code blocks first; falls back to extracting lines
    that match ``TAG = VALUE`` patterns.
    """
    # Strategy 1: fenced code blocks (```)
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    if blocks:
        scored: list[tuple[int, str]] = []
        for block in blocks:
            score = sum(
                1
                for tag in _INCAR_INDICATOR_TAGS
                if re.search(rf"\b{tag}\b", block, re.IGNORECASE)
            )
            if score >= 3:  # at least 3 recognizable VASP tags
                scored.append((score, block))
        if scored:
            scored.sort(key=lambda x: -x[0])
            return scored[0][1].strip()

    # Strategy 2: extract KEY = VALUE lines directly
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^[A-Z_][A-Z_0-9]*\s*=", stripped, re.IGNORECASE):
            lines.append(stripped)
        elif stripped.startswith("#") and "=" in stripped:
            # Commented-out VASP tag — skip
            continue

    if len(lines) >= 3:
        return "\n".join(lines)

    return None
