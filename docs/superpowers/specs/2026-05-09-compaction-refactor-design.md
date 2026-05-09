# MatMaster Compaction Refactor Design

## 背景

MatMaster 当前已经有一条可运行的上下文压缩链路：

- `matmaster/core/context_compactor.py` 判断是否触发压缩，并直接改写内存中的 `messages`。
- `matmaster/core/agent.py` 在压缩前后发出公开 `compaction` 生命周期事件。
- `src/services/history_checkpoint_service.py` 在 durable summary 成功后写入内部 `history_checkpoint`。
- `src/services/history_restore_service.py` 在下一轮 run 开始前优先从 checkpoint 恢复模型可见历史。
- `src/services/stream_service.py` 回放公开 `compaction` 事件，同时隐藏内部 `history_checkpoint`。

这套机制已经具备基本的可恢复性，但与 Claude Code 的压缩实现相比，仍存在几个结构性短板：

- 压缩前没有工具结果预算。大 Bash、计算日志、检索结果会先进入上下文，后续只能靠 full compact 或简单截断兜底。
- 自动压缩阈值只使用 `context_limit * trigger_ratio`，没有给摘要输出、工具 schema、system prompt 和 provider 估算误差预留足够 token。
- full compact 本身如果 prompt too long，没有按 API round 分组丢弃旧上下文并重试的机制。
- 压缩摘要结构较短，没有充分覆盖科研工作流中的文件、路径、工具结果、错误修复、远端任务状态和下一步。
- 压缩后恢复现场只依赖摘要和最近 turns，没有稳定的 rehydration 层来恢复 skills、workspace artifacts 等静态工作状态，也没有明确 Bohrium job 这类动态状态的重建边界。
- 当前 durable compact summary 使用 `SystemMessage`，这保留了 provider 兼容性，但也会把历史摘要放入 system 通道；任何角色调整必须先经过多 provider 兼容验证。

本设计的目标是吸收 Claude Code 的优秀分层思想，但保持 MatMaster 现有 API / Worker / DB / SSE 分离架构，不把 Claude Code 的前端 transcript 模型照搬进来。

## 设计目标

1. 降低长会话中大工具结果对上下文窗口的污染。
2. 在接近上下文硬限制前更早、更稳定地触发自动压缩。
3. 让 full compact 在摘要请求自身过大时可以有损重试，而不是直接失败或粗暴 sliding window。
4. 让压缩摘要更适合科研 agent 场景，保留路径、文件、数值、错误、远端任务和下一步。
5. 保持 `history_checkpoint` 的恢复语义，继续支持 API / Worker 多实例、Redis 事件转发、DB 历史恢复。
6. 对旧 checkpoint、旧 `compaction` 事件和旧 `context_compaction` 事件保持兼容。
7. 先交付高收益、低耦合的最小版本，再为 session memory、partial compact 留接口。

## 非目标

第一阶段不实现完整 Claude Code UI 行为：

- 不引入模型可见的 system `compact_boundary` 消息。
- 不让 `history_checkpoint` 出现在普通 SSE 回放中。
- 不实现 partial compact 的消息选择 UI。
- 不实现后台 session memory extraction。
- 不重写 MatMaster 的 transcript 或 chat 前端渲染体系。

这些能力可以在后续设计中扩展，但不应该阻塞当前压缩链路稳定性改造。

## Claude Code 到 MatMaster 的映射

Claude Code 的关键概念可以映射为 MatMaster 现有对象：

| Claude Code 概念 | MatMaster 对应设计 |
| --- | --- |
| `compact_boundary` | `history_checkpoint.covered_until_event_id` + `compaction complete` |
| `getMessagesAfterCompactBoundary` | `HistoryRestoreService.restore_history()` |
| compact summary message | v2 compact summary envelope；第一版仍 provider-safe，不默认改成 `UserMessage` |
| UI 显示 `Conversation compacted` | SSE 回放公开 `compaction` 事件 |
| transcript 保留完整历史 | `evo_chat_events` 持久化事件 |
| 模型只看 boundary 之后上下文 | checkpoint `base_messages` + covered id 之后的 tail events |

核心判断是：MatMaster 不需要照搬 Claude Code 的 system boundary message。MatMaster 的 DB checkpoint 已经天然承担切断模型历史的职责。公开 `compaction` 事件负责用户可见状态，内部 `history_checkpoint` 负责模型恢复状态。

## 0.1 现有代码事实与约束

本节用于锁定评审中暴露出的实现边界，防止后续计划基于错误假设展开。

### Runtime User Instructions 确实存在

运行时用户指令注入逻辑位于 `src/services/agent_run_service.py`：

- `_find_first_user_message_index()` 会返回模型可见历史中的第一条 `UserMessage`，见 [src/services/agent_run_service.py:155](../../../src/services/agent_run_service.py:155)。
- `_apply_user_instructions_to_initial_user_query()` 会把用户指令注入这条 first user message；没有历史 user message 时才注入当前 prompt，见 [src/services/agent_run_service.py:175](../../../src/services/agent_run_service.py:175)。
- `run_agent()` 在恢复历史之后、进入 `Exp.run_stream()` 之前调用该逻辑，见 [src/services/agent_run_service.py:843](../../../src/services/agent_run_service.py:843)。
- 现有测试覆盖了 v1 compact summary `SystemMessage` + 第一条真实 `UserMessage` 的注入行为，见 [tests/matmaster/services/test_user_instructions_runtime_injection.py:188](../../../tests/matmaster/services/test_user_instructions_runtime_injection.py:188)。

因此如果未来把 compact summary 改成 `UserMessage`，会直接改变这段逻辑的目标消息。当前设计不再把 `UserMessage` 作为默认 v2 路线。

### Checkpoint Codec 当前是 v1 单路径

`HistoryCheckpointService.sink()` 和 `HistoryRestoreService.restore_history()` 都调用 `validate_base_messages()`，见 [src/services/history_checkpoint_service.py:34](../../../src/services/history_checkpoint_service.py:34) 和 [src/services/history_restore_service.py:41](../../../src/services/history_restore_service.py:41)。该函数当前硬编码要求 `base_messages[0]` 是 compacted `SystemMessage`，见 [src/services/history_checkpoint_codec.py:83](../../../src/services/history_checkpoint_codec.py:83)。

所以 v2 checkpoint 不能只在 payload 里增加 `schema_version`，必须拆出双路径 codec：

- `validate_checkpoint_v1_base_messages(messages)`：保留当前 `SystemMessage` 起头规则。
- `validate_checkpoint_v2_base_messages(messages, envelope)`：按 v2 envelope 规则验证。
- `decode_history_checkpoint_content(content)`：先读 `schema_version`，再选择 v1/v2 校验。
- `HistoryCheckpointService` 写入前和 `HistoryRestoreService` 读取后都必须走同一个 decoder。

### Bedrock 当前会合并连续 UserMessage，但这不是全局保证

`BedrockProvider` 的 OpenAI-to-Bedrock mapper 会把连续 user messages 合并成一个 Bedrock user block，见 [matmaster/providers/bedrock_provider.py:109](../../../matmaster/providers/bedrock_provider.py:109)。但是 `normalize_and_validate_openai_messages()` 只做 OpenAI 兼容校验和 tool turn 校验，不强制 user/assistant 交替，见 [matmaster/types/message_normalization.py:52](../../../matmaster/types/message_normalization.py:52)。

MatMaster 还可能通过 OpenAI-compatible provider 访问 Anthropic / Gemini / LiteLLM 路由。不同 provider 对连续 user messages 的接受、合并或拒绝行为不一致。因此本设计不把 consecutive-user 结构作为默认输出。任何 compact summary 角色变化都必须有 provider compatibility 测试。

### 现有 `_truncate_tool_results` 是兜底，不是可替代的 artifact 预算

当前 `ContextCompactor._truncate_tool_results()` 已经能在没有可压缩旧 turn 时截断大 `ToolMessage`，保留 head 200、tail 100 和 marker，见 [matmaster/core/context_compactor.py:405](../../../matmaster/core/context_compactor.py:405)。它的行为是原地修改内存消息，`strategy="tool_truncation"`，`durability="ephemeral"`，不会写 checkpoint。

新的 `ToolResultBudgeter` 与它的关系是：

1. `ToolResultBudgeter` 优先执行，目标是把完整工具结果保存到 artifact store，并在模型上下文中放入稳定 preview。
2. 如果 budgeter 写入失败、session 不支持 artifact store、或结果类型不适合持久化，则 fallback 到现有 `_truncate_tool_results()`。
3. `tool_result_budget` 可以是轻量压缩策略；如果预算处理后 token 已低于 full compact 阈值，则本轮不需要 summary compact。
4. `tool_truncation` 保留为最后兜底，仍然是 ephemeral，不写 checkpoint。

## 目标架构

将当前单体 `ContextCompactor` 拆成一个专门的 compaction 子系统：

```text
matmaster/core/compaction/
  __init__.py
  budget.py
  rounds.py
  tool_result_budget.py
  summary_prompt.py
  summary_runner.py
  rehydration.py
  compactor.py
```

职责划分：

- `budget.py`：计算有效上下文窗口和自动压缩阈值；手动 compact 阻断阈值等到 `/compact` 入口设计时再加入。
- `rounds.py`：按 API round 对消息分组，保证 assistant tool call 与 tool result 不被拆开。
- `tool_result_budget.py`：将超大工具结果写入 artifact store，并把模型上下文中的结果替换为预览和路径。
- `summary_prompt.py`：生成结构化摘要 prompt，并从模型输出中提取最终 `<summary>`。
- `summary_runner.py`：调用摘要 LLM，处理 prompt-too-long 重试和最终 fallback。
- `rehydration.py`：压缩后重新注入确定性工作现场，例如已调用 skills、关键 workspace artifact；Bohrium job 这类会快速变化的状态由 runtime rehydration 每轮动态构建。
- `compactor.py`：编排上述模块，实现与现有 `ContextCompactor` 等价的外部接口。

`matmaster/core/context_compactor.py` 在迁移期保留为兼容门面，对外继续暴露：

- `estimate_tokens`
- `parse_turns`
- `CompactionPlan`
- `CompactionResult`
- `ContextCompactor`

内部逐步委托给新模块，避免一次性修改 `AgentKernel`、测试和调用方。

## 压缩流水线

新的压缩流水线分为轻量减负和完整摘要压缩两层：

```text
1. AgentKernel 组装当前模型可见 messages
2. ContextCompactor.plan_preflight_compaction 或 plan_runtime_compaction 估算 token
3. 超过 soft budget 时先执行 tool result budget / microcompact
4. 再次估算 token
5. 若低于 full compact 阈值，则返回轻量压缩结果
6. 若仍超阈值，则执行 full summary compact
7. summary compact 成功后生成 durable base_messages
8. AgentKernel 通过 checkpoint_sink 写 history_checkpoint
9. AgentKernel 发 compaction complete 事件
```

轻量减负必须发生在 full compact 之前。科研任务中，上下文压力经常来自单个工具输出，而不是对话历史本身。先把大结果落盘，可能避免昂贵且有损的完整摘要。

### API Round 定义

本文中的 round 不是用户视角的一轮多轮对话，也不是当前 `parse_turns()` 中的粗粒度 user/assistant 边界。这里的 API round 定义为：

```text
一次 LLM 调用产生的 AssistantMessage
+ 该 assistant message 声明的 tool_calls
+ 紧随其后的所有匹配 ToolMessage
```

没有 tool call 的自然语言 assistant response 也构成一个 round。压缩、prompt-too-long 重试和 tool result budget 都必须以 API round 为最小安全单元，避免保留 assistant tool call 却丢掉对应 tool result，或保留 orphan tool result。

## Token 预算

新增预算字段，合并进现有 `CompactionConfig`：

```python
class CompactionConfig(BaseModel):
    context_limit: int = 200_000
    trigger_ratio: float = 0.9
    strategy: str = "summary"
    compaction_llm: str | None = None
    summary_reserved_tokens: int = 20_000
    auto_compact_buffer_tokens: int = 13_000
    provider_token_safety_ratio: float = 1.15
    tool_result_budget_tokens: int = 32_000
    tool_result_preview_tokens: int = 4_000
```

实际阈值计算：

```text
reserved = min(summary_reserved_tokens, int(context_limit * 0.1))
effective_window = context_limit - reserved
auto_compact_threshold = effective_window - auto_compact_buffer_tokens
```

默认情况下：

```text
context_limit = 200000
reserved = 20000
effective_window = 180000
auto_compact_threshold = 167000
```

`trigger_ratio` 在迁移期保留，但内部优先使用新预算公式。若旧配置只设置了 `trigger_ratio`，仍能按旧逻辑近似工作。

`provider_token_safety_ratio` 用于抵消 `tiktoken` GPT encoder 在 Claude、Gemini 和中文文本上的偏差。第一版不试图实现精确 tokenizer，而是在本地估算后乘以安全系数；不同 provider 可以在配置中覆盖 buffer 或 ratio。Phase 2 的验收必须包含中文长会话和 Claude/Bedrock provider 的 smoke 验证，不能只依赖 OpenAI-compatible 单元测试。

## 工具结果预算

新增 `ToolResultBudgeter`，在 full compact 之前处理超大工具结果。

触发条件：

- 单个 `ToolMessage` 估算 token 超过 `tool_result_preview_tokens`。
- 当前 API round 的工具结果总量超过 `tool_result_budget_tokens`。
- 工具结果为文本或可 JSON 序列化对象。

处理方式：

1. 将完整内容写入 artifact store。
2. 生成稳定 preview message。
3. 用 preview 替换模型上下文中的原始 `ToolMessage.content`。
4. 保留 `tool_call_id` 和 `tool_name`，不破坏工具调用序列。

预算处理必须覆盖两条模型输入路径：

- 运行中内存路径：`AgentKernel` 当前 `state.messages` 中的大 `ToolMessage` 需要被替换为 preview。
- 历史恢复路径：`HistoryRestoreService` 从 raw events fallback 恢复时，也必须对超大 `tool_result` 重新应用 budgeter，不能把完整结果直接还原进下一轮模型上下文。

第一阶段 DB 仍可保留完整 `tool_result` 事件，确保既有审计和前端行为不被一次性破坏。模型上下文以 budgeter 输出为准：如果 checkpoint 写入成功，`base_messages` 中保存 preview；如果 checkpoint 不可用而走 raw events 恢复，则 restore-time budgeter 根据 DB 完整结果重新生成或复用 artifact preview。

预览格式：

```text
<persisted-output>
Full tool output was saved to: /share/.matmaster/tool-results/{session_id}/{task_id}/{call_id}.txt
Tool: Bash
Original size: 5489123 bytes
Preview:
{head}

...

{tail}
</persisted-output>
```

artifact store 规则：

- 本地 session：写入 `workspace_root/.matmaster/tool-results/{session_id}/{task_id}/{call_id}.txt`。
- Bohrium / SSH session：写入远端 workspace root，例如 `/share/.matmaster/tool-results/{session_id}/{task_id}/{call_id}.txt`。
- `session_id`、`task_id`、`call_id` 都必须做安全化：只保留 `[A-Za-z0-9_-]`，其它字符替换为 `_`，单段最大 96 字符。
- 路径拼接后必须做 `resolve()` / realpath 校验，确认最终路径仍位于 `.matmaster/tool-results/` 根目录下。
- 本地文件权限使用 `0600`；远端若无法设置权限，至少路径中带 session scope，且日志中不得打印可能包含 token 的完整内容。
- 写入失败时不能丢失原工具结果；fallback 为现有内存截断策略，并在 `CompactionEvent.failure_reason` 或 `payload` 中标记。

这部分需要尊重项目约定：Bohrium 远端共享目录默认是 project-scoped `/share`，不要偷偷创建 session-scoped `/share/workspace/{session_id}`。

清理策略：

- 不在 run cancel 或失败时立即删除 artifact，因为 checkpoint 或后续 restore 可能仍引用它。
- 由会话删除、工作区清理或后台保留期 GC 删除 `.matmaster/tool-results/{session_id}`。
- Phase 3 必须提供 `list_referenced_tool_result_artifacts(session_id)`，让未来 GC 能区分仍被 checkpoint 引用的 artifact 和可清理 artifact。
- `/share` 是 project-scoped，共享可见性是当前 Bohrium 远端目录语义的一部分；artifact 路径通过 session scope 降低误读风险，但不应存放未脱敏的 access key、token 或 secret。

## 摘要 prompt

新的 summary prompt 使用结构化输出，并采用 analysis / summary 双段：

```text
You are summarizing a long MatMaster scientific computing agent session.

Write an internal <analysis> first to organize facts. Then write the final
<summary>. Only <summary> will be retained in future context.

The summary must preserve exact values, paths, filenames, job ids, tool outcomes,
errors, user constraints, current state, and next steps. Do not invent facts.

<summary> sections:
## User Goal
## Constraints And Preferences
## Completed Work
## Tool Calls And Results
## Files, Paths, And Artifacts
## Errors And Fixes
## Bohrium Or Remote Job State
## Current State
## Next Steps
```

`summary_prompt.py` 负责：

- 将旧消息序列化成摘要输入。
- 将图片、文档、大二进制块替换成 `[image]`、`[document]`、`[binary artifact]`。
- 去掉压缩后会由 rehydration 再注入的重复 skill discovery 或附件内容。
- 从模型输出中提取 `<summary>` 内容。
- 如果模型没有标签，则将完整输出作为 summary，但记录 warning。

## 摘要消息角色与 Provider 兼容

现有 durable compact 会生成：

```text
SystemMessage("[Compacted Context]\n...")
```

原设计曾考虑把 compact summary 改成 `UserMessage`，因为历史摘要不是 system 指令。但该方案会引入连续 user message 风险：

```text
SystemMessage(system_prompt)
UserMessage("[Compacted Context]\n...")
UserMessage(initial task)
```

当前 Bedrock mapper 会合并连续 user messages，但 OpenAI-compatible 的 Anthropic、Gemini、LiteLLM 路由不一定有同样语义。因此 Phase 4 不默认切换到 `UserMessage`。

v2 的第一版做法是引入 compact summary envelope，而不是改变 provider 角色：

```text
Checkpoint content:
{
  "schema_version": 2,
  "summary_role": "system",
  "base_messages": [
    SystemMessage("[Compacted Context]\n...")
  ]
}
```

`summary_role="system"` 表示当前 provider-safe 序列化方式。后续如果要改为 user 或 assistant 角色，必须先通过 provider compatibility gate，并在 checkpoint 中记录 `summary_role`，不能隐式改变旧数据解释。

可选替代方案保留为后续评估：

- `summary_role="user_merged"`：把 compact summary 和 initial task 合并到同一条 user message，避免连续 user，但需要证明不会破坏历史结构。
- `summary_role="assistant"`：把摘要作为 assistant 对历史的整理，但需要证明不会产生连续 assistant 或工具序列问题。
- 保持 `summary_role="system"`：最兼容，代价是摘要仍在 system 通道。

兼容策略：

- `HistoryCheckpointCodec` 继续接受 v1 以 compacted `SystemMessage` 开头的 checkpoint。
- v2 checkpoint 使用 `schema_version: 2` 和 `summary_role`；第一版只写 `summary_role="system"`。
- 恢复后如果是 v1，可以原样保留，不强制迁移。
- 新写入 checkpoint 一律使用 v2。

runtime user instructions 的影响：

- 当前逻辑会查找第一条 `UserMessage` 并注入用户指令。
- 如果 v2 第一版保持 `summary_role="system"`，无需改变注入目标，但必须新增回归测试证明 v2 checkpoint 下仍注入第一条真实 user query。
- 如果后续启用 user summary，则必须跳过 compact summary。判定方式：`message.content.startswith("[Compacted Context]")` 或 checkpoint metadata 标记。
- 该逻辑的具体位置是 `src/services/agent_run_service.py::_apply_user_instructions_to_initial_user_query()`，不是空泛的后续事项。

Phase 4 的 provider compatibility gate 至少包含：

- OpenAIProvider / LiteLLM OpenAI-compatible 路由。
- BedrockProvider 的 `openai_messages_to_bedrock_converse()` 映射。
- Claude/Anthropic 模型路由。
- Gemini 模型路由。
- compact -> checkpoint -> restore -> 下一轮 LLM 调用的端到端 smoke。

## Prompt-too-long 重试

`SummaryRunner` 支持最多 3 次摘要重试：

1. 第一次使用完整可压缩旧历史。
2. 若 provider 返回 prompt too long / context length exceeded，不解析 provider error 文案中的 token gap。
3. 调用 `rounds.truncate_head_for_retry()` 从最旧 API round 开始丢弃。
4. 默认丢弃约 20% 最旧分组；若本地 estimator 明确能算出超过阈值的数量，也只作为辅助，不依赖 provider 文案。
5. 重新发起摘要。
6. 3 次仍失败，则 runtime fallback 到 `sliding_window`；preflight 可以 fallback 到 conservative `sliding_window`，但必须在事件里标记 `failure_reason`。

分组约束：

- 不能拆开 assistant tool call 和对应 tool result。
- 不能保留 orphan tool result。
- 不能生成 provider 无法接受的 tool sequence。
- 分组后必须通过 `normalize_and_validate_openai_messages`。

这里刻意不解析 provider error message。不同 provider 对 context length exceeded 的错误文案差异很大，解析 token gap 会把压缩稳定性绑定到供应商文案。第一版只使用本地估算和固定比例丢弃策略。

## 压缩后 rehydration

第一阶段只实现确定性恢复，不引入后台长期 memory。

rehydration 分为静态 checkpoint rehydration 和动态 runtime rehydration，不能混在一起。

静态 rehydration 会写入 checkpoint `base_messages`，适合不会快速过期的信息：

```text
compact summary
rehydrated skills summary
rehydrated artifact pointers
initial task
recent turns
```

动态 rehydration 不写入 checkpoint，而是在每次 restore 后、下一次 LLM 调用前根据最新事件重新生成：

```text
restored checkpoint base_messages
runtime remote jobs summary
current run user instructions injection
current user prompt
```

第一阶段数据源：

- 已调用 skills：静态。可从 `skill_hit` 和 Skill tool result 恢复 skill 名称，并写入 checkpoint。
- 工具结果 artifacts：静态。由 `ToolResultBudgeter` 生成 artifact manifest，并写入 checkpoint。
- Bohrium job 状态：动态。复用现有 `get_bohrium_events()` 和 `JobRegistry.rebuild_from_events()` 语义，每次 run 重新构建，不把可能过期的 job 状态快照固化进 checkpoint。
- 用户 instructions：动态。保留现有运行时注入机制；如果未来 compact summary 采用 user 角色，注入逻辑必须跳过 compact summary。

这意味着 `history_checkpoint.base_messages` 是恢复核心上下文，不一定等于最终模型可见上下文。最终模型可见上下文是：

```text
checkpoint base_messages
+ runtime rehydration messages
+ current user prompt
```

实现上需要明确一个边界：`HistoryRestoreService` 只负责 checkpoint core + tail events；动态 rehydration 应在 `AgentRunService` 或 `Exp.build_runtime()` 附近完成，靠 `run_meta` 和现有 registry 重建能力注入。

不在第一阶段注入：

- 最近读过文件的完整内容。
- plan mode 状态。
- MCP schema diff。
- 后台 session memory markdown。

这些能力需要独立设计，避免本次重构范围失控。

## 事件模型

保留公开事件类型 `compaction`，扩展 payload 字段：

```python
class CompactionEvent(EventBase):
    type: Literal["compaction"] = "compaction"
    compaction_id: str
    status: Literal["running", "complete", "interrupted"]
    phase: Literal["preflight", "runtime"]
    trigger: Literal["auto", "manual", "preflight", "runtime"] | None = None
    strategy: Literal["summary", "sliding_window", "tool_truncation", "tool_result_budget"] | None = None
    durability: Literal["durable", "ephemeral"] | None = None
    trigger_tokens: int | None = None
    pre_tokens: int | None = None
    post_tokens_estimated: int | None = None
    retained_turns: int | None = None
    checkpoint_written: bool | None = None
    covered_until_event_id: int | None = None
    summary_retry_count: int | None = None
    tool_results_persisted: int | None = None
    failure_reason: str | None = None
```

其中 `manual` 是为后续 `/compact` 入口预留的事件值，Phase 1-5 不要求产生手动 compact 事件。

兼容规则：

- 回放时继续隐藏 `history_checkpoint`。
- 回放时继续丢弃 legacy `context_compaction`。
- 孤儿 `compaction running` 继续归一化为 `interrupted`。
- 前端即使忽略新增字段，也能继续根据 `status`、`strategy`、`phase` 展示。

## 持久化与恢复

`history_checkpoint` 内容升级为 v2：

```json
{
  "schema_version": 2,
  "summary_role": "system",
  "covered_until_event_id": 42,
  "base_messages": [],
  "reason": "summary",
  "compaction": {
    "compaction_id": "task:root:1",
    "strategy": "summary",
    "pre_tokens": 168000,
    "post_tokens_estimated": 42000,
    "tool_results_persisted": 3
  }
}
```

恢复逻辑：

1. 优先读取最新 valid checkpoint。
2. 通过 `decode_history_checkpoint_content()` 读取 `schema_version`。
3. v1 走旧校验规则：`base_messages[0]` 必须是 compacted `SystemMessage`。
4. v2 走新校验规则：检查 `summary_role`、compact summary envelope、tool sequence、artifact manifest。
5. tail events 查询继续排除 `history_checkpoint`、`compaction`、`context_compaction`。
6. 如果 checkpoint 无效，尝试更老 checkpoint。
7. 都失败时退回 raw events 恢复。

`covered_until_event_id` 仍必须基于 `fanout.flush_persistence_barrier()` 之后的最新业务事件 id，避免 checkpoint 越过未落库事件。

raw events fallback 必须经过 restore-time budgeter。否则 checkpoint 失败时，DB 中完整 `tool_result` 会重新进入模型上下文，抵消工具结果预算的效果。预算输出如果能复用既有 artifact path 就复用；artifact 缺失时可以从 DB 完整内容重新写入 artifact。

## 错误处理

错误处理遵循项目 AGENTS.md 约定：DAO 不吞异常；服务层除明确降级外让异常向上抛；压缩链路中的可降级错误必须写清原因。

具体策略：

- 工具结果 artifact 写入失败：不丢原始内容，fallback 到内存截断，`CompactionEvent.failure_reason` 记录简短原因。
- summary LLM 返回空内容：runtime fallback `sliding_window`；preflight 可按配置 fallback 或抛错，默认 fallback 以避免服务卡死。
- prompt too long：最多 3 次有损重试。
- checkpoint 写入失败：压缩仍完成，`checkpoint_written=false`，`failure_reason` 写入 complete event。
- checkpoint 恢复失败：尝试旧 checkpoint；最后退回 raw events。

checkpoint 写入失败的真实语义必须写进实现注释和事件说明：本次 run 内存中的 `messages` 已经被压缩并会继续运行，但下一次冷启动或新 Worker restore 时，因为没有 durable checkpoint，会从 raw events 恢复，相当于回到 compact 前的长历史。用户可见表现可能是下一轮再次触发 compact，或者在 raw restore + budgeter 之前再次接近 token 硬限制。

处理策略：

- checkpoint 写失败不回滚当前 run 的内存压缩。
- complete event 必须包含 `checkpoint_written=false` 和简短 `failure_reason`。
- 可以在同一 run 内做一次 checkpoint 写入重试，但不能无限重试或阻塞 LLM 继续执行。
- raw restore fallback 必须经过 tool result budgeter 和 token budget 判断，降低再次超限概率。

## 测试策略

保留并扩展现有测试集：

```bash
uv run pytest tests/matmaster/core/test_context_compactor.py
uv run pytest tests/matmaster/core/test_agent_kernel_compaction.py
uv run pytest tests/matmaster/services/test_history_checkpoint_service.py
uv run pytest tests/matmaster/services/test_history_restore_service.py
uv run pytest tests/test_stream_replay_skill_hit.py
```

新增测试覆盖：

- token budget 公式产生 167k 默认自动压缩阈值。
- `ToolResultBudgeter` 将大工具结果写入 artifact，并保留合法 `ToolMessage`。
- raw events restore fallback 会重新应用 tool result budgeter，不把完整大结果直接送入模型上下文。
- artifact 写入失败时 fallback，不丢失工具结果。
- v1 `SystemMessage` checkpoint 仍可恢复。
- v2 `summary_role="system"` checkpoint 可恢复。
- runtime user instructions 在 v2 checkpoint 下仍注入第一条真实 user query。
- prompt-too-long 重试按分组丢弃旧 round，且不拆断 tool call / tool result。
- `summary_prompt.py` 能剥离 `<analysis>`，只保留 `<summary>`。
- `compaction` 新字段可序列化、持久化、SSE 回放。
- ephemeral `sliding_window` 和 `tool_truncation` 不写 `history_checkpoint`。

新增端到端测试覆盖：

- 长会话超过 50 个 API round：compact -> checkpoint -> restore -> 再次 compact。
- 父 agent 与子 agent 各自 compact，`spawn_id` 隔离不串。
- checkpoint 写入后模拟 API / Worker 重启，新进程 restore 能复用 checkpoint。
- v1 checkpoint 与 v2 checkpoint 混合存在时，优先使用最新 valid checkpoint；最新 v2 坏掉时能 fallback 到旧 v1。
- provider compatibility smoke：OpenAIProvider、BedrockProvider、Claude/Anthropic 路由、Gemini 路由至少跑 compact -> restore -> 下一轮 LLM 调用。

## 可观测性

新增字段不只是给前端展示，也要能进入日志和监控。至少记录：

- `summary_retry_count > 0` 的比例：衡量 prompt-too-long 发生频率。
- `failure_reason != null` 的比例：衡量压缩降级和 checkpoint 失败频率。
- `tool_results_persisted` 分布：衡量工具结果预算的工作量。
- `pre_tokens / post_tokens_estimated` 比值分布：衡量压缩效率。
- `checkpoint_written=false` 的数量和 session 分布：定位 DB / codec / barrier 问题。
- artifact 写入失败数量：区分本地权限、远端 session 能力和路径安全校验失败。

## 分阶段实施

### Phase 1: 无行为重构

创建 `matmaster/core/compaction/` 包，将 token 估算、turn 分组、summary prompt 生成等逻辑从 `context_compactor.py` 抽出。外部行为保持不变。

验收：

- 现有 compaction 测试全部通过。
- `ContextCompactor` 外部接口不变。

### Phase 2: Token 预算

引入 reserved summary tokens、auto compact buffer 和 provider safety ratio。保留旧配置兼容；不引入 manual compact 字段。

验收：

- 默认自动压缩阈值为 167000。
- Claude/Bedrock 中文长会话 smoke 不撞 provider 硬限制。
- 旧测试按新阈值更新。
- 不改变 checkpoint schema。

### Phase 3: 工具结果预算

实现 `ToolResultArtifactStore` 和 `ToolResultBudgeter`，先支持通用文本 `ToolMessage`。接入 full compact 前置流程。

验收：

- 大工具结果写入 `.matmaster/tool-results/`。
- 模型上下文只保留预览和路径。
- raw restore fallback 也应用 budgeter。
- 现有 `_truncate_tool_results()` 保留为 artifact 写入失败或不支持 artifact store 时的 fallback。
- 工具调用序列仍通过 provider 消息校验。

### Phase 4: v2 summary 和 checkpoint

引入结构化摘要 prompt，checkpoint 写入 `schema_version=2` 和 `summary_role="system"`。恢复端通过 codec decoder 兼容 v1 / v2。暂不把 summary 改成 `UserMessage`。

验收：

- 新 checkpoint 使用 v2。
- 旧 checkpoint 不需要迁移即可恢复。
- user instructions 在 v2 checkpoint 下仍注入第一条真实 user query。
- 连续 user message 兼容风险被测试覆盖；默认实现不产生新增连续 user。

### Phase 5: prompt-too-long 重试与 rehydration

实现摘要重试和最小 rehydration：静态 skills、tool result artifacts 写入 checkpoint；Bohrium job 状态作为动态 runtime rehydration 每轮重建。

验收：

- summary prompt too long 时最多重试 3 次。
- 重试不破坏 tool call / result 配对。
- 压缩后上下文中能看到关键 artifact。
- 每次 restore 后能看到最新 Bohrium job 状态摘要，但该摘要不固化进 checkpoint。

## 后续扩展

在上述稳定后，可以单独设计：

- 手动 `/compact` API 和 UI 入口。
- partial compact。
- session memory markdown extraction。
- 最近文件读取缓存和注入。
- plan mode / planner artifact rehydration。
- 更细的 UI 展示，例如折叠 compact summary、展示 pre/post token 差异。

## 自检

- 没有依赖同级其它仓库的代码修改。
- 没有要求改写历史 commit。
- 没有改变 API / Worker 分离假设。
- 没有让 `history_checkpoint` 进入普通 SSE 回放。
- 没有要求第一阶段实现 session memory 或 partial compact。
- 新增配置均有默认值，旧 exp 配置可继续加载。
- v1 checkpoint 兼容路径明确。
