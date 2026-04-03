from matmaster.types.tool_runner_state import ToolRunnerState


def test_get_set() -> None:
    state = ToolRunnerState()

    assert state.get("k") is None
    assert state.get("k", 42) == 42

    state.set("k", "v")

    assert state.get("k") == "v"


def test_clear() -> None:
    state = ToolRunnerState()
    state.set("a", 1)

    state.clear()

    assert state.get("a") is None
