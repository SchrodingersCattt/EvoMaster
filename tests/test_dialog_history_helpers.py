"""Tests for playground.mat_master.core.dialog_history_helpers."""

from playground.mat_master.core.dialog_history_helpers import (
    build_mat_master_discovery_task,
    trim_events_for_dialog_history,
)


def test_trim_empty():
    assert trim_events_for_dialog_history([], 10) == []


def test_trim_removes_trailing_user_query():
    ev = [{'source': 'User', 'type': 'query', 'content': 'hi'}]
    assert trim_events_for_dialog_history(ev, 10) == []


def test_trim_keeps_when_last_not_user_query():
    ev = [{'source': 'Assistant', 'type': 'reply', 'content': 'x'}]
    assert trim_events_for_dialog_history(ev, 10) == ev


def test_trim_query_without_user_source_not_stripped():
    ev = [{'source': 'System', 'type': 'query', 'content': 'x'}]
    assert trim_events_for_dialog_history(ev, 10) == ev


def test_trim_max_events_tail():
    ev = [{'i': i} for i in range(5)]
    out = trim_events_for_dialog_history(ev, 3)
    assert len(out) == 3
    assert [x['i'] for x in out] == [2, 3, 4]


def test_trim_user_query_then_tail():
    base = [{'i': i} for i in range(4)]
    base.append({'source': 'User', 'type': 'query', 'content': 'q'})
    out = trim_events_for_dialog_history(base, 3)
    assert len(out) == 3
    assert [x['i'] for x in out] == [1, 2, 3]


def test_build_mat_master_discovery_task():
    t = build_mat_master_discovery_task(
        'tid', 'hello', [{'role': 'user', 'content': 'a'}]
    )
    assert t.task_id == 'tid'
    assert t.task_type == 'discovery'
    assert t.description == 'hello'
    assert t.meta['dialog_history'] == [{'role': 'user', 'content': 'a'}]
