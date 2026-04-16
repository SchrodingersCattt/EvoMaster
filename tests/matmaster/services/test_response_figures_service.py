from __future__ import annotations

import logging

from matmaster.types.events import ToolResultEvent
from src.services.response_figures_service import ResponseFiguresAccumulator


def _figure(
    figure_id: str,
    *,
    asset_url: str | None = None,
    caption: str | None = None,
    source_tool_call_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        'figure_id': figure_id,
        'asset_url': asset_url or f'https://oss.example/{figure_id}.png',
        'caption': caption or figure_id,
        'importance': 'primary',
        'placement_hint': 'sidebar_only',
    }
    if source_tool_call_id is not None:
        payload['source_tool_call_id'] = source_tool_call_id
    return payload


def _tool_result(
    call_id: str,
    figures: list[dict[str, object]] | object,
    *,
    spawn_id: str | None = None,
) -> ToolResultEvent:
    return ToolResultEvent(
        source='MatMaster',
        spawn_id=spawn_id,
        call_id=call_id,
        tool_name='Bash',
        result='done',
        payload={'figures': figures},
    )


def test_snapshot_requires_mark_emitted_before_repeats_are_suppressed() -> None:
    acc = ResponseFiguresAccumulator()

    changed = acc.add_tool_result(_tool_result('call-band', [_figure('band')]))
    assert changed is True

    first = acc.build_snapshot_event_if_dirty()
    assert first is not None
    assert [fig.figure_id for fig in first.figures] == ['band']

    repeated_before_commit = acc.build_snapshot_event_if_dirty()
    assert repeated_before_commit is not None
    assert [fig.figure_id for fig in repeated_before_commit.figures] == ['band']

    acc.mark_snapshot_emitted()
    assert acc.build_snapshot_event_if_dirty() is None


def test_later_tool_result_emits_complete_snapshot_with_previous_figures() -> None:
    acc = ResponseFiguresAccumulator()

    assert acc.add_tool_result(_tool_result('call-band', [_figure('band')])) is True
    first = acc.build_snapshot_event_if_dirty()
    assert first is not None
    acc.mark_snapshot_emitted()

    assert acc.add_tool_result(_tool_result('call-dos', [_figure('dos')])) is True
    second = acc.build_snapshot_event_if_dirty()

    assert second is not None
    assert [fig.figure_id for fig in second.figures] == ['band', 'dos']
    assert [fig.source_tool_call_id for fig in second.figures] == [
        'call-band',
        'call-dos',
    ]


def test_duplicate_figure_id_keeps_first_and_logs_warning(caplog) -> None:
    acc = ResponseFiguresAccumulator()
    caplog.set_level(logging.WARNING)

    assert acc.add_tool_result(_tool_result('call-band', [_figure('band')])) is True
    assert acc.add_tool_result(_tool_result('call-band-new', [_figure('band')])) is False

    snapshot = acc.build_snapshot_event_if_dirty()
    assert snapshot is not None
    assert [fig.source_tool_call_id for fig in snapshot.figures] == ['call-band']
    assert 'Ignoring duplicate response figure_id=band' in caplog.text
    assert 'first_tool_call=call-band' in caplog.text
    assert 'duplicate_tool_call=call-band-new' in caplog.text


def test_ignores_child_spawn_invalid_payload_and_non_list_figures() -> None:
    acc = ResponseFiguresAccumulator()

    assert (
        acc.add_tool_result(
            _tool_result('call-child', [_figure('child')], spawn_id='sub-1')
        )
        is False
    )
    assert (
        acc.add_tool_result(
            _tool_result(
                'call-invalid',
                [{'figure_id': 'broken', 'asset_url': 'https://oss.example/broken.png'}],
            )
        )
        is False
    )
    assert acc.add_tool_result(_tool_result('call-non-list', {'bad': 'shape'})) is False
    assert acc.build_snapshot_event_if_dirty() is None
