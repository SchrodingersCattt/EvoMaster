"""Tests for playground.mat_master.core.ask_human_helpers."""

from playground.mat_master.core.ask_human_helpers import (
    attach_ask_human_on_agent,
    get_ask_human_config_dict,
)


def test_get_ask_human_empty():
    assert get_ask_human_config_dict({}) == {}


def test_get_ask_human_from_mat_master():
    cfg = {
        'mat_master': {
            'ask_human': {'timeout_seconds': 42},
        },
    }
    assert get_ask_human_config_dict(cfg) == {'timeout_seconds': 42}


def test_get_ask_human_invalid_nested_returns_empty():
    assert get_ask_human_config_dict({'mat_master': {'ask_human': 'bad'}}) == {}


def test_attach_skips_when_queue_none():
    class A:
        pass

    a = A()
    attach_ask_human_on_agent(a, None, lambda *a, **k: None, {})
    assert not hasattr(a, '_ask_human_queue')
