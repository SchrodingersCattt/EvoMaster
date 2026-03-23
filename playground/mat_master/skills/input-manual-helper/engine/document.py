"""
document.py — 文档模型（AST）。

定义解析后输入文件的内存表示：
  SourceRange   — 源码位置范围
  ParsedParam   — 单个参数节点
  ParsedSection — Section 节点（可嵌套）
  DocumentModel — 整个文档的 AST 根节点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engine.diagnostics import Diagnostic


@dataclass
class SourceRange:
    """源码中的位置范围。

    行号 (start_line / end_line) 为 1-based；
    列号 (start_col / end_col) 为 0-based。
    """

    start_line: int
    """起始行（1-based）"""

    start_col: int
    """起始列（0-based）"""

    end_line: int
    """结束行（1-based，inclusive）"""

    end_col: int
    """结束列（0-based，exclusive）"""

    def __str__(self) -> str:
        return f"{self.start_line}:{self.start_col}-{self.end_line}:{self.end_col}"


@dataclass
class ParsedParam:
    """解析后的单个参数节点。

    Attributes
    ----------
    name:
        参数名（原始大小写）。
    value:
        解析后的 Python 值（str / int / float / bool / list 等）。
    raw_text:
        原始文本（含空白、注释等）。
    range:
        在源文件中的位置范围。
    section_path:
        所属 section 的路径，如 ``'&FORCE_EVAL/&DFT/&MGRID'``。
        顶层参数（无 section）为空字符串 ``''``。
    """

    name: str
    value: Any
    raw_text: str
    range: SourceRange
    section_path: str = ""
    """'&FORCE_EVAL/&DFT/&MGRID' 或 '' (顶层)"""


@dataclass
class ParsedSection:
    """解析后的 Section 节点（可递归嵌套）。

    Attributes
    ----------
    name:
        Section 名称，如 ``'&FORCE_EVAL'``。
    range:
        在源文件中的位置范围（从开头关键字到结束关键字）。
    params:
        直属本 section 的参数列表。
    children:
        子 section 列表。
    parent:
        父 section 引用；顶层 section 的 parent 为 None。
    """

    name: str
    range: SourceRange
    params: list[ParsedParam] = field(default_factory=list)
    children: list[ParsedSection] = field(default_factory=list)
    parent: ParsedSection | None = field(default=None, repr=False, compare=False)

    @property
    def path(self) -> str:
        """返回从根到本 section 的完整路径，如 ``'&FORCE_EVAL/&DFT'``。"""
        if self.parent is None or not self.parent.name:
            return self.name
        return f"{self.parent.path}/{self.name}"


@dataclass
class DocumentModel:
    """整个输入文件解析后的 AST 根节点。

    Attributes
    ----------
    software:
        文件对应的软件名称，如 ``'cp2k'``。
    source:
        文档来源描述（文件路径或 ``'<string>'``）。
    raw_text:
        原始文件内容。
    sections:
        顶层 section 列表。
    params:
        所有参数的扁平列表（含所有嵌套层级）。
    parse_errors:
        解析阶段产生的 :class:`~engine.diagnostics.Diagnostic` 列表。
    """

    software: str
    source: str
    raw_text: str
    sections: list[ParsedSection] = field(default_factory=list)
    params: list[ParsedParam] = field(default_factory=list)
    """所有参数的扁平列表（含所有嵌套层级）。"""
    parse_errors: list[Diagnostic] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def get_param(self, name: str) -> ParsedParam | None:
        """按参数名查找第一个匹配的 ParsedParam（大小写不敏感）。"""
        name_lower = name.lower()
        for p in self.params:
            if p.name.lower() == name_lower:
                return p
        return None

    def get_section(self, path: str) -> ParsedSection | None:
        """按路径查找 Section，如 ``'&FORCE_EVAL/&DFT'``（大小写不敏感）。"""
        path_lower = path.lower()
        parts = [p for p in path_lower.split("/") if p]
        if not parts:
            return None

        def _search(sections: list[ParsedSection], depth: int) -> ParsedSection | None:
            target = parts[depth]
            for sec in sections:
                if sec.name.lower() == target:
                    if depth == len(parts) - 1:
                        return sec
                    return _search(sec.children, depth + 1)
            return None

        return _search(self.sections, 0)

    def get_param_at_line(self, line: int) -> ParsedParam | None:
        """返回覆盖指定行号（1-based）的 ParsedParam，无则返回 None。"""
        for p in self.params:
            if p.range.start_line <= line <= p.range.end_line:
                return p
        return None
