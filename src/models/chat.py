"""MatMaster Chat 相关请求/响应数据类型

ag-ui 协议（前后端约定）：
- 服务端 -> 客户端：SSE，event 固定为 "ag-ui"，data 为 JSON 字符串，字段：
  source: "System"|"User"|"MatMaster"|"Planner", type: 事件类型, content: 内容, session_id: 会话 id
  事件类型示例: session_status, status, query, thought, tool_call, tool_result, finish, error, cancelled, run_interrupted, planner_ask, planner_reply, exp_run, log_line, workspace_uploaded, workspace_upload_error, bohrium_node 等。
  session_status：流开头推送，含 status: 'idle'|'active'|'waiting'（waiting=已入队未接手），可选 last_task_id；便于部署/重启后前端根据 idle 结束“未结束的 stream”状态。
  run_interrupted：部署/重启导致上一轮在别的 pod 上被中断时推送；reason 现区分 'deploy'（新版本部署）与 'restart'（同版本实例重启），并追加 current_version、previous_version（可选，缺失时表示未知），可选 last_user_content；若无法读取上一版本会提供 reason_note='missing_previous_version'。后端会自动在新 pod 上重跑上次任务，前端可仅做展示或根据 reason 结束“未结束的 stream”状态。bohrium_node 的 content 含 node_id, status: 'created'|'ready'|'skills_synced'|'connected'|'destroyed', message，ready/skills_synced/connected 时另有 ip。
- 客户端 -> 服务端：REST
  POST /chat/sessions/{session_id}/stream  Body 可选：不传或 content 为空→仅历史+ping；有 content→发送并返回本次 SSE 流
  POST /chat/sessions/{session_id}/stop  终止当前运行
  POST /chat/sessions/{session_id}/confirmation_reply Body: ChatPlannerReplyRequest（planner_ask / confirmation_request 统一回复）
- 统一流接口：POST /stream，要发消息就带 content，仅订阅就省略 body 或 content 为空。
"""

from typing import Any, List, Literal, Optional

from pydantic import BaseModel

from src.base.base_res import BaseResponse


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
    status: str = (
        'idle'  # idle=空闲/已结束，active=运行中，waiting=已入队等待 worker（用于限流与前端展示）
    )
    history_length: int
    first_user_message: Optional[str] = None  # 第一条用户消息


class SessionListResponse(BaseModel):
    """GET /api/sessions 列表数据（放在 data 字段内）"""

    sessions: List[SessionItem]


class SessionListApiResponse(BaseResponse[SessionListResponse]):
    """GET /api/sessions 规范响应：code, msg, data"""


class ActiveSessionsCountData(BaseModel):
    """GET /chat/sessions/active_count 的 data 字段"""

    active_count: int


class ActiveSessionsCountApiResponse(BaseResponse[ActiveSessionsCountData]):
    """GET /chat/sessions/active_count 规范响应：code, msg, data"""


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


# ---------- Workspace OSS 列表 ----------


class WorkspaceEntry(BaseModel):
    """workspace 列表：单项（目录或文件），按 entries 顺序展示即可。"""

    type: Literal['directory', 'file']
    name: str
    path: str
    download_url: Optional[str] = None  # 仅 type=file 时有值


class WorkspaceListData(BaseModel):
    """GET /chat/sessions/{session_id}/workspace/list 的 data 字段。entries 已按目录在前、文件在后、同类型按 name 排序。"""

    path: str
    entries: List[WorkspaceEntry]


class WorkspaceListApiResponse(BaseResponse[WorkspaceListData]):
    """GET /chat/sessions/{session_id}/workspace/list 规范响应：code, msg, data"""


# ---------- 会话分享 ----------


class ShareStatusData(BaseModel):
    """分享状态 data 字段"""

    enabled: bool


class ShareStatusApiResponse(BaseResponse[ShareStatusData]):
    """GET/PUT /chat/sessions/{session_id}/share 规范响应：code, msg, data"""


class ShareSetRequest(BaseModel):
    """PUT /chat/sessions/{session_id}/share 设置分享状态请求体"""

    enabled: bool


# ---------- ag-ui 协议：客户端 -> 服务端 (REST Body) ----------


class ChatSendRequest(BaseModel):
    """POST /chat/sessions/{session_id}/stream 请求体：不传或 content 为空则仅拉历史+ping；有 content 则发送消息并返回本次运行的 SSE 流"""

    content: str = ''  # 为空或不传 body 时为「仅订阅」模式
    files: List[str] | None = (
        None  # 可选，OSS 链接列表，前端展示与 content 分开，传给 agent 时拼成 content + URLs
    )
    mode: str = 'direct'  # "direct" | "planner"
    bohrium_project_id: int | str | None = None  # 可选的 Bohrium project id
    bohrium_user_id: int | str | None = (
        None  # 可选的 Bohrium user id（MCP 计算类工具需要）
    )


class ChatPlannerReplyRequest(BaseModel):
    """POST /chat/sessions/{session_id}/confirmation_reply 用户确认回复（planner_ask / ask_human 统一）"""

    content: str


# ---------- ag-ui 协议：服务端 -> 客户端 (SSE event data) ----------


class AgUiEvent(BaseModel):
    """SSE event 固定为 "ag-ui"，data 为本结构 JSON"""

    source: str  # System | User | MatMaster | Planner
    type: str  # status | query | thought | tool_call | tool_result | finish | error | cancelled | planner_ask | ...
    content: Any
    session_id: Optional[str] = None
    invocation_id: Optional[str] = (
        None  # 本轮调用的唯一标识，前端用于区分第几轮（仅发送流/当轮事件带此字段）
    )

    class Config:
        extra = 'allow'


# 兼容别名
ChatEventPayload = dict[str, Any]
