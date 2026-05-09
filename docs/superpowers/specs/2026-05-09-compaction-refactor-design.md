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
- 压缩后恢复现场只依赖摘要和最近 turns，没有稳定的 rehydration 层来重新注入 skills、Bohrium job、workspace artifacts 等工作状态。
- 当前 durable compact summary 使用 `SystemMessage`，这会把历史摘要提升到 system 优先级，不如使用普通用户级历史消息安全。

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
| compact summary 用户消息 | v2 compact summary `UserMessage` |
| UI 显示 `Conversation compacted` | SSE 回放公开 `compaction` 事件 |
| transcript 保留完整历史 | `evo_chat_events` 持久化事件 |
| 模型只看 boundary 之后上下文 | checkpoint `base_messages` + covered id 之后的 tail events |

核心判断是：MatMaster 不需要照搬 Claude Code 的 system boundary message。MatMaster 的 DB checkpoint 已经天然承担切断模型历史的职责。公开 `compaction` 事件负责用户可见状态，内部 `history_checkpoint` 负责模型恢复状态。

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

- `budget.py`：计算有效上下文窗口、自动压缩阈值、手动 compact 阻断阈值。
- `rounds.py`：按 turn / API round 对消息分组，保证 assistant tool call 与 tool result 不被拆开。
- `tool_result_budget.py`：将超大工具结果写入 artifact store，并把模型上下文中的结果替换为预览和路径。
- `summary_prompt.py`：生成结构化摘要 prompt，并从模型输出中提取最终 `<summary>`。
- `summary_runner.py`：调用摘要 LLM，处理 prompt-too-long 重试和最终 fallback。
- `rehydration.py`：压缩后重新注入确定性工作现场，例如已调用 skills、Bohrium job 状态、关键 workspace artifact。
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

## Token 预算

新增 `CompactionBudgetConfig` 字段，合并进现有 `CompactionConfig`：

```python
class CompactionConfig(BaseModel):
    context_limit: int = 200_000
    trigger_ratio: float = 0.9
    strategy: str = "summary"
    compaction_llm: str | None = None
    summary_reserved_tokens: int = 20_000
    auto_compact_buffer_tokens: int = 13_000
    manual_compact_buffer_tokens: int = 3_000
    tool_result_budget_tokens: int = 32_000
    tool_result_preview_tokens: int = 4_000
```

实际阈值计算：

```text
reserved = min(summary_reserved_tokens, int(context_limit * 0.1))
effective_window = context_limit - reserved
auto_compact_threshold = effective_window - auto_compact_buffer_tokens
manual_compact_block_threshold = context_limit - manual_compact_buffer_tokens
```

默认情况下：

```text
context_limit = 200000
reserved = 20000
effective_window = 180000
auto_compact_threshold = 167000
manual_compact_block_threshold = 197000
```

`trigger_ratio` 在迁移期保留，但内部优先使用新预算公式。若旧配置只设置了 `trigger_ratio`，仍能按旧逻辑近似工作。

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

预览格式：

```text
<persisted-output>
Full tool output was saved to: /share/.matmaster/tool-results/{task_id}/{call_id}.txt
Tool: Bash
Original size: 5489123 bytes
Preview:
{head}

...

{tail}
</persisted-output>
```

artifact store 规则：

- 本地 session：写入 `workspace_root/.matmaster/tool-results/{task_id}/{call_id}.txt`。
- Bohrium / SSH session：写入远端 workspace root，例如 `/share/.matmaster/tool-results/{task_id}/{call_id}.txt`。
- 文件名必须只使用 task id 和 tool call id 的安全字符版本。
- 写入失败时不能丢失原工具结果；fallback 为现有内存截断策略，并在 `CompactionEvent.failure_reason` 或 `payload` 中标记。

这部分需要尊重项目约定：Bohrium 远端共享目录默认是 project-scoped `/share`，不要偷偷创建 session-scoped `/share/workspace/{session_id}`。

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

## 摘要消息角色

现有 durable compact 会生成：

```text
SystemMessage("[Compacted Context]\n...")
```

v2 改为：

```text
UserMessage("[Compacted Context]\n...")
```

原因：

- 历史摘要是用户级会话事实，不应获得 system prompt 的优先级。
- 第一条 system prompt 应继续由 `AgentKernel` 每轮从当前 exp 的 `system_prompt` 生成。
- 这能避免旧摘要中的措辞意外覆盖当前系统约束。

兼容策略：

- `HistoryCheckpointCodec` 继续接受 v1 以 compacted `SystemMessage` 开头的 checkpoint。
- v2 checkpoint 使用 `schema_version: 2`，接受以 compact summary `UserMessage` 开头的 `base_messages`。
- 恢复后如果是 v1，可以原样保留，不强制迁移。
- 新写入 checkpoint 一律使用 v2。

需要同步修复 runtime user instructions 注入逻辑：

- 当前逻辑会查找第一条 `UserMessage` 并注入用户指令。
- v2 compact summary 也是 `UserMessage`，所以必须跳过 compact summary。
- 判定方式：`message.content.startswith("[Compacted Context]")` 或 message metadata 标记。

## Prompt-too-long 重试

`SummaryRunner` 支持最多 3 次摘要重试：

1. 第一次使用完整可压缩旧历史。
2. 若 provider 返回 prompt too long / context length exceeded，则估算 token gap。
3. 调用 `rounds.truncate_head_for_retry()` 从最旧 API round 开始丢弃。
4. 若没有 token gap，则默认丢弃约 20% 最旧分组。
5. 重新发起摘要。
6. 3 次仍失败，则 runtime fallback 到 `sliding_window`；preflight 可以 fallback 到 conservative `sliding_window`，但必须在事件里标记 `failure_reason`。

分组约束：

- 不能拆开 assistant tool call 和对应 tool result。
- 不能保留 orphan tool result。
- 不能生成 provider 无法接受的 tool sequence。
- 分组后必须通过 `normalize_and_validate_openai_messages`。

## 压缩后 rehydration

第一阶段只实现确定性恢复，不引入后台长期 memory。

`rehydration.py` 生成额外 base messages，可追加在 compact summary 之后、最近 turns 之前：

```text
compact summary
rehydrated skills summary
rehydrated remote jobs summary
rehydrated artifact pointers
initial task
recent turns
```

第一阶段数据源：

- 已调用 skills：从 `skill_hit` 和 Skill tool result 恢复 skill 名称。
- Bohrium job 状态：复用现有 `get_bohrium_events()` 和 `JobRegistry.rebuild_from_events()` 语义。
- 工具结果 artifacts：由 `ToolResultBudgeter` 生成的 artifact manifest。
- 用户 instructions：保留现有运行时注入机制，但跳过 compact summary。

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
2. v2 走新校验规则。
3. v1 走旧校验规则。
4. tail events 查询继续排除 `history_checkpoint`、`compaction`、`context_compaction`。
5. 如果 checkpoint 无效，尝试更老 checkpoint。
6. 都失败时退回 raw events 恢复。

`covered_until_event_id` 仍必须基于 `fanout.flush_persistence_barrier()` 之后的最新业务事件 id，避免 checkpoint 越过未落库事件。

## 错误处理

错误处理遵循项目 AGENTS.md 约定：DAO 不吞异常；服务层除明确降级外让异常向上抛；压缩链路中的可降级错误必须写清原因。

具体策略：

- 工具结果 artifact 写入失败：不丢原始内容，fallback 到内存截断，`CompactionEvent.failure_reason` 记录简短原因。
- summary LLM 返回空内容：runtime fallback `sliding_window`；preflight 可按配置 fallback 或抛错，默认 fallback 以避免服务卡死。
- prompt too long：最多 3 次有损重试。
- checkpoint 写入失败：压缩仍完成，`checkpoint_written=false`，`failure_reason` 写入 complete event。
- checkpoint 恢复失败：尝试旧 checkpoint；最后退回 raw events。

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
- artifact 写入失败时 fallback，不丢失工具结果。
- v1 `SystemMessage` checkpoint 仍可恢复。
- v2 `UserMessage` compact summary 可恢复。
- runtime user instructions 不注入 compact summary。
- prompt-too-long 重试按分组丢弃旧 round，且不拆断 tool call / tool result。
- `summary_prompt.py` 能剥离 `<analysis>`，只保留 `<summary>`。
- `compaction` 新字段可序列化、持久化、SSE 回放。
- ephemeral `sliding_window` 和 `tool_truncation` 不写 `history_checkpoint`。

## 分阶段实施

### Phase 1: 无行为重构

创建 `matmaster/core/compaction/` 包，将 token 估算、turn 分组、summary prompt 生成等逻辑从 `context_compactor.py` 抽出。外部行为保持不变。

验收：

- 现有 compaction 测试全部通过。
- `ContextCompactor` 外部接口不变。

### Phase 2: Token 预算

引入 reserved summary tokens 和 buffer 公式。保留旧配置兼容。

验收：

- 默认自动压缩阈值为 167000。
- 旧测试按新阈值更新。
- 不改变 checkpoint schema。

### Phase 3: 工具结果预算

实现 `ToolResultArtifactStore` 和 `ToolResultBudgeter`，先支持通用文本 `ToolMessage`。接入 full compact 前置流程。

验收：

- 大工具结果写入 `.matmaster/tool-results/`。
- 模型上下文只保留预览和路径。
- 工具调用序列仍通过 provider 消息校验。

### Phase 4: v2 summary 和 checkpoint

引入结构化摘要 prompt，summary 改为 `UserMessage`，checkpoint 写入 `schema_version=2`。恢复端兼容 v1 / v2。

验收：

- 新 checkpoint 使用 v2。
- 旧 checkpoint 不需要迁移即可恢复。
- user instructions 注入跳过 compact summary。

### Phase 5: prompt-too-long 重试与 rehydration

实现摘要重试和最小 rehydration：skills、Bohrium job 状态、tool result artifacts。

验收：

- summary prompt too long 时最多重试 3 次。
- 重试不破坏 tool call / result 配对。
- 压缩后上下文中能看到关键 artifact 和 job 状态摘要。

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
