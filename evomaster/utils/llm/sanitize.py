"""工具调用参数与日志截断。"""

from __future__ import annotations

import json
import logging
import re

_sanitize_logger = logging.getLogger(__name__)


def truncate_content(
    content: str,
    max_length: int = 5000,
    head_length: int = 2500,
    tail_length: int = 2500,
) -> str:
    """截断内容，如果超过最大长度，保留开头和结尾部分

    Args:
        content: 要截断的内容
        max_length: 最大长度阈值，超过此长度才截断
        head_length: 保留的开头部分长度
        tail_length: 保留的结尾部分长度

    Returns:
        截断后的内容
    """
    if len(content) <= max_length:
        return content
    return content[:head_length] + '\n... [truncated] ...\n' + content[-tail_length:]


def _sanitize_tool_call_arguments(arguments: str | None) -> str:
    """验证并修复 LLM 返回的工具调用 arguments JSON 字符串。

    LLM 偶尔会生成非法 JSON（如混入 XML 属性语法 ``is_input="true"``），
    导致下一轮 LLM 调用时 litellm/Bedrock 适配器解析失败并抛出 500 错误。
    本函数在 LLM 响应解析阶段对 arguments 做防御性清洗：

    1. 若 arguments 已是合法 JSON，原样返回。
    2. 若非法，尝试移除 JSON key 与冒号之间的 XML 属性风格 token
       （如 ``"command" is_input="true":`` → ``"command":``）后重新解析。
    3. 若修复后仍非法，返回 ``"{}"`` 并记录 ERROR 日志。
       工具层会因参数校验失败返回错误 observation，Agent 可从中恢复，
       远比让整个下一轮 LLM 调用崩溃要安全。

    Args:
        arguments: LLM 返回的 function.arguments 字符串（可为 None）。

    Returns:
        合法的 JSON 字符串。
    """
    if not arguments:
        return '{}'

    # 快速路径：合法 JSON 直接返回
    try:
        json.loads(arguments)
        return arguments
    except (json.JSONDecodeError, ValueError):
        pass

    # 修复：移除 JSON key 与冒号之间的 XML 属性风格 token
    # 例：`"command" is_input="true":` → `"command":`
    repaired = re.sub(r'(?<=")\s+\w+="[^"]*"(?=\s*:)', '', arguments)
    try:
        json.loads(repaired)
        _sanitize_logger.warning(
            '_sanitize_tool_call_arguments: repaired malformed tool call arguments. '
            'Original: %r  Repaired: %r',
            arguments,
            repaired,
        )
        return repaired
    except (json.JSONDecodeError, ValueError):
        pass

    # 无法修复：返回空对象，记录 ERROR
    _sanitize_logger.error(
        '_sanitize_tool_call_arguments: could not repair malformed tool call arguments, '
        "falling back to '{}'. Original: %r",
        arguments,
    )
    return '{}'
