"""Unit tests for _adapt_tool_calls_format in chat_history."""

import json

from src.services.chat_history import _adapt_tool_calls_format


class TestAdaptToolCallsFormat:
    """Tests for the tool_calls format adapter."""

    def test_matmaster_flat_format_converted_to_nested(self):
        """Matmaster ToolCallData flat format → evomaster ToolCall nested format."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'name': 'execute_bash',
                    'arguments': {'command': 'pwd'},
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        tc = result['tool_calls'][0]
        assert tc['id'] == 'call_1'
        assert tc['type'] == 'function'
        assert tc['function']['name'] == 'execute_bash'
        assert json.loads(tc['function']['arguments']) == {'command': 'pwd'}

    def test_evomaster_nested_format_passthrough(self):
        """Already-nested evomaster format passes through unchanged."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'type': 'function',
                    'function': {
                        'name': 'execute_bash',
                        'arguments': '{"command":"pwd"}',
                    },
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0] is raw['tool_calls'][0]

    def test_no_tool_calls_passthrough(self):
        """Dict without tool_calls returns as-is."""
        raw = {'role': 'assistant', 'content': 'hello'}
        assert _adapt_tool_calls_format(raw) is raw

    def test_empty_tool_calls_list_passthrough(self):
        """Empty tool_calls list returns as-is."""
        raw = {'role': 'assistant', 'content': '', 'tool_calls': []}
        assert _adapt_tool_calls_format(raw) is raw

    def test_none_tool_calls_passthrough(self):
        """None tool_calls returns as-is."""
        raw = {'role': 'assistant', 'content': '', 'tool_calls': None}
        assert _adapt_tool_calls_format(raw) is raw

    def test_arguments_already_string(self):
        """String arguments passed through without double-encoding."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'name': 'bash',
                    'arguments': '{"cmd":"ls"}',
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0]['function']['arguments'] == '{"cmd":"ls"}'

    def test_arguments_none_fallback(self):
        """None arguments produce empty JSON object string."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {'id': 'call_1', 'name': 'bash', 'arguments': None}
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0]['function']['arguments'] == '{}'

    def test_unicode_arguments_preserved(self):
        """Non-ASCII arguments preserved with ensure_ascii=False."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'name': 'search',
                    'arguments': {'query': '分子动力学'},
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        args_str = result['tool_calls'][0]['function']['arguments']
        assert '分子动力学' in args_str
        assert json.loads(args_str) == {'query': '分子动力学'}

    def test_mixed_formats_in_same_list(self):
        """List with both flat and nested items — each handled correctly."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'type': 'function',
                    'function': {'name': 'a', 'arguments': '{}'},
                },
                {
                    'id': 'call_2',
                    'name': 'b',
                    'arguments': {'x': 1},
                },
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert 'function' in result['tool_calls'][0]
        assert result['tool_calls'][0] is raw['tool_calls'][0]
        assert result['tool_calls'][1]['function']['name'] == 'b'

    def test_non_dict_original_unchanged(self):
        """Other top-level fields in raw dict are preserved."""
        raw = {
            'role': 'assistant',
            'content': 'text',
            'reasoning_content': 'think',
            'tool_calls': [
                {'id': 'c1', 'name': 'x', 'arguments': {}}
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['role'] == 'assistant'
        assert result['content'] == 'text'
        assert result['reasoning_content'] == 'think'
