from __future__ import annotations

import pytest

from matmaster.types.message_normalization import restore_persisted_assistant_state


def _tool_msg(call_id: str, n_images: int = 1, byte_size: int = 100):
    from matmaster.types.messages import ImageContentPart, ToolMessage

    payload = "x" * byte_size
    return ToolMessage(
        tool_call_id=call_id,
        tool_name="Read",
        content=f"Read image: /{call_id}.png",
        images=[
            ImageContentPart(url=f"data:image/png;base64,{payload}")
            for _ in range(n_images)
        ],
    )


def test_budget_keeps_newest_strips_oldest_by_count() -> None:
    from matmaster.types.message_normalization import apply_tool_image_budget

    messages = [_tool_msg(f"tc{i}") for i in range(6)]
    out = apply_tool_image_budget(messages, max_count=4, max_bytes=10**9)
    assert [bool(m.images) for m in out] == [False, False, True, True, True, True]
    assert "[image pruned from context" in out[0].content
    assert "[image pruned from context" not in out[2].content
    assert messages[0].images


def test_budget_strips_by_bytes() -> None:
    from matmaster.types.message_normalization import apply_tool_image_budget

    messages = [_tool_msg(f"tc{i}", byte_size=600) for i in range(3)]
    out = apply_tool_image_budget(messages, max_count=10, max_bytes=1300)
    assert [bool(m.images) for m in out] == [False, True, True]


def test_budget_noop_within_limits() -> None:
    from matmaster.types.message_normalization import apply_tool_image_budget

    messages = [_tool_msg("tc0"), _tool_msg("tc1")]
    out = apply_tool_image_budget(messages)
    assert out[0] is messages[0] and out[1] is messages[1]


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
