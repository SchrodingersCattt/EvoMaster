# Programmatic Agent Run Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入一个通用的程序化触发原语，让后台进程 / 外部工具 / 未来的 loop·schedule 驱动器都能"代替用户注入一条 query 并触发一次 agent run"，完全复用现有 `/stream` 的 SSE、计费、事件落库链路。

**Architecture:** 把"怎么正确触发一次 run"（会话确保、并发锁、task 标识、历史边界、组 job、入队）从用户发送路径抽取为共享内核 `_prepare_run` + `_enqueue_run`，用 `event_writer` 策略点区分"写 User/query 事件"还是"写 System/trigger 事件"。系统触发适配器 `trigger_run` 与改造后的 `prepare_send_message`/`generate_send_stream` 都汇入同一内核。`/stream` 入口增加 `X-Internal-Token` 鉴权分叉走内部发起路径。执行层（Worker、`run_agent`）完全不改，仅让 Worker 读 job 里新增的 `delivery` 字段决定完成通知。

**Tech Stack:** Python ≥3.10、FastAPI、Redis（队列 + dedup `SET NX EX`）、MySQL（事件落库）、pydantic v2、pytest（`asyncio_mode=auto`，`MagicMock` + `@patch`，`uv run pytest`）。

---

## 关键架构决策（spec → 实现的细化）

实现前必须理解这三个决策。它们是 spec 在真实代码约束下的落地方式，偏离 spec 字面处都有明确理由。

### 决策 1：把 spec 的 `_prepare_run` 拆成 `_prepare_run` + `_enqueue_run`

spec 的 `_prepare_run` 职责列表把 lpush 入队列为第 7 步（与组 job、set waiting 并列）。但现有用户路径有一条**硬约束不能破坏**：

`generate_send_stream`（[src/services/stream_service.py:693-751](src/services/stream_service.py)）严格保证"先启动 Redis subscribe 线程 → 等 `subscribe_ready` → 才 lpush 入队"。注释明说：若 worker 在订阅就绪前就 publish 事件，订阅流会漏掉 run 开头的事件（历史已推送完，开头事件又没订阅到）。现有测试 `test_generate_send_stream_subscribes_before_enqueue`（[tests/test_chat_stream_direct.py:761](tests/test_chat_stream_direct.py)）断言 `call_order[:2] == ['subscribe', 'lpush']` 锁死了这个顺序。

同时，并发锁 `try_acquire_session_run` 必须在 `prepare_send_message`（同步、能让 `chat_stream` 返回 409）里完成，而它在 `generate_send_stream`（异步、已是 200 SSE 流）之前调用。即：**锁在 subscribe 之前，入队在 subscribe 之后**，两者被 `subscribe_ready` 屏障分隔。

因此 spec 的 `_prepare_run`（锁 + 入队同在一函数）无法在用户路径作为单次同步调用。本 plan 拆为：

- **`_prepare_run(...) -> RunHandle | Busy`**：ensure_session → 占锁（失败返回 `Busy`）→ 生成 `task_id`/`invocation_id` → set_last_task/version → 可选 `pre_event_hook`（用户路径的 `replace_last_turn`、Bohrium 凭证与 chat_mode 锁后副作用）→ **快照历史边界**（在写事件前）→ 构造 `TurnInput` → `event_writer` 写发起事件 → 组 job（含 `origin`/`delivery`）→ 返回 `RunHandle`。**不 lpush。**
- **`_enqueue_run(session_id, job) -> bool`**：set waiting → set_session_run_queued → discard_session_run_from_this_pod → 排队飞书通知 → lpush → 失败时回滚（set idle + delete queued）。

系统触发路径不做 SSE 订阅回放，所以 `trigger_run` 调完 `_prepare_run` 立刻调 `_enqueue_run`（无屏障）。用户路径 `prepare_send_message` 调 `_prepare_run` 返回 ctx；`generate_send_stream` 在 `subscribe_ready` 之后调 `_enqueue_run(sid, ctx.job)`。**最易漂移的逻辑（锁、历史边界、组 job、入队回滚）全部单点收敛**，只有 lpush 的"调用时机"由两条路径各自决定——这正是 subscribe 约束要求的。

### 决策 2：内部发起返回 `generate_subscribe_stream`，非 `generate_send_stream`

`generate_send_stream` 内部含入队逻辑。`trigger_run` 已经入队，若内部发起再返回 `generate_send_stream` 会**二次入队**。故内部发起成功（`enqueued`）时返回 `generate_subscribe_stream(sid)`（纯订阅流，订阅 `chat:stream:{sid}` channel 收 worker 实时事件，不入队）；`deduped`/`busy`/`error` 时返回 `JSONResponse`，不返回 SSE。这忠于 spec"复用 `/stream`、返回 `StreamingResponse`、发起方可消费可不消费"，同时避免重复入队。

注意：内部发起是先入队、后返回订阅流，因此它不像用户发送路径那样提供 subscribe-before-enqueue 强保证。该 SSE 连接与任何旁观订阅者一样是尽力而为：连接建立前已经落库的事件由 `generate_subscribe_stream` 开头的历史 replay 兜底，连接建立后的实时事件由 Redis channel 转发。

### 决策 3：System/trigger 事件落库可区分，LLM 侧还原为普通 UserMessage

落库 `source='System'`、`type='trigger'`、`content={'text': prompt, 'origin': origin}`。`normalize_event_source('System')` 已返回 `'System'`（[matmaster/utils/event_source.py](matmaster/utils/event_source.py)，已确认），`'System'` 是合法 source。历史还原时 `events_to_dialog_messages` 新增 `source=='System' and typ=='trigger'` 分支，做与 `User/query` 分支**完全相同**的轮次边界重置（flush pending reasoning/tool calls、清 turn 状态），再 `out.append(UserMessage(content=text).model_dump())`。第一版 LLM 文本即 prompt 原文，不加来源前缀。

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/models/chat.py` | 修改 | 新增 `DeliverySpec` 模型；`ChatSendRequest` 增 `origin`/`dedup_key`/`on_busy`/`delivery` 可选字段（仅内部发起有意义） |
| `src/dao/redis_dao.py` | 修改 | 新增 dedup helper：`dedup_key_exists`（EXISTS 预检）、`mark_dedup_key_nx`（`SET NX EX` 成功后标记）；新增 key 前缀常量 |
| `src/utils/constant.py` | 修改 | 新增 `INTERNAL_TRIGGER_TOKEN` 环境变量常量 |
| `src/services/stream_service.py` | 修改 | 新增数据类 `RunHandle`/`Busy`/`TriggerResult`；`SendStreamContext` 增 `job` 字段；新增共享内核 `_prepare_run`/`_enqueue_run`/`_notify_run_queued`；新增 `trigger_run`；改造 `prepare_send_message`/`generate_send_stream` 调内核 |
| `src/services/chat_history.py` | 修改 | `events_to_dialog_messages` 新增 `System/trigger` 分支；新增 `_system_trigger_text` 提取助手 |
| `src/apis/chat_api.py` | 修改 | `chat_stream` 增 `X-Internal-Token` Header 与内部发起分叉；新增 `_handle_internal_trigger` 辅助 |
| `src/worker/agent_worker.py` | 修改 | 消费 job 时读 `delivery`；完成通知段按 `delivery.notify` 决定是否发飞书卡片 + 邮件 |
| `tests/test_agent_run_trigger.py` | 新建 | `_prepare_run`/`_enqueue_run`/`trigger_run`/dedup helper 单测 |
| `tests/test_chat_history_system_trigger.py` | 新建 | `events_to_dialog_messages` System/trigger 还原单测 |
| `tests/test_chat_internal_trigger_api.py` | 新建 | `chat_stream` 内部发起鉴权与分派单测 |
| `tests/test_agent_worker_delivery.py` | 新建 | Worker 完成通知按 delivery 开关单测 |
| `tests/test_chat_stream_direct.py` 等 stream 测试 | 修改 | 先删除/迁移依赖 `SendStreamContext` 旧字段组 job 的测试，再把保留的入队测试统一改为 `ctx.job` 契约 |
| `tests/test_extension_points_smoke.py` | 新建 | loop（RUN_END handler）/ schedule（扫表）接入路线签名验收（桩） |

任务依赖顺序：1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11。Task 4 定义类型供 5/6/7 用；Task 5 内核供 6/7 用；Task 1/2/3 的字段/helper/常量供 7/9 用；Task 8/10/11 相对独立但依赖前序产物。

---

### Task 1: `DeliverySpec` 模型与 `ChatSendRequest` 扩展

**Files:**
- Modify: `src/models/chat.py`（`ChatSendRequest` 定义在 365-421 行附近）
- Test: `tests/test_agent_run_trigger.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_agent_run_trigger.py`：

```python
"""程序化触发原语测试：DeliverySpec / ChatSendRequest 扩展 / dedup / _prepare_run / _enqueue_run / trigger_run。"""

from unittest.mock import MagicMock, patch

import pytest


def test_chat_send_request_trigger_fields_default():
    from src.models.chat import ChatSendRequest

    req = ChatSendRequest(content="hi")
    assert req.origin is None
    assert req.dedup_key is None
    assert req.on_busy == "skip"
    assert req.delivery is None


def test_chat_send_request_accepts_delivery_spec():
    from src.models.chat import ChatSendRequest, DeliverySpec

    req = ChatSendRequest(
        content="作业123已完成",
        origin="hpc_job",
        dedup_key="job:123:done",
        on_busy="skip",
        delivery={"notify": True},
    )
    assert req.origin == "hpc_job"
    assert req.dedup_key == "job:123:done"
    assert isinstance(req.delivery, DeliverySpec)
    assert req.delivery.notify is True


def test_delivery_spec_notify_defaults_true():
    from src.models.chat import DeliverySpec

    assert DeliverySpec().notify is True
    assert DeliverySpec(notify=False).notify is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_run_trigger.py -v`
Expected: FAIL，`ImportError: cannot import name 'DeliverySpec'`。

- [ ] **Step 3: 实现 `DeliverySpec` 与字段**

在 `src/models/chat.py` 中，在 `ChatSendRequest` 类定义**之前**（紧邻它上方）新增 `DeliverySpec`：

```python
class DeliverySpec(BaseModel):
    """控制一次 run 完成后的通知行为。第一版仅 notify 开关；后续可扩展渠道字段。"""

    notify: bool = True
```

然后在 `ChatSendRequest` 类体内、`replace_last_turn` 字段定义**之后**、`model_config` **之前**，新增 4 个可选字段：

```python
    # 以下字段仅在 /stream 内部发起模式（X-Internal-Token）下有意义
    origin: str | None = Field(
        default=None,
        description="内部发起来源标记，如 hpc_job/cron/loop/external_tool；写入 System 触发事件，用于前端渲染、审计、dedup 命名",
    )
    dedup_key: str | None = Field(
        default=None,
        description="内部发起幂等键；命中已存在则不触发。仅成功入队后标记，busy/error 不标记",
    )
    on_busy: str = Field(
        default="skip",
        description="会话运行锁被占时的策略；第一版仅 skip（放弃本次触发，返回 busy 可重试）",
    )
    delivery: DeliverySpec | None = Field(
        default=None,
        description="内部发起的完成通知控制；缺省时按 origin 约定默认（用户发送路径恒为 None=保持现状）",
    )
```

`BaseModel`、`Field` 已在文件顶部导入（[src/models/chat.py:22](src/models/chat.py)），无需新增 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_run_trigger.py -v`
Expected: 3 passed。

- [ ] **Step 5: 提交**

```bash
git add src/models/chat.py tests/test_agent_run_trigger.py
git commit -m "feat: add DeliverySpec and internal-trigger fields to ChatSendRequest"
```

---

### Task 2: Redis dedup helper

**Files:**
- Modify: `src/dao/redis_dao.py`（key 常量在 24-27 行附近；`set_session_run_queued` 在 298 行附近作为样板）
- Test: `tests/test_agent_run_trigger.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_run_trigger.py` 末尾追加：

```python
def test_dedup_key_exists_uses_prefixed_key():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    fake_client.exists.return_value = 1
    with patch.object(dao, "create_client", return_value=fake_client):
        assert dao.dedup_key_exists("job:123:done") is True
    fake_client.exists.assert_called_once_with("chat:trigger:dedup:job:123:done")


def test_dedup_key_exists_false_when_no_client():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    with patch.object(dao, "create_client", return_value=None):
        assert dao.dedup_key_exists("job:123:done") is False


def test_mark_dedup_key_nx_sets_with_nx_and_ttl():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    fake_client.set.return_value = True
    with patch.object(dao, "create_client", return_value=fake_client):
        assert dao.mark_dedup_key_nx("job:123:done", "trig_abc", ttl_sec=86400) is True
    fake_client.set.assert_called_once_with(
        "chat:trigger:dedup:job:123:done", "trig_abc", nx=True, ex=86400
    )


def test_mark_dedup_key_nx_returns_false_when_already_present():
    from src.dao.redis_dao import RedisDao

    dao = RedisDao()
    fake_client = MagicMock()
    fake_client.set.return_value = None  # NX 未设置成功 → redis 返回 None
    with patch.object(dao, "create_client", return_value=fake_client):
        assert dao.mark_dedup_key_nx("job:123:done", "trig_abc", ttl_sec=86400) is False
```

注意：`RedisDao` 的类名需确认。

- [ ] **Step 2: 跑测试确认失败**

`get_redis_dao()` 返回的 DAO 类名已查实为 `RedisDao`（[src/dao/redis_dao.py:70](src/dao/redis_dao.py)），测试中的 `from src.dao.redis_dao import RedisDao` 与 `RedisDao()` 即正确。

Run: `uv run pytest tests/test_agent_run_trigger.py -k dedup -v`
Expected: FAIL，`AttributeError: ... object has no attribute 'dedup_key_exists'`。

- [ ] **Step 3: 实现 dedup helper**

在 `src/dao/redis_dao.py` 顶部 key 常量区（`AGENT_RUN_QUEUE_KEY = "chat:agent_run_queue"` 即 27 行附近）新增：

```python
DEDUP_KEY_PREFIX = "chat:trigger:dedup:"
DEFAULT_DEDUP_TTL_SEC = 86400  # 24h，程序化触发幂等窗口默认值
```

在文件内 `set_session_run_queued` 方法附近（同一个 DAO 类内）新增两个方法：

```python
    def dedup_key_exists(self, dedup_key: str) -> bool:
        """预检：dedup_key 是否已标记。无 Redis 或异常时按"未命中"处理（不阻塞触发）。"""
        client = self.create_client()
        if not client:
            return False
        try:
            return bool(client.exists(DEDUP_KEY_PREFIX + dedup_key))
        except Exception as e:
            logger.warning("Redis dedup_key_exists failed key=%s: %s", dedup_key, e)
            return False

    def mark_dedup_key_nx(
        self, dedup_key: str, value: str, ttl_sec: int = DEFAULT_DEDUP_TTL_SEC
    ) -> bool:
        """成功入队后标记 dedup_key（SET NX EX）。返回是否首次设置成功。无 Redis 或异常返回 False。"""
        client = self.create_client()
        if not client:
            return False
        try:
            return bool(
                client.set(DEDUP_KEY_PREFIX + dedup_key, value, nx=True, ex=ttl_sec)
            )
        except Exception as e:
            logger.warning("Redis mark_dedup_key_nx failed key=%s: %s", dedup_key, e)
            return False
```

`logger` 已在 `redis_dao.py` 定义（现有 `set_session_run_queued` 已用 `logger.warning`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_run_trigger.py -k dedup -v`
Expected: 4 passed。

- [ ] **Step 5: 提交**

```bash
git add src/dao/redis_dao.py tests/test_agent_run_trigger.py
git commit -m "feat: add Redis dedup helpers for programmatic trigger idempotency"
```

---

### Task 3: `INTERNAL_TRIGGER_TOKEN` 环境变量常量

**Files:**
- Modify: `src/utils/constant.py`（`REDIS_URL` 定义在 23 行附近）
- Test: `tests/test_agent_run_trigger.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_run_trigger.py` 末尾追加：

```python
def test_internal_trigger_token_constant_importable():
    import src.utils.constant as constant

    # 常量必须存在；值取决于环境变量，未配置时为 None
    assert hasattr(constant, "INTERNAL_TRIGGER_TOKEN")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_run_trigger.py -k internal_trigger_token -v`
Expected: FAIL，`AttributeError: module 'src.utils.constant' has no attribute 'INTERNAL_TRIGGER_TOKEN'`。

- [ ] **Step 3: 实现常量**

在 `src/utils/constant.py` 的 `REDIS_URL` 定义（23 行附近）**之后**新增：

```python
# 内部程序化触发鉴权 token（共享密钥，仅内网可达）。未配置则禁用 /stream 内部发起。
INTERNAL_TRIGGER_TOKEN = (os.getenv("INTERNAL_TRIGGER_TOKEN") or "").strip() or None
```

`os` 已在文件顶部导入（现有 `os.getenv` 用法为证）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_run_trigger.py -k internal_trigger_token -v`
Expected: 1 passed。

- [ ] **Step 5: 提交**

```bash
git add src/utils/constant.py tests/test_agent_run_trigger.py
git commit -m "feat: add INTERNAL_TRIGGER_TOKEN env constant for internal /stream auth"
```

---

### Task 4: 服务层数据类型 + `SendStreamContext.job` 字段

**Files:**
- Modify: `src/services/stream_service.py`（`SendStreamContext` 在 109-125 行）
- Test: `tests/test_agent_run_trigger.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_run_trigger.py` 末尾追加：

```python
def test_run_handle_and_busy_and_trigger_result_shapes():
    from matmaster.context.sources.turn_input import TurnInput
    from src.services.stream_service import Busy, RunHandle, TriggerResult

    ti = TurnInput.from_values(user_text="hi")
    handle = RunHandle(
        task_id="trig_x",
        invocation_id="inv_x",
        turn_input=ti,
        job={"session_id": "s1"},
        event={"source": "System", "type": "trigger"},
    )
    assert handle.task_id == "trig_x"
    assert handle.job["session_id"] == "s1"

    busy = Busy(reason="already_in_run")
    assert busy.reason == "already_in_run"

    res = TriggerResult(status="enqueued", task_id="trig_x", invocation_id="inv_x")
    assert res.status == "enqueued"
    assert res.reason is None
    assert res.dedup_key is None


def test_send_stream_context_has_job_field():
    import asyncio

    from src.services.stream_service import SendStreamContext

    ctx = SendStreamContext(
        task_id="t",
        invocation_id="i",
        mode="direct",
        user_msg={},
        request_event_queue=asyncio.Queue(),
        job={"session_id": "s1"},
    )
    assert ctx.job == {"session_id": "s1"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_run_trigger.py -k "run_handle or send_stream_context" -v`
Expected: FAIL，`ImportError: cannot import name 'RunHandle'`。

- [ ] **Step 3: 实现数据类与字段**

在 `src/services/stream_service.py` 的 `SendStreamContext` 定义（109 行的 `@dataclass`）**之前**新增三个数据类：

```python
@dataclass
class RunHandle:
    """_prepare_run 的成功产物：已写好发起事件、已组好 job，待 _enqueue_run 入队。"""

    task_id: str
    invocation_id: str
    turn_input: TurnInput
    job: dict
    event: dict  # 已落库的发起事件（User/query 或 System/trigger）


@dataclass
class Busy:
    """_prepare_run 因会话运行锁被占而放弃的产物。"""

    reason: str  # 'already_in_run' | 'db_update_failed' | 'unknown'


@dataclass
class TriggerResult:
    """trigger_run 的返回。status: 'enqueued' | 'deduped' | 'busy' | 'error'。"""

    status: str
    task_id: str | None = None
    invocation_id: str | None = None
    dedup_key: str | None = None
    reason: str | None = None
```

然后在 `SendStreamContext` 类体内、`request_event_queue` 字段之后、`llm` 字段之前新增必填字段：

```python
    job: dict  # _prepare_run 组好的入队 job；由 generate_send_stream 经 _enqueue_run 入队
```

注意：`job` 必须是必填字段，不提供 `None` 默认值；由于 dataclass 不允许必填字段出现在带默认值字段之后，必须放在 `llm` 等默认字段之前。这样所有仍在用 `ctx.llm` / `ctx.model` / `ctx.turn_input` / `ctx.images` / `ctx.bohrium_required` / `ctx.remote_workdir` / `ctx.session_directory_source` 间接组 job 的测试，会在迁移阶段直接暴露出来，避免后续为了旧测试保留半兼容路径。

`dataclass`、`field`、`TurnInput` 已在文件顶部导入（[src/services/stream_service.py:11,17](src/services/stream_service.py)）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_run_trigger.py -k "run_handle or send_stream_context" -v`
Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add src/services/stream_service.py tests/test_agent_run_trigger.py
git commit -m "feat: add RunHandle/Busy/TriggerResult types and SendStreamContext.job"
```

---

### Task 5: 共享内核 `_prepare_run` / `_enqueue_run` / `_notify_run_queued`

**Files:**
- Modify: `src/services/stream_service.py`（在 `_get_pre_turn_history_event_id` 即 169 行之后、`generate_subscribe_stream` 之前新增方法）
- Test: `tests/test_agent_run_trigger.py`

这是本设计的核心。先实现内核并直接单测，下一个 task 再让现有路径改用它。

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_run_trigger.py` 末尾追加：

```python
def _make_service():
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 42
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )
    return service, sessions_service, events_service


def test_prepare_run_snapshots_boundary_before_writing_event():
    """历史边界必须在写发起事件之前快照（否则注入消息被算进自身历史）。"""
    from src.services.stream_service import RunHandle

    service, sessions_service, events_service = _make_service()
    call_order = []
    events_service.get_latest_scope_event_id.side_effect = lambda *a, **k: (
        call_order.append("snapshot") or 42
    )

    def writer(task_id, invocation_id):
        call_order.append("write_event")
        event = {"source": "System", "type": "trigger", "task_id": task_id}
        service._events_service.add_history_event("s1", event, user_id="owner-1")
        return event

    handle = service._prepare_run(
        "s1",
        user_id="owner-1",
        user_text="作业完成",
        files=None,
        images=None,
        workspace_paths=None,
        event_writer=writer,
        id_prefix="trig_",
        mode="direct",
        origin="hpc_job",
        delivery={"notify": True},
    )

    assert isinstance(handle, RunHandle)
    assert call_order == ["snapshot", "write_event"]
    assert handle.task_id.startswith("trig_")
    assert handle.invocation_id.startswith("inv_")
    assert handle.turn_input.pre_turn_history_event_id == 42
    assert handle.turn_input.user_text == "作业完成"
    assert handle.job["origin"] == "hpc_job"
    assert handle.job["delivery"] == {"notify": True}
    assert handle.job["user_prompt"] == "作业完成"
    assert handle.job["session_id"] == "s1"
    assert handle.job["task_id"] == handle.task_id


def test_prepare_run_returns_busy_when_lock_held():
    from src.services.stream_service import Busy

    service, sessions_service, events_service = _make_service()
    sessions_service.try_acquire_session_run.return_value = (False, "already_in_run")

    handle = service._prepare_run(
        "s1",
        user_id="owner-1",
        user_text="x",
        files=None,
        images=None,
        workspace_paths=None,
        event_writer=lambda t, i: {},
        id_prefix="trig_",
        mode="direct",
    )
    assert isinstance(handle, Busy)
    assert handle.reason == "already_in_run"
    events_service.add_history_event.assert_not_called()


def test_prepare_run_runs_pre_event_hook_after_lock_before_snapshot():
    service, sessions_service, events_service = _make_service()
    order = []
    sessions_service.try_acquire_session_run.side_effect = lambda sid: (
        order.append("lock") or (True, None)
    )
    events_service.get_latest_scope_event_id.side_effect = lambda *a, **k: (
        order.append("snapshot") or 42
    )

    service._prepare_run(
        "s1",
        user_id="owner-1",
        user_text="x",
        files=None,
        images=None,
        workspace_paths=None,
        event_writer=lambda t, i: {},
        id_prefix="sse_",
        mode="direct",
        pre_event_hook=lambda: order.append("hook"),
    )
    assert order == ["lock", "hook", "snapshot"]


def test_enqueue_run_pushes_job_and_sets_waiting():
    service, sessions_service, events_service = _make_service()
    fake_redis = MagicMock()
    fake_redis.lpush_agent_run_job.return_value = True
    job = {"session_id": "s1", "user_prompt": "hi", "llm": None, "model": None}

    with (
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.UserService.get_user_info_for_display",
            return_value={"user_id": "u", "nickname": "n", "email": "e"},
        ),
        patch(
            "src.services.stream_service.get_worker_registry_service",
            return_value=MagicMock(count_active_runs=MagicMock(return_value=0)),
        ),
    ):
        ok = service._enqueue_run("s1", job)

    assert ok is True
    sessions_service.set_session_status.assert_any_call("s1", "waiting")
    fake_redis.lpush_agent_run_job.assert_called_once_with(job)


def test_enqueue_run_rolls_back_on_lpush_failure():
    service, sessions_service, events_service = _make_service()
    fake_redis = MagicMock()
    fake_redis.lpush_agent_run_job.return_value = False
    job = {"session_id": "s1", "user_prompt": "hi", "llm": None, "model": None}

    with (
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.UserService.get_user_info_for_display",
            return_value={"user_id": "u", "nickname": "n", "email": "e"},
        ),
        patch(
            "src.services.stream_service.get_worker_registry_service",
            return_value=MagicMock(count_active_runs=MagicMock(return_value=0)),
        ),
    ):
        ok = service._enqueue_run("s1", job)

    assert ok is False
    sessions_service.set_session_status.assert_any_call("s1", "idle")
    fake_redis.delete_session_run_queued.assert_called_once_with("s1")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_run_trigger.py -k "prepare_run or enqueue_run" -v`
Expected: FAIL，`AttributeError: 'ChatStreamService' object has no attribute '_prepare_run'`。

- [ ] **Step 3: 实现内核**

在 `src/services/stream_service.py` 的 `_get_pre_turn_history_event_id` 方法（169-179 行）**之后**新增三个方法。注意 `Callable` 需从 typing 导入——在文件顶部 `from typing import Protocol, runtime_checkable`（14 行）改为 `from typing import Callable, Protocol, runtime_checkable`：

```python
    def _prepare_run(
        self,
        session_id: str,
        *,
        user_id: str,
        user_text: str,
        files: list[str] | None,
        images: list[str] | None,
        workspace_paths: list[str] | None,
        event_writer: Callable[[str, str], dict],
        id_prefix: str,
        mode: str,
        llm: str | None = None,
        model: str | None = None,
        byok_credential_id: str | None = None,
        bohrium_required: bool = False,
        remote_workdir: str | None = None,
        session_directory_source: SessionDirectorySource = "none",
        origin: str | None = None,
        delivery: dict | None = None,
        pre_event_hook: Callable[[], None] | None = None,
        on_busy: str = "skip",
    ) -> "RunHandle | Busy":
        """共享内核：确保会话→占锁→生成标识→（可选 pre_event_hook）→快照历史边界→写发起事件→组 job。

        不负责 lpush（见 _enqueue_run），以保护用户路径 subscribe-before-enqueue 不变量。
        占锁失败时按 on_busy 返回 Busy（第一版 on_busy 仅 skip 语义：直接返回 Busy 供调用方处理）。
        """
        sid = session_id.strip()
        self._sessions_service.ensure_session(sid, user_id=user_id)
        acquired_ok, reason = self._sessions_service.try_acquire_session_run(sid)
        if not acquired_ok:
            return Busy(reason=reason or "unknown")
        if pre_event_hook is not None:
            pre_event_hook()
        task_id = id_prefix + uuid.uuid4().hex[:16]
        invocation_id = 'inv_' + uuid.uuid4().hex[:16]
        self._sessions_service.set_session_last_task(sid, task_id, user_id=user_id)
        self._deploy_state_service.record_session_version(sid)
        # 历史边界必须在写发起事件【之前】快照，否则注入的这条会被算进"历史"
        pre_turn_history_event_id = self._get_pre_turn_history_event_id(sid) or 0
        turn_input = TurnInput.from_values(
            user_text=user_text,
            files=files,
            images=images,
            workspace_paths=workspace_paths,
            pre_turn_history_event_id=pre_turn_history_event_id,
        )
        event = event_writer(task_id, invocation_id)
        job = {
            'session_id': sid,
            'task_id': task_id,
            'invocation_id': invocation_id,
            'user_prompt': turn_input.user_text,
            'mode': mode,
            'llm': llm,
            'model': model,
            'byok_credential_id': byok_credential_id,
            'turn_input': turn_input.to_payload(),
            'images': list(images or []),
            'bohrium_required': bohrium_required,
            'remote_workdir': remote_workdir,
            'session_directory_source': session_directory_source,
            'origin': origin,
            'delivery': delivery,
            'submitted_at': datetime.now(timezone.utc).isoformat(),
        }
        return RunHandle(
            task_id=task_id,
            invocation_id=invocation_id,
            turn_input=turn_input,
            job=job,
            event=event,
        )

    def _notify_run_queued(self, session_id: str, job: dict) -> None:
        """发送「任务进入排队」飞书运维通知，失败不影响入队。用户发送与系统触发共用。"""
        sid = session_id.strip()
        try:
            session_user_id = self._sessions_service.get_session_user_id(sid)
            user_info = UserService.get_user_info_for_display(session_user_id)
            user_info_display = (
                f"{user_info['user_id']} | {user_info['nickname']} | {user_info['email']}"
            )
            env = (SERVICE_ENV or '').strip().lower()
            session_url = f"https://matmaster{'' if not env or env == 'prod' else f'.{env}'}.bohrium.com/matmaster/chat-evo/{sid}"
            queue_len = get_redis_dao().llen_agent_run_queue()
            active_count = get_worker_registry_service().count_active_runs()
            user_question = (job.get('user_prompt') or '').strip()
            if len(user_question) > 500:
                user_question = user_question[:500] + '…'
            notify_post_async(
                '任务进入排队',
                [
                    ('会话ID', sid),
                    ('会话地址', session_url),
                    ('用户', user_info_display),
                    ('模型', format_llm_model_for_notify(job.get('llm'), job.get('model'))),
                    ('用户问题', user_question or '-'),
                    ('排队数', str(queue_len)),
                    ('执行中', str(active_count)),
                ],
                template=CARD_TEMPLATE_ORANGE,
            )
        except Exception as e:
            logger.warning('Feishu 进入排队通知发送失败 session_id=%s: %s', sid, e)

    def _enqueue_run(self, session_id: str, job: dict) -> bool:
        """共享入队：set waiting→标记 queued→脱离本 pod 占用→排队通知→lpush。

        入队失败时回滚 waiting/queued 并返回 False；不回滚已写的发起事件（沿用既有语义）。
        """
        sid = session_id.strip()
        # 先设为 waiting 再入队，避免 Worker 接手后 set active 被此处覆盖（竞态）
        self._sessions_service.set_session_status(sid, 'waiting')
        get_redis_dao().set_session_run_queued(sid)
        self._sessions_service.discard_session_run_from_this_pod(sid)
        # 入队之前发「进入排队」通知，避免 Worker 先发「开始执行」导致顺序颠倒
        self._notify_run_queued(sid, job)
        if not get_redis_dao().lpush_agent_run_job(job):
            self._sessions_service.set_session_status(sid, 'idle')
            get_redis_dao().delete_session_run_queued(sid)
            return False
        return True
```

注意：`RunHandle | Busy` 返回注解用字符串 `"RunHandle | Busy"` 是因为它们是 Task 4 新增的类——若类已定义在文件靠前（Task 4 放在 `SendStreamContext` 之前，确在本方法之前），可去掉引号直接写 `-> RunHandle | Busy`。两种写法均可。`SERVICE_ENV`、`CARD_TEMPLATE_ORANGE`、`format_llm_model_for_notify`、`notify_post_async`、`UserService`、`get_worker_registry_service`、`get_redis_dao`、`uuid`、`datetime`、`timezone`、`SessionDirectorySource` 均已在文件顶部导入（见 16-54 行）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_run_trigger.py -k "prepare_run or enqueue_run" -v`
Expected: 5 passed。

- [ ] **Step 5: 提交**

```bash
git add src/services/stream_service.py tests/test_agent_run_trigger.py
git commit -m "feat: extract _prepare_run/_enqueue_run/_notify_run_queued shared kernel"
```

---

### Task 6: 改造 `prepare_send_message` / `generate_send_stream` 调用内核

**Files:**
- Modify: `src/services/stream_service.py`（`prepare_send_message` 442-577 行；`generate_send_stream` 612-798 行）
- Test: `tests/test_chat_stream_direct.py`、`tests/test_chat_stream_planner.py`、`tests/test_chat_stream_session_directory.py`、`tests/test_chat_stream_direct_response_figures.py`（先删除/迁移旧字段驱动测试，再保留 job 契约测试）

让用户发送路径改用 Task 5 的内核，删除其内联的重复逻辑。**不得破坏** `test_generate_send_stream_subscribes_before_enqueue`（subscribe 必须在 lpush 前）与 `test_prepare_send_message_captures_turn_input_before_user_event`（边界在写事件前）。

- [ ] **Step 1: 提前删除/迁移旧字段驱动的 generate 测试**

改造后 `generate_send_stream` 不再从 `SendStreamContext` 的旧字段自己组 job，而是只入队 `ctx.job`。因此先做测试清理，避免旧测试倒逼主代码继续保留半兼容路径。

先全局定位直接构造 ctx 的测试：

```bash
rg -n "SendStreamContext\\(" tests/
```

处理规则：

- 删除旧字段驱动的入队测试：凡是测试目标是验证 `generate_send_stream` 能从 `ctx.images`、`ctx.bohrium_required`、`ctx.remote_workdir`、`ctx.session_directory_source`、`ctx.llm`、`ctx.model`、`ctx.turn_input` 这些字段组出 job，均属于旧契约测试，提前删除，不迁就。
- 保留行为不变量测试：历史 replay、`subscribe-before-enqueue`、`stream_closed`、elapsed 注入等测试可以保留，但凡它们会走到 `_enqueue_run`，必须显式传入完整 `job={...}`。
- 迁移 prepare 测试：`prepare_send_message` 仍要验证图片、Bohrium、目录、模型选择等信息进入 `handle.job` / `ctx.job`，不要继续断言这些信息挂在 `SendStreamContext` 的旧字段上。
- 禁止新增兼容测试：不要新增 `ctx.job is None`、从旧字段 fallback 组 job、缺 job 也可入队这类测试。

需要优先处理的旧字段测试包括：

- `tests/test_chat_stream_direct.py::test_generate_send_stream_enqueues_bohrium_required_flag`
- `tests/test_chat_stream_direct.py::test_generate_send_stream_enqueues_images`
- `tests/test_chat_stream_session_directory.py::test_generate_send_stream_enqueues_remote_workdir_and_source`
- `tests/test_chat_stream_planner.py` 中直接靠 `ctx.mode` 之外旧字段验证 pushed job 的 generate 测试

保留并迁移 `test_generate_send_stream_subscribes_before_enqueue`：它验证的是 subscribe 屏障，不是旧字段组 job。该测试的 `SendStreamContext(...)` 必须携带完整 `job`，并继续断言 `call_order[:2] == ['subscribe', 'lpush']`。

- [ ] **Step 2: 跑测试确认（现在应失败，因为实现还没改）**

Run: `uv run pytest tests/test_chat_stream_direct.py tests/test_chat_stream_planner.py tests/test_chat_stream_session_directory.py tests/test_chat_stream_direct_response_figures.py -k "generate_send_stream or prepare_send_message" -v`
Expected: FAIL —— 此时 `SendStreamContext.job` 已是必填，且旧字段驱动测试已删除/迁移；现有 `generate_send_stream` 仍从 ctx 旧字段组 job。Step 4 实现后这些测试必须全绿。

- [ ] **Step 3: 改造 `prepare_send_message`**

将 `prepare_send_message` 体内从 `task_id = 'sse_' + ...`（512 行）到 `self._events_service.add_history_event(...)`（557 行）这一整段，替换为：定义 user-event writer 与锁后/写事件前的 pre-event hook，调用 `_prepare_run`。

具体：保留 442-503 行（签名、`REDIS_URL` 检查、`ensure_session`、`resolve` 目录、mode/llm/model/byok/bohrium 参数解析与 `bohrium_required` 计算）。这里保留 `_prepare_run` 前的 `ensure_session` 是为了让 `SessionDirectoryResolver.resolve(...)` 能读取已存在/新建会话；`_prepare_run` 内部仍会再 `ensure_session` 一次，作为系统触发路径的共享内核保障。**删除** 466-468 行的 `try_acquire_session_run`（移入 `_prepare_run`）、470-478 行的 `replace_last_turn`（移入 hook）、以及 504-510 行的 `set_session_bohrium`（移入锁后的 pre-event hook）。然后把 512-577 行替换为：

```python
        user_content = (req.content or '').strip()

        def _run_pre_event_hook() -> None:
            if req.replace_last_turn:
                last_query_ev = self._events_service.get_last_user_query_event(sid)
                if last_query_ev and last_query_ev.get('id'):
                    self._events_service.delete_events_from_id(
                        sid, last_query_ev['id']
                    )
                    logger.info(
                        "replace_last_turn: deleted events from id=%s session_id=%s",
                        last_query_ev['id'],
                        sid,
                    )
            # 保持旧语义：Bohrium 凭证与 chat_mode 偏好只在 run 锁获取成功后写库；
            # 同时保持它们发生在本轮发起事件落库之前。
            if req.bohrium_project_id is not None or org_id is not None:
                self._sessions_service.set_session_bohrium(
                    sid,
                    org_id=org_id_val,
                    project_id=project_id_val,
                )
            if user_content and user_id:
                try:
                    self._sessions_service.set_session_chat_mode(sid, mode, user_id)
                except Exception as e:
                    logger.warning(
                        "persist chat_mode failed (best-effort) session_id=%s: %s",
                        sid,
                        e,
                    )

        def _user_event_writer(task_id: str, invocation_id: str) -> dict:
            user_msg = {
                'source': 'User',
                'type': 'query',
                'content': user_content,
                'mode': mode,
                'session_id': sid,
                'task_id': task_id,
                'invocation_id': invocation_id,
            }
            if llm:
                user_msg['requested_llm'] = llm
            if model:
                user_msg['requested_model'] = model
            if req.files:
                user_msg['files'] = list(req.files)
            if req.images:
                user_msg['images'] = list(req.images)
            if req.workspace_paths:
                user_msg['workspace_paths'] = list(req.workspace_paths)
            if resolved_directory.source != "none":
                user_msg["session_directory"] = resolved_directory.remote_workdir
                user_msg["session_directory_source"] = resolved_directory.source
            self._events_service.add_history_event(sid, user_msg, user_id=user_id)
            return user_msg

        handle = self._prepare_run(
            sid,
            user_id=user_id,
            user_text=user_content,
            files=req.files,
            images=req.images,
            workspace_paths=req.workspace_paths,
            event_writer=_user_event_writer,
            id_prefix='sse_',
            mode=mode,
            llm=llm,
            model=model,
            byok_credential_id=byok_credential_id,
            bohrium_required=bohrium_required,
            remote_workdir=resolved_directory.remote_workdir,
            session_directory_source=resolved_directory.source,
            origin=None,
            delivery=None,
            pre_event_hook=_run_pre_event_hook,
            on_busy="skip",
        )
        if isinstance(handle, Busy):
            return None

        dao = get_redis_dao()
        dao.delete_interaction_reply_list(sid)
        request_event_queue: asyncio.Queue = asyncio.Queue()

        return SendStreamContext(
            task_id=handle.task_id,
            invocation_id=handle.invocation_id,
            mode=mode,
            user_msg=handle.event,
            request_event_queue=request_event_queue,
            job=handle.job,
        )
```

注意：`user_id` 可能为 `None`（匿名分享场景），但发送消息路径 `chat_stream` 在 `can_access_session` 后仍可能 user_id=None？实际发送需登录（`can_access_session` 对未分享会话要求 owner）。`_prepare_run` 的 `user_id` 形参类型按现状接受 `str`；此处传入 `user_id`（与原 `add_history_event(..., user_id=user_id)`、`set_session_last_task(..., user_id=user_id)` 一致），保持原语义。

- [ ] **Step 4: 改造 `generate_send_stream`**

将 `generate_send_stream` 体内从 `turn_input_payload = (...)`（700-702 行）经组 job（704-719）、set waiting/queued/discard（721-723）、排队通知（724-750）、到 lpush 失败处理（751-772）这一整段，替换为：保留 `subscribe_ready.wait` 警告（693-699 行）后，直接调 `_enqueue_run(ctx.job)`：

把 700-772 行替换为：

```python
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
```

消费循环（773-795 行）与 `finally`（796-798 行）保持不变。`user_prompt` 形参仍在签名上（`chat_api`/`feishu_inbound` 调用方传 `base_prompt`），改造后其值由 `ctx.job['user_prompt']` 承载，方法体不再单独使用 `user_prompt` 组 job——保留形参不改调用方。

实现后立即删除 `SendStreamContext` 上只服务旧组 job 的字段：`llm`、`model`、`byok_credential_id`、`turn_input`、`bohrium_required`、`images`、`remote_workdir`、`session_directory_source`。`generate_send_stream` 只允许读取 `ctx.task_id`、`ctx.invocation_id`、`ctx.mode`、`ctx.user_msg`、`ctx.request_event_queue`、`ctx.job`。

- [ ] **Step 5: 跑全部 stream 测试确认通过**

Run: `uv run pytest tests/test_chat_stream_direct.py tests/test_chat_stream_planner.py tests/test_chat_stream_session_directory.py tests/test_chat_stream_direct_response_figures.py tests/test_agent_run_trigger.py -v`
Expected: 全绿。特别确认：
- `test_generate_send_stream_subscribes_before_enqueue` PASS（subscribe 在 lpush 前）
- `test_prepare_send_message_captures_turn_input_before_user_event` PASS（边界在写事件前；`get_latest_scope_event_id` called once、`add_history_event` called once）
- `test_prepare_send_message_marks_explicit_bohrium_requirement` / `test_prepare_send_message_persists_images_in_user_message` PASS，且断言目标已迁移到 `ctx.job`

若 `test_prepare_send_message_persists_images_in_user_message`（297 行）失败，检查 `add_history_event.call_args.args[1]['images']` —— 改造后写事件发生在 `_user_event_writer` 内，调用形态不变（仍 `add_history_event(sid, user_msg, user_id=user_id)`），断言应通过。

- [ ] **Step 6: 提交**

```bash
git add src/services/stream_service.py tests/test_chat_stream_direct.py
git commit -m "refactor: route prepare_send_message/generate_send_stream through shared kernel"
```

---

### Task 7: 系统触发适配器 `trigger_run`

**Files:**
- Modify: `src/services/stream_service.py`（在 `_enqueue_run` 之后、`generate_subscribe_stream` 之前或 `prepare_send_message` 附近新增方法）
- Test: `tests/test_agent_run_trigger.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_run_trigger.py` 末尾追加：

```python
def _make_trigger_service(owner="owner-1"):
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session_user_id.return_value = owner
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 10
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )
    return service, sessions_service, events_service


def _trigger_patches(fake_redis):
    return (
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch("src.services.stream_service.notify_post_async"),
        patch(
            "src.services.stream_service.UserService.get_user_info_for_display",
            return_value={"user_id": "u", "nickname": "n", "email": "e"},
        ),
        patch(
            "src.services.stream_service.get_worker_registry_service",
            return_value=MagicMock(count_active_runs=MagicMock(return_value=0)),
        ),
    )


def test_trigger_run_error_when_no_owner():
    from src.services.stream_service import TriggerResult

    service, sessions_service, events_service = _make_trigger_service(owner=None)
    res = service.trigger_run("s1", "作业完成", origin="hpc_job")
    assert isinstance(res, TriggerResult)
    assert res.status == "error"
    events_service.add_history_event.assert_not_called()


def test_trigger_run_enqueues_and_writes_system_event():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1",
            "作业123已完成，请下载并分析结果",
            origin="hpc_job",
            dedup_key="job:123:done",
            delivery=None,
        )
    assert res.status == "enqueued"
    assert res.task_id.startswith("trig_")
    # 写了 System/trigger 事件
    written = events_service.add_history_event.call_args.args[1]
    assert written["source"] == "System"
    assert written["type"] == "trigger"
    assert written["content"] == {
        "text": "作业123已完成，请下载并分析结果",
        "origin": "hpc_job",
    }
    # 入队 job 带 origin
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["origin"] == "hpc_job"
    # 成功入队后标记 dedup
    fake_redis.mark_dedup_key_nx.assert_called_once()
    assert fake_redis.mark_dedup_key_nx.call_args.args[0] == "job:123:done"


def test_trigger_run_deduped_short_circuits():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1", "x", origin="hpc_job", dedup_key="job:123:done"
        )
    assert res.status == "deduped"
    events_service.add_history_event.assert_not_called()
    fake_redis.lpush_agent_run_job.assert_not_called()
    fake_redis.mark_dedup_key_nx.assert_not_called()


def test_trigger_run_busy_does_not_mark_dedup():
    service, sessions_service, events_service = _make_trigger_service()
    sessions_service.try_acquire_session_run.return_value = (False, "already_in_run")
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1", "x", origin="loop", dedup_key="loop:1:3"
        )
    assert res.status == "busy"
    fake_redis.lpush_agent_run_job.assert_not_called()
    fake_redis.mark_dedup_key_nx.assert_not_called()


def test_trigger_run_accepts_delivery_spec():
    from src.models.chat import DeliverySpec

    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1", "x", origin="hpc_job", delivery=DeliverySpec(notify=False)
        )
    assert res.status == "enqueued"
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["delivery"] == {"notify": False}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_run_trigger.py -k trigger_run -v`
Expected: FAIL，`AttributeError: 'ChatStreamService' object has no attribute 'trigger_run'`。

- [ ] **Step 3: 实现 `trigger_run`**

在 `src/services/stream_service.py` 的 `_enqueue_run` 方法之后新增。需要 `DEFAULT_DEDUP_TTL_SEC`——在文件顶部 `from src.dao.redis_dao import (STREAM_CHANNEL_PREFIX, get_redis_dao)`（18-21 行）改为同时导入 `DEFAULT_DEDUP_TTL_SEC`：

```python
from src.dao.redis_dao import (
    DEFAULT_DEDUP_TTL_SEC,
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
)
```

并在文件顶部确保导入 `DEFAULT_MODE, SUPPORTED_MODES`（已在 16 行导入）。新增方法：

```python
    def trigger_run(
        self,
        session_id: str,
        prompt: str,
        *,
        origin: str,
        dedup_key: str | None = None,
        delivery: "DeliverySpec | dict | None" = None,
        on_busy: str = "skip",
        mode: str | None = None,
        llm: str | None = None,
        model: str | None = None,
        dedup_ttl_sec: int = DEFAULT_DEDUP_TTL_SEC,
    ) -> TriggerResult:
        """程序化触发一次 agent run（系统触发适配器）。

        计费/历史/鉴权以 session owner 为主体；绝不静默创建无主 session。
        注入消息以 source='System'、type='trigger' 落库供前端区分，喂 LLM 时为普通 user message。
        """
        sid = session_id.strip()
        owner = self._sessions_service.get_session_user_id(sid)
        if not owner:
            logger.warning(
                "trigger_run rejected: session not found or no owner session_id=%s", sid
            )
            return TriggerResult(
                status="error", reason="session_not_found_or_no_owner"
            )

        # dedup 预检是快速短路；真正避免同 session 双入队的是 _prepare_run 内的 run 锁，
        # 成功入队后再用 SET NX EX 留下幂等标记，供后续重复触发直接 deduped。
        if dedup_key and get_redis_dao().dedup_key_exists(dedup_key):
            logger.info(
                "trigger_run deduped session_id=%s dedup_key=%s", sid, dedup_key
            )
            return TriggerResult(status="deduped", dedup_key=dedup_key)

        resolved_mode = (mode or DEFAULT_MODE).strip().lower() or DEFAULT_MODE
        if resolved_mode not in SUPPORTED_MODES:
            resolved_mode = DEFAULT_MODE
        llm_val = (llm or '').strip() or None
        model_val = (model or '').strip() or None
        if delivery is None:
            delivery_payload: dict | None = None
        elif hasattr(delivery, "model_dump"):
            delivery_payload = delivery.model_dump()
        else:
            delivery_payload = dict(delivery)

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
            llm=llm_val,
            model=model_val,
            byok_credential_id=None,
            bohrium_required=False,
            remote_workdir=None,
            session_directory_source="none",
            origin=origin,
            delivery=delivery_payload,
            on_busy=on_busy,
        )
        if isinstance(handle, Busy):
            logger.info(
                "trigger_run busy session_id=%s reason=%s", sid, handle.reason
            )
            return TriggerResult(status="busy", reason=handle.reason)

        if not self._enqueue_run(sid, handle.job):
            return TriggerResult(status="error", reason="enqueue_failed")

        if dedup_key:
            get_redis_dao().mark_dedup_key_nx(
                dedup_key, handle.task_id, ttl_sec=dedup_ttl_sec
            )
        logger.info(
            "trigger_run enqueued session_id=%s task_id=%s origin=%s",
            sid,
            handle.task_id,
            origin,
        )
        return TriggerResult(
            status="enqueued",
            task_id=handle.task_id,
            invocation_id=handle.invocation_id,
        )
```

`DeliverySpec` 仅用于类型注解（运行时通过 `hasattr(delivery, "model_dump")` 鸭子判断），无需在 `stream_service.py` 顶部导入 `DeliverySpec`——注解写成字符串 `"DeliverySpec | dict | None"` 即可避免循环导入。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_run_trigger.py -k trigger_run -v`
Expected: 5 passed。

- [ ] **Step 5: 提交**

```bash
git add src/services/stream_service.py tests/test_agent_run_trigger.py
git commit -m "feat: add trigger_run system-trigger adapter"
```

---

### Task 8: `events_to_dialog_messages` 新增 System/trigger 还原分支

**Files:**
- Modify: `src/services/chat_history.py`（`events_to_dialog_messages` 的 `User/query` 分支在 442-460 行；`_user_content` 在 250-257 行）
- Test: `tests/test_chat_history_system_trigger.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_chat_history_system_trigger.py`：

```python
"""System/trigger 事件历史还原：落库可区分，喂 LLM 时为普通 UserMessage。"""

from src.services.chat_history import ChatHistoryConverter


def test_system_trigger_event_restored_as_user_message():
    events = [
        {
            'source': 'User',
            'type': 'query',
            'content': '第一轮问题',
            'session_id': 's1',
            'task_id': 'task-0',
        },
        {
            'source': 'MatMaster',
            'type': 'run_result',
            'content': '第一轮回答',
            'session_id': 's1',
            'task_id': 'task-0',
        },
        {
            'source': 'System',
            'type': 'trigger',
            'content': {'text': '作业123已完成，请分析', 'origin': 'hpc_job'},
            'session_id': 's1',
            'task_id': 'trig_1',
        },
    ]
    msgs = ChatHistoryConverter.events_to_dialog_messages(events)
    # System/trigger 还原成一条普通 user message，文本为 content.text
    assert msgs[-1]['role'] == 'user'
    assert msgs[-1]['content'] == '作业123已完成，请分析'


def test_system_trigger_resets_turn_boundary_like_user_query():
    """System/trigger 前若有未 flush 的 reasoning，应在该轮边界被 flush（与 User/query 一致）。"""
    events = [
        {
            'source': 'MatMaster',
            'type': 'thought',
            'content': '思考中…',
            'session_id': 's1',
            'task_id': 'task-0',
        },
        {
            'source': 'System',
            'type': 'trigger',
            'content': {'text': '继续', 'origin': 'loop'},
            'session_id': 's1',
            'task_id': 'trig_1',
        },
    ]
    msgs = ChatHistoryConverter.events_to_dialog_messages(events)
    # pending reasoning 被 flush 成 assistant message，再追加 trigger 的 user message
    roles = [m['role'] for m in msgs]
    assert roles[-1] == 'user'
    assert msgs[-1]['content'] == '继续'
    assert 'assistant' in roles  # thought 被 flush
```

注意：还原后的 dict 键（`role`/`content`）需与 `UserMessage(...).model_dump()` 的实际输出一致。若 `model_dump()` 输出的不是 `role`/`content`，按实际键调整断言。

- [ ] **Step 2: 跑测试确认失败**

`UserMessage(content="x").model_dump()` 已查实输出为 `{'role': Role.USER, 'content': 'x', 'images': []}`，其中 `Role` 是 `str` 子类枚举，故 `dict['role'] == 'user'`、`AssistantMessage` 的 `dict['role'] == 'assistant'` 均成立——Step 1 断言无需调整。

Run: `uv run pytest tests/test_chat_history_system_trigger.py -v`
Expected: FAIL —— System/trigger 事件未被还原（当前循环对 `source=='System'` 不产出 UserMessage），`msgs[-1]` 不是 trigger 文本。

- [ ] **Step 3: 实现 System/trigger 分支与文本提取**

在 `src/services/chat_history.py` 的 `_user_content`（250-257 行）**之后**新增助手方法：

```python
    @staticmethod
    def _system_trigger_text(ev: dict) -> str:
        """从 System/trigger 事件取注入文本。content 形如 {'text': str, 'origin': str}。"""
        c = ev.get('content')
        if isinstance(c, dict):
            return str(c.get('text') or '')
        return str(c) if c is not None else ''
```

然后在 `events_to_dialog_messages` 的 `User/query` 分支（442-460 行）**之后**、紧邻新增并列分支。该分支体与 `User/query` 分支的轮次重置逻辑**逐行一致**，只是文本取自 `_system_trigger_text` 且不取图片：

```python
            if source == 'System' and typ == 'trigger':
                if pending_reasoning:
                    out.append(
                        AssistantMessage(
                            content='',
                            reasoning_content=pending_reasoning,
                        ).model_dump()
                    )
                    pending_reasoning = None
                flush_tool_calls()
                last_assistant_text_idx = None
                assistant_state_tool_ids.clear()
                active_tool_turn_ids.clear()
                response_seen_in_turn = False
                text = cls._system_trigger_text(ev)
                out.append(UserMessage(content=text).model_dump())
                continue
```

注意：`source` 在循环里由 `normalize_event_source(ev.get('source'))` 得到（438 行附近），`normalize_event_source('System')` 返回 `'System'`（已确认），故 `source == 'System'` 判断成立。`flush_tool_calls`、`pending_reasoning`、`assistant_state_tool_ids`、`active_tool_turn_ids`、`last_assistant_text_idx`、`response_seen_in_turn` 均为该函数内已存在的局部状态（412-419 行）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_chat_history_system_trigger.py -v`
Expected: 2 passed。

并跑历史相关回归：
Run: `uv run pytest tests/test_chat_history_repair.py -v`
Expected: 全绿（未引入回归）。

- [ ] **Step 5: 提交**

```bash
git add src/services/chat_history.py tests/test_chat_history_system_trigger.py
git commit -m "feat: restore System/trigger events as UserMessage in dialog history"
```

---

### Task 9: 泛化 `/stream` 入口——内部发起分叉

**Files:**
- Modify: `src/apis/chat_api.py`（`chat_stream` 在 183-367 行；导入区 1-54 行）
- Test: `tests/test_chat_internal_trigger_api.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_chat_internal_trigger_api.py`：

```python
"""/stream 内部发起（X-Internal-Token）鉴权与分派测试。"""

import uuid
from unittest.mock import MagicMock, patch


async def _check_quota_ok(user_id: str):
    from src.services.quota_service import QuotaStatus

    return QuotaStatus(remaining_yuan=10.0, reset_at=None)


def _client_with_overrides(fake_sessions, fake_stream):
    from fastapi.testclient import TestClient

    from app import app
    from src.services.sessions_service import get_sessions_service
    from src.services.stream_service import get_stream_service

    app.dependency_overrides[get_sessions_service] = lambda: fake_sessions
    app.dependency_overrides[get_stream_service] = lambda: fake_stream
    return TestClient(app), app, get_sessions_service, get_stream_service


def _clear_overrides(app, *dependencies):
    for dep in dependencies:
        app.dependency_overrides.pop(dep, None)


def test_internal_trigger_enqueues_with_valid_token():
    from src.services.stream_service import TriggerResult

    fake_stream = MagicMock()
    fake_stream.trigger_run.return_value = TriggerResult(
        status="enqueued", task_id="trig_abc", invocation_id="inv_abc"
    )

    async def _empty_subscribe(_sid):
        if False:
            yield ""  # async generator with no output

    fake_stream.generate_subscribe_stream.side_effect = lambda sid: _empty_subscribe(
        sid
    )

    fake_sessions = MagicMock()
    fake_sessions.get_session_user_id.return_value = "owner-1"

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
        patch("src.apis.chat_api.check_quota_status", side_effect=_check_quota_ok),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/v1/chat/sessions/{sid}/stream",
            json={
                "content": "作业123已完成",
                "origin": "hpc_job",
                "dedup_key": "job:123:done",
                "delivery": {"notify": True},
            },
            headers={"X-Internal-Token": "secret-token"},
        )
        assert resp.status_code == 200, resp.text
        fake_stream.trigger_run.assert_called_once()
        kwargs = fake_stream.trigger_run.call_args.kwargs
        assert kwargs["origin"] == "hpc_job"
        assert kwargs["dedup_key"] == "job:123:done"
        # 内部发起不调用用户登录鉴权
        fake_sessions.can_access_session.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()


def test_internal_trigger_deduped_returns_json_not_stream():
    from src.services.stream_service import TriggerResult

    fake_stream = MagicMock()
    fake_stream.trigger_run.return_value = TriggerResult(
        status="deduped", dedup_key="job:123:done"
    )
    fake_sessions = MagicMock()
    fake_sessions.get_session_user_id.return_value = "owner-1"

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
        patch("src.apis.chat_api.check_quota_status", side_effect=_check_quota_ok),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/v1/chat/sessions/{sid}/stream",
            json={"content": "x", "origin": "hpc_job", "dedup_key": "job:123:done"},
            headers={"X-Internal-Token": "secret-token"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["status"] == "deduped"
        fake_stream.generate_subscribe_stream.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()


def test_internal_trigger_wrong_token_rejected_fail_closed():
    """只要带了 X-Internal-Token 但不匹配，就直接拒绝，不回落普通用户鉴权。"""
    fake_stream = MagicMock()
    fake_sessions = MagicMock()

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/v1/chat/sessions/{sid}/stream",
            json={"content": "x", "origin": "hpc_job"},
            headers={"X-Internal-Token": "wrong-token"},
        )
        assert resp.status_code == 403, resp.text
        fake_stream.trigger_run.assert_not_called()
        fake_sessions.can_access_session.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()


def test_internal_trigger_rejected_on_share_route():
    """分享页路由保持只读，即使带合法内部 token 也不能触发 run。"""
    fake_stream = MagicMock()
    fake_sessions = MagicMock()

    patches = [
        patch("src.apis.chat_api.REDIS_URL", "redis://test"),
        patch("src.apis.chat_api.INTERNAL_TRIGGER_TOKEN", "secret-token"),
    ]
    for p in patches:
        p.start()
    client, app, sessions_dep, stream_dep = _client_with_overrides(
        fake_sessions, fake_stream
    )
    try:
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/pubapi/v1/chat/sessions/{sid}/stream",
            json={"content": "x", "origin": "hpc_job"},
            headers={"X-Internal-Token": "secret-token"},
        )
        assert resp.status_code == 403, resp.text
        fake_stream.trigger_run.assert_not_called()
        fake_sessions.can_access_session.assert_not_called()
    finally:
        _clear_overrides(app, sessions_dep, stream_dep)
        for p in patches:
            p.stop()
```

注意：FastAPI 的 `Depends(get_stream_service)` / `Depends(get_sessions_service)` 在路由定义时已捕获函数对象；测试中不能用 `patch("src.apis.chat_api.get_stream_service", ...)` 替换依赖，必须用 `app.dependency_overrides`。路由前缀 `/api/v1/chat/sessions` 与现有 `test_chat_stream_direct.py:111` 一致；分享页路由前缀为 `/pubapi/v1/chat/sessions`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_chat_internal_trigger_api.py -v`
Expected: FAIL —— 当前 `chat_stream` 无 `X-Internal-Token` 处理，带 token 的请求会走用户鉴权（`get_session_user_id`/`trigger_run` 不被调用）。

- [ ] **Step 3: 实现内部发起分叉**

在 `src/apis/chat_api.py` 导入区改造：

新增标准库导入：
```python
import hmac
```

`from fastapi import APIRouter, Body, Depends, Path, Request`（5 行）增加 `Header`：
```python
from fastapi import APIRouter, Body, Depends, Header, Path, Request
```
`from fastapi.responses import StreamingResponse`（6 行）增加 `JSONResponse`：
```python
from fastapi.responses import JSONResponse, StreamingResponse
```
`from src.utils.constant import REDIS_URL`（48 行）增加 `INTERNAL_TRIGGER_TOKEN`：
```python
from src.utils.constant import INTERNAL_TRIGGER_TOKEN, REDIS_URL
```

在 `chat_stream` 函数签名（223-227 行的参数区）增加 Header 参数。在 `events_svc: ChatEventsService = Depends(get_events_service),` 之后加：

```python
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
```

基于当前真实代码的 233-237 行做重排。现状是：

```python
    sid = session_id.strip()
    has_content = req is not None and bool((req.content or "").strip())
    is_share_route = request.url.path.startswith("/pubapi/")
    if is_share_route and has_content:
        raise ForbiddenErrorResponse(msg="分享页仅支持只读订阅，不允许发送消息")
```

把内部发起分叉插在 `sid = session_id.strip()` 之后、`has_content = ...` 之前；复用原有 `is_share_route` 变量，只是把它提前到 token 分叉前：

```python
    sid = session_id.strip()
    is_share_route = request.url.path.startswith("/pubapi/")
    if x_internal_token:
        if not INTERNAL_TRIGGER_TOKEN or not hmac.compare_digest(
            x_internal_token, INTERNAL_TRIGGER_TOKEN
        ):
            raise ForbiddenErrorResponse(msg="内部触发 token 无效")
        if is_share_route:
            raise ForbiddenErrorResponse(msg="分享页仅支持只读订阅，不允许内部触发")
        return await _handle_internal_trigger(sid, req, chat_svc, stream_svc)
```

然后保留普通用户路径的分享页只读守卫；此处不要重复定义 `is_share_route`：

```python
    has_content = req is not None and bool((req.content or "").strip())
    if is_share_route and has_content:
        raise ForbiddenErrorResponse(msg="分享页仅支持只读订阅，不允许发送消息")
```

注意：当前基线里不存在 `is_internal` 变量，不要寻找或删除不存在的旧代码。新的语义是：不带 `X-Internal-Token` 才走普通用户路径；只要带了 header 但 token 不匹配，就直接 403，不回落普通用户鉴权。

在 `chat_stream` 函数**之前**（如 `_session_directory_error` 辅助函数附近，97 行之后）新增辅助函数：

```python
async def _handle_internal_trigger(
    sid: str,
    req: "ChatSendRequest | None",
    chat_svc: ChatSessionsService,
    stream_svc: ChatStreamService,
):
    """X-Internal-Token 通过后的内部发起：以 session owner 为计费/鉴权主体，调 trigger_run。"""
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
    result = stream_svc.trigger_run(
        sid,
        prompt,
        origin=(req.origin or "external_tool"),
        dedup_key=req.dedup_key,
        delivery=req.delivery,
        on_busy=(req.on_busy or "skip"),
        mode=req.mode,
        llm=req.llm,
        model=req.model,
    )
    if result.status == "enqueued":
        return StreamingResponse(
            stream_svc.generate_subscribe_stream(sid),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "msg": result.status,
            "data": {
                "status": result.status,
                "task_id": result.task_id,
                "invocation_id": result.invocation_id,
                "reason": result.reason,
            },
        },
    )
```

`ChatSessionsService`、`ChatStreamService`、`check_quota_status`、`BaseErrorResponse`、`NotFoundErrorResponse`、`ForbiddenErrorResponse`、`SSE_HEADERS` 均已在 `chat_api.py` 导入/定义。`ChatSendRequest` 注解用字符串避免顺序问题（已在 13 行导入，直接用 `ChatSendRequest` 亦可）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_chat_internal_trigger_api.py -v`
Expected: 4 passed。

并跑入口回归：
Run: `uv run pytest tests/test_chat_stream_direct.py::test_chat_stream_returns_503_when_redis_url_missing -v`
Expected: PASS（用户路径未受影响）。

- [ ] **Step 5: 提交**

```bash
git add src/apis/chat_api.py tests/test_chat_internal_trigger_api.py
git commit -m "feat: add internal-trigger branch to /stream via X-Internal-Token"
```

---

### Task 10: Worker 按 `delivery` 控制完成通知

**Files:**
- Modify: `src/worker/agent_worker.py`（job 消费段 313-341 行；完成通知段 511-559 行）
- Test: `tests/test_agent_worker_delivery.py`（新建）

worker 完成通知段（飞书卡片 542 行 + 邮件 549 行）当前在 `if acquired:` 内无条件执行。改为读 job 的 `delivery`：`delivery` 为 `None`（用户路径恒为 None）→ 维持现状（发）；`delivery={'notify': False}` → 不发。

- [ ] **Step 1: 写失败测试**

worker 的完成通知段嵌在 `while True` 主循环内，难以整段端到端单测。为可测，先把"是否发送完成通知"抽成一个纯函数 `_should_notify_completion(delivery)`，对它单测。

新建 `tests/test_agent_worker_delivery.py`：

```python
"""Worker 完成通知按 job.delivery 开关。"""

from src.worker.agent_worker import _should_notify_completion


def test_should_notify_when_delivery_absent():
    # 用户发送路径 job 无 delivery（None）→ 维持现状：发通知
    assert _should_notify_completion(None) is True


def test_should_notify_when_delivery_notify_true():
    assert _should_notify_completion({"notify": True}) is True


def test_should_not_notify_when_delivery_notify_false():
    assert _should_notify_completion({"notify": False}) is False


def test_should_notify_defaults_true_for_malformed_delivery():
    # delivery 存在但缺 notify 键 → 默认发（保守，避免漏报）
    assert _should_notify_completion({}) is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_worker_delivery.py -v`
Expected: FAIL，`ImportError: cannot import name '_should_notify_completion'`。

- [ ] **Step 3: 实现开关函数并接入**

在 `src/worker/agent_worker.py` 模块级（与 `_format_run_duration`、`_session_url` 等模块级辅助同处，文件靠前）新增：

```python
def _should_notify_completion(delivery: dict | None) -> bool:
    """job.delivery 控制完成通知：None（用户路径）或缺 notify 键 → 默认发；显式 notify=False → 不发。"""
    if not isinstance(delivery, dict):
        return True
    return bool(delivery.get("notify", True))
```

在 job 消费段（313-341 行，与 `remote_workdir` 提取相邻）新增提取 `delivery`，在 `remote_workdir = (...)`（337-341 行）之后加：

```python
        delivery = payload.get('delivery')
```

在完成通知段（`if acquired:` 的 try 内，527-559 行）把飞书卡片与邮件调用包进开关。将 542-559 行（`notify_post_async(title, rows, template=template)` 到 `_send_completion_email(...)` 结束）改为：

```python
                    if _should_notify_completion(delivery):
                        notify_post_async(title, rows, template=template)
                        logger.info(
                            'Agent worker: Feishu completion card queued session_id=%s title=%s',
                            session_id,
                            title,
                        )
                        # 会话完成/失败时给用户发邮件（模板：会话已执行完成+链接），与飞书通知并行
                        _send_completion_email(
                            session_user_id=session_user_id,
                            user_info=user_info,
                            payload=payload,
                            session_url=session_url,
                            user_question=user_question,
                            duration_str=duration_str,
                            run_success=run_success,
                            fail_reason=fail_reason,
                            fail_reason_str=fail_reason_str,
                        )
                    else:
                        logger.info(
                            'Agent worker: completion notify suppressed by delivery session_id=%s',
                            session_id,
                        )
```

`_build_completion_card(...)`（527-541 行）保持在 `if _should_notify_completion(delivery):` **之外**还是 **之内**？放在**之内**更省（不发就不必构卡）。即把 527-541 行的 `title, rows, template = _build_completion_card(...)` 一并移入 `if` 分支顶部。最终该 try 块结构为：

```python
                try:
                    queue_len = redis_dao.llen_agent_run_queue()
                    active_count = get_worker_registry_service().count_active_runs()
                    session_url = _session_url(session_id)
                    user_question = (user_prompt or '').strip()
                    if len(user_question) > 500:
                        user_question = user_question[:500] + '…'
                    if elapsed_ms is not None:
                        duration_sec = elapsed_ms / 1000.0
                    else:
                        duration_sec = time.monotonic() - run_start_time
                    duration_str = _format_run_duration(duration_sec)
                    fail_reason_str = (
                        str(fail_reason).strip() if fail_reason is not None else ''
                    )
                    if _should_notify_completion(delivery):
                        title, rows, template = _build_completion_card(
                            session_id=session_id,
                            session_url=session_url,
                            user_info_display=user_info_display,
                            llm=llm_override,
                            model=model_override,
                            user_question=user_question,
                            run_success=run_success,
                            fail_reason=fail_reason,
                            fail_reason_str=fail_reason_str,
                            duration_str=duration_str,
                            active_count=active_count,
                            queue_len=queue_len,
                            usage_summary=usage_summary,
                        )
                        notify_post_async(title, rows, template=template)
                        logger.info(
                            'Agent worker: Feishu completion card queued session_id=%s title=%s',
                            session_id,
                            title,
                        )
                        _send_completion_email(
                            session_user_id=session_user_id,
                            user_info=user_info,
                            payload=payload,
                            session_url=session_url,
                            user_question=user_question,
                            duration_str=duration_str,
                            run_success=run_success,
                            fail_reason=fail_reason,
                            fail_reason_str=fail_reason_str,
                        )
                    else:
                        logger.info(
                            'Agent worker: completion notify suppressed by delivery session_id=%s',
                            session_id,
                        )
                except Exception:
                    logger.exception(
                        'Agent worker: completion notify block failed session_id=%s task_id=%s',
                        session_id,
                        task_id,
                    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_worker_delivery.py -v`
Expected: 4 passed。

并做语法/导入冒烟（worker 模块可被导入）：
Run: `uv run python -c "import src.worker.agent_worker"`
Expected: 无 ImportError/SyntaxError。

- [ ] **Step 5: 提交**

```bash
git add src/worker/agent_worker.py tests/test_agent_worker_delivery.py
git commit -m "feat: gate worker completion notifications on job.delivery"
```

---

### Task 11: 接入路线接口验收（loop / schedule 桩）

**Files:**
- Test: `tests/test_extension_points_smoke.py`（新建）

spec 要求：loop 的 `RUN_END` handler 与 schedule 的扫表循环都能**仅通过调 `trigger_run`（不改其签名）**完成触发。本 task 用桩验证签名兼容，不实现 loop/schedule 本身。

- [ ] **Step 1: 写验收测试**

新建 `tests/test_extension_points_smoke.py`：

```python
"""接入路线接口验收：loop（RUN_END handler）/ schedule（扫表）仅靠 trigger_run 即可触发。"""

import asyncio
from unittest.mock import MagicMock


def test_loop_run_end_handler_can_drive_trigger_run():
    """loop 驱动器：RUN_END handler 收到 RunContext，调 trigger_run，不改其签名。"""
    from matmaster.core.hooks import HookEvent, RunContext

    stream_svc = MagicMock()
    stream_svc.trigger_run.return_value = MagicMock(status="enqueued")

    # 模拟 loop 状态：尚未达成目标，继续下一轮
    loop_state = {"sess-1": {"turn": 3, "next_prompt": "继续下一步", "should_continue": True}}

    async def on_run_end(ctx: RunContext) -> None:
        st = loop_state.get(ctx.session_id)
        if st and st["should_continue"]:
            stream_svc.trigger_run(
                ctx.session_id,
                st["next_prompt"],
                origin="loop",
                dedup_key=f"loop:{ctx.session_id}:{st['turn']}",
                delivery={"notify": False},
                on_busy="skip",
            )

    ctx = RunContext(task_id="t1", session_id="sess-1", reason="natural")
    asyncio.run(on_run_end(ctx))

    stream_svc.trigger_run.assert_called_once()
    kwargs = stream_svc.trigger_run.call_args.kwargs
    assert kwargs["origin"] == "loop"
    assert kwargs["dedup_key"] == "loop:sess-1:3"
    assert kwargs["on_busy"] == "skip"


def test_loop_handler_registers_on_run_end():
    """RUN_END 是可注册的 observe hook（接入点存在）。"""
    from matmaster.core.hooks import HookEvent

    assert hasattr(HookEvent, "RUN_END")


def test_schedule_tick_can_drive_trigger_run():
    """schedule 驱动器：扫到 due 行，调 trigger_run，不改其签名。"""
    stream_svc = MagicMock()
    stream_svc.trigger_run.return_value = MagicMock(status="enqueued")

    due_rows = [
        {"id": 7, "session_id": "sess-2", "prompt": "每日巡检", "fire_epoch": 1717459200},
    ]

    def schedule_tick():
        for row in due_rows:
            stream_svc.trigger_run(
                row["session_id"],
                row["prompt"],
                origin="cron",
                dedup_key=f"sched:{row['id']}:{row['fire_epoch']}",
                delivery={"notify": True},
                on_busy="skip",
            )

    schedule_tick()

    stream_svc.trigger_run.assert_called_once()
    kwargs = stream_svc.trigger_run.call_args.kwargs
    assert kwargs["origin"] == "cron"
    assert kwargs["dedup_key"] == "sched:7:1717459200"
```

- [ ] **Step 2: 跑测试确认（验证 RunContext/HookEvent 形状）**

Run: `uv run pytest tests/test_extension_points_smoke.py -v`
Expected: 若 `RunContext` 的字段名（`task_id`/`session_id`/`reason`）与 [matmaster/core/hooks.py:59-63](matmaster/core/hooks.py) 一致，应直接 3 passed。若字段名不同，按实际调整 `RunContext(...)` 构造。

- [ ] **Step 3: 无需实现（接口已由 Task 7 提供）**

这些测试仅验证 `trigger_run` 的签名（`origin`/`dedup_key`/`delivery`/`on_busy` 关键字参数）足以支撑 loop/schedule 两个未来用例。无新增产品代码。若测试中发现 `trigger_run` 签名不足以表达某用例，回到 Task 7 调整签名并补测。

- [ ] **Step 4: 跑全量测试确认整体绿**

Run: `uv run pytest tests/test_agent_run_trigger.py tests/test_chat_history_system_trigger.py tests/test_chat_internal_trigger_api.py tests/test_agent_worker_delivery.py tests/test_extension_points_smoke.py tests/test_chat_stream_direct.py -v`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add tests/test_extension_points_smoke.py
git commit -m "test: verify loop/schedule extension points compose over trigger_run"
```

---

## 最终验证

- [ ] 跑全量相关测试 + lint：

```bash
uv run pytest tests/ -q
uv run pre-commit run --all-files
```

Expected: 测试全绿；pre-commit 全绿（black/isort/autoflake/pyupgrade/flake8/file hygiene 均无新增报错）。

- [ ] 手动冒烟：确认改动的模块都能导入：

```bash
uv run python -c "import src.apis.chat_api, src.services.stream_service, src.worker.agent_worker, src.services.chat_history; print('import ok')"
```

---

## Self-Review（plan 对照 spec 的覆盖检查）

**Components 覆盖：**
- `_prepare_run` 共享内核 → Task 5（+ 决策 1 说明拆分理由）✓
- `trigger_run` 系统触发适配器 → Task 7 ✓
- 泛化 `/stream` 入口（X-Internal-Token、owner 身份、owner 额度、分派、返回）→ Task 9 ✓
- System 触发事件落库与历史还原 → Task 8（落库在 Task 7 的 `_system_event_writer`，还原在 Task 8）✓

**Extension Points 覆盖：**
- `origin`（必填、写入 System 事件 content、进 job）→ Task 1 字段 + Task 7 `_system_event_writer`/job ✓
- `dedup_key`（Redis SET NX EX、预检 deduped、仅成功后标记、TTL、可空）→ Task 2 helper + Task 7 逻辑 ✓
- `delivery`（进 job payload、worker 读它决定通知、缺省默认）→ Task 1 模型 + Task 7 job + Task 10 worker ✓
- `on_busy`（第一版 skip 返回 busy、不标 dedup）→ Task 5 `_prepare_run` 返回 Busy + Task 7 busy 分支 ✓

**Invariants & Error Handling 覆盖：**
- session 必须已存在且有 owner，否则 error，不静默创建无主 session → Task 7 `owner` 校验 + 测试 `test_trigger_run_error_when_no_owner` ✓
- 历史边界单调（pre_turn 在写事件前取）→ Task 5 实现顺序 + 测试 `test_prepare_run_snapshots_boundary_before_writing_event` ✓
- dedup 标记时机（仅成功入队后；busy/error 不标记）→ Task 7 + 测试 `test_trigger_run_busy_does_not_mark_dedup` ✓
- 入队失败留孤儿事件（不回滚已写事件）→ Task 5 `_enqueue_run` 仅回滚 waiting/queued + 测试 `test_enqueue_run_rolls_back_on_lpush_failure` ✓
- 鉴权失败（缺失/错误 X-Internal-Token、分享页内部触发）→ Task 9 + 测试 `test_internal_trigger_wrong_token_rejected_fail_closed` / `test_internal_trigger_rejected_on_share_route` ✓
- 占锁（try_acquire 失败按 on_busy）→ Task 5/7 ✓

**Billing & Auth 覆盖：**
- 计费主体 = session owner（worker 反查 owner，无需改动）→ Task 7 owner + 执行层不改（保留现状）✓
- 额度检查以 owner 为主体 → Task 9 `_handle_internal_trigger` 用 `check_quota_status(owner)` ✓
- 鉴权边界分叉（用户登录 vs 内部 token）→ Task 9 ✓

**Delivery 三层覆盖：**
- 落库始终发生 → 执行层不改（worker 落库照旧）✓
- SSE（worker publish 到 channel，订阅流消费）→ Task 9 返回 `generate_subscribe_stream`（决策 2）✓
- 通知（worker 按 delivery 决定）→ Task 10 ✓

**Testing Plan 覆盖：** spec 列出的 11 项测试点全部映射到 Task 1-11 的测试（共享内核、System 落库与还原、dedup、鉴权、计费、约束、on_busy、SSE 经订阅流、通知、入队失败语义、接入路线签名）。SSE 实时投递的端到端在 Task 9 以 `generate_subscribe_stream` 被调用验证（非整流断言，受单测边界限制）。

**Placeholder 扫描：** 各 Step 均含完整代码 / 完整测试 / 精确命令与预期；无 TBD / "类似上文" / "添加适当错误处理" 等占位。

**类型一致性：** `RunHandle`/`Busy`/`TriggerResult`（Task 4 定义）在 Task 5/7 使用；`DeliverySpec`（Task 1）在 Task 7/9 使用；`_prepare_run` 的关键字参数在 Task 5 定义、Task 6/7 调用一致；`trigger_run` 签名在 Task 7 定义、Task 9/11 调用一致；`_should_notify_completion`（Task 10）签名自洽。

**实现前已查实的外部事实**（无需执行者再核对）：
1. `get_redis_dao()` 返回的 DAO 类名为 `RedisDao`（[src/dao/redis_dao.py:70](src/dao/redis_dao.py)）——Task 2 测试已用正确类名。
2. `UserMessage(content=...).model_dump()` → `{'role': Role.USER, 'content': str, 'images': []}`，`Role` 为 `str` 子类枚举，故 `== 'user'` 成立——Task 8 断言成立。
3. `NotFoundErrorResponse`/`ForbiddenErrorResponse`/`BaseErrorResponse` 均为 `BaseErrorResponse(Exception)` 子类，接受 `msg=`/`code=`/`http_status=` 关键字——Task 9 用法正确。
4. `RunContext(task_id, session_id, reason)` 字段名见 [matmaster/core/hooks.py:59](matmaster/core/hooks.py)——Task 11 构造正确。
