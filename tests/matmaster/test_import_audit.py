"""Import audit for matmaster/ package isolation.

Behavioral contract:
- No matmaster/ file has a top-level (col_offset == 0) import from:
    * evomaster.agent.tools.mcp
    * evomaster.adaptors.calculation
- No matmaster/ file imports from src.* (any level, excluding TYPE_CHECKING)
- No matmaster/ file imports from evomaster.agent.session.* (any level, excluding TYPE_CHECKING)
- No matmaster/ file imports from evomaster.env.bohrium (any level, excluding TYPE_CHECKING)
  (Phase 28 migrates all bohrium imports to matmaster.integration.bohrium_env)

Scope: All .py files under matmaster/ excluding tests.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _find_matmaster_py_files() -> list[Path]:
    """Return all .py files under matmaster/ package directory."""
    # __file__ = .../matmaster-evo/tests/matmaster/test_import_audit.py
    # parent.parent.parent = .../matmaster-evo/
    matmaster_root = Path(__file__).parent.parent.parent / "matmaster"
    return sorted(matmaster_root.rglob("*.py"))


def _find_top_level_imports_matching(source: str, module_prefix: str) -> list[ast.ImportFrom]:
    """Parse source and return top-level (col_offset==0) ImportFrom nodes matching module_prefix."""
    tree = ast.parse(source)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith(module_prefix)
        and node.col_offset == 0
    ]


def _is_inside_type_checking(node: ast.ImportFrom, tree: ast.Module) -> bool:
    """Check whether an ImportFrom node lives inside an ``if TYPE_CHECKING:`` block.

    Walks all top-level ``If`` nodes whose test is ``TYPE_CHECKING`` (Name or Attribute)
    and checks whether *node* falls within any such block's line range.
    """
    for top_node in ast.walk(tree):
        if not isinstance(top_node, ast.If):
            continue
        test = top_node.test
        is_tc = (
            (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
        )
        if not is_tc:
            continue
        # node is inside this if-block if its line falls within body or orelse
        block_start = top_node.lineno
        block_end = top_node.end_lineno or top_node.lineno
        if block_start <= node.lineno <= block_end:
            return True
    return False


def _find_all_imports_matching(
    source: str, module_prefix: str, *, exclude_type_checking: bool = True
) -> list[tuple[ast.ImportFrom, str]]:
    """Find ALL ImportFrom nodes matching module_prefix (any nesting level).

    Returns (node, relative_path_placeholder) tuples. When *exclude_type_checking*
    is True, imports inside ``if TYPE_CHECKING:`` blocks are skipped.
    """
    tree = ast.parse(source)
    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None:
            continue
        if not (node.module.startswith(module_prefix) or node.module == module_prefix.rstrip(".")):
            continue
        if exclude_type_checking and _is_inside_type_checking(node, tree):
            continue
        results.append(node)
    return results


class TestNoTopLevelEvomasterMCPImports:
    """No matmaster file may have top-level import from evomaster.agent.tools.mcp."""

    def test_no_top_level_evomaster_agent_tools_mcp_imports(self):
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_top_level_imports_matching(source, "evomaster.agent.tools.mcp")
            for node in hits:
                violations.append(
                    f"{py_file.relative_to(Path(__file__).parent.parent.parent.parent)}:"
                    f"L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found top-level 'from evomaster.agent.tools.mcp' imports in matmaster/:\n"
            + "\n".join(violations)
        )


class TestNoTopLevelEvomasterCalculationImports:
    """No matmaster file may have top-level import from evomaster.adaptors.calculation."""

    def test_no_top_level_evomaster_adaptors_calculation_imports(self):
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_top_level_imports_matching(source, "evomaster.adaptors.calculation")
            for node in hits:
                violations.append(
                    f"{py_file.relative_to(Path(__file__).parent.parent.parent.parent)}:"
                    f"L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found top-level 'from evomaster.adaptors.calculation' imports in matmaster/:\n"
            + "\n".join(violations)
        )


class TestNoSrcImportsInMatmaster:
    """No matmaster file may import from src (neither top-level nor lazy).

    Excludes TYPE_CHECKING blocks (forward references are acceptable).
    """

    def test_no_src_imports_anywhere(self):
        project_root = Path(__file__).parent.parent.parent
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_all_imports_matching(source, "src.", exclude_type_checking=True)
            for node in hits:
                rel = py_file.relative_to(project_root)
                violations.append(
                    f"{rel}:L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found non-TYPE_CHECKING 'from src.*' imports in matmaster/:\n"
            + "\n".join(violations)
        )


class TestNoEvomasterSessionImportsInMatmaster:
    """No matmaster file may import from evomaster.agent.session.

    Excludes TYPE_CHECKING blocks.
    """

    def test_no_evomaster_session_imports(self):
        project_root = Path(__file__).parent.parent.parent
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_all_imports_matching(
                source, "evomaster.agent.session", exclude_type_checking=True
            )
            for node in hits:
                rel = py_file.relative_to(project_root)
                violations.append(
                    f"{rel}:L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found non-TYPE_CHECKING 'from evomaster.agent.session.*' imports in matmaster/:\n"
            + "\n".join(violations)
        )


class TestNoEvomasterEnvBohriumImportsAnywhere:
    """No matmaster file may import from evomaster.env.bohrium (any level).

    Phase 28 migrates all such imports to matmaster.integration.bohrium_env.
    Excludes TYPE_CHECKING blocks.
    """

    def test_no_evomaster_env_bohrium_imports(self):
        project_root = Path(__file__).parent.parent.parent
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_all_imports_matching(
                source, "evomaster.env.bohrium", exclude_type_checking=True
            )
            for node in hits:
                rel = py_file.relative_to(project_root)
                violations.append(
                    f"{rel}:L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found non-TYPE_CHECKING 'from evomaster.env.bohrium' imports in matmaster/:\n"
            + "\n".join(violations)
        )


class TestNoEvomasterConfigImportsInMatmaster:
    """No matmaster file may import from evomaster.config (any level).

    Phase 29 replaces monitor_job/_llm.py ConfigManager with matmaster native config.
    Excludes TYPE_CHECKING blocks.
    """

    def test_no_evomaster_config_imports(self):
        project_root = Path(__file__).parent.parent.parent
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_all_imports_matching(
                source, "evomaster.config", exclude_type_checking=True
            )
            for node in hits:
                rel = py_file.relative_to(project_root)
                violations.append(
                    f"{rel}:L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found non-TYPE_CHECKING 'from evomaster.config' imports in matmaster/:\n"
            + "\n".join(violations)
        )


class TestNoEvomasterUtilsImportsInMatmaster:
    """No matmaster file may import from evomaster.utils (any level).

    Phase 29 replaces monitor_job/_llm.py create_llm/LLMConfig with matmaster native config.
    Excludes TYPE_CHECKING blocks.
    """

    def test_no_evomaster_utils_imports(self):
        project_root = Path(__file__).parent.parent.parent
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_all_imports_matching(
                source, "evomaster.utils", exclude_type_checking=True
            )
            for node in hits:
                rel = py_file.relative_to(project_root)
                violations.append(
                    f"{rel}:L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found non-TYPE_CHECKING 'from evomaster.utils' imports in matmaster/:\n"
            + "\n".join(violations)
        )


class TestTargetFilesMigratedToMatmaster:
    """Verify the 8 target files explicitly listed in Plan 03 use matmaster-native imports."""

    def _read_file(self, rel_path: str) -> str:
        # __file__ = .../matmaster-evo/tests/matmaster/test_import_audit.py
        # parent.parent.parent = .../matmaster-evo/
        target = Path(__file__).parent.parent.parent / rel_path
        assert target.exists(), f"File not found: {rel_path}"
        return target.read_text(encoding="utf-8")

    def test_lazy_mcp_no_evomaster(self):
        source = self._read_file("matmaster/tools/lazy_mcp.py")
        import_lines = [
            line.strip() for line in source.split('\n')
            if ('from evomaster' in line or 'import evomaster' in line)
            and not line.strip().startswith('#')
            and not line.strip().startswith('"')
            and not line.strip().startswith("'")
        ]
        assert import_lines == [], f"lazy_mcp.py has evomaster imports: {import_lines}"

    def test_cache_mcp_schemas_no_evomaster(self):
        source = self._read_file("matmaster/tools/cache_mcp_schemas.py")
        import_lines = [
            line.strip() for line in source.split('\n')
            if ('from evomaster' in line or 'import evomaster' in line)
            and not line.strip().startswith('#')
        ]
        assert import_lines == [], f"cache_mcp_schemas.py has evomaster imports: {import_lines}"

    def test_exp_no_evomaster_calculation_import(self):
        source = self._read_file("matmaster/core/exp.py")
        assert "from evomaster.adaptors.calculation" not in source, (
            "exp.py still imports from evomaster.adaptors.calculation"
        )

    def test_eval_tooling_no_evomaster_calculation_import(self):
        source = self._read_file("matmaster/eval_tooling_snapshot.py")
        assert "from evomaster.adaptors.calculation" not in source, (
            "eval_tooling_snapshot.py still imports from evomaster.adaptors.calculation"
        )

    def test_monitor_job_lifecycle_no_evomaster(self):
        source = self._read_file("matmaster/tools/builtin/monitor_job/_lifecycle.py")
        assert "from evomaster" not in source, (
            "_lifecycle.py still imports from evomaster"
        )

    def test_monitor_job_llm_no_evomaster(self):
        source = self._read_file("matmaster/tools/builtin/monitor_job/_llm.py")
        assert "from evomaster" not in source, (
            "_llm.py still imports from evomaster"
        )

    def test_monitor_job_logs_no_evomaster(self):
        source = self._read_file("matmaster/tools/builtin/monitor_job/_logs.py")
        assert "from evomaster" not in source, (
            "_logs.py still imports from evomaster"
        )

    def test_monitor_job_download_no_evomaster(self):
        source = self._read_file("matmaster/tools/builtin/monitor_job/_download.py")
        assert "from evomaster" not in source, (
            "_download.py still imports from evomaster"
        )
