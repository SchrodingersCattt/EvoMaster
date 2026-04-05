"""Cross-module E2E regressions for runtime credential bridge.

Verify that BohriumTool, calculation path_adaptor, and job_service all
resolve credentials through the unified bridge rather than their own
fallback chains.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult


def _session_with_bohrium(
    access_key="e2e-ak", project_id=99, user_id=7, user_no="U001"
):
    return SimpleNamespace(
        _bohrium_credentials={
            "access_key": access_key,
            "project_id": project_id,
            "user_id": user_id,
            "user_no": user_no,
        },
        is_open=True,
    )


class TestBohriumToolAndRemoteShare:
    """BohriumTool should use session credentials for all actions."""

    def test_bohrium_tool_submit_uses_session_credentials(self, tmp_path, monkeypatch):
        """Submit action uses session-backed credentials, not env vars."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        session = _session_with_bohrium()
        tool = BohriumTool(session=session, workdir=tmp_path)

        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "test.inp").write_text("data", encoding="utf-8")

        # Mock the API calls
        import matmaster.tools.builtin.bohrium_tool as bmod

        post_calls = []

        def fake_post(base_url, path, access_key, payload, timeout=30):
            post_calls.append({"path": path, "access_key": access_key})
            if "create" in path:
                return {
                    "code": 0,
                    "data": {
                        "storePath": "p/",
                        "storeHost": "https://s.com",
                        "token": "t",
                        "jobId": "j1",
                    },
                }
            return {"code": 0, "data": {"jobId": "j2", "bohrJobId": "b2"}}

        monkeypatch.setattr(bmod, "_post", fake_post)

        # Mock tiefblue SDK
        sdk_module = types.ModuleType("bohrium_open_sdk")
        opensdk_module = types.ModuleType("bohrium_open_sdk.opensdk")
        tiefblue_module = types.ModuleType("bohrium_open_sdk.opensdk._tiefblue_client")

        class FakeTiefblue:
            def __init__(self, **kw):
                pass

            def upload_from_file_multi_part(self, **kw):
                return {}

        tiefblue_module.Tiefblue = FakeTiefblue
        sdk_module.opensdk = opensdk_module
        opensdk_module._tiefblue_client = tiefblue_module

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)

        with monkeypatch.context() as m:
            m.setitem(sys.modules, "bohrium_open_sdk", sdk_module)
            m.setitem(sys.modules, "bohrium_open_sdk.opensdk", opensdk_module)
            m.setitem(
                sys.modules,
                "bohrium_open_sdk.opensdk._tiefblue_client",
                tiefblue_module,
            )
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": str(input_dir),
                        "image": "test:latest",
                        "cmd": "echo hi",
                    }
                )
            )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        # Verify session credential was used
        assert any(c["access_key"] == "e2e-ak" for c in post_calls)

    def test_bohrium_tool_poll_with_remote_share_and_no_session_errors(
        self, tmp_path, monkeypatch
    ):
        """Poll with /share/ result_dir and no session should produce an error."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        tool = BohriumTool(workdir=tmp_path)
        result = asyncio.run(
            tool.execute(
                {
                    "action": "poll",
                    "job_id": "j1",
                    "result_dir": "/share/results",
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.status == "error"


class TestCalculationPathAdaptorUsesBridge:
    """path_adaptor should get credentials through the bridge."""

    def test_resolve_args_uses_session_credentials_via_bridge(self, monkeypatch):
        """resolve_args should use bridge-resolved session credentials."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        from matmaster.adaptors.calculation.path_adaptor import (
            CalculationPathAdaptor,
        )

        adaptor = CalculationPathAdaptor(
            calculation_executors={
                "test_server": {
                    "executor": {"type": "local", "env": {}},
                }
            }
        )

        session = _session_with_bohrium(access_key="adaptor-ak", project_id=77)

        # Mock OSS upload to prevent actual network calls
        with patch(
            "matmaster.adaptors.calculation.path_adaptor.upload_file_to_oss"
        ) as mock_oss:
            mock_oss.return_value = "https://oss.example.com/file.txt"
            result = adaptor.resolve_args(
                workspace_path="/workspace",
                args={"param": "value"},
                tool_name="test_server_run_tool",
                server_name="test_server",
                input_schema=None,
                session=session,
            )

        # Verify executor was injected with session credentials
        executor = result.get("executor")
        assert executor is not None
        env = executor.get("env", {})
        assert env.get("BOHRIUM_ACCESS_KEY") == "adaptor-ak"

    def test_old_session_credentials_helper_removed(self):
        """The old _session_bohrium_credentials helper should be removed."""
        import matmaster.adaptors.calculation.path_adaptor as mod

        assert not hasattr(mod, "_session_bohrium_credentials")


class TestJobServiceUsesBridge:
    """job_service should get access_key through the bridge."""

    def test_get_access_key_uses_session_via_bridge(self, monkeypatch):
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        from matmaster.adaptors.calculation.job_service import _get_access_key

        session = _session_with_bohrium(access_key="js-ak")
        ak = _get_access_key(session=session)
        assert ak == "js-ak"

    def test_get_access_key_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env-ak")
        from matmaster.adaptors.calculation.job_service import _get_access_key

        ak = _get_access_key()
        assert ak == "env-ak"
