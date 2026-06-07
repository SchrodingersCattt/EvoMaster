from __future__ import annotations

import pytest

from matmaster.types.message_normalization import restore_persisted_assistant_state


class TestRestorePersistedAssistantState:
    def test_restore_wrapped_state_preserves_internal_none(self) -> None:
        restored = restore_persisted_assistant_state(
            {
                "state": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "bash",
                            "arguments": {"cmd": "pwd"},
                        }
                    ],
                }
            }
        )

        assert restored.content is None
        assert restored.tool_calls is not None
        assert restored.tool_calls[0].id == "tc-1"

    def test_restore_trivial_preamble_becomes_internal_none(self) -> None:
        restored = restore_persisted_assistant_state(
            {
                "state": {
                    "role": "assistant",
                    "content": "...",
                    "tool_calls": [
                        {
                            "id": "tc-ellipsis",
                            "name": "bash",
                            "arguments": {"cmd": "pwd"},
                        }
                    ],
                }
            }
        )

        assert restored.content is None

    def test_restore_rejects_non_assistant_wrapper(self) -> None:
        with pytest.raises(ValueError, match="assistant_state payload"):
            restore_persisted_assistant_state(
                {"state": {"role": "tool", "content": "bad"}}
            )
