"""MatMaster Chat 相关请求/响应数据类型

ag-ui 协议（前后端约定）：
- 服务端 -> 客户端：SSE，event 固定为 "ag-ui"，data 为 JSON 字符串，字段：
  source: "System"|"User"|"MatMaster", type: 事件类型, content: 内容, session_id: 会话 id
  事件类型示例: session_status, status, query, thought, response, response_figures, tool_call, tool_result, run_result, error, cancelled, run_interrupted, interaction_request, interaction_reply, interaction_timeout, planner_reply, exp_run, log_line, workspace_uploaded, workspace_upload_error, bohrium_node 等。
  thought：仅表示 reasoning / thinking 内容；若为流式思考分片，则仍使用 type='thought'，并在 payload 顶层附带 stream_state='start'|'streaming'|'end'，以及可选 stream_id/context/token_count。
  response：assistant 对用户可见的正文内容；流式分片同样在 payload 顶层附带 stream_state / stream_id，非流式 response 用于持久化与历史回放。
  response_figures：回答级图片绑定事件；content.figures 为已上传图片列表，顶层仍带 session_id、task_id、invocation_id、spawn_id。该事件用于侧边栏等图像展示，不会把图片写回正文文本。该事件可以在同一 invocation_id 下出现多次，每次都是当前已知完整图片组快照；合法顺序包括早于第一段 response、位于多个 response chunk 之间、或位于 run_result 之前的 final flush。前端应按 invocation_id eager upsert，且不从 tool_result.payload.figures 反推正式回答级图片。
  session_status：流开头推送，含 status: 'idle'|'active'|'waiting'|'failed'（waiting=已入队未接手，failed=上一轮因 run_interrupted reason=restart 或 deploy 按失败结束），可选 last_task_id；便于部署/重启后前端根据 idle/failed 结束“未结束的 stream”状态。
  run_interrupted：部署/重启导致上一轮在别的 pod 上被中断时推送；reason 现区分 'deploy'（新版本部署）与 'restart'（同版本实例重启），并追加 current_version、previous_version（可选，缺失时表示未知），可选 last_user_content；若无法读取上一版本会提供 reason_note='missing_previous_version'。reason 为 'restart' 或 'deploy' 时 payload 带 treat_as_failure=true，且后端会立即推送 type='stream_closed'（end_reason='run_interrupted_restart' 或 'run_interrupted_deploy', treat_as_failure=true），按失败直接结束流；前端应据此结束“未结束的 stream”并展示为失败。bohrium_node 的 content 含 node_id, status: 'created'|'ready'|'connected'|'destroyed', message，ready/connected 时另有 ip。
  stream_closed：SSE 传输层关闭标记；run_result 表示本轮业务结果，stream_closed 只表示这条实时流可以结束。payload 顶层可选 task_completed（boolean），true 表示本轮任务成功完成（已发 run_result），false 或缺失表示未成功（用户取消、异常或 run_interrupted 等）。
- 客户端 -> 服务端：REST
  POST /chat/sessions/{session_id}/stream  Body 可选：不传或 content 为空→仅历史+ping；有 content→发送并返回本次 SSE 流
  POST /chat/sessions/{session_id}/stop  终止当前运行
  POST /chat/sessions/{session_id}/interactions/{request_id}/reply Body: InteractionReplyRequest（通用交互回复）
- 统一流接口：POST /stream，要发消息就带 content，仅订阅就省略 body 或 content 为空。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.base.base_res import BaseResponse


class SessionListQuery(BaseModel):
    """GET /chat/sessions/list 查询参数：按 session_directory 聚合"""

    project_id: int = Field(
        ...,
        description="项目 ID；只返回该项目下的会话。",
        examples=[42],
    )
    per_group_limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="每个目录组首屏返回的会话条数（组内按 updated_at 倒序）",
    )


class SessionItem(BaseModel):
    """会话列表项"""

    id: str
    project_id: int | None = None  # 归属项目 ID；历史数据或未归属项目时可为 None
    status: str = (
        "idle"  # idle=空闲/已结束，active=运行中，waiting=已入队等待 worker（用于限流与前端展示）
    )
    title: str | None = None  # 用户自定义标题；为 None 时前端回退 first_user_message
    history_length: int
    first_user_message: str | None = None  # 第一条用户消息


class SessionDirectoryGroup(BaseModel):
    """按 session_directory 聚合的一组会话"""

    session_directory: str | None = Field(
        default=None,
        description="该组绑定的工作区目录；未设置目录的会话归在 session_directory=null",
    )
    session_count: int = Field(
        description="该目录组下的会话总数（可能大于本组 sessions 长度）",
    )
    sessions: list[SessionItem]
    has_more: bool = Field(description="该组是否还有未返回的会话")
    next_cursor: str | None = Field(
        default=None,
        description="加载该组下一页时传入 /list/more 的 cursor；仅 has_more 时有值",
    )


class SessionListResponse(BaseModel):
    """GET /chat/sessions/list 的 data：按 session_directory 分组"""

    groups: list[SessionDirectoryGroup]
    total_sessions: int = Field(
        description="该项目下当前用户的会话总数",
    )


class SessionListMoreQuery(BaseModel):
    """GET /chat/sessions/list/more 查询参数：单目录组分页"""

    project_id: int = Field(..., description="项目 ID", examples=[42])
    limit: int = Field(default=10, ge=1, le=50, description="本页条数")
    cursor: str = Field(
        ...,
        min_length=1,
        description="同组上一页返回的 next_cursor（首屏来自 GET /chat/sessions/list）",
    )
    directory: str | None = Field(
        default=None,
        description="工作区目录；与 unset_directory 二选一",
    )
    unset_directory: bool = Field(
        default=False,
        description="为 true 表示「未设置目录」分组（session_directory 为空）",
    )

    @model_validator(mode="after")
    def directory_xor_unset(self) -> "SessionListMoreQuery":
        if self.unset_directory:
            return self
        d = (self.directory or "").strip()
        if not d:
            raise ValueError("请指定 unset_directory=true 或非空 directory")
        self.directory = d
        return self


class SessionListMoreResponse(BaseModel):
    """GET /chat/sessions/list/more 的 data"""

    sessions: list[SessionItem]
    has_more: bool
    next_cursor: str | None = Field(
        default=None,
        description="下一页游标，仅 has_more 时有值",
    )


class SessionListMoreApiResponse(BaseResponse[SessionListMoreResponse]):
    """GET /chat/sessions/list/more 规范响应"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "success",
                "data": {
                    "sessions": [],
                    "has_more": False,
                    "next_cursor": None,
                },
            }
        }
    )


class SessionListApiResponse(BaseResponse[SessionListResponse]):
    """GET /chat/sessions/list 规范响应"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "success",
                "data": {
                    "groups": [
                        {
                            "session_directory": "/share/run1",
                            "session_count": 3,
                            "sessions": [
                                {
                                    "id": "session-001",
                                    "project_id": 42,
                                    "status": "idle",
                                    "title": "结构分析会话",
                                    "history_length": 4,
                                    "first_user_message": "分析结构",
                                }
                            ],
                            "has_more": False,
                            "next_cursor": None,
                        }
                    ],
                    "total_sessions": 10,
                },
            }
        }
    )


class SessionDirectoryDeleteQuery(BaseModel):
    """DELETE /chat/sessions/by-directory 查询参数：整组删除某个 session_directory"""

    project_id: int = Field(..., description="项目 ID", examples=[42])
    directory: str | None = Field(
        default=None,
        description="工作区目录；与 unset_directory 二选一",
        max_length=2048,
    )
    unset_directory: bool = Field(
        default=False,
        description="为 true 表示删除「未设置目录」分组（session_directory 为空）",
    )

    @model_validator(mode="after")
    def directory_xor_unset(self) -> "SessionDirectoryDeleteQuery":
        if self.unset_directory:
            return self
        d = (self.directory or "").strip()
        if not d:
            raise ValueError("请指定 unset_directory=true 或非空 directory")
        self.directory = d
        return self


class SessionDirectoryDeleteData(BaseModel):
    """DELETE /chat/sessions/by-directory 的 data 字段"""

    deleted_count: int = Field(description="本次成功软删除的会话数量")


class SessionDirectoryDeleteApiResponse(BaseResponse[SessionDirectoryDeleteData]):
    """DELETE /chat/sessions/by-directory 规范响应"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "ok",
                "data": {"deleted_count": 12},
            }
        }
    )


class RunStatusData(BaseModel):
    """GET /chat/sessions/run_status 的 data 字段：执行中数、排队数"""

    active_count: int
    queued_count: int


class RunStatusApiResponse(BaseResponse[RunStatusData]):
    """GET /chat/sessions/run_status 规范响应：code, msg, data"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "success",
                "data": {
                    "active_count": 2,
                    "queued_count": 5,
                },
            }
        }
    )


# ---------- 会话分享 ----------


class ShareStatusData(BaseModel):
    """分享状态 data 字段"""

    enabled: bool


class ShareStatusApiResponse(BaseResponse[ShareStatusData]):
    """GET/PUT /chat/sessions/{session_id}/share 规范响应：code, msg, data"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "success",
                "data": {"enabled": True},
            }
        }
    )


class ShareSetRequest(BaseModel):
    """PUT /chat/sessions/{session_id}/share 设置分享状态请求体"""

    enabled: bool

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "enabled": True,
            }
        }
    )


# ---------- 会话绑定目录 ----------


class SessionDirectoryData(BaseModel):
    """GET /chat/sessions/{session_id}/session-directory 的 data 字段"""

    directory: str | None = Field(
        default=None,
        description="该会话绑定的工作区目录路径（如远端 /share/... 或 /personal/...）；未设置时为 null",
    )
    mode: Literal["direct", "planner"] | None = Field(
        default=None,
        description="本会话偏好模式 direct|planner；未设置时为 null，前端可默认 direct",
    )


class SessionDirectoryApiResponse(BaseResponse[SessionDirectoryData]):
    """GET/PUT session-directory 规范响应"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "success",
                "data": {
                    "directory": "/share/my_workspace/run1",
                    "mode": "direct",
                },
            }
        }
    )


class SessionDirectorySetRequest(BaseModel):
    """PUT /chat/sessions/{session_id}/session-directory 请求体"""

    directory: str | None = Field(
        default=None,
        max_length=2048,
        description="绑定 Bohrium 远端工作目录（/share 或 /personal 下）；传 null 或空字符串表示清除",
    )
    mode: Literal["direct", "planner"] | None = Field(
        default=None,
        description="偏好模式；传 null 清除持久化；可仅更新 mode 或仅更新 directory",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            m = v.strip().lower()
            if m in ("direct", "planner"):
                return m
        raise ValueError("mode must be direct or planner")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "directory": "/share/project/foo",
                "mode": "planner",
            }
        }
    )


# ---------- Bohrium 提交确认偏好（会话级） ----------


class BohriumSubmitConfirmationData(BaseModel):
    """GET/PUT /chat/sessions/{session_id}/bohrium-submit-confirmation 的 data 字段"""

    session_id: str = Field(description="会话 ID")
    required: bool | None = Field(
        default=None,
        description="会话级 Bohrium 提交确认覆盖值；null 表示未设置/继承",
    )
    source: Literal["session", "user", "default"] = Field(
        default="default",
        description="覆盖来源：session=会话级覆盖；user=用户全局占位；default=无覆盖",
    )


class BohriumSubmitConfirmationApiResponse(BaseResponse[BohriumSubmitConfirmationData]):
    """Bohrium 提交确认偏好规范响应"""


class BohriumSubmitConfirmationSetRequest(BaseModel):
    """PUT /chat/sessions/{session_id}/bohrium-submit-confirmation 请求体"""

    required: bool | None = Field(
        default=None,
        description="会话级覆盖值；null 表示清除覆盖",
    )


# ---------- 会话标题（重命名） ----------


class SessionTitleData(BaseModel):
    """PUT /chat/sessions/{session_id}/title 的 data 字段"""

    id: str = Field(description="会话 ID")
    title: str | None = Field(
        default=None,
        description="当前自定义标题；为 null 表示未设置/已清除，前端回退 first_user_message",
    )


class SessionTitleApiResponse(BaseResponse[SessionTitleData]):
    """PUT /chat/sessions/{session_id}/title 规范响应"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "success",
                "data": {
                    "id": "session-001",
                    "title": "结构分析会话",
                },
            }
        }
    )


class SessionTitleSetRequest(BaseModel):
    """PUT /chat/sessions/{session_id}/title 请求体"""

    title: str | None = Field(
        default=None,
        max_length=255,
        description="会话自定义标题；传 null 或空字符串表示清除（前端回退 first_user_message）",
    )

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        raise ValueError("title must be a string or null")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "结构分析会话",
            }
        }
    )


class SessionTitlesQueryRequest(BaseModel):
    """POST /chat/sessions/titles 请求体：按 sessionId 批量查询标题"""

    session_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="待查询的会话 ID 列表，单次最多 200 个；超量请分批。",
    )

    @field_validator("session_ids", mode="before")
    @classmethod
    def normalize_ids(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("session_ids must be a list")
        # 去空白、去空串、去重（保序）
        seen: set[str] = set()
        out: list[str] = []
        for item in v:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


class SessionTitleItem(BaseModel):
    """会话标题查询项；比 SessionItem 更窄，不包含列表页专属统计字段。"""

    id: str
    project_id: int | None = None
    status: str = "idle"
    title: str | None = None
    first_user_message: str | None = None


class SessionTitlesData(BaseModel):
    """POST /chat/sessions/titles 的 data 字段：仅返回归属当前用户的会话"""

    sessions: list[SessionTitleItem] = Field(
        default_factory=list,
        description="命中的会话项（含 title 与 first_user_message，供前端按既有规则派生显示标题）",
    )


class SessionTitlesApiResponse(BaseResponse[SessionTitlesData]):
    """POST /chat/sessions/titles 规范响应"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 0,
                "msg": "success",
                "data": {
                    "sessions": [
                        {
                            "id": "session-001",
                            "project_id": 42,
                            "status": "idle",
                            "title": "结构分析会话",
                            "first_user_message": "分析结构",
                        }
                    ],
                },
            }
        }
    )


# ---------- ag-ui 协议：客户端 -> 服务端 (REST Body) ----------


class DeliverySpec(BaseModel):
    """控制一次 run 完成后的通知行为。第一版仅 notify 开关；后续可扩展渠道字段。"""

    notify: bool = True


class ChatSendRequest(BaseModel):
    """POST /chat/sessions/{session_id}/stream 请求体：不传或 content 为空则仅拉历史+ping；有 content 则发送消息并返回本次运行的 SSE 流"""

    content: str = ""  # 为空或不传 body 时为「仅订阅」模式
    files: list[str] | None = (
        None  # 可选，OSS 链接列表，前端展示与 content 分开，传给 agent 时拼成 content + URLs
    )
    images: list[str] | None = (
        None  # 可选，OSS 图片链接列表；进入模型 vision content parts，不作为普通附件 URL 拼入正文
    )
    workspace_paths: list[str] | None = (
        None  # 可选，工作区/个人路径列表，如 /personal/1.cif，与 files(OSS) 区分
    )
    mode: str = "direct"  # "direct" | "planner"
    model: str | None = (
        None  # 可选，本轮使用的模型名（如 claude-sonnet-4-6、matmaster/qwen3.7-max），覆盖所选 LLM 配置里的 model
    )
    byok_credential_id: str | None = (
        None  # 可选，用户自带 Key（BYOK）凭证 ID；命中时本轮用该凭证的 endpoint/model/key，不走平台模型
    )
    bohrium_project_id: int | str | None = None  # 可选的 Bohrium project id
    bohrium_user_id: int | str | None = (
        None  # 可选的 Bohrium user id（MCP 计算类工具需要）
    )
    directory: str | None = Field(
        default=None,
        max_length=2048,
        description="可选，前端传入的本轮 Bohrium 远端工作目录（/share 或 /personal 下），随 query 写入历史事件；持久化请用 PUT …/session-directory",
    )
    bohrium_submit_confirmation_required: bool | None = Field(
        default=None,
        description="可选，Bohrium 任务提交是否需要确认；显式传入时同步写入会话级设置，并透传到本轮 job",
    )
    bohrium_job_max_runtime_seconds: int | None = Field(
        default=None,
        gt=0,
        description="可选，本轮 Bohrium job 最大运行时间（秒）；透传为 launching job/add 的 maxRunTime，未传则使用平台默认",
    )
    replace_last_turn: bool = Field(
        default=False,
        description="为 true 时先物理删除最后一条 User/query 及之后的所有事件，再以新 content 发送；用于编辑重发",
    )
    # 以下字段仅在 /stream 内部发起模式（X-Internal-Token）下有意义
    origin: str | None = Field(
        default=None,
        description="内部发起来源标记，如 hpc_job/cron/loop/external_tool；写入 System 触发事件，用于前端渲染、审计、dedup 命名",
    )
    dedup_key: str | None = Field(
        default=None,
        description="内部发起幂等键；命中已存在则不触发。仅成功入队后标记，busy/error 不标记",
    )
    delivery: DeliverySpec | None = Field(
        default=None,
        description="内部发起的完成通知控制；缺省时按 origin 约定默认（用户发送路径恒为 None=保持现状）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "content": "帮我总结这个项目下最近三轮会话的结论",
                    "mode": "direct",
                    "bohrium_project_id": 42,
                },
                {
                    "content": "",
                    "mode": "direct",
                },
                {
                    "content": "列出该目录下的文件",
                    "mode": "direct",
                    "directory": "/share/my_run",
                },
            ]
        }
    )


class InteractionReplyRequest(BaseModel):
    """POST /chat/sessions/{session_id}/interactions/{request_id}/reply 通用回复体。"""

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reply(self) -> "InteractionReplyRequest":
        self.kind = self.kind.strip()
        if not self.kind:
            raise ValueError("kind must not be empty")
        return self


class ErrorApiResponse(BaseResponse[None]):
    """错误响应示例。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": 403,
                "msg": "无权限访问该会话",
                "data": None,
            }
        }
    )
