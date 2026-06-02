# Agent Token Usage Aggregation Design

## 背景

当前 `RunResultEvent.usage` 的实际语义是 root agent kernel 中通过 retry gate 后
被接受的主循环 LLM turn 累计值。这个值能覆盖普通对话轮次，但不能覆盖两类真实
消耗：

- subagent 内部 LLM 调用。
- context compaction summary LLM 调用。

已有代码里这不是 provider 取不到 usage 的问题，而是聚合边界没有把这两类消耗并入
`state.total_usage`：

- `SubagentOrchestrator.spawn()` 通过 `drain_run_stream()` 拿到 child
  `DrainResult`，但只把 `final_content` 返回给 `AgentTool`。
- `call_summary_llm()` 调用 `llm_provider.chat()` 后只返回 summary text，
  丢弃 `LLMResponse.usage`。
- `ToolResultEvent.total_usage` 和 `RunResultEvent.usage` 都来自
  `AgentKernel` 的 `state.total_usage`，所以没有进入该状态的 usage 都不会出现在
  最终 run 统计里。

本设计目标是在不新增数据类、不引入独立 ledger 表、不改变 provider usage
抽取方式的前提下，把 subagent 和 compaction 的 usage 作为增量接入现有累计器。

## 目标

- 让 `RunResultEvent.usage` 表示一次 root run 下已知的累计 LLM usage，包含：
  - root agent accepted LLM turns。
  - `Agent` 工具触发的 subagent run usage。
  - compaction summary LLM usage。
- 保持 `turn_usage` 的现有语义：父 agent 当前 LLM turn 的 usage，不被 subagent
  或 compaction 覆盖。
- 保持 `ToolResultEvent.total_usage` 的现有语义：父 run 截止该事件时的累计 usage。
- 不新增 Pydantic model、dataclass、数据库表或新的顶层事件类型。
- 使用已有 `ToolResult.payload`、`CompactionEvent` 和 `state.total_usage` 承载增量。
- 明确哪些字段可以聚合，哪些字段只是累计快照，避免重复计数。
- 一次性迁移相关调用点和测试，不保留旧 `SpawnFn` 字符串返回形态或
  `call_summary_llm()` 兼容 wrapper。

## 非目标

- 不追求账单级完整成本核算。provider 抛异常且没有返回 usage 的失败请求仍无法统计。
- 不把 retry 丢弃 attempt 纳入本阶段统计；root agent 仍只统计 accepted response。
- 不把 `usage_vendor_by_turn` 扩展成全模型调用明细。它继续只表示 root agent accepted
  LLM turns 的 provider-native 快照。
- 不改变 quota 扣减逻辑。
- 不从持久化历史回扫 usage；聚合发生在当前 run 的内核状态机内。
- 不在本阶段新增 compaction interrupted 事件的产出逻辑；这里只定义如果未来产出该事件，
  usage 字段应遵循的语义。

## 设计原则

### 以 delta 接入累计器

subagent 的 `DrainResult.usage` 对 child run 来说是 total usage，但对父 run 来说是
一次工具调用带来的 usage delta。compaction summary LLM 的 `LLMResponse.usage` 也是
一次额外模型调用的 usage delta。

这两类 delta 都应在事件发出前并入 `state.total_usage`，让后续事件天然看到更新后的
累计值。

所有进入 `state.total_usage` 的 delta 都必须是 provider-normalized scalar usage：
字段值为非负整数，且 cache / reasoning 等扩展字段应尽量在 provider 层从
provider-native usage 投影到 scalar dict。服务层不再把 root-only
`usage_vendor_by_turn` 当成混合来源 usage 的补账来源。

### 不重定义 total_usage

`ToolResultEvent.total_usage` 不能在 `Agent` 工具上改成 subagent total usage。
它必须继续表示父 run 累计值。

subagent 自己的消耗放在 `ToolResult.payload` 中，例如
`payload["subagent_usage"]`。这样：

- 顶层 `turn_usage` 仍是父 agent 发起工具调用那一轮的 usage。
- 顶层 `total_usage` 是父 run 聚合后的累计值。
- payload 中的 `subagent_usage` 是本次工具调用贡献的增量证据。

### 不覆盖 turn_usage

`state.turn_usage` 只由主循环 accepted `LLMResponse.usage` 更新。subagent 和
compaction 都只更新 `state.total_usage`，不改 `state.turn_usage`。

原因是 runtime compaction planner 会读取 `state.turn_usage["prompt_tokens"]` 估算
当前上下文大小。如果 compaction usage 或 subagent usage 覆盖了 `turn_usage`，下一次
压缩判断会把非主对话调用误当成主对话 turn。

### 单点累计

subagent usage 只在 `dispatch_tool_calls()` 处理 `Agent` 工具结果时累计一次。
不要再从 child forwarded events 或 DB persistence 中二次聚合。

compaction usage 只在 `run_compaction_plan()` 获得 summary LLM response 后累计一次。
不要再从 `CompactionEvent` 回读聚合。

### 事件使用快照

所有对外事件上的 `turn_usage` / `total_usage` 都必须是事件创建时的独立快照，至少包括
`ResponseEvent`、`AssistantStateEvent`、`ToolResultEvent` 和新增 usage 字段后的
`CompactionEvent`：

```python
turn_usage=dict(state.turn_usage)
total_usage=dict(state.total_usage)
```

不要把 `state.turn_usage` 或 `state.total_usage` 的原始 dict 引用直接传给事件。否则同
一批多个 tool result 或后续 compaction/subagent 累计可能让较早事件观察到较晚状态。

## 字段语义

### RunResultEvent

现有字段继续使用：

```python
usage: dict[str, int]
usage_vendor_by_turn: list[dict[str, Any]]
```

变更后的语义：

- `usage`：root run 已知累计 scalar usage，包含 root accepted turns、subagent
  usage、compaction summary usage。
- `usage_vendor_by_turn`：仍只包含 root agent accepted turns 的 provider-native
  usage，保持与 root `num_turns` 对齐。

这意味着 `usage` 不再要求等于 `usage_vendor_by_turn` 的 scalar 投影之和。

`FinishDetail.last_turn_usage` 不随本设计扩张。它继续表示触发 invalid finish 等诊断
的 root agent 最后一轮 LLM usage，不包含 subagent 或 compaction usage。这一点依赖
不覆盖 `state.turn_usage` 的原则。

### ToolResultEvent

现有字段继续使用：

```python
turn_usage: dict[str, int]
total_usage: dict[str, int]
payload: dict[str, Any]
```

变更后的语义：

- `turn_usage`：父 agent 当前 LLM turn usage。
- `total_usage`：父 run 截止该 tool result 事件时的累计 usage。
- `payload["subagent_usage"]`：仅 `tool_name == "Agent"` 时出现，表示本次 subagent
  run 的 scalar usage delta。

`payload["subagent_usage"]` 示例：

```json
{
  "prompt_tokens": 100,
  "completion_tokens": 20,
  "total_tokens": 120,
  "cache_read_tokens": 40
}
```

可选调试字段：

```json
{
  "subagent_status": "completed",
  "subagent_reason": "natural",
  "subagent_num_turns": 2
}
```

不使用 `payload["total_usage"]` 表示 subagent usage，避免和事件顶层
`total_usage` 混淆。

### CompactionEvent

在现有 `CompactionEvent` 上增加真正可选的 usage 字段：

```python
turn_usage: dict[str, int] | None = None
total_usage: dict[str, int] | None = None
```

字段语义：

- `turn_usage`：本次 compaction summary LLM call 的 usage delta。虽然字段名沿用
  response/tool_result 事件习惯，但这里的 turn 指本次 compaction model call。
- `total_usage`：父 run 截止该 compaction lifecycle 事件时的累计 usage。

这里不使用 `Field(default_factory=dict)`。原因是 Pydantic `model_dump()` 会把默认空
dict 序列化出来，而 public payload 投影层如果只判断字段是否为 `None`，会让
`status == "running"` 事件看起来也带了空 usage。`None` 才表示该 lifecycle event 不携带
usage 语义。

`status == "running"` 的 compaction event 不带 usage；`status == "complete"` 时，如果
summary LLM 返回了非空 usage，则带 `turn_usage` 和 `total_usage`。`status ==
"interrupted"` 时分两类：

- 中断发生在 summary LLM 返回之前：没有 usage 可计，事件不带 `turn_usage`，可带当前
  `total_usage` 快照。
- 中断发生在 summary LLM 返回之后：先累计该 response 的 usage，再让 interrupted
  event 带 `turn_usage` 和更新后的 `total_usage`。

本 spec 不要求新增 interrupted 事件生产路径；现有正常路径仍只产出 `running` 和
`complete`。这里的 interrupted 规则是事件模型已有状态的前瞻约束，避免未来补产出时
重新定义 usage 语义。

## Subagent 数据流

当前数据流：

```text
parent LLM tool call
  -> AgentTool.execute()
  -> spawn_fn(exp_name, prompt, cancel_token)
  -> SubagentOrchestrator.spawn()
  -> drain_run_stream(child_exp.run_stream(...))
  -> returns final_content only
  -> ToolResult(content=final_content, payload={exp_name, task_summary, prompt})
  -> dispatch_tool_calls() emits ToolResultEvent(total_usage=parent_total_only)
```

目标数据流：

```text
parent LLM tool call
  -> AgentTool.execute()
  -> spawn_fn(...)
  -> SubagentOrchestrator.spawn()
  -> drain_run_stream(child_exp.run_stream(...))
  -> returns DrainResult
  -> AgentTool builds ToolResult(payload["subagent_usage"] = child usage)
  -> dispatch_tool_calls() extracts payload["subagent_usage"]
  -> accumulate_usage(state.total_usage, subagent_usage)
  -> emit ToolResultEvent(total_usage=parent_total_with_subagent)
```

### SpawnFn contract

不新增 subagent result 数据结构，复用已有 `DrainResult`，但不能让 `matmaster/tools`
反向依赖 `matmaster/core`。因此先把 `DrainResult` 从
`matmaster/core/stream_drain.py` 移动到 types 层，例如
`matmaster/types/stream_drain.py`；`core.stream_drain` 只保留 `drain_run_stream()` 并从
types 导入 `DrainResult`。

移动后 `DrainResult` 是 run-stream drain / subagent boundary 的 canonical result type。
现有 `KernelResult` 暂不参与这条 subagent contract；它属于遗留或内部 terminal summary
重叠对象，本设计不继续扩大它的使用面，也不让 `SpawnFn` 返回 `KernelResult`。

迁移后 `SpawnFn` 返回值从 `str` 一次性迁移为：

```python
Awaitable[DrainResult]
```

`SubagentOrchestrator.spawn()` 返回 `drain_run_stream()` 得到的 `DrainResult`。
`AgentTool.execute()` 从 types 层导入 `DrainResult`，负责把它映射为 `ToolResult`：

```python
content = (
    drain.final_content
    if drain.status == "completed" and drain.final_content
    else f"SubAgent finished with status={drain.status}, reason={drain.reason}"
)
payload["subagent_usage"] = dict(drain.usage or {})
payload["subagent_status"] = drain.status
payload["subagent_reason"] = drain.reason
payload["subagent_num_turns"] = drain.num_turns
```

同步迁移所有 fake spawn 和测试，不保留旧字符串返回分支。

### dispatch_tool_calls 聚合规则

在 `matmaster/core/agent_tool_dispatch.py` 中新增一个小 helper：

```python
def extract_tool_usage_delta(tool_name: str, tool_result: ToolResult) -> dict[str, int]:
    ...
```

第一阶段只识别：

- `tool_name == AgentTool.name`，或同模块常量 `AGENT_TOOL_NAME = "Agent"`
- `tool_result.payload["subagent_usage"]` 是 `dict`

`ToolResult.payload` 是开放 dict，helper 是该开放边界进入累计器前的净化点。helper
要求 `subagent_usage` 是 `dict[str, int]` 语义的 scalar usage：每个值都必须是非 bool
的非负 int。缺失 `subagent_usage` 时不做累计；存在但形状非法时，这是内部契约错误，
不能静默净化后继续。`accumulate_usage()` 本身保持简单裸加法，只接收已经校验或来自
provider 的可信 scalar usage。

处理顺序：

1. `ToolRunner.execute_batch()` 返回 `runner_results`。
2. 对每个 `(tc, tool_result)`：
   - 先把 `ToolMessage` append 到 `state.messages`，保持模型上下文行为不变。
   - 提取 `usage_delta`。
   - 如非空，`accumulate_usage(state.total_usage, usage_delta)`。
   - 再构造 `ToolResultEvent(... total_usage=dict(state.total_usage) ...)`。

这样 `ToolResultEvent.total_usage` 会包含本次 subagent usage。

### 多 Agent 工具调用

如果同一父 LLM turn 批量调用多个 `Agent` 工具，每个 tool result 按完成后的
`runner_results` 顺序依次累计。每个事件的 `turn_usage` 相同，表示父 LLM turn；每个
事件的 `total_usage` 逐步增加。

最终 `RunResultEvent.usage` 与这些 tool result 的处理顺序无关，因为 scalar usage
加法满足交换律；顺序只影响中间 `ToolResultEvent.total_usage` 快照看到的是哪个阶段的
累计值。

消费者不得通过累加多个 `ToolResultEvent.turn_usage` 来计算总量，这是既有约束。

## Compaction 数据流

当前数据流：

```text
run_compaction_plan()
  -> call_summary_llm()
  -> llm_provider.chat(...)
  -> return response.content
  -> apply_summary(...)
  -> emit CompactionEvent(status="complete", trigger_tokens=...)
```

目标数据流：

```text
run_compaction_plan()
  -> call_summary_llm_response()
  -> llm_provider.chat(...)
  -> receive LLMResponse
      -> summary_usage = dict(response.usage or {})
      -> if summary_usage: accumulate_usage(state.total_usage, summary_usage)
      -> validate response.content / tool_calls
      -> apply_summary(...)
      -> emit CompactionEvent(
           status="complete",
	       turn_usage=dict(summary_usage) if summary_usage else None,
	       total_usage=dict(state.total_usage) if summary_usage else None,
	     )
```

### call_summary_llm_response helper

将现有 `call_summary_llm()` 迁移为 `call_summary_llm_response()`，返回
`LLMResponse`。不新增数据类，不保留返回 `str` 的兼容 wrapper。同步迁移
`run_compaction_plan()` 和相关测试。

把现有 `call_summary_llm()` 内部的校验逻辑抽成纯函数：

```python
def validate_summary_response(response: LLMResponse) -> str:
    if response.tool_calls:
        raise ValueError("Summary LLM attempted tool calls")
    if not response.content or not response.content.strip():
        raise ValueError("Summary LLM returned empty content")
    return response.content
```

推荐形态：

```python
response = await call_summary_llm_response(...)
summary_usage = dict(response.usage or {})
if summary_usage:
    accumulate_usage(state.total_usage, summary_usage)
summary = validate_summary_response(response)
result = await compactor.apply_summary(..., summary, ...)
```

这样 provider 成功返回但 summary 内容非法时，也可以在进入 fallback 前累计该次
summary LLM 的 usage。

如果 provider 抛异常且没有 `LLMResponse`，没有 usage 可累计，保持当前 fallback 或
preflight abort 行为。需要特别区分 runtime 与 preflight：runtime validation failure 会继续
走 `apply_fallback()`，因此后续 `CompactionEvent(status="complete")` 和最终
`RunResultEvent` 可以承载已累计 usage；preflight validation failure 当前会 abort 并向
service 抛出异常，本阶段不新增 preflight abort terminal 事件，所以这类 usage 不作为 public
payload / final `RunResultEvent.usage` 的验收项。

### runtime fallback

runtime summary LLM 失败后，当前逻辑会走 `apply_fallback()`。本设计下：

- 如果 provider 已返回 `LLMResponse`，其 usage 已经累计。
- 如果 provider 在网络、超时、鉴权、上下文溢出等阶段抛异常，没有 usage 可累计。
- `apply_fallback()` 本身不调用 LLM，不新增 usage。

### preflight compaction

preflight compaction 发生在第一轮 root LLM call 之前。此时：

- `state.turn == 0`。
- `state.turn_usage == {}`。
- `state.total_usage` 可以因为 compaction summary LLM 变为非空。

这是合法状态。`num_turns` 仍只表示 root agent 主循环完成的 LLM turns，不表示所有模型
调用次数。

## 持久化与 SSE

### ToolResultEvent

`_public_content_for_event("tool_result", payload)` 已经会在 `content` 中投影
`payload` 为 `info`，并在存在 `turn_usage` 时投影 `turn_usage` / `total_usage`。

subagent usage 将作为 `content.info.subagent_usage` 持久化和发送给前端。顶层
`total_usage` 不重复上提，保持当前结构化事件规则。

### CompactionEvent

`_public_content_for_event("compaction", payload)` 需要把新增的 `turn_usage` 和
`total_usage` 放入 compaction content，但只投影非空 usage dict：

```python
for key in ("turn_usage", "total_usage"):
    if payload.get(key):
        content[key] = payload[key]
```

这样历史 DB 和 live SSE 都能看到每次 compaction 对累计 usage 的影响。

### RunResultEvent

`run_result` public content 已包含聚合后的 `usage`，不需要新增字段。前端如果只关心
最终总量，继续读 `content.usage` 即可。

## Feishu usage summary

`_build_run_usage_summary()` 当前文档说明 root-only，需要更新为：

- scalar `event.usage` 是 run-level aggregated usage，包含 root accepted turns、
  subagent 和 compaction。
- `usage_vendor_by_turn` 仍是 root accepted turns 的 vendor detail，不再作为混合来源
  aggregate scalar 的 cache/reasoning 补账来源。

原因是 `event.usage` 变成 root + subagent + compaction 的混合 scalar，而
`usage_vendor_by_turn` 仍只覆盖 root turns。若 `_build_run_usage_summary()` 在
`cache_read_tokens == 0` 之外继续用 root vendor 兜底，可能漏算 root cache；若无条件把
vendor cache 加回 aggregate scalar，又可能在 OpenAI 这类 scalar 已包含 root cache 的
路径上双算。

因此本设计要求把 cache / reasoning 的 scalar normalization 前移到 provider 层：

| scalar 字段 | OpenAI chat | OpenAI stream | Bedrock chat | Bedrock stream | 本设计要求 |
| --- | --- | --- | --- | --- | --- |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | 已投影 | 已投影 | 已投影 | 已投影 | 保持 |
| `cache_read_tokens` | 已投影 | 已投影 | 未投影 | 未投影 | Bedrock 补齐可得字段 |
| `cache_write_tokens` | 未投影 | 未投影 | 未投影 | 未投影 | provider 返回时投影；缺失则不带 key |
| `reasoning_tokens` | 未投影 | 未投影 | 未投影 | 未投影 | provider 返回时投影；缺失则不带 key |

后续 provider 也必须在 `LLMResponse.usage` / `StreamChunk.usage` 中产出一致 scalar
字段。reasoning 覆盖度天然取决于 provider 是否返回该信息；provider 不返回时不带
`reasoning_tokens`，不再由服务层从 root-only vendor detail 补账。

`_build_run_usage_summary()` 以 `event.usage` 的 scalar 字段为准。若仍要保留
vendor-by-turn fallback，只能用于 root-only 旧口径或诊断日志，不能参与 run-level
aggregate cache/reasoning 统计。

实施时必须先补 provider scalar normalization，再停用 `_build_run_usage_summary()` 的
vendor fallback，两者应在同一变更集中原子完成。否则 `cache_write_tokens` 和
`reasoning_tokens` 会在中间态直接归零。

## 迁移清单

- `SpawnFn` 返回类型从 `Awaitable[str]` 改为 `Awaitable[DrainResult]`。
- `DrainResult` 从 `matmaster/core/stream_drain.py` 移动到 types 层，避免
  `matmaster/tools` 依赖 `matmaster.core`。
- `SubagentOrchestrator.spawn()` 返回 `DrainResult`，不再返回字符串。
- `AgentTool.execute()` 负责把 `DrainResult` 转成 `ToolResult.content` 和
  `payload["subagent_usage"]`。
- `call_summary_llm()` 改名/迁移为 `call_summary_llm_response()`，返回 `LLMResponse`。
- `validate_summary_response()` 承接 summary tool-call / empty-content 校验。
- `dispatch_tool_calls()` 发事件时使用 usage dict 快照；`AgentKernel` 中的
  `ResponseEvent` 和 `AssistantStateEvent` 也必须同步改成 usage dict 快照。
- `AgentKernel` 捕获 malformed subagent usage 的 typed internal exception，产出 failed
  `RunResultEvent(reason="internal_error")`，避免异常冒泡到 service 后丢失 terminal usage。
- provider scalar usage normalization 覆盖 cache / reasoning 字段，服务层不再用
  root-only vendor detail 补混合 aggregate。
- `usage_vendor_by_turn` 不扩展，避免破坏 DevShell / eval 中 by-turn 对齐假设。
- `ToolResultEvent.total_usage` 语义不变，不影响现有前端读取。
- `RunResultEvent.usage` 语义从 root-only 升级为 run-level known aggregate，需要同步更新
  token usage 设计文档和相关测试命名。

## 错误处理

### malformed subagent usage

`payload["subagent_usage"]` 只应由 `AgentTool` 从 `DrainResult.usage` 写入。如果存在但
不是 dict，或任一字段值不是非 bool 的非负 int，这是内部契约错误：

- 不把非法值传给 `accumulate_usage()`。
- 记录 warning，包含 `tool_name` 和非法字段名。
- 对于 `Agent` 工具的成功结果，非法 `subagent_usage` 应使当前 run 进入 failed terminal
  路径：`extract_tool_usage_delta()` 抛出 typed internal exception，`AgentKernel` 在
  `dispatch_tool_calls()` 调用边界捕获它，产出 `RunResultEvent(status="failed",
  reason="internal_error", usage=dict(state.total_usage))` 后结束。不要让这个异常直接冒泡到
  service 的 generic `ErrorEvent + StreamClosedEvent` 路径，否则已经累计的 root usage 没有
  terminal carrier。
- 对于 tool 本身已经是 error/cancelled 且没有 usage 的结果，可以跳过累计。

### subagent cancelled/error

只要 child `DrainResult.usage` 非空，无论 child `status` 是 completed、failed 还是
cancelled，都应作为已经发生的模型调用消耗计入父 run。

`AgentTool` 的 `content` 仍按当前逻辑：

- completed 且有 final content：返回 child final content。
- 其他情况：返回 `SubAgent finished with status=..., reason=...`。

### compaction validation failure

summary LLM 返回 response 后，如果 response 有 usage，应先累计，再验证内容：

- response 试图 tool call：累计 usage，然后 runtime 进入 fallback；preflight 仍按现有逻辑
  abort。
- response content 为空：累计 usage，然后 runtime 进入 fallback；preflight 仍按现有逻辑
  abort。

provider 直接抛异常时没有 response usage 可累计。

preflight abort 当前不会产出 `RunResultEvent` 或 `CompactionEvent(status="complete")`，
所以即使内存态已经累计该 usage，也不会进入 public payload。本阶段不修这个更大的
preflight failure terminal contract；如果未来要把 preflight abort usage 对外暴露，需要另起
设计补齐失败 terminal carrier。

### compaction interrupted

当前正常 compaction 生成 `running` / `complete` 两类事件，但事件模型允许
`interrupted`。如果后续在 compaction generator close/cancel 路径补 interrupted 事件，
usage 行为按是否已经拿到 `LLMResponse` 判定：

- 未拿到 response：不累计 summary usage。
- 已拿到 response：先累计 response usage，再让 interrupted 事件携带本次
  `turn_usage` 和累计后的 `total_usage` 快照。

本阶段不新增 interrupted 事件生产逻辑，也不把 interrupted 产出纳入验收测试。

## 测试计划

### unit: subagent aggregation

新增 `tests/matmaster/core/test_agent_kernel_subagent_usage.py`，或扩展
`tests/matmaster/core/test_agent_kernel_usage_events.py`：

- child run 返回 `RunResultEvent(usage={"prompt_tokens": 10, "total_tokens": 12})`。
- `AgentTool` 返回的 `ToolResult.payload["subagent_usage"]` 等于 child usage。
- parent `ToolResultEvent.total_usage` 包含 previous total + parent accepted turn usage
  + subagent usage。
- parent final `RunResultEvent.usage` 包含 subagent usage。

### unit: SpawnFn migration

扩展 `tests/matmaster/tools/builtin/test_agent_tool.py`：

- `spawn_fn` 返回 `DrainResult`。
- completed 且有 `final_content` 时，`final_content` 映射为 `ToolResult.content`。
- error/cancelled 或无 final content 时，状态字符串映射为 `ToolResult.content`。
- `DrainResult.usage` 映射为 `payload["subagent_usage"]`。
- `status`、`reason`、`num_turns` 映射为 subagent debug payload。

### unit: dispatch_tool_calls extraction

新增 `extract_tool_usage_delta` helper 测试：

- 只识别 `tool_name == "Agent"`。
- 缺失 `subagent_usage` 时不累计。
- `Agent` 工具成功结果中存在非 dict usage 时产出 failed
  `RunResultEvent(reason="internal_error")`，不冒泡到 service generic error path。
- 非 int、负数、bool 值不会进入 `accumulate_usage()`；成功结果中出现这类字段时进入
  failed terminal 路径。
- 多字段 usage 正确累计到 `state.total_usage`。
- `ToolResultEvent.turn_usage` / `total_usage` 是独立快照，后续累计不会改变已发事件。

### unit: compaction aggregation

扩展 `tests/matmaster/core/test_agent_kernel_compaction.py` 或
`tests/matmaster/core/test_agent_compaction.py`：

- summary LLM success 时，`CompactionEvent(status="complete").turn_usage` 等于 summary
  response usage。
- complete compaction event 的 `total_usage` 包含 summary usage。
- final `RunResultEvent.usage` 包含 compaction usage。
- runtime summary validation failure 但 provider 返回 usage 时，usage 仍累计，然后走
  fallback。
- preflight summary validation failure 不属于本阶段 public usage 验收；它仍按现有 abort
  路径结束，不产出 terminal usage carrier。
- interrupted 事件 production 不属于本阶段测试；如果未来补 production，再按本 spec
  增加对应测试。

### integration: public payload

扩展 `tests/matmaster/integration/test_event_payloads.py`：

- tool_result public content 保留 `info.subagent_usage`。
- compaction public content 只在 usage dict 非空时投影 `turn_usage` 和 `total_usage`。
- run_result public content 的 `usage` 是最终聚合值。
- `_build_run_usage_summary()` 不用 root-only `usage_vendor_by_turn` 对混合 aggregate
  做 cache/reasoning 补账。

### regression: root-only behavior

调整 `tests/matmaster/core/test_agent_kernel_usage_events.py`：

- 无 subagent、无 compaction 时，`RunResultEvent.usage` 仍等于 distinct root
  `ResponseEvent.turn_usage` 之和。
- 有 subagent 或 compaction 时，root response usage 只是 final run usage 的子集，不再
  作为全量断言来源。

## 实施顺序

1. 增加 usage delta helper，并在 `dispatch_tool_calls()` 中累计 `Agent` 工具
   `payload["subagent_usage"]`。
2. 移动 `DrainResult` 到 types 层，并更新 core/devshell/evaluation/tests 的 import；
   同时在文档和代码注释中明确 `DrainResult` 是 drain/subagent boundary result，
   `KernelResult` 不参与 `SpawnFn` contract。
3. 调整 `SubagentOrchestrator.spawn()` 和 `AgentTool.execute()`，让 child
   `DrainResult.usage` 进入 `ToolResult.payload["subagent_usage"]`，并迁移 `SpawnFn`
   返回类型为 `DrainResult`。
4. 调整 compaction summary 调用返回 `LLMResponse`，抽出
   `validate_summary_response()`，在 `run_compaction_plan()` 中累计
   summary usage。
5. 扩展 `CompactionEvent` 可选 usage 字段和 public payload 投影。
6. 补齐 provider scalar usage normalization，并在同一变更中更新
   `_build_run_usage_summary()` 的 cache/reasoning 统计口径，确保先有 scalar 再停
   vendor fallback。
7. 给 `ResponseEvent`、`AssistantStateEvent`、`ToolResultEvent`、`CompactionEvent` 的
   usage 字段补齐独立快照测试。
8. 更新 token usage 设计文档中的 root-only 描述。
9. 补齐测试。

## 验收标准

- 普通 root-only run 的 usage 统计保持原行为。
- 包含 subagent 的 run 中，最终 `RunResultEvent.usage` 等于 root accepted turn usage
  加 subagent usage。
- 包含 preflight 或 runtime summary compaction 的 run 中，最终 `RunResultEvent.usage`
  包含 compaction summary usage。
- `ToolResultEvent.total_usage` 始终表示父 run 累计 usage。
- 已发出的 usage-bearing 事件持有创建时快照，不会被后续累计原地改变。
- `ToolResult.payload["subagent_usage"]` 和 `CompactionEvent.turn_usage` 可以作为局部
  增量审计证据，但消费者不需要累加事件即可读取最终 `run_result.content.usage`。
- `usage_vendor_by_turn` 仍只包含 root accepted turns，不因 subagent 或 compaction 扩张。
- Feishu usage summary 以 aggregate scalar 为准，不因 root-only vendor fallback 对混合
  usage 漏算或双算 cache/reasoning。
- malformed `Agent` `subagent_usage` 不会被静默净化，也不会冒泡到 service generic error
  路径；kernel 会产出 failed `RunResultEvent(reason="internal_error")` 并保留当前
  `state.total_usage` 快照。
