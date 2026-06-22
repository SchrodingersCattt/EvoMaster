# Trigger 继承实际模型 Profile 实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 程序化 `trigger_run` 在未显式传 `model` 时继承同一 session 最近一次父级实际 LLM 输出的 `model_profile`，并把无效的服务端默认 profile `qwen_3_7_max` 修正为真实存在的 `matmaster/DeepSeek-v4-Pro`（一并修复默认链路潜伏的 `KeyError`）。

**Architecture:** 三层由内向外：DAO 新增一条窄查询（取最近一条父级 `response`/`assistant_state` 事件 content 里的 `model_profile`，BYOK 与缺失返回 `None`）→ `ChatEventsService` 封装并把查询失败兜成 `None`（warning，不阻断入队）→ `ChatStreamService.trigger_run` 在显式 `model` 为空时调用 service 填充 `model_val`。外加一处 `config/config.yaml` 默认 profile 修正。Worker、`AgentRunService`、provider factory 不改。

**Tech Stack:** Python ≥3.11、`pymysql` 裸 SQL（DAO 层）、FastAPI service 层、`pytest` + `unittest.mock`（同步测试，`uv run pytest`）。

---

## 背景与数据来源验证（执行前必读）

执行本 plan 前请信任以下已核实的事实，不要怀疑「字段可能不存在」：

1. **持久化路径**：`matmaster/integration/persistence_handler.py:65-77` 先 `payload = event.model_dump(mode='json')`，再 `content = _public_content_for_event(event_type, payload)`，把 `content` 作为 `add_event(...)` 的 content 参数写入 `evo_chat_events.content`（MySQL `JSON` 列）。
2. **content 里确有 `model_profile`/`model_route`**：`matmaster/integration/event_payloads.py` 的 `_response_public_content`（行 97-107，针对 `response`）和 `assistant_state` 分支（行 349-359）都调用 `_copy_nonempty_keys(out, payload, _MODEL_IDENTITY_KEYS)`，其中 `_MODEL_IDENTITY_KEYS = ('model', 'model_profile', 'model_route')`（行 62-66）。因此这两类事件的 content dict 在运行时这些值非空时携带 `model_profile`、`model_route`。值为空时 `_copy_nonempty_keys` 不写该 key，于是 `content.get('model_profile')` 自然得 `None`。
3. **普通 profile**：`build_provider_bundle` 设 `model_profile == model_route == profile_key`（如 `matmaster/qwen3.7-max`，见 `matmaster/providers/llm_factory.py:231`）。
4. **BYOK**：`build_byok_provider_bundle` 设 `model_profile = "byok"`，`model_route = "byok:{credential_id}"` 或 `"byok"`（`matmaster/providers/llm_factory.py:277-278`，`BYOK_PROFILE_KEY = "byok"`）。
5. **父子作用域**：`add_event(..., spawn_id=getattr(event, 'spawn_id', None))`（persistence_handler.py:77）。父级事件 `spawn_id IS NULL`，子 agent 事件 `spawn_id` 非空。查询过滤 `spawn_id IS NULL` 即只取父级。
6. **pymysql 取值**：DAO 用 `DictCursor`，`row['content']` 返回 JSON **字符串**，需 `json.loads`（现有代码 `_row_to_event` 即如此处理）。
7. **默认 profile 失效**：`config/config.yaml` 当前 `agents.general.llm: "qwen_3_7_max"`（带下划线）在 `config/llm_config.yaml` 中无对应 profile key（实际 key 形如 `matmaster/qwen3.7-max`）。`LLMConfig.resolve`（`matmaster/config/llm.py:93-111`）miss 时 `raise KeyError`，因此 `model=None` 落到默认链路时会 `KeyError`。仓库内 `qwen_3_7_max`（下划线）只在 `config/config.yaml:6` 出现一处。`matmaster/DeepSeek-v4-Pro` 是 `config/llm_config.yaml` 真实存在的 profile key。
8. **不按 source 过滤的依据（已评审定案）**：`response` / `assistant_state` 这两个 type 仅由 agent kernel 产生——主 agent 父级事件落库 `source='agent'`（`matmaster/core/agent.py:395` 等创建处 `source="agent"`；fanout 不改、`add_event` 不规范化、persistence 存 `event.source` 原始值）；子 agent 事件 `source='MatMaster:<exp>'` 但 `spawn_id` 非空，已被 `spawn_id IS NULL` 排除。source 的规范化只发生在**读取侧**（`src/services/chat_history.py:469` `normalize_event_source`，把 `'agent'`→`'MatMaster'`），DB 列里**不是** `'MatMaster'`。因此查询**绝不能**写 `source = 'MatMaster'`（会匹配不到任何行、继承静默失效），也无需加 source 条件：当前落库契约下 `type IN ('response','assistant_state') + spawn_id IS NULL` 已唯一锁定主 agent 输出。

---

## File Structure

新增 / 修改的文件及职责：

- `config/config.yaml`（修改一行）：把 `agents.general.llm` 由 `qwen_3_7_max` 改为 `matmaster/DeepSeek-v4-Pro`。
- `src/dao/chat_events_table.py`（新增一个方法）：`get_last_resolved_model_profile(session_id) -> str | None`，纯窄查询，失败 `raise`（与现有 DAO 一致）。
- `src/services/events_service.py`（新增一个方法）：`get_last_resolved_model_profile(session_id) -> str | None`，封装 DAO，`try/except` 兜底记 warning 返回 `None`。
- `src/services/stream_service.py`（`trigger_run` 内加 3 行）：显式 `model` 为空时调用 service 填充 `model_val`。
- `tests/test_default_agent_llm.py`（新建）：默认 profile 配置断言 + resolve 真链路断言（改前 resolve 抛 KeyError）。
- `tests/test_chat_events_table_model_profile.py`（新建）：DAO 查询行为与 SQL 结构断言。
- `tests/test_events_service_model_profile.py`（新建）：service 透传与失败兜底。
- `tests/test_agent_run_trigger.py`（修改）：改 `_make_trigger_service` helper + 4 个继承行为测试。

每个 Task 自成一个可独立提交的逻辑单元。提交顺序遵循运行时依赖：config → DAO → service → trigger。

---

## Task 1: 修正服务端默认模型 profile（config）

把无效的 `qwen_3_7_max` 改为真实 profile key，修复默认链路潜伏的 `KeyError`。这是「无可继承 profile 时落回默认链路」能成立的前置条件。

**Files:**
- Create: `tests/test_default_agent_llm.py`
- Modify: `config/config.yaml:6`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_default_agent_llm.py`：

```python
"""服务端默认 agent 模型 profile 配置 + 解析链路测试。"""

from pathlib import Path

from matmaster.config.loader import load_agents_general_llm, load_llm_config

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
_LLM_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "llm_config.yaml"
)


def test_default_agent_llm_is_deepseek_v4_pro():
    assert load_agents_general_llm(_CONFIG_PATH) == "matmaster/DeepSeek-v4-Pro"


def test_default_agent_llm_resolves_without_keyerror():
    # 真链路：默认 profile key 必须能被 llm_config 解析，否则 model=None
    # 落默认链路时 LLMConfig.resolve(default_key=...) 抛 KeyError（本 plan 要修的潜伏 bug）。
    llm_config = load_llm_config(_LLM_CONFIG_PATH)
    default_llm = load_agents_general_llm(_CONFIG_PATH)
    resolved = llm_config.resolve(default_key=default_llm)
    assert resolved.profile_key == "matmaster/DeepSeek-v4-Pro"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_default_agent_llm.py -v`
Expected: FAIL — `test_default_agent_llm_is_deepseek_v4_pro` 断言失败（读到 `"qwen_3_7_max"`）；`test_default_agent_llm_resolves_without_keyerror` 抛 `KeyError: "LLM profile 'qwen_3_7_max' not found, available: [...]"`（正是要修的默认链路潜伏 bug）。

- [ ] **Step 3: 修改 config**

`config/config.yaml` 顶部 `agents` 段当前为：

```yaml
agents:
  general:
    llm: "qwen_3_7_max"
```

把第 6 行 `llm: "qwen_3_7_max"` 改为：

```yaml
agents:
  general:
    llm: "matmaster/DeepSeek-v4-Pro"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_default_agent_llm.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add config/config.yaml tests/test_default_agent_llm.py
git commit -m "fix: point default agent llm at valid matmaster/DeepSeek-v4-Pro profile"
```

---

## Task 2: DAO 窄查询 `get_last_resolved_model_profile`

读取最近一条父级 `response`/`assistant_state` 事件 content 里的 `model_profile`；BYOK 与缺失返回 `None`，不向更早历史回溯。

**Files:**
- Create: `tests/test_chat_events_table_model_profile.py`
- Modify: `src/dao/chat_events_table.py`（在 `get_last_user_query_event` 方法之后新增）

参考模板（已存在，无需改动，仅供对照）：
- `src/dao/chat_events_table.py:617-632` `get_last_user_query_event`：`spawn_id IS NULL` + `ORDER BY ... DESC LIMIT 1` 的查询骨架。
- `src/dao/chat_events_table.py:17-30` `_row_to_event`：`json.loads(row['content'])` 解析模式。
- `tests/conftest.py:142-167` fixture `chat_events_table_with_mocks`：返回 `(table, cursor)`，`cursor.fetchone.return_value` 喂数据，`cursor.execute.call_args[0]` 取 `(sql, params)`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_chat_events_table_model_profile.py`：

```python
"""ChatEventsTable.get_last_resolved_model_profile 查询行为测试。"""

import json
from typing import Any

from src.dao.chat_events_table import ChatEventsTable


def test_returns_profile_from_response_event(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {
                "content": "回答",
                "model_profile": "matmaster/qwen3.7-max",
                "model_route": "matmaster/qwen3.7-max",
            }
        )
    }
    assert table.get_last_resolved_model_profile("s1") == "matmaster/qwen3.7-max"


def test_returns_profile_from_assistant_state_event(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {
                "state": {"role": "assistant"},
                "model_profile": "global.anthropic.claude-opus-4-6-v1",
                "model_route": "global.anthropic.claude-opus-4-6-v1",
            }
        )
    }
    assert (
        table.get_last_resolved_model_profile("s1")
        == "global.anthropic.claude-opus-4-6-v1"
    )


def test_skips_byok_when_profile_is_byok(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {"content": "x", "model_profile": "byok", "model_route": "byok:cred-1"}
        )
    }
    assert table.get_last_resolved_model_profile("s1") is None


def test_skips_byok_when_route_has_byok_prefix(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    # model_profile 看似普通，但 model_route 标记 byok → 仍跳过
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {
                "content": "x",
                "model_profile": "matmaster/qwen3.7-max",
                "model_route": "byok:cred-9",
            }
        )
    }
    assert table.get_last_resolved_model_profile("s1") is None


def test_returns_none_when_profile_missing(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {"content": json.dumps({"content": "无模型字段"})}
    assert table.get_last_resolved_model_profile("s1") is None


def test_returns_none_when_no_row(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None
    assert table.get_last_resolved_model_profile("s1") is None


def test_query_filters_parent_scope_and_event_types(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None
    table.get_last_resolved_model_profile("sess-x")
    sql, params = cursor.execute.call_args[0]
    assert "spawn_id IS NULL" in sql
    assert "type IN ('response', 'assistant_state')" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "LIMIT 1" in sql
    assert params == ("sess-x",)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_chat_events_table_model_profile.py -v`
Expected: FAIL — `AttributeError: 'ChatEventsTable' object has no attribute 'get_last_resolved_model_profile'`。

- [ ] **Step 3: 实现 DAO 方法**

在 `src/dao/chat_events_table.py` 的 `get_last_user_query_event` 方法之后新增（`json` 已在文件顶部 import，`self.get_connection` / `self.table_name` 已由 `BaseTable` 提供）：

```python
    def get_last_resolved_model_profile(self, session_id: str) -> str | None:
        """返回该会话最近一条父级 LLM 输出事件解析出的 model_profile。

        仅看 spawn_id IS NULL 的 response / assistant_state 事件，按时间倒序取最近一条。
        若该事件是 BYOK（model_profile == 'byok' 或 model_route 以 'byok:' 开头），
        或 model_profile 字段缺失/为空，返回 None（由调用方落回默认模型链路）。
        判别只看这一条，不向更早历史回溯。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT content
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND spawn_id IS NULL
                      AND type IN ('response', 'assistant_state')
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    ''',
                    (session_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                try:
                    content = json.loads(row['content'])
                except (json.JSONDecodeError, TypeError):
                    return None
                if not isinstance(content, dict):
                    return None
                model_route = content.get('model_route') or ''
                if isinstance(model_route, str) and model_route.startswith('byok:'):
                    return None
                profile = content.get('model_profile')
                if not profile or profile == 'byok':
                    return None
                return profile
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_chat_events_table_model_profile.py -v`
Expected: PASS（7 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/dao/chat_events_table.py tests/test_chat_events_table_model_profile.py
git commit -m "feat: add get_last_resolved_model_profile DAO query"
```

---

## Task 3: Service 封装 `get_last_resolved_model_profile`

封装 DAO 调用，并按失败语义把查询失败兜成 `None`（记 warning，不抛，不阻断入队）。

**Files:**
- Create: `tests/test_events_service_model_profile.py`
- Modify: `src/services/events_service.py`（在 `get_last_user_query_event` 方法之后新增；`logger` 已在文件顶部 `logger = logging.getLogger(__name__)` 定义）

参考模板（已存在）：`src/services/events_service.py:116-118` `get_last_user_query_event` 的纯透传风格。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_events_service_model_profile.py`：

```python
"""ChatEventsService.get_last_resolved_model_profile 透传与失败兜底测试。"""

import logging
from unittest.mock import MagicMock

from src.services.events_service import ChatEventsService


def test_delegates_to_dao():
    table = MagicMock()
    table.get_last_resolved_model_profile.return_value = "matmaster/qwen3.7-max"
    svc = ChatEventsService(events_table=table, sessions_service=MagicMock())

    assert svc.get_last_resolved_model_profile("s1") == "matmaster/qwen3.7-max"
    table.get_last_resolved_model_profile.assert_called_once_with("s1")


def test_returns_none_and_warns_on_dao_error(caplog):
    table = MagicMock()
    table.get_last_resolved_model_profile.side_effect = RuntimeError("db down")
    svc = ChatEventsService(events_table=table, sessions_service=MagicMock())

    with caplog.at_level(logging.WARNING):
        result = svc.get_last_resolved_model_profile("s1")

    assert result is None
    assert "get_last_resolved_model_profile" in caplog.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_events_service_model_profile.py -v`
Expected: FAIL — `AttributeError: 'ChatEventsService' object has no attribute 'get_last_resolved_model_profile'`。

- [ ] **Step 3: 实现 service 方法**

在 `src/services/events_service.py` 的 `get_last_user_query_event` 方法之后新增：

```python
    def get_last_resolved_model_profile(self, session_id: str) -> str | None:
        """返回该会话最近一条父级 LLM 输出事件的 model_profile（程序化 trigger 继承用）。

        查询失败时记 warning 并返回 None，不抛出，不阻断调用方（trigger 入队）。
        """
        try:
            return self.table.get_last_resolved_model_profile(session_id)
        except Exception:
            logger.warning(
                'get_last_resolved_model_profile failed session_id=%s',
                session_id,
                exc_info=True,
            )
            return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_events_service_model_profile.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/services/events_service.py tests/test_events_service_model_profile.py
git commit -m "feat: wrap model-profile inheritance query in ChatEventsService"
```

---

## Task 4: `trigger_run` 在 model 为空时继承

在 `trigger_run` 中，当显式 `model` 解析为空时调用 service 填充 `model_val`；显式非空时不调用继承查询。

**Files:**
- Modify: `src/services/stream_service.py`（`trigger_run` 内，当前 `model_val = (model or '').strip() or None` 在第 382 行）
- Modify: `tests/test_agent_run_trigger.py`（改 `_make_trigger_service` helper 行 277-290；新增 4 个测试）

关键事实：
- `trigger_run` 内 `sid = session_id.strip()`（行 367），`self._events_service` 由构造函数注入（`ChatStreamService.__init__`，行 148-155）。
- `model_val` 经 `_prepare_run(..., model=model_val, ...)` 进入 job，`_prepare_run` 把它放进 `job['model']`（stream_service.py:292 `'model': model,`）。
- 测试里 `pushed = fake_redis.lpush_agent_run_job.call_args.args[0]` 即入队的 job dict，`pushed["model"]` 是最终 model 值。
- `_make_trigger_service` 用 `events_service = MagicMock()`，若不设默认值，`get_last_resolved_model_profile` 会返回一个 MagicMock 对象污染 `job['model']`。Step 1 给 helper 设默认 `None`，让基线为「无可继承」。

- [ ] **Step 1: 给 helper 设继承查询默认返回 None**

`tests/test_agent_run_trigger.py` 的 `_make_trigger_service`（行 277-290）当前片段：

```python
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 10
    service = ChatStreamService(
```

在中间插入一行，改为：

```python
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 10
    events_service.get_last_resolved_model_profile.return_value = None
    service = ChatStreamService(
```

- [ ] **Step 2: 写失败测试**

在 `tests/test_agent_run_trigger.py` 末尾追加（复用文件内已有的 `_make_trigger_service` 与 `_trigger_patches`，`MagicMock` / `patch` / `pytest` 已在文件顶部 import）：

```python
@pytest.mark.parametrize("model_kwargs", [{}, {"model": None}, {"model": ""}])
def test_trigger_run_inherits_when_model_blank(model_kwargs):
    # 未传 / model=None / model="" 三种空值都按未传处理，走继承
    service, sessions_service, events_service = _make_trigger_service()
    events_service.get_last_resolved_model_profile.return_value = (
        "matmaster/qwen3.7-max"
    )
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run("s1", "继续分析", origin="loop", **model_kwargs)
    assert res.status == "enqueued"
    events_service.get_last_resolved_model_profile.assert_called_once_with("s1")
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["model"] == "matmaster/qwen3.7-max"


def test_trigger_run_explicit_model_skips_inheritance():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1",
            "继续",
            origin="loop",
            model="global.anthropic.claude-opus-4-6-v1",
        )
    assert res.status == "enqueued"
    events_service.get_last_resolved_model_profile.assert_not_called()
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["model"] == "global.anthropic.claude-opus-4-6-v1"


def test_trigger_run_keeps_none_when_no_inheritable_profile():
    # service 返回 None 涵盖三种情形：BYOK 历史 / model_profile 缺失 / 无可继承历史。
    service, sessions_service, events_service = _make_trigger_service()
    events_service.get_last_resolved_model_profile.return_value = None
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)
    with p1, p2, p3, p4:
        res = service.trigger_run("s1", "继续", origin="loop")
    assert res.status == "enqueued"
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["model"] is None
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_agent_run_trigger.py -k "inherits or explicit_model or keeps_none" -v`
Expected: FAIL — `test_trigger_run_inherits_when_model_blank`（3 个参数化用例）失败（`get_last_resolved_model_profile` 未被调用、`pushed["model"]` 为 `None` 而非 `"matmaster/qwen3.7-max"`）。`explicit` 与 `keeps_none` 此时可能已通过——它们要等 Step 4 才有完整意义，无需在此纠结。

- [ ] **Step 4: 实现 `trigger_run` 继承逻辑**

`src/services/stream_service.py` 的 `trigger_run` 内当前片段（约第 381-383 行）：

```python
        resolved_mode = self._resolve_mode(mode)
        model_val = (model or '').strip() or None
        delivery_payload = delivery.model_dump() if delivery is not None else None
```

改为（在 `model_val` 计算后插入继承）：

```python
        resolved_mode = self._resolve_mode(mode)
        model_val = (model or '').strip() or None
        if model_val is None:
            model_val = self._events_service.get_last_resolved_model_profile(sid)
        delivery_payload = delivery.model_dump() if delivery is not None else None
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_agent_run_trigger.py -v`
Expected: PASS（全文件通过，含 4 个新测试与原有 trigger 测试）。

- [ ] **Step 6: Commit**

```bash
git add src/services/stream_service.py tests/test_agent_run_trigger.py
git commit -m "feat: inherit last resolved model profile in programmatic trigger_run"
```

---

## 最终验证

跑完四个 Task 后整体验证一次：

Run:
```bash
uv run pytest tests/test_default_agent_llm.py tests/test_chat_events_table_model_profile.py tests/test_events_service_model_profile.py tests/test_agent_run_trigger.py -v
```
Expected: 全部 PASS。

---

## Self-Review（plan 作者已核对，执行者可对照）

**1. Spec coverage：**

| Spec 要求 | 落点 |
| --- | --- |
| 目标①显式非空 model 不被继承覆盖 | Task 4 `test_trigger_run_explicit_model_skips_inheritance` |
| 目标②未传 / None / 空串继承最近父级 model_profile | Task 4 `..._inherits_when_model_blank`（参数化 omitted/`None`/`""`）；Task 2 response/assistant_state 两类事件 |
| 目标③无可继承时保持默认链路（model=None） | Task 4 `..._keeps_none_when_no_inheritable_profile` |
| 目标④默认 profile 改 DeepSeek + 修 KeyError | Task 1（`..._is_deepseek_v4_pro` 测配置值 + `..._resolves_without_keyerror` 走真解析链路，改前 `resolve(default_key=...)` 抛 KeyError） |
| 目标⑤不加 alias/兼容/迁移 | 全程未引入任何 alias 映射或兜底 |
| 模型选择规则①②③ | Task 4 三个分支测试 |
| 可继承只接受普通 profile，BYOK 直接 None，不回溯 | Task 2 `..._skips_byok_when_profile_is_byok` / `..._skips_byok_when_route_has_byok_prefix`（单条 LIMIT 1，不回溯） |
| 数据来源：spawn_id IS NULL / type IN(response,assistant_state) / created_at DESC,id DESC / 取 model_profile（**不按 source 过滤**，依据见背景⑧——落库 source 是 `'agent'` 非 `'MatMaster'`） | Task 2 `..._query_filters_parent_scope_and_event_types` |
| 失败语义：查询失败 warning 返回 None 不阻断 | Task 3 `..._returns_none_and_warns_on_dao_error` |
| 测试⑦默认模型配置为 matmaster/DeepSeek-v4-Pro | Task 1 |

spec「测试」第 4/5/6 条（BYOK / 字段缺失 / 无历史 → 队列 job model 保持 None）在 DAO 层（Task 2 各自返回 None）与 trigger 层（Task 4 `keeps_none`，service 返回 None → `job['model']` 为 None）组合覆盖。无遗漏需求。

**2. Placeholder scan：** 无 TBD / “按需补充” / “类似上文” 等占位；每个改代码的 step 都给出完整可运行代码与精确命令、预期。

**3. Type consistency：** 方法名 `get_last_resolved_model_profile`、签名 `(session_id: str) -> str | None`、job 字段 `model`、BYOK 判别（`model_profile == 'byok'` 或 `model_route` 以 `'byok:'` 开头）在 DAO / service / 测试三处一致。
