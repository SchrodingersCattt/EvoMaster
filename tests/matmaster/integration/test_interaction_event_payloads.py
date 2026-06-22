"""Tests for public interaction event payload adaptation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from matmaster.integration.event_payloads import (
    PublicInteractionSseEnvelope,
    _public_content_for_event,
    build_public_sse_payload_from_bus_dump,
)
from matmaster.types.events import (
    InteractionReplyEvent,
    InteractionRequestEvent,
    InteractionTimeoutEvent,
)


class TestInteractionPayloadMapping:
    """Generic interaction event family SSE payload mapping."""

    _SSE_FIXTURES_DIR = Path(__file__).resolve().parents[2] / 'fixtures' / 'sse'
    _FIXED_TS = datetime(2026, 6, 21, 10, 15, 30, 123456)

    def _load_sample(self, name: str) -> dict:
        return json.loads((self._SSE_FIXTURES_DIR / name).read_text())

    def test_interaction_request_maps_all_fields(self) -> None:
        result = _public_content_for_event(
            'interaction_request',
            {
                'type': 'interaction_request',
                'kind': 'ask_question',
                'request_id': 'aq_1',
                'task_id': 'task_1',
                'expires_at': '2026-06-18T00:00:00+00:00',
                'payload': {
                    'questions': [{'question': 'Which?', 'header': 'H', 'options': []}],
                    'metadata': {'source': 'tool'},
                    'origin': 'tool:AskQuestion',
                    'preview_format': 'markdown',
                },
            },
        )
        assert result['kind'] == 'ask_question'
        assert result['request_id'] == 'aq_1'
        assert result['task_id'] == 'task_1'
        assert len(result['payload']['questions']) == 1
        assert result['payload']['origin'] == 'tool:AskQuestion'
        assert result['payload']['preview_format'] == 'markdown'

    def test_interaction_reply_maps_payload(self) -> None:
        result = _public_content_for_event(
            'interaction_reply',
            {
                'type': 'interaction_reply',
                'kind': 'ask_question',
                'request_id': 'aq_1',
                'payload': {
                    'answers': {'Q1': 'A1'},
                    'annotations': {'Q1': {'notes': 'extra'}},
                },
            },
        )
        assert result['kind'] == 'ask_question'
        assert result['payload']['answers'] == {'Q1': 'A1'}
        assert result['payload']['annotations'] == {'Q1': {'notes': 'extra'}}

    def test_interaction_timeout_maps_reason(self) -> None:
        result = _public_content_for_event(
            'interaction_timeout',
            {
                'type': 'interaction_timeout',
                'kind': 'ask_question',
                'request_id': 'aq_1',
                'reason': 'timeout',
            },
        )
        assert result['kind'] == 'ask_question'
        assert result['request_id'] == 'aq_1'
        assert result['reason'] == 'timeout'

    def test_request_envelope_matches_golden_sample(self) -> None:
        event = InteractionRequestEvent(
            source='MatMaster',
            kind='submit_review',
            request_id='sr_1',
            task_id='t1',
            expires_at='2026-06-21T11:00:00+00:00',
            payload={
                'tool_name': 'Bohrium',
                'tool_call_id': 'call_1',
                'review_draft_arguments': {
                    'input_dir': '/share/c',
                    'cmd': 'sleep 180 > log 2>&1',
                },
                'editable_fields': ['input_dir', 'cmd'],
                'input_dir': '/share/c',
            },
            timestamp=self._FIXED_TS,
        )
        out = build_public_sse_payload_from_bus_dump(
            event.model_dump(mode='json'),
            session_id='s1',
            task_id='t1',
            invocation_id='inv1',
            spawn_id=None,
        )
        PublicInteractionSseEnvelope.model_validate(out)
        assert out == self._load_sample('interaction_request.sample.json')

    def test_reply_envelope_matches_golden_sample(self) -> None:
        event = InteractionReplyEvent(
            source='User',
            kind='submit_review',
            request_id='sr_1',
            payload={'decision': 'submit', 'disable_future_confirmation': True},
            timestamp=self._FIXED_TS,
        )
        out = build_public_sse_payload_from_bus_dump(
            event.model_dump(mode='json'),
            session_id='s1',
            task_id='t1',
            invocation_id='inv1',
            spawn_id=None,
        )
        PublicInteractionSseEnvelope.model_validate(out)
        assert out == self._load_sample('interaction_reply.sample.json')

    def test_timeout_envelope_matches_golden_sample(self) -> None:
        event = InteractionTimeoutEvent(
            source='MatMaster',
            kind='submit_review',
            request_id='sr_1',
            reason='timeout',
            timestamp=self._FIXED_TS,
        )
        out = build_public_sse_payload_from_bus_dump(
            event.model_dump(mode='json'),
            session_id='s1',
            task_id='t1',
            invocation_id='inv1',
            spawn_id=None,
        )
        PublicInteractionSseEnvelope.model_validate(out)
        assert out == self._load_sample('interaction_timeout.sample.json')

    def test_envelope_rejects_legacy_flat_reply(self) -> None:
        flat = {
            'source': 'User',
            'type': 'interaction_reply',
            'kind': 'submit_review',
            'request_id': 'sr_1',
            'payload': {'decision': 'submit'},
            'session_id': 's1',
            'task_id': 't1',
            'invocation_id': 'inv1',
        }
        with pytest.raises(ValidationError):
            PublicInteractionSseEnvelope.model_validate(flat)
