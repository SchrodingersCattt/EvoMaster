"""tests/matmaster/tools/builtin/test_glob_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.glob_tool import MAX_GLOB_RESULTS, GlobTool


def make_session(output="", exit_code=0):
    s = MagicMock()
    s.exec_bash.return_value = {"output": output, "exit_code": exit_code}
    return s


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestGlobToolMetadata:
    def test_name(self):
        assert GlobTool.name == "Glob"

    def test_effect_level(self):
        assert GlobTool.effect_level == "none"

    def test_fast_path(self):
        assert GlobTool.fast_path_eligible is True


# ---------------------------------------------------------------------------
# _build_find_command — command generation
# ---------------------------------------------------------------------------


class TestBuildFindCommand:
    """Verify that glob patterns are correctly translated to find commands."""

    # -- recursive patterns (contains **) --

    def test_double_star_simple_suffix(self):
        """**/*.py -> find from root, -name '*.py'"""
        cmd = GlobTool._build_find_command("**/*.py", "/workspace")
        assert cmd.startswith("find /workspace")
        assert "-name '*.py'" in cmd
        assert "shopt" not in cmd
        assert "globstar" not in cmd
        # Must NOT have -maxdepth (recursive)
        assert "-maxdepth" not in cmd

    def test_double_star_with_prefix_dir(self):
        """src/**/*.ts -> find from /workspace/src, -name '*.ts'"""
        cmd = GlobTool._build_find_command("src/**/*.ts", "/workspace")
        assert "find /workspace/'src'" in cmd or "find /workspace/src" in cmd
        assert "-name '*.ts'" in cmd

    def test_double_star_deep_suffix(self):
        """src/**/bar/*.ts -> -path with wildcard"""
        cmd = GlobTool._build_find_command("src/**/bar/*.ts", "/workspace")
        assert "-path" in cmd
        assert "bar/*.ts" in cmd

    def test_bare_double_star(self):
        """** -> match all files (no -name / -path filter)"""
        cmd = GlobTool._build_find_command("**", "/workspace")
        assert "find /workspace" in cmd
        assert "-type f" in cmd
        # No -name or -path constraint for bare **
        assert "-name" not in cmd

    def test_prefix_double_star_no_suffix(self):
        """src/** -> match all files under src/"""
        cmd = GlobTool._build_find_command("src/**", "/workspace")
        assert "find /workspace" in cmd
        assert "-type f" in cmd

    # -- path patterns (contains / but no **) --

    def test_path_pattern_no_double_star(self):
        """src/*.py -> find with -path './src/*.py'"""
        cmd = GlobTool._build_find_command("src/*.py", "/workspace")
        assert "find /workspace" in cmd
        assert "-path" in cmd
        assert "src/*.py" in cmd
        assert "-maxdepth" not in cmd

    # -- simple name patterns (no / and no **) --

    def test_simple_name_pattern(self):
        """*.py -> maxdepth 1, -name '*.py'"""
        cmd = GlobTool._build_find_command("*.py", "/workspace")
        assert "find /workspace" in cmd
        assert "-maxdepth 1" in cmd
        assert "-name '*.py'" in cmd

    def test_simple_name_exact(self):
        """Makefile -> maxdepth 1, -name Makefile"""
        cmd = GlobTool._build_find_command("Makefile", "/workspace")
        assert "-maxdepth 1" in cmd
        # shlex.quote leaves safe strings unquoted
        assert "-name Makefile" in cmd

    # -- VCS excludes --

    def test_vcs_excludes_present(self):
        cmd = GlobTool._build_find_command("**/*.py", "/workspace")
        assert ".git" in cmd
        assert "node_modules" in cmd
        assert "__pycache__" in cmd
        assert ".svn" in cmd

    # -- result limit --

    def test_head_limit(self):
        cmd = GlobTool._build_find_command("**/*.py", "/workspace")
        assert f"head -{MAX_GLOB_RESULTS}" in cmd

    # -- type f --

    def test_always_type_f(self):
        """All commands should search for files only."""
        for pattern in ("**/*.py", "src/*.ts", "*.py", "**"):
            cmd = GlobTool._build_find_command(pattern, "/workspace")
            assert "-type f" in cmd, f"Missing -type f for pattern: {pattern}"

    # -- no shopt or globstar anywhere --

    def test_no_shopt_in_any_variant(self):
        for pattern in ("**/*.py", "src/**/*.ts", "src/*.py", "*.py"):
            cmd = GlobTool._build_find_command(pattern, "/workspace")
            assert "shopt" not in cmd, f"shopt found for pattern: {pattern}"
            assert "globstar" not in cmd, f"globstar found for pattern: {pattern}"

    # -- shell safety --

    def test_path_with_spaces_is_quoted(self):
        cmd = GlobTool._build_find_command("*.py", "/work space/dir")
        assert "'/work space/dir'" in cmd

    def test_pattern_with_injection_is_quoted(self):
        """Dangerous pattern chars must be shell-quoted."""
        cmd = GlobTool._build_find_command("$(rm -rf /)", "/workspace")
        # Pattern contains '/' so it goes through -path branch.
        # shlex.quote wraps in single quotes, neutralizing $(...)
        # The final string includes the './' prefix: './$(rm -rf /)'
        assert "-path" in cmd
        assert "$(rm -rf /)" in cmd
        # Verify it's safely quoted (inside single quotes)
        assert "'./$(rm -rf /)'" in cmd


# ---------------------------------------------------------------------------
# _execute — integration via mocked session
# ---------------------------------------------------------------------------


class TestGlobExecution:
    def test_empty_pattern_returns_error(self):
        tool = GlobTool(session=make_session(), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": ""}))
        assert "error" in result.lower()

    def test_no_results(self):
        tool = GlobTool(session=make_session(output=""), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "*.xyz"}))
        assert "no files" in result.lower()

    def test_results_returned(self):
        tool = GlobTool(
            session=make_session(output="/workspace/a.py\n/workspace/b.py"),
            workdir="/workspace",
        )
        result = asyncio.run(tool.execute({"pattern": "*.py"}))
        assert "a.py" in result
        assert "b.py" in result

    def test_truncation_message(self):
        """When results hit MAX_GLOB_RESULTS, append truncation notice."""
        lines = "\n".join(f"/workspace/f{i}.py" for i in range(MAX_GLOB_RESULTS))
        tool = GlobTool(session=make_session(output=lines), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "**/*.py"}))
        assert "truncated" in result.lower()

    def test_exec_bash_receives_find_command(self):
        """The command passed to exec_bash must be find-based, not shopt."""
        session = make_session(output="")
        tool = GlobTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "**/*.py"}))
        cmd = session.exec_bash.call_args[1].get("command", "")
        assert cmd.startswith("find ")
        assert "shopt" not in cmd

    def test_shell_escape_applied(self):
        """Pattern with shell-dangerous chars should be escaped."""
        session = make_session(output="")
        tool = GlobTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "$(rm -rf /)"}))
        cmd = session.exec_bash.call_args[1].get("command", "")
        # $( must be inside single quotes to neutralize it
        assert "'./$(rm -rf /)'" in cmd

    def test_custom_path_used(self):
        session = make_session(output="/workspace/sub/x.py")
        tool = GlobTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "*.py", "path": "sub"}))
        cmd = session.exec_bash.call_args[1].get("command", "")
        assert "/workspace/sub" in cmd


class TestGlobEnvInjection:
    def test_glob_injects_bohrium_env_from_bridge(self):
        session = MagicMock()
        session._bohrium_credentials = {"access_key": "ak", "project_id": 42}
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {"stdout": "/workspace/a.py", "stderr": "", "exit_code": 0, "output": "/workspace/a.py"},
            ]
        )
        tool = GlobTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "**/*.py"}))
        final_call = session.exec_bash.call_args_list[-1]
        assert final_call.kwargs["command"] != GlobTool._build_find_command("**/*.py", "/workspace")
        assert "find" in final_call.kwargs["command"]
        assert session.write_file.called
