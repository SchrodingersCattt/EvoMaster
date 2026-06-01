# B 类兼容残留清理 — 设计

- 日期: 2026-06-01
- 分支: refactor/context
- 状态: 设计已确认，待实施

## 背景与动机

`refactor/context` 重构的"读侧"尚未收口，主代码里残留若干**内联兼容/兜底/迁移**逻辑，违反项目原则（偏好迁移而非兼容，迁移走外部脚本/手动，严禁主代码内联自动兜底）。本次清理这些"B 类"残留。

项目处于开发阶段、无运行中的设备与用户，因此**存量旧会话数据可直接丢弃**，不需要兼容读取，也不需要外部迁移脚本。

## 范围与关键决策

| 决策 | 取值 |
|------|------|
| 纳入项 | #1 turn_input 旧键、#2 history_restore legacy、#3 events 旧 type、#5 config/llm 兼容、#6 config/loader docstring |
| 排除项 | #4 event_payloads 前端兼容（依赖前端 `scimaster-bohr-chat` 迁移，单独处理） |
| 旧会话数据 | 直接丢弃，删兼容读取，不留内联兜底 |
| #2 中 v1 checkpoint 损坏（`covered_until=null`）的处理 | **显式 `raise`**（数据损坏应暴露，不静默兜底） |
| 提交粒度 | **合并为单一提交** |

## 核心判断：清理必须 matmaster + src 对称

清理跨 `matmaster/`（核心库）与 `src/`（服务层），二者都在本 repo，必须同步改：

- `#2` 的 `legacy_restore` 是 `ModelHistoryRestorer` 的**构造参数**，matmaster 删除后 `src` 不同步删注入会 `TypeError`——强制对称。
- `#1` 的旧键由 `src` 写、matmaster 读；`#3` 的旧 type 由 matmaster 定义、`src` 消费。

"只清 matmaster 留 src 尾巴"既不成立（#2），也违反"不留兜底"原则。故采用对称清理。

## 逐项清理设计

### #1 turn_input 旧键 `pre_query_scope_event_id`

旧键的写入方在 `src`、读取方在 matmaster，一并删除。

| 文件 | 改动 |
|------|------|
| `matmaster/context/sources/turn_input.py:133-136` | `from_payload` 删 `pre_query_scope_event_id` 回退及其注释（129-131），改为只读 `pre_turn_history_event_id` |
| `src/services/stream_service.py:700-714` | 删 `legacy_current_input_payload` 构造块（含注释） |
| `src/services/stream_service.py:725` | 删 job dict 中的 `'current_input_context': legacy_current_input_payload` 字段 |

当前 worker（`src/worker/agent_worker.py:333`）读新 `turn_input` 字段，不读 `current_input_context`，不受影响。

### #2 history_restore legacy（精准切，保留 hybrid 正常路径）

**`matmaster/context/history_restore.py`**

- 删 `LegacyRestore` 类型别名（16）、`legacy_restore` 构造参数、`_legacy_restore` 字段。
- `restore()`（47-79）两处 `return self._legacy_restore(...)` 改写：
  - 第 56-58 行（无 v1 checkpoint 且无 `user_turn_context` 事件 = 纯旧会话）→ `return []`（丢弃）。
  - 第 69-73 行（v1 checkpoint 但 `covered_until_event_id` 为 `null` = 数据损坏）→ **`raise`**（`ValueError` 或项目既有恢复异常，plan 阶段定），保留告警日志。
- `_restore_v1` / `_event_to_v1_compatible_event`：
  - 删 hybrid 的 `covered_invocations` 收集（111-117）。
  - 删 `_event_to_v1_compatible_event` 中 `source == "User" and etype == "query"` 整段（165-171，真·旧 query 事件处理）。
  - 删该函数 `hybrid_mode` / `covered_invocations` 参数（删 query 分支后不再被任何分支使用），同步简化 `_restore_v1` 的调用处。
  - 删 etype 白名单里的 `"finish"`（180）。
- **保留（非 legacy，勿删）**：`hybrid_mode`（无 checkpoint 的新会话恢复）、`user_turn_context` → `query` 的内存转换（146-163，复用 `events_to_messages`）、白名单外丢弃 `context_compaction`（190，`hooks.py:42` 在用的钩子名）。

**`src/services/model_history_restore_service.py`**

- 删 `legacy_restore` callback（120-126）。
- 删 `_restore_legacy_untrimmed`（147+）及其仅服务于 legacy 的依赖。
- 删 `ModelHistoryRestorer(...)` 调用中的 `legacy_restore=` 实参（139）。
- 保留 `has_user_turn_context`（用于区分"旧会话 → `[]`"与"新会话 → hybrid"）。

清理后 `restore()` 形态：

```python
def restore(self, session_id, *, spawn_id=None):
    checkpoint = self._get_latest_checkpoint(session_id, spawn_id)
    if not self._is_v1_checkpoint(checkpoint):
        if not self._has_user_turn_context(session_id, spawn_id):
            return []                                  # 旧会话丢弃
        return self._restore_v1(session_id, spawn_id, checkpoint=None)  # hybrid
    content = checkpoint["content"]
    if content.get("covered_until_event_id") is None:
        logger.warning(...)
        raise ValueError("history_checkpoint.v1 缺少 covered_until_event_id")  # 损坏暴露
    return self._restore_v1(session_id, spawn_id, checkpoint=checkpoint)
```

### #3 events 旧 type `finish` / `end`

| 文件 | 改动 |
|------|------|
| `matmaster/types/events.py:125` | `RunResultEvent.type` → `Literal["run_result"]`，删 legacy docstring（121-123） |
| `matmaster/types/events.py:258` | `StreamClosedEvent.type` → `Literal["stream_closed"]`，删 legacy docstring（254-255） |
| `src/services/stream_sse_filter.py:132,142` | `{'run_result', 'finish'}` → `{'run_result'}` |
| `src/services/chat_history.py:574` | `('run_result', 'finish')` → `('run_result',)` |
| `src/services/stream_service.py:409,803` | `{'stream_closed', 'end'}` → `{'stream_closed'}` |

注：`agent_llm_stream.py:210` 的 `"end"` 是 `stream_state`（非 event type），不动。

### #5 config/llm 兼容层

- `resolve_profile`（`config/llm.py:292-312`）：删整个方法。生产代码全部使用 `resolve_route`；唯一调用方是 `tests/matmaster/config/test_llm.py:326-350`，连带删这些测试。
- `_normalize_legacy_or_explicit_schema`（`config/llm.py:215-228`）：删。生产输入全为 profiles 格式（`config/llm_config.yaml` 已确认；`loader.py:13` 的 `ConfigManager` 仅 docstring 示例，无实际调用）。连带修改/删除 `test_llm.py`、`test_loader.py` 中针对扁平 legacy 格式的用例。

### #6 config/loader docstring

`config/loader.py:3-14` 删 evomaster `ConfigManager` "共存"描述（3-5、12-13），改为纯 matmaster 独立入口说明。无代码改动。

## 排除项与可接受的暂时不一致

`#4`（`integration/event_payloads.py:84,310` 的 `_FRONTEND_COMPAT_PASSTHROUGH = {'run_result', 'finish'}`）**不动**，留待前端 `scimaster-bohr-chat` 迁移到从 `content` 读 `final_content`/`status` 后一并清理。

由此产生一个**可接受的暂时不一致**：清理 `#3` 后后端不再产生 `finish` 事件，但 `event_payloads` 仍"识别"它（死分支）。在该处补一行注释点明此状态，等 `#4` 阶段删除。

## 执行顺序（单一提交内，按风险低→高编辑）

`#6` → `#5 resolve_profile` → `#5 _normalize` → `#1` → `#3` → `#2`

每完成一项跑相关测试，全部通过后合并为一个提交。

## 验证

- 定向测试：`tests/matmaster/context/`（turn_input、history_restore）、`tests/matmaster/types/test_events.py`、`tests/matmaster/config/`（llm、loader）、`tests/matmaster/services/`（model_history_restore_service、stream_sse_filter、chat_history）。
- 全量 `pytest`。
- 残留 grep 必须为空：`pre_query_scope_event_id`、`_legacy_restore` / `LegacyRestore`、`resolve_profile`、`_normalize_legacy_or_explicit_schema`、events 中的 `"finish"` / `"end"` Literal、src 中消费 `'finish'` / `'end'` 的判断。

## 风险与回滚

- **误删 hybrid 正常逻辑**：通过明确"保留 `hybrid_mode` / `user_turn_context`→`query` 转换 / `context_compaction`"规避；`test_history_restore.py` 的 hybrid/v1 用例须保持通过。
- **src 消费方漏改**：靠上面的残留 grep 兜底验证。
- **回滚**：单一提交，`git revert` 该 commit 即可整体回退。
