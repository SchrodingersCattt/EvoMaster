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
  截断，本阶段先记录诊断，后续可单独设计是否阻止不完整 tool call。

## 推荐方案

采用附加诊断字段方案：新增 `finish_detail`，保留现有 `reason`。

示例结构：

```json
{
  "kind": "output_length_exceeded",
  "provider_finish_reason": "length",
  "message": "Model output was truncated by the provider output-token limit.",
  "content_chars": 1234,
  "reasoning_chars": 44000,
  "has_tool_calls": false,
  "usage": {
    "prompt_tokens": 1000,
    "completion_tokens": 4096,
    "total_tokens": 5096
  },
  "usage_vendor": {
    "outputTokens": 4096
  }
}
```

`kind` 是 MatMaster 自己的稳定分类，`provider_finish_reason` 保留 provider
或项目归一化后的原始 finish reason。前端、评测和日志应优先依赖 `kind`。

## 诊断分类

`finish_detail.kind` 使用以下分类：

| kind | 判定条件 | 用户侧含义 |
| --- | --- | --- |
| `output_length_exceeded` | `response.finish_reason == "length"` | 模型输出被 provider 输出 token 上限截断 |
| `content_filtered` | `response.finish_reason == "content_filter"` | provider 内容安全策略拦截或截断 |
| `empty_response` | `finish_reason == "stop"` 且无可见正文、无 reasoning | provider 正常 stop 但没有返回有效正文 |
| `reasoning_only` | `finish_reason == "stop"` 且有 reasoning、无可见正文 | 模型只产生思考内容，没有产生最终回答 |
| `missing_llm_response` | `_call_llm_streaming()` 没有产出 `LLMResponse` | 流结束但 kernel 没拿到可验证响应对象 |
| `non_stop_finish` | 其它非 `stop` finish | provider 返回了不被 kernel 接受的结束原因 |
| `unknown` | 无法分类的异常边界 | 保底分类，避免诊断字段缺失 |

`finish_reason == "tool_calls"` 且存在 tool calls 是正常中间态，不进入自然终止校验。
若存在 tool calls 同时 finish reason 是 `length`，本阶段先在日志或 assistant state
链路中保留风险信息，不改变执行语义。

## 数据流

1. Provider 产生 `StreamChunk.finish_reason`。
2. `AgentKernel._stream_llm_items()` 聚合 `content_parts`、`reasoning_parts`、
   `tool_calls_acc`、`usage`、`usage_vendor` 和最后一次 `finish_reason`。
3. `AgentKernel._run_items()` 校验 `LLMResponse`。
4. 若校验失败，kernel 调用诊断构造函数生成 `finish_detail`，然后返回
   `_TerminalItem(reason="invalid_finish", finish_detail=...)`。
5. `AgentKernel.run_stream()` 把 `finish_detail` 复制到 `RunResultEvent`。
6. `SSEHandler` 通过 `event.model_dump(mode="json")` 发送实时 payload。
7. `PersistenceHandler` 通过 `_public_content_for_event()` 将 `finish_detail`
   写入 run_result content，保证刷新历史后诊断不丢失。
8. `AgentRunService` 根据 `finish_detail.kind` 生成更具体的 `ErrorEvent.message`。
9. devshell drain 和 CLI summary 复制 `finish_detail`，供评测和脚本读取。

## 事件契约

`RunResultEvent` 新增可选字段：

```python
finish_detail: dict[str, Any] | None = None
```

实时 SSE payload 中，run_result 将同时包含：

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

顶层字段来自 `RunResultEvent.model_dump()`，`content.finish_detail` 来自
`_public_content_for_event()`。保留两处是为了兼容实时 SSE 与持久化历史两种读取方式。

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

这些文案可以先在后端固定，后续若前端需要本地化或更复杂展示，可改为读取
`finish_detail.kind` 自行映射。

## 兼容性

- 现有 `reason == "invalid_finish"` 不变。
- 现有 `status == "failed"` 不变。
- 现有 quota 行为不变：失败不扣 quota。
- 现有 `stream_closed.end_reason == "invalid_finish"` 不变。
- 现有 `RunResultEvent.messages` 仍然 `exclude=True`，不向 SSE 暴露完整上下文。
- 新字段为可选字段，旧历史事件没有 `finish_detail` 时按原逻辑展示。

## 文件影响范围

预计修改：

- `matmaster/core/agent.py`
  - `_TerminalItem` 增加 `finish_detail`。
  - `_terminal()` 支持传入诊断信息。
  - 新增私有诊断构造函数。
  - invalid finish 分支传递诊断信息。
- `matmaster/types/events.py`
  - `RunResultEvent` 增加 `finish_detail`。
- `matmaster/integration/event_payloads.py`
  - run_result / finish content 映射保留 `finish_detail`。
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
- `tests/matmaster/integration/test_event_payloads.py`
- `tests/matmaster/integration/test_quota_pipeline.py`
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
4. `_public_content_for_event("run_result", payload)` 保留 `finish_detail`。
5. `AgentRunService` 对 `output_length_exceeded` 发出包含输出 token 上限截断含义的
   `ErrorEvent`，并继续返回 `(False, "invalid_finish")`。
6. devshell summary JSON 包含 `finish_detail`。

回归测试：

- 当前 invalid finish quota 测试仍通过。
- 当前 empty stop retry 行为仍通过。
- 当前 `stream_closed` 顺序和 `end_reason` 不变。

## 后续扩展

本设计完成后，可以继续做两个独立增强：

- 对 `output_length_exceeded` 增加一次可控自动重试，例如降低 reasoning effort、压缩历史、
  或提高 `max_tokens` 后重试。
- 对 tool call 生成阶段的 `finish_reason="length"` 增加保护，避免执行明显截断的工具参数。

