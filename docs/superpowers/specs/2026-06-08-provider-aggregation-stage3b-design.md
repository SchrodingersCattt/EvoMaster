# 阶段三 b：native anthropic_messages transport（详细设计）

- 日期：2026-06-08
- 状态：brainstorming 逐段确认完成，待落实施计划
- 上游：
  - `docs/superpowers/specs/2026-06-06-provider-aggregation-design.md`（三阶段总方向，第 4 节硬约束 + 第 7 节阶段三蓝图）
  - `docs/superpowers/specs/2026-06-07-provider-aggregation-stage2-design.md`（阶段二聚合核心：`Transport` 基类 / dispatch 表 / `convert_messages` 接缝）
  - `docs/superpowers/specs/2026-06-07-provider-aggregation-stage3a-design.md`（中立 IR + provider_state 全链路通道，仅 chat_completions 验证）
  - `docs/superpowers/specs/2026-05-31-anthropic-prompt-cache-design.md`（原始 prompt cache 设计，断点策略源）
- 范围：把母文档第 7 节阶段三的 3b 子阶段落地——新增 native `anthropic_messages` transport，首次产出真实 `provider_state`（signed thinking 回放），native prompt cache 断点策略重实现，跨协议 tag-丢弃实际生效。**仅 anthropic（opus）一个 native 通道**；`responses` 是 3c。

> 母文档第 4 节「已确认决策」仍为硬约束。本文以 3a 已落地的 IR / provider_state 通道为地基（写 3b plan 前 3a 必须已落地）。

---

## 1. 前置依赖与现状基线（文档与代码的关键校正）

3b 依赖 3a 产物：中立 `list[Message]` 契约、`ProviderState` 模型、`AssistantMessage/LLMResponse/StreamChunk.provider_state` 字段、`agent_llm_stream` 流末聚合 provider_state、`agent.py` 组装写入 provider_state、持久化/resume 全链路（自然完成分支条件发射、tail restore 携带 provider_state、checkpoint 中立校验）、`Transport._claim_provider_state` tag-丢弃 helper。

**与母文档描述的实质差异（以当前代码为准，写进基线避免被旧文档误导）**：

| 母文档/3a 文档假设 | 当前代码事实（截至 2026-06-08） |
|---|---|
| 3b 把走 litellm 的 sonnet/opus「迁回」anthropic、删除 LiteLLM-Claude 通道 | `config/llm_config.yaml` **无任何 Claude profile**（commit `faefdfe1` 已删 opus/opus_global/路由）；3b 是**纯新增**，无迁移/删除对象 |
| 3b 删 `bedrock_provider.py`（596 行） | providers/ 下**早无 bedrock**（stage2 已删）；空操作 |
| 3b 从现存 ~160 行「搬迁」prompt cache 策略 `_select_anthropic_cache_targets` | chat_completions.py **已无任何 prompt cache 代码**（stage2 删除）；3b 是**重新实现**，策略逻辑从 git 历史（`aa3e5d9d~1:matmaster/providers/openai_provider.py`）+ 原始设计文档恢复 |
| 母文档 §8.2 设想嵌套 `reasoning: {effort, summary}` | stage2 落地的是**扁平** `reasoning_effort` / `reasoning_summary`（`LLMProfileConfig`）；3b **沿用扁平**，不引入嵌套（遵循现有代码模式） |
| 3b 做「inline thinking 剥离」 | native anthropic thinking 是**结构化独立 block**，content 天然不含内联 thinking；正则剥离对 native anthropic **N/A**。3b 真正要做的是 thinking **回放**（signed block 回传），由 provider_state 承载 |

依赖事实：
- `anthropic` SDK 已是依赖（`pyproject.toml`，uv.lock 锁 `0.79.0`），直接用 `AsyncAnthropic`。
- reasoning 展示链路就绪：`agent_llm_stream` 已把 `StreamChunk.reasoning_content` 流式发成 `ThoughtEvent`，native thinking 展示侧复用，无需改。
- `LLMProvider` Protocol 已是 `list[Message]`（3a）。

3b 真正改造/新增的文件：

| 文件 | 角色 | 3b 动作 |
|---|---|---|
| `matmaster/providers/transports/anthropic_messages.py` | 不存在 | **新增** `AnthropicMessagesTransport(Transport)` |
| `matmaster/providers/llm_factory.py` | dispatch 表只有 chat_completions | 加 `"anthropic_messages": _build_anthropic_messages_transport` + builder |
| `matmaster/config/llm.py` | 纯数据 profile，无 prompt_cache | 新增 `PromptCacheConfig`，`LLMProfileConfig` 加 `prompt_cache` 字段 |
| `config/llm_config.yaml` | 仅 litellm-openai profiles | 新增 anthropic provider 连接 + opus profile |
| `.env`（仓库外） | — | 新增 `LITELLM_PROXY_ANTHROPIC_BASE`（网关）/ `ANTHROPIC_API_KEY`（直连备选） |

**kernel 主循环、持久化 schema、IR 字段在 3b 零改动**——3a 已把通道建好，3b 只填「真实转换 + 产 state」。

---

## 2. 接入范围（纯新增 native anthropic）

3b 交付：
1. `AnthropicMessagesTransport`：native message/tool/thinking 转换、native prompt cache、stream/usage 归一、错误分类、provider_state 存取回放。
2. factory dispatch 加 `anthropic_messages`。
3. config 加 anthropic provider 连接（两层：网关主路径 + 直连备选）+ opus profile + `PromptCacheConfig`。
4. **首次产真实 `provider_state`**（signed thinking 块）。
5. **跨协议 tag-丢弃实际生效**（3a helper 此前单 transport 惰性，3b 引入第二个 transport 后触发）。

只接 **global.anthropic.claude-opus-4-6-v1（`claude-opus-4-6`）一个 profile**：transport 实现量与接几个模型无关，差别只在 config 行数 + 启用 prompt cache。其余 Claude 模型后续加 config 即可。

---

## 3. 模块布局与 factory 接入

```
providers/
  transport.py                     # 3a 基类，复用（_claim_provider_state / 生命周期 / timeout property）
  llm_factory.py                   # 加 anthropic_messages dispatch
  transports/
    chat_completions.py            # 不动
    anthropic_messages.py          # 新增
```

`llm_factory.py`：
```python
_TRANSPORT_BUILDERS = {
    "chat_completions": _build_chat_completions_transport,
    "anthropic_messages": _build_anthropic_messages_transport,   # 新增
}
```
`_build_anthropic_messages_transport(profile, provider, *, extra_body=None)`：把 profile 平铺字段（model / reasoning_effort / max_tokens / timeout 系列）+ provider 连接（api_key / base_url）+ profile.prompt_cache 转成的 provider-local cache options 传给 transport 构造。未命中 transport 仍 fail-fast（3a 行为不变）。

transport 的 `_open_client` 构造 `AsyncAnthropic(api_key=..., base_url=..., max_retries=0, http_client=...)`——**显式 `max_retries=0`**：anthropic SDK 默认 `DEFAULT_MAX_RETRIES=2`（实测），不关会与 kernel 重试循环叠加（一次 kernel attempt 内含多次 HTTP，污染排障/计费/cancel 语义）；与 `ChatCompletionsTransport`（`max_retries=0`）一致，重试权归 kernel。`http_client` 的 read timeout 比照 chat_completions 由 stream timeout 推导。

BYOK 仍 `transport=chat_completions`（母文档：BYOK 自带 anthropic key 留未来，非 3b）。

---

## 4. 配置

### 4.1 provider 连接两层（你确认的方案）
两条连接都 `transport: anthropic_messages`，靠 `base_url` 区分网关 vs 直连：

```yaml
providers:
  litellm-anthropic:                       # 主路径：经 litellm anthropic-passthrough 网关
    transport: anthropic_messages
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_ANTHROPIC_BASE}"   # anthropic 协议根；SDK 在其后拼 /v1/messages
  anthropic:                               # 备选：直连 api.anthropic.com
    transport: anthropic_messages
    api_key: "${ANTHROPIC_API_KEY}"        # 不设 base_url → SDK 默认端点
```
`ProviderConfig{transport, api_key, base_url}`（3a/stage2 已有）原样支持，无需改 schema。transport 把 `base_url` 透传给 `AsyncAnthropic(base_url=...)`。

### 4.2 opus profile（扁平 schema + 新增 prompt_cache）
```yaml
profiles:
  global.anthropic.claude-opus-4-6-v1:
    provider: litellm-anthropic
    model: claude-opus-4-6                  # 裸名（非 global.anthropic.* 路由名）
    reasoning_effort: max                   # → output_config.effort=max（Opus-only，GA 无 beta header）
    context_limit: 200000                   # 可调；native 实际支持 1M，按预算保守取旧值
    supports_vision: true
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0
    prompt_cache:
      system_prompt_breakpoint: true
      automatic: true
      latest_user_breakpoint: true
      tool_result_breakpoint: true
      flexible_breakpoint: true
      max_breakpoints: 4
      min_flexible_chars: 1000
      ttl: "5m"
```
- `reasoning_summary` 对 anthropic 无意义（thinking 默认 summarized），忽略。
- **`max_tokens` 在当前 Anthropic Messages API 是可选**（仅 `model` / `messages` 必填）。本 profile 缺省不设 → 模型用自身输出上限（Opus 4.6 最高 128K），与现有其它 profile（qwen/gpt/deepseek 等均不设）一致；需要硬上限再加。
- `context_limit: 200000` 可调；native 实际支持 1M，按预算保守取旧值。
- `model` 取决于所选端点：直连 / 透传网关用裸名 `claude-opus-4-6`；若网关按自有 route 名路由，则填该 route 名（可能与 profile key `global.anthropic.claude-opus-4-6-v1` 一致）。见 §12 风险 #1。

### 4.3 `config/llm.py` 新增
```python
class PromptCacheConfig(BaseModel):
    system_prompt_breakpoint: bool = False
    automatic: bool = False
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = 4
    min_flexible_chars: int = 1000
    ttl: Literal["5m", "1h"] = "5m"

    def cache_control(self) -> dict[str, str]:
        cc = {"type": "ephemeral"}
        if self.ttl == "1h":
            cc["ttl"] = "1h"
        return cc
```
`LLMProfileConfig` 加 `prompt_cache: PromptCacheConfig | None = None`。字段名/默认与被删旧版 `AnthropicPromptCacheOptions` 对齐，使从 git 恢复策略逻辑无歧义。

---

## 5. 消息转换：`convert_messages` / build_kwargs（与 chat_completions 结构性不同）

anthropic Messages API 与 OpenAI chat completions 有三处结构差异，transport 各自处理：

### 5.1 system 抽取（顶层参数，不在 messages）
anthropic `system` 是顶层参数。`build_kwargs` 从 canonical `list[Message]` 取出 `SystemMessage` → `system` kwarg（string 或 `[{type:text,text,...}]` block，prompt cache 注入时用 block 形态），其余消息 → `messages` kwarg。kernel 只产一个 system prompt；抽取后 `messages[0]` 为 user，满足 anthropic「首条必须 user」。

### 5.2 消息块映射
| IR | anthropic wire |
|---|---|
| `UserMessage` | `{role:"user", content: [text block / image block]}`；image → `{type:"image", source:{type:"url"\|"base64", ...}}` |
| `AssistantMessage` | 见 §7 重建（thinking 块 + text + tool_use） |
| `ToolMessage` | 合成 `{type:"tool_result", tool_use_id, content}` block，按 §5.2.1 放进紧跟 assistant tool_use 的 user 回合 |

> tool_call id（`ToolCallData.id`）对 anthropic 是 `toolu_...`（normalize 时原样捕获）；`ToolMessage.tool_call_id` 同。配对由 3a 中立 `validate_tool_turn_sequence(list[Message])` 在 convert 前校验。

#### 5.2.1 tool_result 放置规则（硬约束，官方：违反报 400）
官方 handle-tool-calls 两条硬规则：① tool_result 所在 user 消息必须**紧跟**含对应 tool_use 的 assistant 消息，中间不得有其它消息；② 同一 user content 数组里 **tool_result block 必须全部排在任何 text/image block 之前**（text 在前直接 400）。

convert 扫描算法：
- 遇到 `AssistantMessage(tool_calls=...)` → 收集其后**连续的一个或多个 `ToolMessage`**（并行工具 → 多个结果），合成**单个** `role:"user"` 消息，content 先依次放所有 `tool_result` block。
- 若这组 ToolMessage 之后**紧邻**一个须并入同一 user 回合的真实 `UserMessage`，把它的 text/image block 追加在所有 tool_result **之后**。
- 正常 kernel 流里 ToolMessage 后接的是下一个 assistant 回合，少见「tool_result + 真实 user 文本同回合」；算法须正确处理以防 400。

### 5.3 tools 转换（transport 内，kernel 仍传 OpenAI dict）
OpenAI `{"type":"function","function":{"name","description","parameters"}}` → anthropic `{"name","description","input_schema"}`（符合 3a §3.1：tools IR 化非本阶段，各 transport 自转 native）。

### 5.4 tool_choice 映射（受 thinking 恒开约束）
**官方硬约束**：extended thinking + tool use 下，`tool_choice` 只兼容 `auto` 与 `none`；`any` / `tool`（强制工具）会**报错 400**（[define-tools 的 extended thinking 注记](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)，原文："tool_choice: any 和 tool ... are not supported and will result in an error. Only auto and none are compatible with extended thinking"）。本 profile thinking 恒开，故：
| kernel 入参 | anthropic（thinking 恒开） |
|---|---|
| `"auto"` / None | `{"type":"auto"}` |
| `"none"` | `{"type":"none"}`；无 tools 时直接省略 tools/tool_choice（compaction 纯 summary 调用） |
| `"required"` / `"any"` / `{"type":"function","function":{"name":X}}` | **fail-fast** → `LLMError(error_category="bad_request", retryable=False)`（thinking 下强制工具不被支持，不静默发出致 400） |

> 仅当未来新增「关闭 thinking」的调用路径时再放开 any/tool 映射；本阶段 thinking 恒开，强制工具一律 fail-fast。当前主链路 `chat_stream` 不传 tool_choice、compaction 只传 `none`，故现网不触发，但映射表与测试就此锁死正确行为。

---

## 6. build_kwargs：thinking / effort / temperature / max_tokens

- `thinking = {"type": "adaptive", "display": "summarized"}`——显式 summarized 保证 thought stream 有展示文本，且对将来升级 4.7/4.8（默认 omitted）鲁棒。adaptive 自动启用 interleaved thinking，无需 beta header。
- `output_config = {"effort": reasoning_effort}`（`max`，Opus-tier，GA 无 beta header）。
- **temperature**：thinking 开启时**不发** temperature——规避「thinking 启用需 temperature=1」约束（对 4.6/4.7/4.8 都安全）。母文档 #7 删 `temperature_policy` 字段，规则收进此处。本 profile thinking 恒开，故 temperature 始终不发；profile.temperature 对本 transport 不生效。
- `max_tokens`：当前 Anthropic Messages API **可选**（仅 `model` / `messages` 必填）；与 chat_completions 一致，仅 `profile.max_tokens` 非 None 时发送，缺省不发（模型用自身输出上限）。
- prompt cache 注入见 §8。

---

## 7. thinking + provider_state（3b 核心）

### 7.1 三类内容去向
| 类别 | 字段 | 来源 | 用途 |
|---|---|---|---|
| 可见内容 | `content` | `text` block | 用户/前端；天然不含 thinking |
| 展示 reasoning | `reasoning_content` | `thinking_delta` 文本聚合 | ThoughtEvent 流（复用现有） |
| 回放状态 | `provider_state` | thinking 块（文本+签名） | 续轮原样回传，不展示 |

### 7.2 native 回放硬约束（官方）
续轮里 assistant content 必须把原始 thinking 块 `{"type":"thinking","thinking":...,"signature":...}` **原样、且排在 tool_use 之前**回传，否则 API 报错。signature 携带加密完整 thinking，服务端解密续推。`thinking` 文本默认 summarized；回放需 text + signature 都原样。

### 7.3 provider_state 结构（方案 A，已选）
```python
provider_state = ProviderState(
    transport="anthropic_messages",
    payload={"thinking": [<thinking-family block 原样 dict>, ...]},  # {type:"thinking", thinking, signature}；含可能的 redacted_thinking 原样
)
```
- payload 是纯 JSON（满足 3a §4.1 契约：`model_dump(mode="json")` 不抛 + round-trip）。
- **重建顺序不变量**：单次 Messages 响应内容序恒为 `[thinking*, (text | tool_use*)]`——thinking 必在前（模型不会对未收到的 tool 结果思考），故 convert 重建 assistant content = `thinking 块(来自 payload) + [text(来自 content)] + tool_use(来自 ToolCallData)` 是忠实的。并行 tool 调用 → 多个 tool_use 顺序取自 `tool_calls`。
- tool 信息仍单一归 `ToolCallData`（母文档 #8 不动），不在 payload 重复。
- `reasoning_content` 单独存展示文本，与 payload 内 thinking 文本少量重复——正确优先（回放要完整未改的块）。

> 放弃的方案 B：payload 存整个 raw content block 数组（含 tool_use），convert 原样回放。最忠实任意排序但 tool_use id/name/input 双存（ToolCallData + payload）→ 漂移风险、payload 更重。§7.2 不变量使 A 已足够。

### 7.4 tag-丢弃实际生效
3a 的 `_claim_provider_state(msg)`：tag == `self.transport_tag` 返回 payload，否则 None。3b 引入第二个 transport 后，**手动跨协议切模型**时 anthropic transport 见到他家 tag（如 chat_completions）的 provider_state → 丢弃签名块、保留 content + tool_calls（避免把签名块发给非 anthropic 端致 400）。chat_completions 见 anthropic tag 同理丢弃。无需改 kernel——3a 契约此处首次真实触发。

---

## 8. prompt cache native 重实现

断点选择策略从 git 历史恢复（`_select_anthropic_cache_targets`：按序 system → latest_user → tool_result → flexible，受 `max_breakpoints`=4 / `min_flexible_chars` 约束），**注入机制改 native**：

- system breakpoint：`system` 用 block 形态 `[{type:"text", text, cache_control}]`。
- latest_user / tool_result breakpoint：目标 message 的最后一个 content block 上加 `cache_control`；content 是 string 时转成 `[{type:"text", text, cache_control}]`。
- flexible breakpoint：更早的、长度 ≥ `min_flexible_chars` 的消息上加断点。
- `automatic: true` → 顶层 cache_control。**SDK 0.79.0 的 `messages.create/stream` 无 `cache_control` typed 参数**（实测签名缺失），故经 `kwargs["extra_body"]["cache_control"]={"type":"ephemeral"}` 注入（占 1 个断点 slot），**不可**写成顶层 kwarg（会 `TypeError`）。block 级 cache_control（system/message content block 内）是 SDK 完全支持的主路径，由我们自建 dict 注入。需确认 SDK 所发 anthropic-version 接受顶层 cache_control；若不接受，则关掉 `automatic`、靠显式 `latest_user_breakpoint` 覆盖同一动态前沿。
- `ttl: "1h"` → block 级 `cache_control={"type":"ephemeral","ttl":"1h"}`（automatic 同理走 extra_body）。
- 前缀顺序 `tools → system → messages`：system block 上的断点实际缓存 tools+system 前缀。
- **Opus 4.6 最小可缓存前缀 4096 tokens**：低于此静默不缓存（非错误），写进策略注释。

provider-local options dataclass（如 `AnthropicPromptCacheOptions`）承载窄数据，不让 transport 反依赖 config model；factory 从 `profile.prompt_cache` 转入（沿用旧设计 D1/D3）。注入只发生在请求边界，不回写 IR / pipeline / 持久化历史。

---

## 9. normalize_response / normalize_stream / chat()

### 9.1 流式（block 化事件，重写）
解析 anthropic SSE 事件：
- `message_start` → 初始 usage（input_tokens / cache_read_input_tokens / cache_creation_input_tokens）。
- `content_block_start`：`thinking` / `text` / `tool_use`（tool_use 带 id、name、空 input）。
- `content_block_delta`：
  - `thinking_delta {thinking}` → `StreamChunk(reasoning_content=...)`（展示）+ 缓冲到该 thinking 块文本。
  - `signature_delta {signature}` → 缓冲该 thinking 块签名（不展示）。
  - `text_delta {text}` → `StreamChunk(content=...)`。
  - `input_json_delta {partial_json}` → `StreamChunk(tool_call_deltas=[...])`（累积 tool input）。
- `content_block_stop` → 该块结束。
- `message_delta` → stop_reason + 累计 usage.output_tokens。
- **流末**：把缓冲的 thinking 块（文本+签名，有序）打包成 `StreamChunk(provider_state=ProviderState("anthropic_messages", {"thinking":[...]}))` 发出；再发 usage 聚合 chunk。3a 的 `stream_llm_items` 聚合点自动捕获 provider_state 与 usage。

> chat_completions 的 `_StreamToolCallState` 索引去重逻辑是 OpenAI-proxy 特有问题；native tool_use 块自带稳定 id，**不复用**那套，按 block index 累积即可。

### 9.2 非流式 `chat()`（规避大 max_tokens guard）
`chat()` 内部用 `async with client.messages.stream(**kwargs) as s: final = await s.get_final_message()`，再 `normalize_response(final)`——避免 anthropic SDK 对大 max_tokens 非流式请求的超时 guard。仅 compaction（`tool_choice="none"`）走此路。`normalize_response` 从 final Message 的 content blocks 抽 thinking（→provider_state）/ text（→content）/ tool_use（→tool_calls），usage 归一，stop_reason 映射。

### 9.3 usage 归一
| anthropic | scalar dict |
|---|---|
| input_tokens | prompt_tokens |
| output_tokens | completion_tokens |
| cache_read_input_tokens | cache_read_tokens |
| cache_creation_input_tokens | cache_write_tokens |
| (sum) | total_tokens |
| `output_tokens_details.thinking_tokens`（若存在） | reasoning_tokens（best-effort） |
`usage_vendor` 存 native usage 原貌（保留 cache_creation / 各细分字段，供前端/评测确认命中）。

> `reasoning_tokens`：为与 chat_completions 跨 provider 可比而归一，但 **SDK 0.79.0 的 `Usage` 类型不含 `output_tokens_details` / `thinking_tokens`**（实测 grep 空）。故按 chat_completions 的 `_extract_*` 同款 **best-effort `getattr`**——SDK 升级或 raw 响应带该字段时映射，否则缺省（不硬取 typed 字段、不报错）；`usage_vendor` 始终留原貌。

### 9.4 finish_reason 映射（必须，接 finish_diagnostics）
`finish_diagnostics.py` / `agent.py` 消费 OpenAI 风格值（`stop`/`length`/`tool_calls`/`content_filter`）。transport 把 anthropic `stop_reason` 映射：
| anthropic stop_reason | kernel finish_reason |
|---|---|
| `end_turn` / `stop_sequence` | `stop` |
| `max_tokens` | `length` |
| `tool_use` | `tool_calls` |
| `refusal` | `content_filter` |
| `pause_turn` | `stop`（不用 server tool，理论不出现） |

---

## 10. classify_error

anthropic SDK 异常下沉到 transport.classify_error，产 `LLMError(retryable, error_category)`，kernel 重试循环不变：
- `APITimeoutError` → retryable / `timeout`
- `APIConnectionError` → retryable / `connection`
- `RateLimitError` → retryable / `rate_limit`
- `InternalServerError` / `OverloadedError`(529) → retryable / `server`
- `AuthenticationError` / `PermissionDeniedError` → non-retryable / `auth`
- `BadRequestError` → 按文本：含 context/token 上限 → non-retryable / `context_overflow`；signature/cache/tool-protocol 相关 400 → non-retryable / `bad_request`；其余 → retryable / `bad_request`
- 已是 `LLMError` → 返回 None（不二次包裹，同 chat_completions）

不引入自动恢复策略。

---

## 11. 集成点（不改 / 改）

**不改**：
- `BillingLLMProvider` / `UsageCollectingProvider`：`__getattr__` 透传，对 provider_state 与签名变更天然无感。
- `exp.py` 装配、compaction 控制流（仍调 `chat`，tool_choice="none"）。注意：3b 让 `chat()` 首次可能返回真实 `provider_state`（summary 调用也带 thinking 块），compaction 仍按 3a §6.5 由 summary 文本构造合成 `AssistantMessage`、**丢弃**该 provider_state（合成摘要无回放状态），控制流不变。
- 持久化/resume 链路：3a 已贯通（assistant_state internal-only 发射、tail restore 携带 provider_state、checkpoint 中立校验、`model_dump(mode="json")`）。3b 产真实 anthropic provider_state 后自动跟上，**无 schema 变更**（payload 是 JSON-compatible 不透明 dict）。
- kernel 主循环、IR 字段、`stream_llm_items` 聚合、`agent.py` 组装点。

**改**：
- `llm_factory.py`：dispatch 加 anthropic_messages + builder。
- `config/llm.py`：`PromptCacheConfig` + `LLMProfileConfig.prompt_cache`。
- `config/llm_config.yaml`：anthropic provider 连接 + opus profile。
- 新增 `anthropic_messages.py`。

---

## 12. 风险与假设

1. **网关必须透明 passthrough**：litellm anthropic 端点若是翻译层而非透传，会改写 thinking signature / cache_control → 回放 400、cache 不命中。需运维侧验证 `LITELLM_PROXY_ANTHROPIC_BASE` 是透传到 anthropic 原始协议。直连 `anthropic` provider 无此风险，可作验证对照。
2. **thinking 回放 token 成本**：每轮把历史 thinking 签名块回传增加 input tokens（被 prompt cache 缓解；缓存读 thinking 块计 input usage）。3b 先全量回放（正确优先）；未来可优化为只保最近 tool 轮的 thinking。
3. **finish_reason 映射完整性**：需对照 `finish_diagnostics.py` 回归（尤其 `tool_use→tool_calls` 与空内容判定）。
4. **直连备选可用性**：`ANTHROPIC_API_KEY` + worker egress 到 api.anthropic.com 是否可用（开发环境疑似受限，故网关为主路径）。
5. **base_url 路径**：SDK 在 base_url 后拼 `/v1/messages`；网关 anthropic 根路径需与之匹配（如 `<proxy>/anthropic`）。
6. **compaction summary 带 thinking 成本**：transport 不区分调用方，compaction 的 summary 调用也以 effort max 跑 thinking → 额外 token。3b 接受现状；如需优化可后续给 compaction 单独的低 effort 调用路径（非本阶段）。

---

## 13. 测试策略（跟随现有测试文化，纯函数为主）

- **convert_messages**：system 抽取（→顶层 system）、UserMessage(含 image)、**tool_result 放置（§5.2.1）：并行多个 tool_result 合成单 user 消息且排在 text/image 之前、紧跟 assistant tool_use**、AssistantMessage 重建顺序（thinking→text→tool_use）、tools OpenAI→anthropic 转换、`validate_tool_turn_sequence` 复用。
- **build_kwargs**：thinking adaptive+display+effort、temperature 省略、max_tokens 可选透传（设置时发送 / 缺省不发）、**tool_choice 映射（auto/none 放行；any/tool/required → 非重试 bad_request fail-fast）**、prompt cache 注入断点数 ≤ max_breakpoints、**automatic 经 `extra_body.cache_control`（非顶层 kwarg）**、ttl 1h。
- **prompt cache 策略**：`_select_anthropic_cache_targets` 纯函数单测（system/latest_user/tool_result/flexible 选择、max_breakpoints 截断、min_flexible_chars 门槛）——迁移旧 `test_openai_provider_prompt_cache.py` 的断点策略断言到本 transport。
- **normalize_stream**：`thinking_delta`→reasoning_content、`signature_delta`+thinking→流末 provider_state、`text_delta`→content、`input_json_delta`→tool_call_deltas、usage 归一（含 reasoning_tokens best-effort：0.79.0 无该字段时不取、不报错）、stop_reason→finish_reason 映射表。
- **provider_state round-trip**：真实结构（thinking 块+签名）产出 → `stream_llm_items` 聚合 → `AssistantMessage.provider_state` → `model_dump(mode="json")` / restore round-trip 一致；convert 重建把签名块排在 tool_use 前。
- **tag-丢弃跨协议**：anthropic transport 对 chat_completions tag 的 provider_state 返回 None（丢弃）、保留 content+tool_calls；反向同理。
- **classify_error**：各 anthropic 异常 → 正确 category/retryable。
- **chat() 非流式**：内部 stream + get_final_message，normalize_response 抽三类内容、usage、finish_reason。

---

## 14. 明确不做

- 不做 native `responses` transport（3c）。
- 不做 BYOK anthropic key（仍 chat_completions）。
- 不做 automatic fallback、不做 Gemini native。
- 不做 reasoning 明文回传 PR（IR 已预留）。
- 不做 inline thinking 正则剥离（native anthropic N/A）。
- 不引入持久化迁移脚本（provider_state 是 3a 既有可选字段）。
- 不改 kernel 主循环控制流、不改 IR 字段、不改持久化 schema。

---

## 15. 完成标准

- `AnthropicMessagesTransport` 落地：满足 `LLMProvider` Protocol（经 `Transport` 基类），native message/tool/thinking 转换、system 抽取、tool_result 放置（§5.2.1）正确；tool_choice 在 thinking 恒开下只放行 auto/none、any/tool/required fail-fast；client 显式 `max_retries=0`（重试归 kernel）。
- factory dispatch 加 `anthropic_messages`；config 加两层 anthropic provider 连接 + opus profile + `PromptCacheConfig`，loader 只认新结构、零兜底。
- thinking 三类内容分离：content(text) / reasoning_content(展示) / provider_state(签名块)；续轮 convert 把签名块原样排在 tool_use 前回传。
- 首次产真实 provider_state，经 3a 通道聚合/持久化/resume round-trip 一致，kernel 全程不透明搬运。
- prompt cache native 注入：block 级 cache_control（SDK 支持）+ automatic 经 `extra_body.cache_control`（0.79.0 无 typed 参数）；断点 ≤4、顺序 tools→system→messages、ttl 可配、命中可经 usage_vendor 验证。
- 跨协议 tag-丢弃实际生效（anthropic ↔ chat_completions）。
- stop_reason→finish_reason 映射使 finish_diagnostics 回归通过；usage scalar+vendor 归一。
- 非流式 chat() 经 stream+get_final_message 规避大 max_tokens guard，compaction 可运行。
- chat_completions 路径完全不受影响（行为等价）。
- convert / build_kwargs / cache 策略 / normalize_stream / provider_state round-trip / tag-丢弃 / classify_error / chat() 各有独立测试。

---

## 16. 3b→3c 衔接

- `convert_messages` / build_kwargs / normalize 接缝形态已被两个 transport（chat_completions + anthropic_messages）验证，3c 的 `responses` transport 照此填 native Responses 转换 + encrypted reasoning provider_state 即可。
- provider_state 通道、tag-丢弃、持久化/resume 已被真实 anthropic state 压测，3c 仅换 payload 结构（encrypted reasoning items + response item id），通道无需再动。
- prompt cache 为 anthropic 专属，3c 不涉及。
- finish_reason 映射模式（provider stop_reason → kernel 词汇）已建立，3c 比照 Responses 的终止语义补映射。
