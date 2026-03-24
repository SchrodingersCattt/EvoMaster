"""MatMaster Chat 相关请求/响应数据类型

ag-ui 协议（前后端约定）：
- 服务端 -> 客户端：SSE，event 固定为 "ag-ui"，data 为 JSON 字符串，字段：
  source: "System"|"User"|"MatMaster", type: 事件类型, content: 内容, session_id: 会话 id
  事件类型示例: session_status, status, query, thought, tool_call, tool_result, run_result, error, cancelled, run_interrupted, confirmation_request, confirmation_reply, planner_reply, exp_run, log_line, workspace_uploaded, workspace_upload_error, bohrium_node 等。
  thought：普通完整文本；若为流式思考分片，则仍使用 type='thought'，并在 payload 顶层附带 stream_state='start'|'streaming'|'end'，以及可选 stream_id/context/token_count。
  session_status：流开头推送，含 status: 'idle'|'active'|'waiting'|'failed'（waiting=已入队未接手，failed=上一轮因 run_interrupted reason=restart 或 deploy 按失败结束），可选 last_task_id；便于部署/重启后前端根据 idle/failed 结束“未结束的 stream”状态。
  run_interrupted：部署/重启导致上一轮在别的 pod 上被中断时推送；reason 现区分 'deploy'（新版本部署）与 'restart'（同版本实例重启），并追加 current_version、previous_version（可选，缺失时表示未知），可选 last_user_content；若无法读取上一版本会提供 reason_note='missing_previous_version'。reason 为 'restart' 或 'deploy' 时 payload 带 treat_as_failure=true，且后端会立即推送 type='stream_closed'（end_reason='run_interrupted_restart' 或 'run_interrupted_deploy', treat_as_failure=true），按失败直接结束流；前端应据此结束“未结束的 stream”并展示为失败。bohrium_node 的 content 含 node_id, status: 'created'|'ready'|'skills_synced'|'connected'|'destroyed', message，ready/skills_synced/connected 时另有 ip。
  stream_closed：SSE 传输层关闭标记；run_result 表示本轮业务结果，stream_closed 只表示这条实时流可以结束。payload 顶层可选 task_completed（boolean），true 表示本轮任务成功完成（已发 run_result），false 或缺失表示未成功（用户取消、异常或 run_interrupted 等）。
- 客户端 -> 服务端：REST
  POST /chat/sessions/{session_id}/stream  Body 可选：不传或 content 为空→仅历史+ping；有 content→发送并返回本次 SSE 流
  POST /chat/sessions/{session_id}/stop  终止当前运行
  POST /chat/sessions/{session_id}/confirmation_reply Body: ChatPlannerReplyRequest（confirmation_request 统一回复）
- 统一流接口：POST /stream，要发消息就带 content，仅订阅就省略 body 或 content 为空。
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from src.base.base_res import BaseResponse


class SessionListQuery(BaseModel):
    """GET /chat/sessions/list 查询参数（分页）"""

    limit: int = Field(default=20, ge=1, le=100, description='每页条数')
    offset: int = Field(default=0, ge=0, description='偏移量')


class SessionItem(BaseModel):
    """会话列表项"""

    id: str
    status: str = (
        'idle'  # idle=空闲/已结束，active=运行中，waiting=已入队等待 worker（用于限流与前端展示）
    )
    history_length: int
    first_user_message: Optional[str] = None  # 第一条用户消息


class SessionListResponse(BaseModel):
    """GET /chat/sessions/list 列表数据（放在 data 字段内）；分页时含 total、has_more。"""

    sessions: List[SessionItem]
    total: Optional[int] = None  # 总分页条数，仅分页时返回
    has_more: Optional[bool] = None  # 是否有更多，仅分页时返回


class SessionListApiResponse(BaseResponse[SessionListResponse]):
    """GET /api/sessions 规范响应：code, msg, data"""


class RunStatusData(BaseModel):
    """GET /chat/sessions/run_status 的 data 字段：执行中数、排队数"""

    active_count: int
    queued_count: int


class RunStatusApiResponse(BaseResponse[RunStatusData]):
    """GET /chat/sessions/run_status 规范响应：code, msg, data"""


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
    workspace_paths: List[str] | None = (
        None  # 可选，工作区/个人路径列表，如 /personal/1.cif，与 files(OSS) 区分
    )
    mode: str = 'direct'  # "direct" | "planner"
    llm: Optional[str] = (
        None  # 可选，本轮使用的 LLM 配置块（如 litellm/azure/deepseek），不传则用 agent 默认
    )
    model: Optional[str] = (
        None  # 可选，本轮使用的模型名（如 gemini-3-flash-preview、azure/gpt-5），覆盖所选 LLM 配置里的 model
    )
    bohrium_project_id: int | str | None = None  # 可选的 Bohrium project id
    bohrium_user_id: int | str | None = (
        None  # 可选的 Bohrium user id（MCP 计算类工具需要）
    )


class ChatPlannerReplyRequest(BaseModel):
    """POST /chat/sessions/{session_id}/confirmation_reply 用户确认回复（planner / ask_human 统一）"""

    content: str
