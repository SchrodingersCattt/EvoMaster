"""SSEHandler._should_skip 的 mode-敏感过滤规则。

验证 complete ThoughtEvent 在 direct 和 planner 模式下都被视为
persist-only（不推送到 SSE）；同时验证 streaming thought 仍会推送。
"""

from __future__ import annotations

import pytest

from matmaster.integration.sse_handler import SSEHandler
from matmaster.types.events import ResponseEvent, ThoughtEvent


def _make_handler(mode: str) -> SSEHandler:
    """构造一个不关心发送目的地的 SSEHandler，只用其 _should_skip 同步方法。"""
    return SSEHandler(
        send_cb=lambda payload: None,
        session_id='sess-test',
        task_id='task-test',
        invocation_id='inv-test',
        mode=mode,
    )


@pytest.mark.parametrize('mode', ['direct', 'planner'])
def test_complete_thought_filtered_for_supported_modes(mode: str) -> None:
    """direct 和 planner 模式下 non-streaming complete thought 均 persist-only。"""
    handler = _make_handler(mode)
    ev = ThoughtEvent(
        source='MatMaster',
        content='final reasoning',
        stream_state='complete',
    )
    assert handler._should_skip(ev) is True


@pytest.mark.parametrize('mode', ['direct', 'planner'])
def test_streaming_thought_pushed_for_supported_modes(mode: str) -> None:
    """streaming thought 在 direct / planner 模式下都应该被推送（不过滤）。"""
    handler = _make_handler(mode)
    ev = ThoughtEvent(
        source='MatMaster',
        content='partial',
        stream_state='streaming',
    )
    # 规则 1（source=='Planner' + streaming）在归一化后 source 是 MatMaster,
    # 所以不会触发；规则 2（mode 判断）只过滤非 streaming。这里都不过滤。
    assert handler._should_skip(ev) is False


def test_complete_response_always_filtered() -> None:
    """ResponseEvent 的 stream_state='complete' 由顶层 isinstance 检查吃掉，
    与 mode 无关；这里是对现状的回归保险。"""
    handler = _make_handler('planner')
    ev = ResponseEvent(
        source='MatMaster',
        content='aggregated reply',
        stream_state='complete',
    )
    assert handler._should_skip(ev) is True
