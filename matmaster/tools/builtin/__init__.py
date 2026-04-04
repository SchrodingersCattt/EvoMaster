"""matmaster/tools/builtin/__init__.py

Builtin tools — matmaster native tool implementations.
All tools inherit from BuiltinTool ABC and satisfy the Tool Protocol.

Tools are added incrementally by plan-01 through plan-04.
"""

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.builtin.glob_tool import GlobTool
from matmaster.tools.builtin.grep_tool import GrepTool
from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.write_tool import WriteTool

__all__ = [
    "BuiltinTool",
    "BashTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "ReadTool",
    "WriteTool",
]
