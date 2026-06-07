#!/usr/bin/env python3
"""Heuristic lint: detect mutation of ToolCallData.arguments.

This is a fast AST tripwire for the E3 arguments_json cache contract. It flags
common mutation patterns on ToolCallData.arguments and on conventional runner
argument variable names.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIRS = [REPO_ROOT / "matmaster", REPO_ROOT / "src"]

ALLOWLIST_PREFIXES = [
    "matmaster/providers/transports/chat_completions.py",
]

PROTECTED_NAMES = {"arguments", "effective_args", "args"}
MUTATING_METHODS = {"update", "pop", "clear", "setdefault", "popitem"}


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
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return violations

    def line_for(node: ast.AST) -> str:
        line_no = getattr(node, "lineno", 1)
        if 1 <= line_no <= len(source_lines):
            return source_lines[line_no - 1].rstrip()
        return ""

    def add(node: ast.AST, label: str) -> None:
        violations.append((getattr(node, "lineno", 1), label, line_for(node)))

    def is_arguments_attr(node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "arguments"

    def protected_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name) and node.id in PROTECTED_NAMES:
            return node.id
        return None

    def model_copy_updates_arguments(node: ast.Call) -> bool:
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "model_copy":
            return False
        for keyword in node.keywords:
            if keyword.arg != "update":
                continue
            value = keyword.value
            if not isinstance(value, ast.Dict):
                continue
            for key in value.keys:
                if isinstance(key, ast.Constant) and key.value == "arguments":
                    return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if is_arguments_attr(target):
                    add(target, "rebind <obj>.arguments = ...")
                if isinstance(target, ast.Subscript):
                    owner = target.value
                    if is_arguments_attr(owner):
                        add(target, "subscript assign <obj>.arguments[k] = ...")
                    name = protected_name(owner)
                    if name is not None:
                        add(target, f"subscript assign {name}[k] = ...")

        if not isinstance(node, ast.Call):
            continue

        if model_copy_updates_arguments(node):
            add(node, "model_copy(update={'arguments': ...}) carries stale cache")
            continue

        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in MUTATING_METHODS:
            continue
        owner = func.value
        if is_arguments_attr(owner):
            add(node, f"mutate via <obj>.arguments.{func.attr}(...)")
        name = protected_name(owner)
        if name is not None:
            add(node, f"mutate via {name}.<method>(...)")

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
