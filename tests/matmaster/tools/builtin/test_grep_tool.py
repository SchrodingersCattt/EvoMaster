"""tests/matmaster/tools/builtin/test_grep_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.grep_tool import GrepTool
from matmaster.tools.filesystem_semantics.snapshots import (
    FileSemanticSnapshot,
    SnapshotFingerprint,
)
from matmaster.tools.filesystem_semantics.text_resolution import TextResolution
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext


def make_session(output="", exit_code=0):
    session = MagicMock()
    session.exec_bash.return_value = {"output": output, "exit_code": exit_code}
    return session


class TestGrepToolMetadata:
    def test_name(self):
        assert GrepTool.name == "Grep"

    def test_schema_has_output_mode(self):
        assert "output_mode" in GrepTool.json_schema["properties"]

    def test_schema_has_context_flags(self):
        props = GrepTool.json_schema["properties"]
        assert "-A" in props
        assert "-B" in props
        assert "-C" in props

    def test_grep_uses_workspace_shared_read(self):
        assert GrepTool.resource_claims == (
            ResourceClaim(resource="workspace", mode="shared_read"),
        )


class TestGrepExecution:
    def test_no_matches(self):
        tool = GrepTool(session=make_session(output=""), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "notfound"}))
        assert isinstance(result, ToolResult)
        assert "no matches" in result.content.lower()
        assert result.meta["fallback_mode"] == "backend"

    def test_files_with_matches_mode(self):
        tool = GrepTool(
            session=make_session(output="/workspace/a.py\n/workspace/b.py"),
            workdir="/workspace",
        )
        result = asyncio.run(
            tool.execute(
                {
                    "pattern": "import",
                    "output_mode": "files_with_matches",
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert "a.py" in result.content

    def test_content_mode(self):
        output = "/workspace/a.py:1:import os"
        tool = GrepTool(session=make_session(output=output), workdir="/workspace")
        result = asyncio.run(
            tool.execute(
                {
                    "pattern": "import",
                    "output_mode": "content",
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert "import os" in result.content

    def test_shell_escape_pattern(self):
        session = make_session(output="")
        tool = GrepTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "$(evil)"}))
        cmd = (
            session.exec_bash.call_args[1].get("command")
            or session.exec_bash.call_args[0][0]
        )
        assert "$(" not in cmd.split("'")[0]  # pattern should be escaped


class TestGrepEnvInjection:
    def test_grep_injects_bohrium_env_from_bridge(self):
        session = MagicMock()
        session._bohrium_credentials = {"access_key": "ak", "project_id": 42}
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"output": "", "exit_code": 1},  # which rg -> not found
                {"stdout": "", "stderr": "", "exit_code": 0},  # chmod for env file
                {"output": "match", "exit_code": 0},  # actual grep
            ]
        )
        tool = GrepTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "test"}))
        # The final exec_bash call (the actual grep) should have env-wrapped command
        final_call = session.exec_bash.call_args_list[-1]
        cmd = final_call.kwargs.get("command", final_call[1].get("command", ""))
        assert "grep" in cmd
        assert session.write_file.called


class TestGrepRgDetection:
    def test_rg_detection_cached(self):
        session = make_session(output="")
        # First call detects rg
        rg_check = MagicMock()
        rg_check.return_value = {"output": "/usr/bin/rg", "exit_code": 0}
        session.exec_bash.side_effect = [
            {"output": "/usr/bin/rg", "exit_code": 0},  # which rg
            {"output": "", "exit_code": 1},  # actual grep
        ]
        tool = GrepTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "test"}))
        assert tool._use_rg is True


class TestGrepFallback:
    def test_grep_binary_signal_uses_semantic_fallback(self, monkeypatch) -> None:
        session = make_session(
            output='binary file matches (found "\\0" byte around offset 1)'
        )
        tool = GrepTool(session=session, workdir="/workspace")
        state = ToolRunnerState()
        state.set(
            "file_semantics",
            {
                "/workspace/a.txt": FileSemanticSnapshot(
                    path="/workspace/a.txt",
                    fingerprint=SnapshotFingerprint(
                        size=8,
                        mtime=1.0,
                        prefix_hash="aaa",
                    ),
                    kind="candidate_text",
                    encoding=None,
                    encoding_source="candidate_probe",
                )
            },
        )
        ctx = ToolExecutionContext(runner_state=state)

        monkeypatch.setattr(tool, "_detect_rg", lambda: True)
        monkeypatch.setattr(
            tool,
            "_list_candidate_files",
            lambda safe_path, file_glob: ["/workspace/a.txt"],
        )
        monkeypatch.setattr(
            "matmaster.tools.builtin.grep_tool.resolve_text_bytes",
            lambda raw, explicit_encoding=None: TextResolution(
                status="success",
                semantic_kind="definite_text",
                text="alpha\nbeta\n",
                encoding="utf-16",
                encoding_source="bom",
            ),
        )
        session.download.return_value = b"unused"

        result = asyncio.run(
            tool.execute_with_context(
                {"pattern": "alpha", "output_mode": "content"}, ctx
            )
        )
        assert isinstance(result, ToolResult)
        assert "alpha" in result.content
        assert result.meta["fallback_mode"] == "semantic"
