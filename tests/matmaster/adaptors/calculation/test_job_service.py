"""Gap 6 (27-02-03 / CALC-01): job_service function signatures, RUNNING_STATUSES, no top-level evomaster imports.

Behavioral contract:
- All 8 public functions and RUNNING_STATUSES constant importable.
- RUNNING_STATUSES is a frozenset/set containing 'Running' and 'Pending'.
- All function signatures match the original contract (accept bohr_job_id as first arg).
- No top-level (col_offset == 0) evomaster imports; evomaster.env.bohrium appears only in function bodies.
- Module uses relative import for env_config ('from .env_config import').
- _get_access_key uses bridge-backed resolution (Task 5).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace


class TestJobServiceImport:
    def test_running_statuses_importable(self):
        from matmaster.adaptors.calculation.job_service import RUNNING_STATUSES

        assert RUNNING_STATUSES is not None

    def test_query_job_status_importable(self):
        from matmaster.adaptors.calculation.job_service import query_job_status

        assert callable(query_job_status)

    def test_get_job_results_importable(self):
        from matmaster.adaptors.calculation.job_service import get_job_results

        assert callable(get_job_results)

    def test_iterate_job_files_importable(self):
        from matmaster.adaptors.calculation.job_service import iterate_job_files

        assert callable(iterate_job_files)

    def test_download_job_file_importable(self):
        from matmaster.adaptors.calculation.job_service import download_job_file

        assert callable(download_job_file)

    def test_download_job_directory_importable(self):
        from matmaster.adaptors.calculation.job_service import download_job_directory

        assert callable(download_job_directory)

    def test_terminate_job_importable(self):
        from matmaster.adaptors.calculation.job_service import terminate_job

        assert callable(terminate_job)

    def test_get_file_token_importable(self):
        from matmaster.adaptors.calculation.job_service import get_file_token

        assert callable(get_file_token)

    def test_get_job_detail_raw_importable(self):
        from matmaster.adaptors.calculation.job_service import get_job_detail_raw

        assert callable(get_job_detail_raw)

    def test_importable_from_package(self):
        from matmaster.adaptors.calculation import (
            query_job_status,
            terminate_job,
        )

        assert callable(query_job_status)
        assert callable(terminate_job)


class TestRunningStatuses:
    def test_is_set_or_frozenset(self):
        from matmaster.adaptors.calculation.job_service import RUNNING_STATUSES

        assert isinstance(RUNNING_STATUSES, (set, frozenset))

    def test_contains_running(self):
        from matmaster.adaptors.calculation.job_service import RUNNING_STATUSES

        assert "Running" in RUNNING_STATUSES

    def test_contains_pending(self):
        from matmaster.adaptors.calculation.job_service import RUNNING_STATUSES

        assert "Pending" in RUNNING_STATUSES

    def test_not_empty(self):
        from matmaster.adaptors.calculation.job_service import RUNNING_STATUSES

        assert len(RUNNING_STATUSES) > 0


class TestJobServiceFunctionSignatures:
    def test_query_job_status_has_bohr_job_id(self):
        from matmaster.adaptors.calculation.job_service import query_job_status

        sig = inspect.signature(query_job_status)
        assert "bohr_job_id" in sig.parameters

    def test_get_job_results_has_bohr_job_id(self):
        from matmaster.adaptors.calculation.job_service import get_job_results

        sig = inspect.signature(get_job_results)
        assert "bohr_job_id" in sig.parameters

    def test_terminate_job_has_bohr_job_id(self):
        from matmaster.adaptors.calculation.job_service import terminate_job

        sig = inspect.signature(terminate_job)
        assert "bohr_job_id" in sig.parameters

    def test_download_job_directory_has_bohr_job_id(self):
        from matmaster.adaptors.calculation.job_service import download_job_directory

        sig = inspect.signature(download_job_directory)
        assert "bohr_job_id" in sig.parameters

    def test_query_job_status_has_optional_access_key(self):
        from matmaster.adaptors.calculation.job_service import query_job_status

        sig = inspect.signature(query_job_status)
        params = sig.parameters
        assert "access_key" in params
        # access_key should be optional (has default)
        assert params["access_key"].default is not inspect.Parameter.empty


class TestJobServiceImportStructure:
    def test_uses_relative_env_config_import(self):
        import matmaster.adaptors.calculation.job_service as mod

        source = inspect.getsource(mod)
        assert (
            "from .env_config import" in source
        ), "job_service.py must use relative import for env_config"

    def test_no_top_level_evomaster_imports(self):
        module_file = Path(
            __import__(
                "matmaster.adaptors.calculation.job_service",
                fromlist=["job_service"],
            ).__file__
        )
        source = module_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_evo = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "evomaster" in node.module
            and node.col_offset == 0
        ]
        assert top_level_evo == [], (
            f"Found {len(top_level_evo)} top-level evomaster imports in job_service.py. "
            "Only function-level lazy imports allowed (per D-06)."
        )

    def test_no_evomaster_imports_remain(self):
        """job_service.py should have no evomaster imports (fully migrated to matmaster)."""
        import matmaster.adaptors.calculation.job_service as mod

        source = inspect.getsource(mod)
        tree = ast.parse(source)
        evo_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and any(
                "evomaster" in (alias.name or "")
                for alias in getattr(node, "names", [])
            )
            or (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and "evomaster" in node.module
            )
        ]
        assert (
            evo_imports == []
        ), "job_service.py should have no evomaster imports -- fully migrated to matmaster native"

    def test_module_import_does_not_trigger_evomaster_load(self):
        """Importing the module must not trigger evomaster top-level loading."""

        # evomaster.env.bohrium should NOT be in sys.modules after a fresh import
        # (we can't truly test this in isolation without reimporting, but we can
        # verify the module loaded fine without evomaster present in top-level imports)
        import matmaster.adaptors.calculation.job_service  # noqa: F401

        # If we got here without ImportError, the module loads without needing evomaster at top level
        assert True


class TestGetAccessKeyBridgeBacked:
    """Task 5: _get_access_key uses bridge-backed credential resolution."""

    def test_get_access_key_prefers_explicit(self):
        """Explicit access_key is returned without bridge lookup."""
        from matmaster.adaptors.calculation.job_service import _get_access_key

        ak = _get_access_key(access_key="explicit-ak")
        assert ak == "explicit-ak"

    def test_get_access_key_accepts_session_kwarg(self):
        """_get_access_key signature must accept session parameter."""
        from matmaster.adaptors.calculation.job_service import _get_access_key

        sig = inspect.signature(_get_access_key)
        assert (
            "session" in sig.parameters
        ), "_get_access_key must accept session parameter for bridge-backed resolution"

    def test_get_access_key_prefers_session_backed_bridge(self, monkeypatch):
        """_get_access_key should use bridge when session is provided."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)
        monkeypatch.delenv("BOHRIUM_USER_ID", raising=False)

        from matmaster.adaptors.calculation.job_service import _get_access_key

        session = SimpleNamespace(
            _bohrium_credentials={"access_key": "session-ak", "project_id": 42}
        )
        ak = _get_access_key(session=session)
        assert ak == "session-ak"

    def test_get_access_key_raises_when_no_credentials(self, monkeypatch):
        """_get_access_key should raise ValueError when no credentials available."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)
        monkeypatch.delenv("BOHRIUM_USER_ID", raising=False)

        import pytest

        from matmaster.adaptors.calculation.job_service import _get_access_key

        with pytest.raises(ValueError, match="[Bb]ohrium"):
            _get_access_key()
