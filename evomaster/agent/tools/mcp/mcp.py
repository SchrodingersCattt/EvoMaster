"""MCP 工具集成

将 MCP (Model Context Protocol) 服务器的工具包装为 EvoMaster 工具。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from typing import TYPE_CHECKING, Any, ClassVar

from ..base import BaseTool, ToolError

# Session-level error types from anyio for reliable isinstance checks.
# These exceptions have an *empty* str() representation, so string matching alone
# misses them (e.g. ``str(ClosedResourceError())`` → ``""``).
_SESSION_ERROR_TYPES: tuple[type[Exception], ...] = ()
try:
    from anyio import BrokenResourceError as _BrokenResourceError
    from anyio import ClosedResourceError as _ClosedResourceError

    _SESSION_ERROR_TYPES = (_ClosedResourceError, _BrokenResourceError)
except ImportError:
    pass

# 外部服务（web-search、论文检索、结构库等）常需更长时间，默认 60 秒；可通过环境变量 MCP_TOOL_CALL_TIMEOUT 覆盖
DEFAULT_MCP_TOOL_CALL_TIMEOUT = 60


def _mcp_call_timeout() -> int:
    try:
        return int(
            os.environ.get('MCP_TOOL_CALL_TIMEOUT', DEFAULT_MCP_TOOL_CALL_TIMEOUT)
        )
    except ValueError:
        return DEFAULT_MCP_TOOL_CALL_TIMEOUT


if TYPE_CHECKING:
    from evomaster.agent.session import BaseSession
    from evomaster.utils.types import ToolSpec


class MCPTool(BaseTool):
    """MCP 工具包装器

    将单个 MCP 工具包装为 EvoMaster BaseTool。

    特点：
    - 动态工具：运行时从 MCP 服务器获取
    - 异步转同步：MCP 是异步的，需要转换
    - Schema 转换：MCP schema -> ToolSpec
    - 元数据标记：标记工具来源（MCP 服务器）

    使用示例：
        mcp_tool = MCPTool(
            mcp_connection=connection,
            tool_name="github_create_issue",
            tool_description="Create a new GitHub issue",
            input_schema={...}
        )
        observation, info = mcp_tool.execute(session, args_json)
    """

    # 类属性（BaseTool 需要）
    name: ClassVar[str] = 'mcp_tool'  # 会被实例属性覆盖
    params_class: ClassVar[type] = None  # MCP 工具不使用 params_class

    def __init__(
        self,
        mcp_connection,  # MCPConnection 实例
        tool_name: str,
        tool_description: str,
        input_schema: dict,
        remote_tool_name: str | None = None,
        call_timeout: int | None = None,
    ):
        """初始化 MCP 工具

        Args:
            mcp_connection: MCP 连接实例
            tool_name: 工具名称（已添加服务器前缀）
            tool_description: 工具描述
            input_schema: 输入参数 schema（JSON Schema 格式）
            call_timeout: 单次调用的超时秒数，默认从环境变量 MCP_TOOL_CALL_TIMEOUT 或 60 读取
        """
        super().__init__()

        # MCP 相关属性
        self.mcp_connection = mcp_connection
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._input_schema = input_schema
        self._remote_tool_name = remote_tool_name
        self._call_timeout = (
            call_timeout if call_timeout is not None else _mcp_call_timeout()
        )
        # ✅ MCP 专用 event loop（由 MCPToolManager 或 Playground 注入）
        self._mcp_loop = None

        # 覆盖类属性
        self.name = tool_name

        # 元数据标记（由 MCPToolManager 设置）
        self._is_mcp_tool = True
        self._mcp_server = None  # 服务器名称
        self._mcp_manager = None  # MCPToolManager 实例（用于断线重连）

        # 统计信息
        self._call_count = 0
        self._last_error = None

    def execute(
        self, session: BaseSession, args_json: str
    ) -> tuple[str | dict[str, Any] | list[Any], dict[str, Any]]:
        """执行 MCP 工具

        Args:
            session: Session 实例（MCP 工具不使用，但保持接口一致）
            args_json: JSON 格式的参数

        Returns:
            (observation, info) 元组
            - observation: 返回给 Agent 的观察结果，可为 str 或结构化 dict/list（单条 JSON 时保留）
            - info: 额外信息（包含 MCP 元数据）
        """
        try:
            # 1. 解析参数
            args = json.loads(args_json)
            self.logger.debug(f"Executing MCP tool {self._tool_name} with args: {args}")

            # 2. Path adaptor: path args must be URL links; local paths → upload then pass OSS URL
            path_adaptor = getattr(self, '_path_adaptor', None)
            if path_adaptor is not None:
                workspace_path = (
                    getattr(getattr(session, 'config', None), 'workspace_path', None)
                    if session
                    else None
                ) or ''

                # 尝试从 session 获取 Bohrium 凭证（如果存在）
                bohrium_creds = getattr(session, '_bohrium_credentials', None)
                access_key = None
                project_id = None
                user_id = None
                if bohrium_creds:
                    access_key = bohrium_creds.get('access_key')
                    project_id = bohrium_creds.get('project_id')
                    user_id = bohrium_creds.get('user_id')

                args = path_adaptor.resolve_args(
                    workspace_path,
                    args,
                    self._tool_name,
                    self._mcp_server or '',
                    input_schema=getattr(self, '_input_schema', None),
                    tool_description=getattr(self, '_tool_description', None),
                    access_key=access_key,
                    project_id=project_id,
                    user_id=user_id,
                    session=session,
                )

            # 3. 调用 MCP 工具（异步转同步）
            result = self._call_mcp_tool_sync(args)

            # 4. 格式化输出
            observation = self._format_mcp_result(result)

            # 5. 更新统计
            self._call_count += 1
            self._last_error = None

            info = {
                'mcp_tool': self._tool_name,
                'mcp_server': self._mcp_server,
                'success': True,
                'call_count': self._call_count,
            }

            return observation, info

        except json.JSONDecodeError as e:
            self._last_error = str(e)
            raise ToolError(f"Invalid JSON arguments: {str(e)}")
        except Exception as e:
            err_desc = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            self._last_error = err_desc
            self.logger.error(f"MCP tool {self._tool_name} failed: {err_desc}")
            raise ToolError(f"MCP tool execution failed: {err_desc}")

    def _call_mcp_tool_sync(self, args: dict) -> Any:
        """同步调用 MCP 工具（内部处理异步）

        ✅ 关键：禁止 asyncio.run() 产生临时 loop，必须把协程丢到“同一个长期 loop”里执行，
        否则会出现 anyio/mcp stream 卡死、ClosedResourceError、cancel scope 错位等问题。
        """
        max_attempts = 2  # 原始调用 + 重连后重试

        for attempt in range(max_attempts):
            # 1) 必须有一个被注入的 MCP loop
            loop = getattr(self, '_mcp_loop', None)
            if loop is None:
                raise ToolError(
                    'MCP loop not injected into MCPTool. '
                    'Please set mcp_tool._mcp_loop = <persistent_event_loop> when creating tools.'
                )
            if loop.is_closed():
                raise ToolError('MCP loop is closed; cannot call MCP tool')

            coro = self.mcp_connection.call_tool(self._remote_tool_name, args)

            try:
                # 2) 如果这个 loop 当前没有在运行（最常见：同步 Agent 场景），直接 run_until_complete
                if not loop.is_running():
                    return loop.run_until_complete(coro)

                # 3) 如果 loop 在运行（比如你把 loop 放到后台线程 run_forever），用线程安全提交
                timeout = getattr(self, '_call_timeout', DEFAULT_MCP_TOOL_CALL_TIMEOUT)
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                return fut.result(timeout=timeout)

            except concurrent.futures.TimeoutError:
                timeout = getattr(self, '_call_timeout', DEFAULT_MCP_TOOL_CALL_TIMEOUT)
                raise ToolError(f"MCP tool call timed out after {timeout} seconds")
            except Exception as e:
                err_desc = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                # 检查是否是 session 过期/断开错误，尝试重连后重试
                if attempt < max_attempts - 1 and self._is_session_error(e):
                    self.logger.warning(
                        "MCP session error for '%s', requesting reconnect (attempt %d/%d): %s",
                        self._tool_name,
                        attempt + 1,
                        max_attempts,
                        err_desc,
                    )
                    if self._try_reconnect():
                        continue  # 重连成功，用新 connection 重试
                    self.logger.error(
                        "MCP reconnect failed for '%s', giving up.",
                        self._tool_name,
                    )
                raise ToolError(f"Failed to call MCP tool: {err_desc}")

        raise ToolError(f"MCP tool call failed after {max_attempts} attempts")

    # ------------------------------------------------------------------
    # Session 断线重连辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _is_session_error(exc: Exception) -> bool:
        """判断异常是否表示 MCP session 过期/连接断开。

        Primary: ``isinstance`` check against anyio error types (works even
        when ``str(e)`` is empty).
        Fallback: class-name string matching for unforeseen exception types.
        """
        # 1) Direct type check — most reliable
        if _SESSION_ERROR_TYPES and isinstance(exc, _SESSION_ERROR_TYPES):
            return True
        # 2) Fallback: string matching on class name + message
        combined = (type(exc).__name__ + ' ' + str(exc)).lower()
        return any(
            ind in combined
            for ind in (
                'closedresourceerror',
                'brokenresourceerror',
                'closedresource',
                'brokenresource',
            )
        )

    def _try_reconnect(self, timeout: float = 30.0) -> bool:
        """通过 MCPToolManager 触发重连并等待完成。

        Pre-flight: 检查 loop 和 runner task 是否存活，死了就不浪费时间。
        Post-verify: ``done_event`` 被 set 不代表重连成功（manager 在
        loop 已关闭或重试耗尽时也会 set event）。必须检查
        ``self.mcp_connection.session`` 是否为有效的新 session。

        Returns:
            True  — 重连成功，``self.mcp_connection`` 已指向新的活跃连接。
            False — 无法重连、超时、或重连后 session 仍无效。
        """
        manager = getattr(self, '_mcp_manager', None)
        server_name = getattr(self, '_mcp_server', None)
        if not manager or not server_name:
            return False

        # ---- pre-flight: loop must be alive ----
        loop = getattr(manager, 'loop', None)
        if loop is None or loop.is_closed():
            self.logger.warning(
                "MCP loop is closed/missing, cannot reconnect server '%s'",
                server_name,
            )
            return False

        # ---- pre-flight: runner task must still be alive to process reconnect ----
        runner_task = manager._server_tasks.get(server_name)
        if runner_task is None or runner_task.done():
            self.logger.warning(
                "Runner task for MCP server '%s' is dead, cannot reconnect",
                server_name,
            )
            return False

        try:
            done_event = manager.request_reconnect(server_name)
            finished = done_event.wait(timeout=timeout)
            if not finished:
                self.logger.warning(
                    "MCP reconnection timed out for server '%s'",
                    server_name,
                )
                return False

            # ---- post-verify: connection must have a live session ----
            # After a successful reconnect, _update_tool_connections() swaps
            # self.mcp_connection to a *new* object whose .session was
            # initialised inside ``async with create_connection(...)``.
            # If the reconnect failed (3 retry attempts exhausted), the old
            # connection's __aexit__ already set .session = None.
            conn = self.mcp_connection
            if conn is not None and getattr(conn, 'session', None) is not None:
                self.logger.info(
                    "MCP server '%s' reconnected, retrying tool '%s'",
                    server_name,
                    self._tool_name,
                )
                return True

            self.logger.warning(
                "MCP reconnect for '%s' completed but connection session is "
                'still invalid (session=%s)',
                server_name,
                getattr(conn, 'session', 'N/A'),
            )
            return False

        except Exception as e:
            self.logger.warning("Reconnect attempt failed for '%s': %s", server_name, e)
            return False

    def _format_mcp_result(self, result: Any) -> str | dict[str, Any] | list[Any]:
        """格式化 MCP 工具返回结果，尽量保留结构化数据（dict/list）。

        MCP 返回的是 content 列表。若只有一条且为 JSON 文本，则解析为 dict/list 返回；
        否则拼接为字符串。这样如 mat_struct_db_fetch_structures_from_db 等工具可在
        tool_result 中保留 observation 为字典。

        Args:
            result: MCP 工具返回的原始结果（多为 content 列表）

        Returns:
            格式化结果：str、dict 或 list
        """
        self.logger.debug(
            '[observation] _format_mcp_result input tool=%s result_type=%s result_len=%s',
            self._tool_name,
            type(result).__name__,
            len(result) if isinstance(result, (list, str)) else 'N/A',
        )
        if isinstance(result, list):
            # MCP 返回的是 content 列表
            parts: list[str] = []
            for item in result:
                if hasattr(item, 'text'):
                    parts.append(item.text)
                elif isinstance(item, dict):
                    if 'text' in item:
                        parts.append(item['text'])
                    elif 'type' in item and item['type'] == 'text':
                        parts.append(item.get('text', ''))
                    else:
                        parts.append(json.dumps(item, indent=2))
                else:
                    parts.append(str(item))
            if not parts:
                return ''
            # 仅当恰好一条且为 JSON 时保留为 dict/list，便于下游 tool_result 使用
            if len(parts) == 1:
                text = parts[0].strip()
                if text.startswith('{') or text.startswith('['):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, (dict, list)):
                            self.logger.debug(
                                '[observation] _format_mcp_result returning dict/list keys=%s',
                                (
                                    list(parsed.keys())[:6]
                                    if isinstance(parsed, dict)
                                    else len(parsed)
                                ),
                            )
                            return parsed
                    except json.JSONDecodeError:
                        pass
            self.logger.debug(
                '[observation] _format_mcp_result returning str (parts=%s)',
                len(parts),
            )
            return '\n'.join(parts)
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            stripped = result.strip()
            if (stripped.startswith('{') or stripped.startswith('[')) and stripped:
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, (dict, list)):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return result
        if result is None:
            return ''
        return json.dumps(result, indent=2, default=str)

    def get_tool_spec(self) -> ToolSpec:
        """获取工具规格（用于 LLM function calling）

        将 MCP 的 schema 转换为 EvoMaster 的 ToolSpec。

        Returns:
            ToolSpec 实例
        """
        from evomaster.utils.types import FunctionSpec, ToolSpec

        return ToolSpec(
            type='function',
            function=FunctionSpec(
                name=self._tool_name,
                description=self._tool_description,
                parameters=self._input_schema,
                strict=None,
            ),
        )

    def get_stats(self) -> dict[str, Any]:
        """获取工具统计信息

        Returns:
            统计信息字典
        """
        return {
            'tool_name': self._tool_name,
            'mcp_server': self._mcp_server,
            'call_count': self._call_count,
            'last_error': self._last_error,
        }
