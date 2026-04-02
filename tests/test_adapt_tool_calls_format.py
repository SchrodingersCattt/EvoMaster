"""Unit tests for _adapt_tool_calls_format in chat_history.

The function converts legacy evomaster nested format → matmaster flat format
so that AssistantMessage.model_validate() can parse ToolCallData correctly.
"""

import json

from src.services.chat_history import _adapt_tool_calls_format


class TestAdaptToolCallsFormat:
    """Tests for the tool_calls format adapter (nested → flat)."""

    def test_evomaster_nested_format_converted_to_flat(self):
        """evomaster nested format → matmaster flat ToolCallData format."""
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
        tc = result['tool_calls'][0]
        assert tc['id'] == 'call_1'
        assert tc['name'] == 'execute_bash'
        assert tc['arguments'] == {'command': 'pwd'}
        assert 'function' not in tc
        assert 'type' not in tc

    def test_matmaster_flat_format_passthrough(self):
        """Already-flat matmaster format passes through with arguments as dict."""
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
        assert tc['name'] == 'execute_bash'
        assert tc['arguments'] == {'command': 'pwd'}

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

    def test_flat_arguments_string_parsed(self):
        """String arguments in flat format are parsed to dict."""
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
        assert result['tool_calls'][0]['arguments'] == {'cmd': 'ls'}

    def test_flat_arguments_none_fallback(self):
        """None arguments in nested format produce empty dict."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'type': 'function',
                    'function': {'name': 'bash', 'arguments': None},
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0]['arguments'] == {}

    def test_nested_unicode_arguments_preserved(self):
        """Non-ASCII arguments preserved through nested → flat conversion."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'type': 'function',
                    'function': {
                        'name': 'search',
                        'arguments': json.dumps(
                            {'query': '分子动力学'}, ensure_ascii=False
                        ),
                    },
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0]['arguments'] == {'query': '分子动力学'}

    def test_mixed_formats_in_same_list(self):
        """List with both nested and flat items — each handled correctly."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'name': 'a',
                    'arguments': {'x': 1},
                },
                {
                    'id': 'call_2',
                    'type': 'function',
                    'function': {'name': 'b', 'arguments': '{}'},
                },
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0]['name'] == 'a'
        assert result['tool_calls'][0]['arguments'] == {'x': 1}
        assert result['tool_calls'][1]['name'] == 'b'
        assert result['tool_calls'][1]['arguments'] == {}

    def test_non_dict_original_unchanged(self):
        """Other top-level fields in raw dict are preserved."""
        raw = {
            'role': 'assistant',
            'content': 'text',
            'reasoning_content': 'think',
            'tool_calls': [
                {
                    'id': 'c1',
                    'type': 'function',
                    'function': {'name': 'x', 'arguments': '{}'},
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['role'] == 'assistant'
        assert result['content'] == 'text'
        assert result['reasoning_content'] == 'think'
