# AskQuestion 迁移到通用 per-request 交互传输底座 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AskQuestion 的"发起交互 + 等待回复"链路从 session 级共享 Redis list 迁移到通用 per-request 交互传输底座（pending registry + per-request reply key + active 索引），AskQuestion 成为底座第一个使用者，对外契约统一为 `interaction_*` 信封 + `interactions/{request_id}/reply` endpoint。

**Architecture:** 三层——(1) 接入层：`AskQuestionTool` 构造/解析自己的 payload；(2) 传输底座层：通用 `InteractionBridge`，对 payload 不透明，只做 `SETNX active → 写 registry → emit 事件 → BLPOP 自己的 reply key → 配对/超时/取消`；(3) Redis DAO 层：per-request key + 两个 Lua 原子函数 + active 守卫。另一侧 API 收回复后用单条 Lua 原子完成"校验 pending + 写 terminal + push reply"。Bohrium submit review（见 `2026-06-17-bohrium-submit-review-design.md`）以后接同一套底座，本次零实现、只预留接入位（kind 分流 + payload 不透明 + active 跨进程仲裁点）。

**Tech Stack:** Python ≥3.10 / FastAPI / Redis（redis-py，同步 DAO + `client.eval()` 内联 Lua）/ Pydantic 事件模型 / pytest（`uv run --extra dev pytest`）。前端 `scimaster-bohr-chat`：React + TypeScript。

---

## 范围与边界（实现者必读）

**测试范围严格 = spec §10 所列，绝不外扩。** 本计划遵循一条硬约束：迁移/瘦身类改动**默认不新增测试**，只迁移或改造既有测试；仅 spec §10 点名的"核心新行为最小直测"才写新测试，且不为通用化铺张。具体落地：

- **纯删除/瘦身步骤**（删 `AskQuestionBridge`、删旧事件类、删共享 list 函数、删旧 endpoint 等）：不配新测试，靠迁移后的既有测试间接覆盖；TDD 五步结构**不**套用在这些步骤上。
- **新行为直测**（spec §10 六项）：per-request 隔离、`answer` 原子性+终态、超时/answer 竞态、stop 唤醒、active 守卫、API 校验（404/409）——这些按 TDD 写，分布在对应任务里。
- **覆盖缺口**（如完整 stale/duplicate 矩阵）先列出、不自动补。

**前端边界。** 前端在另一仓库 `scimaster-bohr-chat`，本计划未核验其源码现状。spec §9 本身把前端定位为"前端阶段细化"。因此 **Task 7 为契约锁定级任务**：给出后端契约的精确边界（endpoint / body / SSE 事件形状 / kind 分流）与 spec §9 的文件清单，前端的逐行实现步骤需在前端仓库内按此契约细化。后端（Task 1–6、8）为代码级、无占位符。

**git 约束。** 本仓库 `CLAUDE.md` 明令：不要向 `docs/` 做任何 git 提交。本计划文档与 spec 都不进 commit；下文各任务的 `git add` 只添加被改的源码与测试文件，**不要** add `docs/`。

**净代码量目标。** 删旧（共享 list + `AskQuestionBridge` + `RedisReplyQueue` + `get_reply_queue` + 旧事件类/endpoint/model）抵消新建（per-request DAO + `InteractionBridge` + 新 endpoint/事件/model），不显著增加。

---

## 文件结构（改动地图）

后端按 spec §11 的实施顺序逐层迁移。每个文件的职责与改动方向：

| 文件 | 改动 | 职责（迁移后） |
|------|------|----------------|
| `src/dao/redis_dao.py` | 删共享 list 三件套 + 评估删 run_active 三件套；新增 per-request CRUD + 2 个 Lua + active 守卫 + 独立 `delete_interaction_run_context` | per-request key 传输原语，保留 run_context / stop 原语 |
| `matmaster/types/events.py` | 删 3 个 `AskQuestion*Event`、加 3 个 `Interaction*Event`、改 `SystemEvent` union | 通用交互事件类型 |
| `matmaster/types/__init__.py` | 换导出 | 类型导出口 |
| `matmaster/integration/event_payloads.py` | 3 个 `ask_question*` 投影分支换为 `interaction_*` | SSE/持久化 public content 投影 |
| `matmaster/integration/interaction_bridge.py` | `AskQuestionBridge` → `InteractionBridge`（通用化 + terminal 保留语义 + active 守卫） | 通用传输底座 |
| `src/services/stream_reply_queue.py` | 删除整个文件（**删除时机延后到 Task 5**，等 `RedisReplyQueue` 两处 import 清完再删，避免中间 commit 断 import — GPT review P2-5） | （`RedisReplyQueue` 退役） |
| `matmaster/tools/builtin/ask_question_tool.py` | 改持 `InteractionBridge`，下沉 payload 构造/解析为薄适配 | AskQuestion 接入层 |
| `matmaster/core/exp.py` | 注入改 `InteractionBridge` | 工具装配 |
| `src/models/chat.py` | `ChatAskQuestionReplyRequest` → `InteractionReplyRequest` | reply 请求体 |
| `src/apis/chat_api.py` | 删 `ask_question_reply`；新增 `interactions/{request_id}/reply`；stop 取消改 per-request；重写 `_submit_interaction_reply` 链路 | reply / stop API |
| `src/services/stream_service.py` | **删 `get_reply_queue`**；`get_run_context` / `publish_reply_event` 保留 | 流服务 |
| `src/services/sessions_service.py` | 不改（`stop_session_run` 保留，靠 run_context 拿 task_id） | session/stop 编排 |
| `src/services/agent_run_service.py` | 建 `InteractionBridge`（传 task_id / invocation_id），删 `RedisReplyQueue` 引用 | worker 注入 |
| `src/worker/agent_worker.py` | cleanup 改 per-request；删 `delete_interaction_reply_list` 调用；保留 run_context、评估删 run_active | worker 生命周期 |
| `scimaster-bohr-chat/...`（另一仓库） | 见 Task 7 契约 | 前端交互渲染 + reply 调用 |

测试文件（迁移既有，按 §10）：`tests/matmaster/types/test_events.py`、`tests/matmaster/integration/test_event_payloads.py`、`tests/matmaster/tools/builtin/test_ask_question_tool.py`、`tests/matmaster/apis/test_interaction_reply_api.py`、`tests/matmaster/services/test_agent_run_stream_interaction.py`、`tests/test_chat_stream_reply_events.py`、`tests/matmaster/core/test_exp.py`。

---

## 命名与常量约定（全计划统一）

后续任务引用的 key 模板、常量、枚举、类型名一律以此为准（避免任务间漂移）：

```python
# redis_dao.py 新增 key 模板
HUMAN_INTERACTION_KEY = "human_interaction:{request_id}"            # hash + TTL：pending registry
INTERACTION_REPLY_KEY = "interaction_reply:{request_id}"           # list + TTL：per-request 回复通道
HUMAN_INTERACTION_ACTIVE_KEY = "human_interaction_active:{session_id}"  # string=request_id + TTL：active 索引

# 保留（改用途：从共享 list 哨兵 → per-request reply key 哨兵）
INTERACTION_CANCEL_VALUE = "__CANCEL__"

# TTL（秒）
INTERACTION_TERMINAL_TTL = 300        # terminal state（answered/timeout/cancelled）保留时长
INTERACTION_REPLY_BUFFER = 60         # reply key / pending registry = timeout + buffer
```

`answer_pending_interaction` Lua 返回码 → 语义：`0=not_found`、`1=not_pending`、`2=ok`。
`finalize_interaction` Lua 返回 `1`（已改 terminal）/`0`（非 pending 或不存在，幂等）。
reply envelope 形状（API 写入 reply key、bridge 解析）：`{"kind": <str>, "request_id": <str>, "payload": <dict>}`。
AskQuestion 的 request payload：`{"questions": [...], "metadata": {...}, "origin": "tool:AskQuestion", "preview_format": "markdown"}`。
AskQuestion 的 reply payload：`{"answers": {...}, "annotations": {...}}`。

---

## Task 1: Redis DAO — per-request 传输原语 + Lua + active 守卫

**Files:**
- Modify: `src/dao/redis_dao.py`（删 line 22-23 部分常量 / 56-57 / 248-291 共享 list 套件；新增 per-request 套件）
- Modify: `src/worker/agent_worker.py`（删 line 372 `delete_interaction_reply_list` 调用）、`src/services/stream_service.py`（删 line 875-876 `delete_interaction_reply_list` 调用）— 删 DAO 方法必须连调用点同 commit，否则运行时 AttributeError（GPT review P1-4）
- Test: `tests/matmaster/services/test_agent_run_stream_interaction.py`（新行为直测追加；该文件已有 Redis 测试设施与 timeout/cancel 用例，作为 fixture 范本）

底座的基石。本任务一次性完成 DAO 层的"删旧 + 新增"，因为它们是同一文件的连贯改动（反碎片化）。

- [ ] **Step 1: 删除共享 list 三件套及其常量/私有 key 函数**

删除以下符号（行号据现状，删除后行号会移动，按符号名定位）：
- 常量 `INTERACTION_REPLY_LIST_KEY = "chat:confirmation_reply:{session_id}"`（line 22）
- 私有函数 `_reply_list_key(session_id)`（line 56-57）
- 方法 `delete_interaction_reply_list(self, session_id)`（line 248-258）
- 方法 `rpush_interaction_reply(self, session_id, value)`（line 260-272）
- 方法 `blpop_interaction_reply(self, session_id, timeout_sec)`（line 274-291）——注意：下文 Step 4 会新增**同名但签名为 `request_id` 的** `blpop_interaction_reply`，本步先删旧 session 签名版。

**保留** `INTERACTION_CANCEL_VALUE = "__CANCEL__"`（line 23）——改用途为 per-request reply key 的取消哨兵。

**同步删除 `delete_interaction_reply_list` 的两处调用点**（GPT review P1-4：删方法必须连调用点同 commit，否则中间状态运行时 AttributeError）：
- `src/worker/agent_worker.py:372` `redis_dao.delete_interaction_reply_list(session_id)` — 整行删除（per-request reply key 由 bridge `finally` 按 request 清理，无 session 级 list 可清；这也覆盖了原 Task 6 Step 2 的该行）。
- `src/services/stream_service.py:875-876` `prepare_send_message` 内 `dao = get_redis_dao()` + `dao.delete_interaction_reply_list(sid)` — 两行删除（新消息发送前无需清 session 级 list；`dao` 局部变量仅此一用，一并删）。

- [ ] **Step 2: 新增 key 模板常量**

在现有 key 模板常量区（line 20-24 附近）追加：

```python
HUMAN_INTERACTION_KEY = "human_interaction:{request_id}"
INTERACTION_REPLY_KEY = "interaction_reply:{request_id}"
HUMAN_INTERACTION_ACTIVE_KEY = "human_interaction_active:{session_id}"
INTERACTION_TERMINAL_TTL = 300
INTERACTION_REPLY_BUFFER = 60
```

在私有 key 函数区（line 48-61 附近）追加：

```python
def _human_interaction_key(request_id: str) -> str:
    return HUMAN_INTERACTION_KEY.format(request_id=request_id)


def _interaction_reply_key(request_id: str) -> str:
    return INTERACTION_REPLY_KEY.format(request_id=request_id)


def _human_interaction_active_key(session_id: str) -> str:
    return HUMAN_INTERACTION_ACTIVE_KEY.format(session_id=session_id)
```

- [ ] **Step 3: 新增 pending registry CRUD（hash）**

在 RedisDao 类内新增（沿用现状 `get_command_client()` 单例做一次性命令）：

```python
def write_pending_interaction(self, request_id: str, record: dict, ttl: int) -> None:
    """写 pending registry（hash + TTL）。record 字段：kind/session_id/task_id/invocation_id/state/expires_at。"""
    client = self.get_command_client()
    if client is None:
        return
    key = _human_interaction_key(request_id)
    try:
        client.hset(key, mapping={k: ("" if v is None else str(v)) for k, v in record.items()})
        client.expire(key, ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_pending_interaction failed: %s", exc)


def read_pending_interaction(self, request_id: str) -> dict | None:
    """读 pending registry；不存在返回 None。"""
    client = self.get_command_client()
    if client is None:
        return None
    try:
        data = client.hgetall(_human_interaction_key(request_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_pending_interaction failed: %s", exc)
        return None
    if not data:
        return None
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in data.items()
    }
```

> 注：`bytes` 解码做了双保险（不假设 `decode_responses`），与现状 DAO 风格一致。

- [ ] **Step 4: 新增 per-request reply 原语**

```python
def blpop_interaction_reply(self, request_id: str, timeout_sec: int) -> str | None:
    """阻塞等待 per-request reply key；超时返回 None。供 worker 在 to_thread 中调用。"""
    client = self.create_client()  # 阻塞命令用独立连接，对齐现状 BLPOP 用法
    if client is None:
        return None
    try:
        result = client.blpop(_interaction_reply_key(request_id), timeout=timeout_sec)
    except Exception as exc:  # noqa: BLE001
        logger.warning("blpop_interaction_reply failed: %s", exc)
        return None
    if result is None:
        return None
    value = result[1]
    return value.decode() if isinstance(value, bytes) else value


def rpush_interaction_cancel(self, request_id: str) -> None:
    """向 per-request reply key 投取消哨兵，唤醒 BLPOP。"""
    client = self.get_command_client()
    if client is None:
        return
    key = _interaction_reply_key(request_id)
    try:
        client.rpush(key, INTERACTION_CANCEL_VALUE)
        client.expire(key, INTERACTION_REPLY_BUFFER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rpush_interaction_cancel failed: %s", exc)


def delete_interaction_reply(self, request_id: str) -> None:
    """cleanup per-request reply key（worker 正常结束路径）。"""
    client = self.get_command_client()
    if client is None:
        return
    try:
        client.delete(_interaction_reply_key(request_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_interaction_reply failed: %s", exc)
```

- [ ] **Step 5: 新增两个 Lua 原子函数**

在模块级定义 Lua 脚本字符串（现状无任何 Lua，这是首次引入；用 `client.eval()` 内联，不预编译，简单）：

```python
# answer：校验 state==pending → 写 answered（短 TTL）+ RPUSH reply（buffer TTL），原子。
# 返回码 0=not_found 1=not_pending 2=ok
_ANSWER_PENDING_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
if redis.call('HGET', KEYS[1], 'state') ~= 'pending' then
  return 1
end
redis.call('HSET', KEYS[1], 'state', 'answered')
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('RPUSH', KEYS[2], ARGV[1])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return 2
"""

# finalize：仅当仍 pending 时改 terminal（timeout/cancelled）+ 短 TTL，幂等。返回 1 改了 / 0 没改。
_FINALIZE_INTERACTION_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
if redis.call('HGET', KEYS[1], 'state') ~= 'pending' then
  return 0
end
redis.call('HSET', KEYS[1], 'state', ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""
```

在 RedisDao 类内新增调用方法：

```python
def answer_pending_interaction(self, request_id: str, envelope: str) -> str:
    """原子：校验 pending + 写 answered + RPUSH reply envelope。返回 'ok'/'not_found'/'not_pending'。"""
    client = self.get_command_client()
    if client is None:
        return "not_found"
    try:
        code = client.eval(
            _ANSWER_PENDING_LUA, 2,
            _human_interaction_key(request_id),
            _interaction_reply_key(request_id),
            envelope, str(INTERACTION_TERMINAL_TTL), str(INTERACTION_REPLY_BUFFER),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer_pending_interaction failed: %s", exc)
        return "not_found"
    return {0: "not_found", 1: "not_pending", 2: "ok"}.get(int(code), "not_found")


def finalize_interaction(self, request_id: str, state: str) -> bool:
    """原子：仅当 pending 时改 terminal=state（timeout/cancelled）。幂等。返回是否本次改成。"""
    client = self.get_command_client()
    if client is None:
        return False
    try:
        changed = client.eval(
            _FINALIZE_INTERACTION_LUA, 1,
            _human_interaction_key(request_id),
            state, str(INTERACTION_TERMINAL_TTL),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_interaction failed: %s", exc)
        return False
    return int(changed) == 1
```

- [ ] **Step 6: 新增 active 守卫（SETNX + compare-and-delete + get）**

```python
# compare-and-delete：只删 value==自己 request_id，防误删下一轮交互的占用
_RELEASE_ACTIVE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

def acquire_active_interaction(self, session_id: str, request_id: str) -> bool:
    """SETNX active 守卫；占用中返回 False。TTL 兜底防泄漏。"""
    client = self.get_command_client()
    if client is None:
        return False
    try:
        ok = client.set(_human_interaction_active_key(session_id), request_id, nx=True, ex=3600)
    except Exception as exc:  # noqa: BLE001
        logger.warning("acquire_active_interaction failed: %s", exc)
        return False
    return bool(ok)


def release_active_interaction(self, session_id: str, request_id: str) -> None:
    """compare-and-delete：仅当当前 active==request_id 时释放。"""
    client = self.get_command_client()
    if client is None:
        return
    try:
        client.eval(_RELEASE_ACTIVE_LUA, 1, _human_interaction_active_key(session_id), request_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("release_active_interaction failed: %s", exc)


def get_active_interaction(self, session_id: str) -> str | None:
    """取当前 active request_id（供 stop 定位）；无则 None。"""
    client = self.get_command_client()
    if client is None:
        return None
    try:
        value = client.get(_human_interaction_active_key(session_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_interaction failed: %s", exc)
        return None
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value
```

- [ ] **Step 7: 写新行为直测 — per-request 隔离 / answer 原子性+终态 / 竞态 / active 守卫**

覆盖 spec §10 第 1/2/3/5 项。复用 `tests/matmaster/services/test_agent_run_stream_interaction.py` 既有的 Redis 测试设施（该文件已连测试 Redis 跑 timeout/cancel），在其中追加：

```python
def test_per_request_reply_isolation(redis_dao):
    """spec §10.1：两个 request 的 reply 互不串台。"""
    redis_dao.write_pending_interaction("aq_A", {"kind": "ask_question", "session_id": "s", "task_id": "t", "invocation_id": "i", "state": "pending", "expires_at": ""}, ttl=60)
    redis_dao.write_pending_interaction("aq_B", {"kind": "ask_question", "session_id": "s", "task_id": "t", "invocation_id": "i", "state": "pending", "expires_at": ""}, ttl=60)
    redis_dao.answer_pending_interaction("aq_A", '{"kind":"ask_question","request_id":"aq_A","payload":{"answers":{"q":"a"}}}')
    # B 的 reply key 不应被 A 的 answer 写入
    assert redis_dao.blpop_interaction_reply("aq_B", timeout_sec=1) is None
    raw_a = redis_dao.blpop_interaction_reply("aq_A", timeout_sec=1)
    assert raw_a is not None and '"aq_A"' in raw_a


def test_answer_is_atomic_and_terminal(redis_dao):
    """spec §10.2：answer 一次完成 state+push；重复/迟到 reply 据 terminal 返回 not_pending。"""
    redis_dao.write_pending_interaction("aq_C", {"kind": "ask_question", "session_id": "s", "task_id": "t", "invocation_id": "i", "state": "pending", "expires_at": ""}, ttl=60)
    assert redis_dao.answer_pending_interaction("aq_C", '{"kind":"ask_question","request_id":"aq_C","payload":{}}') == "ok"
    assert redis_dao.read_pending_interaction("aq_C")["state"] == "answered"
    assert redis_dao.answer_pending_interaction("aq_C", '{"kind":"ask_question","request_id":"aq_C","payload":{}}') == "not_pending"
    assert redis_dao.answer_pending_interaction("aq_missing", "{}") == "not_found"


def test_timeout_finalize_vs_answer_single_winner(redis_dao):
    """spec §10.3：超时 finalize 与 answer 只有一个赢家。"""
    redis_dao.write_pending_interaction("aq_D", {"kind": "ask_question", "session_id": "s", "task_id": "t", "invocation_id": "i", "state": "pending", "expires_at": ""}, ttl=60)
    assert redis_dao.finalize_interaction("aq_D", "timeout") is True
    # finalize 赢了 → answer 必须输（not_pending），且不 push reply
    assert redis_dao.answer_pending_interaction("aq_D", "{}") == "not_pending"
    assert redis_dao.blpop_interaction_reply("aq_D", timeout_sec=1) is None
    assert redis_dao.finalize_interaction("aq_D", "cancelled") is False  # 幂等


def test_active_guard_setnx_and_compare_and_delete(redis_dao):
    """spec §10.5：SETNX 占用中拒新交互；compare-and-delete 不误删下一轮。"""
    assert redis_dao.acquire_active_interaction("sess", "aq_E") is True
    assert redis_dao.acquire_active_interaction("sess", "aq_F") is False  # 占用中拒绝
    assert redis_dao.get_active_interaction("sess") == "aq_E"
    redis_dao.release_active_interaction("sess", "aq_F")  # 非持有者释放 → 不误删
    assert redis_dao.get_active_interaction("sess") == "aq_E"
    redis_dao.release_active_interaction("sess", "aq_E")  # 持有者释放
    assert redis_dao.get_active_interaction("sess") is None
```

> `redis_dao` fixture 沿用该测试文件既有写法。若既有文件用的是其它 fixture 名/构造方式，对齐之，不要另起一套。

- [ ] **Step 8: 跑测试验证**

Run: `uv run --extra dev pytest tests/matmaster/services/test_agent_run_stream_interaction.py -v`
Expected: 新增 4 个用例 PASS；既有用例（含旧 bridge 集成）此时可能因后续任务尚未迁移而 FAIL——记录但本任务只确认新增 DAO 用例绿。

- [ ] **Step 9: Commit**

```bash
git add src/dao/redis_dao.py src/worker/agent_worker.py src/services/stream_service.py tests/matmaster/services/test_agent_run_stream_interaction.py
git commit -m "feat(dao): per-request interaction transport primitives + Lua atomics; drop shared reply list"
```

> 本 commit 含 worker/stream_service 两处 `delete_interaction_reply_list` 调用点的删除（Step 1），与 DAO 方法定义同 commit，保证运行时不破。

---

## Task 2: 通用交互事件类 + union + 导出 + 投影

**Files:**
- Modify: `matmaster/types/events.py`（删 line 206-232 三个事件类；改 **`SystemEvent`（345-362）和 `BusEvent`（364-390+）两个 union**）
- Modify: `matmaster/types/__init__.py`（line 9-11 imports / 104-106 `__all__`）
- Modify: `matmaster/integration/event_payloads.py`（line 370-391 三个投影分支）
- Test: `tests/matmaster/types/test_events.py`、`tests/matmaster/integration/test_event_payloads.py`（迁移既有，不新增）

- [ ] **Step 1: 在 events.py 新增三个通用事件类**

定位现状 `AskQuestionEvent`（line 206-214）所在位置，在其后新增（沿用 `EventBase` 基类，与现状一致）：

```python
class InteractionRequestEvent(EventBase):
    type: Literal["interaction_request"] = "interaction_request"
    kind: str
    request_id: str
    task_id: str
    expires_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class InteractionReplyEvent(EventBase):
    type: Literal["interaction_reply"] = "interaction_reply"
    kind: str
    request_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class InteractionTimeoutEvent(EventBase):
    type: Literal["interaction_timeout"] = "interaction_timeout"
    kind: str
    request_id: str
    reason: str = "timeout"
```

> **`source` 必填（GPT review P0-1）**：`EventBase`（events.py:25）定义 `source: str` 无默认值，三个事件类继承它，构造时**必须传 `source="System"`**（现状 `AskQuestionBridge` 即如此，见 interaction_bridge.py:87/103；漏传 Pydantic 校验失败，第一条 `interaction_request` 就炸）。`EventBase` 只有 `source`/`timestamp`/`spawn_id`，**不含 `session_id`**——事件类不带 session_id 字段（现状 `AskQuestionEvent` 同样不带，session_id 由更上层注入信封），不要在子类声明。

- [ ] **Step 2: 删除三个旧事件类**

删除 `AskQuestionEvent`（line 206-214）、`AskQuestionReplyEvent`（line 217-223）、`AskQuestionTimeoutEvent`（line 226-232）整段定义。

- [ ] **Step 3: 更新 SystemEvent 和 BusEvent 两个 union（GPT review P0-2）**

events.py 有**两个**手写 union 都列了这三个旧事件类，必须一起改，漏一个就会在删旧类后 import 期 NameError：
- `SystemEvent`（line 345-362）：含 `AskQuestionEvent, AskQuestionReplyEvent, AskQuestionTimeoutEvent`（line 347-349）。
- `BusEvent`（line 364-390+）：**又手写一遍**全部事件（不是引用 SystemEvent），同样含这三个（line 378-380）。

两处都把这三行替换为：

```python
        InteractionRequestEvent,
        InteractionReplyEvent,
        InteractionTimeoutEvent,
```

其余成员保持原序不动。

- [ ] **Step 4: 更新 types/__init__.py 导出**

`from .events import (...)` 块（line 9-11）：把 `AskQuestionEvent, AskQuestionReplyEvent, AskQuestionTimeoutEvent` 换成 `InteractionRequestEvent, InteractionReplyEvent, InteractionTimeoutEvent`。
`__all__`（line 104-106）：同样三处字符串替换为 `"InteractionRequestEvent", "InteractionReplyEvent", "InteractionTimeoutEvent"`。

- [ ] **Step 5: 更新 event_payloads.py 投影分支**

把现状三个分支（line 370 `ask_question`、379 `ask_question_reply`、386 `ask_question_timeout`）整体替换为：

```python
if event_type == 'interaction_request':
    return {
        'kind': payload.get('kind'),
        'request_id': payload.get('request_id'),
        'task_id': payload.get('task_id'),
        'expires_at': payload.get('expires_at'),
        'payload': payload.get('payload') or {},
    }

if event_type == 'interaction_reply':
    return {
        'kind': payload.get('kind'),
        'request_id': payload.get('request_id'),
        'payload': payload.get('payload') or {},
    }

if event_type == 'interaction_timeout':
    return {
        'kind': payload.get('kind'),
        'request_id': payload.get('request_id'),
        'reason': payload.get('reason', 'timeout'),
    }
```

> `persistence_handler.py` 的 `_should_persist_type` 是黑名单（`_SKIP_TYPES = {'log_line', 'llm_token'}`），`interaction_*` 天然持久化，**无需改**。

- [ ] **Step 6: 迁移既有事件测试**

`tests/matmaster/types/test_events.py`：把针对 `AskQuestionEvent`/`AskQuestionReplyEvent`/`AskQuestionTimeoutEvent` 的实例化、discriminated union 解析、字段校验用例，改成 `InteractionRequestEvent`/`InteractionReplyEvent`/`InteractionTimeoutEvent`。字段映射：旧 `questions/metadata/origin/preview_format` → 新 `kind/request_id/task_id/expires_at/payload`（request payload 内含 questions 等）；旧 reply `answers/annotations` → 新 reply `kind/request_id/payload`；旧 timeout `questions/reason` → 新 `kind/request_id/reason`。**只改造既有用例，不新增。**

`tests/matmaster/integration/test_event_payloads.py`：把投影测试（现状 ask_question line 643 / reply 660 / timeout 673）改成新 type 与新投影字段。

- [ ] **Step 7: 跑测试验证**

Run: `uv run --extra dev pytest tests/matmaster/types/test_events.py tests/matmaster/integration/test_event_payloads.py -v`
Expected: 迁移后用例 PASS。

- [ ] **Step 8: Commit**

```bash
git add matmaster/types/events.py matmaster/types/__init__.py matmaster/integration/event_payloads.py tests/matmaster/types/test_events.py tests/matmaster/integration/test_event_payloads.py
git commit -m "feat(events): generic interaction_* events replace ask_question_* events"
```

---

## Task 3: InteractionBridge 通用传输底座

**Files:**
- Modify: `matmaster/integration/interaction_bridge.py`（`AskQuestionBridge` line 32-118 → `InteractionBridge`，新增 `InteractionBusyError`）
- Modify: `src/services/agent_run_service.py`（line 54 import / 505-515 唯一构造点）

本任务把 bridge 通用化：保留通用传输部分（asyncio.Lock、emit、to_thread BLPOP、timeout），去掉 AskQuestion 语义（answers/annotations 解析、request_id 前缀），换成 per-request DAO 调用 + active 守卫 + terminal 保留语义。**重命名 `AskQuestionBridge`→`InteractionBridge` 会让其唯一构造点 `agent_run_service.py` 的 import 失效，故本任务一并改造该构造点（原 Task 6 Step 1 上移至此），使本 commit 自洽（GPT review P2-5 深化：重命名与删文件同样会断 import）。** `stream_reply_queue.py` 文件删除留到 Task 5（那里清掉 `stream_service.py:37` 的最后一个 import）。

- [ ] **Step 1: 重写 interaction_bridge.py 为通用 InteractionBridge**

整体替换文件内容为（保留文件顶部既有 import 风格，按需增删）：

```python
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from matmaster.types import InteractionRequestEvent

EventSink = Callable[[Any], Awaitable[None]]
DEFAULT_TIMEOUT_SECONDS = 1800


class InteractionBusyError(RuntimeError):
    """该 session 已有活跃交互占用 active 槽位，拒绝发起新交互。"""


class InteractionBridge:
    """通用 per-request 交互传输底座。对内层 payload 不透明。"""

    def __init__(
        self,
        *,
        session_id: str,
        task_id: str,
        invocation_id: str,
        event_sink: EventSink,
        dao: Any,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id
        self._event_sink = event_sink
        self._dao = dao
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    async def request(
        self,
        *,
        kind: str,
        request_id: str,
        payload: dict,
        timeout_seconds: int | None = None,
    ) -> dict:
        """发起一次交互并阻塞等待回复 payload。
        raise InteractionBusyError / TimeoutError / asyncio.CancelledError。"""
        timeout = self._timeout_seconds if timeout_seconds is None else timeout_seconds
        async with self._lock:
            if not self._dao.acquire_active_interaction(self._session_id, request_id):
                raise InteractionBusyError(
                    f"another interaction is active for session {self._session_id!r}"
                )
            try:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=timeout)
                ).isoformat()
                self._dao.write_pending_interaction(
                    request_id,
                    {
                        "kind": kind,
                        "session_id": self._session_id,
                        "task_id": self._task_id,
                        "invocation_id": self._invocation_id,
                        "state": "pending",
                        "expires_at": expires_at,
                    },
                    ttl=timeout + 60,
                )
                await self._event_sink(
                    InteractionRequestEvent(
                        source="System",
                        kind=kind,
                        request_id=request_id,
                        task_id=self._task_id,
                        expires_at=expires_at,
                        payload=payload,
                    )
                )
                raw = await asyncio.to_thread(
                    self._dao.blpop_interaction_reply, request_id, timeout
                )
                if raw is None:
                    # BLPOP 超时边界（GPT review P1-3）：先原子 finalize 裁决，再让 finally 清理。
                    # finalize 赢 → 确实超时；输了说明窗口内 API 已 answer（或 stop 已 cancel）
                    # 并把消息投进了 reply key，补取它，避免“前端显示已回复 / agent 却抛 timeout”的分裂态。
                    if self._dao.finalize_interaction(request_id, "timeout"):
                        raise TimeoutError(f"interaction {request_id!r} timed out")
                    raw = await asyncio.to_thread(
                        self._dao.blpop_interaction_reply, request_id, 5
                    )
                    if raw is None:
                        raise TimeoutError(f"interaction {request_id!r} timed out")
                if raw == "__CANCEL__":
                    # cancel 哨兵由 stop 链路投递，并已 finalize=cancelled
                    raise asyncio.CancelledError(f"interaction {request_id!r} cancelled")
                envelope = json.loads(raw)
                if (
                    envelope.get("request_id") != request_id
                    or envelope.get("kind") != kind
                ):
                    # 配对校验（GPT review P2-7；spec §2.1/§4 底座负责 request_id+kind 配对）
                    raise RuntimeError(
                        f"interaction envelope mismatch: expected ({kind!r},{request_id!r}) "
                        f"got ({envelope.get('kind')!r},{envelope.get('request_id')!r})"
                    )
                return envelope.get("payload") or {}
            finally:
                self._dao.delete_interaction_reply(request_id)
                self._dao.release_active_interaction(self._session_id, request_id)
```

> 设计要点：(1) registry 的 terminal 保留交给 `finalize_interaction`/`answer_pending_interaction` 的短 TTL，bridge 正常路径只删 reply key + 释放 active（`finally`），**不删 registry**；(2) cancel 路径不在 bridge 内 finalize（stop 链路已 finalize=cancelled，见 Task 5）；(3) timeout 事件不在底座发——由接入层（AskQuestionTool）捕获 `TimeoutError` 后发 `InteractionTimeoutEvent`（payload 不透明原则，底座不知道该发什么 kind 的 timeout）；(4) **超时边界裁决（P1-3）**：BLPOP 返回 None 后先原子 `finalize_interaction(timeout)`，只有 finalize 赢才抛 `TimeoutError`，否则补取窗口内已投递的 answer/cancel 消息——杜绝"API 已 answer 200 但 worker 抛 timeout"的分裂态；(5) **配对校验（P2-7）**：解析 envelope 后校验 `request_id`+`kind` 一致，不匹配即 `raise`（不放回），与旧 bridge 的强配对等价。所有路径的 reply key 删除 + active 释放统一在 `finally`。

- [ ] **Step 2: 改造 agent_run_service 唯一构造点（import + 构造 InteractionBridge）**

`src/services/agent_run_service.py`（line 505-515）现状：

```python
from matmaster.integration.interaction_bridge import AskQuestionBridge
...
bridge = AskQuestionBridge(
    session_id=session_id,
    event_sink=_interaction_event_sink,
    reply_queue=RedisReplyQueue(session_id),
    timeout_seconds=1800,
)
```

替换为（传 `task_id` / `invocation_id` / `dao`，去掉 `reply_queue`）：

```python
from matmaster.integration.interaction_bridge import InteractionBridge
...
bridge = InteractionBridge(
    session_id=session_id,
    task_id=task_id,
    invocation_id=invocation_id,
    event_sink=_interaction_event_sink,
    dao=get_redis_dao(),
    timeout_seconds=1800,
)
```

`_interaction_event_sink`（`await fanout.dispatch(event)`）不变。**删除 `agent_run_service.py:54` 的 `from src.services.stream_reply_queue import RedisReplyQueue`**（此处不再用它；`stream_service.py:37` 的同名 import 留到 Task 5 删）。`task_id` / `invocation_id` 在该构造点作用域内取（与 worker 调 `set_interaction_run_context(session_id, task_id, invocation_id)` 同源；若变量名不同，对齐实际）。

Run: `grep -n "task_id\|invocation_id\|RedisReplyQueue\|AskQuestionBridge" src/services/agent_run_service.py`
Expected: `task_id`/`invocation_id` 在构造点可见；`RedisReplyQueue`/`AskQuestionBridge` 无残留。

- [ ] **Step 3: 跑 import 冒烟**

Run: `uv run python -c "from matmaster.integration.interaction_bridge import InteractionBridge, InteractionBusyError; import src.services.agent_run_service"`
Expected: 无 ImportError（验证重命名 + 构造点改造后该模块 import 干净；`stream_service.py` 此时仍 import `RedisReplyQueue`，文件还在，不受影响）。

- [ ] **Step 4: 写 bridge 竞态补取 + 配对校验直测（GPT review P1-3 / P2-7）**

GPT 指出 Task 1 的竞态测试只覆盖"finalize 先赢"，未覆盖"BLPOP 超时后 answer 在窗口内赢"这条真实边界。补两个 bridge 层单元测（mock `dao`，放 `tests/matmaster/services/test_agent_run_stream_interaction.py`，并把旧 bridge 的 mismatch 测试迁移到此）：

```python
async def test_request_recovers_late_answer_after_blpop_timeout():
    """P1-3：BLPOP 超时 None、finalize 输（answer 已赢）→ bridge 补取 reply 正常返回，不抛 TimeoutError。"""
    dao = _FakeDao()
    dao.acquire_active_interaction = lambda s, r: True
    # 第一次 blpop 超时 None；finalize 返回 False（窗口内已被 answer）；第二次 blpop 拿到 answer envelope
    dao.blpop_interaction_reply = _seq([None, '{"kind":"ask_question","request_id":"aq_x","payload":{"answers":{"q":"a"}}}'])
    dao.finalize_interaction = lambda r, s: False
    bridge = InteractionBridge(session_id="s", task_id="t", invocation_id="i", event_sink=_noop_sink, dao=dao)
    out = await bridge.request(kind="ask_question", request_id="aq_x", payload={"questions": []})
    assert out == {"answers": {"q": "a"}}


async def test_request_rejects_envelope_mismatch():
    """P2-7：reply envelope 的 request_id/kind 不匹配 → RuntimeError（迁移自旧 bridge 的 mismatch 测试）。"""
    dao = _FakeDao()
    dao.acquire_active_interaction = lambda s, r: True
    dao.blpop_interaction_reply = lambda r, t: '{"kind":"ask_question","request_id":"aq_OTHER","payload":{}}'
    bridge = InteractionBridge(session_id="s", task_id="t", invocation_id="i", event_sink=_noop_sink, dao=dao)
    with pytest.raises(RuntimeError, match="mismatch"):
        await bridge.request(kind="ask_question", request_id="aq_x", payload={})
```

`_FakeDao` / `_seq` / `_noop_sink` 按该测试文件既有 fake/mock 风格补最小 helper（其余 dao 方法 no-op）。这两个用例是 spec §10.3 竞态的边界完整化（GPT 点名缺口），不外扩。

- [ ] **Step 5: Commit**

```bash
git add matmaster/integration/interaction_bridge.py src/services/agent_run_service.py tests/matmaster/services/test_agent_run_stream_interaction.py
git commit -m "feat(bridge): generalize AskQuestionBridge into InteractionBridge; rewire agent_run_service"
```

---

## Task 4: AskQuestion kind 适配

**Files:**
- Modify: `matmaster/tools/builtin/ask_question_tool.py`（line 88-159；改持 `InteractionBridge`，下沉 payload 构造/解析）
- Modify: `matmaster/core/exp.py`（line 741-749 注入）
- Test: `tests/matmaster/tools/builtin/test_ask_question_tool.py`（迁移既有，含 timeout/cancel）

- [ ] **Step 1: 改 AskQuestionTool 持有 InteractionBridge + 适配 request/reply payload**

`ResourceClaim(resource="interaction", mode="exclusive")`（line 84-86）**保持不变**（进程内互斥保留，spec §5.5）。`__init__`（line 88-101）的 `bridge` 参数与 `exposed_to_model` 逻辑保持不变（bridge 仍是 None → `exposed_to_model=False`）。

把 `execute_with_context`（line 126-159）改为通过 `bridge.request` 走通用底座、自己构造/解析 payload，并捕获 timeout 发 `InteractionTimeoutEvent`：

```python
async def execute_with_context(
    self,
    arguments: dict[str, Any],
    exec_ctx: ToolExecutionContext | None,
) -> str | ToolResult:
    if self._bridge is None:
        return ToolResult(status="error", content="AskQuestion is not available in this context.")

    normalized_questions = self._normalize_questions(arguments["questions"])
    request_id = f"aq_{uuid.uuid4().hex[:12]}"
    request_payload = {
        "questions": normalized_questions,
        "metadata": arguments.get("metadata") or {},
        "origin": "tool:AskQuestion",
        "preview_format": "markdown",
    }
    try:
        reply_payload = await self._bridge.request(
            kind="ask_question",
            request_id=request_id,
            payload=request_payload,
        )
    except TimeoutError:
        await self._bridge._event_sink(
            InteractionTimeoutEvent(source="System", kind="ask_question", request_id=request_id)
        )
        raise

    answers = reply_payload.get("answers") or {}
    annotations = reply_payload.get("annotations") or {}
    summary = self._render_answer_summary(answers, annotations)
    return ToolResult(
        status="success",
        content=summary,
        payload={"request_id": request_id, "answers": answers, "annotations": annotations},
    )
```

文件顶部 import 增加 `from matmaster.types import InteractionTimeoutEvent`。`_normalize_questions`（line 162-176）、`_render_answer_summary`（line 179-192）**保持不变**——它们正是下沉到接入层的 AskQuestion 专属逻辑。

> 关于 `self._bridge._event_sink`：直连底座的 event_sink 发 timeout 事件略显穿透。若实现时觉得不洁，可在 `InteractionBridge` 暴露一个 `emit(event)` 薄方法替代直接访问 `_event_sink`，二选一即可，不强制。CancelledError 不需要接入层发事件（stop 链路已发取消相关事件）。

- [ ] **Step 2: 改 exp.py 注入 InteractionBridge**

`exp.py` line 741-749 现状构造 `interaction_bridge`（`ctx.request.interaction_bridge if spawn_id is None else None`）后传 `AskQuestionTool(bridge=...)`。该段**结构不变**——`ctx.request.interaction_bridge` 现在承载的是 `InteractionBridge` 实例（由 Task 3 的 `agent_run_service` 构造）。仅当类型注解或 import 显式引用了 `AskQuestionBridge` 时改为 `InteractionBridge`；若只是 `Any`/无注解则无需改。

Run: `grep -n "AskQuestionBridge" matmaster/core/exp.py`
Expected: 无命中（说明 exp.py 未硬引用旧类名）；若有命中则改为 `InteractionBridge`。

- [ ] **Step 3: 迁移 AskQuestionTool 测试**

`tests/matmaster/tools/builtin/test_ask_question_tool.py`：把 mock 的 `bridge.ask(...)` 改为 `bridge.request(kind="ask_question", request_id=..., payload=...)`，返回值从旧 `{request_id, answers, annotations}` TypedDict 改为新的 reply payload `{answers, annotations}`。timeout 用例：`bridge.request` 抛 `TimeoutError` → 断言发了 `InteractionTimeoutEvent`。cancel 用例：抛 `asyncio.CancelledError` → 断言向上传播。**只改造既有用例，不新增。**

- [ ] **Step 4: 跑测试验证**

Run: `uv run --extra dev pytest tests/matmaster/tools/builtin/test_ask_question_tool.py -v`
Expected: 迁移后用例 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/ask_question_tool.py matmaster/core/exp.py tests/matmaster/tools/builtin/test_ask_question_tool.py
git commit -m "feat(ask-question): adapt AskQuestionTool onto InteractionBridge"
```

---

## Task 5: API — 新 reply endpoint + model + stop 取消改造

**Files:**
- Modify: `src/models/chat.py`（line 485-499 `ChatAskQuestionReplyRequest` → `InteractionReplyRequest`）
- Modify: `src/apis/chat_api.py`（line 456-484 stop / 487-517 `_submit_interaction_reply` / 519-555 旧 endpoint）
- Modify: `src/services/stream_service.py`（删 `get_reply_queue` line 885-891 + 删 line 37 `RedisReplyQueue` import）
- Delete: `src/services/stream_reply_queue.py`（此时 `RedisReplyQueue` 两处 import 已清完，安全删文件 — GPT review P2-5）
- Test: `tests/matmaster/apis/test_interaction_reply_api.py`（迁移 + API 校验直测 404/409）

- [ ] **Step 1: 替换 reply 请求 model**

`src/models/chat.py` 把 `ChatAskQuestionReplyRequest`（line 485-499）替换为：

```python
class InteractionReplyRequest(BaseModel):
    """POST /chat/sessions/{session_id}/interactions/{request_id}/reply 的通用回复体。"""

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reply(self) -> "InteractionReplyRequest":
        self.kind = self.kind.strip()
        if not self.kind:
            raise ValueError("kind must not be empty")
        return self
```

> payload 不透明：API 不校验内层 schema（answers/annotations 等由 worker 端 kind 适配解析）。大小限制见 Step 2 endpoint 内。确保文件已 import `Any`、`Field`、`model_validator`（现状 `ChatAskQuestionReplyRequest` 已用 `Field`/`model_validator`，`Any` 按需补 import）。

- [ ] **Step 2: 新增通用 reply endpoint**

`src/apis/chat_api.py`：删除旧 `ask_question_reply` endpoint（line 519-555）整段，新增：

```python
_MAX_REPLY_PAYLOAD_BYTES = 256 * 1024

@router.post(
    "/{session_id}/interactions/{request_id}/reply",
    response_model=BaseResponse,
    summary="提交交互回复",
    description="对 interaction_request 提交回复，Agent 继续执行。",
    operation_id="replyChatSessionInteraction",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
        404: COMMON_ERROR_RESPONSES[404],
        409: COMMON_ERROR_RESPONSES[409],
    },
)
async def interaction_reply(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    request_id: str = Path(..., description="交互请求 ID", examples=["aq_xxx"]),
    req: InteractionReplyRequest = Body(...),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    stream_svc: ChatStreamService = Depends(get_stream_service),
    events_svc: ChatEventsService = Depends(get_events_service),
):
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")

    dao = get_redis_dao()
    record = dao.read_pending_interaction(request_id)
    if record is None:
        raise NotFoundErrorResponse(msg="交互请求不存在或已过期")
    if record.get("session_id") != sid:
        raise NotFoundErrorResponse(msg="交互请求不存在或已过期")
    if record.get("kind") != req.kind:
        raise ConflictErrorResponse(msg="交互类型不匹配")

    if len(json.dumps(req.payload, ensure_ascii=False).encode()) > _MAX_REPLY_PAYLOAD_BYTES:
        raise ConflictErrorResponse(msg="回复内容过大")

    envelope = json.dumps(
        {"kind": req.kind, "request_id": request_id, "payload": req.payload},
        ensure_ascii=False,
    )
    result = dao.answer_pending_interaction(request_id, envelope)
    if result == "not_found":
        raise NotFoundErrorResponse(msg="交互请求不存在或已过期")
    if result == "not_pending":
        raise ConflictErrorResponse(msg="交互已 answered/timeout/cancelled")

    reply_event = {
        "source": "User",
        "type": "interaction_reply",
        "kind": req.kind,
        "request_id": request_id,
        "payload": req.payload,
        "session_id": sid,
        "task_id": record.get("task_id"),
        "invocation_id": record.get("invocation_id"),
    }
    stream_svc.publish_reply_event(sid, reply_event)
    events_svc.add_history_event(sid, reply_event, user_id=user_id)
    return BaseResponse(msg="ok")
```

确保文件已 import：`get_redis_dao`、`NotFoundErrorResponse`、`InteractionReplyRequest`（替换原 `ChatAskQuestionReplyRequest` import）、`json`。`NotFoundErrorResponse` 若不存在则用项目既有的 404 错误响应类（参照 `ForbiddenErrorResponse`/`ConflictErrorResponse` 的定义处）。

- [ ] **Step 3: 改造 stop endpoint 的取消唤醒**

`stop_session`（line 456-484）把现状：

```python
reply_queue = stream_svc.get_reply_queue(sid)
if reply_queue is not None:
    try:
        reply_queue.put_cancel()
    except Exception:
        pass
chat_svc.stop_session_run(sid)
```

替换为（per-request 取消：定位 active → finalize=cancelled → 投哨兵唤醒 BLPOP）：

```python
dao = get_redis_dao()
active_request_id = dao.get_active_interaction(sid)
if active_request_id:
    dao.finalize_interaction(active_request_id, "cancelled")
    dao.rpush_interaction_cancel(active_request_id)
chat_svc.stop_session_run(sid)
```

`stop_session_run` 内部链路（publish stop channel + `get_interaction_run_context` 拿 task_id + `set_stop_requested`）**不变**。

- [ ] **Step 4: 删除 _submit_interaction_reply / get_reply_queue / stream_reply_queue.py**

`_submit_interaction_reply`（chat_api.py line 487-517）现已无调用者（旧 endpoint 删了、stop 改了），删除整个函数。
`src/services/stream_service.py`：删 `get_reply_queue`（line 885-891）+ 删 line 37 `from src.services.stream_reply_queue import RedisReplyQueue`。`get_run_context`（893-897）、`publish_reply_event`（899-903）**保留**。
此时 `RedisReplyQueue` 两处 import 都已清（另一处在 Task 3 删），删文件：

```bash
git rm src/services/stream_reply_queue.py
```

Run: `grep -rn "get_reply_queue\|_submit_interaction_reply\|RedisReplyQueue\|stream_reply_queue" src/`
Expected: 无命中。

- [ ] **Step 5: 迁移 API 测试 + 写 API 校验直测（404/409）**

`tests/matmaster/apis/test_interaction_reply_api.py`：旧用例打 `POST /{sid}/ask_question_reply` + `ChatAskQuestionReplyRequest`，迁移为打 `POST /{sid}/interactions/{request_id}/reply` + `InteractionReplyRequest(kind, payload)`。现状 `test_reply_endpoint_rejects_missing_active_run`（line 118）改造为"registry 不存在 → 404"。追加 spec §10.6 API 校验直测：

```python
def test_reply_404_when_request_not_found(client, ...):
    """spec §10.6：request_id 不存在 → 404。"""
    resp = client.post("/chat/sessions/sess-1/interactions/aq_missing/reply",
                       json={"kind": "ask_question", "payload": {"answers": {}}})
    assert resp.status_code == 404


def test_reply_409_when_kind_mismatch(client, redis_dao, ...):
    """spec §10.6：kind 不匹配 → 409。"""
    redis_dao.write_pending_interaction("aq_K", {"kind": "ask_question", "session_id": "sess-1", "task_id": "t", "invocation_id": "i", "state": "pending", "expires_at": ""}, ttl=60)
    resp = client.post("/chat/sessions/sess-1/interactions/aq_K/reply",
                       json={"kind": "submit_review", "payload": {}})
    assert resp.status_code == 409


def test_reply_409_when_not_pending(client, redis_dao, ...):
    """spec §10.6：非 pending（已 answered/timeout/cancelled）→ 409。"""
    redis_dao.write_pending_interaction("aq_L", {"kind": "ask_question", "session_id": "sess-1", "task_id": "t", "invocation_id": "i", "state": "pending", "expires_at": ""}, ttl=60)
    redis_dao.finalize_interaction("aq_L", "timeout")
    resp = client.post("/chat/sessions/sess-1/interactions/aq_L/reply",
                       json={"kind": "ask_question", "payload": {"answers": {}}})
    assert resp.status_code == 409
```

`client` / 鉴权 mock / `redis_dao` fixture 沿用该测试文件既有写法（现状已有完整鉴权与 queue mock）。

- [ ] **Step 6: 跑测试验证**

Run: `uv run --extra dev pytest tests/matmaster/apis/test_interaction_reply_api.py tests/test_chat_stream_reply_events.py -v`
Expected: 迁移后用例 + 3 个新校验用例 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/models/chat.py src/apis/chat_api.py src/services/stream_service.py tests/matmaster/apis/test_interaction_reply_api.py
git rm src/services/stream_reply_queue.py
git commit -m "feat(api): per-request interactions reply endpoint + per-request stop cancel; drop RedisReplyQueue"
```

---

## Task 6: Worker 注入 + 生命周期迁移

**Files:**
- Modify: `src/worker/agent_worker.py`（line 380-381 set / 416,532 delete；line 372 已在 Task 1 删）
- Modify: `src/dao/redis_dao.py`（评估删 run_active 三件套 + 解耦 `delete_interaction_run_context`）
- Test: `tests/matmaster/services/test_agent_run_stream_interaction.py`（stop 唤醒必测，spec §10.4）

> `agent_run_service.py` 的 bridge 改建已上移至 **Task 3 Step 2**（与 bridge 重命名同 commit 以保 import 完整）；本任务不再碰它。

- [ ] **Step 1: worker cleanup 改 per-request；解耦 run_context 删除**

`src/worker/agent_worker.py`：
- line 372 `delete_interaction_reply_list`：**已在 Task 1 删**（与 DAO 方法定义同 commit），本任务不再处理。
- line 380 `redis_dao.set_interaction_run_active(session_id)`：见 Step 2 评估。
- line 381 `redis_dao.set_interaction_run_context(session_id, task_id, invocation_id or '')`：**保留**（stop 靠它拿 task_id）。
- line 416 / 532 `redis_dao.delete_interaction_run_active(session_id)`：见 Step 2。

- [ ] **Step 2: 评估并删除 run_active 三件套（条件步骤）**

先确认 `is_interaction_run_active` 除已删的 `get_reply_queue` 外无其它消费者：

Run: `grep -rn "is_interaction_run_active\|set_interaction_run_active\|delete_interaction_run_active" src/ matmaster/`

- **若仅剩 worker 的 set(380)/delete(416,532) 自产自销**（无 `is_` 读取者）→ 执行删除：
  - `redis_dao.py`：删 `INTERACTION_RUN_ACTIVE_KEY`、`_run_active_key`、`set_interaction_run_active`、`is_interaction_run_active`，以及 `delete_interaction_run_active`。
  - 因现状 `delete_interaction_run_active`（line 184-197）**联动删了 run_context**，新增独立函数替代：

    ```python
    def delete_interaction_run_context(self, session_id: str) -> None:
        client = self.get_command_client()
        if client is None:
            return
        try:
            client.delete(_run_context_key(session_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_interaction_run_context failed: %s", exc)
    ```
  - worker：删 line 380 的 `set_interaction_run_active`；line 416/532 的 `delete_interaction_run_active(session_id)` 改为 `delete_interaction_run_context(session_id)`。

- **若 `is_interaction_run_active` 还有其它消费者** → 保留整套 run_active（line 380/416/532 维持现状，不删不改）；在本计划末尾"未决项"记录保留原因。

> run_context 的 `get/set/delete` 机制整体保留（spec §8 明确：`stop_session_run` 靠 `get_interaction_run_context` 拿 task_id）。本步只是把"删 context"从"删 active 的副作用"解耦成独立函数。

- [ ] **Step 3: 写 stop 唤醒必测（spec §10.4，后端必测）**

在 `tests/matmaster/services/test_agent_run_stream_interaction.py` 追加端到端唤醒用例（复用既有 Redis + bridge 集成设施）：

```python
async def test_stop_during_ask_question_wakes_blpop_immediately(redis_dao, bridge_factory):
    """spec §10.4：stop → get_active_interaction 定位 → 投哨兵 → BLPOP 立即返回 → CancelledError（不等 1800s）。"""
    bridge = bridge_factory(session_id="sess-w", task_id="t", invocation_id="i")
    request_id = "aq_wake"

    async def fire_stop_after_event():
        # 等 request 写好 active 后模拟 stop endpoint 的取消唤醒
        await _wait_until(lambda: redis_dao.get_active_interaction("sess-w") == request_id)
        redis_dao.finalize_interaction(request_id, "cancelled")
        redis_dao.rpush_interaction_cancel(request_id)

    stopper = asyncio.create_task(fire_stop_after_event())
    with pytest.raises(asyncio.CancelledError):
        # 用 monkeypatch 固定 request_id；timeout 给大值证明是被唤醒而非超时
        await bridge.request(kind="ask_question", request_id=request_id, payload={"questions": []}, timeout_seconds=1800)
    await stopper
    assert redis_dao.read_pending_interaction(request_id)["state"] == "cancelled"
```

`bridge_factory` / `_wait_until` 若既有文件未提供，按该文件既有 fixture 风格补最小 helper（不引入新测试框架）。本用例是 spec §10.4 点名必测，属新行为直测范畴。

- [ ] **Step 4: 跑测试验证**

Run: `uv run --extra dev pytest tests/matmaster/services/test_agent_run_stream_interaction.py -v`
Expected: stop 唤醒用例 + Task 1 的 DAO 用例 + 迁移后的 bridge 集成用例全 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/worker/agent_worker.py src/dao/redis_dao.py tests/matmaster/services/test_agent_run_stream_interaction.py
git commit -m "feat(worker): per-request cleanup; decouple run_context delete; evaluate run_active removal"
```

---

## Task 7: 前端迁移（契约锁定 + 文件清单）

**Repo:** `scimaster-bohr-chat`（另一仓库，本计划未核验其源码现状）

本任务为**契约锁定级**：后端契约已由 Task 1–6 固定，下列契约是前端对接的唯一事实来源；逐行实现步骤需在前端仓库内按现状细化（spec §9 本身标注为"前端阶段细化"）。无兼容期，前后端协调一次切换。

**锁定的后端契约：**

1. **Reply 调用**：
   ```
   POST /chat/sessions/{session_id}/interactions/{request_id}/reply
   body: { "kind": "ask_question", "payload": { "answers": {...}, "annotations": {...} } }
   ```
   - 200 成功；404 request 不存在/session 不匹配；409 kind 不匹配 / 非 pending / payload 过大（>256KiB）。

2. **SSE 事件**（前端 dispatch 只认 3 个 type，按 `kind` 分流渲染）：
   ```jsonc
   { "type": "interaction_request", "kind": "ask_question", "request_id": "aq_xxx",
     "session_id": "...", "task_id": "...", "expires_at": "...",
     "payload": { "questions": [...], "metadata": {...}, "origin": "tool:AskQuestion", "preview_format": "markdown" } }

   { "type": "interaction_reply", "kind": "ask_question", "request_id": "aq_xxx",
     "payload": { "answers": {...}, "annotations": {...} } }

   { "type": "interaction_timeout", "kind": "ask_question", "request_id": "aq_xxx", "reason": "timeout" }
   ```

**spec §9 文件清单与方向：**

- [ ] `src/api/chat-evo-interaction.ts`、`src/api/chat-evo.ts`：reply 调用改新 endpoint + body `{kind, payload}`。
- [ ] `src/pages/matmaster/chat-evo/hooks/evo-sse-handler/dispatch/content-events.ts`、`hooks/evo-sse-handler/types.ts`：SSE 分发改 `interaction_request|reply|timeout` + 按 `kind` 分流。
- [ ] `src/pages/matmaster/chat-evo/utils/interaction.ts`：交互状态逻辑通用化（从 ask_question 专用提升为按 kind 分发）。
- [ ] `src/pages/matmaster/chat-evo/components/AskQuestionWizard.tsx`、`InteractionCard.tsx`：按 `kind` 渲染（`ask_question` 走 wizard）。
- [ ] `tests/chat-evo/*interaction*.test.ts`、`ask-question-tool-result-linkage.test.ts`、`evo-sse-harness.ts`：迁移到新契约（迁移既有，不新增）。
- [ ] 删除：前端 `ask_question` 专用 SSE 处理分支 / 旧 reply 调用。

> 实现入口建议：先在前端仓库 `grep` 旧 type 字面量（`ask_question`、`ask_question_reply`、`ask_question_timeout`）与旧 endpoint 路径（`ask_question_reply`），定位全部触点后按上述契约逐个迁移。

- [ ] **Commit**（前端仓库内）：
```bash
git commit -m "feat(chat-evo): migrate ask_question to generic interaction contract"
```

---

## Task 8: 全量回归 + 半迁移核查

**Files:** 无新增改动，纯验证。

- [ ] **Step 1: 跑迁移涉及的后端测试集**

```bash
uv run --extra dev pytest \
  tests/matmaster/types/test_events.py \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/tools/builtin/test_ask_question_tool.py \
  tests/matmaster/apis/test_interaction_reply_api.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py \
  tests/test_chat_stream_reply_events.py \
  tests/matmaster/core/test_exp.py -v
```
Expected: 全 PASS。

- [ ] **Step 2: 半迁移残留核查（防漏删，spec §10 P2-b 精神）**

```bash
grep -rn "AskQuestionBridge\|RedisReplyQueue\|stream_reply_queue\|ask_question_reply\|delete_interaction_reply_list\|get_reply_queue\|ChatAskQuestionReplyRequest\|AskQuestionEvent\|AskQuestionReplyEvent\|AskQuestionTimeoutEvent" src/ matmaster/ tests/
```
Expected: 无命中（除测试里作为"迁移前后对比注释"的字面量，若有需确认是注释而非活代码）。

- [ ] **Step 3: import 冒烟 + lint**

```bash
uv run python -c "import matmaster.types; import matmaster.integration.interaction_bridge; import src.apis.chat_api"
uv run ruff check src/ matmaster/
```
Expected: 无 ImportError、无 lint 错误。

- [ ] **Step 4: 净代码量自查**

```bash
git diff --stat <迁移起点 commit>..HEAD -- src/ matmaster/
```
Expected: 源码净增量接近零或为负（删旧抵消新建）。若显著为正，回看是否有应删未删。

---

## GPT review 吸收情况（2026-06-18 一轮）

7 条 finding 经核验全部属实、全部已吸收进上文：

| # | 等级 | 问题 | 修复位置 |
|---|------|------|----------|
| 1 | P0 | 事件构造漏 `source` → Pydantic 校验失败 | Task 2 Step 1 注 + Task 3 Step 1 `InteractionRequestEvent(source="System", ...)` + Task 4 Step 1 timeout 事件 |
| 2 | P0 | 只改 `SystemEvent`、漏 `BusEvent`（手写第二份 union） | Task 2 Step 3（两个 union 同改） |
| 3 | P1 | timeout 竞态：finally 先释放后 finalize，answer 钻空 → 分裂态 | Task 3 Step 1（BLPOP None 后先原子 finalize 裁决，输则补取消息）+ Task 3 Step 4 直测 |
| 4 | P1 | `delete_interaction_reply_list` 漏删 `stream_service.prepare_send_message` 调用 | Task 1 Files + Step 1（连两处调用点同 commit 删） |
| 5 | P1/P2 | 中间 commit import-broken（删文件 + 重命名类同样会断） | Task 3（bridge 重命名与 agent_run_service 构造同 commit）+ Task 5（清完两处 import 才删文件） |
| 6 | P2 | reply history 丢 `invocation_id` | Task 5 Step 2（reply_event 从 registry 取 `invocation_id`） |
| 7 | P2 | bridge 丢 `request_id` 强配对校验 | Task 3 Step 1（解析后校验 request_id+kind，不匹配 raise）+ Task 3 Step 4 直测 |

---

## 未决项（缺口先列、不自动补）

按 spec §10 末尾"缺口先列不自动补"与你对测试膨胀的控制，以下不在本计划内实现，留待你定夺：

1. **完整 stale/duplicate reply 矩阵**：本计划测了"非 pending → 409"（Task 5）与"超时后 answer 补取"（Task 3 Step 4），但未穷举 answered-后-timeout、cancelled-后-answer、TTL 过期后回归 not_found 等全组合。
2. **run_active 删除的最终决断**：Task 6 Step 2 是条件步骤，依赖 grep 结果。若发现 `is_interaction_run_active` 有 `get_reply_queue` 以外消费者，则保留整套，此项需你确认是否仍要删。
3. **InteractionBridge 发 timeout 事件的穿透**（Task 4 Step 1 注）：接入层直接访问 `bridge._event_sink` 还是给底座加 `emit()` 薄方法，二选一，未锁定。
4. **payload 大小限制的具体阈值**：API 侧 `_MAX_REPLY_PAYLOAD_BYTES=256KiB`（对齐 6-17 spec §7），questions/answers 条数与单条长度的更细限制未做（spec §10 未要求）。

---

## Self-Review（对照 spec 的覆盖核查）

- **§2.1 目标**：per-request 底座（Task 1）、payload 不透明（Task 1/3 envelope 只搬不解）、AskQuestion 迁入（Task 4）、契约统一（Task 2 事件 + Task 5 endpoint）、前端同步（Task 7）、迁移而非兼容（各任务删旧）——全覆盖。
- **§5.1 key 结构**：registry hash / reply list / active string + TTL + terminal 保留（Task 1 Step 2-6；bridge `finally` 不删 registry，Task 3 Step 1 注）——覆盖。
- **§5.2 状态机 + §6② answer 原子性**：`answer_pending_interaction` / `finalize_interaction` 两个 Lua（Task 1 Step 5），CAS+push 合一杜绝硬挂起——覆盖。
- **§5.3 active 守卫**：SETNX + compare-and-delete + session 维度定位（Task 1 Step 6；stop 用 Task 5 Step 3）——覆盖。
- **§5.5 进程内互斥保留**：`ResourceClaim(interaction, exclusive)` 与 bridge `asyncio.Lock` 原样保留（Task 4 Step 1 / Task 3 Step 1）——覆盖。
- **§5.6 统一 task_id 不引入 run_id**：registry/事件用 task_id，endpoint 无 run/task path 段（Task 1/2/5）——覆盖。
- **§6③ 取消**：stop 改 `get_active_interaction` + finalize + 投哨兵（Task 5 Step 3）；`set_stop_requested` 链路不变（Task 6 保留 run_context）——覆盖。
- **§7 组件接口**：`InteractionBridge`（Task 3，含据 §5.1 补的 `invocation_id` 参数）、DAO 新函数（Task 1）、kind 适配（Task 4）、事件类（Task 2）、`InteractionReplyRequest`（Task 5）——覆盖。
- **§8 删除/保留清单**：删 bridge/queue/共享 list/旧 endpoint/旧事件/旧 model/get_reply_queue（各任务）；保留 run_context；评估删 run_active（Task 6 Step 2）——覆盖。
- **§10 测试**：迁旧测（Task 2/4/5）+ 6 项核心新行为直测（隔离/原子+终态/竞态/active 在 Task 1，API 校验在 Task 5，stop 唤醒在 Task 6）——全覆盖，未外扩。
- **§11 实施顺序**：Task 1-7 对齐 spec §11 的 1-7，Task 8 = §11 第 8 项的回归——一致。
- **类型一致性核查**：`InteractionBridge.request(kind, request_id, payload) -> dict` 在 Task 3 定义、Task 4 调用一致；reply envelope `{kind, request_id, payload}` 在 Task 1 Lua、Task 3 解析、Task 5 写入三处一致；DAO 函数名（`answer_pending_interaction`/`finalize_interaction`/`acquire_active_interaction`/`get_active_interaction`/`blpop_interaction_reply`/`rpush_interaction_cancel`/`delete_interaction_reply`）在 Task 1 定义、Task 3/5/6 调用一致。
- **非目标守边**：未实现 Bohrium submit review（Task 7 契约预留 kind 分流位）、无兼容回放、无重型分布式锁、无 run_id、未改 agent loop/fanout/SSE 机制——守住。
- **GPT review（2026-06-18）**：7 条 finding 全部核验属实并吸收，见"GPT review 吸收情况"表；事件 `source`/`BusEvent`/重命名断 import 等类型与 import 完整性问题均在对应任务闭合，每个 commit 自洽。
