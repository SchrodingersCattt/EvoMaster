"""Performance baseline for E3 message pipeline optimization.

Uses stdlib time.perf_counter (no pytest-benchmark dependency).
This file lives under benchmarks/ which is NOT collected by the default
test path (pytest.ini sets testpaths = tests). Run explicitly:
    uv run pytest benchmarks/test_message_pipeline_perf.py -v -s
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from matmaster.types.message_normalization import (
    canonicalize_messages_for_provider,
    normalize_and_validate_openai_messages,
)
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _build_fixture(num_turns: int, calls_per_turn: int, arg_size_bytes: int):
    """Build a legal message sequence of num_turns x calls_per_turn tool calls.

    Each tool_call's arguments is a dict containing one string of approximately
    arg_size_bytes. ToolCallData instances are fresh; callers should rebuild
    the fixture between pure-path and pipeline-path runs to avoid cross-run
    arguments_json cache pollution.
    """
    big_payload = "x" * arg_size_bytes
    messages: list = [
        SystemMessage(content="You are a helpful assistant."),
        UserMessage(content="Help me with this task."),
    ]
    for turn in range(num_turns):
        tool_calls = [
            ToolCallData(
                id=f"call_{turn}_{i}",
                name="search",
                arguments={"query": f"q{turn}_{i}", "payload": big_payload},
            )
            for i in range(calls_per_turn)
        ]
        messages.append(
            AssistantMessage(
                content=None,
                tool_calls=tool_calls,
                reasoning_content=None,
            )
        )
        for tc in tool_calls:
            messages.append(
                ToolMessage(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    content=f"result for {tc.id}",
                )
            )
    return messages


FIXTURE_CONFIGS = [
    ("small", 10, 3, 500),
    ("medium", 30, 3, 2048),
    ("large", 50, 5, 5120),
]


def _run_pure_path_per_turn(messages: list) -> tuple[float, int]:
    """Simulate the main loop: walk prefix-by-prefix, one turn at a time.

    Each iteration runs canonicalize + normalize + validate on the prefix.
    Returns (wall_time_seconds, json_dumps_call_count).
    """
    dumps_count = 0
    orig_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        nonlocal dumps_count
        dumps_count += 1
        return orig_dumps(*args, **kwargs)

    # Simulate prefix growth: each boundary ends after assistant + tool results.
    boundaries = [2]
    i = 2
    while i < len(messages):
        if isinstance(messages[i], AssistantMessage) and messages[i].tool_calls:
            i += 1 + len(messages[i].tool_calls)
            boundaries.append(i)
        else:
            i += 1

    start = time.perf_counter()
    with patch("json.dumps", side_effect=counting_dumps):
        for end in boundaries:
            prefix = messages[:end]
            normalize_and_validate_openai_messages(
                canonicalize_messages_for_provider(prefix)
            )
    elapsed = time.perf_counter() - start
    return elapsed, dumps_count


@pytest.mark.parametrize("label,num_turns,calls,arg_size", FIXTURE_CONFIGS)
def test_pure_path_baseline(label: str, num_turns: int, calls: int, arg_size: int):
    messages = _build_fixture(num_turns, calls, arg_size)
    elapsed, dumps_count = _run_pure_path_per_turn(messages)
    print(
        f"\n[BASELINE pure] fixture={label} "
        f"turns={num_turns} calls/turn={calls} arg_size={arg_size}B "
        f"wall={elapsed * 1000:.2f}ms json.dumps_calls={dumps_count}"
    )
