"""tests/matmaster/tools/test_bash_runner.py"""

from unittest.mock import MagicMock

from matmaster.tools.bash_runner import BashRunResult, run_bash_command


def make_session(output="ok", exit_code=0, working_dir="/share"):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": output,
        "exit_code": exit_code,
        "working_dir": working_dir,
    }
    return s


def test_run_bash_command_assembles_observation():
    session = make_session(output="hello", exit_code=0, working_dir="/share")
    result = run_bash_command(
        session=session,
        command="echo hello",
        timeout_s=2.0,
        cancel_token=None,
    )
    assert isinstance(result, BashRunResult)
    assert result.output == "hello"
    assert result.exit_code == 0
    assert result.working_dir == "/share"
    assert "[Session working directory: /share]" in result.observation
    assert "[Command finished with exit code 0]" in result.observation


def test_run_bash_command_passes_cancel_token_and_timeout():
    session = make_session()
    sentinel = object()
    run_bash_command(
        session=session,
        command="true",
        timeout_s=5.0,
        cancel_token=sentinel,
    )
    _, kwargs = session.exec_bash.call_args
    assert kwargs["timeout"] == 5.0
    assert kwargs["cancel_token"] is sentinel


def test_run_bash_command_merges_extra_env_without_runtime():
    session = make_session()
    # No runtime attached -> base env empty; extra_env still applied via script_env.
    run_bash_command(
        session=session,
        command="echo $ARTIFACT_DIR",
        timeout_s=2.0,
        cancel_token=None,
        extra_env={"ARTIFACT_DIR": "/share/.artifacts"},
    )
    # Non-empty env is injected via a temp env file (script_env._via_file),
    # so write_file must have been called and the command actually ran. This
    # guards the merge: a dropped extra_env would skip env injection entirely.
    assert session.write_file.called
    assert session.exec_bash.called
