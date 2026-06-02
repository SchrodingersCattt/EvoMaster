# B 类兼容残留清理 — 设计

- 日期: 2026-06-01
- 分支: refactor/context
- 状态: 设计已确认（含 review 修订），待实施

## 背景与动机

`refactor/context` 重构的"读侧"尚未收口，主代码里残留若干**内联兼容/兜底/迁移**逻辑，违反项目原则（偏好迁移而非兼容，迁移走外部脚本/手动，严禁主代码内联自动兜底）。本次清理这些"B 类"残留。

项目处于开发阶段、无运行中的设备与用户，因此**存量旧会话数据可直接丢弃**，不需要兼容读取，也不需要外部迁移脚本。

## 范围与关键决策

| 决策 | 取值 |
|------|------|
| 纳入项 | #1 turn_input 旧键、#2 history_restore legacy、#3 events 旧 type、#5 config/llm 兼容、#6 config/loader docstring |
| 排除项 | #4 event_payloads 前端兼容（依赖前端 `scimaster-bohr-chat` 迁移，单独处理） |
| 旧会话数据 | 直接丢弃，删兼容读取，不留内联兜底 |
| #2 损坏 checkpoint 处理 | **null covered_until → 专用异常并向上暴露（raise）**；malformed base_messages 仍**降级尝试更老的有效 v1 checkpoint**；所有可恢复 v1 checkpoint 耗尽 → 另一类 restore 失败异常 |
| 提交粒度 | **合并为单一提交** |

## 核心判断：清理必须 matmaster + src 对称

清理跨 `matmaster/`（核心库）与 `src/`（服务层），二者都在本 repo，必须同步改：

- `#2` 的 `legacy_restore` 是 `ModelHistoryRestorer` 的**构造参数**，matmaster 删除后 `src` 不同步删注入会 `TypeError`——强制对称。
- `#1` 的旧键由 `src` 写、matmaster 读；`#3` 的旧 type 由 matmaster 定义、`src` 消费。

"只清 matmaster 留 src 尾巴"既不成立（#2），也违反"不留兜底"原则。故采用对称清理。

## 逐项清理设计

### #1 turn_input 旧键 `pre_query_scope_event_id`（写、读、worker 兜底三处）

| 文件 | 改动 |
|------|------|
| `matmaster/context/sources/turn_input.py:133-136` | `from_payload` 删 `pre_query_scope_event_id` 回退及其注释（129-131），改为只读 `pre_turn_history_event_id` |
| `src/services/stream_service.py:700-714` | 删 `legacy_current_input_payload` 构造块（含注释） |
| `src/services/stream_service.py:725` | 删 job dict 中的 `'current_input_context': legacy_current_input_payload` 字段 |
| `src/worker/agent_worker.py:333-335` | **读侧兜底**：`payload.get('turn_input') or payload.get('current_input_context')` → 只 `payload.get('turn_input')` |

三处一并删，否则写侧删了、worker 读侧兜底仍留在主代码，正违反本 spec 原则。

### #2 history_restore legacy（core 精准切 + 服务层控制流重写）

#### (a) core：`matmaster/context/history_restore.py`

- 删 `LegacyRestore` 类型别名（16）、`legacy_restore` 构造参数、`_legacy_restore` 字段。
- 新增专用异常（暂名 `HistoryCheckpointCorruptedError`，位置 plan 定），仅用于 null covered_until；可恢复 checkpoint 全部耗尽使用另一类异常（暂名 `HistoryRestoreFailedError`），不要混用。
- `restore()`（47-79）两处 `return self._legacy_restore(...)` 改写：
  - 第 56-58 行（无 v1 checkpoint 且无 `user_turn_context` 事件 = 纯旧会话）→ `return []`（丢弃）。
  - 第 69-73 行（v1 checkpoint 但 `covered_until_event_id` 为 `null` = 数据损坏）→ `raise HistoryCheckpointCorruptedError(...)`，保留告警日志。
- `_restore_v1` / `_event_to_v1_compatible_event`：
  - 删 `covered_invocations` 的旧语义（111-117）：不再为了保留 pre-UTC raw legacy turn 而收集 invocation。
  - **hybrid 过滤重定义**：无 checkpoint 但已有 `user_turn_context` 时，只保留由 `user_turn_context` 锚定的新 turn；旧 raw `User/query` 及其后续 response/tool tail 必须整段丢弃，避免只删 user 而留下 orphan assistant/tool 历史。
  - 实现方式建议：在 `_restore_v1` 顺序扫描 events 时维护 `hybrid_turn_active`（或等价状态）。遇到 `user_turn_context` → 转成 `User/query` 并置 active；遇到 raw `source=="User" and type=="query"` → 跳过并置 inactive；`response` / `run_result` / `assistant_state` / `tool_call` / `tool_result` 仅在非 hybrid 或 active 时进入 compatible tail。
  - `tool_result` 的 `_normalize_tool_result_event` 保留，但在 hybrid 模式下同样受 active gate 约束。
  - 删 `_event_to_v1_compatible_event` 中 legacy raw `User/query` 返回原事件的分支（165-171）；如保留 helper 参数，也只能用于 hybrid active gating，不能继续表达旧 query 兼容。
  - 删 etype 白名单里的 `"finish"`（180）。
- **保留（非 legacy，勿删）**：`hybrid_mode`（无 checkpoint 的新会话恢复，但只恢复 UTC 锚定的新 turn）、`user_turn_context` → `query` 的内存转换（146-163，复用 `events_to_messages`）、白名单外丢弃 `context_compaction`（190，`hooks.py:42` 在用的钩子名）。

#### (b) 服务层：`src/services/model_history_restore_service.py`（控制流是本项的核心）

`restore_history`（30-87）现状：对每个 v1 checkpoint `try … except Exception: continue` 试更老的，全失败再 `_restore_legacy_untrimmed`（legacy raw 恢复）。这会**吞掉 core 的 raise**，且删 `_restore_legacy_untrimmed` 会让第 70-78 行悬空。重写为：

```python
v1_checkpoints = self._v1_checkpoints(checkpoints)
for ckpt in v1_checkpoints:
    if content_not_dict(ckpt):
        continue                                   # 跳过（保留）
    try:
        return trim(self._delegate_v1_restore(checkpoint=ckpt))
    except HistoryCheckpointCorruptedError:
        raise                                      # null boundary：暴露，不试更老、不兜底
    except Exception:
        continue                                   # malformed base_messages：降级试更老 valid（保留 tail:192/227）
if v1_checkpoints:
    raise HistoryRestoreFailedError(...)           # 全部 v1 因可恢复错误耗尽 → 暴露（删 _restore_legacy_untrimmed）
return trim(self._delegate_v1_restore(checkpoint=None))   # 无 v1 checkpoint → hybrid（core 在无 utc 时返回 []）
```

- 删 `legacy_restore` callback（120-126）、`_restore_legacy_untrimmed`（147+）及其仅服务于 legacy 的依赖、`ModelHistoryRestorer(...)` 的 `legacy_restore=` 实参（139）。
- 区分异常成立的依据：`tail.py:192` 的 `_invalid_checkpoint`（base_messages 以 `AssistantMessage` 开头）是 **malformed**，`covered_until` 为有效 int——与 null boundary 是不同异常，故"null boundary 上抛"与"malformed 试更老"互不冲突。
- 保留 `has_user_turn_context`（用于区分"旧会话 → `[]`"与"新会话 → hybrid"）。

#### (c) 测试处置矩阵

| 测试 | 旧语义 | 新处置 |
|------|--------|--------|
| `tests/matmaster/context/test_history_restore.py:95` 区域 | 构造 `ModelHistoryRestorer` 传 `legacy_restore` | 删 `legacy_restore` 构造参，相应改造 |
| `tests/matmaster/context/test_history_restore.py:136` | hybrid 保留 pre-UTC raw query/response | 改为旧 raw turn 整段丢弃，只保留 UTC 锚定 turn |
| `tests/matmaster/context/test_history_restore.py:246` | null covered_until → 回退 legacy | 改为期望 `raise` |
| `tests/matmaster/services/test_model_history_restore_service.py:282` | 无 checkpoint 无 utc → legacy | 改为期望 `[]` |
| `tests/matmaster/services/test_model_history_restore_service.py:529` | v1 + null boundary → legacy | 改为期望 `raise` |
| `tests/matmaster/services/test_model_history_restore_service.py:552` | hybrid 保留 pre-phase1 raw query/response | 改为旧 raw turn 整段丢弃，防止 orphan assistant |
| `tests/matmaster/services/test_model_history_restore_service.py:599` | hybrid 保留无 invocation 的 old query | 改为期望丢弃 |
| `tests/matmaster/services/test_model_history_restore_service_tail.py:192` | 最新 malformed，有更老 valid → 返回更老 | **保留通过**（malformed 仍试更老） |
| `tests/matmaster/services/test_model_history_restore_service_tail.py:227` | 跳过坏 system checkpoint 用更老 valid | **保留通过** |
| `tests/matmaster/services/test_model_history_restore_service_tail.py:255` | 无 checkpoint、无 utc → 从 raw query 事件恢复 | 改为期望 `[]`（或删除） |
| `tests/matmaster/integration/test_history_checkpoint_recovery.py:469` | hybrid mixed session 保留 pre-Phase1 raw query | 改为旧 raw turn 整段丢弃，只断言 UTC turn |
| `tests/matmaster/integration/test_e2e_mat_master.py:386` | raw `User/query` + legacy `finish` 被恢复到 LLM history | 改成 `user_turn_context` + `run_result` fixture，或改为断言旧 raw history 不再进入 LLM |

### #3 events 旧 type `finish` / `end`

| 文件 | 改动 |
|------|------|
| `matmaster/types/events.py:125` | `RunResultEvent.type` → `Literal["run_result"]`，删 legacy docstring（121-123） |
| `matmaster/types/events.py:258` | `StreamClosedEvent.type` → `Literal["stream_closed"]`，删 legacy docstring（254-255） |
| `src/services/stream_sse_filter.py:132,142` | `{'run_result', 'finish'}` → `{'run_result'}` |
| `src/services/chat_history.py:574` | `('run_result', 'finish')` → `('run_result',)` |
| `src/services/chat_history.py:410` | docstring 删 `run_result\|finish` 兼容兜底描述，改为只 `run_result` |
| `src/services/stream_service.py:409,803` | `{'stream_closed', 'end'}` → `{'stream_closed'}` |
| `tests/matmaster/integration/test_events_to_messages.py:194` | `test_legacy_finish_events_still_map_to_assistant_messages` → 删除或改为断言 finish 不再映射 |

注：`agent_llm_stream.py:210` 等处的 `"end"` 是 `stream_state`（非 event type），不动。

### #5 config/llm 兼容层

- `matmaster/config/llm.py:292-312` `resolve_profile`：删整个方法。生产代码全部使用 `resolve_route`；唯一调用方是 `tests/matmaster/config/test_llm.py:326-350`，连带删这些测试。
- `matmaster/config/llm.py:215-228` `_normalize_legacy_or_explicit_schema`：删。生产输入全为 profiles 格式（`config/llm_config.yaml` 已确认；`loader.py:13` 的 `ConfigManager` 仅 docstring 示例，无实际调用）。连带修改/删除 `tests/matmaster/config/test_llm.py`、`test_loader.py` 中针对扁平 legacy 格式的用例。

### #6 config/loader docstring（纯注释）

`matmaster/config/loader.py:3-14` 删 evomaster `ConfigManager` "共存"描述（3-5、12-13），改为纯 matmaster 独立入口说明。无代码改动。

## 排除项与可接受的暂时不一致

`#4`（`matmaster/integration/event_payloads.py:84,310` 的 `_FRONTEND_COMPAT_PASSTHROUGH = {'run_result', 'finish'}`）**不动**，留待前端 `scimaster-bohr-chat` 迁移到从 `content` 读 `final_content`/`status` 后一并清理。

由此产生一个**可接受的暂时不一致**：清理 `#3` 后后端不再产生 `finish` 事件，但 `event_payloads` 仍"识别"它（死分支）。在该处补一行注释点明此状态，等 `#4` 阶段删除。

## 执行顺序（单一提交内，按风险低→高编辑）

`#6` → `#5 resolve_profile` → `#5 _normalize` → `#1` → `#3` → `#2`

每完成一项跑相关测试，全部通过后合并为一个提交。

## 验证（精确命令，区分合法残留）

定向测试：`tests/matmaster/context/`（turn_input、history_restore）、`tests/matmaster/types/test_events.py`、`tests/matmaster/config/`、`tests/matmaster/services/`（model_history_restore、stream_sse_filter、chat_history）、`tests/matmaster/integration/test_events_to_messages.py`、`tests/matmaster/integration/test_history_checkpoint_recovery.py`、`tests/matmaster/integration/test_e2e_mat_master.py` 中被本 spec 点名的用例。随后全量 `pytest`。

残留 grep（须为空，除非另注；命令里的 `docs/` 默认不纳入，旧 plan/spec 可作为历史记录保留）：

- event type Literal `finish` / `end`：

```bash
rg -n 'type: Literal\[[^]]*("finish"|"end")' matmaster/types/events.py
```

- 流关闭判断含 `'end'`：

```bash
rg -n "payload\.get\('type'\) in \{[^}]*'end'" src/services/stream_service.py
```

- `finish` 作为事件类型被消费（排除 #4 保留区）：

```bash
rg -n "('run_result', 'finish'|\{'run_result', 'finish'\}|type['\"]?\s*:\s*['\"]finish['\"]|_public_content_for_event\(['\"]finish)" matmaster src tests --glob '!matmaster/integration/event_payloads.py' --glob '!tests/matmaster/integration/test_event_payloads.py'
```

- `'end'` 作为 event type（非 `stream_state`）：

```bash
rg -n "(payload\.get\('type'\) in \{[^}]*'end'|type['\"]?\s*:\s*['\"]end['\"]|event_type == ['\"]end['\"])" matmaster src tests
```

- `pre_query_scope_event_id` / `current_input_context` / `legacy_current_input_payload`：

```bash
rg -n 'pre_query_scope_event_id|current_input_context|legacy_current_input_payload' matmaster src tests
```

- `_legacy_restore` / `LegacyRestore` / `_restore_legacy_untrimmed`：

```bash
rg -n '_legacy_restore|LegacyRestore|_restore_legacy_untrimmed' matmaster src tests
```

- `resolve_profile` / `_normalize_legacy_or_explicit_schema`：

```bash
rg -n 'resolve_profile|_normalize_legacy_or_explicit_schema' matmaster src tests
```

## 风险与回滚

- **误删 hybrid 正常逻辑**：明确"保留 `hybrid_mode` / `user_turn_context`→`query` 转换 / `context_compaction`"；`test_history_restore.py`、`tail.py:192/227` 的 hybrid/older-checkpoint 用例须保持通过。
- **core raise 被服务层吞 / 三者打架**：通过区分异常类型规避——core 对 null boundary 抛专用异常，服务层只对该异常上抛、对 malformed 才降级试更老；删 `_restore_legacy_untrimmed` 后用 raise 替代"全失败→legacy"分支。
- **src 消费方漏改 / 验收误判**：靠上面的精确 grep（区分合法的 `stream_state == 'end'` 与排除项 `event_payloads`）兜底。
- **回滚**：单一提交，`git revert` 该 commit 即可整体回退。
