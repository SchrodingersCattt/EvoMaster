# 阶段三 c：native `responses` transport（详细设计）

- 日期：2026-06-09
- 状态：brainstorming 逐段确认完成，待落实施计划
- 上游：
  - `docs/superpowers/specs/2026-06-06-provider-aggregation-design.md`（三阶段总方向，第 4 节硬约束 + 第 7 节阶段三蓝图）
  - `docs/superpowers/specs/2026-06-07-provider-aggregation-stage2-design.md`（阶段二聚合核心：`Transport` 基类 / dispatch 表 / `convert_messages` 接缝）
  - `docs/superpowers/specs/2026-06-07-provider-aggregation-stage3a-design.md`（中立 IR + provider_state 全链路通道）
  - `docs/superpowers/specs/2026-06-08-provider-aggregation-stage3b-design.md`（native anthropic_messages：首产真实 provider_state、tag-丢弃实际生效）
- 范围：把母文档第 7 节阶段三的最后子阶段 3c 落地——新增 native OpenAI `responses` transport，把 `matmaster/gpt-5.5` 从 litellm/chat_completions **原地迁移**到 native responses，第二次产真实 `provider_state`（encrypted reasoning items 回放），三 transport 并存使 tag-丢弃再压测。**仅 litellm responses 网关一条连接**（无直连 openai 备选）。

> 母文档第 4 节「已确认决策」仍为硬约束。本文以 3a 的 IR/provider_state 通道与 3b 的 native transport 接缝为地基（写 3c plan 前 3a/3b 必须已落地——当前分支 `codex/provider-stage1` 已落地并提交）。

---

## 1. 前置依赖与现状基线（文档与代码的关键校正）

3c 依赖 3a/3b 产物：中立 `list[Message]` 契约、`ProviderState` 模型、`AssistantMessage/LLMResponse/StreamChunk.provider_state` 字段、`agent_llm_stream` 流末聚合 provider_state、`agent.py` 组装写入、持久化/resume 全链路、`Transport._claim_provider_state` tag-丢弃 helper、`Transport` 生命周期（`_open_client`/`_close_client`/`__aenter__` 计数）与 timeout/retry property、factory dispatch 表与 fail-fast。

**与母文档/3a/3b 描述的实质差异（以当前代码 + 本次决策为准，写进基线避免被旧文档误导）**：

| 母文档/旧文档假设 | 当前事实（截至 2026-06-09） |
|---|---|
| 母文档 §8.3：`openai-responses: {transport: responses, api_key: ${OPENAI_API_KEY}}` 直连 | 本次决策**仅 litellm responses 网关**：新增 `litellm-responses` provider（复用 `${LITELLM_PROXY_API_KEY}` + 新 `${LITELLM_PROXY_RESPONSES_BASE}`），**无直连 openai 备选** |
| 母文档 §7.6：3c 删旧 OpenAI 通道 | `matmaster/gpt-5.5` 现走 `litellm`/`chat_completions`；3c **原地迁移**该 profile（profile key/model 不变、仅翻 `provider`→`litellm-responses`）。chat_completions transport 保留（qwen/gemini/deepseek 仍用），不删 |
| 3b 需新增 `PromptCacheConfig` 配置 schema | 3c **零 config schema 改动**：responses 复用已有扁平字段 `reasoning_effort`/`reasoning_summary`/`max_tokens`（`LLMProfileConfig` 现有）；OpenAI Responses 自动缓存、无断点配置 |
| 3a/3b：provider_state 承载 anthropic signed thinking | 3c payload 换成 **encrypted reasoning items**（`store=false` + `include=["reasoning.encrypted_content"]`）；通道/聚合/持久化/tag-丢弃 3a 已建好、3c 不动 |
| 3b 假设 anthropic SDK 0.79.0（实际 0.107.1，多处 caveat 失效） | 3c 已实测 openai SDK **2.20.0** 的 Responses 面（见下），不沿用任何旧 SDK 假设 |

**依赖实测确认（openai 2.20.0）**：

- `client.responses.create/stream` 参数齐备：`input` / `instructions` / `reasoning` / `include` / `store` / `tools` / `tool_choice` / `max_output_tokens` / `text` / `temperature` / `parallel_tool_calls` / `previous_response_id` / `model` 等；`stream` 只属于 `responses.create(...)` 路径，`responses.stream(...)` helper **无 `stream` 形参**。
- `async with client.responses.stream(**kwargs) as s: final = await s.get_final_response()` 可用（`AsyncResponseStream` 有 `get_final_response` / `until_done`）。
- 流事件类型已核：`ResponseTextDeltaEvent`（output_text.delta）、`ResponseReasoningSummaryTextDeltaEvent`、`ResponseReasoningTextDeltaEvent`、`ResponseFunctionCallArgumentsDeltaEvent`、`ResponseOutputItemAddedEvent`、`ResponseOutputItemDoneEvent`、`ResponseCompletedEvent`、`ResponseIncompleteEvent`、`ResponseFailedEvent`、`ResponseRefusalDeltaEvent`/`ResponseRefusalDoneEvent`。
- openai 异常体系（`openai.APITimeoutError` 等）与 chat_completions 共用，classify_error 可大量复用现成形态。

3c 真正改造/新增的文件：

| 文件 | 角色 | 3c 动作 |
|---|---|---|
| `matmaster/providers/transports/responses.py` | 不存在 | **新增** `ResponsesTransport(Transport)` |
| `matmaster/providers/llm_factory.py` | dispatch 表有 chat_completions + anthropic_messages | 加 `"responses": _build_responses_transport` + builder |
| `config/llm_config.yaml` | gpt-5.5 走 litellm/chat_completions | 新增 `litellm-responses` provider 连接；`matmaster/gpt-5.5` profile 翻 `provider` |
| `.env`（仓库外） | — | 新增 `LITELLM_PROXY_RESPONSES_BASE` |

**kernel 主循环、IR 字段、持久化 schema、`matmaster/config/llm.py` 在 3c 零改动**——3a 建通道、3b 验 native 接缝，3c 只填「Responses 真实转换 + 产 encrypted reasoning state」。

---

## 2. 接入范围（迁移 gpt-5.5 到 native responses）

3c 交付：
1. `ResponsesTransport`：native input item / function_call / function_call_output 转换、native reasoning（summary 展示 + encrypted_content 回放）、stream/usage/finish_reason 归一、错误分类、provider_state 存取回放。
2. factory dispatch 加 `"responses"`。
3. config 加 `litellm-responses` provider 连接 + `matmaster/gpt-5.5` profile 迁移。
4. **第二次产真实 `provider_state`**（encrypted reasoning items）。
5. **三 transport 并存**（chat_completions + anthropic_messages + responses）使 tag-丢弃再压测。

只迁 `matmaster/gpt-5.5` 一个 profile：transport 实现量与接几个模型无关；其余 OpenAI 推理模型后续加 config 即可。

---

## 3. 唯一设计分叉：provider_state payload 形状（方案 A，已选）

| | 方案 A（选用，对齐 3b）| 方案 B（放弃）|
|---|---|---|
| payload | `{"reasoning": [<reasoning item 原样 dict: type/id/summary/encrypted_content>, ...]}` | 整个 assistant output item 数组原样（含 function_call）|
| function_call 来源 | 从 `ToolCallData` 重建（母文档 #8：tool 信息单一归 `ToolCallData`）| payload 双存 |
| 重建顺序 | `[reasoning items, easy assistant message?, function_call items]`（assistant 文本走 EasyInputMessage，§7.3）| 原样回放，任意序最忠实 |
| 取舍 | 与 3b thinking payload 决策一致、payload 轻、无 tool 漂移；依赖顺序/wire 不变量（见 §7.4）| tool_use id/name/arguments 双存→漂移风险、payload 重 |

选 **方案 A**：与 3b 的 thinking payload（`{"thinking":[...]}`）结构对称，符合母文档决策 #8。**前提（最高风险，plan 首个任务用真实 round-trip spike 验证）**：Responses 单响应内 reasoning 与输出项的顺序/wire 形状（§7.4）。spike 通过则维持方案 A；若 reasoning 回放因顺序或缺 output item 元数据被拒，则降级方案 B（payload 存原始 output item 原样、含 id/status，接受 tool 双存、按母文档 #8 重评边界）。

---

## 4. stateless 回放（锁定，非 stateful）

母文档 §7.5「encrypted reasoning items + response item id 回放」即 **stateless**：

- 请求恒带 `store=false` + `include=["reasoning.encrypted_content"]`。
- 每轮把历史 assistant 回合的 reasoning item（含 `encrypted_content`）原样回放进 `input`，服务端解密续推。
- **不用** `previous_response_id`（stateful 链式）：它建服务端 session 依赖、跨 worker / 持久化 resume 不成立、与 3a/3b 自包含回放架构冲突。SDK 有该参数，刻意不碰。

stateless 与 anthropic signed thinking 回放同构：provider_state 是自包含、可持久化、跨重启可回放的不透明块；3a 通道无需任何改动即可承载。

---

## 5. 模块布局与 factory 接入

```
providers/
  transport.py                     # 3a 基类，复用
  llm_factory.py                   # 加 responses dispatch
  transports/
    chat_completions.py            # 不动（qwen/gemini/deepseek + BYOK 仍用）
    anthropic_messages.py          # 不动
    responses.py                   # 新增
```

`llm_factory.py`：
```python
_TRANSPORT_BUILDERS = {
    "chat_completions": _build_chat_completions_transport,
    "anthropic_messages": _build_anthropic_messages_transport,
    "responses": _build_responses_transport,        # 新增
}
```

`_build_responses_transport(profile, provider, *, extra_body=None)`：把 profile 平铺字段（model / reasoning_effort / reasoning_summary / max_tokens / timeout 系列 / retries）+ provider 连接（api_key / base_url）传给 transport 构造。`extra_body is not None` 时 raise（同 anthropic builder；BYOK 仍 chat_completions，responses 不接 extra_body）。未命中 transport 仍 fail-fast（3a 行为不变）。

transport 的 `_open_client` 构造 `AsyncOpenAI(api_key=..., base_url=..., timeout=self._timeout, max_retries=0, http_client=...)`——**显式 `max_retries=0`**（重试权归 kernel，与 chat_completions / anthropic 一致），`http_client` 的 read timeout 由 stream timeout 推导（复用 chat_completions / anthropic 的 `max(stream_idle_timeout, stream_timeout)+10` 公式）。

BYOK 仍 `transport=chat_completions`（母文档：BYOK 自带 key 留未来，非 3c）。

---

## 6. 配置

### 6.1 provider 连接（仅网关一条）
```yaml
providers:
  litellm-responses:                       # 经 litellm responses-passthrough 网关
    transport: responses
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_RESPONSES_BASE}"   # OpenAI SDK base_url 根；SDK 在其后拼 /responses
```
`ProviderConfig{transport, api_key, base_url}`（现有）原样支持，无需改 schema。transport 把 `base_url` 透传给 `AsyncOpenAI(base_url=...)`。`LITELLM_PROXY_RESPONSES_BASE` 必须是 SDK 根路径（例如 `https://<litellm-host>/v1`），不要包含尾部 `/responses`；否则 SDK 会请求 `/responses/responses`。**无直连 openai provider**（本次决策）——相应地 §13 风险 #1（网关透明性）无 in-house A/B 对照，列为硬前置。

### 6.2 gpt-5.5 profile 迁移（原地翻 provider）
```yaml
profiles:
  matmaster/gpt-5.5:                # profile key 不变 → 前端 model_override 无感
    provider: litellm-responses     # 唯一改动：litellm → litellm-responses
    model: matmaster/gpt-5.5        # litellm route 名不变（网关按此路由到 Responses 模型）
    reasoning_effort: xhigh         # → reasoning.effort（原样透传，非标准枚举，见 §8）
    reasoning_summary: detailed     # → reasoning.summary
    context_limit: 256000
    supports_vision: true
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0
```
- 「迁移」= 原地改 `provider` 字段；无孤儿 profile，前端 `model_override = matmaster/gpt-5.5` 仍有效，仅底层 transport 由 chat_completions 换成 responses。
- `default: matmaster/qwen3.7-max` 不变；其它 profile 不动。
- `max_tokens` 缺省不设 → 不发 `max_output_tokens`，模型用自身输出上限（与现有缺省一致）。

### 6.3 `matmaster/config/llm.py` 不改（零 schema 演进）
responses 所需语义全部命中现有 `LLMProfileConfig` 扁平字段：`reasoning_effort`（→`reasoning.effort`）、`reasoning_summary`（→`reasoning.summary`）、`max_tokens`（→`max_output_tokens`）、`context_limit`、`supports_vision`、`temperature`（responses 忽略，见 §8）。OpenAI Responses 自动缓存、无断点配置，故**不引入** prompt-cache 一类新配置模型。这是 3c 相对 3b 的简化点。

---

## 7. 消息转换：`convert_messages`（与 chat_completions / anthropic 都结构性不同）

Responses 的 `input` 是「item 列表」，与 chat completions 的 message 列表、anthropic 的 messages+system 都不同。三处差异 transport 内部处理。

### 7.1 system 抽取（顶层 `instructions`，不在 input）
`build_kwargs` 从 canonical `list[Message]` 取出 `SystemMessage`（多条 `\n\n` 拼接）→ 顶层 `instructions` kwarg，其余消息 → `input` item 列表。kernel 只产一个 system prompt。

### 7.2 消息项映射

| IR | Responses input item |
|---|---|
| `SystemMessage` | 抽到顶层 `instructions`（§7.1），不进 input |
| `UserMessage` | `{"role":"user","content":[{"type":"input_text","text":...}, {"type":"input_image","image_url":<url 或 data-uri>, "detail":<low/high/auto>}]}`（注意 `input_text`/`input_image`，区别于 chat 的 `text`/`image_url`；`image_url` 是字符串而非嵌套对象。**`detail` 恒发**：`ImageContentPart.detail` 为 None 时填 `"auto"`——已实测 SDK `ResponseInputImageParam.detail` 是 `Required[Literal[low/high/auto]]`，None/缺省非法；历史恢复的图片可能无 detail，见 §13 风险 #6 与 §14 测试）|
| `AssistantMessage` | 见 §7.3 重建（reasoning items + easy assistant message + function_call items）|
| `ToolMessage` | `{"type":"function_call_output","call_id":<tool_call_id>,"output":<content or "">}`，按 §7.5 放在紧跟 assistant function_call 的位置 |

> tool 配对 id：Responses 的配对键是 `call_id`（`call_...`），与 function_call item 的 `id`（`fc_...`，服务端输出 item id）不同。`ToolCallData.id` 存 **`call_id`**（配对用），`ToolMessage.tool_call_id` 同；function_call item 的 `fc_...` id 在 input 上**非必填**（stateless 回放只需 call_id/name/arguments），故方案 A 不存它。配对由 3a 中立 `validate_tool_turn_sequence(list[Message])` 在 convert 前校验。

### 7.3 assistant 重建（方案 A）
`AssistantMessage` → 按序拼：
1. **reasoning items**：`_claim_provider_state(message)` 取 payload（tag 匹配 `"responses"` 才认领，否则丢弃，§7.6）；`payload["reasoning"]` 中每个 item 原样作为 input item（`{"type":"reasoning", ...}` 含 `encrypted_content`）。
2. **assistant 文本**（仅 `content` 非空时）：用 **EasyInputMessage** 形状 `{"role":"assistant","content":content}`（bare str）。**不用** `ResponseOutputMessageParam`——已实测 openai 2.20.0：它 REQ `id`/`status`，其 `output_text` part 还 REQ `annotations`；方案 A 不存这些元数据，按该形状会 400。EasyInputMessage 仅 REQ `role`/`content`，无需 id/status/annotations。
3. **function_call items**（来自 `tool_calls`）：每个 `{"type":"function_call","call_id":tc.id,"name":tc.name,"arguments":tc.arguments_json}`。已实测 function_call input item 仅 REQ `call_id`/`name`/`arguments`/`type`（`id`/`status` 可选），从 `ToolCallData` 重建对回放无损（不需存 `fc_...` id）。

### 7.4 reasoning 回放的 wire 形状与顺序（最高风险，plan 首个任务 spike 验证）
本节是 3c 最不确定处，**实施计划第一个任务必须用真实 gateway round-trip 验证**（多 tool 轮 + reasoning 回放），结果决定方案 A 是否成立。
- **已实测 wire 形状**（openai 2.20.0，确定）：assistant 文本走 EasyInputMessage（§7.3 步 2）；function_call / function_call_output 从 `ToolCallData` / `ToolMessage` 重建无损（§7.3 步 3、§7.5）；reasoning item 原样回放（REQ id/summary/type，encrypted_content 可选）。
- **官方顺序约束**：input 里 reasoning item 不得孤儿/尾随——其后必须紧跟至少一个非 reasoning 输出项（assistant message / function_call），否则 400（"reasoning item ... without its required following item" 一类）。
- **重建规则**：仅当该 assistant 回合**有 content 或 tool_calls** 时才回放其 reasoning items；**纯空回合丢弃 reasoning**（避免孤儿 400）。规范输出序：`[reasoning items, easy assistant message(若 content), function_call items(若 tool_calls)]`。
- **待 spike 验证的开放问题**：① `[reasoning, easy assistant message]`（纯文本回合）是否被接受；② `[reasoning, easy message, function_call]`（文本+工具同回合）reasoning 与 function_call 间隔一条 message 是否破坏「紧跟」约束；③ 是否要求每个 reasoning 与其 function_call **严格 1:1 交错**。
- **降级路径**：若 spike 显示上述任一被拒，方案 A 退化为**方案 B**——payload 存该回合原始 output item 数组原样（reasoning + message(带 id/status/annotations) + function_call，顺序原样），convert 原样回放；接受 tool 信息双存、按母文档 #8 重评边界。spike 通过则维持方案 A（payload 仅存 reasoning）。

### 7.5 function_call_output 放置（per-message 顺序映射，无需 scan-ahead）
不同于 anthropic（tool_result 必须合进紧跟的同一 user 消息、且排在 text 前），Responses input 是扁平 item 列表，function_call 与 function_call_output 靠 `call_id` 配对、位置无关（仅需 output 在其 call 之后）。故 convert 是**按 IR 顺序逐条映射**（同 chat_completions 风格，非 anthropic 的 scan-ahead 合并）：
- `AssistantMessage` → 展开为多个 item（§7.3：reasoning + output_text + function_call），同回合 item 连续 emit。
- `ToolMessage` → 单个 `{"type":"function_call_output","call_id":tool_call_id,"output":content or ""}` item。
- `UserMessage` → 单个 user item；`SystemMessage` → instructions（不进 input）。
- 因按对话顺序映射，「function_call 在前、其 output 在后」与「同回合 reasoning 块紧跟其 output」天然成立（§7.4 约束被满足），无需分组或前瞻。

### 7.6 tag-丢弃（三 transport 并存生效）
`_claim_provider_state(msg)`：tag == `"responses"` 返回 payload，否则 None。手动跨协议切模型时，responses transport 见 anthropic/chat_completions tag 的 provider_state → 丢弃（不把 encrypted reasoning 发错端、也不把别家签名块当 reasoning），保留 `content` + `tool_calls`。反向（anthropic/chat_completions 见 responses tag）3b/3a 已处理。无需改 kernel。

### 7.7 tools 转换（transport 内，kernel 仍传 OpenAI chat dict）
OpenAI chat `{"type":"function","function":{"name","description","parameters"}}` → Responses **扁平** function tool `{"type":"function","name","description","parameters","strict":false}`（符合 3a §3.1：tools IR 化非本阶段，各 transport 自转 native）。`parameters` 缺省 `{"type":"object","properties":{}}`。**`strict` 恒发 `false`**：已实测 SDK `FunctionToolParam.strict` 是 `Required[Optional[bool]]`（TypedDict 静态必填，运行时不报错，但网关/服务端可能按 schema 校验）；当前 tool catalog 产出的 schema 非 strict-mode 兼容（strict 要求全字段 required + `additionalProperties:false` 等），故显式 `false` 保行为、**不 opt-in** strict。

### 7.8 tool_choice 映射（无 fail-fast，区别于 anthropic）
Responses 推理模型**支持** required / 强制 function（不像 anthropic extended thinking 只兼容 auto/none）。故直接映射，不 fail-fast：

| kernel 入参 | Responses |
|---|---|
| `"auto"` / None | `"auto"` |
| `"none"` | `"none"`（无 tools 时省略 tools/tool_choice，compaction 纯 summary 调用）|
| `"required"` / `"any"` | `"required"` |
| `{"type":"function","function":{"name":X}}` | `{"type":"function","name":X}` |

主链路 `chat_stream` 不传 tool_choice（→auto），compaction 传 `"none"`；映射表与测试就此锁死正确行为。

---

## 8. build_kwargs：reasoning / include / store / temperature / max_output_tokens

- `instructions`（§7.1 system 抽取）；`input = self.convert_messages(messages)`。
- `reasoning = {"effort": reasoning_effort}`；`reasoning_summary` 非空时加 `"summary": reasoning_summary`。
  - `effort` **原样透传**（gpt-5.5 用 `xhigh`，非标准 OpenAI 枚举 minimal/low/medium/high）——本地不枚举校验，交网关/模型，与 chat_completions 的透传风格一致（§13 风险 #4）。
- `include = ["reasoning.encrypted_content"]` + `store = False`：**恒发**（本 transport 即 stateless 推理回放语义）。
- **temperature 不发**：推理模型对 temperature 受限（gpt-5 系列）；与 anthropic thinking 省 temperature 同理。`profile.temperature` 对本 transport 不生效。
- `max_output_tokens`：仅 `profile.max_tokens` 非 None 时发（gpt-5.5 缺省不发，模型用自身上限）。
- `text.verbosity`：YAGNI，本期不加配置面（未来需要再加 profile 字段）。
- `parallel_tool_calls`：不显式设（用 SDK 默认）；kernel 已能处理并行工具结果。
- `tools` / `tool_choice`：见 §7.7 / §7.8。
- **不发 `stream` kwarg**：chat() 与 chat_stream() 都经 `client.responses.stream(**kwargs)`（SDK streaming helper，内部自调 `create(stream=True)`）。已实测 `responses.stream()` **无 `stream` 形参**，传 `stream=True` 会 `TypeError`。故 build_kwargs **不得**写 `kwargs["stream"]=True`（`stream` 形参对本 transport 是 no-op；与 anthropic 一致——anthropic chat_stream 也调 `build_kwargs(messages, tools)` 不带 stream、再 `messages.stream(**kwargs)`）。`.create(stream=True)` 路径本 transport 不用。

---

## 9. reasoning + provider_state（3c 核心）

### 9.1 三类内容去向
| 类别 | 字段 | 来源 | 用途 |
|---|---|---|---|
| 可见内容 | `content` | `message` item 的 `output_text` | 用户/前端 |
| 展示 reasoning | `reasoning_content` | `reasoning_summary_text`（best-effort `reasoning_text`）增量聚合 | ThoughtEvent 流（复用现有）|
| 回放状态 | `provider_state` | `reasoning` item 原样（含 `encrypted_content`）| 续轮原样回传，不展示 |

展示文本（summary）与 payload 内 reasoning item 少量信息重复——正确优先（回放要完整未改的 item）；与 3b 同取舍。

### 9.2 native 回放硬约束（官方）
见 §7.4：reasoning item 须 stateless 回传（`encrypted_content` 携加密思维），且不得孤儿/尾随。`encrypted_content` 仅在 `include=["reasoning.encrypted_content"]` + `store=false` 时返回（§13 风险 #3）。

### 9.3 provider_state 结构（方案 A）
```python
provider_state = ProviderState(
    transport="responses",
    payload={"reasoning": [<reasoning item 原样 JSON dict>, ...]},  # 含 type/id/summary/encrypted_content
)
```
- payload 是纯 JSON（满足 3a §4.1 契约：reasoning item 经 `model_dump(mode="json")` 降解为 JSON；`encrypted_content` 是 str、`summary` 是 list[dict]、`id` 是 str，皆 JSON）。
- 重建顺序见 §7.3；并行 function_call 顺序取自 `tool_calls`。
- tool 信息单一归 `ToolCallData`（母文档 #8），不在 payload 重复。

### 9.4 tag-丢弃实际生效（三 transport）
见 §7.6。3c 引入第三个 transport 后，tag 矩阵补全（responses ↔ anthropic_messages ↔ chat_completions 互相丢弃），用纯函数单测覆盖三向。

---

## 10. normalize_stream / normalize_response / chat()

### 10.1 流式（event 化，事件名已核 openai 2.20.0）
解析 Responses 流事件：
- `ResponseTextDeltaEvent`（output_text.delta）→ `StreamChunk(content=delta)`。
- `ResponseReasoningSummaryTextDeltaEvent` → `StreamChunk(reasoning_content=delta)`（gpt-5 summarized reasoning 主展示路径）；`ResponseReasoningTextDeltaEvent` 同样映射（best-effort，部分模型才有原始 reasoning 文本）。
- `ResponseOutputItemAddedEvent` 且 `item.type=="function_call"` → `StreamChunk(tool_call_deltas=[{"index":<逻辑序>, "id":<call_id>, "name":<name>}])`；用 `item_id → 逻辑序` 映射分配稳定逻辑序（首见即分配，自增）。
- `ResponseFunctionCallArgumentsDeltaEvent` → 按 `item_id` 查逻辑序 → `StreamChunk(tool_call_deltas=[{"index":<逻辑序>, "arguments":delta}])`。
  - native function_call 自带稳定 `call_id`，**不复用** chat_completions 的 `_StreamToolCallState` 索引去重（那是 OpenAI-proxy chat 特有问题）；按 item 累积即可。
- `ResponseOutputItemDoneEvent` 且 `item.type=="reasoning"` → 缓冲该 reasoning item（含 `encrypted_content`）作为 provider_state 备选源。
- `ResponseRefusalDeltaEvent`/`Done` → refusal 文本并入 `content`（拒答即模型可见回复）；终态置 finish_reason `content_filter`。
- **`ResponseCompletedEvent` 为权威终态**：从 `event.response.output` 抽全部 `type=="reasoning"` item（含 `encrypted_content`，经 `_dump_model` 转 JSON）→ 流末 `StreamChunk(provider_state=ProviderState("responses", {"reasoning":[...]}))`；`event.response.usage`→usage chunk；`event.response.status` / `incomplete_details`→finish_reason chunk。
  - 取终态 output 的 reasoning item 比逐 `output_item.done` 拼更稳；`output_item.done` 缓冲仅作 completed 缺 output 时的 defensive 备选。
- `ResponseFailedEvent` / `ResponseIncompleteEvent`：failed → 抛 `event.response.error` 给 classify_error；incomplete → 终态走 finish_reason 映射。
- **流末顺序**：先 emit `provider_state` chunk，再 emit `usage` chunk（3a `stream_llm_items` 聚合点自动捕获 provider_state「最后非 None 胜」与 usage）。

### 10.2 非流式 `chat()`（规避长 timeout guard）
`chat()` 内部用 `async with client.responses.stream(**kwargs) as s: final = await s.get_final_response()`，再 `normalize_response(final)`——规避 openai SDK 对长 timeout 非流式请求的 guard（compaction profile timeout=1200s 会触发）；与 anthropic `chat()` 策略一致。仅 compaction（`tool_choice="none"`）走此路。`normalize_response` 从 final Response 的 `output` items 抽 reasoning（→provider_state + reasoning_content summary）/ message output_text（→content）/ function_call（→tool_calls），usage 归一，status→finish_reason。

### 10.3 usage 归一
| Responses usage | scalar dict |
|---|---|
| input_tokens | prompt_tokens |
| output_tokens | completion_tokens |
| total_tokens | total_tokens |
| input_tokens_details.cached_tokens | cache_read_tokens |
| output_tokens_details.reasoning_tokens | reasoning_tokens |

OpenAI 自动缓存**无 cache_write**（不报 write tokens）。`usage_vendor` 存 native usage 原貌（保留 details 细分，供前端/评测确认缓存命中）。取值用 best-effort `getattr`/`_dump_model`（同两个现有 transport），字段缺失不报错。

### 10.4 finish_reason 映射（接 finish_diagnostics）
`finish_diagnostics.py` / `agent.py` 消费 OpenAI 风格值。Responses 无单一 `stop_reason`，由 output items + status 推断：
| 条件 | kernel finish_reason |
|---|---|
| output 含 `function_call` item | `tool_calls` |
| status `incomplete` 且 `incomplete_details.reason == "max_output_tokens"` | `length` |
| refusal / content_filter | `content_filter` |
| status `completed`（无 function_call）| `stop` |
| 其它 incomplete reason | 原样透传或 `stop`（plan 对照 finish_diagnostics 回归）|

> `tool_calls` 优先于 `stop`：一个 completed 且含 function_call 的响应判 `tool_calls`（驱动 kernel 进工具回合）。

---

## 11. classify_error

Responses 走同一套 `openai.*Error`，大量复用 chat_completions 形态，下沉到 `ResponsesTransport.classify_error` 产 `LLMError(retryable, error_category)`：
- `APITimeoutError` / `httpx.ReadTimeout` → retryable / `timeout`
- `APIConnectionError` → retryable / `connection`
- `RateLimitError` → retryable / `rate_limit`
- `InternalServerError` → retryable / `server`
- `AuthenticationError` / `PermissionDeniedError` → non-retryable / `auth`
- `BadRequestError` → 按文本：含 context/token 上限 → non-retryable / `context_overflow`；**Responses 专属非重试 400**（`reasoning` / `encrypted` / `without its required following item` / `previous_response_id` / `store` / `function_call` 协议类）→ non-retryable / `bad_request`；其余 → retryable / `bad_request`
- 已是 `LLMError` → 返回 None（不二次包裹，同两个现有 transport）

不引入自动恢复策略。

---

## 12. 集成点（不改 / 改）

**不改**：
- `BillingLLMProvider` / `UsageCollectingProvider`：`__getattr__` 透传，对 provider_state 天然无感。
- `exp.py` 装配、compaction 控制流（仍调 `chat`，`tool_choice="none"`）。注意：3c 让 gpt-5.5 的 `chat()` 也可能返回真实 `provider_state`（summary 调用也带 reasoning item），compaction 仍按 3a §6.5 由 summary 文本构造合成 `AssistantMessage`、**丢弃**该 provider_state，控制流不变。
- 持久化/resume 链路：3a 已贯通（自然完成分支条件发射、tail restore 携带 provider_state、checkpoint 中立校验、`model_dump(mode="json")`）。3c 产真实 responses provider_state 后自动跟上，**无 schema 变更**（payload 是 JSON-compatible 不透明 dict）。
- kernel 主循环、IR 字段、`stream_llm_items` 聚合、`agent.py` 组装、`matmaster/config/llm.py`。

**改**：
- `llm_factory.py`：dispatch 加 `responses` + builder。
- `config/llm_config.yaml`：`litellm-responses` provider 连接 + `matmaster/gpt-5.5` profile 翻 provider。
- 新增 `responses.py`。

---

## 13. 风险与假设（按严重度）

1. **网关透明性（最高）**：litellm `/v1/responses` 透传必须不改写 `encrypted_content`、不剥 reasoning item、不强制 `store=true`、尊重 `include`、保留 `call_id`。否则 reasoning 回放 400 或失去思维连续性、cache 不命中。**本次仅网关一条连接、无直连 openai A/B 对照**——故列为**硬前置**：plan 启动前需运维确认 `LITELLM_PROXY_RESPONSES_BASE` 是 OpenAI SDK `base_url` 根路径（通常为 `https://<litellm-host>/v1`，不是带 `/responses` 的 endpoint）且该根路径下的 `/responses` 是原始协议透传，并加一次手动 smoke test（多 tool 轮 + reasoning round-trip）。
2. **reasoning 回放顺序约束**：见 §7.4。孤儿/尾随 reasoning item → 400；重建须保证「reasoning 块后紧跟非 reasoning 项」并丢空回合 reasoning。若实测要求严格 1:1 交错，按 §7.4 降级路径切方案 B。
3. **`encrypted_content` 可得性**：依赖网关尊重 `include`+`store=false`；缺失则 stateless 回放降级（reasoning item 无加密块、思维不连续，但普通 function-calling 仍可跑）。plan 验证 round-trip 时断言 `encrypted_content` 存在。
4. **`effort: xhigh` 非标准枚举**：原样透传给网关，需网关/模型接受；若被拒，调 profile 的 `reasoning_effort`。
5. **arguments 字符串 round-trip**：`ToolCallData` 经 `arguments_json`（`json.dumps`）重序列化 function_call.arguments，与原 Responses arguments 串格式或有差异（低风险——function_call 是上下文非再校验；`encrypted_content` 绑定 reasoning item 而非 arguments）。
6. **wire item 形状（已实测 openai 2.20.0，固化为实现硬约束，非「TDD 时再核」）**：① assistant 文本回放走 EasyInputMessage，**不用** output message（后者 REQ id/status，output_text REQ annotations，方案 A 不存→400），§7.3；② `input_image.detail` 必填 `Literal[low/high/auto]`，None→`"auto"`（含历史恢复图片），§7.2；③ function tool 补 `strict:false`，§7.7；④ `responses.stream()` 不收 `stream` kwarg（传则 `TypeError`），§8。
7. **compaction summary 带 reasoning 成本**：transport 不区分调用方，compaction 的 summary 调用也以 effort xhigh 跑 reasoning → 额外 token。3c 接受现状（同 3b note）。

---

## 14. 测试策略（跟随现有测试文化，纯函数为主）

- **convert_messages**：instructions 抽取（→顶层、不进 input）；UserMessage(含 image)→input_text/input_image，**`detail` None→`auto`（含历史恢复无 detail 图片的 convert 用例）**；AssistantMessage 重建顺序（reasoning items→**easy assistant message**→function_call items），assistant 文本是 EasyInputMessage 形状（非 output message）；reasoning 回放仅在有 content/tool_calls 时（空回合丢 reasoning，§7.4）；function_call_output 按 call_id 配对放置（§7.5）；tools chat→Responses 扁平转换**含 `strict:false`**；`validate_tool_turn_sequence` 复用；tag 匹配才认领 reasoning、不匹配丢弃保留 content+tool_calls。
- **build_kwargs**：`reasoning={effort[,summary]}`；`include=["reasoning.encrypted_content"]` + `store=False` 恒发；temperature 省略；`max_output_tokens` 可选透传；tool_choice 映射（auto/none/required/forced-function 全映射、**无 fail-fast**）；instructions；**不产 `stream` kwarg**（chat_stream 也走 `responses.stream()`）。
- **normalize_stream**：output_text.delta→content；reasoning_summary_text.delta(及 reasoning_text.delta)→reasoning_content；output_item.added(function_call)→tool_call_deltas{index,id:call_id,name}；function_call_arguments.delta→tool_call_deltas{index,arguments}（按 item_id 稳定逻辑序）；ResponseCompletedEvent→流末 provider_state(reasoning encrypted items)+usage+finish_reason；usage 归一（含 reasoning_tokens / cached_tokens best-effort）；refusal→content_filter。
- **normalize_response / chat() 非流式**：内部 `stream()`+`get_final_response()`，从 output items 抽三类内容、usage、status→finish_reason。
- **provider_state round-trip**：真实结构（reasoning item+encrypted_content）产出 → `stream_llm_items` 聚合 → `AssistantMessage.provider_state` → `model_dump(mode="json")`/restore round-trip 一致；convert 重建把 reasoning items 排在 easy assistant message/function_call 前。
- **tag-丢弃三 transport**：responses 对 anthropic/chat_completions tag 的 provider_state 返回 None（丢弃）、保留 content+tool_calls；补全三向矩阵。
- **finish_reason 映射**：function_call→tool_calls、incomplete max_output_tokens→length、refusal→content_filter、completed→stop；对照 `finish_diagnostics.py` 回归。
- **classify_error**：各 openai 异常 → 正确 category/retryable；Responses 专属 reasoning-replay bad_request 非重试。
- **factory dispatch**：`"responses"` 命中 builder；builder 把 reasoning_effort / reasoning_summary / max_tokens 正确传入；extra_body 非 None 时 raise。
- **config 回归**：loader 解析 `litellm-responses` provider + 迁移后的 `matmaster/gpt-5.5` profile（provider=litellm-responses、transport 经 provider 解析为 responses）；`default` 不变；构造出 `ResponsesTransport`。
- **构造**：协议一致性（`isinstance(LLMProvider)`、`transport_tag=="responses"`）、client `max_retries=0`、base_url 透传、read timeout 推导。

---

## 15. 明确不做

- 不做直连 openai provider（本次仅网关；直连/BYOK responses 留未来）。
- 不做 stateful `previous_response_id`（stateless 回放锁定）。
- 不做 prompt cache（OpenAI Responses 自动缓存、无断点配置）。
- 不做 BYOK anthropic/openai key（仍 chat_completions）。
- 不做 `text.verbosity` 配置面、不做 automatic fallback、不做 Gemini native、不做 reasoning 明文回传 PR。
- 不引入持久化迁移脚本（provider_state 是 3a 既有可选字段）。
- 不改 kernel 主循环控制流、不改 IR 字段、不改持久化 schema、不改 `matmaster/config/llm.py`。

---

## 16. 完成标准

- `ResponsesTransport` 落地：满足 `LLMProvider` Protocol（经 `Transport` 基类），native input item / function_call / function_call_output 转换、instructions 抽取、reasoning 回放顺序（§7.4）正确；tool_choice 全映射无 fail-fast；client 显式 `max_retries=0`（重试归 kernel）。
- **wire 形状正确（已实测固化、有断言覆盖）**：assistant 文本 EasyInputMessage、`input_image.detail` None→`auto`、function tool `strict:false`、`responses.stream()` 不传 `stream`（§13 风险 #6）。
- factory dispatch 加 `responses`；config 加 `litellm-responses` provider 连接 + `matmaster/gpt-5.5` 迁移（仅翻 provider），loader 只认新结构、零兜底；`matmaster/config/llm.py` 未改。
- reasoning 三类内容分离：content(output_text) / reasoning_content(summary 展示) / provider_state(encrypted reasoning items)；续轮 convert 把 reasoning items 排在 easy assistant message/function_call 前回传。
- 第二次产真实 provider_state，经 3a 通道聚合/持久化/resume round-trip 一致，kernel 全程不透明搬运；断言 `encrypted_content` 存在。
- stateless：`store=false` + `include=["reasoning.encrypted_content"]` 恒发；不用 `previous_response_id`。
- 三 transport 并存 tag-丢弃实际生效（responses ↔ anthropic_messages ↔ chat_completions 三向）。
- status→finish_reason 映射使 finish_diagnostics 回归通过；usage scalar+vendor 归一（reasoning_tokens / cached_tokens）。
- 非流式 chat() 经 `stream()`+`get_final_response()` 规避长 timeout guard，compaction 可运行。
- chat_completions / anthropic_messages 路径完全不受影响（行为等价）。
- convert / build_kwargs / normalize_stream / normalize_response / provider_state round-trip / tag-丢弃 / classify_error / chat() / factory / config 各有独立测试。

---

## 17. 3c → provider 聚合收尾

- 3c 是母文档决策 #1 三协议（chat_completions / anthropic_messages / responses）的最后一个 native transport。落地后母文档第 7 节阶段三蓝图全部完成：中立 IR + 三类内容分离（3a）、native anthropic（3b）、native responses（3c）、旧 Claude/Bedrock 通道（stage2 + 前序 commit 已删）、手动切协议 tag-丢弃（3a 建、3b/3c 压测）、持久化/resume（3a）。
- 母文档明确排除项（Gemini native、automatic fallback）仍不做；预留接口未启用。
- 未来扩展（非本次，IR/接缝已预留）：reasoning 明文回传 PR、BYOK 自带 anthropic/openai key、直连 openai-responses provider、`text.verbosity` 配置面。
