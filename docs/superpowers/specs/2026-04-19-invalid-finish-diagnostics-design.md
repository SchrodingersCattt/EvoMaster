# Invalid Finish Diagnostics Design

## 背景

当前 MatMaster 经常出现 agent kernel 返回 `invalid_finish`，但前端和
devshell 只能看到统一错误文案，无法判断是 provider 输出长度截断、空响应、
reasoning-only 响应、content filter，还是其它非自然结束。

现有链路已经保留了一部分底层信息，但在 terminal event 处被折叠：

- `OpenAIProvider.chat_stream()` 会读取 provider chunk 的 `finish_reason`，
  并通过 `StreamChunk.finish_reason` 传给 kernel。
- `BedrockProvider` 会把 Bedrock `stopReason=max_tokens` 映射为 OpenAI 风格
  的 `finish_reason="length"`。
- `AgentKernel._stream_llm_items()` 会把最后一次 `finish_reason` 放进
  `LLMResponse.finish_reason`。
- `AgentKernel._is_valid_natural_finish()` 只接受无 tool calls、`finish_reason`
  为 `stop`、且有可见正文的结果。其它情况都会变成 terminal reason
  `invalid_finish`。
- `RunResultEvent` 没有诊断字段，`AgentRunService` 遇到 `invalid_finish` 时
  只发固定错误文案：`Model did not return a valid final response. Please retry.`

因此，用户关于 provider max length 的怀疑是合理的：`invalid_finish` 确实可能
覆盖了 `finish_reason="length"`，而 Bedrock 的 `max_tokens` 会明确映射到这个值。
但 `invalid_finish` 不是单一原因，它目前是多个失败形态的总类。

## 目标

本设计的目标是让 `invalid_finish` 保持现有业务兼容性，同时补充结构化诊断信息：

- 保留 `RunResultEvent.reason == "invalid_finish"`，避免破坏 quota、worker、
  前端、历史回放和既有测试契约。
- 在 kernel terminal、`RunResultEvent`、实时 SSE payload、持久化历史 content、
  devshell summary 中携带同一份诊断信息。
- 明确区分输出长度截断、空响应、reasoning-only、content filter、缺失 LLM
  response、其它非 stop finish。
- 对用户显示更有用的错误信息，尤其是 `finish_reason="length"` 时明确提示输出
  token 上限截断。
- 为评测和后续排障提供机器可读字段，而不是依赖自然语言错误文案。

## 非目标

- 不在本阶段改变 provider 的 max token 配置。
- 不在本阶段改变 `invalid_finish` 的顶层 reason 名称。
- 不在本阶段实现自动重试或自动提高输出 token 上限。
- 不在本阶段重构前端展示组件；后端会提供向后兼容 payload，前端可按需读取。
- 不在本阶段改变 tool call 执行策略。若 provider 在生成 tool call 参数时被长度
  截断，本阶段记录结构化风险并写日志，但仍沿用现有执行语义。
- 不在本阶段把所有 `LLMError` 异常路径改造成 `invalid_finish`。provider error
  仍然走现有异常处理；`finish_detail` 只描述 kernel 收到响应后判定出的 finish
  问题，以及极少数没有 `LLMResponse` 的防御性分支。

## 推荐方案

采用附加诊断字段方案：新增结构化 `FinishDetail`，保留现有 `reason`。

内部类型使用 Pydantic，而不是 `dict[str, Any]`，保证 `kind` 枚举、字段名称和
序列化行为有 schema 约束。SSE payload 和 DB JSON 中仍然表现为普通 dict。

建议在 `matmaster/types/events.py` 中定义：

```python
class FinishDetail(BaseModel):
    kind: Literal[
        "output_length_exceeded",
        "content_filtered",
        "empty_response",
        "reasoning_only",
        "missing_llm_response",
        "non_stop_finish",
        "unknown",
    ]
    provider_finish_reason: str | None = None
    message: str
    content_chars: int = 0
    reasoning_chars: int = 0
    has_visible_content: bool = False
    has_reasoning: bool = False
    has_tool_calls: bool = False
    tool_call_count: int = 0
    last_turn_usage: dict[str, int] = Field(default_factory=dict)
    last_turn_usage_vendor: dict[str, Any] = Field(default_factory=dict)
    attempts: int | None = None
    last_error_kind: str | None = None
    truncation_risk: bool = False
```

`RunResultEvent` 使用：

```python
finish_detail: FinishDetail | None = None
```

示例：

```json
{
  "kind": "output_length_exceeded",
  "provider_finish_reason": "length",
  "message": "Model output was truncated by the provider output-token limit.",
  "content_chars": 1234,
  "reasoning_chars": 8000,
  "has_visible_content": true,
  "has_reasoning": true,
  "has_tool_calls": false,
  "tool_call_count": 0,
  "last_turn_usage": {
    "prompt_tokens": 1000,
    "completion_tokens": 4096,
    "total_tokens": 5096
  },
  "last_turn_usage_vendor": {
    "outputTokens": 4096
  },
  "attempts": 1,
  "last_error_kind": null,
  "truncation_risk": false
}
```

`last_turn_usage` 是触发 invalid finish 的最后一轮 LLM response usage，不是
`RunResultEvent.usage` 的全程累计值。输出 token 上限判断以
`last_turn_usage.completion_tokens` 或 provider-native `last_turn_usage_vendor` 为主要证据，
`content_chars` 和 `reasoning_chars` 只作为排障辅助。

## 诊断分类

`finish_detail.kind` 使用以下分类：

| kind | 判定条件 | 用户侧含义 |
| --- | --- | --- |
| `output_length_exceeded` | `response.finish_reason == "length"` | 模型输出被 provider 输出 token 上限截断 |
| `content_filtered` | `response.finish_reason == "content_filter"` | provider 内容安全策略拦截或截断 |
| `empty_response` | `finish_reason == "stop"` 且无可见正文、无 reasoning | provider 正常 stop 但没有返回有效正文 |
| `reasoning_only` | `finish_reason == "stop"` 且有 reasoning、无可见正文 | 模型只产生思考内容，没有产生最终回答 |
| `missing_llm_response` | kernel 结束 LLM 调用后没有拿到 `LLMResponse` | 流结束但 kernel 没拿到可验证响应对象 |
| `non_stop_finish` | 其它非 `stop` finish | provider 返回了不被 kernel 接受的结束原因 |
| `unknown` | 分类器自身遇到无法归类的异常边界 | 保底分类，避免诊断字段缺失 |

`missing_llm_response` 是防御性分类。当前 `_call_llm_streaming()` 遇到大多数 provider
错误时会抛出 `LLMError`，由 `AgentRunService` 的异常路径生成 `ErrorEvent`，不一定会
进入 `llm_response is None` 分支。若后续某条路径确实返回了无 `LLMResponse` 的终态，
`FinishDetail.last_error_kind` 应尽量填入最后一次 `LLMError.error_category`，`attempts`
应填入实际尝试次数。没有错误对象时这两个字段为 `None`。

分类必须以 `finish_reason` 为主键，并保证互斥。`length` 的优先级高于是否有正文或
reasoning，避免 `length + reasoning-only` 被错误归到 `reasoning_only`。

伪代码：

```python
def build_finish_detail(response: LLMResponse | None, *, attempts=None, last_error=None):
    if response is None:
        return FinishDetail(
            kind="missing_llm_response",
            provider_finish_reason=None,
            message="LLM stream ended without a final response object.",
            attempts=attempts,
            last_error_kind=getattr(last_error, "error_category", None),
        )

    finish_reason = response.finish_reason
    has_visible = AgentKernel._has_visible_content(response)
    has_reasoning = bool(response.reasoning_content)
    has_tools = bool(response.tool_calls)
    base = {
        "provider_finish_reason": finish_reason,
        "content_chars": len(response.content or ""),
        "reasoning_chars": len(response.reasoning_content or ""),
        "has_visible_content": has_visible,
        "has_reasoning": has_reasoning,
        "has_tool_calls": has_tools,
        "tool_call_count": len(response.tool_calls or []),
        "last_turn_usage": dict(response.usage or {}),
        "last_turn_usage_vendor": dict(response.usage_vendor or {}),
    }

    if finish_reason == "length":
        return FinishDetail(
            kind="output_length_exceeded",
            message="Model output was truncated by the provider output-token limit.",
            truncation_risk=True,
            **base,
        )
    if finish_reason == "content_filter":
        return FinishDetail(
            kind="content_filtered",
            message="Model output was blocked or truncated by provider content policy.",
            **base,
        )
    if finish_reason == "stop" and not has_visible and has_reasoning:
        return FinishDetail(
            kind="reasoning_only",
            message="Model returned reasoning content without a visible final answer.",
            **base,
        )
    if finish_reason == "stop" and not has_visible:
        return FinishDetail(
            kind="empty_response",
            message="Model stopped without a visible final answer.",
            **base,
        )
    return FinishDetail(
        kind="non_stop_finish",
        message="Model returned a finish reason that cannot be committed as natural.",
        **base,
    )
```

`unknown` 只在分类器包装层兜底使用，例如分类器内部遇到非预期异常时返回
`FinishDetail(kind="unknown", message=...)`，并写 `logger.warning(..., exc_info=True)`。

## Tool Call 截断风险

`finish_reason == "tool_calls"` 且存在 tool calls 是正常中间态，不进入自然终止校验。
但如果存在 tool calls，同时 provider finish reason 是 `length`，这代表工具参数可能被
截断。当前代码会解析参数并继续执行工具，因此本阶段必须做可观测记录：

- 不改变工具执行语义，仍然执行既有 `response.tool_calls`。
- 构造 `FinishDetail(kind="output_length_exceeded", has_tool_calls=True,
  truncation_risk=True, ...)`。
- `logger.warning()` 输出结构化字段，包括 `finish_detail.model_dump(mode="json")`、
  `turn`、`tool_names`。
- `AssistantStateEvent` 新增可选 `finish_detail: FinishDetail | None`，并在
  tool-call assistant state 事件中携带这个风险信息。
- `_public_content_for_event("assistant_state", ...)` 在 content 中保留
  `finish_detail`，因为 `assistant_state` 不推送到 SSE，但会持久化，便于历史排障。

这解决的是可观测性，不是保护性执行。阻止截断工具调用或做参数完整性校验属于后续增强。

## 数据流

1. Provider 产生 `StreamChunk.finish_reason`。
2. `AgentKernel._stream_llm_items()` 聚合 `content_parts`、`reasoning_parts`、
   `tool_calls_acc`、`last_turn_usage`、`last_turn_usage_vendor` 和最后一次
   `finish_reason`。
3. `AgentKernel._run_items()` 校验 `LLMResponse`。
4. 若无 tool calls 且校验失败，kernel 调用诊断构造函数生成 `FinishDetail`，然后返回
   `_TerminalItem(reason="invalid_finish", finish_detail=...)`。
5. 若有 tool calls 且 `finish_reason == "length"`，kernel 记录 tool call 截断风险，
   并在 `AssistantStateEvent.finish_detail` 中携带诊断，不改变执行语义。
6. `AgentKernel.run_stream()` 把 terminal `finish_detail` 复制到 `RunResultEvent`。
7. `SSEHandler` 通过 `event.model_dump(mode="json")` 发送实时 payload。
8. `PersistenceHandler` 通过 `_public_content_for_event()` 将 `finish_detail`
   写入 run_result content，保证刷新历史后诊断不丢失。
9. `AgentRunService` 根据 `finish_detail.kind` 生成更具体的 `ErrorEvent.message`。
10. devshell drain、runner observer 和 CLI summary 复制 `finish_detail`，供评测和脚本读取。

## 事件契约

`RunResultEvent` 新增可选字段：

```python
finish_detail: FinishDetail | None = None
```

实时 SSE payload 中，run_result 会出现两处 `finish_detail`：

```json
{
  "type": "run_result",
  "status": "failed",
  "reason": "invalid_finish",
  "finish_detail": {
    "kind": "output_length_exceeded",
    "provider_finish_reason": "length"
  },
  "content": {
    "content": "",
    "status": "failed",
    "reason": "invalid_finish",
    "finish_detail": {
      "kind": "output_length_exceeded",
      "provider_finish_reason": "length"
    }
  }
}
```

`content.finish_detail` 是前端和历史回放的主契约，因为持久化历史只保存
`_public_content_for_event()` 生成的 content。顶层 `finish_detail` 来自
`RunResultEvent.model_dump()` 的 Pydantic 字段自动展开，是实时 SSE 的附加信息。
消费者应优先读取 `content.finish_detail`，没有时再回退到顶层 `finish_detail`。

历史回放中的 run_result 至少包含：

```json
{
  "type": "run_result",
  "content": {
    "content": "",
    "status": "failed",
    "reason": "invalid_finish",
    "finish_detail": {
      "kind": "output_length_exceeded",
      "provider_finish_reason": "length"
    }
  }
}
```

`StreamClosedEvent` 在本阶段不新增 `failure_kind`。它是传输层关闭标记，继续保留
`end_reason="invalid_finish"` 和 `treat_as_failure=True`。前端如果要展示具体错误，应读取
在 `stream_closed` 之前发出的 `ErrorEvent`，或读取同一轮 `run_result.content.finish_detail`。
如果现有前端只消费 `stream_closed`，需要在前端改为消费 `ErrorEvent` 或 run_result 诊断。

## 用户可见错误文案

`AgentRunService` 仍然只在 `run_result_event.reason == "invalid_finish"` 时发
`ErrorEvent`，但 message 根据 `finish_detail.kind` 区分：

| kind | message |
| --- | --- |
| `output_length_exceeded` | 模型输出被 provider 的输出 token 上限截断，未形成可提交的最终回答。请缩短上下文或提高输出上限后重试。 |
| `content_filtered` | 模型输出被 provider 内容策略截断或拦截，未形成可提交的最终回答。 |
| `reasoning_only` | 模型只返回了思考内容，没有生成可见最终回答。请重试。 |
| `empty_response` | 模型本轮没有返回可见最终回答。请重试。 |
| `missing_llm_response` | 模型流结束但没有返回可验证的响应对象。请重试。 |
| 其它 | 模型没有返回有效最终回答。请重试。 |

这些文案先在后端固定。后续若前端需要本地化或更复杂展示，可改为读取
`finish_detail.kind` 自行映射。

## 兼容性

- 现有 `reason == "invalid_finish"` 不变。
- 现有 `status == "failed"` 不变。
- 现有 quota 行为不变：失败不扣 quota。
- 现有 `stream_closed.end_reason == "invalid_finish"` 不变。
- 现有 `RunResultEvent.messages` 仍然 `exclude=True`，不向 SSE 暴露完整上下文。
- `RunResultEvent.finish_detail` 是可选字段，旧历史事件没有 `finish_detail` 时按原逻辑展示。
- `AssistantStateEvent.finish_detail` 是可选字段，不影响旧 assistant_state 反序列化。

## 文件影响范围

预计修改：

- `matmaster/types/events.py`
  - 新增 `FinishDetail` Pydantic 类型。
  - `RunResultEvent` 增加 `finish_detail: FinishDetail | None`。
  - `AssistantStateEvent` 增加 `finish_detail: FinishDetail | None`。
- `matmaster/types/__init__.py`
  - 若该模块维护事件类型导出列表，补充导出 `FinishDetail`。
- `matmaster/core/agent.py`
  - `_TerminalItem` 增加 `finish_detail`。
  - `_terminal()` 支持传入诊断信息。
  - 新增私有诊断构造函数，复用 `_has_visible_content()`。
  - invalid finish 分支传递诊断信息。
  - tool calls 加 `finish_reason == "length"` 的 warning 和 assistant state 诊断。
- `matmaster/integration/event_payloads.py`
  - run_result / finish content 映射保留 `finish_detail`。
  - assistant_state content 映射保留 `finish_detail`。
- `src/services/agent_run_service.py`
  - invalid finish 的 `ErrorEvent.message` 根据 `finish_detail.kind` 生成。
- `matmaster/core/stream_drain.py`
  - `DrainResult` 增加 `finish_detail`。
- `matmaster/devshell/cli.py`
  - summary JSON 输出 `finish_detail`。
- `matmaster/devshell/runner.py`
  - observer 重新发出 `RunResultEvent` 时携带 `finish_detail`。

预计新增或修改测试：

- `tests/matmaster/core/test_agent_kernel_stream.py`
- `tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py`
- `tests/matmaster/integration/test_event_payloads.py`
- `tests/matmaster/integration/test_quota_pipeline.py`
- `tests/matmaster/types/test_events.py`
- `tests/matmaster/devshell/test_runner.py` 或 `tests/matmaster/devshell/test_event_logger.py`

## 测试策略

核心测试：

1. fake provider 返回 `content="partial"` 和 `finish_reason="length"`，期望
   `RunResultEvent.reason == "invalid_finish"` 且
   `finish_detail.kind == "output_length_exceeded"`。
2. fake provider 返回 `reasoning_content="thinking only"`、`finish_reason="stop"`、
   无正文，期望 `finish_detail.kind == "reasoning_only"`。
3. fake provider 返回 `finish_reason="content_filter"`，期望
   `finish_detail.kind == "content_filtered"`。
4. 复用现有 `test_empty_stop_finishes_as_invalid_finish` 和
   `test_agent_kernel_empty_response_sentinels.py`，补充断言
   `finish_detail.kind == "empty_response"`。
5. fake provider 返回未知 finish reason，例如 `finish_reason="guardrail_intervened"`，
   期望 `finish_detail.kind == "non_stop_finish"`，并保留原始
   `provider_finish_reason`。
6. 针对 `llm_response is None` 的防御性路径加单测，期望
   `finish_detail.kind == "missing_llm_response"`，并在可模拟时断言
   `attempts` 或 `last_error_kind`。
7. 对分类器 fallback 加单测，模拟分类器内部异常或非法 response-like 输入，期望
   `finish_detail.kind == "unknown"` 且有 warning。
8. fake provider 返回 tool calls 且 `finish_reason="length"`，期望
   `AssistantStateEvent.finish_detail.kind == "output_length_exceeded"`，
   `truncation_risk is True`，同时工具仍按现有语义执行。
9. `_public_content_for_event("run_result", payload)` 保留 `finish_detail`。
10. `_public_content_for_event("assistant_state", payload)` 保留 `finish_detail`。
11. `AgentRunService` 对 `output_length_exceeded` 发出包含输出 token 上限截断含义的
    `ErrorEvent`，并继续返回 `(False, "invalid_finish")`。
12. devshell summary JSON 包含 `finish_detail`，尤其覆盖 `runner.py` 重新组装
    `RunResultEvent` 的路径。

回归测试：

- 当前 invalid finish quota 测试仍通过。
- 当前 empty stop retry 行为仍通过。
- 当前 `stream_closed` 顺序和 `end_reason` 不变。

## 实现顺序

1. 定义 `FinishDetail` 类型和分类器，先用纯函数单测覆盖分类矩阵。
2. 修改 `_TerminalItem` / `_terminal()`，让 terminal detail 可以透传。
3. 修改 `AgentKernel._run_items()` 的 invalid finish 分支，填充 terminal
   `finish_detail`。
4. 修改 `AgentKernel.run_stream()` 的 `RunResultEvent` 组装。
5. 修改 tool call + `finish_reason="length"` 的 assistant state 诊断和 warning。
6. 修改 `_public_content_for_event()`，同时覆盖 run_result 和 assistant_state。
7. 修改 `DrainResult`、devshell `runner.py`、devshell CLI summary。
8. 修改 `AgentRunService.ErrorEvent.message`。
9. 回归既有 invalid_finish 测试，补充 `finish_detail.kind` 断言。
10. 补齐新 kind 和 tool call 截断风险的专门测试。

## 后续扩展

本设计完成后，可以继续做两个独立增强：

- 对 `output_length_exceeded` 增加一次可控自动重试，例如降低 reasoning effort、压缩历史、
  或提高 `max_tokens` 后重试。
- 对 tool call 生成阶段的 `finish_reason="length"` 增加保护，避免执行明显截断的工具参数。
