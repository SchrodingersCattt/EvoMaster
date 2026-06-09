# Provider vendor 子类统一：chat_completions reasoning 多轮回放 + anthropic prompt cache 子类化（详细设计）

- 日期：2026-06-10
- 状态：brainstorming 逐段确认完成（vendor 子类方向收敛），待落实施
- 上游：
  - `docs/superpowers/specs/2026-06-06-provider-aggregation-design.md`（三阶段总方向；第 4 节硬约束，尤其 #6 provider/transport 双层、#9 静态 capability 声明不建 matrix、#12 测试文化）
  - `docs/superpowers/specs/2026-06-08-provider-aggregation-stage3b-design.md`（native `anthropic_messages` transport）
  - `docs/superpowers/specs/2026-06-10-provider-vendor-capability-design.md`（顶层 prompt cache 方言收口的**前案**）
- 范围：把「同协议、不同 vendor 的请求体/消息体方言」从「配置位 + 单一实现内部 if 消费」**改为「协议基类 + vendor 子类」**，由 `ProviderConfig.vendor` 判别字段、factory 按 `(transport, vendor)` 分发。本次两个落点：(1) `chat_completions` 协议下 qwen/deepseek 的**多轮思考过程传递**（回放 `reasoning_content` + qwen 的 `preserve_thinking`）；(2) `anthropic_messages` 协议下 bedrock 的顶层 `cache_control` 抑制（把已落地的 `prompt_cache_compat` 枚举**重构成 `BedrockAnthropicTransport` 子类**）。

> 本设计**取代前案**（`provider-vendor-capability-design.md`）的「`ProviderConfig` 配置布尔位/枚举 + transport 内部 if 消费」形态。母文档第 4 节决策仍为硬约束。不新增协议、不动 kernel 主循环、不改持久化 schema 或 IR 字段。

---

## 1. 背景与触发

母文档把 provider 与 transport 分两层（决策 #6）：`transport` 表达 wire 协议（Messages / ChatCompletions / Responses），`provider` 表达「连到哪个后端」。前案在 `ProviderConfig` 上挂静态布尔/枚举（`supports_automatic_cache` 设想、实际落地的 `prompt_cache_compat`），由单一 transport 用 `if` 消费——这能表达「单一布尔门」式差异，但当差异是**一组请求体字段组装 + 消息体序列化变体**时，会把 vendor 知识摊成数据位再散落进 transport 分支，职责不清。

真实触发：`chat_completions`（OpenAI 兼容）协议下，qwen / deepseek 需要**在多轮对话里传递思考过程**——把上一轮 assistant 的 `reasoning_content` 带回下一轮，让模型参考既有推理。但「要不要回传、要不要额外开关」是**逐 vendor 不同的请求体方言**：

- **qwen**（qwen3.7-max 等，经百炼/DashScope OpenAI 兼容端点）：多轮默认不读历史 `reasoning_content`；需客户端把 `reasoning_content` 放回 messages，**并**发 `extra_body.preserve_thinking=true`，服务端才拼接。
- **deepseek-v4 / v4-pro**：tool call 之间的 assistant `reasoning_content` **必须**回传（否则 400），非 tool call 轮回传则被忽略；无需额外开关。
- **deepseek-reasoner (R1)**：输入带 `reasoning_content` 直接 **400**，必须丢弃。

同协议、同一套 OpenAI wire，仅因 vendor 不同而「思考如何回传」不同——这正是 spec §7 预留的 vendor 维度在 `chat_completions` 上的首个落点。与之同源的 anthropic 侧 bedrock 顶层 cache_control 差异，本次一并收编为同一套 vendor 子类机制。

---

## 2. 现状基线（以当前代码为准，截至 2026-06-10 `codex/provider-stage1`）

> 行号会随并行改动漂移，以符号名为准。

| 关注点 | 当前代码事实 |
|---|---|
| chat_completions 流式 reasoning | **已取**：`normalize_stream` `reasoning_content = getattr(delta, "reasoning_content", None)`（`chat_completions.py:571`）→ `StreamChunk`（:596） |
| chat_completions 非流式 reasoning | **缺口**：`normalize_response`（:528-550）只取 `message.content`，丢 `message.reasoning_content` |
| chat_completions 多轮回放 | **缺口**：assistant→wire 是**模块函数** `_assistant_message_to_dict`（:44-55），不序列化 `reasoning_content` |
| chat_completions vendor 请求字段 | **无通路**：`build_kwargs` 有 `extra_body` 注入（:407-418），但 `_build_chat_completions_transport` 的 `extra_body` 参数只在 BYOK 路径传，普通 profile 传不进 |
| anthropic prompt cache 顶层差异 | **已落地（枚举形态）**：`ProviderConfig.prompt_cache_compat`（`config/llm.py:16-18`）→ factory 接线（`llm_factory.py:101`）→ transport 按 `prompt_cache_compat == "anthropic_native"` 决定发不发顶层 cache_control（`anthropic_messages.py:499-505`）与槽预留（:118-121）。`litellm-anthropic` 已配 `prompt_cache_compat: bedrock_blocks`，带 3 个测试（5d4f269e） |
| Transport 基类 | 「协议骨架 + seam」设计（`transport.py:1-8` 注释）：收敛生命周期/timeout，把 `build_kwargs`/`convert_messages`/`normalize_*` 声明为 seam 交子类；"满足 LLMProvider Protocol 的是具体子类" |
| 两 transport 风格不一致 | chat_completions 用模块函数 `_assistant_message_to_dict`（:44，不可 override）；anthropic 用**实例方法** `_assistant_to_wire`（:422，可 override，且其 thinking 回放就放在此方法内）|
| reasoning_content 全链路 | **已通**：`LLMResponse`/`StreamChunk`/`AssistantMessage` 均有 `reasoning_content` 字段；kernel 经 `ThoughtEvent` 实时推送、聚合入 `AssistantMessage.reasoning_content`；SSE / 持久化已处理 |

**结论**：reasoning_content 的 IR/kernel/SSE/持久化全链路无需动；缺的是 (a) chat_completions 把前轮 `reasoning_content` 序列化回 wire、(b) vendor 请求字段通路、(c) 非流式提取。anthropic 顶层差异已能工作，但以枚举形态存在，需重构为子类以统一架构。

---

## 3. vendor 差异矩阵（多轮 reasoning，官方依据）

**wire 格式两家统一**：assistant 消息里 `reasoning_content` 是同级独立字段：
```json
{"role": "assistant", "content": "...", "reasoning_content": "...", "tool_calls": [...]}
```
差异仅在「是否回放」与「是否额外开关」：

| vendor / model | 客户端回放 reasoning_content | 额外请求字段 | 依据 |
|---|---|---|---|
| qwen3.7-max 等 | ✅ 要 | `extra_body.preserve_thinking=true` | 默认服务端丢弃历史，preserve_thinking 才拼接 |
| deepseek-v4 / v4-pro | ✅ 要（tool call 之间**必须**，否则 400） | 无（thinking 默认 enabled） | 放回即生效，非 tool call 轮被忽略 |
| deepseek-reasoner (R1) | ❌ **禁止**（带了直接 400） | 无 | 必须丢弃 |
| gpt / 其他 | ❌ 默认不放 | — | 各自不同，默认安全 |

- 简化：deepseek-v4「总是回放」是安全的（tool call 必须、非 tool call 被忽略），无需运行时检测 tool-call，对齐决策 #9（不做运行时探测）。代价是非 tool call 轮多算 input token。
- 默认「不回放」对 R1 与未知 vendor 天然安全，且等于当前代码行为。

官方依据：
- [阿里云百炼 · 深度思考模型](https://help.aliyun.com/zh/model-studio/deep-thinking)（preserve_thinking 经 `extra_body`；多轮 assistant 同级 reasoning_content）
- [DeepSeek · Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)（v4 tool-call 必须回传 reasoning_content，否则 400）

外部风险（非本仓库代码可解）：[BerriAI/litellm #26395](https://github.com/BerriAI/litellm/issues/26395) —— DeepSeek V4 Pro 多轮时 reasoning_content 可能被 litellm proxy strip。客户端做对后仍需确认 proxy 版本。

---

## 4. 设计原则与决策对齐

- **vendor 子类（继承）而非配置位**：transport 定协议骨架与 seam，vendor 差异用子类 override 表达。三条理由：(1) vendor 特化「就是」对协议方法（`build_kwargs`/`convert_messages`）的局部 override，是继承的本职；(2) `chat_stream`/`chat` 编排里调 `self.build_kwargs()`/`self.convert_messages()`，**多态自动织入**子类特化，零注入机制；(3) reasoning 回放的 wire 格式随协议绑定（chat_completions 的同级字段 ≠ anthropic 的 thinking block），vendor×协议是正确粒度，不存在可跨协议复用的「纯 vendor 逻辑」，组合的复用优势在此为空。
- **兑现 spec §7 的 vendor 判别字段**：§7 原文预告「未来加 `vendor`/`flavor` 判别字段，由 transport 的 `_open_client` 与 model-id 解析消费」。本设计加 `ProviderConfig.vendor`，消费点扩到 `build_kwargs`/`convert_messages`（与 `_open_client` 同源）。
- **能力轴随差异源（对 §7 的有意延伸）**：前案把能力挂 `ProviderConfig` 因 prompt cache 差异源是 base_url 指向的后端（native vs bedrock，两个 provider）。reasoning 差异源是 **model/vendor**，且 qwen/deepseek 当前共用同一 litellm proxy——故按 vendor 拆 provider、用 vendor 子类承载，是正确粒度。
- **对齐决策 #9**：`vendor` 是静态声明；未知 vendor 在 factory 装配时 fail-fast；不做运行时探测、不建 capability matrix。
- **对齐决策 #12**：迁移已落地的 prompt_cache_compat 测试；新 vendor 子类补纯函数单测。
- **clean migration、零兜底**：不在主代码写「检测到 deepseek-r1 就剥 reasoning_content」之类内联兜底——默认基类不回放即对 R1 安全；要回放的 vendor 用子类显式声明。

---

## 5. 架构：协议基类 + vendor 子类

```
ProviderConfig:  transport(协议) + vendor(特化判别·新增) + api_key/base_url
       │  factory 按 (transport, vendor) 查表分发；未知 vendor → fail-fast
       ▼
Transport(协议骨架 + seam；满足 LLMProvider Protocol 的是具体子类)
  ├─ ChatCompletionsTransport            (chat_completions 协议基本实现)
  │    ├─ QwenChatCompletionsTransport       回放 reasoning_content + 请求注 preserve_thinking
  │    └─ DeepSeekChatCompletionsTransport   回放 reasoning_content
  └─ AnthropicMessagesTransport          (anthropic_messages 协议基本实现 = native 行为)
       └─ BedrockAnthropicTransport          抑制顶层 cache_control（取代 prompt_cache_compat 枚举）
```

`vendor` 是**协议内**的特化判别：每个协议 builder 解释自己的 vendor 命名空间（`chat_completions` 认 `qwen`/`deepseek`，`anthropic_messages` 认 `bedrock`），跨协议互不干扰；vendor 与协议不匹配（如 chat_completions 配 vendor=bedrock）→ factory fail-fast。`transport_tag` 子类继承基类不变（`chat_completions` / `anthropic_messages`），保证 `provider_state` 回放按协议级匹配、vendor 子类间互通。

---

## 6. 精确改动点

### 6.1 chat_completions：reasoning 回放 + preserve_thinking

基类 `ChatCompletionsTransport`：模块函数提升为实例 seam，并加 vendor 请求字段 seam。

```python
def _assistant_to_wire(self, message: AssistantMessage) -> dict[str, Any]:
    payload = {"role": message.role.value, "content": message.content}
    if message.tool_calls is not None:
        payload["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.name, "arguments": tc.arguments_json}}
            for tc in message.tool_calls]
    return payload                       # 基类：纯协议，不含 reasoning_content

def _message_to_wire(self, message: Message) -> dict[str, Any]:
    if isinstance(message, AssistantMessage):
        payload = self._assistant_to_wire(message)      # ← 实例 seam，可 override
    elif isinstance(message, UserMessage):
        payload = _user_message_to_dict(message)         # user/tool 仍是模块函数
    elif isinstance(message, ToolMessage):
        payload = {"role": message.role.value, "content": message.content,
                   "tool_call_id": message.tool_call_id}
    else:
        payload = {"role": message.role.value, "content": message.content}
    if payload.get("content") is None:
        payload["content"] = ""
    return payload

def convert_messages(self, messages):
    validate_tool_turn_sequence(messages)
    return [self._message_to_wire(m) for m in messages]

def _vendor_request_fields(self) -> dict[str, Any]:
    return {}                            # 基类：无 vendor 字段
# build_kwargs 现有 extra_body 注入处（:415 区）追加：extra_body.update(self._vendor_request_fields())
```

非流式补全（协议级，对所有 chat_completions vendor 适用，对 R1 安全因默认不回放）：`normalize_response` 的 `LLMResponse(...)` 增 `reasoning_content=getattr(message, "reasoning_content", None)`。

回放复用 + vendor 子类：
```python
class _ReasoningReplayMixin:
    def _assistant_to_wire(self, message):
        payload = super()._assistant_to_wire(message)   # MRO 接力到协议基类
        if message.reasoning_content is not None:
            payload["reasoning_content"] = message.reasoning_content
        return payload

class DeepSeekChatCompletionsTransport(_ReasoningReplayMixin, ChatCompletionsTransport):
    pass                                 # 纯回放

class QwenChatCompletionsTransport(_ReasoningReplayMixin, ChatCompletionsTransport):
    def _vendor_request_fields(self):
        return {"preserve_thinking": True}
```

- 删除模块函数 `_assistant_message_to_dict` / `_message_to_openai_dict`（被实例方法取代，净代码迁移而非新增）。

### 6.2 anthropic_messages：bedrock 子类化（取代 prompt_cache_compat 枚举）

删除：`PromptCacheCompat = Literal[...]`（:41）、transport `__init__` 的 `prompt_cache_compat` 参数与 `self._prompt_cache_compat`（:382/:402）、`_select_anthropic_cache_targets` 的 `prompt_cache_compat` 形参（:113）。

基类加顶层抑制 seam，两处消费点改读它：
```python
class AnthropicMessagesTransport(Transport):
    def _emit_top_level_auto_cache(self) -> bool:
        return True                      # native 默认：随 automatic 发顶层

# build_kwargs（:499 区）：
#   if options.automatic and self._emit_top_level_auto_cache():
#       kwargs_extra_body = {"cache_control": dict(options.cache_control)}
# _select_anthropic_cache_targets：形参 prompt_cache_compat → emit_top_level_auto: bool；
#   automatic_uses_top_level = options.automatic and emit_top_level_auto
#   build_kwargs 调用处传 emit_top_level_auto=self._emit_top_level_auto_cache()

class BedrockAnthropicTransport(AnthropicMessagesTransport):
    def _emit_top_level_auto_cache(self) -> bool:
        return False                     # bedrock：抑制顶层，块级断点（含 ttl:1h）不受影响
```
（`automatic` 回归单一语义＝块级断点启发式；顶层发不发 = `automatic AND 子类能力`，合流在 transport 内部用 `self`，不落成 options 字段、不需 factory 合流。）

### 6.3 ProviderConfig + factory

```python
# config/llm.py：删 prompt_cache_compat，加 vendor
class ProviderConfig(BaseModel):
    transport: str
    api_key: str
    base_url: str | None = None
    vendor: str | None = None            # 协议内 vendor 特化判别

# llm_factory.py：每协议一张 vendor→class 表，builder 内分发，未知 vendor fail-fast
_CHAT_COMPLETIONS_BY_VENDOR = {
    None: ChatCompletionsTransport,
    "qwen": QwenChatCompletionsTransport,
    "deepseek": DeepSeekChatCompletionsTransport,
}
_ANTHROPIC_BY_VENDOR = {
    None: AnthropicMessagesTransport,
    "bedrock": BedrockAnthropicTransport,
}
# builder 取 cls = _<...>_BY_VENDOR[provider.vendor]（KeyError 包成 ValueError 列可用 vendor）
# 删 _build_anthropic_messages_transport 里 prompt_cache_compat=provider.prompt_cache_compat 一行
```

### 6.4 config/llm_config.yaml

```yaml
litellm-qwen:        # base_url 复用同 proxy
  transport: chat_completions
  vendor: qwen
  api_key: "${LITELLM_PROXY_API_KEY}"
  base_url: "${LITELLM_PROXY_API_BASE}"
litellm-deepseek:
  transport: chat_completions
  vendor: deepseek
  api_key: "${LITELLM_PROXY_API_KEY}"
  base_url: "${LITELLM_PROXY_API_BASE}"
litellm-anthropic:
  transport: anthropic_messages
  vendor: bedrock                  # 取代 prompt_cache_compat: bedrock_blocks
  api_key: "${LITELLM_PROXY_API_KEY}"
  base_url: "${LITELLM_PROXY_BASE}"
```
- profile 改指向：`matmaster/qwen3.7-max` → `litellm-qwen`；`matmaster/dsk-v4p`、`matmaster/DeepSeek-v4-Pro` → `litellm-deepseek`；`gemini-3.1-pro-preview` 留 `litellm`（vendor 默认 None）；`global.anthropic.claude-opus-4-6-v1` 保持 `litellm-anthropic`（现 vendor: bedrock）。

### 6.5 测试（决策 #12）

- **迁移** 5d4f269e 的 3 个测试（`test_anthropic_messages_prompt_cache.py` / `test_llm_factory.py` / `test_loader.py`）：`prompt_cache_compat` 断言 → vendor/子类断言（bedrock 子类不发顶层、native 基类发）。
- **新增**纯函数单测：基类 `_assistant_to_wire` 不含 reasoning_content；Qwen/DeepSeek 子类含（reasoning 非 None 时）；Qwen `build_kwargs` 的 extra_body 含 `preserve_thinking`、基类/deepseek 不含；factory `(transport, vendor)` → 类映射正确、未知 vendor fail-fast；`_select_anthropic_cache_targets` 在 `emit_top_level_auto=False` 时块级目标可达 `max_breakpoints`、`=True` 时 ≤ `max_breakpoints-1`。

---

## 7. 净效果

- **qwen3.7-max（litellm-qwen）**：多轮把前轮 `reasoning_content` 放回 messages + 发 `preserve_thinking=true`，服务端拼接历史推理；思考过程跨轮传递。
- **deepseek-v4（litellm-deepseek）**：多轮总是回放 `reasoning_content`，tool-call 链满足「必须回传」，不再 400；非 tool call 轮被服务端忽略。
- **deepseek-r1 / gpt / gemini**：默认基类不回放，对 R1 的 400 约束天然安全，行为零变化。
- **anthropic native / bedrock**：行为与现状一致（native 发顶层、bedrock 抑制），但实现从枚举判断变为子类 override；`automatic` 回归单一（块级）语义。
- **架构统一**：`ProviderConfig.vendor` 单一判别同时驱动两协议的 vendor 特化；新增 vendor 不改协议基类（开放-封闭）。

---

## 8. 取舍与未来扩展

- **子类 vs 配置位/组合/独立 provider 层**：选子类。配置位（前案）把 vendor 知识摊成数据再散落 if，用户评价别扭；组合（VendorDialect 策略对象）多一层抽象且无法靠多态自动织入编排；独立 Provider 行为层与现有 `LLMProvider` Protocol（已是抽象面）+ transport 子类（已是实现）职责重叠，是无独立职责的中间层。子类贴现有继承轴、靠多态零成本织入、命中 §7 缝。
- **回放复用用 mixin**：qwen/deepseek 的回放是同一段 OpenAI 兼容逻辑，`_ReasoningReplayMixin` 避免两子类重复；将来第三个回放 vendor 直接复用。
- **不做 `enable_thinking`/`thinking_budget`**：YAGNI。qwen3.7-max（max 系列、`reasoning_effort: high`）大概率默认开思考；若实测 `preserve_thinking` 须配 `enable_thinking` 才生效，再在 `QwenChatCompletionsTransport._vendor_request_fields` 平铺加一项。
- **不专门处理 deepseek-r1**：当前无 R1 profile；默认基类不回放已对其 400 约束安全。将来接 R1 直接用基类 `ChatCompletionsTransport`（vendor=None）即可。
- **未来同源扩展**：spec §7 的另一维度——直连 Bedrock/Vertex 的 client 构造方言（`AnthropicBedrock`/`AnthropicVertex` + SigV4/OAuth + region/ARN model-id）——同样由 `ProviderConfig.vendor` 驱动，在对应 vendor 子类 override `_open_client` 与 model-id 解析。与本次 reasoning/cache 子类同一套机制，本次不实现，仅确认落点同源。

---

## 9. 不做

- 不新增协议/transport，不动 kernel 主循环，不改持久化 schema 或 IR 字段（reasoning_content 全链路已通）。
- 不改 `PromptCacheConfig` schema（`automatic` 仍是唯一面向用户的块级 prompt-cache 旋钮）。
- 不做 `enable_thinking`/`thinking_budget`、不专门处理 deepseek-r1、不实现直连 Bedrock/Vertex client 构造方言。
- 不建 capability matrix / preflight 子系统，不做运行时 capability 探测。
- 不在主代码写「检测到某 vendor 自动剥/补字段」之类内联兜底——回放与请求字段由 vendor 子类显式承载。
- 不解决 litellm proxy strip reasoning_content（#26395）的外部依赖——需确认 proxy 版本，非本仓库代码范畴。
