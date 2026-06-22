# Trigger 前端感知（后端）实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在后端新增用户级 wakeup stream（任意 MatMaster 页面感知 session 后台唤醒），让后台 trigger 入队后向用户级 Redis channel 发一条最小 wakeup 信号；并把内部 HTTP trigger 改成 subscribe-before-enqueue，消除先入队后订阅的早期事件丢失竞态。

**Architecture:** 三层由内向外。DAO（`redis_dao`）新增用户级 channel 发布 + 一条按 user_id/status 查 session 的窄查询；service（`stream_service`）新增 `_publish_user_wakeup` 辅助、`generate_wakeup_stream`（snapshot + 订阅用户 channel）、并把 `trigger_run` 的前半段抽成可复用的 `prepare_internal_trigger_run`，再新增 `generate_internal_trigger_stream` 对齐发送路径的 subscribe-before-enqueue；API 层新增 `GET /api/v1/chat/wakeup/stream` 端点（独立 router，登录强制），并把内部 HTTP trigger 分派改为 prepare + generate 两段。前端状态机（spec §7、§9.5）属于另一个仓库，单独成计划，本计划只把它依赖的协议契约固定下来。

**Tech Stack:** Python ≥3.10、FastAPI、Redis pub/sub（`redis-py`）、`pymysql` 裸 SQL（DAO 层）、`pytest` + `pytest-asyncio`（`asyncio_mode=auto`）+ `unittest.mock`，运行 `uv run pytest`。

---

## 背景与数据来源验证（执行前必读）

以下事实均已逐行核实，请直接信任，不要怀疑"字段/函数可能不存在"：

1. **SSE 帧格式**：`ChatStreamService.sse_format(payload)`（`src/services/stream_service.py:157-162`）是静态方法，输出 `event: ag-ui\ndata: {json}\n\n`，`AG_UI_EVENT="ag-ui"`。任意 dict payload 都能套进去。
2. **Redis channel 约定与 publish 模板**：channel 常量集中在 `src/dao/redis_dao.py:20-44`；通用发布 `RedisDao.publish(channel, message) -> bool`（`redis_dao.py:107-117`，无 client 时返回 False）；会话级封装 `publish_stream_event(session_id, payload)`（`redis_dao.py:119-131`）是本计划新增 `publish_user_wakeup` 的直接模板；`create_client()`（`redis_dao.py:103-105`）供订阅线程取独立连接。
3. **`trigger_run` 现状**（`stream_service.py:353-434`）：顺序为 owner 校验 → dedup 预检 → `_resolve_mode` + model 继承（`model_val is None` 时取 `get_last_resolved_model_profile`，`stream_service.py:382-384` 已存在）→ `_prepare_run`（写 `System/trigger` + 组装 job，不入队）→ `_enqueue_run` → `mark_dedup_key_nx` → 返回。`TriggerResult.status` 只有四个：`enqueued | deduped | busy | error`（`stream_service.py:122-130`）；`enqueue_failed`、`session_not_found_or_no_owner` 是 `error` 的 reason；额度不足在 API 层抛异常，不进 `trigger_run`。
4. **`_enqueue_run` 是三路共享的入队核心**（`stream_service.py:340-351`）：普通用户发送（`generate_send_stream`）、后台 trigger（`trigger_run`）、内部 HTTP trigger 都经它入队。**因此 `publish_user_wakeup` 绝不能写进 `_enqueue_run`**，否则普通前台发送也会误发 wakeup。这是本计划最重要的约束（spec §6.1 发布点约束）。
5. **subscribe-before-enqueue 不变量 + 软等待**：`generate_send_stream`（`stream_service.py:795-881`）先推 status + history replay + 发起事件 → `_start_redis_stream_subscription`（`stream_service.py:54-102`，订阅 `chat:stream:{session_id}`）→ `await asyncio.to_thread(subscribe_ready.wait, 3.0)`（**软等待：超时只 warning、照常入队**，`stream_service.py:837-842`）→ `_enqueue_run`（`:843`）→ while 循环转发 redis_queue 事件、收到 `stream_closed` 退出，`finally` 里 `shutdown_event.set()` + `sub_thread.join`。内部 trigger 的 generator 段照此结构。
6. **内部 HTTP trigger 现状（要改的竞态）**：`_handle_internal_trigger`（`src/apis/chat_api.py:129-181`）先 `await asyncio.to_thread(stream_svc.trigger_run, ...)`（内部已入队），`status=="enqueued"` 后才 `generate_subscribe_stream`。即 enqueue-then-subscribe，订阅建立前 Worker publish 且尚未落库的早期事件会被错过。
7. **路由聚合**：`src/apis/api_router.py:5-9`：`api_router = APIRouter()`，`api_router.include_router(chat_api.router, prefix='/chat/sessions')`。`app.py:119` 以 `prefix='/api/v1'` 挂 `api_router`。所以 `chat_api.router` 下的路由都在 `/api/v1/chat/sessions/...`。wakeup 端点 `GET /api/v1/chat/wakeup/stream` **不能**加进 `chat_api.router`（会变成 `/chat/sessions/...`），必须新建 router 以 `prefix='/chat'` 挂进 `api_router`。
8. **share route 隔离**：`src/apis/share_router.py` 只 `include_router(share_chat_router, prefix='/chat/sessions')`，且 `share_chat_router` 只暴露 `POST /{session_id}/stream`。只要不把 wakeup router 加进 `share_router`，它天然不在 `/pubapi/v1` 下。判断 share 路由用 `request.url.path.startswith("/pubapi/")`（`chat_api.py:312`）。
9. **鉴权**：`UserService.require_user_id`（强制 X-User-Id，缺失 401，`user_service.py:53-72`）与 `optional_user_id`（可选，`:74-88`）都是 FastAPI `Depends`。list 端点用 `require_user_id`（`chat_api.py:229`），现有 stream 用 `optional_user_id`（分享只读）。wakeup 端点用 **`require_user_id`**。
10. **session 状态四态**：`idle | active | waiting | failed`（`sessions_service.py:347-351`、`get_session_status_payload` 同处）。**当前没有**"按 user_id 过滤 status"的查询：`list_sessions`（`chat_sessions_table.py:358`）不按 status 过滤，`count_active_sessions`（`:305`）不按 user 过滤。snapshot 需新增查询。`get_session_user_id`（`sessions_service.py:376-382`）取 owner。
11. **DAO 查询模板**：`chat_sessions_table.py` 用 `BaseTable`，方法体形如 `with self.get_connection() as conn: with conn.cursor() as cursor: cursor.execute(sql, params); rows = cursor.fetchall()`，DictCursor（按列名取值，见 `count_active_sessions:305-318` 与 `list_sessions:358-400`）。`self.table_name` 为表名。
12. **测试风格**（`tests/`，`pytest.ini`：`asyncio_mode=auto`、`pythonpath=.`）：
    - 不用 fixture 造 service，直接 `MagicMock` + `ChatStreamService(sessions_service=, events_service=, deploy_state_service=)` 注入（`tests/test_agent_run_trigger.py:122-134` `_make_service`、`:277-291` `_make_trigger_service`、`:294+` `_trigger_patches`）。
    - DAO 测试 patch 客户端：`patch.object(dao, "get_command_client", return_value=fake_client)` 或 `get_publish_client`（`tests/test_agent_run_trigger.py:39-78`）。
    - 表 SQL 测试用 `conftest.py:142-166` 的 `chat_events_table_with_mocks` 模式（`patch.object(Table, "init_table", lambda self: None)` + mock 出 `get_connection().__enter__→conn`、`conn.cursor().__enter__→cursor`，`cursor.execute.call_args[0]` 取 `(sql, params)`）。
    - 端点测试用 `TestClient(app)` + `app.dependency_overrides[get_sessions_service/get_stream_service]`（`tests/test_chat_internal_trigger_api.py:13-22`），X-User-Id 走 `headers={"X-User-Id": ...}`。
    - SSE 帧解析：`json.loads(frame.split("data: ", 1)[1])`，回放多帧用 `chunk.split("\n\n")` 切分（`tests/test_chat_stream_direct.py`）。
    - 运行：`uv run pytest tests/<file>.py -v`。无 ruff/mypy 强制。

---

## 协议契约（前端计划依赖，先固定）

后端实现完成后，前端（另一仓库）按此对接。本计划所有 task 都不得偏离：

- **端点**：`GET /api/v1/chat/wakeup/stream`，登录强制（`X-User-Id`），SSE（`event: ag-ui`）。建连先订阅再发 snapshot，然后转发 live（前端仍是先收到 snapshot 帧、再收到 live 帧）。
- **心跳**：流空闲时发 SSE comment（`: keepalive\n\n`）保活，不进入 ag-ui data，浏览器 EventSource 透明忽略，前端无需处理；ag-ui data 帧严格只有 `session_wakeup`。
- **wakeup payload（严格四字段，禁止其它）**：
  ```json
  {"source": "System", "type": "session_wakeup", "reason": "<reason>", "session_id": "sess_xxx"}
  ```
- **reason 枚举**：`trigger_enqueued`（后台 trigger 入队后 live 推送）、`session_waiting_snapshot`（建连/重连时对每个 waiting/active session 各发一条）。
- **Redis channel**：`chat:user:{user_id}:wakeup`。
- **发布判定**：当且仅当 trigger 入队成功（`status=="enqueued"`）才 publish；deduped/busy/error 不发。publish 失败只 warning，不回滚 trigger。

---

## File Structure

新增 / 修改文件及职责：

- `src/dao/redis_dao.py`（修改）：新增 `USER_WAKEUP_CHANNEL_PREFIX` 常量、模块级 `user_wakeup_channel(user_id)`、`RedisDao.publish_user_wakeup(user_id, payload)`。
- `src/dao/chat_sessions_table.py`（修改）：新增 `list_session_ids_by_status(user_id, statuses) -> list[str]`。
- `src/services/sessions_service.py`（修改）：新增 `list_waiting_or_active_session_ids(user_id) -> list[str]` 封装 DAO。
- `src/services/stream_service.py`（修改）：新增 `TriggerStreamContext` dataclass、`_publish_user_wakeup`、`prepare_internal_trigger_run`（抽自 `trigger_run`）、`generate_wakeup_stream`、`generate_internal_trigger_stream`；把 `_start_redis_stream_subscription` 重构为薄封装、新增底层 `_start_redis_channel_subscription`；`trigger_run` 改为复用 `prepare_internal_trigger_run` 并在入队后 publish。
- `src/apis/wakeup_api.py`（新建）：`GET /wakeup/stream` 端点。
- `src/apis/api_router.py`（修改）：以 `prefix='/chat'` 挂 `wakeup_api.router`。
- `src/apis/chat_api.py`（修改）：`_handle_internal_trigger` 改为 prepare + generate 两段；顶部 import 加 `TriggerStreamContext`。
- 测试：`tests/test_redis_dao_user_wakeup.py`（新）、`tests/test_chat_sessions_table_status.py`（新）、`tests/test_agent_run_trigger.py`（改：trigger publish 用例）、`tests/test_wakeup_stream.py`（新）、`tests/test_internal_trigger_timing.py`（新）、`tests/test_chat_internal_trigger_api.py`（改：适配 prepare/generate）。

提交顺序按运行时依赖：DAO publish（T1）→ DAO 查询（T2）→ trigger publish（T3）→ wakeup stream 端到端（T4）→ 内部 trigger 时序（T5）。

---

## Task 1: Redis 用户级 wakeup 发布（redis_dao）

最底层，无依赖。新增按 user_id 计算的 channel 与发布方法，照 `publish_stream_event` 模板。

**Files:**
- Create: `tests/test_redis_dao_user_wakeup.py`
- Modify: `src/dao/redis_dao.py`（常量区 + `publish_stream_event` 方法之后）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_redis_dao_user_wakeup.py`：

```python
"""RedisDao.publish_user_wakeup 与 user_wakeup_channel 行为测试。"""

import json
from unittest.mock import MagicMock, patch


def test_user_wakeup_channel_format():
    from src.dao.redis_dao import user_wakeup_channel

    assert user_wakeup_channel("user-1") == "chat:user:user-1:wakeup"
    assert user_wakeup_channel(" user-2 ") == "chat:user:user-2:wakeup"


def test_publish_user_wakeup_uses_user_channel_and_serializes_payload():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    payload = {
        "source": "System",
        "type": "session_wakeup",
        "reason": "trigger_enqueued",
        "session_id": "s1",
    }
    with patch.object(dao, "get_publish_client", return_value=fake_client):
        ok = dao.publish_user_wakeup("user-1", payload)

    assert ok is True
    fake_client.publish.assert_called_once()
    channel, message = fake_client.publish.call_args.args
    assert channel == "chat:user:user-1:wakeup"
    assert json.loads(message) == payload


def test_publish_user_wakeup_false_when_no_client():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    with patch.object(dao, "get_publish_client", return_value=None):
        ok = dao.publish_user_wakeup("user-1", {"session_id": "s1"})
    assert ok is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_redis_dao_user_wakeup.py -v`
Expected: FAIL — `ImportError: cannot import name 'user_wakeup_channel'`。

- [ ] **Step 3: 实现常量、channel helper 与发布方法**

在 `src/dao/redis_dao.py` 的 `STREAM_CHANNEL_PREFIX = "chat:stream:"`（第 27 行）之后新增常量：

```python
# 用户级 wakeup：API 进程的用户级 SSE 订阅此 channel，后台 trigger 入队后发 session 唤醒信号
USER_WAKEUP_CHANNEL_PREFIX = "chat:user:"
```

在 `_stop_key`（约第 59 行）等模块级 helper 附近新增（放在 `class RedisDao` 定义之前的 helper 区）：

```python
def user_wakeup_channel(user_id: str) -> str:
    return USER_WAKEUP_CHANNEL_PREFIX + (user_id or "").strip() + ":wakeup"
```

在 `RedisDao.publish_stream_event` 方法之后（约第 131 行后）新增方法：

```python
    def publish_user_wakeup(self, user_id: str, payload: dict) -> bool:
        """向该用户的 wakeup channel 发布一条 session 唤醒信号（用户级 SSE 订阅消费）。"""
        channel = user_wakeup_channel(user_id)
        try:
            message = json.dumps(payload, ensure_ascii=False)
            return self.publish(channel, message)
        except (TypeError, ValueError) as e:
            logger.warning(
                "Redis publish_user_wakeup json failed user_id=%s: %s", user_id, e
            )
            return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_redis_dao_user_wakeup.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/dao/redis_dao.py tests/test_redis_dao_user_wakeup.py
git commit -m "feat(redis): add user-level wakeup channel publish"
```

---

## Task 2: 按状态查 session 的 DAO 查询 + service 封装

snapshot 来源：该用户 waiting/active 的 session。当前无此查询，新增 DAO + service 封装。

**Files:**
- Create: `tests/test_chat_sessions_table_status.py`
- Modify: `src/dao/chat_sessions_table.py`（`count_active_sessions` 方法之后，约第 318 行后）
- Modify: `src/services/sessions_service.py`（`get_session_user_id` 方法之后，约第 382 行后）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_chat_sessions_table_status.py`：

```python
"""ChatSessionsTable.list_session_ids_by_status 查询行为测试。"""

from unittest.mock import MagicMock, patch


def _table_with_cursor():
    from src.dao.chat_sessions_table import ChatSessionsTable

    with patch.object(ChatSessionsTable, "init_table", lambda self: None):
        table = ChatSessionsTable()
    cursor = MagicMock()
    conn = MagicMock()
    cursor_ctx = MagicMock()
    cursor_ctx.__enter__.return_value = cursor
    cursor_ctx.__exit__.return_value = False
    conn.cursor.return_value = cursor_ctx
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False
    table.get_connection = MagicMock(return_value=conn_ctx)
    return table, cursor


def test_returns_session_ids():
    table, cursor = _table_with_cursor()
    cursor.fetchall.return_value = [
        {"session_id": "s1"},
        {"session_id": "s2"},
    ]
    assert table.list_session_ids_by_status("user-1", ["waiting", "active"]) == [
        "s1",
        "s2",
    ]


def test_empty_statuses_short_circuits_without_query():
    table, cursor = _table_with_cursor()
    assert table.list_session_ids_by_status("user-1", []) == []
    cursor.execute.assert_not_called()


def test_query_filters_user_and_status_in():
    table, cursor = _table_with_cursor()
    cursor.fetchall.return_value = []
    table.list_session_ids_by_status("user-9", ["waiting", "active"])
    sql, params = cursor.execute.call_args[0]
    assert "WHERE user_id = %s" in sql
    assert "status IN (%s, %s)" in sql
    assert params == ("user-9", "waiting", "active")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_chat_sessions_table_status.py -v`
Expected: FAIL — `AttributeError: 'ChatSessionsTable' object has no attribute 'list_session_ids_by_status'`。

- [ ] **Step 3: 实现 DAO 查询**

在 `src/dao/chat_sessions_table.py` 的 `count_active_sessions` 方法之后新增（`self.table_name`、`self.get_connection` 由 `BaseTable` 提供，DictCursor）：

```python
    def list_session_ids_by_status(
        self, user_id: str, statuses: list[str]
    ) -> list[str]:
        """返回该用户名下 status 命中给定集合的 session_id（wakeup snapshot 用），按更新时间倒序。"""
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                sql = f"""
                    SELECT session_id
                    FROM {self.table_name}
                    WHERE user_id = %s
                      AND status IN ({placeholders})
                    ORDER BY updated_at DESC
                """
                cursor.execute(sql, (user_id, *statuses))
                rows = cursor.fetchall()
                return [
                    str(r["session_id"]) for r in rows if r.get("session_id")
                ]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_chat_sessions_table_status.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 写 service 透传失败测试**

在 `tests/test_chat_sessions_table_status.py` 末尾追加（顶部已 import `MagicMock`）：

```python
def test_service_lists_waiting_or_active_ids():
    from src.services.sessions_service import ChatSessionsService

    table = MagicMock()
    table.list_session_ids_by_status.return_value = ["s1", "s2"]
    svc = ChatSessionsService(table)
    assert svc.list_waiting_or_active_session_ids("user-1") == ["s1", "s2"]
    table.list_session_ids_by_status.assert_called_once_with(
        "user-1", ["waiting", "active"]
    )
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/test_chat_sessions_table_status.py::test_service_lists_waiting_or_active_ids -v`
Expected: FAIL — `AttributeError: 'ChatSessionsService' object has no attribute 'list_waiting_or_active_session_ids'`。

- [ ] **Step 7: 实现 service 封装方法**

在 `src/services/sessions_service.py` 的 `get_session_user_id` 方法之后新增：

```python
    def list_waiting_or_active_session_ids(self, user_id: str) -> list[str]:
        """该用户名下仍在 waiting 或 active 的 session_id（用户级 wakeup stream snapshot 用）。"""
        return self.table.list_session_ids_by_status(user_id, ["waiting", "active"])
```

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/test_chat_sessions_table_status.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 9: Commit**

```bash
git add src/dao/chat_sessions_table.py src/services/sessions_service.py tests/test_chat_sessions_table_status.py
git commit -m "feat(sessions): query session ids by user and status for wakeup snapshot"
```

---

## Task 3: trigger_run 接入 live wakeup（抽 prepare + publish）

把 `trigger_run` 前半段抽成可复用的 `prepare_internal_trigger_run`（行为不变），新增 `_publish_user_wakeup` 辅助，并让 `trigger_run` 入队成功后发布 `trigger_enqueued`。后台 monitor 走的就是 `trigger_run`，这是后台触发 → 前端感知的发布端。

**Files:**
- Modify: `src/services/stream_service.py`（新增 `TriggerStreamContext` dataclass、`_publish_user_wakeup`、`prepare_internal_trigger_run`；重写 `trigger_run`）
- Modify: `tests/test_agent_run_trigger.py`（末尾追加 publish 用例）

关键事实复述：`trigger_run` 当前体（`stream_service.py:353-434`）顺序见背景§3；`owner` 在校验后已得；`_publish_user_wakeup` 不检查 `REDIS_URL`，直接调 DAO（无 redis 时 DAO 内部返回 False，触发 warning；能走到 publish 的前提是 `_enqueue_run` 成功即 redis 可用）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_run_trigger.py` 末尾追加（复用文件内 `_make_trigger_service`、`_trigger_patches`；`MagicMock`/`patch`/`pytest` 已在顶部 import）：

```python
def test_trigger_run_publishes_wakeup_on_enqueue():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run("s1", "作业完成", origin="bohrium_completion")
    assert res.status == "enqueued"
    fake_redis.publish_user_wakeup.assert_called_once()
    uid, payload = fake_redis.publish_user_wakeup.call_args.args
    assert uid == "owner-1"
    assert payload == {
        "source": "System",
        "type": "session_wakeup",
        "reason": "trigger_enqueued",
        "session_id": "s1",
    }


def test_trigger_run_deduped_does_not_publish():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = True  # 命中去重
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1", "x", origin="loop", dedup_key="job:1:done"
        )
    assert res.status == "deduped"
    fake_redis.publish_user_wakeup.assert_not_called()


def test_trigger_run_busy_does_not_publish():
    service, sessions_service, events_service = _make_trigger_service()
    sessions_service.try_acquire_session_run.return_value = (False, "already_in_run")
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run("s1", "x", origin="loop")
    assert res.status == "busy"
    fake_redis.publish_user_wakeup.assert_not_called()


def test_trigger_run_enqueue_failed_does_not_publish():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = False  # 入队失败
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run("s1", "x", origin="loop")
    assert res.status == "error"
    assert res.reason == "enqueue_failed"
    fake_redis.publish_user_wakeup.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_agent_run_trigger.py -k "publishes_wakeup or does_not_publish" -v`
Expected: FAIL — `test_trigger_run_publishes_wakeup_on_enqueue` 失败（`publish_user_wakeup` 未被调用）。其余三个负向用例此时可能已"碰巧通过"（方法尚不存在调用），Step 4 后才有完整意义。

- [ ] **Step 3: 新增 dataclass + 辅助方法**

在 `src/services/stream_service.py` 的 `TriggerResult` dataclass 之后（约第 131 行后）新增：

```python
@dataclass
class TriggerStreamContext:
    """prepare_internal_trigger_run 的成功产物：已写 System/trigger、已组好 job，待入队。"""

    task_id: str
    invocation_id: str
    owner: str
    job: dict
    event: dict  # 已落库的 System/trigger 发起事件
    dedup_key: str | None = None
```

在 `ChatStreamService` 内、`trigger_run` 之前（约第 352 行前）新增辅助方法：

```python
    def _publish_user_wakeup(
        self, user_id: str, session_id: str, reason: str
    ) -> None:
        """向用户级 wakeup channel 发布一条 session 唤醒信号。

        感知层加速，不是 run 提交条件：publish 失败只记 warning，不回滚 trigger。
        """
        payload = {
            "source": "System",
            "type": "session_wakeup",
            "reason": reason,
            "session_id": session_id.strip(),
        }
        if not get_redis_dao().publish_user_wakeup(user_id, payload):
            logger.warning(
                "publish_user_wakeup failed user_id=%s session_id=%s reason=%s",
                user_id,
                session_id,
                reason,
            )
```

- [ ] **Step 4: 抽 prepare 并重写 trigger_run**

把 `src/services/stream_service.py` 现有 `trigger_run`（第 353-434 行整段）替换为下面两个方法（`prepare_internal_trigger_run` 是原前半段，`trigger_run` 复用它再入队 + publish；行为对原 deduped/busy/error/enqueued 完全一致）：

```python
    def prepare_internal_trigger_run(
        self,
        session_id: str,
        prompt: str,
        *,
        origin: str,
        dedup_key: str | None = None,
        delivery: DeliverySpec | None = None,
        mode: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> TriggerResult | TriggerStreamContext:
        """trigger 前半段：校验 owner / dedup 预检 / 写 System/trigger / 组装 job，不入队。

        返回 TriggerStreamContext 表示已准备好待入队（同步路径由 trigger_run 入队，
        内部 HTTP 路径由 generate_internal_trigger_stream 在订阅就绪后入队）；
        返回 TriggerResult(deduped/busy/error) 表示无需开流。
        """
        sid = session_id.strip()
        owner = self._sessions_service.get_session_user_id(sid)
        if not owner:
            logger.warning(
                "trigger prepare rejected: session not found or no owner session_id=%s",
                sid,
            )
            return TriggerResult(
                status="error", reason="session_not_found_or_no_owner"
            )

        if dedup_key and get_redis_dao().dedup_key_exists(dedup_key):
            logger.info(
                "trigger prepare deduped session_id=%s dedup_key=%s", sid, dedup_key
            )
            return TriggerResult(status="deduped", dedup_key=dedup_key)

        resolved_mode = self._resolve_mode(mode)
        model_val = (model or '').strip() or None
        if model_val is None:
            model_val = self._events_service.get_last_resolved_model_profile(sid)
        delivery_payload = delivery.model_dump() if delivery is not None else None

        def _system_event_writer(task_id: str, invocation_id: str) -> dict:
            event = {
                'source': 'System',
                'type': 'trigger',
                'content': {'text': prompt, 'origin': origin},
                'session_id': sid,
                'task_id': task_id,
                'invocation_id': invocation_id,
            }
            self._events_service.add_history_event(sid, event, user_id=owner)
            return event

        handle = self._prepare_run(
            sid,
            user_id=owner,
            user_text=prompt,
            files=None,
            images=None,
            workspace_paths=None,
            event_writer=_system_event_writer,
            id_prefix='trig_',
            mode=resolved_mode,
            model=model_val,
            byok_credential_id=None,
            workspace=workspace,
            origin=origin,
            delivery=delivery_payload,
        )
        if isinstance(handle, Busy):
            logger.info("trigger prepare busy session_id=%s reason=%s", sid, handle.reason)
            return TriggerResult(status="busy", reason=handle.reason)

        return TriggerStreamContext(
            task_id=handle.task_id,
            invocation_id=handle.invocation_id,
            owner=owner,
            job=handle.job,
            event=handle.event,
            dedup_key=dedup_key,
        )

    def trigger_run(
        self,
        session_id: str,
        prompt: str,
        *,
        origin: str,
        dedup_key: str | None = None,
        delivery: DeliverySpec | None = None,
        mode: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> TriggerResult:
        """程序化触发一次 agent run（后台 monitor / loop / schedule 同步入队路径）。"""
        sid = session_id.strip()
        prep = self.prepare_internal_trigger_run(
            sid,
            prompt,
            origin=origin,
            dedup_key=dedup_key,
            delivery=delivery,
            mode=mode,
            model=model,
            workspace=workspace,
        )
        if isinstance(prep, TriggerResult):
            return prep

        if not self._enqueue_run(sid, prep.job):
            return TriggerResult(status="error", reason="enqueue_failed")

        if prep.dedup_key:
            get_redis_dao().mark_dedup_key_nx(prep.dedup_key, prep.task_id)
        self._publish_user_wakeup(prep.owner, sid, "trigger_enqueued")
        logger.info(
            "trigger_run enqueued session_id=%s task_id=%s origin=%s",
            sid,
            prep.task_id,
            origin,
        )
        return TriggerResult(
            status="enqueued",
            task_id=prep.task_id,
            invocation_id=prep.invocation_id,
        )
```

- [ ] **Step 5: 运行测试确认通过（含原有 trigger 用例不回归）**

Run: `uv run pytest tests/test_agent_run_trigger.py -v`
Expected: PASS（全文件通过：新增 4 个 publish 用例 + 原有 `_prepare_run`/`_enqueue_run`/`trigger_run`/dedup 用例）。

- [ ] **Step 6: Commit**

```bash
git add src/services/stream_service.py tests/test_agent_run_trigger.py
git commit -m "feat(trigger): publish user wakeup on enqueue, extract prepare_internal_trigger_run"
```

---

## Task 4: 用户级 wakeup stream（generate + endpoint + 路由）

新增 `generate_wakeup_stream`（snapshot + 订阅用户 channel 转发 live），以及独立 router 的 `GET /api/v1/chat/wakeup/stream`，登录强制、不挂 share。先把订阅循环抽成通用的按 channel 订阅。

**Files:**
- Modify: `src/services/stream_service.py`（重构 `_start_redis_stream_subscription` 为薄封装、新增 `_start_redis_channel_subscription`；新增 `generate_wakeup_stream`；顶部 import 加 `user_wakeup_channel`）
- Create: `src/apis/wakeup_api.py`
- Modify: `src/apis/api_router.py`
- Create: `tests/test_wakeup_stream.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_wakeup_stream.py`：

```python
"""用户级 wakeup stream：subscribe-before-snapshot + live 转发 + 端点鉴权。"""

import asyncio
import json
import threading
from unittest.mock import MagicMock, patch


def _make_service(sessions=None):
    from src.services.stream_service import ChatStreamService

    return ChatStreamService(
        sessions_service=sessions or MagicMock(),
        events_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )


def _frames_from_chunk(chunk: str) -> list[dict]:
    # 只解析 ag-ui data 帧；SSE comment 心跳（": keepalive"）不以 event: 开头，跳过
    return [
        json.loads(part.split("data: ", 1)[1])
        for part in chunk.split("\n\n")
        if part.strip() and part.lstrip().startswith("event:")
    ]


async def test_snapshot_emits_one_wakeup_per_waiting_active_session():
    sessions = MagicMock()
    sessions.list_waiting_or_active_session_ids.return_value = ["s1", "s2"]
    service = _make_service(sessions)

    frames: list[dict] = []
    with patch("src.services.stream_service.REDIS_URL", None):
        async for chunk in service.generate_wakeup_stream("user-1"):
            frames.extend(_frames_from_chunk(chunk))

    sessions.list_waiting_or_active_session_ids.assert_called_once_with("user-1")
    assert [f["session_id"] for f in frames] == ["s1", "s2"]
    for f in frames:
        assert f["type"] == "session_wakeup"
        assert f["reason"] == "session_waiting_snapshot"
        # 严格四字段，不泄漏 task_id/invocation_id/status/origin 等
        assert set(f.keys()) == {"source", "type", "reason", "session_id"}


async def test_subscribes_before_snapshot_query():
    # 订阅必须在 snapshot 查询之前 ready，否则二者之间入队并 publish 的 trigger 会落空窗
    sessions = MagicMock()
    order: list[str] = []
    sessions.list_waiting_or_active_session_ids.side_effect = lambda uid: (
        order.append("snapshot") or ["s1"]
    )
    service = _make_service(sessions)

    def _fake_sub(channel, loop, *, thread_name):
        order.append("subscribe")
        ready = threading.Event()
        ready.set()
        return asyncio.Queue(), threading.Event(), ready, MagicMock()

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch(
            "src.services.stream_service._start_redis_channel_subscription",
            side_effect=_fake_sub,
        ),
    ):
        gen = service.generate_wakeup_stream("user-1")
        first = await gen.__anext__()
        await gen.aclose()

    assert order == ["subscribe", "snapshot"]
    payload = _frames_from_chunk(first)[0]
    assert payload["session_id"] == "s1"
    assert payload["reason"] == "session_waiting_snapshot"


async def test_forwards_live_wakeup_then_closes_on_client_disconnect():
    sessions = MagicMock()
    sessions.list_waiting_or_active_session_ids.return_value = []
    service = _make_service(sessions)
    live = {
        "source": "System",
        "type": "session_wakeup",
        "reason": "trigger_enqueued",
        "session_id": "s9",
    }

    def _fake_sub(channel, loop, *, thread_name):
        assert channel == "chat:user:user-1:wakeup"
        ready = threading.Event()
        ready.set()
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(live)
        return q, threading.Event(), ready, MagicMock()

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch(
            "src.services.stream_service._start_redis_channel_subscription",
            side_effect=_fake_sub,
        ),
    ):
        gen = service.generate_wakeup_stream("user-1")
        chunk = await gen.__anext__()
        await gen.aclose()

    payload = _frames_from_chunk(chunk)[0]
    assert payload == live


def test_wakeup_endpoint_requires_login():
    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    resp = client.get("/api/v1/chat/wakeup/stream")  # 不带 X-User-Id
    assert resp.status_code == 401, resp.text


def test_wakeup_endpoint_success_invokes_generator():
    from fastapi.testclient import TestClient

    from app import app
    from src.services.stream_service import get_stream_service

    fake_stream = MagicMock()

    async def _empty(_uid):
        if False:
            yield ""

    fake_stream.generate_wakeup_stream.side_effect = lambda uid: _empty(uid)
    app.dependency_overrides[get_stream_service] = lambda: fake_stream
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/chat/wakeup/stream", headers={"X-User-Id": "user-1"}
        )
        assert resp.status_code == 200, resp.text
        fake_stream.generate_wakeup_stream.assert_called_once_with("user-1")
    finally:
        app.dependency_overrides.pop(get_stream_service, None)


def test_wakeup_not_exposed_on_share_route():
    from fastapi.testclient import TestClient

    from app import app

    client = TestClient(app)
    resp = client.get(
        "/pubapi/v1/chat/wakeup/stream", headers={"X-User-Id": "user-1"}
    )
    assert resp.status_code == 404, resp.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_wakeup_stream.py -v`
Expected: FAIL — snapshot/race/live 用例 `AttributeError: ... has no attribute 'generate_wakeup_stream'`；端点 success/share 用例 404（路由还没挂），`..._requires_login` 此时可能也是 404 而非 401。

- [ ] **Step 3: 顶部 import 加 channel helper**

`src/services/stream_service.py` 顶部现有（第 16-19 行）：

```python
from src.dao.redis_dao import (
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
)
```

改为：

```python
from src.dao.redis_dao import (
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
    user_wakeup_channel,
)
```

- [ ] **Step 4: 抽通用 channel 订阅，把 stream 订阅改薄封装**

把 `src/services/stream_service.py` 现有 `_start_redis_stream_subscription`（第 54-102 行整段）替换为下面两个函数（订阅循环移入 `_start_redis_channel_subscription`，stream 版变成一行；现有 `generate_subscribe_stream`/`generate_send_stream` 的调用签名不变，不受影响）：

```python
def _start_redis_channel_subscription(
    channel: str,
    loop: asyncio.AbstractEventLoop,
    *,
    thread_name: str,
) -> tuple[asyncio.Queue, threading.Event, threading.Event, threading.Thread]:
    redis_queue: asyncio.Queue = asyncio.Queue()
    shutdown_event = threading.Event()
    subscribe_ready = threading.Event()

    def _redis_subscribe_loop() -> None:
        client = get_redis_dao().create_client()
        if not client:
            subscribe_ready.set()
            return
        pubsub = client.pubsub()
        try:
            pubsub.subscribe(channel)
            while not shutdown_event.is_set():
                msg = pubsub.get_message(timeout=1.0)
                if not msg:
                    continue
                msg_type = msg.get('type')
                if msg_type == 'subscribe':
                    subscribe_ready.set()
                    continue
                if msg_type != 'message':
                    continue
                try:
                    data = json.loads(msg['data'])
                    loop.call_soon_threadsafe(redis_queue.put_nowait, data)
                except (json.JSONDecodeError, TypeError):
                    pass
        finally:
            subscribe_ready.set()
            try:
                pubsub.unsubscribe(channel)
                pubsub.close()
            except Exception:
                pass

    sub_thread = threading.Thread(
        target=_redis_subscribe_loop,
        name=thread_name,
        daemon=True,
    )
    sub_thread.start()
    return redis_queue, shutdown_event, subscribe_ready, sub_thread


def _start_redis_stream_subscription(
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    *,
    thread_name: str,
) -> tuple[asyncio.Queue, threading.Event, threading.Event, threading.Thread]:
    return _start_redis_channel_subscription(
        STREAM_CHANNEL_PREFIX + session_id, loop, thread_name=thread_name
    )
```

- [ ] **Step 5: 实现 generate_wakeup_stream**

在 `src/services/stream_service.py` 的 `generate_subscribe_stream` 方法之后（约第 646 行后，`prepare_send_message` 之前）新增：

```python
    async def generate_wakeup_stream(
        self, user_id: str
    ) -> AsyncGenerator[str, None]:
        """用户级 wakeup 流：先订阅 chat:user:{user_id}:wakeup（软等 subscribe_ready），
        再查并发送 snapshot（当前 waiting/active session 各一条），最后 drain live wakeup。

        订阅在 snapshot 查询之前 ready，保证 snapshot 与订阅之间入队并 publish 的 trigger
        不落空窗（subscribe-before-snapshot；重复 session_id 由前端 reducer 幂等处理）。
        只承载 session 唤醒信号，不承载 run 详情；心跳走 SSE comment，不进 ag-ui data。
        无 Redis 时只发一次 snapshot 后结束。"""
        uid = (user_id or "").strip()

        def _snapshot_frames() -> list[str]:
            return [
                self.sse_format(
                    {
                        "source": "System",
                        "type": "session_wakeup",
                        "reason": "session_waiting_snapshot",
                        "session_id": sid,
                    }
                )
                for sid in self._sessions_service.list_waiting_or_active_session_ids(
                    uid
                )
            ]

        if not REDIS_URL:
            for frame in _snapshot_frames():
                yield frame
            return

        loop = asyncio.get_running_loop()
        channel = user_wakeup_channel(uid)
        (
            redis_queue,
            shutdown_event,
            subscribe_ready,
            sub_thread,
        ) = _start_redis_channel_subscription(
            channel,
            loop,
            thread_name=f"wakeup-{uid[:8]}",
        )
        try:
            # 先确保订阅 ready，再查 snapshot：订阅就绪之后入队的 trigger 一定进 queue
            if not await asyncio.to_thread(subscribe_ready.wait, 3.0):
                logger.warning(
                    "generate_wakeup_stream: redis subscribe not ready before "
                    "snapshot user_id=%s",
                    uid,
                )
            for frame in _snapshot_frames():
                yield frame
            while True:
                try:
                    payload = await asyncio.wait_for(redis_queue.get(), timeout=30.0)
                except TimeoutError:
                    # SSE comment 心跳：浏览器 EventSource 透明忽略，不进 ag-ui data
                    yield ": keepalive\n\n"
                    continue
                yield self.sse_format(payload)
        finally:
            shutdown_event.set()
            sub_thread.join(timeout=2.0)
```

- [ ] **Step 6: 新建 wakeup 端点**

创建 `src/apis/wakeup_api.py`：

```python
"""用户级 wakeup SSE：任意 MatMaster 页面感知 session 后台唤醒（登录强制，不支持 share）。"""

from fastapi import APIRouter, Depends, Request

from src.apis.chat_api import _sse_streaming_response
from src.services.stream_service import ChatStreamService, get_stream_service
from src.services.user_service import UserService

router = APIRouter(tags=["Chat Wakeup"])


@router.get(
    "/wakeup/stream",
    summary="用户级会话唤醒流（登录用户）",
    description="登录用户的 session 唤醒感知流：建连先发 snapshot（当前 waiting/active "
    "session 各一条），随后转发 live wakeup。仅承载 session_wakeup 信号，不承载 run 详情。",
    operation_id="streamUserWakeup",
)
async def wakeup_stream(
    request: Request,
    user_id: str = Depends(UserService.require_user_id),
    stream_svc: ChatStreamService = Depends(get_stream_service),
):
    return _sse_streaming_response(
        request, stream_svc.generate_wakeup_stream(user_id)
    )
```

- [ ] **Step 7: 挂载路由**

`src/apis/api_router.py` 全文当前为：

```python
from fastapi import APIRouter

from src.apis import admin_chat_api, chat_api, debug_api, feishu_api

api_router = APIRouter()
api_router.include_router(chat_api.router, prefix='/chat/sessions')
api_router.include_router(admin_chat_api.router, prefix='/admin/chat/sessions')
api_router.include_router(debug_api.router, prefix='/debug')
api_router.include_router(feishu_api.router, prefix='/integrations/feishu')
```

改为（import 段加 `wakeup_api`，并在 `chat_api` include 之后加一行；注意 `/chat` 段要在 `/chat/sessions` 之后挂不影响匹配，FastAPI 按声明顺序逐路由匹配且路径不同段）：

```python
from fastapi import APIRouter

from src.apis import admin_chat_api, chat_api, debug_api, feishu_api, wakeup_api

api_router = APIRouter()
api_router.include_router(chat_api.router, prefix='/chat/sessions')
api_router.include_router(wakeup_api.router, prefix='/chat')
api_router.include_router(admin_chat_api.router, prefix='/admin/chat/sessions')
api_router.include_router(debug_api.router, prefix='/debug')
api_router.include_router(feishu_api.router, prefix='/integrations/feishu')
```

最终路径 = `/api/v1` + `/chat` + `/wakeup/stream` = `/api/v1/chat/wakeup/stream`。不加进 `share_router`，故 `/pubapi/v1` 下不可达。

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/test_wakeup_stream.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 9: 回归确认既有发送/订阅流不受订阅函数重构影响**

Run: `uv run pytest tests/test_chat_stream_direct.py tests/test_chat_stream_subscribe_replay.py -v`
Expected: PASS（既有用例全绿；`_start_redis_stream_subscription` 签名未变）。

- [ ] **Step 10: Commit**

```bash
git add src/services/stream_service.py src/apis/wakeup_api.py src/apis/api_router.py tests/test_wakeup_stream.py
git commit -m "feat(wakeup): add user-level wakeup SSE stream endpoint"
```

---

## Task 5: 内部 HTTP trigger 时序修正（subscribe-before-enqueue）

新增 `generate_internal_trigger_stream`（对齐发送路径：推 status+history+System/trigger → 订阅就绪 → 入队 → mark dedup → publish wakeup → 转发），把 `_handle_internal_trigger` 改为 prepare + generate 两段，消除 enqueue-then-subscribe 竞态。

**已知限制（本计划范围外，follow-up）：** internal HTTP trigger 保持原 `trigger_run` 不传 workspace 的既有行为——`ChatSendRequest.directory` 在 internal trigger 模式下当前不进 job payload，Worker 拿到的 `job['workspace']` 为 `None`（既有行为，非本计划回归）。后台 monitor 走 `trigger_run` 时由 `bohrium_completion_scheduler` 传入 workspace，不受影响。让 internal trigger 支持 workspace（需与普通发送路径 `SessionDirectoryResolver` 的目录校验/继承语义对齐，并与 workspace-job-context-section 设计协调）单独立项，不在本计划改。

**Files:**
- Modify: `src/services/stream_service.py`（新增 `generate_internal_trigger_stream`）
- Modify: `src/apis/chat_api.py`（顶部 import 加 `TriggerStreamContext`；重写 `_handle_internal_trigger`）
- Create: `tests/test_internal_trigger_timing.py`
- Modify: `tests/test_chat_internal_trigger_api.py`（适配 prepare/generate 分派）

- [ ] **Step 1: 写时序失败测试**

创建 `tests/test_internal_trigger_timing.py`：

```python
"""内部 HTTP trigger 的 subscribe-before-enqueue 时序：订阅就绪后才入队。"""

import asyncio
import threading
from unittest.mock import MagicMock, patch


def _make_service():
    from src.services.stream_service import ChatStreamService

    sessions = MagicMock()
    sessions.get_session_status_payload.return_value = {
        "source": "System",
        "type": "session_status",
        "status": "idle",
        "session_id": "s1",
    }
    return ChatStreamService(
        sessions_service=sessions,
        events_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )


async def test_generate_internal_trigger_stream_subscribes_before_enqueue():
    from src.services.stream_service import TriggerStreamContext

    service = _make_service()
    ctx = TriggerStreamContext(
        task_id="trig_1",
        invocation_id="inv_1",
        owner="owner-1",
        job={"session_id": "s1"},
        event={"source": "System", "type": "trigger", "session_id": "s1"},
        dedup_key=None,
    )
    order: list[str] = []

    def _fake_sub(session_id, loop, *, thread_name):
        order.append("subscribe")
        ready = threading.Event()
        ready.set()
        q: asyncio.Queue = asyncio.Queue()
        # 立刻投递一个 stream_closed 让转发循环尽快结束
        q.put_nowait({"type": "stream_closed", "session_id": "s1"})
        return q, threading.Event(), ready, MagicMock()

    def _fake_enqueue(sid, job):
        order.append("enqueue")
        return True

    with (
        patch(
            "src.services.stream_service._start_redis_stream_subscription",
            side_effect=_fake_sub,
        ),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
        patch.object(service, "_iter_history_replay_batches", return_value=iter([])),
        patch.object(service, "_enqueue_run", side_effect=_fake_enqueue),
        patch.object(service, "_publish_user_wakeup") as pub,
    ):
        async for _ in service.generate_internal_trigger_stream("s1", ctx):
            pass

    assert order == ["subscribe", "enqueue"]
    pub.assert_called_once_with("owner-1", "s1", "trigger_enqueued")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_internal_trigger_timing.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'generate_internal_trigger_stream'`。

- [ ] **Step 3: 实现 generate_internal_trigger_stream**

在 `src/services/stream_service.py` 的 `generate_send_stream` 方法之后（约第 881 行后、`get_stream_service` 之前）新增（结构对齐 `generate_send_stream`，差异：发起事件是 `ctx.event` 即 System/trigger；入队成功后多 mark dedup + publish wakeup）：

```python
    async def generate_internal_trigger_stream(
        self,
        session_id: str,
        ctx: TriggerStreamContext,
    ) -> AsyncGenerator[str, None]:
        """内部 HTTP trigger 流：推 status + history + System/trigger，订阅就绪后入队，
        再转发 Worker 实时事件。对齐发送路径的 subscribe-before-enqueue 不变量。"""
        sid = session_id.strip()
        loop = asyncio.get_running_loop()
        start_time_ms = int(time.time() * 1000)
        logger.info(
            'generate_internal_trigger_stream: start session_id=%s task_id=%s',
            sid,
            ctx.task_id,
        )

        payload = self._sessions_service.get_session_status_payload(sid)
        payload['stream_started_at'] = start_time_ms
        payload['invocation_id'] = ctx.invocation_id
        yield self.sse_format(payload)
        for batch in self._iter_history_replay_batches(
            sid, exclude_task_id=ctx.task_id
        ):
            yield batch
        yield self.sse_format(ctx.event)

        (
            redis_queue,
            shutdown_event,
            subscribe_ready,
            sub_thread,
        ) = _start_redis_stream_subscription(
            sid,
            loop,
            thread_name=f"trigger-stream-{sid[:8]}",
        )

        try:
            if not await asyncio.to_thread(subscribe_ready.wait, 3.0):
                logger.warning(
                    'generate_internal_trigger_stream: redis subscribe not ready '
                    'before enqueue session_id=%s task_id=%s',
                    sid,
                    ctx.task_id,
                )
            if not self._enqueue_run(sid, ctx.job):
                yield self.sse_format(
                    {
                        'source': 'System',
                        'type': 'error',
                        'content': 'Queue unavailable.',
                        'session_id': sid,
                        'invocation_id': ctx.invocation_id,
                    }
                )
                yield self.sse_format(
                    {
                        'source': 'System',
                        'type': 'stream_closed',
                        'content': '',
                        'session_id': sid,
                        'invocation_id': ctx.invocation_id,
                    }
                )
                return
            if ctx.dedup_key:
                get_redis_dao().mark_dedup_key_nx(ctx.dedup_key, ctx.task_id)
            self._publish_user_wakeup(ctx.owner, sid, "trigger_enqueued")
            while True:
                try:
                    payload = await asyncio.wait_for(redis_queue.get(), timeout=30.0)
                except TimeoutError:
                    yield self.sse_format(self._ping_payload(sid))
                    continue
                elapsed_ms = int(time.time() * 1000) - start_time_ms
                out = {
                    **payload,
                    'elapsed_ms': elapsed_ms,
                    'stream_started_at': start_time_ms,
                    'invocation_id': payload.get('invocation_id') or ctx.invocation_id,
                }
                yield self.sse_format(out)
                if payload.get('type') == 'stream_closed':
                    break
        finally:
            shutdown_event.set()
            sub_thread.join(timeout=2.0)
```

- [ ] **Step 4: 运行时序测试确认通过**

Run: `uv run pytest tests/test_internal_trigger_timing.py -v`
Expected: PASS（1 passed，`order == ["subscribe", "enqueue"]`）。

- [ ] **Step 5: 改 chat_api 顶部 import**

`src/apis/chat_api.py` 现有（第 45-48 行）：

```python
from src.services.stream_service import (
    ChatStreamService,
    get_stream_service,
)
```

改为：

```python
from src.services.stream_service import (
    ChatStreamService,
    TriggerStreamContext,
    get_stream_service,
)
```

- [ ] **Step 6: 重写 _handle_internal_trigger 为 prepare + generate**

把 `src/apis/chat_api.py` 的 `_handle_internal_trigger`（第 129-181 行整段）替换为（owner/quota/redis 校验不变，只把 `trigger_run + generate_subscribe_stream` 换成 `prepare_internal_trigger_run + generate_internal_trigger_stream`）：

```python
async def _handle_internal_trigger(
    request: Request,
    sid: str,
    req: ChatSendRequest | None,
    chat_svc: ChatSessionsService,
    stream_svc: ChatStreamService,
):
    """X-Internal-Token 通过后的内部发起：以 session owner 为计费/鉴权主体。

    subscribe-before-enqueue：prepare 占用/写 System/trigger/组装 job（不入队），
    deduped/busy/error 直接返回 JSON；prepared 则进 generator，订阅就绪后再入队。"""
    prompt = (req.content or "").strip() if req else ""
    if not prompt:
        raise BaseErrorResponse(
            http_status=400, code=400, msg="内部触发需要非空 content"
        )
    owner = chat_svc.get_session_user_id(sid)
    if not owner:
        raise NotFoundErrorResponse(msg="会话不存在或无所有者，无法内部触发")
    quota_status = await check_quota_status(owner)
    if quota_status.is_exhausted:
        raise ForbiddenErrorResponse(
            msg=quota_status.exhausted_message("额度已用完，无法触发")
        )
    if not REDIS_URL:
        raise BaseErrorResponse(
            http_status=503, code=503, msg="队列服务不可用，请检查 REDIS_URL 配置"
        )
    # prepare 是同步函数（多次 DB/Redis 往返），放线程池避免卡事件循环
    prep = await asyncio.to_thread(
        stream_svc.prepare_internal_trigger_run,
        sid,
        prompt,
        origin=(req.origin or "external_tool"),
        dedup_key=req.dedup_key,
        delivery=req.delivery,
        mode=req.mode,
        model=req.model,
    )
    if isinstance(prep, TriggerStreamContext):
        return _sse_streaming_response(
            request, stream_svc.generate_internal_trigger_stream(sid, prep)
        )
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "msg": prep.status,
            "data": {
                "status": prep.status,
                "task_id": prep.task_id,
                "invocation_id": prep.invocation_id,
                "reason": prep.reason,
            },
        },
    )
```

- [ ] **Step 7: 改造内部 trigger API 测试**

`tests/test_chat_internal_trigger_api.py` 里把对 `trigger_run` + `generate_subscribe_stream` 的 mock 改为 `prepare_internal_trigger_run` + `generate_internal_trigger_stream`。

改 `test_internal_trigger_enqueues_with_valid_token`（第 30-80 行）的 mock 设置与断言：

把第 31-44 行：

```python
    from src.services.stream_service import TriggerResult

    fake_stream = MagicMock()
    fake_stream.trigger_run.return_value = TriggerResult(
        status="enqueued", task_id="trig_abc", invocation_id="inv_abc"
    )

    async def _empty_subscribe(_sid):
        if False:
            yield ""

    fake_stream.generate_subscribe_stream.side_effect = lambda sid: _empty_subscribe(
        sid
    )
```

改为：

```python
    from src.services.stream_service import TriggerStreamContext

    fake_stream = MagicMock()
    fake_stream.prepare_internal_trigger_run.return_value = TriggerStreamContext(
        task_id="trig_abc",
        invocation_id="inv_abc",
        owner="owner-1",
        job={"session_id": "sess"},
        event={"source": "System", "type": "trigger"},
        dedup_key="job:123:done",
    )

    async def _empty_stream(_sid, _ctx):
        if False:
            yield ""

    fake_stream.generate_internal_trigger_stream.side_effect = (
        lambda sid, ctx: _empty_stream(sid, ctx)
    )
```

把第 72-75 行断言：

```python
        fake_stream.trigger_run.assert_called_once()
        kwargs = fake_stream.trigger_run.call_args.kwargs
        assert kwargs["origin"] == "hpc_job"
        assert kwargs["dedup_key"] == "job:123:done"
```

改为：

```python
        fake_stream.prepare_internal_trigger_run.assert_called_once()
        kwargs = fake_stream.prepare_internal_trigger_run.call_args.kwargs
        assert kwargs["origin"] == "hpc_job"
        assert kwargs["dedup_key"] == "job:123:done"
```

改 `test_internal_trigger_deduped_returns_json_not_stream`（第 83-117 行）：把第 86-89 行：

```python
    fake_stream = MagicMock()
    fake_stream.trigger_run.return_value = TriggerResult(
        status="deduped", dedup_key="job:123:done"
    )
```

改为（保留顶部 `from src.services.stream_service import TriggerResult`）：

```python
    fake_stream = MagicMock()
    fake_stream.prepare_internal_trigger_run.return_value = TriggerResult(
        status="deduped", dedup_key="job:123:done"
    )
```

把第 113 行断言：

```python
        fake_stream.generate_subscribe_stream.assert_not_called()
```

改为：

```python
        fake_stream.generate_internal_trigger_stream.assert_not_called()
```

`test_internal_trigger_wrong_token_rejected_fail_closed`（第 142 行）与 `test_internal_trigger_rejected_on_share_route`（第 172 行）里的：

```python
        fake_stream.trigger_run.assert_not_called()
```

改为：

```python
        fake_stream.prepare_internal_trigger_run.assert_not_called()
```

- [ ] **Step 8: 运行内部 trigger API 测试确认通过**

Run: `uv run pytest tests/test_chat_internal_trigger_api.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 9: Commit**

```bash
git add src/services/stream_service.py src/apis/chat_api.py tests/test_internal_trigger_timing.py tests/test_chat_internal_trigger_api.py
git commit -m "fix(trigger): make internal HTTP trigger subscribe before enqueue"
```

---

## 最终验证

跑完五个 Task 后整体验证一次：

Run:
```bash
uv run pytest tests/test_redis_dao_user_wakeup.py tests/test_chat_sessions_table_status.py tests/test_agent_run_trigger.py tests/test_wakeup_stream.py tests/test_internal_trigger_timing.py tests/test_chat_internal_trigger_api.py tests/test_chat_stream_direct.py tests/test_chat_stream_subscribe_replay.py -v
```
Expected: 全部 PASS（新增功能用例 + 既有 stream/trigger 回归用例）。

冒烟 import（确认路由与服务可加载）：
```bash
uv run python -c "from app import app; print([r.path for r in app.routes if 'wakeup' in r.path])"
```
Expected: 输出包含 `/api/v1/chat/wakeup/stream`。

---

## Self-Review（plan 作者已核对，执行者可对照）

**1. Spec coverage（后端部分）：**

| Spec 要求 | 落点 |
| --- | --- |
| §5 wakeup 协议（严格四字段、reason 枚举、禁止 task_id/invocation_id/status/origin） | 协议契约节 + T3 `_publish_user_wakeup` payload + T4 snapshot payload；测试 `test_trigger_run_publishes_wakeup_on_enqueue`、`test_snapshot_...`（断言 `set(keys)=={source,type,reason,session_id}`） |
| §6.1 live 发布点（enqueue 成功后 publish；判定收敛为 status==enqueued；publish 不下沉 _enqueue_run） | T3 `trigger_run` 重写（复用 prepare + 入队后 publish）；负向用例 deduped/busy/enqueue_failed 不 publish |
| §6.2 publish 失败只 warning 不回滚 | T3 `_publish_user_wakeup`（DAO 返 False 仅 warning） |
| §6.3 Redis channel `chat:user:{user_id}:wakeup` | T1 `user_wakeup_channel` + `test_user_wakeup_channel_format` |
| §6.4 wakeup endpoint（登录强制、不支持 share、subscribe→snapshot→live、断开释放订阅） | T4 `wakeup_api.py`（`require_user_id`）+ `api_router` 不挂 share + `generate_wakeup_stream`（订阅就绪→snapshot→live，`finally` 释放）；`test_wakeup_endpoint_requires_login` / `..._success_invokes_generator` / `..._not_exposed_on_share_route` |
| §6.5 snapshot 来源 waiting/active；failed/idle 不纳入；需新增查询；subscribe-before-snapshot 无空窗 | T2 `list_session_ids_by_status(["waiting","active"])` + T4 `generate_wakeup_stream`（先订阅后 snapshot）；`test_snapshot_...` / `test_subscribes_before_snapshot_query` |
| §6.6 内部 trigger subscribe-before-enqueue（prepare 不入队 / generate 订阅就绪后入队 + publish） | T3 `prepare_internal_trigger_run` + T5 `generate_internal_trigger_stream` + `_handle_internal_trigger` 改造；`test_..._subscribes_before_enqueue` |
| §8 安全（登录强制、不允许 share route） | T4 `require_user_id` + 仅挂 `api_router`（不挂 `share_router`） |
| §9.1 trigger publish 测试（成功 publish 一次 / 各失败不 publish） | T3 四个用例 |
| §9.2 wakeup live 测试（转发当前用户 wakeup，ag-ui frame） | T4 `test_forwards_live_wakeup_...` |
| §9.3 wakeup snapshot 测试（每 session 一条；不含 task_id 等） | T4 `test_snapshot_...` |
| §9.4 内部 trigger 时序测试（subscribe ready 之后才 lpush） | T5 `test_..._subscribes_before_enqueue`（断言 order==[subscribe, enqueue]） |
| §10 验收①②③④⑤⑥ | ①②后端发布+端点（T3/T4，前端打开流属前端计划）；④⑤payload 四字段（T3/T4 断言）；⑥时序（T5）；③非当前 session 不开流属前端计划 |

**2. 范围说明（不在本计划）：** §7 前端状态机、§9.5 前端 reducer/hook、§10 验收②③中"前端打开/标记 session"的部分属于 scimaster-bohr-chat 仓库，单独成前端计划，依赖本计划"协议契约"节固定的端点与 payload。另外，internal HTTP trigger 的 workspace 传递（`ChatSendRequest.directory` 当前在 internal 模式被静默丢弃）是既有 gap、非本计划回归，标为 follow-up 单独处理（见 Task 5 已知限制）。

**3. Placeholder scan：** 无 TBD / "按需补充" / "同上"；每个改代码的 step 给出完整可运行代码、精确命令与 Expected。

**4. Type consistency：** `TriggerStreamContext`（字段 `task_id/invocation_id/owner/job/event/dedup_key`）在 T3 定义、T5 与 chat_api 一致使用；`_publish_user_wakeup(user_id, session_id, reason)` 签名在 T3 定义、T5 调用一致；`publish_user_wakeup(user_id, payload)`（DAO）在 T1 定义、T3 `_publish_user_wakeup` 调用一致；`user_wakeup_channel(user_id)` 在 T1 定义、T4 订阅复用；`list_session_ids_by_status(user_id, statuses)`（DAO）/`list_waiting_or_active_session_ids(user_id)`（service）在 T2 定义、T4 调用一致；`generate_internal_trigger_stream(session_id, ctx)` 在 T5 定义、chat_api 调用一致。
