# Phase 2: Agent Kernel - Research

**Researched:** 2026-03-22
**Domain:** Agent execution loop, Guard pipeline, Hook system, LLM Provider abstraction
**Confidence:** HIGH

## Summary

Phase 2 的核心目标是将现有分散在 `evomaster/agent/agent.py` (BaseAgent.run/_step)、`playground/mat_master/core/agent.py` (MatMasterAgent) 和 `playground/mat_master/core/tool_guard.py` (ToolGuard) 中的执行循环、guard 评估、hook 扩展逻辑抽取为一个纯执行 kernel，并定义 LLMProvider Protocol 统一 LLM 调用接口。

现有代码的 BaseAgent.run() 循环已经验证了 LLM call -> tool exec -> message accumulate -> loop 的基本模式，但其中混合了配置装配（prompt 组装、tool filter、compaction config）、轨迹记录、日志输出等非执行职责。新 kernel 需要精确提取执行循环的纯逻辑，同时支持 CONTEXT.md 中确定的 Hook 拦截模型和 Guard 评估 -> Hook -> 工具执行的流水线顺序。

LLMProvider 需要同时覆盖 chat()（完整响应）和 chat_stream()（流式 Iterator），其中 chat_stream() 是默认调用方式，streaming token 通过 Hook 转发给事件系统。现有 OpenAILLM.query_stream() 的流式 delta 累积逻辑和 tool_call delta 重组逻辑是可复用的参考模式。

**Primary recommendation:** 将 kernel 设计为 3 个独立模块 (kernel.py + guard_pipeline.py + hooks.py) 加 LLM Provider 抽象 (llm_provider.py + 消息类型 types.py)，全部放入 `matmaster/engine/` 目录，同步线程模型（Iterator 而非 AsyncIterator），与 Phase 1 的 threading 模型保持一致。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- LLM 返回无 tool_calls 时直接结束，发射 FinishEvent(reason='natural')，不给再试机会
- max_turns 到达时发射 FinishEvent(reason='max_turns')，用同一事件类型通过 reason 区分
- 支持 threading.Event 外部取消，每轮开始前检查，设置后发射 FinishEvent(reason='cancelled')
- 工具串行执行，一次处理一个 tool call。并行能力留给 exp 层或后续优化
- 去掉 finish tool，终止条件只有：自然结束 / max_turns / 外部取消
- Hook 是可拦截式扩展点，不是纯观察者
- 单一 Hook Protocol + BaseHook 基类，实现者只需 override 关心的方法
- 4 个 hook point：pre_tool_call (返回 HookAction: CONTINUE/SKIP)、post_tool_call (观察)、pre_llm_call (观察)、should_continue (返回 bool)
- 多个 hook 按注册顺序串行执行，第一个返回拦截结果的立即生效，后续 hook 不执行
- 事件发射通过 EventEmitterHook + MessageBus 组合实现：kernel 不直接持有 MessageBus
- on_stream_chunk 作为 hook 的一部分，将 streaming token 转发给事件系统
- LLMProvider Protocol 包含 chat() 和 chat_stream() 两个核心方法
- chat() 返回完整 LLMResponse，chat_stream() 返回 Iterator[StreamChunk]
- retry 策略内置于 provider 实现，kernel 不管重试
- kernel 默认使用 chat_stream()，通过 hook 将 streaming chunks 转发给事件系统
- 新定义 matmaster/ 下的消息类型（Message/LLMResponse/StreamChunk），与 evomaster/utils/types.py 完全脱钩
- Phase 2 实现一个具体的 LLMProvider 验证 Protocol 可用性
- 被 guard 拦截的 tool call 通过 ToolMessage 错误响应返回给 LLM，包含 reason 和 guidance
- GuardPipeline 内置 LoopDetectionGuard（滑动窗口检测重复调用），不可移除
- MaxTurns 由循环计数器处理，不需要单独 guard
- 业务 guard 由 exp 层通过 AgentRuntimeSpec.guards 注入
- Guard 评估时机：每个 tool call 执行前
- 执行顺序：Guard 评估 -> pre_tool_call hook -> tool 执行 -> post_tool_call hook。Guard 拦截的调用不触发 hook

### Claude's Discretion
- LLMResponse / StreamChunk / Message 等新消息类型的具体字段设计
- LoopDetectionGuard 的窗口大小和阈值参数（可参考现有 LOOP_WINDOW=5, LOOP_THRESHOLD=2）
- HookAction 枚举的具体值
- GuardPipeline 内部 recent_calls 滑动窗口的维护方式
- 具体 LLMProvider 实现的选择（OpenAI vs LiteLLM）
- kernel 内部状态管理细节

### Deferred Ideas (OUT OF SCOPE)
- 工具并行执行（基于 depend_on 或无依赖分组）-- 重构稳定后优化
- Context compaction -- kernel 需要支持但具体策略留给后续（CompactionConfig 已在 spec 中）
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| KERN-01 | AgentKernel 实现纯执行循环，只消费 AgentRuntimeSpec，不做 config 装配 | 现有 BaseAgent.run()/_step() 循环已提供参考模式；nanobot AgentLoop._run_agent_loop() 提供更简洁的参考；kernel 只需 spec + task + 可选 stop_event |
| KERN-02 | 内置通用 Guard（loop detection、max turns），不可移除 | LoopDetectionGuard 从现有 ToolGuard._is_loop() 提取，使用 LOOP_WINDOW=5/LOOP_THRESHOLD=2；max_turns 由循环计数器直接处理 |
| KERN-03 | GuardPipeline 支持串联执行多个 Guard（内置 + 业务注入） | GuardPipeline 先执行内置 LoopDetectionGuard，再按注册顺序执行 spec.guards 列表中的业务 guard |
| KERN-04 | Hook Point API 支持 4 个扩展点 | Hook Protocol + BaseHook 基类；pre_tool_call 返回 HookAction(CONTINUE/SKIP)，should_continue 返回 bool，其余为观察 |
| LLMP-01 | LLMProvider Protocol 接口定义 chat() + chat_stream() | 新定义的 Protocol 与现有 BaseLLM 脱钩；chat() 返回 LLMResponse，chat_stream() 返回 Iterator[StreamChunk]；retry 内置于 provider |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | v2 (existing) | Message/LLMResponse 等不可变数据模型 | 项目已使用，frozen model 保证 kernel 运行期间数据不变性 |
| stdlib threading | 3.13 | threading.Event 外部取消 | 同步线程模型，与 Phase 1 MessageBus 一致 |
| stdlib collections.deque | 3.13 | 滑动窗口 (LoopDetectionGuard) | 固定大小窗口，O(1) 追加/弹出 |
| stdlib enum | 3.13 | HookAction 枚举 | 简单有限集合 |
| stdlib time | 3.13 | monotonic 计时 (RecentCall.timestamp) | 已在 guards.py 使用 |
| stdlib typing | 3.13 | Protocol, runtime_checkable | 项目惯例，Guard Protocol 已使用此模式 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| openai | existing | 具体 LLMProvider 实现 | Phase 2 验证 Protocol 可用性 |
| litellm | existing | 可选的 LLMProvider 实现 | 如果需要多 provider 统一接口 |
| logging | stdlib | kernel 内部日志 | 项目惯例 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| openai 直接调用 | litellm 统一封装 | litellm 开箱多 provider 但增加一层抽象；推荐 Phase 2 用 litellm（现有系统已依赖） |
| Pydantic frozen model | dataclass(frozen=True) | 项目惯例是 Pydantic，但消息类型（Message、StreamChunk）更适合 Pydantic 因为需要序列化 |
| Protocol (structural typing) | ABC (nominal typing) | 项目已确立 Protocol 模式（Guard），LLMProvider/Hook 沿用 |

**Installation:**
```bash
# 无新依赖，全部使用现有 stack
```

## Architecture Patterns

### Recommended Project Structure
```
matmaster/
├── contracts/
│   ├── runtime.py          # AgentRuntimeSpec (Phase 1, 更新 llm_provider/hooks 类型)
│   ├── guards.py           # Guard Protocol (Phase 1, 不变)
│   ├── events.py           # AgentEvent/BusEvent (Phase 1, 不变)
│   └── context.py          # PlaygroundContext (Phase 1, 不变)
├── bus/
│   ├── queue.py            # MessageBus (Phase 1, 不变)
│   └── bridge.py           # QueueBridge (Phase 1, 不变)
└── kernel/                 # Phase 2 新目录
    ├── __init__.py          # 公开 API re-exports
    ├── kernel.py            # AgentKernel 执行循环
    ├── guard_pipeline.py    # GuardPipeline + LoopDetectionGuard
    ├── hooks.py             # Hook Protocol + BaseHook + HookAction + EventEmitterHook
    ├── types.py             # Message/LLMResponse/StreamChunk/ToolCallData 消息类型
    └── llm_provider.py      # LLMProvider Protocol + 具体实现 (OpenAIProvider 或 LiteLLMProvider)
```

### Pattern 1: Kernel 只消费 Spec 的纯执行循环

**What:** AgentKernel.run(spec, task, stop_event) 是唯一公开方法，接受不可变的 AgentRuntimeSpec，执行 LLM -> tool -> loop 直到终止条件。

**When to use:** 每次 agent 运行时由 exp 层构建 spec 并传入。

**Example:**
```python
# Source: 从现有 BaseAgent.run() + nanobot _run_agent_loop() 提炼
class AgentKernel:
    """纯执行循环 -- 不持有配置，不做装配。"""

    def run(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        stop_event: threading.Event | None = None,
    ) -> FinishEvent:
        messages: list[Message] = [
            SystemMessage(content=spec.system_prompt),
            UserMessage(content=task),
        ]
        guard_pipeline = GuardPipeline(spec.guards)
        turn = 0

        while turn < spec.max_turns:
            # 外部取消检查
            if stop_event and stop_event.is_set():
                return self._finish(spec, messages, "cancelled")

            turn += 1

            # pre_llm_call hook (观察)
            self._run_hooks(spec.hooks, "pre_llm_call", messages=messages, turn=turn)

            # should_continue hook (可拦截)
            if not self._check_should_continue(spec.hooks, messages, turn):
                return self._finish(spec, messages, "hook_stopped")

            # LLM 调用 (默认 streaming)
            response = self._call_llm(spec, messages)

            # 自然结束：LLM 无 tool_calls
            if not response.tool_calls:
                messages.append(AssistantMessage(
                    content=response.content,
                    reasoning_content=response.reasoning_content,
                ))
                return self._finish(spec, messages, "natural",
                                    final_content=response.content)

            # 有 tool_calls：逐个串行处理
            messages.append(AssistantMessage(
                content=response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
            ))

            for tc in response.tool_calls:
                # Guard 评估 (先于 hook)
                guard_result = guard_pipeline.evaluate(tc, turn, spec.max_turns)
                if not guard_result.allowed:
                    # 拦截 -> ToolMessage 错误响应 (不触发 hook)
                    messages.append(ToolMessage(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=f"BLOCKED: {guard_result.reason}\n{guard_result.guidance}",
                    ))
                    continue

                # pre_tool_call hook (可拦截：SKIP/CONTINUE)
                action = self._run_pre_tool_call(spec.hooks, tc)
                if action == HookAction.SKIP:
                    messages.append(ToolMessage(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content="Tool call skipped by hook.",
                    ))
                    continue

                # 工具执行
                result = spec.tool_registry.execute(tc.name, tc.arguments)
                messages.append(ToolMessage(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    content=result,
                ))

                # post_tool_call hook (观察)
                self._run_hooks(spec.hooks, "post_tool_call",
                               tool_call=tc, result=result)

        # max_turns 耗尽
        return self._finish(spec, messages, "max_turns")
```

### Pattern 2: Guard Pipeline 不可移除内置 Guard

**What:** GuardPipeline 在构造时自动注入 LoopDetectionGuard 到管线头部，外部不可移除。

**Example:**
```python
class GuardPipeline:
    """串联执行 Guard：内置不可移除 + 外部业务注入。"""

    def __init__(self, external_guards: list[Guard] | None = None) -> None:
        self._loop_guard = LoopDetectionGuard()  # 内置，不可移除
        self._guards: list[Guard] = [self._loop_guard]
        if external_guards:
            self._guards.extend(external_guards)
        self._recent_calls: deque[RecentCall] = deque(maxlen=LOOP_WINDOW)

    def evaluate(
        self, tool_call: ToolCallData, current_turn: int, max_turns: int
    ) -> GuardResult:
        ctx = GuardContext(
            tool_name=tool_call.name,
            tool_args=tool_call.arguments,
            tool_call_id=tool_call.id,
            current_turn=current_turn,
            max_turns=max_turns,
            recent_calls=list(self._recent_calls),
        )
        for guard in self._guards:
            result = guard.evaluate(ctx)
            if not result.allowed:
                return result
        # 通过 -> 记录调用
        self._record_call(tool_call)
        return GuardResult(allowed=True)
```

### Pattern 3: Hook Protocol + BaseHook 默认实现

**What:** 单一 Hook Protocol 定义所有 hook point，BaseHook 提供默认返回值，实现者只 override 关心的方法。

**Example:**
```python
class HookAction(enum.Enum):
    CONTINUE = "continue"
    SKIP = "skip"

@runtime_checkable
class Hook(Protocol):
    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction: ...
    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None: ...
    def pre_llm_call(self, messages: list[Message], turn: int) -> None: ...
    def should_continue(self, messages: list[Message], turn: int) -> bool: ...
    def on_stream_chunk(self, chunk: StreamChunk) -> None: ...

class BaseHook:
    """默认实现 -- 所有方法返回默认值，实现者只 override 关心的。"""
    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        return HookAction.CONTINUE
    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        pass
    def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        pass
    def should_continue(self, messages: list[Message], turn: int) -> bool:
        return True
    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        pass
```

### Pattern 4: LLMProvider Protocol + Streaming 默认路径

**What:** LLMProvider Protocol 定义 chat() 和 chat_stream()，kernel 默认使用 chat_stream() 并通过 hook.on_stream_chunk() 转发 token。

**Example:**
```python
@runtime_checkable
class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]: ...
```

### Pattern 5: EventEmitterHook -- Kernel 与事件系统的桥梁

**What:** EventEmitterHook 实现 Hook Protocol，在 hook 方法中将数据转换为 AgentEvent 并通过 MessageBus.emit() 发射。Kernel 不直接持有 MessageBus。

**Example:**
```python
class EventEmitterHook(BaseHook):
    """通过 MessageBus 将 kernel 行为转化为事件流。"""

    def __init__(self, bus: MessageBus, source: str) -> None:
        self._bus = bus
        self._source = source

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self._bus.emit(ToolCallEvent(
            source=self._source,
            call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        ))
        return HookAction.CONTINUE

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        self._bus.emit(ToolResultEvent(
            source=self._source,
            call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result,
        ))

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self._bus.emit(ThoughtEvent(
            source=self._source,
            content=chunk.content or "",
            stream_state=chunk.stream_state,
            stream_id=chunk.stream_id,
            reasoning_content=chunk.reasoning_content,
        ))
```

### Anti-Patterns to Avoid
- **Kernel 内配置装配:** Kernel 绝不应该读取 config dict、组装 prompt、初始化 LLM 客户端 -- 这些全部是 exp 层 assemble() 的职责
- **Kernel 直接持有 MessageBus:** 事件发射通过 Hook 间接完成，保持 kernel 与事件系统解耦
- **Guard 与 Hook 职责混淆:** Guard 做安全拦截（blocking），Hook 做行为扩展（observing/intercepting）。Guard 拦截不触发 Hook
- **消息类型复用 evomaster/utils/types.py:** 新 kernel 定义独立的消息类型，与旧代码完全脱钩
- **在 kernel 中实现 retry:** Retry 是 LLMProvider 的内部职责，kernel 只调用 chat_stream() 一次

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 流式 delta 累积重组 | 自定义 stream parser | 参考现有 OpenAILLM.query_stream() 的 tool_calls_acc 模式 | 需要正确处理 tool_call delta 按 index 累积、finish_reason 提取、reasoning_content 合并 |
| LLM retry 策略 | kernel 内 retry 循环 | provider 内 `_call_with_retry` 模式 | 指数退避、超时翻倍、non-retryable error 分类已在 BaseLLM 中验证 |
| 滑动窗口重复检测 | 自定义数组+手动淘汰 | collections.deque(maxlen=N) | 固定窗口自动弹出旧元素，O(1) |
| JSON arguments 清洗 | 手动 regex 替换 | 参考 `_sanitize_tool_call_arguments()` | LLM 返回的 tool_call arguments 偶尔包含非法 JSON（XML 属性混入），需要防御性清洗 |
| 消息格式转换 (Message -> API dict) | 逐字段手动构造 | Message.to_api_dict() 方法 | 每种消息角色的 API 格式不同（assistant 有 tool_calls、tool 需要 tool_call_id） |

**Key insight:** 现有 `evomaster/utils/llm.py` 中的流式处理、retry 策略、arguments 清洗都是经过生产验证的逻辑。新 LLMProvider 实现应参考（而非复制粘贴）这些模式，使用新的消息类型重新实现。

## Common Pitfalls

### Pitfall 1: Streaming Tool Call Delta 重组错误
**What goes wrong:** LLM streaming 返回 tool_call delta 时，每个 chunk 只包含部分数据（id 在第一个 chunk，name 分段到达，arguments 逐字符累积）。如果不按 index 正确累积，会导致 tool_call 丢失或参数截断。
**Why it happens:** OpenAI streaming API 将 tool_calls 拆分为增量 delta，需要按 index 分组重组。
**How to avoid:** 使用 `dict[int, ToolCallAccumulator]` 按 index 累积，stream 结束后按 index 排序组装。参考现有 `OpenAILLM.query_stream()` 的 `tool_calls_acc` 模式。
**Warning signs:** 测试中 tool_call 的 arguments 不完整或为空字符串。

### Pitfall 2: Guard 评估 vs 工具执行的时序错误
**What goes wrong:** Guard 评估和 Hook 触发的顺序不正确，导致被拦截的 tool call 仍然触发 pre_tool_call hook 事件，或者 guard 评估时 recent_calls 没有正确维护。
**Why it happens:** CONTEXT.md 明确要求 Guard 拦截 -> pre_tool_call hook -> 工具执行 -> post_tool_call hook，Guard 拦截的调用不触发 hook。
**How to avoid:** 在循环中严格按序：(1) guard.evaluate() (2) 如果 blocked, append ToolMessage 并 continue (3) pre_tool_call hook (4) execute (5) post_tool_call hook。只在 guard 通过后才 record_call()。
**Warning signs:** 事件系统收到了被 guard 拦截的 tool call 的 ToolCallEvent。

### Pitfall 3: LoopDetectionGuard 状态生命周期
**What goes wrong:** LoopDetectionGuard 的 recent_calls 在多次 kernel.run() 调用之间没有正确重置，导致上次运行的 tool call 历史影响本次评估。
**Why it happens:** GuardPipeline 是在 kernel.run() 内部创建的，但如果作为 kernel 属性持有则会跨调用泄漏状态。
**How to avoid:** GuardPipeline 在每次 kernel.run() 的开头创建新实例（内部创建新 LoopDetectionGuard），确保无状态泄漏。外部注入的 guard 实例是否有状态由业务层自己管理。
**Warning signs:** 第二次 kernel.run() 的第一个 tool call 被错误拦截为 loop。

### Pitfall 4: Kernel 终止条件遗漏
**What goes wrong:** kernel.run() 在某些边界条件下不发射 FinishEvent 就返回了，导致事件消费者永远等不到结束信号。
**Why it happens:** 有 4 种终止路径（natural / max_turns / cancelled / hook_stopped），如果没有统一的 _finish() 方法，容易遗漏。
**How to avoid:** 所有退出路径都必须经过 _finish() 方法，该方法负责发射 FinishEvent 并返回结果。在 _finish() 中通过 hook 发射事件。
**Warning signs:** QueueBridge 在 kernel 运行结束后没有收到 FinishEvent。

### Pitfall 5: 消息类型到 API 格式的转换不一致
**What goes wrong:** 新定义的 Message 类型转换为 LLM API dict 时格式不正确，导致 LLM provider 返回 400 错误（特别是 Claude/Bedrock 对 tool_use/tool_result 匹配有严格要求）。
**Why it happens:** 不同 LLM provider 对消息格式有不同要求：OpenAI 用 tool_call_id，Anthropic/Bedrock 要求每个 tool_use 必须有对应的 tool_result。
**How to avoid:** Message.to_api_dict() 只生成 OpenAI 格式（provider-agnostic），具体 provider 实现负责格式适配。provider 内部做 sanitize（参考 nanobot LLMProvider._sanitize_empty_content()）。
**Warning signs:** assistant message 有 tool_calls 但 content 为空字符串（应该是 None）导致 provider 报错。

### Pitfall 6: Hook 串行执行的短路语义不明确
**What goes wrong:** 多个 hook 中某个返回 SKIP 后，后续 hook 的 pre_tool_call 仍然被调用，导致事件重复或行为不一致。
**Why it happens:** CONTEXT.md 明确要求第一个返回拦截结果的 hook 立即生效，后续 hook 不执行。如果实现为先收集所有结果再判断，就会违反这个语义。
**How to avoid:** pre_tool_call 和 should_continue 使用短路循环：遍历 hooks，遇到第一个非默认返回值立即 break。post_tool_call 和 pre_llm_call 是观察型，全部执行。
**Warning signs:** 在有 SKIP hook 的情况下，EventEmitterHook 仍然发射了 ToolCallEvent。

## Code Examples

### Message Types (新定义，与 evomaster/utils/types.py 脱钩)

```python
# Source: 参考 evomaster/utils/types.py + nanobot/providers/base.py
# 位置: matmaster/engine/types.py

from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class ToolCallData(BaseModel):
    """单个工具调用数据（从 LLM 响应中提取）。"""
    id: str
    name: str
    arguments: dict[str, Any]  # 注意：已解析为 dict，不是 JSON 字符串

class Message(BaseModel):
    """基础消息类型。"""
    role: Role
    content: str | None = None

class SystemMessage(Message):
    role: Role = Role.SYSTEM

class UserMessage(Message):
    role: Role = Role.USER

class AssistantMessage(Message):
    role: Role = Role.ASSISTANT
    tool_calls: list[ToolCallData] | None = None
    reasoning_content: str | None = None

class ToolMessage(Message):
    role: Role = Role.TOOL
    tool_call_id: str
    tool_name: str

class LLMResponse(BaseModel):
    """完整 LLM 响应。"""
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCallData] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)

class StreamChunk(BaseModel):
    """流式 chunk。"""
    content: str | None = None
    reasoning_content: str | None = None
    tool_call_deltas: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    stream_state: str | None = None  # 'start' | 'streaming' | 'end'
    stream_id: str | None = None
```

### LoopDetectionGuard 实现

```python
# Source: 提取自 playground/mat_master/core/tool_guard.py _is_loop()
# 位置: matmaster/engine/guard_pipeline.py

import json
import time
from collections import deque
from matmaster.types.guards import Guard, GuardContext, GuardResult, RecentCall

LOOP_WINDOW = 5
LOOP_THRESHOLD = 2

class LoopDetectionGuard:
    """滑动窗口检测重复 tool call，kernel 内置不可移除。

    从 recent_calls 中提取指纹（tool_name + canonical args JSON），
    在窗口内出现 >= LOOP_THRESHOLD 次则拦截。
    """

    def __init__(
        self,
        window: int = LOOP_WINDOW,
        threshold: int = LOOP_THRESHOLD,
    ) -> None:
        self._window = window
        self._threshold = threshold

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        fp = self._fingerprint(ctx.tool_name, ctx.tool_args)
        # 从 ctx.recent_calls 中统计窗口内相同指纹的数量
        recent_fps = [
            self._fingerprint(rc.tool_name, rc.tool_args)
            for rc in ctx.recent_calls[-self._window:]
        ]
        count = recent_fps.count(fp)
        if count >= self._threshold:
            return GuardResult(
                allowed=False,
                reason=f"Loop detected: '{ctx.tool_name}' called {count + 1} times "
                       f"with identical arguments in the last {self._window} calls.",
                guidance="Try a different approach or modify the arguments. "
                         "Repeating the same call will not produce different results.",
            )
        return GuardResult(allowed=True)

    @staticmethod
    def _fingerprint(tool_name: str, tool_args: dict[str, Any]) -> str:
        try:
            canonical = json.dumps(tool_args, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canonical = str(tool_args)
        return f"{tool_name}|{canonical}"
```

### LLMProvider Protocol 接口

```python
# Source: 参考 nanobot/providers/base.py + evomaster/utils/llm.py BaseLLM
# 位置: matmaster/types/llm_provider.py

from typing import Any, Iterator, Protocol, runtime_checkable
from matmaster.engine.types import LLMResponse, StreamChunk

@runtime_checkable
class LLMProvider(Protocol):
    """LLM 推理接口。

    实现者负责：
    - API 调用和错误处理
    - retry 策略（kernel 不管重试）
    - 消息格式适配（OpenAI/Anthropic/etc.）
    """

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """完整推理调用，返回整个响应。"""
        ...

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        """流式推理调用，逐 chunk 返回。

        调用方负责消费 Iterator 并累积为 LLMResponse。
        最后一个 chunk 的 finish_reason 非 None 表示流结束。
        """
        ...
```

### Kernel 内部 LLM 调用 + Stream 累积

```python
# Source: 参考 OpenAILLM.query_stream() 的累积模式
# 位置: matmaster/engine/agent.py 内部方法

def _call_llm(self, spec: AgentRuntimeSpec, messages: list[Message]) -> LLMResponse:
    """调用 LLM（默认 streaming），通过 hook 转发 chunk。"""
    api_messages = [m.to_api_dict() for m in messages]
    tool_defs = spec.tool_registry.get_tool_definitions() if spec.tool_registry else None

    # 使用 chat_stream 逐 chunk 处理
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_acc: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None

    for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs):
        # 通过 hook 转发 chunk
        for hook in spec.hooks:
            hook.on_stream_chunk(chunk)

        if chunk.content:
            content_parts.append(chunk.content)
        if chunk.reasoning_content:
            reasoning_parts.append(chunk.reasoning_content)
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason
        if chunk.tool_call_deltas:
            for delta in chunk.tool_call_deltas:
                idx = delta.get("index", 0)
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                if delta.get("id"):
                    tool_calls_acc[idx]["id"] = delta["id"]
                if delta.get("name"):
                    tool_calls_acc[idx]["name"] += delta["name"]
                if delta.get("arguments"):
                    tool_calls_acc[idx]["arguments"] += delta["arguments"]

    # 组装 tool_calls
    tool_calls = None
    if tool_calls_acc:
        tool_calls = [
            ToolCallData(
                id=v["id"],
                name=v["name"],
                arguments=_parse_arguments(v["arguments"]),
            )
            for _, v in sorted(tool_calls_acc.items())
        ]

    return LLMResponse(
        content="".join(content_parts) or None,
        reasoning_content="".join(reasoning_parts) or None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| BaseAgent._step() 内做 tool guard + config 检查 | GuardPipeline 独立于 kernel 主循环 | Phase 2 设计 | Guard 可独立测试，kernel 循环更纯 |
| BaseLLM 继承体系 + query()/query_stream() | LLMProvider Protocol + chat()/chat_stream() | Phase 2 设计 | 从继承变为组合，provider 可替换 |
| event_callback 直传 | Hook + EventEmitterHook + MessageBus | Phase 1/2 设计 | kernel 与事件系统解耦 |
| ToolGuard 的 GuardDecision (blocked/message) | Guard Protocol 的 GuardResult (allowed/reason/guidance) | Phase 1 已定义 | 统一接口，内外一致 |
| Dialog.tools + get_messages_for_api() | Message.to_api_dict() + tool_defs 独立传递 | Phase 2 设计 | messages 和 tool definitions 分离 |

**Deprecated/outdated:**
- `evomaster/utils/types.py` Message 体系: kernel 使用新定义的 `matmaster/engine/types.py`，不再依赖
- `evomaster/utils/llm.py` BaseLLM 继承体系: 被 LLMProvider Protocol 替代
- `BaseAgent._step()` 中的 finish tool 检查逻辑: CONTEXT 已确认去掉 finish tool

## Open Questions

1. **LLMProvider 具体实现选择**
   - What we know: 现有系统同时支持 OpenAI 直调和通过 LiteLLM proxy 调用。litellm 已是项目依赖。
   - What's unclear: Phase 2 应该实现 OpenAIProvider（直接用 openai SDK）还是 LiteLLMProvider（统一多 provider）？
   - Recommendation: 实现 LiteLLMProvider，因为 (1) 已是现有依赖 (2) 开箱支持 OpenAI/Anthropic/Google (3) 现有 BaseLLM._call_with_retry() 的 retry 逻辑可以直接复用在 provider 内部。同时保留一个最简 MockLLMProvider 用于 kernel 单元测试。

2. **ToolCallData.arguments 的类型：dict vs str**
   - What we know: 现有 evomaster/utils/types.py 中 FunctionCall.arguments 是 str (JSON 字符串)。nanobot ToolCallRequest.arguments 是 dict。
   - What's unclear: 新类型应该用哪种？
   - Recommendation: 使用 `dict[str, Any]`（已解析）。理由：(1) kernel 内部需要 dict 形式传递给 guard 和 tool (2) 从 LLM streaming delta 累积后立即 json.loads 解析 (3) 避免每个消费者重复 json.loads。JSON 字符串只在 Provider 内部处理。

3. **Message.to_api_dict() 的格式**
   - What we know: OpenAI API 格式是 `{"role": "...", "content": "...", "tool_calls": [...]}`
   - What's unclear: 是否需要在 Message 层面支持多模态 content (list of blocks)?
   - Recommendation: Phase 2 先只支持 str content。多模态支持留给 Phase 3/5 迁移时按需扩展。to_api_dict() 输出 OpenAI 格式，provider 内部做格式适配。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (已配置) |
| Config file | pytest.ini |
| Quick run command | `pytest tests/matmaster/engine/ -x -q` |
| Full suite command | `pytest tests/matmaster/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| KERN-01 | AgentKernel 用 mock spec 完成完整循环 | unit | `pytest tests/matmaster/engine/test_kernel.py -x` | Wave 0 |
| KERN-01 | 自然终止 (no tool_calls) | unit | `pytest tests/matmaster/engine/test_kernel.py::test_natural_finish -x` | Wave 0 |
| KERN-01 | max_turns 终止 | unit | `pytest tests/matmaster/engine/test_kernel.py::test_max_turns -x` | Wave 0 |
| KERN-01 | 外部取消 (stop_event) | unit | `pytest tests/matmaster/engine/test_kernel.py::test_cancel -x` | Wave 0 |
| KERN-02 | LoopDetectionGuard 阻断重复调用 | unit | `pytest tests/matmaster/engine/test_guard_pipeline.py::test_loop_detection -x` | Wave 0 |
| KERN-02 | LoopDetectionGuard 不可移除 | unit | `pytest tests/matmaster/engine/test_guard_pipeline.py::test_builtin_not_removable -x` | Wave 0 |
| KERN-03 | GuardPipeline 串联执行 (内置 + 外部) | unit | `pytest tests/matmaster/engine/test_guard_pipeline.py::test_pipeline_order -x` | Wave 0 |
| KERN-03 | 第一个拒绝立即生效 | unit | `pytest tests/matmaster/engine/test_guard_pipeline.py::test_first_deny -x` | Wave 0 |
| KERN-04 | pre_tool_call SKIP 阻断执行 | unit | `pytest tests/matmaster/engine/test_hooks.py::test_pre_tool_call_skip -x` | Wave 0 |
| KERN-04 | should_continue 返回 False 终止循环 | unit | `pytest tests/matmaster/engine/test_hooks.py::test_should_continue_false -x` | Wave 0 |
| KERN-04 | 多 hook 短路执行 | unit | `pytest tests/matmaster/engine/test_hooks.py::test_hook_short_circuit -x` | Wave 0 |
| KERN-04 | EventEmitterHook 转发事件 | unit | `pytest tests/matmaster/engine/test_hooks.py::test_event_emitter_hook -x` | Wave 0 |
| LLMP-01 | LLMProvider Protocol 类型检查 | unit | `pytest tests/matmaster/engine/test_llm_provider.py::test_protocol_check -x` | Wave 0 |
| LLMP-01 | chat() 返回 LLMResponse | unit | `pytest tests/matmaster/engine/test_llm_provider.py::test_chat -x` | Wave 0 |
| LLMP-01 | chat_stream() 返回 Iterator[StreamChunk] | unit | `pytest tests/matmaster/engine/test_llm_provider.py::test_chat_stream -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/matmaster/engine/ -x -q`
- **Per wave merge:** `pytest tests/matmaster/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/engine/__init__.py` -- package init
- [ ] `tests/matmaster/engine/test_kernel.py` -- covers KERN-01
- [ ] `tests/matmaster/engine/test_guard_pipeline.py` -- covers KERN-02, KERN-03
- [ ] `tests/matmaster/engine/test_hooks.py` -- covers KERN-04
- [ ] `tests/matmaster/engine/test_llm_provider.py` -- covers LLMP-01
- [ ] `tests/matmaster/engine/test_types.py` -- covers Message types
- [ ] `tests/matmaster/engine/conftest.py` -- shared fixtures (MockLLMProvider, mock spec builder)

## Sources

### Primary (HIGH confidence)
- `matmaster/types/runtime.py` -- Phase 1 AgentRuntimeSpec 定义 (直接读取)
- `matmaster/types/guards.py` -- Phase 1 Guard Protocol 定义 (直接读取)
- `matmaster/types/events.py` -- Phase 1 AgentEvent 定义 (直接读取)
- `matmaster/bus/queue.py` -- Phase 1 MessageBus 实现 (直接读取)
- `evomaster/agent/agent.py` -- 现有 BaseAgent.run()/_step() 循环 (直接读取)
- `evomaster/utils/llm.py` -- 现有 BaseLLM/OpenAILLM 实现 (直接读取)
- `playground/mat_master/core/tool_guard.py` -- 现有 ToolGuard 实现 (直接读取)
- `playground/mat_master/core/agent.py` -- MatMasterAgent 扩展 (直接读取)
- `nanobot/agent/loop.py` -- nanobot AgentLoop 参考 (直接读取)
- `nanobot/providers/base.py` -- nanobot LLMProvider 参考 (直接读取)

### Secondary (MEDIUM confidence)
- `.planning/phases/02-agent-kernel/02-CONTEXT.md` -- Phase 2 用户决策 (直接读取)
- `.planning/PROJECT.md` -- 项目愿景与方案选型 (直接读取)
- `.planning/REQUIREMENTS.md` -- 需求定义 (直接读取)
- `.planning/codebase/ARCHITECTURE.md` -- 现有架构分析 (直接读取)
- `.planning/codebase/CONVENTIONS.md` -- 编码规范 (直接读取)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 全部使用现有依赖，零新依赖
- Architecture: HIGH - 5 个模块的职责划分清晰，全部基于现有代码提取和参考架构
- Pitfalls: HIGH - 6 个 pitfall 全部来自对现有代码的直接分析
- Message types: MEDIUM - 字段设计基于 Claude's discretion，需在实现时与现有事件类型对齐验证
- LLMProvider 具体实现: MEDIUM - LiteLLM vs OpenAI 直调的选择需要在实现时确认

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable domain, project-internal)
