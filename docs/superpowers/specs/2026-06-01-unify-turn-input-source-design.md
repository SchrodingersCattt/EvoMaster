# 统一 root turn 与 preflight 压缩的当轮输入来源

日期: 2026-06-01
分支: refactor/context
状态: 第一阶段已落地; 第二阶段候选 1 已实现

## 1. 目标

修正 preflight 压缩中当轮输入来源不一致的问题, 并把本文件早期草稿中已经被
近期 refactor 覆盖的判断改写为当前 checkout 事实。

本 spec 的目标是:

- 明确当前 root run 的真实调用链和数据源边界。
- 防止 preflight current split 丢失或降级当轮用户 query。
- 保留 `TurnInput` 作为语义化当轮输入, 避免把已渲染 prompt 文本重新塞回
  `TurnInput.instruction.user_text`。
- 退役 `AgentKernel.run_stream(..., task=...)` 裸字符串入口, 改由
  `AgentKernelTurnRequest` 承载本次 kernel 调用的 rendered content 和
  typed current input。

## 2. 当前事实与修订范围

当前主路径已经不是旧的 kernel-only 三段式拼装。生产 root run 的真实链路是:

```text
AgentRunService.run_agent
  -> AgentRunContext(request=AgentRunRequest(turn_input, user_instructions, ports, ...))
  -> Exp.run_stream(ctx, history=history)
  -> resolve_turn_intent(...)
  -> _render_and_persist_root_turn(...)
  -> ContextAssembler.assemble_turn(...)
  -> AgentKernel.run_stream(kernel_runtime, turn_request, history=history)
```

关键事实:

- `AgentRunService` 已构造 `AgentRunRequest.turn_input`、`user_instructions` 和
  `AgentRunPorts`。
- `build_history_wiring()` 已提供真实 history port。该对象在
  `AgentRunPorts.compaction.history` 下传递, 在 `runtime_context_assembly.py`
  组装进 `ContextAssemblyPorts` 时成为 `session_events` port。
  `_RunSessionEventHistory.load_events()` 通过 `events_table.query_context_events()`
  查询事件, 再由 `decode_session_events()` 转成 typed events。
- `resolve_turn_intent()` 已调用 `decide_turn_context_intent()`, 并从
  `user_turn_context` / `history_checkpoint` 事件推导 latest anchor hash。
- root run 缺 `ctx.request.turn_input` 会在 `Exp.run_stream()` 中 fail-fast。
- `assemble_turn()` 已在 root run 生产路径被调用。
- 当前仍接近空实现的是 `session_jobs`, 不是整个 context assembly 的
  service backing。

这些事实来自最近一轮 root-turn-rendering 下沉到 `Exp` 的 refactor
(`b8790c81`、`da4d5349`、`c01b6be5` 一线)。因此, 本文件覆盖的是早期草稿中的
过时判断, 不是在纠正同目录 v2 orchestrator 文档:

- 早期草稿若说 `assemble_turn` / `ANCHOR_COMPOSITION` /
  `CONTINUATION_COMPOSITION` / `decide_turn_context_intent` 在生产零调用, 已经过时。
- 早期草稿若说第一阶段集中改 `agent.py:_run_items` 就能安全统一, 已经过时。
- 早期草稿若说仓库内没有真实 history/session events port 或 latest anchor hash
  查询来源, 已经过时。

## 3. 当前数据源分层

当前系统里有三个不同层次的数据源, 它们不能混成一个字符串。

| 层次 | 当前载体 | 语义 |
|------|----------|------|
| 语义输入 | `AgentRunRequest.turn_input` / `AgentKernelTurnRequest.turn_input` | 用户当轮输入的结构化来源, 包括 `instruction.user_text`、files、images、workspace paths、`pre_turn_history_event_id`。 |
| root 已渲染输入 | `RootTurnRender.rendered_content` / `AgentKernelTurnRequest.user_message_content` | `ContextAssembler.assemble_turn()` 产出的 runtime prompt 文本, 可能包含 user instructions、session sections、当前指令、附件清单。 |
| kernel 执行消息 | `state.messages[-1]` | 实际送入模型和 preflight summary call 的最后一条 `UserMessage`。当前由 `UserMessage(content=turn_request.user_message_content, images=turn_images)` 构造。 |

这三者在标准 root path 下本来就不应简单相等:

- `turn_input.user_text` 是只读 property, 返回 `turn_input.instruction.user_text`;
  真正的 dataclass 字段在 `instruction.user_text`。
- `task` 可能已经是带 tag 的完整 root turn prompt。
- `messages[-1]` 是 kernel 的执行消息, 还会经过 `USER_PROMPT_SUBMIT`
  rewrite hook 修改。

正确设计不是强行让三者共用一个字符串, 而是保证每一层的 canonical source
明确, 且 preflight split 后能用同一组 typed sources 重建当前模型上下文。

## 4. 问题定义

preflight 压缩在 `current_split=True` 时会把最后一条 user message 从摘要输入中
分离:

```python
current_split = (
    phase == "preflight"
    and turn_input is not None
    and turn_input.has_effective_input()
    and len(full_messages) >= 3
    and isinstance(full_messages[-1], UserMessage)
    and bool(full_messages[1:-1])
)
base_messages = full_messages[:-1] if current_split else full_messages
```

同一个 `current_split` 谓词当前有两份拷贝:

- `_summary_base_messages()` 用它决定 summary call 是否排除最后一条 user message。
- `ContextCompactor.apply_summary()` 用它决定 compacted prompt 是否按
  `PREFLIGHT_COMPACTION` 贴回 `turn_input`。

随后 `apply_summary()` 会用 `turn_input` 重新组装 compacted prompt:

```python
turn_input=turn_input if current_split else None
```

这两处谓词必须保持同步。只要一边 split、一边不 reattach, 当前轮就会丢失; 反过来
则可能重复注入当前轮。实现阶段应优先抽出共享 helper, 或至少用一组测试同时覆盖
summary-input slice 与 apply-summary intent。

这条机制的安全前提不是 `messages[-1] == turn_input.instruction.user_text`, 而是:

1. `messages[-1]` 中属于当前用户输入的部分必须能由 `turn_input` 重建。
2. `messages[-1]` 中属于 session context 的部分必须能由 context assembly ports
   和 `user_instructions` 重建。
3. kernel 层不得在 root turn 已渲染后再做不可回流到 typed sources 的 prompt
   rewrite。

当前风险集中在第 3 点。

当前 checkout 中, 非测试代码没有注册 `HookEvent.USER_PROMPT_SUBMIT` rewriter:
`HookExecutor` 支持 rewrite, `AgentKernel` 也会无条件调用 `emit_rewrite()`, 但
`executor.rewrite(HookEvent.USER_PROMPT_SUBMIT, ...)` 只在测试中出现。因此, 这不是
已确认正在生产触发的数据丢失, 而是对公开扩展点的预防性边界加固。若后续任何生产
hook 注册了 user-prompt rewrite, 这个风险会立刻变成可触发 bug。

`AgentKernel._run_items()` 会先执行 `USER_PROMPT_SUBMIT` rewrite, 再构造
`state.messages[-1]`。但是传给 preflight compaction 的 typed current input 来自
`AgentKernelTurnRequest.turn_input`。如果 rewrite hook 给
`user_message_content` 注入了额外指令, 这些额外内容只存在于 `messages[-1]`,
不存在于 `turn_input`、`user_instructions` 或 session events。
`current_split=True` 时, summary call 会排除 `messages[-1]`, `apply_summary()`
又只从 typed sources 贴回, rewrite 注入内容就会丢失。

另外, 对 direct kernel 调用或非 root 路径来说, 如果
`AgentKernelTurnRequest.user_message_content` 有当前 query 但 `turn_input` 为空,
preflight 不会 split 当前消息, query 会被卷入摘要, 从当前指令语义降级为历史摘要的
一部分。生产 root path 已经通过 `Exp.run_stream()` 的 turn_input fail-fast 降低了
这个风险, 但 kernel public API 仍然允许显式传入缺失 typed current input 的 request。

## 5. 设计原则

### 5.1 root run 的唯一语义输入是 `AgentRunRequest.turn_input`

root run 中, service 已经把当轮输入整理进 `AgentRunRequest.turn_input`。Exp 负责
把这个语义输入和 session context 合成 root turn prompt。Kernel 不应把 Exp
渲染后的 `task` 再写回 `TurnInput.instruction.user_text`。

禁止的修复方式:

```python
turn_input = replace(
    turn_request.turn_input,
    instruction=replace(turn_request.turn_input.instruction, user_text=task),
)
```

原因是 root path 下的 `task` 可能已经包含:

- `<user_instructions>`
- `<session_*>` sections
- `<current_instruction>`
- 已渲染的附件清单

如果再通过 `turn_input.to_sections()` 渲染一次, 会把整段已渲染上下文包进新的
`<current_instruction>`, 破坏 context section 边界。

### 5.2 root turn 渲染点归 Exp 所有

root run 的渲染流程保持在 Exp:

```text
AgentRunRequest.turn_input
  -> ContextAssembler.assemble_turn(...)
  -> UserTurnContext.to_message(ContextView.RUNTIME)
  -> RootTurnRender
  -> AgentKernelTurnRequest(
       user_message_content=rendered_content,
       turn_input=AgentRunRequest.turn_input,
     )
  -> AgentKernel.run_stream(kernel_runtime, turn_request, ...)
```

Kernel 可以消费 root turn 已渲染结果, 但不应重新解释它的 tag 结构。

### 5.3 preflight compaction 只从可重建 sources split

preflight current split 允许丢弃 `messages[-1]`, 但必须满足:

- 当前指令和当前附件来自 `turn_input`。
- user instructions 来自 `ContextCompactor._user_instructions`。
- session sections 来自 `ContextAssemblyPorts.session_events`。
- session jobs 目前可以为空, 后续接入真实 port 后仍走同一组装路径。

任何只存在于 `messages[-1]`、不能回流到上述 typed sources 的内容, 都不能在
current split 之前被加入 root prompt。

### 5.4 hook rewrite 不能发生在 root 已渲染 prompt 之后

`USER_PROMPT_SUBMIT` rewrite 当前是 kernel 层能力。它适合 direct kernel 或
spawn 这类 caller 明确构造 `AgentKernelTurnRequest.user_message_content` 的场景,
但不适合 root run 已经由 Exp 渲染后的 prompt。

root run 后续只有两种可接受策略:

1. 第一阶段先禁止 root 已渲染 prompt 在 kernel 层做 rewrite, 保留 observe hook。
2. 若确实需要 rewrite root 用户输入, 后续单独把 rewrite 上移到
   `assemble_turn()` 之前, 对 `TurnInput.instruction.user_text` 做 typed rewrite,
   然后再渲染、持久化和传给 kernel。

第一阶段采用策略 1。策略 2 不是本 spec 的必要前提。

## 6. 第一阶段设计

第一阶段只修复当前 root path 的不安全边界, 不做大规模接口退役。

### 6.1 明确 root prompt rewrite mode

在 kernel runtime spec 增加窄字段, 表示 `USER_PROMPT_SUBMIT` rewrite 是否已经
由上游处理或当前路径是否允许 kernel rewrite。

建议字段:

```python
prompt_submit_rewrite_enabled: bool = True
```

语义:

- `True`: kernel 在构造 `state.messages[-1]` 前执行
  `USER_PROMPT_SUBMIT` rewrite。direct kernel 调用和 spawn typed request 路径沿用
  当前行为。
- `False`: kernel 不执行 rewrite, 但仍可在最终 prompt 上 emit observe hook。
  root run 使用该模式, 因为 root prompt 已由 Exp 和 `ContextAssembler`
  渲染完成。

`Exp.build_runtime()` 不应按 `spawn_id is None` 直接把该字段设为 `False`。原因是
`build_runtime()` 也被 devshell、integration tests 和其他 direct runtime
调用者使用, 这些调用者随后会构造自己的 `AgentKernelTurnRequest` 并传给
`kernel.run_stream()`。如果在 `build_runtime()` 阶段一刀切关闭 rewrite, 会误伤这些
非 root request 路径。

选择 kernel spec 字段, 而不是把 `allow_prompt_rewrite` 放进每次
`AgentKernelTurnRequest`, 是因为 prompt rewrite 开关描述的是 runtime policy,
不是本轮语义输入本身。spec 字段随 runtime 边界传播, root path 只需要派生一份
局部 `AgentKernelRuntime` / `AgentKernelSpec`。

正确落点是在 `Exp.run_stream()` 的 root 分支中:

1. 先通过 `_render_and_persist_root_turn()` 得到已渲染的 root prompt。
2. 在调用 `runtime.kernel.run_stream(...)` 之前, 派生一个只用于本次 root stream 的
   `kernel_runtime`, 把其中 `spec.prompt_submit_rewrite_enabled` 设为 `False`。
3. spawn path 和 direct `build_runtime()` 使用者继续使用默认 `True`。

这样关闭的是 root 已渲染 prompt 的 post-render rewrite, 不是所有
`spawn_id=None` runtime 的 rewrite。

生产入口不变量: `Exp.run_stream(spawn_id=None)` 的 root 分支由
`AgentRunService` 驱动, 输入已经是 `AgentRunRequest.turn_input`。devshell、
evaluation 和手写 integration runtime 继续通过 `runtime_scope()` /
`build_runtime()` 后构造自己的 `AgentKernelTurnRequest`, 不走这个 root
post-render rewrite 关闭点。

### 6.2 保留 `turn_input` 原始语义, 不用 rendered `task` 覆盖

`AgentKernel._run_items()` 中继续从 `AgentKernelTurnRequest.turn_input` 读取
images 和 preflight compaction 所需的 typed current input。不得把
`user_message_content` 覆盖回 `turn_input.instruction.user_text`。

root path 下:

- `user_message_content` 是 Exp 已渲染 runtime prompt。
- `turn_input` 是 raw semantic current input。
- 两者共同通过 `AgentKernelTurnRequest` 进入 kernel, 但职责不同。

preflight compaction 下:

- summary call 可以排除最后一条 root runtime prompt。
- `apply_summary()` 继续通过 `assemble_compaction()` 用 typed sources 重建
  compacted prompt。
- 因为 root path 不再允许 kernel post-render rewrite, 不会出现只有
  `messages[-1]` 知道、typed sources 不知道的额外内容。

### 6.3 direct kernel 使用 typed request 表达契约

direct kernel API 已不再显式接收裸 `task` 字符串。调用方必须构造
`AgentKernelTurnRequest`, 其中:

- `user_message_content` 是本次要送入 kernel 的执行文本。
- `turn_input` 是 preflight current split 后用于重建当前指令和附件的 typed
  source。

契约:

- 如果 direct caller 希望 preflight current split 保留当前指令语义, 必须提供与
  `user_message_content` 语义一致的 `turn_input`。
- 如果 `turn_input` 缺失或无有效输入, preflight 会走 runtime compaction 语义,
  当前 query 可能进入摘要。
- kernel 不在主代码中为缺失 `turn_input` 构造 ephemeral fallback; 需要 typed
  source 的调用点必须显式提供。

## 7. 第二阶段设计

第二阶段用于收敛 direct/spawn 兼容路径, 不是 root bug fix 的前提。

候选方向:

1. 已实现: 让 `AgentKernel.run_stream()` 接收 `AgentKernelTurnRequest`, 而不是裸
   `task` 字符串; `turn_input` 已从 `AgentKernelSpec` 迁出, 改由每次 kernel
   调用的 typed request 承载。
2. 已实现: 为 spawn run 构造独立 child `TurnInput`, 避免 child runtime 继续携带
   parent root `turn_input`。
3. 待业务需求确认: 若 root prompt rewrite 仍有业务需求, 在 Exp root turn preparation 阶段对
   `TurnInput.instruction.user_text` 做 rewrite, 并把 rewrite 后的 turn_input
   用于 render、persist、kernel turn request。

这三个方向可以分开排期。第一阶段只需要防止 root 已渲染 prompt 在 kernel 内
继续被不可回流地 rewrite。

## 8. 测试策略

### 8.1 Red tests

新增或更新测试, 先证明当前边界问题:

1. root-rendered prompt 不应在 kernel 层执行 rewrite。
   - 构造 `AgentKernelSpec(prompt_submit_rewrite_enabled=False)`。
   - 注册 `USER_PROMPT_SUBMIT` rewrite handler。
   - 断言 provider 看到的 root rendered content 未被 rewrite 修改。
   - 断言 observe hook 仍能看到最终 prompt, 如果保留 observe 语义。

2. root preflight current split 不丢 query。
   - 构造 root-like rendered task, raw `TurnInput.from_values(user_text="current query")`,
     以及非空 history。
   - 注册一个会给 `USER_PROMPT_SUBMIT` 注入文本的 rewrite handler, 并在 root-like
     path 通过 `prompt_submit_rewrite_enabled=False` 禁止它影响 rendered prompt。
   - 触发 preflight summary + `apply_summary()`。
   - 断言 compacted runtime prompt 中仍有 `<current_instruction>` 和
     `current query`。
   - 断言没有出现 nested `<current_instruction>` 或 `</ current_instruction>`。

3. direct kernel mismatch 行为被文档化。
   - 保留或更新现有 `test_kernel_passes_raw_turn_input_to_preflight_compactor`。
   - 新增测试说明 direct caller 传入不一致
     `AgentKernelTurnRequest.user_message_content` 和 `turn_input` 时, compactor
     使用 `turn_input` 作为 reattach source。这是当前显式契约,
     不把它误认为 root path 修复。

### 8.2 Green implementation

实现时集中修改:

- `matmaster/types/runtime.py`
  - `AgentKernelSpec` 增加 `prompt_submit_rewrite_enabled: bool = True`。
- `matmaster/core/exp.py`
  - `build_runtime()` 保持默认 `True`, 以兼容 devshell 和 direct runtime
    使用者。
  - `Exp.run_stream()` root 分支在 `_render_and_persist_root_turn()` 之后,
    调用 kernel 前派生一个 root-only `kernel_runtime`, 将
    `prompt_submit_rewrite_enabled` 设为 `False`。
  - spawn `run_stream(..., spawn_id=<child>)` 保持默认 `True`。
- `matmaster/core/agent.py`
  - `_run_items()` 根据 `prompt_submit_rewrite_enabled` 决定是否执行
    `emit_rewrite()`。
  - 只 gate `emit_rewrite()`; observe hook 的 `emit()` 仍在最终 `task` 上执行,
    不能把整个 `USER_PROMPT_SUBMIT` hook 分支一起关掉。
- `matmaster/context/compaction.py`
  - 将 `_summary_base_messages()` 和 `apply_summary()` 中重复的 `current_split`
    谓词抽成共享 helper, 或补测试确保两处行为同步。
- `tests/matmaster/core/`
  - 覆盖 root rewrite disabled、direct rewrite enabled、preflight reattach
    三类行为。
  - 覆盖 `Exp.run_stream(spawn_id=None)` 只用于已渲染 root prompt 的不变量, 防止
    direct typed-request caller 被误接到 root rewrite-disabled 路径。

### 8.3 Regression

至少运行:

```bash
uv run pytest \
  tests/matmaster/core/test_exp_turn_preparation.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_hook_wiring.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/context/test_assembly.py \
  -q
```

若改动触及 runtime spec 字段, 还要运行:

```bash
uv run pytest \
  tests/matmaster/types/test_runtime.py \
  tests/matmaster/test_runtime_spec.py \
  -q
```

## 9. 非目标

- 不在主代码中为旧 `task` 字符串入口保留兼容 wrapper。
- 不把 rendered `task` 写回 `TurnInput.instruction.user_text`。
- 不恢复 `run_meta["current_user_images"]` 或任何 run_meta 输入兜底。
- 不把服务能力 callback、factory、sink、barrier 放入 `run_meta`。
- 不把 `AgentRunPorts` 或 `KernelRuntimePorts` 扩成服务对象大袋子。
- 不在第一阶段接入真实 `session_jobs`。
- 不把 root prompt rewrite 上移到 `assemble_turn()` 之前。若需要, 后续单独设计。

## 10. 验收标准

第一阶段和已完成的第二阶段候选 1 完成后应满足:

- root run 的 `assemble_turn()` 仍是唯一 root turn renderer。
- root run 的 kernel 层不会对已渲染 prompt 做 `USER_PROMPT_SUBMIT` rewrite。
- preflight current split 后, 当前 query 来自 `turn_input` 并能稳定贴回
  `<current_instruction>`。
- 压缩后的 runtime prompt 不出现 nested `<current_instruction>`。
- direct kernel 的 typed request 行为有测试覆盖和明确契约, 不被误写成 root
  path 事实。
- 文档和测试都以 `assemble_turn` 已在 root path 落地为前提。
