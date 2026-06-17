# Opus 经 litellm-anthropic(Bedrock) transport 思维链丢失专项调查

- **调查日期:** 2026-06-17
- **分支:** `feat/trigger`
- **触发问题:** opus 模型经 `litellm-anthropic` transport 时，思维链（thinking / reasoning）在前端未回传；该 profile 的 key 实际指向 AWS Bedrock。
- **范围(read-only 调查):**
  [`matmaster/providers/transports/anthropic_messages.py`](../matmaster/providers/transports/anthropic_messages.py)、
  [`matmaster/providers/transport.py`](../matmaster/providers/transport.py)、
  [`matmaster/core/agent_llm_stream.py`](../matmaster/core/agent_llm_stream.py)、
  [`matmaster/core/agent.py`](../matmaster/core/agent.py)、
  [`config/llm_config.yaml`](../config/llm_config.yaml)、
  [`matmaster/providers/llm_factory.py`](../matmaster/providers/llm_factory.py)
- **结论一句话:** 这不是「思维链整体丢失」，而是 **interleaved thinking（跨工具调用的思维链）在 tool_result 续接回合失效**。首轮（工具调用之前）思维链正常；所有喂回 tool_result 之后的续接回合 Bedrock 完全不产生 thinking。代码侧的提取与回放链路经验证是健全的，断点在 LiteLLM `/v1/messages` → Bedrock 的转换层（最可能是 thinking 块的 `signature` 在双向转换中丢失，导致 interleaved 上下文断裂）。

---

## 1. 链路还原

`litellm-anthropic` profile 的实际链路（逐行核对代码与配置）：

```text
matmaster 代码 (BedrockAnthropicTransport，继承 AnthropicMessagesTransport)
   └─ 用 anthropic.AsyncAnthropic SDK
      build_kwargs() 每轮请求体 (anthropic_messages.py:522-543):
      {
        "model": "global.anthropic.claude-opus-4-6-v1",   # Bedrock inference profile 命名
        "thinking": {"type": "adaptive", "display": "summarized"},  # 硬编码，第525行
        "output_config": {"effort": "max"},                # reasoning_effort=max，第529-530行
        "messages": [...], "tools": [...]
      }
      POST {base_url}/v1/messages
           base_url = https://ai-gateway-global.dp.tech/   # DP AI 网关，不是 api.anthropic.com
                 │
                 ▼
   LiteLLM Proxy (DP AI 网关) 的 /v1/messages 端点
      ← key = LITELLM_PROXY_API_KEY；后端凭证是 AWS Bedrock
      ← 把 Anthropic Messages 协议 转换/路由 到 AWS Bedrock
                 │
                 ▼
   AWS Bedrock: anthropic.claude-opus-4-6-v1
                 │ 响应原路返回
                 ▼
   代码 normalize_stream / normalize_response
      └─ 找 thinking 块 / thinking_delta → reasoning_content → 前端 thought 事件
```

相关配置（[`config/llm_config.yaml`](../config/llm_config.yaml)）：

```yaml
litellm-anthropic:
  transport: anthropic_messages
  api_key: "${LITELLM_PROXY_API_KEY}"
  base_url: "${LITELLM_PROXY_API_BASE}"   # https://ai-gateway-global.dp.tech/
  vendor: bedrock                          # → llm_factory 分发到 BedrockAnthropicTransport

global.anthropic.claude-opus-4-6-v1:
  provider: litellm-anthropic
  model: global.anthropic.claude-opus-4-6-v1
  reasoning_effort: max
```

关键事实：这条名为 `anthropic_messages` 的 transport，**后端不是 first-party Anthropic API（不会纯透传），必然经过 LiteLLM 的 Anthropic↔Bedrock 协议转换**。thinking/reasoning 块正是这层转换里最容易丢的东西。

---

## 2. 决定性证据：思维链是「续接回合」丢失，不是整体丢失

用两条线上 SSE trace 对照。

### 2.1 case A — `hello`（单轮、无工具）：思维链正常

- 有 `thought` 事件，内容非空（真实 reasoning 摘要）。
- `usage.reasoning_tokens: 40`，与可见摘要长度吻合。
- 结论：单轮、不依赖任何前序 thinking 块的场景，链路通畅。

### 2.2 case B — `构建咖啡因和腺苷的分子结构`（`num_turns: 3`，2 个 tool_call）

逐 turn 拉出 thinking：

| | **turn 0**(首轮，tool_result 之前) | **turn 1**(看到 Bash 结果后) | **turn 2**(看到贴图结果后) |
|---|---|---|---|
| 动作 | `thought` + Bash tool_call | AttachFigure tool_call | 最终总结 run_result |
| `thought` 事件 | ✅ 有(完整 SMILES 推理) | ❌ 无 | ❌ 无 |
| 该轮 `reasoning_tokens` | **201** | **字段直接消失** | 无新增 |
| `usage_vendor.output_tokens_details` | `{thinking_tokens: 201}` | **字段直接消失** | — |

全程累计 `total reasoning_tokens` 始终 = **201**，**全部来自 turn 0**。turn 1、turn 2 一个 thinking token 都没产生。

这钉死两件事：

1. **不是代码的提取问题。** turn 0 的 thinking 被完整提取、显示、计费——证明 `normalize_stream` 的提取链路是好的。
2. **是续接回合 Bedrock 根本没产生 thinking。** turn 1 的 `usage_vendor` 里连 `output_tokens_details` 这个键都没有了——模型这一轮压根没思考，不是思考了没传回来。

---

## 3. 代码侧链路验证（证明问题不在本仓库代码）

### 3.1 请求侧：每轮都正确发了 thinking + effort

[`anthropic_messages.py:522-543`](../matmaster/providers/transports/anthropic_messages.py) 的 `build_kwargs` 每次被调用都会带上 `thinking:{type:"adaptive", display:"summarized"}`（第525行）与 `output_config:{effort:"max"}`（第529-530行）。agent loop 每个 turn 调一次 `chat_stream` → 每个 turn 都重新 `build_kwargs` → **续接回合的请求同样带了 effort=max + adaptive thinking**。所以「续接回合不思考」不是请求侧漏发。

### 3.2 响应侧：thinking 块（含 signature）提取健全

- 非流式 [`normalize_response`](../matmaster/providers/transports/anthropic_messages.py)（545-592行）：遍历 `raw.content` 找 `type=="thinking"`，取 `block.thinking` 与 `block.signature`，存入 `reasoning_content` 与 `provider_state.payload["thinking"]`。
- 流式 [`normalize_stream`](../matmaster/providers/transports/anthropic_messages.py)（594-704行）：`thinking_delta` 累积 thinking 文本（641-644行），`signature_delta` 捕获 signature（645-646行），`content_block_stop` 时把 `{type, thinking, signature}` 收进 `thinking_payload`（665-672行），最后随流尾 `StreamChunk(provider_state=...)` 一并发出（691-699行）。

### 3.3 回放侧：thinking 块按协议放在 assistant 消息最前面

- [`agent_llm_stream.py:171-172`](../matmaster/core/agent_llm_stream.py) 用 `captured_provider_state` 抓住流尾的 provider_state，并在 273 行放进 `LLMResponse.provider_state`。
- [`agent.py:454-459`](../matmaster/core/agent.py) 把它带进 `AssistantMessage` 并 append 进会话历史。
- 续接回合构造请求时，[`_assistant_to_wire`](../matmaster/providers/transports/anthropic_messages.py)（430-437行）调 `_thinking_blocks_from_payload(self._claim_provider_state(message))`，把 thinking 块放在 `text`/`tool_use` 块**之前**——符合 Anthropic 协议「thinking 开启时带 tool_use 的 assistant 消息必须以 thinking 块开头」的硬性要求。

### 3.4 排除一个怀疑点：`_claim_provider_state` 并不清空

方法名带 "claim"，一度怀疑是「取一次就清空」导致多轮回放丢块。核对 [`transport.py:127-132`](../matmaster/providers/transport.py)：它只做 transport tag 匹配后返回 `state.payload`，**不修改、不清空**。所以同一条 assistant 消息在后续每一轮被重复 `_assistant_to_wire` 都能取到 thinking payload，claim-and-clear bug **不存在**。

> 小结：从「提取 → 保存 → 回放到 wire」整条代码链路是健全的。问题发生在 wire 之后（LiteLLM↔Bedrock），代码无法控制的那一段。

---

## 4. 机制根因：interleaved thinking 在转换链上失效

背景（已与 AWS Bedrock 官方文档核对，见 §8）：

- `reasoning_effort: max` 在 Bedrock 的定义是 **Claude always thinks with no constraints**——每一轮都该思考。
- adaptive thinking **自动启用 interleaved thinking**：Claude can think *between tool calls*，正是为 agentic workflow 设计。也就是说 turn 1、turn 2 这种工具调用之间的回合，本应有思维链。

而 case B 里它们没有思考 → **interleaved thinking 在 LiteLLM `/v1/messages` → Bedrock 这条链上没生效**。

interleaved thinking 的维持有一个硬前提：**前序回合的 thinking 块（带 `signature`）必须原样回传**。§3 已证代码侧把带 signature 的 thinking 块正确回放到了 wire。因此断点在 wire 之后，三个最可能的位置（均吻合已知 issue，见 §8）：

1. **signature 在响应方向丢失（最可能）。** turn 0 响应经 LiteLLM 转回 Anthropic 格式时，Bedrock 的 `reasoningContent.signature` 没被转成 `signature_delta` 事件 → 代码存到的 thinking 块**没有有效 signature** → turn 1 把无签名块发回去，Bedrock 不认，interleaved 上下文断裂 → 续接回合静默退化为不思考（**不报错**，只是没思考）。
2. **请求方向 interleaved / effort 未透传。** 带 `tool_result` 的请求转 Bedrock 时，thinking 配置或 interleaved 开关没传过去。
3. 两者叠加。

### 为什么 case A 正常、case B 不正常（闭环）

- `hello`：单轮，直接思考，**不依赖任何前序 thinking 块回放** → 正常。
- 带工具：turn 0 同样是「直接思考」（不依赖前序）→ 也正常；但 turn 1/turn 2 是续接回合，**100% 依赖「前序 thinking 块 + signature 正确回传 + interleaved 启用」**，这条链在 LiteLLM↔Bedrock 上断了 → 续接回合无思维链。

两个 case 精确指向同一断点。

---

## 5. 待验证：三步锁定是 §4 的 1 / 2 / 3 中哪个

当前已确定「断点在转换层」，但区分三个具体位置需要抓原始 wire 数据：

1. **抓 turn 0 的原始响应**（在 `normalize_stream` 的 `raw_iter` 处打印）：看 thinking 块对应的 `signature_delta` 事件存不存在、signature 值是否非空。
   - 无 signature → 命中根因 1（响应方向丢签名）。
2. **抓 turn 1 发给网关的原始请求体**：看 assistant 消息第一个 block 是不是 `{type:"thinking", signature:"..."}`、signature 是否完整。
   - 带了完整 thinking 块 + signature，但 Bedrock 仍不思考 → 命中根因 2（请求方向未透传）。
   - 根本没带 thinking 块 → 回头查 `provider_state` 在流式聚合时是否丢了（但 §3 看代码是带的）。
3. **boto3 直连 Bedrock 对照**：用 Converse / InvokeModel 做一个两轮带 tool 的对话，手动把 turn 0 的 `reasoningContent`（含 signature）回放进 turn 1，看 Bedrock 是否产生新 thinking。
   - 直连能、过网关不能 → 实锤是 DP 网关的 LiteLLM 转换层。拿证据找网关团队确认后端是 Converse 还是 InvokeModel、LiteLLM 版本，以及它对 `/v1/messages` thinking+tool 的 signature 处理。

---

## 6. 影响评估

- **对最终结果无影响：** 工具正常调用、答案正确（case B 圆满完成）。
- **受影响的是：**
  - 续接回合的思维链对用户不可见（本次问题的直接表现）。
  - 续接回合可能少了显式推理（interleaved thinking 本为 agentic 续接决策设计）——属潜在质量影响，量级未测。

---

## 7. 修复方向

修复**不在本仓库代码**（§3 已验证代码侧健全），在 LiteLLM↔Bedrock 转换层，两条路：

1. **网关侧修复（根治）：** 让 DP 网关的 LiteLLM 在 `/v1/messages` ↔ Bedrock 之间正确双向透传 thinking 的 `signature`，并在带 `tool_result` 的续接请求上维持 interleaved thinking。需网关团队配合（确认后端 API、LiteLLM 版本）。
2. **临时绕过：** 改走 first-party Anthropic key 的纯透传链路（`providers.anthropic`，`transport: anthropic_messages` 且不经 Bedrock）。纯透传链路上 thinking 块原样返回，interleaved 不会断。代价是离开 Bedrock 计费/合规链路——是否可行取决于业务约束。

---

## 8. 参考资料

LiteLLM 已知 issue：

- [#21128 — Wildcard route breaks extended thinking on /v1/messages pass-through (Bedrock)](https://github.com/BerriAI/litellm/issues/21128)
- [#27946 — Anthropic → OpenAI conversion drops reasoning_content](https://github.com/BerriAI/litellm/issues/27946)
- [#15601 — Anthropic thinking blocks not present in request with tool calls](https://github.com/BerriAI/litellm/issues/15601)
- [#14194 — Bedrock thinking model with tools fails (expected thinking/redacted_thinking)](https://github.com/BerriAI/litellm/issues/14194)
- [google/adk-python #4801 — Adaptive Thinking Broken Claude Litellm](https://github.com/google/adk-python/issues/4801)

官方文档：

- [AWS Bedrock — Claude 自适应思考（adaptive thinking）](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-adaptive-thinking.html)（确认 `effort:max` = always think、adaptive 自动启用 interleaved thinking、请求格式无 `display` 字段）
- [LiteLLM /v1/messages (anthropic_unified)](https://docs.litellm.ai/docs/anthropic_unified/)
- [LiteLLM 'Thinking' / 'Reasoning Content'](https://docs.litellm.ai/docs/reasoning_content)
