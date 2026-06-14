# Subagent spawn 绑定事件设计（spawn_id ↔ parent_call_id）

日期：2026-06-13
状态：已与需求方对齐，待实施
约束：本文档不进入 git 提交（项目规则：docs/ 不提交）

## 1. 背景与问题

并行调用多个 subagent 时，前端无法把子事件流归属到具体的 Agent 工具调用卡片，UI 一片混乱。

后端排查结论：子 agent 自身的事件（thought / response / tool_call / tool_result / run_result）在当前代码中**全链路已携带 spawn_id**：

- 事件基类字段：`matmaster/types/events.py` 的 `EventBase.spawn_id`
- 编排器重打标：`matmaster/core/subagent_orchestrator.py` 对每条子事件 `model_copy(update={"source": "MatMaster:<exp>", "spawn_id": ...})`
- live SSE 顶层恒带：`sse_handler.py` → `event_payloads.build_public_sse_payload_from_bus_dump` 写入顶层 `spawn_id`
- 持久化专列：`PersistenceHandler` → `evo_chat_events.spawn_id`
- 回放带出：`chat_events_table._row_to_event` 顶层含 `spawn_id`

真正的缺口是**父子绑定契约**：父 Agent 工具的 `tool_call`（call_id）与子事件流（spawn_id）之间没有任何映射。

- spawn_id 在工具执行期由 orchestrator 内部 mint，从不回传：`spawn_fn(exp_name, prompt, cancel_token)` 不知道父 call_id；`DrainResult` 无 spawn_id；父 `tool_result` payload 无 spawn_id
- `SUBAGENT_START/STOP` 只是内部 hook，不上事件总线
- 并行同类型 spawn 的子事件 source 完全相同（`MatMaster:<exp_name>`），前端只能按位置猜配对

前端侧（scimaster-bohr-chat）已独立分析确认同一结论，并选定方向：后端补契约 + 前端按 spawn_id 分容器。

## 2. 方案选型

**方案 A（采纳）：spawn 绑定事件 + spawn_id 回传。**
orchestrator 在子事件流开始前发公共绑定事件；DrainResult 与父 tool_result payload 回传 spawn_id 作收口与兜底。绑定先于首条子事件可用；取消/异常路径绑定不丢（绑定事件已持久化）；不动 kernel、不动 DB schema、不动现有事件结构。

**方案 B（否决）：spawn_id 复用父 call_id。**
call_id 由 LLM vendor 生成（LiteLLM Proxy 后多 vendor），跨 turn 唯一性无保证（索引式 "0"/"1" 风格真实存在）。spawn_id 是持久化层检查点 scope 查询键（`(session_id, invocation_id, spawn_id)` 去重、`get_latest_scope_event_id` 划界），同 session 撞键会混淆两个 spawn 的事件 scope，损坏回放与上下文恢复。

**方案 C（否决，并入 A）：仅 tool_result payload 回传。**
绑定要等子代理结束才可用，不解决并行运行中的实时归属；execute 抛异常时 runner 把结果包成 error 字符串，payload 丢失、绑定断链。

## 3. 详细设计

### 3.1 新事件：SubagentSpawnEvent

`matmaster/types/events.py` 新增，注册进 `SystemEvent` 与 `BusEvent` union（顶部 docstring 的事件计数同步 +1）：

```python
class SubagentSpawnEvent(EventBase):
    """Spawn 绑定事件：宣告 spawn_id 与父 Agent 工具调用的对应关系。"""

    type: Literal["subagent_spawn"] = "subagent_spawn"
    parent_call_id: str | None = None
    exp_name: str
    task_summary: str = ""
```

- `spawn_id` 来自 `EventBase`，由 orchestrator 构造时直接填入（不依赖转发重打标）
- `source` 为 `MatMaster:<exp_name>`，与该 spawn 的子事件一致

线上形态（live SSE 与回放同构）：

```json
{ "source": "MatMaster:<exp>", "type": "subagent_spawn",
  "content": { "parent_call_id": "call_x", "exp_name": "...", "task_summary": "..." },
  "spawn_id": "<16hex>", "session_id": "...", "task_id": "...", "invocation_id": "..." }
```

### 3.2 数据流（一次 Agent 调用）

1. 父 LLM 发起 `tool_call`（call_id=C）——现状不变
2. runner 执行 AgentTool，`exec_ctx.tool_call_id=C`（`tool_runner.py` 已注入，现状不变）
3. AgentTool 把 C 与 task_summary 传入 spawn_fn
4. orchestrator mint spawn_id=S（随机 16 hex，机制不变），**先 await** 经 child_event_sink 分发 `SubagentSpawnEvent`，**再**开始 drain 子流——保证绑定事件先于任何 spawn_id=S 的子事件进入 fanout
5. 子事件照旧转发（顶层 spawn_id=S，不动）
6. 子终止事件 `run_result`（spawn_id=S，含 status/reason）照旧，前端以它收口每条流；**不新增 stop 事件**
7. orchestrator 把 spawn_id 填入 DrainResult 返回；AgentTool 在 tool_result payload 增加 `spawn_id: S`

### 3.3 改动清单

| 文件 | 改动 |
|---|---|
| `matmaster/types/events.py` | 新增 `SubagentSpawnEvent`，注册 union，docstring 计数更新 |
| `matmaster/types/stream_drain.py` | `DrainResult` 增加 `spawn_id: str \| None = None` |
| `matmaster/core/subagent_orchestrator.py` | `spawn()` 增加关键字参数 `parent_call_id: str \| None = None`、`task_summary: str = ""`；mint 后先经 sink 发绑定事件（沿用既有 try/except 包裹，发送失败只告警不中断 spawn）；返回前填充 `DrainResult.spawn_id`；`make_spawn_fn` 闭包透传新参数 |
| `matmaster/tools/builtin/agent_tool.py` | `SpawnFn` 类型放宽为 `Callable[..., Awaitable[DrainResult]]`；实现集中到 `execute_with_context`（compiler 首选路径），`parent_call_id` 取 `exec_ctx.tool_call_id`（exec_ctx 为 None 时传 None）；tool_result payload 增加 `"spawn_id": drain.spawn_id` |
| `matmaster/integration/event_payloads.py` | `_public_content_for_event` 增加 `subagent_spawn` 映射：`{parent_call_id, exp_name, task_summary}` |
| `src/services/stream_sse_filter.py` | 无改动；明确 `REPLAY_DISCARDED_EVENT_TYPES` **不**加入该类型（回放需要它重建归属） |
| 测试 | 更新 `tests/matmaster/tools/builtin/test_agent_tool.py`、`tests/matmaster/core/test_hook_wiring.py` 中 spawn_fn 替身；新增行为断言（见 3.6） |

### 3.4 自动成立、无需改动的部分

- SSE 与持久化 handler 走 EventBase 通用路径，新事件自动获得顶层 spawn_id 与 DB spawn_id 列
- 绑定事件 spawn_id 非空 → `chat_history.exclude_spawn_events` 自动将其排除出父 LLM 对话历史，不污染上下文
- devshell EventLogger 透明记录新事件类型
- `SUBAGENT_START/STOP` hook 保留原样（面向 skills/automation 的内部机制，与本事件互不替代）

### 3.5 边界情况

- **parent_call_id 缺失**：runner 恒注入 tc.id，理论上不发生；防御性允许 None，前端退回位置匹配或独立卡片
- **kernel 重试同一 call_id 二次 spawn**：两个 spawn_id 先后绑定同一 call_id，前端按后到优先或并列展示（契约中说明）
- **子代理取消/异常**：绑定事件已落库；子 `run_result(status=cancelled)` 或父 error tool_result 收口
- **持久化落库顺序**：persistence 为后台任务，绑定行与首条子事件行的 DB 写入顺序在极端情况下可能互换。前端容器按 spawn_id 路由不受影响；卡片挂载以绑定信息到达为准，迟到绑定同样成立
- **绑定事件发送失败**：与既有子事件转发同语义——告警、不中断 spawn；前端靠 tool_result payload 兜底

### 3.6 测试与验证点

行为断言（不做措辞逐字断言）：

- orchestrator：spawn 过程先产出 `subagent_spawn` 事件、后产出子事件；事件携带 parent_call_id / exp_name / task_summary，且 spawn_id 与后续子事件、DrainResult.spawn_id 一致
- agent_tool：`execute_with_context` 把 `exec_ctx.tool_call_id` 传入 spawn_fn；tool_result payload 含 spawn_id
- event_payloads：`subagent_spawn` 的 SSE content 映射字段齐全；顶层 spawn_id 存在
- 回放：`subagent_spawn` 不在 `REPLAY_DISCARDED_EVENT_TYPES` 中（行为：回放输出包含该事件）

人工验证：mm-devshell 触发并行双 spawn，检查 SSE 序列中绑定事件先于子事件、tool_result payload 含 spawn_id、DB 行 spawn_id 列正确。

## 4. 前端契约（scimaster-bohr-chat 侧，不在本仓实施）

- 按 spawn_id 分容器路由所有子事件（含 tool_call / tool_result / tool_progress，不再只有 thought / response 开块）
- 收到 `subagent_spawn` 即建立 spawn_id → parent_call_id 绑定，把容器挂到对应 Agent 卡片下
- `tool_result.content.info.spawn_id` 作晚绑定兜底（回放、绑定事件丢失场景）
- 卡片标题使用绑定后的 task_summary（或 exp_name + spawn_id 短码）

## 5. 明确不做

- 不加 `subagent_stop` 事件（子 run_result 已是终止信号）
- 不改 DB schema（spawn_id 列已存在）
- 不动 kernel / tool_runner（call_id 注入已存在）
- 不在子事件上附加 parent_call_id 字段（绑定事件一处宣告即可，避免每条子事件冗余）
