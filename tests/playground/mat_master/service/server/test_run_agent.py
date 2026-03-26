from playground.mat_master.service.server.run_agent import _build_dialog_history


def test_build_dialog_history_excludes_spawn_events() -> None:
    events = [
        {"source": "User", "type": "query", "content": "hello"},
        {
            "source": "MatMaster",
            "type": "tool_call",
            "content": {"id": "call_1", "name": "spawn", "args": {"task": "demo"}},
            "spawn_id": "childdeadbeef123",
        },
        {
            "source": "MatMaster",
            "type": "tool_result",
            "content": {"id": "call_1", "name": "spawn", "result": "done"},
            "spawn_id": "childdeadbeef123",
        },
        {"source": "MatMaster", "type": "run_result", "content": "answer"},
    ]

    dialog_history = _build_dialog_history(events)

    assert [msg["role"] for msg in dialog_history] == ["user", "assistant"]
    assert dialog_history[-1]["content"] == "answer"
