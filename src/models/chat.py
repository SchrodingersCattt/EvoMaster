"""MatMaster Chat 相关请求/响应数据类型

ag-ui 协议（前后端约定）：
- 服务端 -> 客户端：SSE，event 固定为 "ag-ui"，data 为 JSON 字符串，字段：
  source: "System"|"User"|"MatMaster"|"Planner", type: 事件类型, content: 内容, session_id: 会话 id
  事件类型示例: status, query, thought, tool_call, tool_result, finish, error, cancelled, planner_ask, planner_reply, exp_run, log_line 等
- 客户端 -> 服务端：REST
  POST /chat/sessions/{session_id}/stream  Body 可选：不传或 content 为空→仅历史+ping；有 content→发送并返回本次 SSE 流
  POST /chat/sessions/{session_id}/cancel  取消当前运行
  POST /chat/sessions/{session_id}/planner_reply Body: ChatPlannerReplyRequest
- 统一流接口：POST /stream，要发消息就带 content，仅订阅就省略 body 或 content 为空。
"""

from typing import Any, List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """发起/就绪请求"""

    prompt: str = ''
    workspace: str = './workspace'


class ChatStartResponse(BaseModel):
    """POST /api/start 响应"""

    status: str = 'ready'
    session_id: str


class SessionItem(BaseModel):
    """会话列表项"""

    id: str
    history_length: int
    first_user_message: Optional[str] = None  # 第一条用户消息


class SessionListResponse(BaseModel):
    """GET /api/sessions 响应"""

    sessions: List[SessionItem]


class RunInfoResponse(BaseModel):
    """GET /api/sessions/{id}/run_info 响应"""

    run_id: str
    last_task_id: Optional[str] = None
    task_ids: List[str] = []


class FileEntry(BaseModel):
    """文件/目录项"""

    name: str
    path: str
    dir: bool


class SessionFilesResponse(BaseModel):
    """GET /api/sessions/{id}/files 响应"""

    run_id: str
    path: str
    entries: List[FileEntry]
    workspace_root: Optional[str] = None
    task_id: Optional[str] = None


class RunItem(BaseModel):
    """Run 列表项"""

    id: str
    label: str


class RunListResponse(BaseModel):
    """GET /api/runs 响应"""

    runs: List[RunItem]


class RunFilesResponse(BaseModel):
    """GET /api/runs/{id}/files 响应"""

    run_id: str
    path: str
    entries: List[FileEntry]


# ---------- ag-ui 协议：客户端 -> 服务端 (REST Body) ----------


class ChatSendRequest(BaseModel):
    """POST /chat/sessions/{session_id}/stream 请求体：不传或 content 为空则仅拉历史+ping；有 content 则发送消息并返回本次运行的 SSE 流"""

    content: str = ''  # 为空或不传 body 时为「仅订阅」模式
    mode: str = 'direct'  # "direct" | "planner"
    bohrium_access_key: str | None = None  # 可选的 Bohrium access key
    bohrium_project_id: int | str | None = None  # 可选的 Bohrium project id


class ChatPlannerReplyRequest(BaseModel):
    """POST /chat/sessions/{session_id}/planner_reply Planner 模式下用户回复"""

    content: str


# ---------- ag-ui 协议：服务端 -> 客户端 (SSE event data) ----------


class AgUiEvent(BaseModel):
    """SSE event 固定为 "ag-ui"，data 为本结构 JSON"""

    source: str  # System | User | MatMaster | Planner
    type: str  # status | query | thought | tool_call | tool_result | finish | error | cancelled | planner_ask | ...
    content: Any
    session_id: Optional[str] = None

    class Config:
        extra = 'allow'


# 兼容别名
ChatEventPayload = dict[str, Any]
