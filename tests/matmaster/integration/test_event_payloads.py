"""Tests for public event payload adaptation helpers."""

from __future__ import annotations

from matmaster.integration.event_payloads import (
    _normalize_public_source,
    _public_content_for_event,
)


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


class TestToolResultPayloadMapping:
    """ESIN-07: ToolResult.payload -> SSE info field mapping."""

    def test_tool_result_payload_maps_to_info(self) -> None:
        """ESIN-07: ToolResult.payload maps to SSE 'info' field."""
        result = _public_content_for_event('tool_result', {
            'call_id': 'call-1',
            'tool_name': 'bash',
            'result': 'output text',
            'status': 'success',
            'payload': {'exit_code': 0, 'cwd': '/tmp'},
        })
        assert result['info'] == {'exit_code': 0, 'cwd': '/tmp'}
        assert result['status'] == 'success'
        assert result['name'] == 'bash'

    def test_tool_result_none_payload_maps_to_empty_info(self) -> None:
        """ESIN-07: None payload -> empty info dict."""
        result = _public_content_for_event('tool_result', {
            'call_id': 'call-2',
            'tool_name': 'read',
            'result': 'file content',
            'status': 'success',
            'payload': None,
        })
        assert result['info'] == {}

    def test_tool_result_missing_payload_maps_to_empty_info(self) -> None:
        """ESIN-07: Missing payload key -> empty info dict."""
        result = _public_content_for_event('tool_result', {
            'call_id': 'call-3',
            'tool_name': 'write',
            'result': 'written',
            'status': 'success',
        })
        assert result['info'] == {}

    def test_tool_result_payload_with_meta_flags(self) -> None:
        """ESIN-07: payload with auto_save/summarize flags preserved in info."""
        result = _public_content_for_event('tool_result', {
            'call_id': 'call-4',
            'tool_name': 'write_file',
            'result': 'saved',
            'status': 'success',
            'payload': {'auto_save': True, 'file': '/tmp/out.txt'},
        })
        assert result['info']['auto_save'] is True
        assert result['info']['file'] == '/tmp/out.txt'

    def test_tool_result_info_key_from_model_dump(self) -> None:
        """ESIN-07: ToolResultEvent.model_dump() uses 'info' key, must map correctly."""
        # When ToolResultEvent.model_dump() is used, 'info' key is in the dict
        # (not 'payload'). This tests the generator event path.
        result = _public_content_for_event('tool_result', {
            'call_id': 'call-5',
            'tool_name': 'bash',
            'result': 'output',
            'status': 'success',
            'info': {'exit_code': 0, 'signal': None},
        })
        assert result['info'] == {'exit_code': 0, 'signal': None}


class TestSourceNormalization:
    """ESIN-06: Source normalization for generator events."""

    def test_source_normalization_agent_to_matmaster(self) -> None:
        """ESIN-06: Internal sources normalize to MatMaster."""
        assert _normalize_public_source('agent') == 'MatMaster'
        assert _normalize_public_source('direct') == 'MatMaster'
        assert _normalize_public_source('') == 'MatMaster'

    def test_source_normalization_preserves_subtype(self) -> None:
        """ESIN-06: MatMaster:subtype preserved."""
        assert _normalize_public_source('MatMaster:code') == 'MatMaster:code'
        assert _normalize_public_source('MatMaster:sub1') == 'MatMaster:sub1'

    def test_source_normalization_system_passthrough(self) -> None:
        """ESIN-06: System/User pass through unchanged."""
        assert _normalize_public_source('System') == 'System'
        assert _normalize_public_source('User') == 'User'
