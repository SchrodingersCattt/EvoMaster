from src.apis.chat_api import _build_agent_prompt


def test_build_agent_prompt_appends_available_attachments() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "old turn",
            "files": ["https://oss.example.com/chat/old.csv"],
            "images": ["https://oss.example.com/chat/old.png"],
            "workspace_paths": ["/share/old.cif"],
            "session_id": "sess-attachments",
            "task_id": "task-old",
        },
        {
            "source": "User",
            "type": "query",
            "content": "new turn",
            "files": ["https://oss.example.com/chat/new.csv"],
            "images": ["https://oss.example.com/chat/new.png"],
            "workspace_paths": ["/share/new.cif"],
            "session_id": "sess-attachments",
            "task_id": "task-new",
        },
    ]

    prompt = _build_agent_prompt("new turn", events)

    assert "[Available attachments]" in prompt
    assert "file_1 old.csv https://oss.example.com/chat/old.csv" in prompt
    assert "file_2 new.csv https://oss.example.com/chat/new.csv" in prompt
    assert "image_1 old.png https://oss.example.com/chat/old.png" in prompt
    assert "image_2 new.png https://oss.example.com/chat/new.png" in prompt
    assert "workspace_1 /share/old.cif" in prompt
    assert "workspace_2 /share/new.cif" in prompt
