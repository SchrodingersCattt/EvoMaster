"""matmaster/tools/builtin/__init__.py

Builtin tools — matmaster native tool implementations.
All tools inherit from BuiltinTool ABC and satisfy the Tool Protocol.

Tools are added incrementally by plan-01 through plan-04.
"""

from matmaster.tools.builtin.base import BuiltinTool

__all__ = [
    "BuiltinTool",
]
