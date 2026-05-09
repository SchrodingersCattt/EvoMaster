"""
completion.py — 补全数据类型。

CompletionItem 对应 LSP CompletionItem 的精简版，
用于向 LLM / UI 返回参数补全候选项。
"""

from dataclasses import dataclass


@dataclass
class CompletionItem:
    """单个补全候选项。

    Attributes
    ----------
    label:
        候选项的显示标签，通常是参数名或枚举值。
    detail:
        简短的附加信息，如类型、默认值（单行）。
    documentation:
        完整的 Markdown 文档字符串（可多行）。
    insert_text:
        实际插入到编辑器的文本（可含占位符）。
    category:
        所属分类，如 'electronic'、'ionic'。
    sort_priority:
        排序优先级；数值越小越靠前（默认 0）。
    """

    label: str
    detail: str
    documentation: str
    insert_text: str
    category: str
    sort_priority: int = 0
