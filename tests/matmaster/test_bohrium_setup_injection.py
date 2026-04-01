"""Unit tests for BohriumSetupService callback injection pattern.

Verifies that BohriumSetupService delegates to injected callables
instead of importing from src.services.agent_run_bohrium.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_service(**overrides: Any):
    """Build a BohriumSetupService with mock callables."""
    from matmaster.integration.bohrium_setup import BohriumSetupService

    defaults = {
        "load_credentials_fn": MagicMock(return_value=({"ak": "x"}, "uid", "org")),
        "apply_credentials_fn": MagicMock(),
        "setup_fn": MagicMock(),
        "cleanup_fn": MagicMock(),
        "bus": None,
    }
    defaults.update(overrides)
    return BohriumSetupService(**defaults)


class TestCallbackInjection:
    """Verify each method delegates to the injected callable."""

    def test_load_credentials_calls_injected_fn(self):
        mock_fn = MagicMock(return_value=({"ak": "test"}, "u1", "org1"))
        svc = _make_service(load_credentials_fn=mock_fn)
        result = svc.load_credentials("session-123")
        mock_fn.assert_called_once_with("session-123")
        assert result == ({"ak": "test"}, "u1", "org1")

    def test_apply_credentials_calls_injected_fn(self):
        mock_fn = MagicMock()
        svc = _make_service(apply_credentials_fn=mock_fn)
        fake_session = object()
        creds = {"access_key": "abc"}
        svc.apply_credentials(fake_session, creds)
        mock_fn.assert_called_once_with(fake_session, creds)

    def test_setup_calls_injected_fn(self):
        from matmaster.integration.bohrium_env import BohriumSetupResult

        expected = BohriumSetupResult(
            ssh_attached=True,
            abort_result=None,
            execution_session=None,
            execution_workdir="/remote",
            session_type="ssh",
        )
        mock_fn = MagicMock(return_value=expected)
        svc = _make_service(setup_fn=mock_fn)
        result = svc.setup(
            session_id="s1",
            pg=object(),
            skill_sync_spec=None,
            run_creds={"ak": "x"},
            user_id_for_ak="uid",
            org_id="org",
            event_callback=lambda *a, **k: None,
            run_started_at=1000.0,
        )
        assert mock_fn.called
        assert result is expected

    def test_cleanup_calls_injected_fn(self):
        mock_fn = MagicMock()
        svc = _make_service(cleanup_fn=mock_fn)
        svc.cleanup(
            session_id="s1",
            event_callback=lambda *a, **k: None,
            pg_for_run=object(),
            ssh_attached=True,
        )
        assert mock_fn.called


class TestNoSrcImportInBohriumSetup:
    """AST scan confirms bohrium_setup.py has no src imports."""

    def test_no_src_import_in_bohrium_setup(self):
        setup_file = (
            Path(__file__).parent.parent.parent
            / "matmaster"
            / "integration"
            / "bohrium_setup.py"
        )
        source = setup_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        src_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("src.")
        ]
        assert src_imports == [], (
            "bohrium_setup.py still has src imports: "
            + ", ".join(f"L{n.lineno}: from {n.module}" for n in src_imports)
        )


class TestBohriumSetupResultFromBohriumEnv:
    """Verify BohriumSetupResult is sourced from matmaster.integration.bohrium_env."""

    def test_bohrium_setup_result_from_bohrium_env(self):
        setup_file = (
            Path(__file__).parent.parent.parent
            / "matmaster"
            / "integration"
            / "bohrium_setup.py"
        )
        source = setup_file.read_text(encoding="utf-8")
        assert "from matmaster.integration.bohrium_env import BohriumSetupResult" in source, (
            "bohrium_setup.py should import BohriumSetupResult from matmaster.integration.bohrium_env"
        )
