"""Cross-module E2E regressions for Bohrium runtime handle consumers."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import matmaster.bohrium.client as bohrium_client_module
import matmaster.bohrium.upload as bohrium_upload_module
from matmaster.bohrium.credentials import build_bohrium_context
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime, get_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult
from matmaster_bohrium_transfer.version import SCHEMA_VERSION


def _attach_runtime(
    session: SimpleNamespace,
    *,
    access_key: str = "e2e-ak",
    project_id: int = 99,
    user_id: int = 7,
    user_no: str = "U001",
    session_type: str = "ssh",
) -> SimpleNamespace:
    attach_runtime(
        session,
        BohriumRuntimeHandle(
            credentials=BohriumCredentials(
                access_key=access_key,
                project_id=project_id,
                user_id=user_id,
                user_no=user_no,
                base_url="https://openapi.test.dp.tech",
            ),
            execution=BohriumExecutionContext(
                session_type=session_type,
                execution_workdir="/share",
                remote_workspace_root="/share",
                remote_project_root="/share/.matmaster",
                node_id=1,
                node_ip="10.0.0.1",
                ssh_attached=session_type == "ssh",
            ),
            execution_session=session,
        ),
    )
    return session


def _session_with_runtime(
    access_key: str = "e2e-ak",
    project_id: int = 99,
    user_id: int = 7,
    user_no: str = "U001",
) -> SimpleNamespace:
    session = SimpleNamespace(is_open=True)
    return _attach_runtime(
        session,
        access_key=access_key,
        project_id=project_id,
        user_id=user_id,
        user_no=user_no,
    )


class _RemoteShareSession(SimpleNamespace):
    """Session double exposing remote directory operations for Bohrium submit."""

    def __init__(self):
        super().__init__(is_open=True)
        self.exec_calls: list[str] = []
        self.download_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []
        _attach_runtime(self)

    def path_exists(self, path: str) -> bool:
        return path == "/share/inputs"

    def is_file(self, path: str) -> bool:
        return False

    def exec_bash(self, command: str, timeout=None, cancel_token=None) -> dict:
        self.exec_calls.append(command)
        if command.startswith("command -v "):
            return {
                "stdout": "/usr/bin/python3\nPython 3.12.3\n",
                "stderr": "",
                "exit_code": 0,
                "working_dir": "/share",
                "output": "/usr/bin/python3\nPython 3.12.3\n",
            }
        if command.startswith("mktemp -d "):
            return {
                "stdout": "/tmp/matmaster_bohrium_transfer.ABCD12\n",
                "stderr": "",
                "exit_code": 0,
                "working_dir": "/share",
                "output": "/tmp/matmaster_bohrium_transfer.ABCD12\n",
            }
        if "matmaster_bohrium_transfer.remote version --json" in command:
            return {
                "stdout": json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "protocol_version": "1.0",
                        "ok": True,
                    }
                ),
                "stderr": "",
                "exit_code": 0,
                "working_dir": "/share",
                "output": "",
            }
        if "-m matmaster_bohrium_transfer.remote" in command:
            return {
                "stdout": json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "protocol_version": "1.0",
                        "ok": True,
                        "oss_key": "p/input.zip",
                    }
                ),
                "stderr": "",
                "exit_code": 0,
                "working_dir": "/share",
                "output": "",
            }
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "working_dir": "/share",
            "output": "",
        }

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        del encoding
        self.write_calls.append((path, content))

    def download(self, path: str, timeout=None) -> bytes:
        self.download_calls.append(path)
        return b"zip-bytes"


class TestBohriumToolAndRemoteShare:
    """BohriumTool should use runtime-backed credentials for all actions."""

    def test_bohrium_tool_submit_uses_runtime_credentials(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        session = _session_with_runtime()
        tool = BohriumTool(session=session, workdir=tmp_path)

        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "test.inp").write_text("data", encoding="utf-8")

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

        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)

        class FakeTiefblue:
            def __init__(self, base_url=None):
                pass

            def upload_From_file_multi_part(self, *args, **kw):
                return None

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        monkeypatch.setattr(
            bohrium_upload_module, "_load_tiefblue_client", lambda: FakeTiefblue
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
        assert any(c["access_key"] == "e2e-ak" for c in post_calls)

    def test_bohrium_tool_submit_remote_share_uses_runtime_session_for_input_dir(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        session = _RemoteShareSession()
        tool = BohriumTool(session=session, workdir=tmp_path)

        post_calls = []

        def fake_post(base_url, path, access_key, payload, timeout=30):
            post_calls.append(
                {"path": path, "access_key": access_key, "payload": payload}
            )
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

        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        result = asyncio.run(
            tool.execute(
                {
                    "action": "submit",
                    "input_dir": "/share/inputs",
                    "image": "test:latest",
                    "cmd": "echo hi",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert session.exec_calls
        assert not session.download_calls
        assert any(
            "-m matmaster_bohrium_transfer.remote upload-submit" in call
            and "--payload-file" in call
            for call in session.exec_calls
        )
        payload_writes = [
            content
            for path, content in session.write_calls
            if path.endswith("payload.json")
        ]
        assert len(payload_writes) == 1
        helper_payload = json.loads(payload_writes[0])
        assert helper_payload["schema_version"] == SCHEMA_VERSION
        assert helper_payload["input_dir"] == "/share/inputs"
        assert helper_payload["token"] == "t"
        assert post_calls[1]["payload"]["cmd"].endswith("> log 2>&1")

    def test_bohrium_tool_poll_with_remote_share_and_no_session_errors(
        self, tmp_path, monkeypatch
    ):
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


class TestCalculationPreflightUsesRuntime:
    def test_prepare_call_uses_runtime_credentials(self, monkeypatch):
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        from matmaster.mcp.calculation.preflight import CalculationPreflight

        preflight = CalculationPreflight(
            calculation_executors={
                "test_server": {
                    "executor": {"type": "local", "env": {}},
                }
            }
        )

        session = _session_with_runtime(access_key="adaptor-ak", project_id=77)
        runtime = get_runtime(session)
        assert runtime is not None

        result = preflight.prepare_call(
            workspace_path="/workspace",
            args={"param": "value"},
            tool_name="test_server_run_tool",
            remote_tool_name="run_tool",
            server_name="test_server",
            input_schema=None,
            tool_description=None,
            runtime=runtime,
            session=session,
        )

        executor = result.get("executor")
        assert executor is not None
        env = executor.get("env", {})
        assert env.get("BOHRIUM_ACCESS_KEY") == "adaptor-ak"


class TestBohriumContextBuildUsesRuntime:
    def test_build_context_uses_session_runtime(self, monkeypatch):
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)

        session = _session_with_runtime(access_key="js-ak")
        ctx = build_bohrium_context(session=session)
        assert ctx.credentials.access_key == "js-ak"
        assert ctx.credential_source == "runtime"

    def test_build_context_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env-ak")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "9")

        ctx = build_bohrium_context(session=None, require_project=True)
        assert ctx.credentials.access_key == "env-ak"
        assert ctx.credentials.project_id == 9
        assert ctx.credential_source == "env"
