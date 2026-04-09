from matmaster.tools.filesystem_semantics.shell_planner import plan_shell_command


def test_single_line_pipe_remains_inline() -> None:
    plan = plan_shell_command("cat a.txt | wc -l")
    assert plan.mode == "inline"


def test_heredoc_switches_to_script_mode() -> None:
    plan = plan_shell_command("python3 << 'PYEOF'\nprint(1)\nPYEOF")
    assert plan.mode == "script"
    assert plan.reason == "heredoc"


def test_multiline_if_switches_to_script_mode() -> None:
    plan = plan_shell_command("if true; then\necho hi\nfi")
    assert plan.mode == "script"
