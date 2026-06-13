# Provider 聚合架构三阶段设计

- 日期：2026-06-06
- 状态：三阶段总方向 + 已确认决策，待逐阶段写详细设计
- 范围：定义 provider 聚合重构的阶段边界与已锁定决策；具体 class/字段细节放各阶段详细设计
- 参考：外部 Provider 聚合架构报告、当前仓库 provider 链路审阅、本次 brainstorming 逐段确认结论

> 阶段划分（收束边界 → 抽聚合核心 → native/provider_state）风险递进、可逐阶段确认。
> 第 4 节「已确认决策」是本次 brainstorming 逐段敲定的硬约束，三个阶段都不得偏离。

---

## 1. 背景

当前 MatMaster 的 LLM provider 层已从早期单一 OpenAI provider，演化成多模型、多网关混合状态，但核心仍是 OpenAI-compatible 中心：

- kernel 内部消息最终变成 OpenAI-compatible message dict（`messages.py` 的 `to_api_dict()` 写死 OpenAI 格式），OpenAI 格式是隐式的内部 IR。
- `OpenAIProvider` 实际不是纯 OpenAI provider，而是 Chat Completions / OpenAI-compatible adapter。
- Claude、Qwen、DeepSeek、Gemini 等多数模型当前都走 OpenAI-compatible gateway（LiteLLM proxy）。
- `BedrockProvider` 已存在，把 OpenAI-shaped messages/tools 转成 Bedrock Converse 请求。
- provider 选择偏硬编码：`provider == "bedrock"` 走 Bedrock，其他默认走 OpenAI-compatible provider。
- config model（`LLMProfileConfig`）里直接生成 provider request kwargs：`reasoning_effort`、`extra_body.reasoning`、`extra_body.thinking`、`extra_body.output_config`。

由此导致的债务：

- `provider` 一词时而表示厂商、时而表示 SDK adapter、时而表示网关协议。
- `OpenAIProvider` 里塞进了 Anthropic prompt cache 这类非 OpenAI 语义（约 160 行）。
- 没有原生 Anthropic 通道：Claude 只能走 LiteLLM-OpenAI 协议或 Bedrock Converse。
- `LLMProvider` Protocol 没声明 kernel 实际依赖的 timeout/retry 属性，kernel 甚至读 provider 私有 `_timeout`（`agent_llm_stream.py:263`）。

目标不是一次性做一个完美 provider 平台，而是把重构拆成三个可确认、风险递进的阶段，最终达成对等的多协议 provider 聚合层。

---

## 2. 总目标

```text
Agent kernel 只处理统一的 agent 语义；
provider 差异集中在 provider 聚合层；
新增 provider 优先新增配置/profile/transport，而不是修改 kernel loop。
```

kernel 只关心：历史消息、当前输入、工具定义、工具调用、工具结果、可见回答、reasoning 回调、usage、run result。

provider 聚合层负责：解析请求用哪个 provider、判断走哪种协议、把内部消息/工具转成 wire format、把 raw response 归一化、错误分类、以及（阶段三）provider 私有 replay state 的存取与隔离。

---

## 3. 关键原则

### 3.1 内部语义稳定，外部协议分化
kernel 主循环不理解 OpenAI、Anthropic、Responses 的私有协议；provider adapter 在 API 边界负责转换。

### 3.2 kernel loop 里不写 provider 名字判断
新增 provider 不应导致 `AgentKernel` 出现 `if provider == "anthropic"` 这类分支。

### 3.3 clean migration，不写主路径兼容兜底
项目仍在开发阶段，不为旧配置/旧 schema 在主代码写自动兼容层。需要迁移时手动迁移或外部脚本。

### 3.4 先收束边界，再做抽象，再做 native
```text
阶段一：让当前 provider 身份与协议边界变诚实（行为等价）
阶段二：抽出 provider 聚合核心层（Transport 基类 + chat_completions）
阶段三：引入中立 IR + provider_state + native Anthropic/Responses，并删除旧 Claude 通道
```
顺序不可倒置：若在阶段一就引入 provider_state / native，会把复杂度压到尚未整理好的边界上。

---

## 4. 已确认决策（硬约束，三阶段不得偏离）

本次 brainstorming 逐段敲定，后续任何阶段设计以此为准：

1. **接入范围**：仅三种协议——`chat_completions`、`anthropic_messages`、`responses`。**明确排除 Gemini native**（不做 functionCall / thoughtSignature 一类）。
2. **Claude 全走原生**：阶段三引入 native Anthropic 后，**删除 LiteLLM-Claude 通道 + 删除 `bedrock_provider.py`**，不并存。
3. **不做 automatic fallback**：仅在 IR/registry 预留接口；只实现「手动切协议时按 tag 丢弃 provider_state」。不做跨协议自动切换、不做跨协议占位补全。
4. **内部表示走方案）**：引入中立 IR，但 kernel 改动收敛在 provider 边界（`messages.py` + `agent_llm_stream.py`），主循环结构不动。IR 改造与 `provider_state` 落在阶段三。
5. **门面与协议层合并为一个 `Transport` 基类**：基类即对外门面（满足 `LLMProvider` Protocol、管生命周期与 timeout/retry 属性），子类是各协议实现。不另设独立的 ProviderClient 类。
6. **provider 与 transport 双层**：`provider`（厂商，用户友好，如 qwen/anthropic）声明走哪个 `transport`（协议，内部）。映射收在 registry，常见厂商可代码内置默认。
7. **reasoning 配置只声明意图**：`reasoning: {effort, summary?}`，协议无关。翻译成各协议字段在 transport 的 `build_kwargs` 内部完成。**删除 `reasoning_protocol` / `temperature_policy` 字段及 `_infer_model_family` 中为推断二者而存在的部分**。
8. **provider_state 对 kernel 不透明、transport 私有、带 transport tag**；存于 `AssistantMessage`、`LLMResponse`、`StreamChunk`（聚合）；`ToolCallData` 不动，tool-call 级回放信息归入 `AssistantMessage.provider_state`。
9. **不做运行时 capability 探测**：用静态 profile 声明；只做轻量的请求前 fail-fast 校验（未知 transport、配了该 transport 不支持的特性），不建独立 capability matrix/preflight 子系统。
10. **集成点最小改动**：`BillingLLMProvider` / `UsageCollectingProvider` / exp.py 装配 / compaction 不改；只改 factory、BYOK、`call_llm_streaming`、各 transport 的 `classify_error`。
11. **错误处理**：保留统一 `LLMError(retryable, error_category)` 与 kernel 重试循环；只把「翻译 SDK 异常」下沉到各 transport 的 `classify_error`。不引入自动恢复策略。
12. **测试跟随现有测试文化**：删的代码连测试一起删，改的迁移，新 transport 补纯函数单测。
13. **未来扩展（非本次）**：reasoning 明文回传（单独 PR，IR 已预留 `reasoning_content` 完整保存 + transport 唯一序列化点）；BYOK 自带 anthropic key。

---

## 5. 阶段一：Provider 身份与协议边界收束

### 5.1 目标
让当前系统的 provider 语义变诚实，未知配置 fail-fast，**现有行为保持等价**。不动 IR、不动 kernel、不删任何 provider。

### 5.2 要做

1. **显式区分 provider 与 transport**：配置与运行时区分 `provider`（厂商/网关/凭证源，如 openai/qwen/byok/bedrock）与 `transport`（API 协议，如 `chat_completions`/`bedrock_converse`）。阶段一不引入复杂 registry，routes 能解析出明确 provider + transport 即可。
2. **factory 显式分发并 fail-fast**：取消 `provider == bedrock ? Bedrock : OpenAI` 的隐式分支，改为按 transport 分发，`chat_completions → ChatCompletionsProvider`、`bedrock_converse → BedrockProvider`、未知 → 配置错误。误写 `provider: anthropic` 不再静默走 OpenAI-compatible。
3. **`OpenAIProvider` 命名收束为 `ChatCompletionsProvider`**：clean rename（`openai_provider.py → chat_completions_provider.py`），仍用 OpenAI SDK、仍调 `chat.completions.create(...)`，但名字不再暗示只代表 OpenAI 厂商。（阶段二再把它重构成 transport 子类。）
4. **补全 `LLMProvider` Protocol**：显式声明 `stream_timeout` / `stream_idle_timeout` / `max_retries` / `retry_delay`；移除 kernel 对 provider 私有 `_timeout` 的读取（`agent_llm_stream.py:263`）。
5. **收束 BYOK 身份**：表达为 `provider=byok` / `transport=chat_completions` / `billing_mode=byok`，不再在 bundle 或运行时伪装成平台 OpenAI provider。

### 5.3 不做
- 不改 kernel 主循环、不改 message IR、不删 `to_api_dict()`。
- 不引入 provider_state、不新增 native transport、不删除 Bedrock、不做 fallback。
- 不为 Bedrock 做能力校验工作（它将在阶段三删除，阶段一保持现状不碰）。

### 5.4 完成标准
- 所有 routes 解析出明确 provider + transport；未知 provider/transport fail-fast。
- `OpenAIProvider` 命名不再误导；kernel 不再读 `_timeout`。
- BYOK 不再伪装成平台 OpenAI provider。
- 当前 chat_completions 与 bedrock 路径行为等价。

---

## 6. 阶段二：Provider 聚合核心层

### 6.1 目标
建立真正的聚合核心层，把 provider 类里的职责拆清楚，第一版只落地 `chat_completions`。

### 6.2 模块边界

```text
providers/
  transport.py        # Transport 基类（= 门面）：满足 LLMProvider Protocol，管 SDK client 生命周期、
                      #   timeout/retry 属性、共享 helper（usage 提取等）。不另设 ProviderClient 类。
  registry.py         # provider→transport 映射 + 连接；route 解析。替代 if/elif。
  transports/
    chat_completions.py  # 从 ChatCompletionsProvider 重构为 transport 子类
  llm_factory.py      # 外部入口不变，内部改 registry/transport 驱动
```

Bedrock 在阶段二**暂留旧 `BedrockProvider` 实现**，由 factory 直接构造过渡，**不重写为 transport 子类**（阶段三随 native Anthropic 引入一起删除，避免为将删的代码做抽象）。

### 6.3 Request build 迁移（含 reasoning 整合）
把 provider kwargs 生成从 `LLMProfileConfig` 迁到 transport：

- 配置层只表达语义：`reasoning: {effort, summary?}`、`prompt_cache: {...}`。
- transport 的 `build_kwargs` 决定语义如何变成具体 kwargs：
  - `chat_completions` + openai 风格 → top-level `reasoning_effort`，summary 进 `extra_body.reasoning`；
  - `chat_completions` + anthropic-through-litellm（过渡期仍存在的 Claude-via-proxy）→ `extra_body.thinking` + `extra_body.output_config`。
- **删除** `reasoning_protocol` / `temperature_policy` 字段及 `_infer_model_family` 中为推断二者存在的部分（协议由 transport 决定）。

### 6.4 Message conversion 归属迁移
provider-specific 转换从 kernel/pipeline 移到 transport：`AgentKernel → canonical payload → Transport.convert_messages → wire`。第一版 `chat_completions` 基本直通现有 OpenAI-compatible dict。**真正移除 `to_api_dict()`、引入中立 IR 留到阶段三**（方案 C 渐进实施）。

### 6.5 Response normalization
raw response 映射从 concrete provider 类拆出到 transport：`raw → LLMResponse / StreamChunk`。保留现有返回形状（content / reasoning_content / tool_calls / tool_call_deltas / finish_reason / usage / usage_vendor）；`usage` 仍 scalar dict，`usage_vendor` 仍保留 provider-native detail。

### 6.6 轻量 fail-fast 校验（非 capability 子系统）
仅做静态、就地的请求前校验：未知 transport、或 profile 配了该 transport 明确不支持的特性时 fail-fast。**不建独立 capability matrix/preflight 模块，不做运行时探测**。

### 6.7 不做
- 不新增 native Anthropic / Responses transport、不引入 provider_state、不删 `to_api_dict()`、不删 Bedrock、不做 fallback。

### 6.8 完成标准
- factory 由 registry/transport 驱动，不再 hardcoded if/else。
- chat_completions request kwargs 不再由 config model 直接生成；`reasoning_protocol`/`temperature_policy` 已删。
- message/tool conversion、request build、response normalization 各有独立测试。
- 现有 chat_completions routes 行为等价；Bedrock route 仍可运行（旧实现过渡）。

---

## 7. 阶段三：中立 IR、Provider State、Native Transports，并删除旧 Claude 通道

### 7.1 目标
落地方案 C 的最终形态：中立 IR + 三类内容分离 + provider_state；新增 native Anthropic / Responses；Claude 全走原生后删除旧通道与 Bedrock。

### 7.2 中立 IR 与三类内容分离
`AssistantMessage` 区分三类输出，去掉 `to_api_dict()`（序列化交给 `transport.convert_messages`）：

| 类别 | 字段 | 消费方 | 特性 |
|---|---|---|---|
| 可见内容 | `content` | 用户/前端/session | 已剥离 inline thinking |
| 展示用 reasoning | `reasoning_content` | thought stream/日志/debug | 明文，不回传 API（直到未来回传 PR） |
| provider 回放状态 | `provider_state` | 仅写它的那个 transport | 不透明、不展示、仅供回放 |

- `provider_state` **对 kernel 不透明、transport 私有**：normalize 时由 transport 打包并盖 transport tag，kernel 原样存；convert 时原样交还同一 transport。
- 存于 `AssistantMessage`、`LLMResponse`、`StreamChunk`（由 `stream_llm_items` 聚合）；**`ToolCallData` 不动**，tool-call 级回放信息（Responses item id、Anthropic tool_use 与 thinking 关联）归入 `AssistantMessage.provider_state`。
- inline thinking 剥离复用/补齐 `matmaster/response_text.py`。

### 7.3 手动切协议（不是 fallback）
session 中途用户手动换模型跨了协议时：transport 在 convert 时只认自己 tag 的 `provider_state`，**tag 不匹配就丢弃该回放状态，保留 `content` + `tool_calls`**（不会把别家签名/加密块发出去导致 400）。这是参考方案 sanitizer 的最小退化版。**automatic fallback 不做，仅预留接口**。

### 7.4 Native Anthropic transport（`anthropic_messages`）
原生 anthropic SDK：native message / tool_use / tool_result 转换、native thinking（signed block）、**native prompt cache**、stream/usage 归一、错误分类、provider_state 存取回放。

prompt cache 搬迁：现状 `OpenAIProvider` 那约 160 行里，「塞 OpenAI dict 的 `cache_control`」部分**彻底删**；「选断点策略」（`_select_anthropic_cache_targets`：system / latest_user / tool_result / flexible）**搬到本 transport**，改用 SDK 原生 `cache_control` 注入。`prompt_cache` 的配置字段（`system_prompt_breakpoint`/`automatic`/`latest_user_breakpoint`/`tool_result_breakpoint`/`flexible_breakpoint`/`max_breakpoints`/`min_flexible_chars`/`ttl`）保留在 profile，仅消费方改为本 transport。

### 7.5 Native OpenAI Responses transport（`responses`）
原生 Responses API：input item 转换、function_call / function_call_output 转换、reasoning summary / encrypted reasoning 提取、output/stream 归一、usage 归一、provider_state（encrypted reasoning items + response item id）存取回放。

### 7.6 删除旧 Claude 通道与 Bedrock（已确认）
native Anthropic 落地后：
- 把现有走 LiteLLM-OpenAI 协议的 Claude profile（sonnet / opus_global）迁到 `provider: anthropic` / `transport: anthropic_messages`，**删除 LiteLLM-Claude 通道**。
- **删除 `bedrock_provider.py`（596 行）及其测试**（opus_bedrock 迁移或废弃）。
- 删除随之而来的 `chat_completions` 里 anthropic-through-litellm 的 reasoning 分支。

### 7.7 集成点与错误处理
- 不改：`BillingLLMProvider` / `UsageCollectingProvider`（`__getattr__` 透传，对 `provider_state` 透明）、exp.py 装配（`billing_scope` 用 getattr 软探测）、compaction（调 `chat`）。
- 改：factory（registry 驱动，删 bedrock 分支）、BYOK（构造临时 profile，transport=chat_completions；自带 anthropic key 留未来）、`call_llm_streaming`（已在阶段一去 `_timeout`）、各 transport `classify_error`。
- 错误分类下沉各 transport，`LLMError` + category 与重试循环不变；不引入自动恢复。

### 7.8 Persistence / resume
`provider_state` 进入历史后同步设计 event payload、DB history、checkpoint、history replay、compaction、前端/SSE 是否隐藏、debug/export 是否包含。需要 schema 迁移时用 clean migration 或外部脚本。

### 7.9 不做
- 不把 thinking 拼进普通 content；不把 provider raw response 整包塞历史；不在 kernel loop 写 provider 分支；不在主代码做旧 provider_state 兼容兜底；不做 automatic fallback；不做 Gemini native。

### 7.10 完成标准
- 可见内容 / reasoning 展示 / provider_state 三者分离；`to_api_dict()` 已移除，中立 IR 落地。
- `anthropic_messages` 与 `responses` 两个 native transport 均通过 conversion / tool round-trip / reasoning replay 测试。
- LiteLLM-Claude 通道与 `bedrock_provider.py` 已删除，对应测试同步删除。
- 手动切协议不会把旧 provider_state 发给新 provider；persistence/resume 可验证。

---

## 8. 配置演进

### 8.1 阶段一：显式 transport
```yaml
profiles:
  qwen_3_7_max: {provider: litellm, transport: chat_completions, model: matmaster/qwen3.7-max, api_key: ${LITELLM_PROXY_API_KEY}, base_url: ${LITELLM_PROXY_API_BASE}, context_limit: 1000000}
  opus_bedrock: {provider: bedrock, transport: bedrock_converse, model: "arn:aws:bedrock:...", bedrock_region: ${AWS_REGION}, context_limit: 200000}
```
重点：transport 显式，不再靠 provider 名字隐式推断。

### 8.2 阶段二：连接归 providers 段，reasoning 只声明意图
```yaml
providers:
  litellm: {transport: chat_completions, api_key: ${LITELLM_PROXY_API_KEY}, base_url: ${LITELLM_PROXY_API_BASE}}
profiles:
  qwen_3_7_max:
    provider: litellm
    model: matmaster/qwen3.7-max
    context_limit: 1000000
    reasoning: {effort: high}          # 只声明意图，无 kind/protocol；翻译在 transport
```
连接归 provider，模型语义归 profile。注意 `reasoning` 下**没有** `kind`/`protocol`（协议由 provider→transport 决定）。

### 8.3 阶段三：native provider 取代旧 Claude 通道
```yaml
providers:
  anthropic: {transport: anthropic_messages, api_key: ${ANTHROPIC_API_KEY}}
  openai-responses: {transport: responses, api_key: ${OPENAI_API_KEY}}
profiles:
  sonnet: {provider: anthropic, model: claude-sonnet-4-6, reasoning: {effort: high}, prompt_cache: {system_prompt_breakpoint: true, automatic: true}, context_limit: 200000}
```
sonnet/opus 从 litellm 迁到 anthropic provider；bedrock profile 一并删除。`routes` 与 `default` 机制不变。迁移手动改 `llm_config.yaml`，loader 只认新结构、零兜底。

---

## 9. 测试策略（跟随现有测试文化）

### 9.1 阶段一
config parse / route resolution / provider+transport identity / 未知 fail-fast / BYOK identity / `LLMProvider` Protocol 属性 / chat_completions 行为等价。

### 9.2 阶段二
canonical → chat_completions wire / tools conversion / request build（reasoning 意图→kwargs）/ response + stream 归一 / usage scalar+vendor / 轻量 fail-fast / compaction `tool_choice="none"`。改造 `test_openai_provider*` / `test_llm_factory` / `test_byok_provider`。

### 9.3 阶段三
provider_state extraction / replay / tag mismatch 丢弃 / inline thinking 剥离 / native Anthropic signed thinking / Responses encrypted reasoning / tool round-trip with provider metadata / persistence-resume / 手动切协议。**删除 `test_bedrock_provider.py`**；`test_openai_provider_prompt_cache.py` 的断点策略测试迁到 anthropic transport。
