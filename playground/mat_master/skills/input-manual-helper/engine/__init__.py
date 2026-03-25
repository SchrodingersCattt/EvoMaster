"""
input-manual-helper engine 包。

LSP-style Schema 驱动的参数补全/验证引擎。
子模块：
  schema      — ParamTag + SchemaRegistry
  document    — DocumentModel (AST)
  diagnostics — Diagnostic 数据类型
  completion  — CompletionItem 数据类型
  renderer    — RenderIntent 数据类型
  software    — SoftwareBackend ABC + 各软件实现

注意：由于父目录名 input-manual-helper 含连字符，此包不能通过顶层
Python 包路径导入，应在 input-manual-helper/ 目录下用相对 import
或将该目录加入 sys.path 后使用。
"""

from engine.schema import ParamTag, SchemaRegistry
from engine.document import (
    SourceRange,
    ParsedParam,
    ParsedSection,
    DocumentModel,
)
from engine.diagnostics import Diagnostic
from engine.completion import CompletionItem
from engine.renderer import RenderIntent

__all__ = [
    "ParamTag",
    "SchemaRegistry",
    "SourceRange",
    "ParsedParam",
    "ParsedSection",
    "DocumentModel",
    "Diagnostic",
    "CompletionItem",
    "RenderIntent",
]
