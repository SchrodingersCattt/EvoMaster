"""
diagnostics.py — 诊断数据类型。

Diagnostic 代表解析器或验证器对文档中某一位置（或整体）发现的问题。
severity 参照 LSP DiagnosticSeverity：error / warning / info。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.document import SourceRange


@dataclass
class Diagnostic:
    """对文档某位置（或整体）发现的问题描述。

    Attributes
    ----------
    severity:
        严重程度，'error' | 'warning' | 'info'。
    message:
        人类可读的问题说明。
    range:
        问题所在的源码范围；None 表示整个文件级别的问题。
    param:
        关联的参数名（可为空字符串）。
    suggestion:
        修复建议（可选）。
    rule_id:
        触发该诊断的规则 ID（可选），便于批量过滤或关闭特定规则。
    """

    severity: str
    """'error' | 'warning' | 'info'"""

    message: str

    range: SourceRange | None = None
    """源码范围；None 表示文件级别问题。"""

    param: str = ""
    """关联参数名。"""

    suggestion: str | None = None
    """修复建议。"""

    rule_id: str | None = None
    """规则 ID。"""

    def to_dict(self) -> dict:
        """序列化为普通 dict，便于 JSON 输出。"""
        d: dict = {
            "severity": self.severity,
            "message": self.message,
            "param": self.param,
        }
        if self.range is not None:
            d["range"] = {
                "start_line": self.range.start_line,
                "start_col": self.range.start_col,
                "end_line": self.range.end_line,
                "end_col": self.range.end_col,
            }
        if self.suggestion is not None:
            d["suggestion"] = self.suggestion
        if self.rule_id is not None:
            d["rule_id"] = self.rule_id
        return d

    def to_human(self) -> str:
        """生成单行人类可读字符串，格式：

        ``[ERROR] line 10:5  ENCUT: 值超出合法范围  (建议: ...)``
        """
        prefix = f"[{self.severity.upper()}]"

        location = ""
        if self.range is not None:
            location = f" line {self.range.start_line}:{self.range.start_col}"

        param_part = f"  {self.param}:" if self.param else ""
        suggestion_part = f"  (建议: {self.suggestion})" if self.suggestion else ""
        rule_part = f"  [{self.rule_id}]" if self.rule_id else ""

        return f"{prefix}{location}{param_part}  {self.message}{suggestion_part}{rule_part}"
