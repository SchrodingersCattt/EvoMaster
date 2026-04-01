"""Gap 8 (27-03-01 / ALL): Broader import audit across all matmaster/ files.

Behavioral contract:
- No matmaster/ file has a top-level (col_offset == 0) import from:
    * evomaster.agent.tools.mcp
    * evomaster.adaptors.calculation
- The exceptions (function-level lazy imports) are:
    * path_adaptor.py: evomaster.env.bohrium (lazy per D-08)
    * job_service.py: evomaster.env.bohrium (lazy per D-06)
- These bohrium imports must NOT be at col_offset == 0 (must be inside function bodies).

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


class TestBohrimLazyImportsAreNotTopLevel:
    """evomaster.env.bohrium imports must appear only inside function bodies (not at module top level)."""

    def test_no_top_level_evomaster_env_bohrium_imports(self):
        violations = []
        for py_file in _find_matmaster_py_files():
            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _find_top_level_imports_matching(source, "evomaster.env.bohrium")
            for node in hits:
                violations.append(
                    f"{py_file.relative_to(Path(__file__).parent.parent.parent.parent)}:"
                    f"L{node.lineno}: from {node.module} import ..."
                )
        assert violations == [], (
            "Found top-level 'from evomaster.env.bohrium' imports in matmaster/. "
            "These must be function-level lazy imports (per D-08):\n"
            + "\n".join(violations)
        )


class TestExpectedLazyBohrimImportsExist:
    """Verify the expected lazy bohrium imports are present in the correct files (sanity check)."""

    def test_path_adaptor_has_bohrium_lazy_import(self):
        # __file__ = .../matmaster-evo/tests/matmaster/test_import_audit.py
        # parent.parent.parent = .../matmaster-evo/
        path_adaptor = Path(__file__).parent.parent.parent / "matmaster" / "adaptors" / "calculation" / "path_adaptor.py"
        assert path_adaptor.exists(), "path_adaptor.py not found"
        source = path_adaptor.read_text(encoding="utf-8")
        assert "evomaster.env.bohrium" in source, (
            "path_adaptor.py should have lazy imports from evomaster.env.bohrium"
        )

    def test_job_service_has_bohrium_lazy_import(self):
        job_service = Path(__file__).parent.parent.parent / "matmaster" / "adaptors" / "calculation" / "job_service.py"
        assert job_service.exists(), "job_service.py not found"
        source = job_service.read_text(encoding="utf-8")
        assert "evomaster.env.bohrium" in source, (
            "job_service.py should have lazy imports from evomaster.env.bohrium"
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
        assert "from evomaster.adaptors.calculation" not in source, (
            "_llm.py still imports from evomaster.adaptors.calculation"
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
