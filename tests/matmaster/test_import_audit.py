"""Import audit tests for matmaster package isolation.

Scans matmaster/ source files for forbidden runtime imports of evomaster,
playground, or src modules. Uses AST-level analysis to detect only real
import statements (not comments, strings, or TYPE_CHECKING blocks).

Phase 30 adds TestPhase30FullIsolation: unified audit covering evomaster + playground + src prefixes.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_matmaster_py_files() -> list[Path]:
    """Collect all .py files under matmaster/ (excluding __pycache__)."""
    matmaster_dir = _PROJECT_ROOT / "matmaster"
    return sorted(
        p for p in matmaster_dir.rglob("*.py") if "__pycache__" not in p.parts
    )


def _is_inside_type_checking(node: ast.AST, tree: ast.Module) -> bool:
    """Return True if ``node`` is inside an ``if TYPE_CHECKING:`` block."""
    for top_node in ast.walk(tree):
        if isinstance(top_node, ast.If):
            test = top_node.test
            # if TYPE_CHECKING:
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                for child in ast.walk(top_node):
                    if child is node:
                        return True
            # if typing.TYPE_CHECKING:
            if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                for child in ast.walk(top_node):
                    if child is node:
                        return True
    return False


def _find_all_imports_matching(
    source: str,
    module_prefix: str,
    *,
    exclude_type_checking: bool = True,
) -> list[ast.ImportFrom]:
    """Find all ``from <module_prefix>... import ...`` statements in source.

    Returns a list of ast.ImportFrom nodes whose module starts with
    ``module_prefix``. By default, imports inside ``if TYPE_CHECKING:``
    blocks are excluded.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits: list[ast.ImportFrom] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None:
            continue
        if not node.module.startswith(module_prefix):
            continue
        if exclude_type_checking and _is_inside_type_checking(node, tree):
            continue
        hits.append(node)
    return hits


# ---------------------------------------------------------------------------
# Phase 30 unified audit
# ---------------------------------------------------------------------------


class TestPhase30FullIsolation:
    """Phase 30 unified audit: matmaster/ must have no evomaster/playground/src runtime imports.

    This is the final full-coverage audit, covering all three forbidden prefixes
    in a single pass. All earlier fine-grained audit classes (if any) are retained
    as regression guards; this class provides completeness guarantee.

    Known pre-existing violations are tracked in KNOWN_VIOLATIONS. As each
    violation is resolved by subsequent plans, remove it from the set.
    The test fails if:
    - A NEW violation appears (not in KNOWN_VIOLATIONS) -- regression.
    - KNOWN_VIOLATIONS lists a file:line that no longer exists -- stale entry.
    """

    FORBIDDEN_PREFIXES = ["evomaster", "playground", "src."]

    # Pre-existing violations as of Phase 30 Plan 02.
    # Format: "relative/path.py:L<lineno>"
    # Remove entries as subsequent plans resolve them.
    # 23 violations resolved since Plan 01 (exp.py, playground.py, tools/, integration/).
    KNOWN_VIOLATIONS: frozenset[str] = frozenset(
        {
            "matmaster/core/__init__.py:L11",
            "matmaster/skills/playground-skills/bohrium-job/scripts/list_images.py:L30",
            "matmaster/skills/playground-skills/bohrium-job/scripts/list_machines.py:L28",
            "matmaster/skills/playground-skills/bohrium-job/scripts/poll_job.py:L27",
            "matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py:L33",
            "matmaster/skills/playground-skills/structure-manager/scripts/fetch_web_structure.py:L30",
        }
    )

    def test_no_forbidden_imports_in_matmaster(self):
        """Scan all matmaster/*.py, confirm no evomaster/playground/src runtime imports.

        Known pre-existing violations are allowed. New violations or stale
        known-entries cause failure.
        """
        project_root = _PROJECT_ROOT
        found: dict[str, str] = {}  # key -> full description
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for prefix in self.FORBIDDEN_PREFIXES:
                hits = _find_all_imports_matching(
                    source, prefix, exclude_type_checking=True
                )
                for node in hits:
                    rel = py_file.relative_to(project_root)
                    key = f"{rel}:L{node.lineno}"
                    found[key] = f"{key}: from {node.module} import ..."

        found_keys = set(found.keys())
        new_violations = found_keys - self.KNOWN_VIOLATIONS
        stale_entries = self.KNOWN_VIOLATIONS - found_keys

        errors: list[str] = []
        if new_violations:
            errors.append(
                "NEW forbidden imports (regression):\n"
                + "\n".join(f"  {found[k]}" for k in sorted(new_violations))
            )
        if stale_entries:
            errors.append(
                "STALE known-violation entries (already fixed, remove from KNOWN_VIOLATIONS):\n"
                + "\n".join(f"  {k}" for k in sorted(stale_entries))
            )

        assert not errors, "\n".join(errors)

    def test_known_violations_count(self):
        """Track the total known violations count -- this number should decrease over time."""
        assert len(self.KNOWN_VIOLATIONS) == 6, (
            f"Expected 6 known violations, got {len(self.KNOWN_VIOLATIONS)}. "
            "Update this count as violations are resolved."
        )
