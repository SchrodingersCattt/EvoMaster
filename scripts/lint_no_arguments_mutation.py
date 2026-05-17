#!/usr/bin/env python3
"""Heuristic lint: detect mutation of ToolCallData.arguments.

This is a fast tripwire for the E3 arguments_json cache contract, not a full
AST proof. It only flags common mutation patterns on identifiers that normally
refer to ToolCallData instances.
"""

from __future__ import annotations

import re
import sys
import tokenize
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIRS = [REPO_ROOT / "matmaster", REPO_ROOT / "src"]

ALLOWLIST_PREFIXES = [
    "matmaster/providers/openai_provider.py",
]

_TC_IDENTIFIERS = r"(?:tc|tool_call|tool_calls\[\s*\d+\s*\])"

PATTERNS = [
    (
        rf"\b{_TC_IDENTIFIERS}\.arguments\s*=\s*[^=]",
        "rebind <tc>.arguments = ...",
    ),
    (
        rf"\b{_TC_IDENTIFIERS}\.arguments\[[^\]]+\]\s*=\s*",
        "subscript assign <tc>.arguments[k] = ...",
    ),
    (
        rf"\b{_TC_IDENTIFIERS}\.arguments\.(?:update|pop|clear|setdefault|popitem)\b",
        "mutate via <tc>.arguments.<method>(...)",
    ),
    (
        rf"\b{_TC_IDENTIFIERS}\.model_copy\([^)]*update\s*=\s*\{{[^}}]*['\"]arguments['\"]",
        "<tc>.model_copy(update={'arguments': ...}) carries stale cache",
    ),
]


def _is_allowlisted(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in ALLOWLIST_PREFIXES)


def check_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (line_no, pattern_label, line) violations for one Python file."""
    violations: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return violations

    source_lines = text.splitlines()
    code_lines = list(source_lines)
    try:
        tokens = tokenize.generate_tokens(StringIO(text).readline)
        for token in tokens:
            if token.type not in {tokenize.STRING, tokenize.COMMENT}:
                continue
            start_line, start_col = token.start
            end_line, end_col = token.end
            for line_no in range(start_line, end_line + 1):
                index = line_no - 1
                if index >= len(code_lines):
                    continue
                line = code_lines[index]
                left = start_col if line_no == start_line else 0
                right = end_col if line_no == end_line else len(line)
                code_lines[index] = line[:left] + (" " * (right - left)) + line[right:]
    except tokenize.TokenError:
        code_lines = source_lines

    for line_no, line in enumerate(code_lines, start=1):
        for regex, label in PATTERNS:
            if re.search(regex, line):
                violations.append((line_no, label, source_lines[line_no - 1].rstrip()))
    return violations


def main() -> int:
    all_violations: list[tuple[Path, int, str, str]] = []
    for base in SEARCH_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(REPO_ROOT))
            if _is_allowlisted(rel):
                continue
            for violation in check_file(path):
                all_violations.append((path, *violation))

    if all_violations:
        print("ToolCallData.arguments mutation detected (E3 R2 violation):")
        for path, line_no, label, line in all_violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:{line_no}: {label}")
            print(f"    {line}")
        print(
            "\nFix: construct a new ToolCallData or new dict instead of mutating "
            "in place. See matmaster/types/messages.py ToolCallData docstring."
        )
        return 1

    print("OK: no ToolCallData.arguments mutation found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
