"""Builtin tools -- matmaster native tool implementations.

All builtin tools inherit from BuiltinTool ABC and satisfy the Tool Protocol.
"""

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.builtin.glob_tool import GlobTool
from matmaster.tools.builtin.grep_tool import GrepTool
from matmaster.tools.builtin.listdir_tool import ListDirTool
from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.read_tracker import ReadTracker
from matmaster.tools.builtin.spawn_tool import SpawnTool
from matmaster.tools.builtin.web_fetch_tool import WebFetchTool
from matmaster.tools.builtin.web_search_tool import WebSearchTool
from matmaster.tools.builtin.write_tool import WriteTool
from matmaster.tools.builtin.task import (
    TaskCompleteTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
)

__all__ = [
    "BuiltinTool",
    "BashTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "ListDirTool",
    "ReadTool",
    "ReadTracker",
    "SpawnTool",
    "WriteTool",
    "TaskCompleteTool",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskUpdateTool",
    "WebFetchTool",
    "WebSearchTool",
]
