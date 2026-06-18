"""Tests for public event payload adaptation helpers."""

from __future__ import annotations

from matmaster.integration.event_payloads import (
    _normalize_public_source,
    _public_content_for_event,
    build_public_sse_payload_from_bus_dump,
    normalize_response_sse_payload,
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

    def test_run_result_none_final_content_stays_empty_public_content(self) -> None:
        payload = {
            'type': 'run_result',
            'source': 'Agent',
            'status': 'failed',
            'reason': 'invalid_finish',
            'final_content': None,
        }

        assert _public_content_for_event('run_result', payload) == {
            'content': '',
            'status': 'failed',
            'reason': 'invalid_finish',
        }

    def test_run_result_preserves_finish_detail_in_public_content(self) -> None:
        detail = {
            'kind': 'output_length_exceeded',
            'provider_finish_reason': 'length',
            'message': 'Model output was truncated by the provider output-token limit.',
        }
        payload = {
            'type': 'run_result',
            'source': 'Agent',
            'status': 'failed',
            'reason': 'invalid_finish',
            'final_content': None,
            'finish_detail': detail,
        }

        content = _public_content_for_event('run_result', payload)

        assert content == {
            'content': '',
            'status': 'failed',
            'reason': 'invalid_finish',
            'finish_detail': detail,
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

        assert _public_content_for_event('assistant_state', payload) == {'state': state}

    def test_assistant_state_preserves_finish_detail_in_public_content(self) -> None:
        state = {'role': 'assistant', 'content': None, 'tool_calls': []}
        detail = {
            'kind': 'output_length_exceeded',
            'provider_finish_reason': 'length',
            'message': 'Model output was truncated by the provider output-token limit.',
            'has_tool_calls': True,
            'truncation_risk': True,
        }
        payload = {
            'type': 'assistant_state',
            'source': 'Agent',
            'state': state,
            'finish_detail': detail,
        }

        assert _public_content_for_event('assistant_state', payload) == {
            'state': state,
            'finish_detail': detail,
        }

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

    def test_exp_run_returns_exp_name(self) -> None:
        payload = {'type': 'exp_run', 'source': 'System', 'exp_name': 'vasp-relax'}

        assert _public_content_for_event('exp_run', payload) == {
            'exp_name': 'vasp-relax'
        }

    def test_compaction_running_payload_maps_public_fields(self) -> None:
        payload = {
            'type': 'compaction',
            'source': 'context_compactor',
            'compaction_id': 'task-1:root:1',
            'status': 'running',
            'phase': 'runtime',
            'trigger_tokens': 950,
        }

        assert _public_content_for_event('compaction', payload) == {
            'compaction_id': 'task-1:root:1',
            'status': 'running',
            'phase': 'runtime',
            'trigger_tokens': 950,
        }

    def test_compaction_complete_payload_maps_checkpoint_fields(self) -> None:
        payload = {
            'type': 'compaction',
            'source': 'context_compactor',
            'compaction_id': 'task-1:root:2',
            'status': 'complete',
            'phase': 'runtime',
            'strategy': 'summary',
            'durability': 'durable',
            'checkpoint_written': True,
            'covered_until_event_id': 88,
            'retained_turns': 3,
        }

        assert _public_content_for_event('compaction', payload) == {
            'compaction_id': 'task-1:root:2',
            'status': 'complete',
            'phase': 'runtime',
            'strategy': 'summary',
            'durability': 'durable',
            'checkpoint_written': True,
            'covered_until_event_id': 88,
            'retained_turns': 3,
        }

    def test_compaction_public_content_includes_usage_fields(self) -> None:
        content = _public_content_for_event(
            'compaction',
            {
                'compaction_id': 'root:1',
                'status': 'complete',
                'phase': 'runtime',
                'turn_usage': {'prompt_tokens': 40},
                'total_usage': {'prompt_tokens': 55},
            },
        )

        assert content['turn_usage'] == {'prompt_tokens': 40}
        assert content['total_usage'] == {'prompt_tokens': 55}

        running = _public_content_for_event(
            'compaction',
            {
                'compaction_id': 'root:1',
                'status': 'running',
                'phase': 'runtime',
                'turn_usage': None,
                'total_usage': None,
            },
        )
        assert 'turn_usage' not in running
        assert 'total_usage' not in running

    def test_response_uses_content_field(self) -> None:
        payload = {'type': 'response', 'source': 'Agent', 'content': 'hello'}

        assert _public_content_for_event('response', payload) == 'hello'

    def test_thought_with_model_returns_plain_content(self) -> None:
        payload = {
            'type': 'thought',
            'source': 'Agent',
            'content': 'reasoning',
            'model': 'matmaster/qwen3.7-max',
            'model_profile': 'matmaster/qwen3.7-max',
            'model_route': 'matmaster/qwen3.7-max',
        }

        assert _public_content_for_event('thought', payload) == 'reasoning'

    def test_thought_without_usage_keeps_plain_content(self) -> None:
        payload = {'type': 'thought', 'source': 'Agent', 'content': 'delta'}

        assert _public_content_for_event('thought', payload) == 'delta'

    def test_thought_with_usage_returns_structured_content(self) -> None:
        payload = {
            'type': 'thought',
            'source': 'Agent',
            'content': 'reasoning text',
            'stream_state': 'complete',
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }
        assert _public_content_for_event('thought', payload) == {
            'content': 'reasoning text',
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }

    def test_tool_call_with_usage_projects_usage_fields(self) -> None:
        payload = {
            'type': 'tool_call',
            'source': 'Agent',
            'call_id': 'call_1',
            'tool_name': 'Bash',
            'arguments': {'cmd': 'ls'},
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }
        assert _public_content_for_event('tool_call', payload) == {
            'id': 'call_1',
            'call_id': 'call_1',
            'name': 'Bash',
            'args': {'cmd': 'ls'},
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }

    def test_tool_call_without_usage_keeps_minimal_shape(self) -> None:
        payload = {
            'type': 'tool_call',
            'source': 'Agent',
            'call_id': 'call_1',
            'tool_name': 'Bash',
            'arguments': {'cmd': 'ls'},
            'turn_index': None,
            'turn_usage': {},
            'total_usage': {},
            'usage_vendor': None,
        }
        assert _public_content_for_event('tool_call', payload) == {
            'id': 'call_1',
            'call_id': 'call_1',
            'name': 'Bash',
            'args': {'cmd': 'ls'},
        }

    def test_tool_result_does_not_project_usage(self) -> None:
        payload = {
            'type': 'tool_result',
            'source': 'Agent',
            'call_id': 'call-6',
            'tool_name': 'bash',
            'result': 'output',
            'status': 'success',
            'turn_index': 1,
            'turn_usage': {'prompt_tokens': 10},
            'total_usage': {'prompt_tokens': 10},
        }
        assert _public_content_for_event('tool_result', payload) == {
            'id': 'call-6',
            'call_id': 'call-6',
            'name': 'bash',
            'result': 'output',
            'status': 'success',
            'info': {},
        }

    def test_response_with_usage_returns_structured_content(self) -> None:
        payload = {
            'type': 'response',
            'source': 'Agent',
            'content': 'answer',
            'stream_state': 'complete',
            'stream_id': 's1',
            'turn_index': 2,
            'turn_usage': {'prompt_tokens': 10, 'completion_tokens': 4},
            'total_usage': {'prompt_tokens': 30, 'completion_tokens': 9},
            'usage_vendor': {'inputTokens': 10, 'outputTokens': 4},
        }
        assert _public_content_for_event('response', payload) == {
            'content': 'answer',
            'turn_index': 2,
            'stream_id': 's1',
            'turn_usage': {'prompt_tokens': 10, 'completion_tokens': 4},
            'total_usage': {'prompt_tokens': 30, 'completion_tokens': 9},
            'usage_vendor': {'inputTokens': 10, 'outputTokens': 4},
        }

    def test_response_with_model_returns_structured_content(self) -> None:
        payload = {
            'type': 'response',
            'source': 'Agent',
            'content': 'answer',
            'model': 'matmaster/qwen3.7-max',
            'model_profile': 'matmaster/qwen3.7-max',
            'model_route': 'matmaster/qwen3.7-max',
        }

        assert _public_content_for_event('response', payload) == {
            'content': 'answer',
            'model': 'matmaster/qwen3.7-max',
            'model_profile': 'matmaster/qwen3.7-max',
            'model_route': 'matmaster/qwen3.7-max',
        }

    def test_run_result_public_content_includes_usage(self) -> None:
        payload = {
            'type': 'run_result',
            'source': 'Agent',
            'status': 'completed',
            'reason': 'natural',
            'final_content': 'done',
            'num_turns': 2,
            'usage': {'prompt_tokens': 20, 'completion_tokens': 6},
            'usage_vendor_by_turn': [{'inputTokens': 20, 'outputTokens': 6}],
        }
        assert _public_content_for_event('run_result', payload)['usage'] == {
            'prompt_tokens': 20,
            'completion_tokens': 6,
        }

    def test_run_result_public_content_omits_model_identity(self) -> None:
        payload = {
            'type': 'run_result',
            'source': 'Agent',
            'status': 'completed',
            'reason': 'natural',
            'final_content': 'done',
            'model': 'matmaster/qwen3.7-max',
            'model_profile': 'matmaster/qwen3.7-max',
            'model_route': 'matmaster/qwen3.7-max',
        }

        assert _public_content_for_event('run_result', payload) == {
            'content': 'done',
            'status': 'completed',
            'reason': 'natural',
        }

    def test_assistant_state_public_content_includes_model(self) -> None:
        state = {'role': 'assistant', 'content': None, 'tool_calls': []}
        payload = {
            'type': 'assistant_state',
            'source': 'Agent',
            'state': state,
            'model': 'matmaster/qwen3.7-max',
            'model_profile': 'matmaster/qwen3.7-max',
            'model_route': 'matmaster/qwen3.7-max',
        }

        assert _public_content_for_event('assistant_state', payload) == {
            'state': state,
            'model': 'matmaster/qwen3.7-max',
            'model_profile': 'matmaster/qwen3.7-max',
            'model_route': 'matmaster/qwen3.7-max',
        }

    def test_failed_run_result_preserves_usage_and_finish_detail(self) -> None:
        detail = {
            'kind': 'reasoning_only',
            'message': 'Model produced reasoning but no visible content.',
            'last_turn_usage': {'prompt_tokens': 10, 'completion_tokens': 2},
        }
        content = _public_content_for_event(
            'run_result',
            {
                'status': 'failed',
                'reason': 'invalid_finish',
                'final_content': None,
                'num_turns': 1,
                'usage': {'prompt_tokens': 10, 'completion_tokens': 2},
                'finish_detail': detail,
            },
        )
        assert content['usage'] == {'prompt_tokens': 10, 'completion_tokens': 2}
        assert content['finish_detail'] == detail

    def test_assistant_state_mapping_preserves_turn_index(self) -> None:
        state = {'role': 'assistant', 'content': None, 'tool_calls': []}
        assistant = _public_content_for_event(
            'assistant_state',
            {
                'state': state,
                'turn_index': 1,
                'turn_usage': {'prompt_tokens': 10},
                'total_usage': {'prompt_tokens': 10},
            },
        )
        assert assistant['turn_index'] == 1

    def test_tool_result_public_content_carries_images(self) -> None:
        payload = {
            "call_id": "tc1",
            "tool_name": "Read",
            "result": "Read image: a.png",
            "status": "success",
            "payload": {},
            "images": [
                {
                    "url": "data:image/png;base64,aGVsbG8=",
                    "mime_type": "image/png",
                    "detail": None,
                }
            ],
        }
        out = _public_content_for_event("tool_result", payload)
        assert out["images"][0]["url"] == "data:image/png;base64,aGVsbG8="

    def test_tool_result_public_content_no_images_key_when_empty(self) -> None:
        payload = {
            "call_id": "tc1",
            "tool_name": "Read",
            "result": "ok",
            "status": "success",
            "payload": {},
        }
        out = _public_content_for_event("tool_result", payload)
        assert "images" not in out

    def test_structured_response_content_is_unpacked_for_sse(self) -> None:
        payload = {
            'source': 'MatMaster',
            'type': 'response',
            'content': {
                'content': 'answer',
                'turn_index': 3,
                'turn_usage': {'total_tokens': 12},
                'total_usage': {'total_tokens': 30},
                'usage_vendor': {'inputTokens': 10, 'outputTokens': 2},
            },
            'session_id': 'sess',
            'task_id': 'task',
            'spawn_id': None,
        }
        normalized = normalize_response_sse_payload(payload)
        assert normalized['content'] == 'answer'
        assert normalized['turn_index'] == 3
        assert normalized['turn_usage'] == {'total_tokens': 12}

    def test_structured_thought_content_strips_model_identity_for_sse(self) -> None:
        payload = {
            'source': 'MatMaster',
            'type': 'thought',
            'content': {
                'content': 'thinking',
                'model': 'matmaster/qwen3.7-max',
                'model_profile': 'matmaster/qwen3.7-max',
                'model_route': 'matmaster/qwen3.7-max',
            },
            'session_id': 'sess',
            'task_id': 'task',
            'spawn_id': None,
        }

        normalized = normalize_response_sse_payload(payload)

        assert normalized['content'] == 'thinking'
        assert 'model' not in normalized
        assert 'model_profile' not in normalized
        assert 'model_route' not in normalized

    def test_structured_thought_content_is_unpacked_for_sse(self) -> None:
        payload = {
            'source': 'MatMaster',
            'type': 'thought',
            'content': {
                'content': 'reasoning text',
                'turn_index': 3,
                'turn_usage': {'total_tokens': 12},
                'total_usage': {'total_tokens': 30},
                'usage_vendor': {'inputTokens': 10, 'outputTokens': 2},
            },
            'session_id': 'sess',
            'task_id': 'task',
            'spawn_id': None,
        }
        normalized = normalize_response_sse_payload(payload)
        assert normalized['content'] == 'reasoning text'
        assert normalized['turn_index'] == 3
        assert normalized['turn_usage'] == {'total_tokens': 12}
        assert normalized['total_usage'] == {'total_tokens': 30}

    def test_response_figures_payload_maps_to_public_content(self) -> None:
        payload = {
            'type': 'response_figures',
            'source': 'System',
            'figures': [
                {
                    'figure_id': 'band',
                    'asset_url': 'https://oss.example/band.png',
                    'caption': 'band',
                    'importance': 'primary',
                    'placement_hint': 'sidebar_only',
                    'source_tool_call_id': 'call-band',
                }
            ],
        }

        assert _public_content_for_event('response_figures', payload) == {
            'figures': payload['figures']
        }

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
        result = _public_content_for_event(
            'tool_result',
            {
                'call_id': 'call-1',
                'tool_name': 'bash',
                'result': 'output text',
                'status': 'success',
                'payload': {'exit_code': 0, 'cwd': '/tmp'},
            },
        )
        assert result['info'] == {'exit_code': 0, 'cwd': '/tmp'}
        assert result['status'] == 'success'
        assert result['name'] == 'bash'

    def test_tool_result_none_payload_maps_to_empty_info(self) -> None:
        """ESIN-07: None payload -> empty info dict."""
        result = _public_content_for_event(
            'tool_result',
            {
                'call_id': 'call-2',
                'tool_name': 'read',
                'result': 'file content',
                'status': 'success',
                'payload': None,
            },
        )
        assert result['info'] == {}

    def test_tool_result_missing_payload_maps_to_empty_info(self) -> None:
        """ESIN-07: Missing payload key -> empty info dict."""
        result = _public_content_for_event(
            'tool_result',
            {
                'call_id': 'call-3',
                'tool_name': 'write',
                'result': 'written',
                'status': 'success',
            },
        )
        assert result['info'] == {}

    def test_tool_result_payload_with_meta_flags(self) -> None:
        """ESIN-07: payload with auto_save/summarize flags preserved in info."""
        result = _public_content_for_event(
            'tool_result',
            {
                'call_id': 'call-4',
                'tool_name': 'write_file',
                'result': 'saved',
                'status': 'success',
                'payload': {'auto_save': True, 'file': '/tmp/out.txt'},
            },
        )
        assert result['info']['auto_save'] is True
        assert result['info']['file'] == '/tmp/out.txt'

    def test_tool_result_info_key_from_model_dump(self) -> None:
        """ESIN-07: ToolResultEvent.model_dump() uses 'info' key, must map correctly."""
        # When ToolResultEvent.model_dump() is used, 'info' key is in the dict
        # (not 'payload'). This tests the generator event path.
        result = _public_content_for_event(
            'tool_result',
            {
                'call_id': 'call-5',
                'tool_name': 'bash',
                'result': 'output',
                'status': 'success',
                'info': {'exit_code': 0, 'signal': None},
            },
        )
        assert result['info'] == {'exit_code': 0, 'signal': None}


class TestInteractionPayloadMapping:
    """Generic interaction event family SSE payload mapping."""

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


class TestRunResultOmitsVendorByTurn:
    """run_result public content carries aggregated usage only.

    usage_vendor_by_turn is per-turn vendor detail consumed by the in-process
    drain (eval / devshell); it must not bloat the frontend SSE / persisted
    payload.
    """

    def test_run_result_public_content_omits_usage_vendor_by_turn(self) -> None:
        payload = {
            'type': 'run_result',
            'source': 'Agent',
            'status': 'completed',
            'reason': 'natural',
            'final_content': 'done',
            'num_turns': 2,
            'usage': {'total_tokens': 20},
            'usage_vendor_by_turn': [{'total_tokens': 10}, {'total_tokens': 10}],
        }
        content = _public_content_for_event('run_result', payload)
        assert content['usage'] == {'total_tokens': 20}
        assert 'usage_vendor_by_turn' not in content


class TestBuildPublicSsePayloadDedup:
    """Top-level projection rules for the live SSE payload.

    Structured events (tool_result, run_result, tool_call, error, mcp_*, ...)
    keep their business fields in ``content`` only. The top level must not
    double-encode them. usage_vendor_by_turn is dropped from the public payload
    entirely.
    """

    def _build(self, raw: dict) -> dict:
        return build_public_sse_payload_from_bus_dump(
            raw,
            session_id='s',
            task_id='t',
            invocation_id='i',
            spawn_id=None,
        )

    def test_tool_result_business_fields_not_duplicated_at_top_level(self) -> None:
        raw = {
            'source': 'agent',
            'type': 'tool_result',
            'timestamp': '2026-05-31T00:00:00',
            'call_id': 'c1',
            'tool_name': 'Bash',
            'result': 'big output',
            'status': 'success',
            'payload': {},
            'turn_index': 2,
            'turn_usage': {'total_tokens': 10},
            'total_usage': {'total_tokens': 20},
        }
        out = self._build(raw)
        # Structured payload lives in content.
        assert out['content']['result'] == 'big output'
        assert 'turn_index' not in out['content']
        assert 'turn_usage' not in out['content']
        assert 'total_usage' not in out['content']
        # No business field is duplicated at the top level.
        for key in (
            'result',
            'status',
            'turn_usage',
            'total_usage',
            'call_id',
            'tool_name',
            'payload',
            'turn_index',
        ):
            assert key not in out, f'{key} duplicated at top level'
        # Envelope metadata is preserved.
        assert out['timestamp'] == '2026-05-31T00:00:00'
        assert out['session_id'] == 's'

    def test_run_result_business_fields_not_duplicated_at_top_level(self) -> None:
        raw = {
            'source': 'agent',
            'type': 'run_result',
            'timestamp': '2026-05-31T00:00:00',
            'status': 'completed',
            'reason': 'natural',
            'final_content': 'answer',
            'num_turns': 4,
            'usage': {'total_tokens': 100},
            'usage_vendor_by_turn': [{'total_tokens': 10}],
            'model': 'm',
            'model_profile': 'p',
            'model_route': 'r',
        }
        out = self._build(raw)
        assert out['content']['content'] == 'answer'
        assert out['content']['status'] == 'completed'
        assert out['content']['reason'] == 'natural'
        assert out['content']['num_turns'] == 4
        assert out['content']['usage'] == {'total_tokens': 100}
        assert 'usage_vendor_by_turn' not in out['content']
        for key in (
            'final_content',
            'usage',
            'usage_vendor_by_turn',
            'status',
            'reason',
            'num_turns',
            'model',
            'model_profile',
            'model_route',
        ):
            assert key not in out, f'{key} duplicated at top level'
        for key in ('model', 'model_profile', 'model_route'):
            assert key not in out['content'], f'{key} leaked into content'
        assert out['timestamp'] == '2026-05-31T00:00:00'

    def test_tool_progress_keeps_top_level_identifiers(self) -> None:
        # tool_progress has no structured-content branch: content is the string,
        # so call_id / tool_name must stay at the top level for the frontend to
        # associate the progress line with its tool call.
        raw = {
            'source': 'agent',
            'type': 'tool_progress',
            'timestamp': 't',
            'call_id': 'c1',
            'tool_name': 'Bash',
            'content': 'line of stdout',
        }
        out = self._build(raw)
        assert out['content'] == 'line of stdout'
        assert out['call_id'] == 'c1'
        assert out['tool_name'] == 'Bash'

    def test_streaming_response_keeps_stream_state_at_top_level(self) -> None:
        raw = {
            'source': 'agent',
            'type': 'response',
            'timestamp': 't',
            'content': 'hel',
            'stream_state': 'streaming',
            'stream_id': 'r1',
        }
        out = self._build(raw)
        assert out['content'] == 'hel'
        assert out['stream_state'] == 'streaming'
        assert out['stream_id'] == 'r1'

    def test_response_end_omits_empty_model_identity_fields(self) -> None:
        raw = {
            'source': 'agent',
            'type': 'response',
            'timestamp': 't',
            'content': '',
            'stream_state': 'end',
            'stream_id': 'turn-6',
            'model': None,
            'model_profile': None,
            'model_route': None,
        }

        out = self._build(raw)

        assert out['content'] == ''
        assert out['stream_state'] == 'end'
        assert out['stream_id'] == 'turn-6'
        assert 'model' not in out
        assert 'model_profile' not in out
        assert 'model_route' not in out


def test_thought_complete_without_usage_serializes_to_plain_text() -> None:
    """Usage-free complete thoughts expose plain text content."""
    from matmaster.integration.event_payloads import _public_content_for_event

    payload = {
        "type": "thought",
        "content": "some reasoning",
        "stream_state": "complete",
        "reasoning_content": "some reasoning",
        "turn_index": None,
        "turn_usage": {},
        "total_usage": {},
    }

    content = _public_content_for_event("thought", payload)

    assert content == "some reasoning"
    assert not isinstance(content, dict)


class TestSubagentSpawnContent:
    def test_subagent_spawn_maps_binding_fields(self) -> None:
        payload = {
            'type': 'subagent_spawn',
            'source': 'MatMaster:direct',
            'spawn_id': 'ab12cd34ef56ab12',
            'parent_call_id': 'call_x',
            'exp_name': 'direct',
            'task_summary': 'summarize logs',
        }

        assert _public_content_for_event('subagent_spawn', payload) == {
            'parent_call_id': 'call_x',
            'exp_name': 'direct',
            'task_summary': 'summarize logs',
        }

    def test_build_public_sse_payload_carries_top_level_spawn_id(self) -> None:
        from matmaster.types.events import SubagentSpawnEvent

        event = SubagentSpawnEvent(
            source='MatMaster:direct',
            spawn_id='ab12cd34ef56ab12',
            parent_call_id='call_x',
            exp_name='direct',
            task_summary='summarize logs',
        )

        out = build_public_sse_payload_from_bus_dump(
            event.model_dump(mode='json'),
            session_id='sess-1',
            task_id='task-1',
            invocation_id='inv-1',
            spawn_id=event.spawn_id,
        )

        assert out['type'] == 'subagent_spawn'
        assert out['source'] == 'MatMaster:direct'
        assert out['spawn_id'] == 'ab12cd34ef56ab12'
        assert out['content'] == {
            'parent_call_id': 'call_x',
            'exp_name': 'direct',
            'task_summary': 'summarize logs',
        }
        assert 'parent_call_id' not in out
        assert 'task_summary' not in out
