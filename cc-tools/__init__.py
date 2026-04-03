"""cc-tools -- Claude Code tool implementations in Python.

Python ports of Claude Code's 14 core tools, adapted for the matmaster-evo
project. Each tool follows the matmaster BuiltinTool pattern (ClassVar metadata,
sync _execute, async execute via to_thread).

Usage:
    from cc_tools import create_all_tools, ReadTool, EditTool

    # Create all tools with session/workdir injection
    tools = create_all_tools(session=my_session, workdir=Path("/workspace"))

    # Or instantiate individually
    read = ReadTool(session=my_session, tracker=my_tracker)
    result = await read.execute({"file_path": "/tmp/foo.py"})
"""

from .base import BuiltinTool, ToolResult, normalize_tool_result

from .read_tool import ReadTool
from .edit_tool import EditTool
from .write_tool import WriteTool
from .bash_tool import BashTool
from .glob_tool import GlobTool
from .grep_tool import GrepTool
from .web_fetch_tool import WebFetchTool
from .web_search_tool import WebSearchTool
from .agent_tool import AgentTool
from .todo_write_tool import TodoWriteTool
from .tool_search_tool import ToolSearchTool
from .skill_tool import SkillTool
from .ask_user_question_tool import AskUserQuestionTool
from .send_message_tool import SendMessageTool

__all__ = [
    # Base
    "BuiltinTool",
    "ToolResult",
    "normalize_tool_result",
    # Tools (14 total, matching reference doc)
    "ReadTool",
    "EditTool",
    "WriteTool",
    "BashTool",
    "GlobTool",
    "GrepTool",
    "WebFetchTool",
    "WebSearchTool",
    "AgentTool",
    "TodoWriteTool",
    "ToolSearchTool",
    "SkillTool",
    "AskUserQuestionTool",
    "SendMessageTool",
    # Factory
    "create_all_tools",
    "CC_TOOL_CLASSES",
]

# All CC tool classes in registration order
CC_TOOL_CLASSES: list[type[BuiltinTool]] = [
    ReadTool,
    EditTool,
    WriteTool,
    BashTool,
    GlobTool,
    GrepTool,
    WebFetchTool,
    WebSearchTool,
    AgentTool,
    TodoWriteTool,
    ToolSearchTool,
    SkillTool,
    AskUserQuestionTool,
    SendMessageTool,
]


def create_all_tools(
    *,
    session: any = None,
    workdir: any = None,
    tracker: any = None,
    **kwargs: any,
) -> list[BuiltinTool]:
    """Create instances of all CC tools with shared session/workdir.

    Extra kwargs are forwarded to tools that accept them:
    - agent_id: for TodoWriteTool, SendMessageTool
    - spawn_fn: for AgentTool
    - send_fn: for SendMessageTool
    - search_backend: for WebSearchTool
    - skill_registry: for SkillTool
    - tool_registry: for ToolSearchTool
    - deferred_tools: for ToolSearchTool
    """
    common = {"session": session, "workdir": workdir}

    tools: list[BuiltinTool] = [
        ReadTool(**common, tracker=tracker),
        EditTool(**common, tracker=tracker),
        WriteTool(**common, tracker=tracker),
        BashTool(**common),
        GlobTool(**common),
        GrepTool(**common),
        WebFetchTool(**common),
        WebSearchTool(**common, search_backend=kwargs.get("search_backend")),
        AgentTool(**common, spawn_fn=kwargs.get("spawn_fn")),
        TodoWriteTool(**common, agent_id=kwargs.get("agent_id", "main")),
        ToolSearchTool(
            **common,
            tool_registry=kwargs.get("tool_registry"),
            deferred_tools=kwargs.get("deferred_tools"),
        ),
        SkillTool(
            **common,
            skill_registry=kwargs.get("skill_registry"),
            skill_dirs=kwargs.get("skill_dirs"),
        ),
        AskUserQuestionTool(**common),
        SendMessageTool(
            **common,
            send_fn=kwargs.get("send_fn"),
            agent_id=kwargs.get("agent_id", "main"),
            teammates=kwargs.get("teammates"),
        ),
    ]
    return tools
