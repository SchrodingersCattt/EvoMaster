# MCP Concurrency Phase1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MatMaster 的 MCP 客户端增加一期受控并发能力，让同一 `server` 上的请求在不破坏 owner task 生命周期边界的前提下，按 `max_inflight` 上限并发执行，并支持 backpressure、关闭与快速回退。

**Architecture:** 保留 `matmaster.mcp.manager._ManagedConn` 作为单连接 owner task 生命周期容器，不再让 `_requests` 充当串行执行队列，而是改成调度入口。owner task 只负责 enter/startup/spawn/drain/exit，真正的 `conn.call_tool(...)` 在 child task 内部执行，并通过有界队列和 `Semaphore` 做受控并发。`matmaster.tools.lazy_mcp.configure_mcp_manager()` 负责把 `config/mcp.yaml` 中的并发策略注入 `MCPToolManager`，便于按 transport 和按 server 渐进启用。

**Tech Stack:** Python 3.13, `uv run pytest`, asyncio, anyio-backed MCP SDK, YAML 配置, pytest + `pytest.mark.asyncio`

---

## Scope Guardrails

### In Scope

1. 为 `MCPToolManager` 和 `_ManagedConn` 增加一期受控并发骨架。
2. 保留 `__aenter__` / `__aexit__` 必须由同一个 owner task 执行的约束。
3. 增加 `ManagedConnClosing`、`ManagedConnBackpressure`、`ManagedConnDead` 三类明确异常。
4. 支持 `serial` / `multiplex` 两种一期模式，以及 `max_inflight` / `max_pending_requests`。
5. 为 `configure_mcp_manager()` 加入并发配置注入。
6. 在 `config/mcp.yaml` 增加带注释的 rollout 示例。
7. 增加 SDK 前提验证、owner task 生命周期、并发、取消、关闭和配置注入测试。

### Out of Scope

1. 二期连接池。
2. 协议级 `CancelledNotification`。
3. 新增独立 metrics 子系统。
4. 重构 `LazyMCPConnector` 的线程/loop 结构。
5. 改动 `ToolRunner` 或 `Exp` 的 tool 调用入口。

## File Structure

### 新增文件

- `docs/superpowers/plans/2026-04-14-mcp-concurrency-phase1.md`
  - 本 implementation plan。
- `tests/matmaster/mcp/test_session_concurrency.py`
  - 锁定底层 MCP SDK 单 session 并发请求与乱序响应回填的前提。
- `tests/matmaster/mcp/test_manager_concurrency.py`
  - backpressure、取消、队列上限、fatal error 与关闭边界测试。

### 修改文件

- `matmaster/mcp/manager.py:25-39`
  - 新增并发 policy 和显式异常类型。
- `matmaster/mcp/manager.py:42-136`
  - 将 `_ManagedConn` 从串行执行器改为 owner-task 调度器。
- `matmaster/mcp/manager.py:152-188`
  - 给 `MCPToolManager` 增加并发策略存储与 transport 记录。
- `matmaster/mcp/manager.py:221-320`
  - `add_server()` / `_add_server_once()` / `call_tool()` 应用策略并保留现有启动语义。
- `matmaster/mcp/manager.py:452-482`
  - `cleanup()` 走新的 drain / force-close 语义。
- `matmaster/tools/lazy_mcp.py:295-342`
  - 在 `configure_mcp_manager()` 注入 `mcp_concurrency`。
- `config/mcp.yaml:1-108`
  - 增加带注释的一期并发配置示例。
- `tests/matmaster/mcp/test_manager.py:18-85`
  - 增加 manager 新属性与 policy helper 测试。
- `tests/matmaster/mcp/test_manager_owner_task.py:13-215`
  - 把旧的 same-task 断言改成 enter/list_tools/exit 同 task，并增加 multiplex 并发断言。
- `tests/matmaster/tools/test_lazy_mcp.py:595-688`
  - 扩展 `configure_mcp_manager()` 的并发配置注入测试。

## Task 1: 建立并发前提与 manager 配置骨架

**Files:**
- Create: `tests/matmaster/mcp/test_session_concurrency.py`
- Modify: `matmaster/mcp/manager.py:25-39`
- Modify: `matmaster/mcp/manager.py:152-188`
- Modify: `tests/matmaster/mcp/test_manager.py:18-85`

- [ ] **Step 1: 写聚焦测试，先锁 manager 新属性与 SDK 并发前提**

```python
# tests/matmaster/mcp/test_manager.py
def test_manager_has_empty_concurrency_policy_maps():
    from matmaster.mcp.manager import MCPToolManager

    manager = MCPToolManager()

    assert manager.concurrency_defaults_by_transport == {}
    assert manager.concurrency_by_server == {}
    assert manager._server_transports == {}


def test_default_stdio_policy_is_serial():
    from matmaster.mcp.manager import MCPConcurrencyPolicy

    policy = MCPConcurrencyPolicy.default_for_transport("stdio")

    assert policy.mode == "serial"
    assert policy.max_inflight == 1
    assert policy.max_pending_requests == 16
```

```python
# tests/matmaster/mcp/test_session_concurrency.py
from __future__ import annotations

import asyncio

from anyio import create_memory_object_stream
from mcp import types
from mcp.client.session import ClientSession
from mcp.shared.message import JSONRPCMessage, SessionMessage


async def test_client_session_routes_out_of_order_responses_by_request_id():
    server_to_client_send, server_to_client_recv = create_memory_object_stream(10)
    client_to_server_send, client_to_server_recv = create_memory_object_stream(10)

    async with ClientSession(server_to_client_recv, client_to_server_send) as session:
        async def fake_server():
            first = await client_to_server_recv.receive()
            second = await client_to_server_recv.receive()
            req1 = first.message.root
            req2 = second.message.root

            await server_to_client_send.send(
                SessionMessage(
                    message=JSONRPCMessage(
                        types.JSONRPCResponse(jsonrpc="2.0", id=req2.id, result={})
                    )
                )
            )
            await asyncio.sleep(0.05)
            await server_to_client_send.send(
                SessionMessage(
                    message=JSONRPCMessage(
                        types.JSONRPCResponse(jsonrpc="2.0", id=req1.id, result={})
                    )
                )
            )

        server_task = asyncio.create_task(fake_server())
        ping1 = asyncio.create_task(
            session.send_request(types.ClientRequest(types.PingRequest()), types.EmptyResult)
        )
        ping2 = asyncio.create_task(
            session.send_request(types.ClientRequest(types.PingRequest()), types.EmptyResult)
        )

        result1, result2 = await asyncio.gather(ping1, ping2)
        await server_task

    assert result1 is not None
    assert result2 is not None
```

- [ ] **Step 2: 运行聚焦测试，确认 manager 骨架先失败，SDK 回归测试先通过**

Run:

```bash
uv run pytest \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/mcp/test_session_concurrency.py \
  -k "policy or out_of_order_responses" -v
```

Expected:

- `test_manager_has_empty_concurrency_policy_maps` 失败，因为 `MCPToolManager` 还没有这些属性。
- `test_default_stdio_policy_is_serial` 失败，因为 `MCPConcurrencyPolicy` 还不存在。
- `test_client_session_routes_out_of_order_responses_by_request_id` 通过，用来锁定第三方 SDK 前提。

- [ ] **Step 3: 实现最小 policy / exception 骨架，不改动请求执行模型**

```python
# matmaster/mcp/manager.py
from dataclasses import dataclass
from typing import Any, Literal


class ManagedConnClosing(RuntimeError):
    """Raised when a managed connection is draining and rejects new work."""


class ManagedConnBackpressure(RuntimeError):
    """Raised when the per-connection pending queue is full."""


class ManagedConnDead(RuntimeError):
    """Raised when the owner task or underlying connection becomes unusable."""


@dataclass(frozen=True)
class MCPConcurrencyPolicy:
    mode: Literal["serial", "multiplex"] = "serial"
    max_inflight: int = 1
    max_pending_requests: int = 16

    @classmethod
    def default_for_transport(cls, transport: str) -> "MCPConcurrencyPolicy":
        transport = transport.lower()
        if transport == "stdio":
            return cls(mode="serial", max_inflight=1, max_pending_requests=16)
        return cls(mode="multiplex", max_inflight=4, max_pending_requests=64)
```

```python
# matmaster/mcp/manager.py
class MCPToolManager:
    def __init__(self) -> None:
        self.connections: dict[str, MCPConnection] = {}
        self.tools_by_server: dict[str, dict[str, dict[str, Any]]] = {}
        self._managed: dict[str, _ManagedConn] = {}
        self._startup_tasks: dict[str, asyncio.Task[None]] = {}
        self.calculation_preflight_servers: set[str] = set()
        self.calculation_preflight_factory: Callable[[], Any] | None = None
        self.sync_tools_by_server: dict[str, set[str]] = {}
        self.tool_include_only: dict[str, list[str]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._seen_tools: set[str] = set()
        self._closing = False

        self.concurrency_defaults_by_transport: dict[str, MCPConcurrencyPolicy] = {}
        self.concurrency_by_server: dict[str, MCPConcurrencyPolicy] = {}
        self._server_transports: dict[str, str] = {}
```

- [ ] **Step 4: 重新运行聚焦测试，确认骨架稳定**

Run:

```bash
uv run pytest \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/mcp/test_session_concurrency.py \
  -k "policy or out_of_order_responses" -v
```

Expected:

- 3 个测试全部 `PASS`
- 这一步不应触发真实 MCP 连接

- [ ] **Step 5: 提交骨架与前提测试**

```bash
git add \
  matmaster/mcp/manager.py \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/mcp/test_session_concurrency.py
git commit -m "test: lock MCP concurrency assumptions"
```

## Task 2: 把 `_ManagedConn` 改成 owner-task 受控调度器

**Files:**
- Modify: `matmaster/mcp/manager.py:42-136`
- Modify: `matmaster/mcp/manager.py:221-320`
- Modify: `tests/matmaster/mcp/test_manager_owner_task.py:13-215`

- [ ] **Step 1: 先写 owner-task 生命周期与 multiplex 并发失败测试**

```python
# tests/matmaster/mcp/test_manager_owner_task.py
class _TracingConn:
    def __init__(self, server_name: str, *, call_delay: float = 0.0) -> None:
        self.server_name = server_name
        self.call_delay = call_delay
        self.events: list[_Event] = []
        self.concurrent_calls = 0
        self.max_concurrent_calls = 0
        self.enter_count = 0
        self.list_tools_count = 0
        self._active_call_count = asyncio.Event()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]):
        self._record(f"call_tool:{tool_name}")
        self.concurrent_calls += 1
        self.max_concurrent_calls = max(
            self.max_concurrent_calls, self.concurrent_calls
        )
        self._active_call_count.set()
        try:
            if self.call_delay:
                await asyncio.sleep(self.call_delay)
            return [{"text": f"{self.server_name}:{arguments['value']}"}]
        finally:
            self.concurrent_calls -= 1


async def test_enter_list_tools_exit_stay_on_owner_task_only():
    manager = MCPToolManager()
    conn = _TracingConn("srv")

    with patch("matmaster.mcp.manager.create_connection", return_value=conn):
        await manager.add_server("srv", transport="http", url="http://srv")
        await manager.call_tool("srv", "remote_tool", {"value": "payload"})
        await manager.cleanup()

    lifecycle = [event for event in conn.events if event.name in {"enter", "list_tools", "exit"}]
    assert len({event.task_id for event in lifecycle}) == 1


async def test_same_server_requests_overlap_when_policy_is_multiplex():
    manager = MCPToolManager()
    manager.concurrency_defaults_by_transport["http"] = MCPConcurrencyPolicy(
        mode="multiplex",
        max_inflight=2,
        max_pending_requests=8,
    )
    conn = _TracingConn("srv", call_delay=0.1)

    with patch("matmaster.mcp.manager.create_connection", return_value=conn):
        await manager.add_server("srv", transport="http", url="http://srv")
        first = asyncio.create_task(manager.call_tool("srv", "remote_tool", {"value": "a"}))
        second = asyncio.create_task(manager.call_tool("srv", "remote_tool", {"value": "b"}))
        await asyncio.gather(first, second)
        await manager.cleanup()

    assert conn.max_concurrent_calls == 2
```

- [ ] **Step 2: 运行 owner-task 测试，确认当前实现仍是串行**

Run:

```bash
uv run pytest tests/matmaster/mcp/test_manager_owner_task.py -k "owner_task or overlap" -v
```

Expected:

- lifecycle 测试需要调整断言后才会通过
- `test_same_server_requests_overlap_when_policy_is_multiplex` 失败，当前实现 `max_concurrent_calls` 只能得到 `1`

- [ ] **Step 3: 重写 `_ManagedConn` 为 owner-task 调度器，但仍由 owner task 持有 enter/exit**

```python
# matmaster/mcp/manager.py
@dataclass
class _CallToolRequest:
    tool_name: str
    arguments: dict[str, Any]
    result: asyncio.Future[Any]


class _ManagedConn:
    def __init__(
        self,
        conn_ctx: MCPConnection,
        *,
        max_inflight: int = 1,
        max_pending_requests: int = 16,
    ) -> None:
        self._conn_ctx = conn_ctx
        self._ready: asyncio.Future[_StartupState] = (
            asyncio.get_running_loop().create_future()
        )
        self._requests: asyncio.Queue[_CallToolRequest | None] = asyncio.Queue(
            maxsize=max_pending_requests
        )
        self._sem = asyncio.Semaphore(max_inflight)
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._close_requested = asyncio.Event()
        self._drain_event = asyncio.Event()
        self._closing = False
        self._closed = False
        self._fatal_error: BaseException | None = None
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            async with self._conn_ctx as conn:
                tools_info = await asyncio.wait_for(
                    conn.list_tools(), timeout=MCP_CONNECT_TIMEOUT
                )
                self._ready.set_result(
                    _StartupState(connection=conn, tools_info=tools_info)
                )

                while True:
                    if self._close_requested.is_set():
                        await self._wait_for_drain()
                        return

                    request = await self._requests.get()
                    if request is None:
                        self._close_requested.set()
                        continue
                    if request.result.cancelled():
                        self._maybe_mark_drained()
                        continue

                    task = asyncio.create_task(self._execute_request(conn, request))
                    self._active_tasks.add(task)
                    task.add_done_callback(self._on_task_done)
        except BaseException as exc:
            self._fatal_error = exc
            if not self._ready.done():
                self._ready.set_exception(exc)
            self._fail_all_pending(ManagedConnDead("MCP owner task crashed"))
        finally:
            self._closed = True

    async def _execute_request(
        self,
        conn: MCPConnection,
        request: _CallToolRequest,
    ) -> None:
        async with self._sem:
            if request.result.cancelled():
                return
            try:
                result = await conn.call_tool(request.tool_name, request.arguments)
            except Exception as exc:
                if not request.result.done():
                    request.result.set_exception(exc)
            else:
                if not request.result.done():
                    request.result.set_result(result)
```

```python
# matmaster/mcp/manager.py
class MCPToolManager:
    def _resolve_policy(self, server_name: str, transport: str) -> MCPConcurrencyPolicy:
        if server_name in self.concurrency_by_server:
            return self.concurrency_by_server[server_name]
        if transport in self.concurrency_defaults_by_transport:
            return self.concurrency_defaults_by_transport[transport]
        return MCPConcurrencyPolicy.default_for_transport(transport)
```

```python
# matmaster/mcp/manager.py
async def _add_server_once(
    self, name: str, transport: str, **connection_kwargs: Any
) -> None:
    if name in self.connections:
        return

    logger.info("Adding MCP server: %s (%s)", name, transport)
    conn_ctx = create_connection(transport=transport, **connection_kwargs)
    policy = self._resolve_policy(name, transport)
    managed = _ManagedConn(
        conn_ctx,
        max_inflight=policy.max_inflight,
        max_pending_requests=policy.max_pending_requests,
    )
    self._managed[name] = managed
    self._server_transports[name] = transport
    startup = await managed.wait_ready(timeout=MCP_CONNECT_TIMEOUT)
    self.connections[name] = startup.connection
    self._build_tools(name, startup.connection, startup.tools_info)
```

- [ ] **Step 4: 运行 owner-task 测试，确认 lifecycle 还在、并发已经打开**

Run:

```bash
uv run pytest tests/matmaster/mcp/test_manager_owner_task.py -k "owner_task or overlap" -v
```

Expected:

- lifecycle 断言通过，`enter/list_tools/exit` 仍然属于同一个 task
- overlap 测试通过，`max_concurrent_calls == 2`

- [ ] **Step 5: 提交 owner-task 调度器改造**

```bash
git add matmaster/mcp/manager.py tests/matmaster/mcp/test_manager_owner_task.py
git commit -m "feat: multiplex MCP calls within owner task"
```

## Task 3: 完成 backpressure、取消与 fatal error 语义

**Files:**
- Modify: `matmaster/mcp/manager.py:42-136`
- Modify: `matmaster/mcp/manager.py:124-136`
- Create: `tests/matmaster/mcp/test_manager_concurrency.py`

- [ ] **Step 1: 先写失败测试，覆盖队列上限、取消、错误释放与 owner crash**

```python
# tests/matmaster/mcp/test_manager_concurrency.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from matmaster.mcp.manager import (
    MCPConcurrencyPolicy,
    MCPToolManager,
    ManagedConnBackpressure,
    ManagedConnClosing,
    ManagedConnDead,
)


@dataclass
class _Event:
    name: str


class _TracingConn:
    def __init__(
        self,
        server_name: str,
        *,
        call_delay: float = 0.0,
        release_event: asyncio.Event | None = None,
        started_event: asyncio.Event | None = None,
    ) -> None:
        self.server_name = server_name
        self.call_delay = call_delay
        self.release_event = release_event
        self.started_event = started_event
        self.events: list[_Event] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "remote_tool",
                "description": "Remote tool",
                "input_schema": {"type": "object"},
            }
        ]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> list[dict[str, str]]:
        self.events.append(
            _Event(name=f"call_tool:{tool_name}:{arguments.get('value', '')}")
        )
        if self.started_event is not None:
            self.started_event.set()
        if self.release_event is not None:
            await self.release_event.wait()
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        return [{"text": f"{self.server_name}:{arguments.get('value', '')}"}]


async def test_call_tool_raises_backpressure_when_pending_queue_is_full():
    manager = MCPToolManager()
    manager.concurrency_defaults_by_transport["http"] = MCPConcurrencyPolicy(
        mode="multiplex",
        max_inflight=1,
        max_pending_requests=1,
    )
    conn = _TracingConn("srv", call_delay=0.2)

    with patch("matmaster.mcp.manager.create_connection", return_value=conn):
        await manager.add_server("srv", transport="http", url="http://srv")
        first = asyncio.create_task(manager.call_tool("srv", "remote_tool", {"value": "a"}))
        second = asyncio.create_task(manager.call_tool("srv", "remote_tool", {"value": "b"}))
        await asyncio.sleep(0.02)

        with pytest.raises(ManagedConnBackpressure):
            await manager.call_tool("srv", "remote_tool", {"value": "c"})

        await asyncio.gather(first, second)
        managed = manager._managed["srv"]
        assert managed._rejected_backpressure_count == 1
        await manager.cleanup()


async def test_cancelled_request_is_dropped_before_real_execution():
    manager = MCPToolManager()
    manager.concurrency_defaults_by_transport["http"] = MCPConcurrencyPolicy(
        mode="multiplex",
        max_inflight=1,
        max_pending_requests=4,
    )
    conn = _TracingConn("srv", call_delay=0.1)

    with patch("matmaster.mcp.manager.create_connection", return_value=conn):
        await manager.add_server("srv", transport="http", url="http://srv")
        blocker = asyncio.create_task(manager.call_tool("srv", "remote_tool", {"value": "blocking"}))
        cancelled = asyncio.create_task(manager.call_tool("srv", "remote_tool", {"value": "cancel-me"}))
        await asyncio.sleep(0.02)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await blocker
        managed = manager._managed["srv"]
        assert managed._requests_cancelled == 1
        await manager.cleanup()

    assert [event.name for event in conn.events].count("call_tool:remote_tool:blocking") == 1
    assert [event.name for event in conn.events].count("call_tool:remote_tool:cancel-me") == 0


async def test_call_tool_raises_managed_conn_closing_after_cleanup_starts():
    manager = MCPToolManager()
    release_event = asyncio.Event()
    started_event = asyncio.Event()
    conn = _TracingConn(
        "srv",
        release_event=release_event,
        started_event=started_event,
    )

    with patch("matmaster.mcp.manager.create_connection", return_value=conn):
        await manager.add_server("srv", transport="http", url="http://srv")
        running = asyncio.create_task(
            manager.call_tool("srv", "remote_tool", {"value": "blocking"})
        )
        await asyncio.wait_for(started_event.wait(), timeout=1.0)

        cleanup_task = asyncio.create_task(manager.cleanup())
        await asyncio.sleep(0.02)

        with pytest.raises(ManagedConnClosing):
            await manager.call_tool("srv", "remote_tool", {"value": "late"})

        release_event.set()
        await running
        await cleanup_task
        managed = manager._managed["srv"]
        assert managed._rejected_closing_count == 1


async def test_owner_task_crash_fails_future_with_managed_conn_dead():
    manager = MCPToolManager()
    conn = _TracingConn("srv")

    with patch("matmaster.mcp.manager.create_connection", return_value=conn):
        await manager.add_server("srv", transport="http", url="http://srv")
        managed = manager._managed["srv"]

        async def broken_get():
            raise RuntimeError("queue broken")

        first = await manager.call_tool("srv", "remote_tool", {"value": "ok"})
        assert first == [{"text": "srv:ok"}]

        managed._requests.get = broken_get  # type: ignore[assignment]
        with pytest.raises(ManagedConnDead):
            await manager.call_tool("srv", "remote_tool", {"value": "boom"})
```

- [ ] **Step 2: 运行新测试，确认当前实现缺少这些边界**

Run:

```bash
uv run pytest tests/matmaster/mcp/test_manager_concurrency.py -v
```

Expected:

- backpressure 测试失败，因为当前 `call_tool()` 仍会阻塞入队
- cancelled request 测试失败，因为当前第二个请求还是会真正执行
- cleanup 中拒绝新请求的测试失败，因为当前没有 `ManagedConnClosing`
- owner crash 测试失败，因为 pending future 可能挂住或得到不精确异常

- [ ] **Step 3: 实现有界入队、取消跳过、fatal fail 和强制关闭**

```python
# matmaster/mcp/manager.py
class _ManagedConn:
    def __init__(
        self,
        conn_ctx: MCPConnection,
        *,
        max_inflight: int = 1,
        max_pending_requests: int = 16,
    ) -> None:
        self._conn_ctx = conn_ctx
        self._ready = asyncio.get_running_loop().create_future()
        self._requests = asyncio.Queue(maxsize=max_pending_requests)
        self._sem = asyncio.Semaphore(max_inflight)
        self._active_tasks = set()
        self._close_requested = asyncio.Event()
        self._drain_event = asyncio.Event()
        self._closing = False
        self._closed = False
        self._fatal_error = None
        self._task = asyncio.create_task(self._run())
        self._rejected_closing_count = 0
        self._rejected_backpressure_count = 0
        self._requests_succeeded = 0
        self._requests_failed = 0
        self._requests_cancelled = 0
        self._forced_close_count = 0
        self._last_cleanup_drain_seconds = 0.0

async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
    if self._closed:
        raise ManagedConnDead("MCP connection is closed")
    if self._closing:
        self._rejected_closing_count += 1
        logger.debug("mcp.admission.reject_closing")
        raise ManagedConnClosing("MCP connection is closing")
    if self._fatal_error is not None:
        raise ManagedConnDead("MCP owner task is unavailable")

    loop = asyncio.get_running_loop()
    result: asyncio.Future[Any] = loop.create_future()
    request = _CallToolRequest(
        tool_name=tool_name,
        arguments=arguments,
        result=result,
    )
    try:
        self._requests.put_nowait(request)
    except asyncio.QueueFull as exc:
        self._rejected_backpressure_count += 1
        logger.debug(
            "mcp.admission.backpressure pending=%s inflight=%s",
            self._requests.qsize(),
            len(self._active_tasks),
        )
        raise ManagedConnBackpressure("MCP pending queue is full") from exc
    return await result
```

```python
# matmaster/mcp/manager.py
async def _execute_request(
    self,
    conn: MCPConnection,
    request: _CallToolRequest,
) -> None:
    try:
        async with self._sem:
            if request.result.cancelled():
                self._requests_cancelled += 1
                logger.debug("mcp.cancel.before_execute")
                return
            try:
                logger.debug("mcp.execute.start inflight=%s", len(self._active_tasks))
                result = await conn.call_tool(request.tool_name, request.arguments)
            except Exception:
                self._requests_failed += 1
                raise
            else:
                self._requests_succeeded += 1
                if not request.result.done():
                    request.result.set_result(result)
    except Exception as exc:
        if not request.result.done():
            request.result.set_exception(exc)


def _fail_all_pending(self, exc: BaseException) -> None:
    while not self._requests.empty():
        item = self._requests.get_nowait()
        if item is not None and not item.result.done():
            item.result.set_exception(exc)


def _on_task_done(self, task: asyncio.Task[None]) -> None:
    self._active_tasks.discard(task)
    self._maybe_mark_drained()


def _maybe_mark_drained(self) -> None:
    if self._close_requested.is_set() and self._requests.empty() and not self._active_tasks:
        self._drain_event.set()
```

```python
# matmaster/mcp/manager.py
async def close(self, timeout: float) -> None:
    self._closing = True
    self._close_requested.set()
    started = asyncio.get_running_loop().time()
    try:
        self._requests.put_nowait(None)
    except asyncio.QueueFull:
        pass

    try:
        await asyncio.wait_for(self._drain_event.wait(), timeout=timeout)
    except TimeoutError:
        self._fail_all_pending(ManagedConnClosing("MCP connection is closing"))
        for task in list(self._active_tasks):
            task.cancel()
            self._forced_close_count += 1
        await asyncio.gather(*self._active_tasks, return_exceptions=True)
    finally:
        self._last_cleanup_drain_seconds = (
            asyncio.get_running_loop().time() - started
        )
        logger.debug(
            "mcp.drain.complete pending=%s inflight=%s cancelled=%s forced=%s drain_s=%.3f",
            self._requests.qsize(),
            len(self._active_tasks),
            self._requests_cancelled,
            self._forced_close_count,
            self._last_cleanup_drain_seconds,
        )
        if not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
```

- [ ] **Step 4: 跑新测试和 manager cleanup 测试，确认关闭语义稳定**

Run:

```bash
uv run pytest \
  tests/matmaster/mcp/test_manager_concurrency.py \
  tests/matmaster/mcp/test_manager.py \
  -k "cleanup or concurrency or backpressure or dead" -v
```

Expected:

- 新增的 4 个并发边界测试全部 `PASS`
- 原有 cleanup 测试仍保持通过
- `manager._managed["srv"]` 上的排队长度、in-flight 和计数字段可直接断言，一期最小可观测性已经具备

- [ ] **Step 5: 提交关闭与 backpressure 语义**

```bash
git add \
  matmaster/mcp/manager.py \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/mcp/test_manager_concurrency.py
git commit -m "feat: add MCP backpressure and shutdown semantics"
```

## Task 4: 接入配置注入并完成一期回归验证

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py:295-342`
- Modify: `config/mcp.yaml:1-108`
- Modify: `tests/matmaster/tools/test_lazy_mcp.py:595-688`
- Modify: `tests/matmaster/mcp/test_manager.py:18-85`

- [ ] **Step 1: 先写失败测试，锁定 `configure_mcp_manager()` 的并发配置注入**

```python
# tests/matmaster/tools/test_lazy_mcp.py
class FakeMCPManager:
    def __init__(self):
        self.calculation_preflight_servers = set()
        self.calculation_preflight_factory = None
        self.sync_tools_by_server = {}
        self.tool_include_only = {}
        self.concurrency_defaults_by_transport = {}
        self.concurrency_by_server = {}


def test_sets_transport_level_concurrency_defaults():
    manager = FakeMCPManager()
    config = {
        "mcp_concurrency": {
            "defaults": {
                "http": {
                    "mode": "multiplex",
                    "max_inflight": 6,
                    "max_pending_requests": 64,
                },
                "stdio": {
                    "mode": "serial",
                    "max_inflight": 1,
                    "max_pending_requests": 16,
                },
            }
        }
    }

    configure_mcp_manager(manager, config)

    assert manager.concurrency_defaults_by_transport["http"].max_inflight == 6
    assert manager.concurrency_defaults_by_transport["stdio"].mode == "serial"


def test_server_override_wins_over_transport_default():
    manager = FakeMCPManager()
    config = {
        "mcp_concurrency": {
            "defaults": {
                "http": {
                    "mode": "multiplex",
                    "max_inflight": 6,
                    "max_pending_requests": 64,
                }
            },
            "servers": {
                "mat_doc": {
                    "mode": "serial",
                    "max_inflight": 1,
                    "max_pending_requests": 8,
                }
            },
        }
    }

    configure_mcp_manager(manager, config)

    assert manager.concurrency_by_server["mat_doc"].mode == "serial"
```

```python
# tests/matmaster/mcp/test_manager.py
def test_resolve_policy_prefers_server_override_over_transport_default():
    from matmaster.mcp.manager import MCPConcurrencyPolicy, MCPToolManager

    manager = MCPToolManager()
    manager.concurrency_defaults_by_transport["http"] = MCPConcurrencyPolicy(
        mode="multiplex",
        max_inflight=6,
        max_pending_requests=64,
    )
    manager.concurrency_by_server["mat_doc"] = MCPConcurrencyPolicy(
        mode="serial",
        max_inflight=1,
        max_pending_requests=8,
    )

    policy = manager._resolve_policy("mat_doc", "http")

    assert policy.mode == "serial"
    assert policy.max_inflight == 1
```

- [ ] **Step 2: 运行配置测试，确认当前还没有并发配置注入**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/test_lazy_mcp.py \
  tests/matmaster/mcp/test_manager.py \
  -k "concurrency_defaults or server_override" -v
```

Expected:

- `FakeMCPManager` 相关测试失败，因为 `configure_mcp_manager()` 还没处理 `mcp_concurrency`
- `_resolve_policy` 测试通过或失败都可以接受，但如果 helper 还不存在则应先失败

- [ ] **Step 3: 在 `lazy_mcp.py` 注入并发配置，并更新 `config/mcp.yaml` 示例**

```python
# matmaster/tools/lazy_mcp.py
def _parse_concurrency_policy(raw: Any) -> MCPConcurrencyPolicy | None:
    if not isinstance(raw, dict):
        return None
    mode = raw.get("mode")
    max_inflight = raw.get("max_inflight")
    max_pending = raw.get("max_pending_requests")
    if mode not in {"serial", "multiplex"}:
        return None
    if not isinstance(max_inflight, int) or max_inflight <= 0:
        return None
    if not isinstance(max_pending, int) or max_pending <= 0:
        return None
    return MCPConcurrencyPolicy(
        mode=mode,
        max_inflight=max_inflight,
        max_pending_requests=max_pending,
    )


def configure_mcp_manager(manager: Any, mcp_config: dict, all_server_names: set[str] | None = None) -> None:
    if mcp_config.get("calculation_preflight") == "calculation":
        calc_servers = mcp_config.get("calculation_servers")
        if calc_servers:
            manager.calculation_preflight_servers = set(calc_servers)
        elif all_server_names:
            manager.calculation_preflight_servers = set(all_server_names)
        try:
            from matmaster.mcp.calculation.preflight import CalculationPreflight

            manager.calculation_preflight_factory = lambda: CalculationPreflight(
                mcp_config.get("calculation_executors") or {}
            )
        except ImportError:
            logger.warning(
                "matmaster.mcp.calculation not available, skipping preflight"
            )

        executors = mcp_config.get("calculation_executors") or {}
        manager.sync_tools_by_server = {
            name: set(cfg.get("sync_tools") or [])
            for name, cfg in executors.items()
            if isinstance(cfg, dict) and cfg.get("sync_tools")
        }

    concurrency = mcp_config.get("mcp_concurrency")
    if isinstance(concurrency, dict):
        defaults = concurrency.get("defaults") or {}
        servers = concurrency.get("servers") or {}
        manager.concurrency_defaults_by_transport = {
            transport: policy
            for transport, raw_policy in defaults.items()
            if (policy := _parse_concurrency_policy(raw_policy)) is not None
        }
        manager.concurrency_by_server = {
            server_name: policy
            for server_name, raw_policy in servers.items()
            if (policy := _parse_concurrency_policy(raw_policy)) is not None
        }

    include_only = mcp_config.get("tool_include_only")
    if include_only and isinstance(include_only, dict):
        manager.tool_include_only = {
            k: list(v) if isinstance(v, (list, tuple)) else []
            for k, v in include_only.items()
        }
```

```yaml
# config/mcp.yaml
mcp_concurrency:
  defaults:
    http:
      mode: "multiplex"
      max_inflight: 6
      max_pending_requests: 64
    sse:
      mode: "multiplex"
      max_inflight: 6
      max_pending_requests: 64
    stdio:
      mode: "serial"
      max_inflight: 1
      max_pending_requests: 16
  servers:
    mat_doc:
      mode: "multiplex"
      max_inflight: 8
      max_pending_requests: 96
    mat_struct_db:
      mode: "multiplex"
      max_inflight: 8
      max_pending_requests: 96
    mat_nmr:
      mode: "serial"
      max_inflight: 1
      max_pending_requests: 16
```

- [ ] **Step 4: 跑一期完整回归集，确认 manager、connector 和配置都收口**

Run:

```bash
uv run pytest \
  tests/matmaster/mcp/test_session_concurrency.py \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/mcp/test_manager_owner_task.py \
  tests/matmaster/mcp/test_manager_concurrency.py \
  tests/matmaster/tools/test_lazy_mcp.py \
  -v
```

Expected:

- 所有 MCP 一期相关测试全部 `PASS`
- 没有新的 hanging test
- 任何 cleanup 相关测试都不应出现超时

- [ ] **Step 5: 提交配置注入与最终回归**

```bash
git add \
  matmaster/tools/lazy_mcp.py \
  config/mcp.yaml \
  tests/matmaster/tools/test_lazy_mcp.py \
  tests/matmaster/mcp/test_manager.py
git commit -m "feat: configure MCP concurrency policies"
```

## Self-Review

### Spec coverage

- spec 第 4 节的 owner task 生命周期约束
  - Task 2 和 Task 3 覆盖。
- spec 第 6 到 8 节的 bounded concurrency、backpressure、取消、关闭与异常类型
  - Task 2 和 Task 3 覆盖。
- spec 第 6.4 节的一期最小可观测性
  - Task 3 通过计数字段、排队长度和 drain 耗时覆盖。
- spec 第 9 到 10 节的配置注入与 rollout 结构
  - Task 4 覆盖。
- spec 第 11 节的 SDK 前提验证与测试矩阵
  - Task 1、Task 2、Task 3、Task 4 覆盖。

### Placeholder scan

- 未使用占位表述，且每个代码步骤都附带明确片段和命令。
- 每个改代码步骤都给了明确代码片段和命令。

### Type consistency

- 统一使用 `MCPConcurrencyPolicy`
- 统一使用 `ManagedConnClosing`、`ManagedConnBackpressure`、`ManagedConnDead`
- 统一使用 `max_inflight`、`max_pending_requests`
