"""Tests for public event payload adaptation helpers."""

from __future__ import annotations

from matmaster.integration.event_payloads import _public_content_for_event


class TestPublicContentForEvent:
    """_public_content_for_event covers every persisted public event family."""

    def test_run_result_extracts_final_content(self) -> None:
        payload = {
            'type': 'run_result',
            'source': 'Agent',
            'status': 'completed',
            'reason': 'natural',
            'final_content': 'here are your files',
        }

        assert _public_content_for_event('run_result', payload) == {
            'content': 'here are your files',
            'status': 'completed',
            'reason': 'natural',
        }

    def test_finish_alias_uses_same_shape(self) -> None:
        payload = {
            'type': 'finish',
            'source': 'Agent',
            'status': 'completed',
            'reason': '',
            'final_content': 'legacy done',
        }

        assert _public_content_for_event('finish', payload) == {
            'content': 'legacy done',
            'status': 'completed',
            'reason': '',
        }

    def test_assistant_state_returns_state_dict(self) -> None:
        state = {'role': 'assistant', 'content': 'hi', 'tool_calls': []}
        payload = {'type': 'assistant_state', 'source': 'Agent', 'state': state}

        assert _public_content_for_event('assistant_state', payload) == state

    def test_skill_hit_returns_skill_name(self) -> None:
        payload = {'type': 'skill_hit', 'source': 'Agent', 'skill_name': 'search'}

        assert _public_content_for_event('skill_hit', payload) == {
            'skill_name': 'search'
        }

    def test_cancelled_returns_reason(self) -> None:
        payload = {'type': 'cancelled', 'source': 'System', 'reason': 'user stop'}

        assert _public_content_for_event('cancelled', payload) == {
            'reason': 'user stop'
        }

    def test_confirmation_timeout_returns_question_and_default(self) -> None:
        payload = {
            'type': 'confirmation_timeout',
            'source': 'System',
            'question': 'Proceed?',
            'default_reply': 'yes',
        }

        assert _public_content_for_event('confirmation_timeout', payload) == {
            'question': 'Proceed?',
            'default_reply': 'yes',
        }

    def test_exp_run_returns_exp_name(self) -> None:
        payload = {'type': 'exp_run', 'source': 'System', 'exp_name': 'vasp-relax'}

        assert _public_content_for_event('exp_run', payload) == {
            'exp_name': 'vasp-relax'
        }

    def test_response_uses_content_field(self) -> None:
        payload = {'type': 'response', 'source': 'Agent', 'content': 'hello'}

        assert _public_content_for_event('response', payload) == 'hello'

    def test_unknown_type_without_content_extracts_business_fields(self) -> None:
        payload = {
            'type': 'new_future_event',
            'source': 'System',
            'timestamp': '2026-03-24T00:00:00',
            'custom_data': {'key': 'value'},
            'detail': 'info',
        }

        assert _public_content_for_event('new_future_event', payload) == {
            'custom_data': {'key': 'value'},
            'detail': 'info',
        }

    def test_unknown_type_with_content_keeps_existing_behavior(self) -> None:
        payload = {'type': 'future_event', 'source': 'System', 'content': 'data'}

        assert _public_content_for_event('future_event', payload) == 'data'
