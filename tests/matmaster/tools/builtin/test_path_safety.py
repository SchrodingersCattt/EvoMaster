"""tests/matmaster/tools/builtin/test_path_safety.py"""

from matmaster.tools.builtin._path_safety import resolve_safe_path, shell_escape


class TestResolveSafePath:
    def test_empty_returns_workdir(self):
        assert resolve_safe_path("", "/workspace") == "/workspace"

    def test_dot_returns_workdir(self):
        assert resolve_safe_path(".", "/workspace") == "/workspace"

    def test_relative_path_joined(self):
        assert resolve_safe_path("src/foo", "/workspace") == "/workspace/src/foo"

    def test_absolute_within_workdir(self):
        assert resolve_safe_path("/workspace/src", "/workspace") == "/workspace/src"

    def test_absolute_outside_workdir_fallback(self):
        assert resolve_safe_path("/etc/passwd", "/workspace") == "/workspace"

    def test_traversal_blocked(self):
        assert resolve_safe_path("../../etc/passwd", "/workspace") == "/workspace"

    def test_normpath_removes_dotdot(self):
        assert resolve_safe_path("src/../src/foo", "/workspace") == "/workspace/src/foo"

    def test_workdir_trailing_slash(self):
        assert resolve_safe_path("src", "/workspace/") == "/workspace/src"

    def test_workdir_trailing_slash_absolute(self):
        assert resolve_safe_path("/workspace/src", "/workspace/") == "/workspace/src"

    def test_prefix_collision_not_subdir(self):
        # /workspacex is NOT a subdirectory of /workspace
        assert resolve_safe_path("/workspacex/foo", "/workspace") == "/workspace"


class TestShellEscape:
    def test_simple_string_unchanged(self):
        # shlex.quote returns safe strings unchanged (no wrapping quotes)
        assert shell_escape("hello") == "hello"

    def test_string_with_spaces(self):
        assert shell_escape("hello world") == "'hello world'"

    def test_injection_attempt_dollar(self):
        result = shell_escape("$(rm -rf /)")
        assert "$(" not in result or result.startswith("'")

    def test_injection_attempt_backtick(self):
        result = shell_escape("`rm -rf /`")
        assert "`" not in result or result.startswith("'")

    def test_injection_attempt_semicolon(self):
        result = shell_escape("foo; rm -rf /")
        assert result.startswith("'")

    def test_empty_string(self):
        assert shell_escape("") == "''"
