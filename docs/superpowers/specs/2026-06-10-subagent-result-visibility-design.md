# Subagent 交付物可见性设计

- 日期: 2026-06-10
- 状态: 根因分析完成，方案待评审
- 范围: 跨仓 — `scimaster-bohr-chat` 前端（主要改动）+ `matmaster-evo` 后端（仅 prompt 文案）
- 作者: Kealdoom + Claude

## 1. 背景与问题陈述

主 agent 通过 AgentTool（工具名 `Agent`）派生子代理执行任务。子代理的最终输出装进 `ToolResult.content` 作为 tool_result 返回主 agent。用户侧现象：前端看不到子代理的成果。

本设计基于对两仓完整链路的逐文件核查，先给出证据链与三层根因，再给方案。

## 2. 事实链（已逐条核实）

### 2.1 后端 matmaster-evo

1. **工具名是 `Agent`**：`matmaster/tools/builtin/agent_tool.py:23`。`execute()` 经 `spawn_fn` 进入 `SubagentOrchestrator.spawn()`（`matmaster/core/subagent_orchestrator.py:72-113`）。
2. **子代理中间事件全部转发**：每个 child 事件被 retag `source="MatMaster:{exp_name}"` + `spawn_id` 后多路复用回 parent 流（`subagent_orchestrator.py:85-102`）。终态 `RunResultEvent` 例外 —— `drain_run_stream` 遇到它直接 return，不经 `on_event` 转发（`matmaster/core/stream_drain.py:30-44`），因此子代理的 run_result 不进 SSE 也不持久化。
3. **子代理最终文本存在两份载体**：
   - 转发的 `ResponseEvent` 流（streaming 增量 + segment_end 快照，`matmaster/core/agent_llm_stream.py:133-218`）→ 前端拼进 `<evo-sub-agent>` 过程块；
   - `ToolResultEvent(tool_name="Agent", result=final_content, payload={exp_name, task_summary, prompt, subagent_usage, subagent_status, ...})`（`agent_tool.py:215-232`，字段定义 `matmaster/types/events.py:73-85`）。
4. **SSE 与回放都不丢 tool_result**：实时路径过滤名单不含 tool_result；回放黑名单 `REPLAY_DISCARDED_EVENT_TYPES`（`src/services/stream_sse_filter.py:20-29`）同样不含。回放终态去重按 `(task_id, spawn_id)` 分键（`stream_sse_filter.py:109-161`），子代理流没有 run_result，其 response 段回放时不会被去重隐藏。
5. **复述责任在主 agent**：AgentTool prompt 写明 "When the subagent is done, the result comes back to you, not directly to the user. If the user should see it, send a concise summary yourself."（`agent_tool.py:132`）。这是无强制力的建议。

### 2.2 前端 scimaster-bohr-chat

事件不构造独立消息，而是序列化为 HTML 占位符拼进 assistant 消息的 content 字符串，渲染时再 tokenize 还原（`features/agent-message/markup.ts`）。

6. **dispatch 层对 `Agent` 无特判，数据完整落地**：
   - tool_call：走通用路径，生成 `<tool-call name="Agent">` 占位符、登记 callId→messageId 映射、进侧栏列表（`hooks/evo-sse-handler/dispatch/tool-call.ts:91-121`）；
   - tool_result：spawn/TodoWrite/finish 特判均不命中 `Agent`，正常生成 `<tool-result name="Agent" result="...">` 占位符并 append 到 tool_call 所在消息，同时写入侧栏 store（`dispatch/tool-result.ts:74-216`）。
   - 即：**子代理最终输出已完整进入消息字符串与侧栏 store，数据没有丢**。
7. **渲染层吞掉了它（直接根因）**：
   - `features/agent-message/parse.tsx:602-608`：tool-call token 配对到 tool-result token 后 `consumedResultIndexes.add(pairedResultIndex)`；
   - `parse.tsx:662-698`：`isEvoMatMasterAgentToolName`（`markup.ts:98`，匹配 `'agent'`）命中后渲染 `AgentToolSubAgentStyleRow`，只传 `argsObj`、`hasResult`（布尔）、`subAgentSlot` —— **`resultToken.parsedData` 没有传入**（对比通用分支 `ToolCallResultCard` 的 `result={resultToken.parsedData}`，`parse.tsx:700-719`）；
   - `parse.tsx:830`：已消费的 result token 跳过独立渲染。
8. **组件没有 result 渲染段**：`AgentToolSubAgentStyleRow` 展开区只渲染 Prompt 与子代理过程块（`features/agent-message/segment-rows.tsx:182-263`）。
9. **侧栏不自动弹详情**：`shouldShowEvoToolCallDetailView` 是白名单制，不含 `Agent`（`utils/evo-tool-call-sidebar-sync.ts:129+`）。
10. **过程块默认折叠**：子代理 ResponseEvent 文本在 `<evo-sub-agent>` 块内（`EvoSubAgentCollapsibleBlock defaultExpanded={false}`），外层 `AgentToolSubAgentStyleRow` 默认折叠且后续出现其它工具调用时 autoCollapse（`segment-rows.tsx:211-227`）。

## 3. 根因分析（三层）

1. **直接原因（前端渲染缺口）**：渲染层把 `Agent` 的 tool-result token「配对吸收但零渲染」——token 被标记消费跳过独立渲染，专用组件又不接收 result 数据。子代理交付物在 UI 上没有任何渲染出口。
2. **结构性原因（语义契约缺失）**：「子代理交付物」没有一等公民的 UI 契约。后端视 tool_result 为给主 agent 的机器语义载荷；前端把 Agent 卡片定位为「过程容器」（Prompt + 过程块）。两端都没有承担「把交付物呈现给用户」的职责。
3. **行为原因（兜底缺失）**：架构预期由主 agent 在下一轮 LLM 输出中复述成果（`agent_tool.py:132`），但这是 prompt 级建议，LLM 不复述（直接结束 turn、或只说一句"子代理已完成"）时，用户彻底看不到成果。

修正一处普遍直觉：问题不是「tool_result 事件被前端丢弃」。事件完整到达前端、进了消息字符串和侧栏 store，实时与回放均在 —— 是渲染层主动吞掉。

## 4. 方案空间与取舍

| 方案 | 内容 | 结论 |
|------|------|------|
| A | 前端为 `Agent` 的 tool_result 打开渲染出口 | **推荐**，改动小、实时/回放天然一致 |
| B | 后端新增独立事件（如 `subagent_result`）或伪造 response 事件 | 排除：final_content 将出现三份载体（response 流、tool_result、新事件），回放去重复杂化，且破坏「结果回到主 agent」的架构语义 |
| C | 收紧 AgentTool prompt 的复述要求 | 辅助手段，与 A 并行；单独做不可靠 |
| D | 默认展开 evo-sub-agent 过程块 | 排除：过程 ≠ 交付物，长过程流刷屏，且回放/实时观感不一致 |

设计立场：**机制兜底（A）+ 行为引导（C）双保险**。UI 保证成果永远可达；主 agent 复述提供自然对话体验的第一眼可见性。

## 5. 推荐设计

### 5.1 前端：交付物渲染出口（scimaster-bohr-chat，2 个文件）

1. `parse.tsx` Agent done 分支（662-698 行）把 `resultToken.parsedData` 与 `resultToken.status` 传入 `AgentToolSubAgentStyleRow`。
2. `segment-rows.tsx` `AgentToolSubAgentStyleRow` 增加交付物段，展开区顺序：Prompt → 子代理过程块 → **Result**。
   - result 提取：`parsedData` 通常为字符串（即 `ToolResult.content` = 子代理 final_content）；为对象时取 `content`/`result` 字段，最后 `JSON.stringify` 兜底（实现时以 `parseToolResultPayload` 实际返回为准）。
   - 渲染：复用现有 Markdown 渲染组件（子代理输出按 Markdown 处理），与过程块在视觉上区分（标题"Result"，对应 Prompt 段样式）。
3. pending 分支（757-789 行）无 result，不动。
4. 失败路径自动覆盖：`drain.status != "completed"` 时 content 为 `"SubAgent finished with status=..., reason=..."`（`agent_tool.py:215-219`），同样经此出口可见。
5. 回放一致性免费获得：占位符已在持久化消息重建路径中，无需额外处理。

### 5.2 后端：prompt 行为收紧（matmaster-evo，文案级）

`agent_tool.py:132` 现文案基于「用户看不到结果」的旧事实，且复述是可选语气。改为明确要求 + 修正事实，方向示例：

> "The subagent's full output is shown to the user only inside a collapsed card. Always present the key findings or deliverable to the user in your own reply — do not assume they have read the raw result."

### 5.3 配套清理（独立任务，scimaster-bohr-chat）

旧协议 `spawn` 工具名残留已是死代码（`matmaster/tools/` 下已无名为 spawn 的工具），且造成功能退化：

- `dispatch/tool-call.ts:65-73`（spawn 特判 + 入队）、`dispatch/tool-result.ts:74-77`（spawn 丢弃）；
- `pendingSubAgentSpawnTaskQueueRef` 全链路与 `<evo-sub-agent>` 的 `task` 属性（`sub-agent.ts:26-28`）：队列永远为空 → `delegatedTask` 恒缺失；该信息已由 `AgentToolSubAgentStyleRow` 从 `argsObj`（task_summary/prompt）覆盖。

按「迁移优于兼容、删除死代码」的项目偏好整链移除，应在主修复合入后单独做。

## 6. 留给实现的决策点

1. 交付物段默认折叠（推荐，与现 UI 一致，第一眼可见性由主 agent 复述承担）还是 `hasResult` 时默认展开。
2. 是否在折叠行 label 旁显示 result 首行摘要（体验增强，非必需）。
3. 侧栏是否为 `Agent` 增加详情视图（主消息流已有出口后，倾向不做）。

验证方式：手动触发一次子代理任务，确认实时流中 Agent 卡片展开可见交付物；刷新会话后回放路径同样可见；构造一次子代理失败（status != completed）确认失败说明可见。
