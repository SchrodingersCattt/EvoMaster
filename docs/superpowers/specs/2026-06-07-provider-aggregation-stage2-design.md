# 阶段二：Provider 聚合核心层（详细设计）

- 日期：2026-06-07
- 状态：brainstorming 逐段确认完成，待落实施计划
- 上游：`docs/superpowers/specs/2026-06-06-provider-aggregation-design.md`（三阶段总方向）
- 范围：定义阶段二的具体 class / 字段 / 文件改动；建立聚合核心接缝，为阶段三 native transport 搭基础设施。

> 本文是阶段二的落地详细设计。母文档第 4 节「已确认决策」仍为硬约束；
> 本次 brainstorming 在母文档基础上**修订了阶段边界**（见第 2 节），以修订后为准。

---

## 1. 术语纪律：三轴模型

整个 provider 层有三个分属不同轴的概念，全文严格区分，禁止混用：

| 轴 | 名称 | 是什么 | 谁属于它 |
|---|---|---|---|
| A 接口/门面 | `LLMProvider` Protocol | kernel 唯一依赖的结构化契约（`chat`/`chat_stream`/生命周期/timeout-retry property）。结构化类型，满足它**不需要继承**。 | 所有 transport 子类 + `BillingLLMProvider` / `UsageCollectingProvider` 两个装饰器 |
| B dispatch 标签 | `transport` | 一个**字符串**，标识走哪种 wire 协议（`chat_completions`，stage 3 加 `anthropic_messages`/`responses`）。是数据，不是类型。 | — |
| C 实现复用 | `Transport` 基类 | 可选的共享实现脚手架（生命周期骨架 + property 实现 + seam 声明）。继承它是为省重复代码。 | `ChatCompletionsTransport`（stage 3 加两个子类） |

精确结论（修正母文档决策 #5）：

1. **门面是 `LLMProvider` Protocol，不是 `Transport` 基类。** `Transport` 基类只实现 Protocol 的一部分（property + 生命周期），`chat`/`chat_stream` 由子类补齐。因此**自满足 Protocol 的是具体子类，抽象基类本身不完整、不可直接实例化使用**。
2. 「是一个 transport」（轴 B：有标签、被 dispatch）与「继承 `Transport` 基类」（轴 C）在概念上正交；阶段二删除 bedrock 后二者恰好 1:1 对应（每个 transport 都继承基类），故基类回归朴素命名 `Transport` / `transport.py`，撞词降级为纯行文纪律。
3. dispatch 表的 value 契约是「→ `LLMProvider`」，不是「→ `Transport` 子类」。

---

## 2. 相对母文档的阶段边界修订

母文档原计划：阶段二让 `chat_completions` 临时背「anthropic-via-litellm 方言」、bedrock 暂留，native anthropic 与删 bedrock 留阶段三。

本次确认**修订为**：阶段二不让 `chat_completions` 承载任何 anthropic 语义，并**一次性删除全部 Claude 通道与 bedrock**，避免为「马上要删的代码」做任何重构/抽象。

| 维度 | 母文档原案 | 本设计修订 |
|---|---|---|
| `chat_completions` 协议纯度 | 临时背 anthropic 方言 | 纯 openai 风格 |
| litellm-Claude（sonnet/opus_global） | 保留，方言下沉 transport | **阶段二移除**，stage 3 以 native anthropic 加回 |
| bedrock（`bedrock_provider.py`/opus_bedrock） | 阶段二暂留、阶段三删 | **阶段二一并删除** |
| `reasoning_protocol`/`temperature_policy` 字段 | 阶段二删 | 阶段二删（移除 Claude/bedrock 后无消费者，删除更干净） |

理由：移除 Claude/bedrock 后，`chat_completions` 唯一伺候的就是 openai 风格 profile；anthropic 方言翻译、prompt cache（约 160 行）、policy 字段全部失去消费者，**只删不搬**，净代码量下降，且 stage 2 的「聚合核心」对着一个无杂质的干净子类来设计基类，是定基类形状的最佳时机。

代价：阶段二期间前端路由 `claude-sonnet-4-6` / `global.anthropic.claude-opus-4-6-v1` / `bedrock-claude-opus` 暂时下线（项目处于开发阶段、无在线用户，可接受）；阶段三引入 native anthropic 时加回。

---

## 3. 移除清单（净代码下降）

### 3.1 litellm-Claude
- `config/llm_config.yaml`：删 `sonnet`、`opus_global` 两个 profile（其对外路由 `claude-sonnet-4-6`、`global.anthropic.claude-opus-4-6-v1` 随整个 `routes:` 段删除，见 5.1）。

### 3.2 bedrock 全套
- 删 `matmaster/providers/bedrock_provider.py`（596 行）。
- 删 `tests/.../test_bedrock_provider.py`（及其他 bedrock 专用测试）。
- `config/llm_config.yaml`：删 `opus_bedrock` profile（其对外路由 `bedrock-claude-opus` 随整个 `routes:` 段删除，见 5.1）。
- `matmaster/config/llm.py`：删 `LLMProfileConfig.bedrock_region` 字段。（`PROVIDER_TRANSPORT` / `PLATFORM_PROVIDERS` 两个硬编码字典在 5.2 随 `providers:` 段引入而**整体删除**，不只是删 bedrock 项。）
- `matmaster/providers/llm_factory.py`：删 `BedrockProvider` import、`LLMProviderBundle.provider` 联合类型里的 `BedrockProvider`、`bedrock_converse` 构造分支。
- `matmaster/providers/__init__.py`：删 `BedrockProvider` 导出。
- 确认：`src/`（worker/services）对 bedrock **零引用**，删除无连带。

### 3.3 reasoning/temperature 死配置
- `matmaster/config/llm.py`：删 `MODEL_FAMILY_DEFAULTS`、`_infer_model_family`、`LLMProfileConfig.model_family` 字段、`LLMProfileConfig.reasoning_protocol`、`LLMProfileConfig.temperature_policy`、`effective_family()`。
- `effective_temperature()` 直接删除，下游改读 `profile.temperature`（`gemini-3.1-pro-preview` 本就直接写 `temperature: 1.0`，行为不变；profile 已收敛为纯数据，见 5.2）。
- `LLMProviderBundle.model_family` 字段删除（全仓无下游功能消费者，仅曾用于日志）。

### 3.4 routes 删除后的连带冗余
这些设计原本只为「`model_override` 是 route 表 key、不是 profile key」这一落差服务；routes 删除后落差消失，全部退化为冗余，一并清除（纯删除，净代码下降）。config 层的类/字段清理并入 5.2 的 schema 重设计，本节只列**跨出 config 层**的连带：

- **`llm_override` / `req.llm` 兼容旁路**：原为「绕过 route 表、直接按 profile key 解释」而设（代码注释即写明 `model_override` = "External route key"、`llm_override` = "Legacy profile key (compat layer)"）。现在 `model_override` 本身即 profile key，二者完全同义。删除：`resolve`（原 `resolve_route`）的 `llm_override` 参数（收为单参数 `model_override`）；`llm_factory.build_provider_bundle` / `build_byok_provider_bundle` 的 `llm_override` 形参与透传；`src/` 调用方传递（`apis/chat_api.py` 的 `req.llm`、`worker/agent_worker.py`、`services/image_input_service.py`、`services/agent_run_service.py`）；`utils/feishu_notifier.format_llm_model_for_notify(llm, model)` 收为单参数。
- **调用方二次查表**：`image_input_service` / `llm_factory` / `devshell/repl` 旧写法是「`resolve_route()` 后再 `get_profile(resolved.profile_key)`」；`ResolvedModel` 已持 `profile` 引用，改读 `resolved.profile`，连带删 `LLMConfig.get_profile`（详见 5.2）。

---

## 4. 聚合核心层

### 4.1 模块布局
```text
matmaster/providers/
  transport.py             # Transport 基类（轴 C）
  transports/
    __init__.py
    chat_completions.py    # ChatCompletionsTransport(Transport)，纯 openai
  llm_factory.py           # dispatch 表（tag→builder）+ build；不另设 registry.py
  usage_collector.py       # 不动
```

### 4.2 `Transport` 基类（`transport.py`）
职责（仅收敛真正共享的部分，**不**模板化 `chat`/`chat_stream`）：

- `__init__` 存公共参数：`timeout` / `stream_timeout` / `stream_idle_timeout` / `max_retries` / `retry_delay`。
- 一次性实现 4 个 property：`stream_timeout` / `stream_idle_timeout`（`None` 回退到 `timeout`）/ `max_retries` / `retry_delay`（消除目前 `ChatCompletionsProvider` 内的重复实现）。
- 生命周期脚手架：基类管 `_client` / `_enter_count` / `_ensure_client()`；`__aenter__` / `__aexit__` 委托子类钩子 `_open_client()` / `_close_client()`（SDK client 创建/关闭由子类提供）。
- 声明子类覆盖契约（seam，抽象方法或 `NotImplementedError`）：
  - `build_kwargs(...)`：语义配置 → 该协议的请求 kwargs。
  - `convert_messages(messages)`：canonical → wire（阶段二 chat_completions = identity 直通）。
  - `normalize_response(raw)` / `normalize_stream(raw_iter)`：raw → `LLMResponse` / `StreamChunk`。
  - `classify_error(exc)`：SDK 异常 → `LLMError(retryable, error_category)`。

类 docstring 必须写明：**本基类不实现 `chat`/`chat_stream`，因此不自满足 `LLMProvider` Protocol；满足 Protocol 的是具体子类。**

`chat`/`chat_stream` 不进基类的理由：实际 API 调用（`chat.completions.create` vs stage 3 `messages.create`/`responses.create`）与流式迭代差异过大，硬模板化会变坏抽象。

### 4.3 `ChatCompletionsTransport`（`transports/chat_completions.py`）
由现有 `ChatCompletionsProvider` 重构而来：

- 继承 `Transport`，从基类拿 property / 生命周期脚手架；实现 `_open_client`（`httpx.AsyncClient` + `openai.AsyncOpenAI`，逻辑同现状）/ `_close_client`。
- `chat` / `chat_stream` 保留现有实现，但：请求 kwargs 改由 `self.build_kwargs(...)` 生成；消息经 `self.convert_messages(...)`；响应经 `normalize_response`/`normalize_stream`；异常经 `classify_error`。
- `build_kwargs`（**纯 openai 风格**，读 profile 平铺字段）：
  - `profile.reasoning_effort` → 顶层 `reasoning_effort`；
  - `profile.reasoning_summary` → `extra_body.reasoning = {summary, effort?}`；
  - 无任何 anthropic 分支、无 `thinking`/`output_config`、无方言判断。
- `convert_messages`：阶段二为 **identity 直通**（消息上游仍由 `to_api_dict()` 转成 dict；prompt cache 随 opus_global 删除）。这是为 stage 3 立的接缝点，不是无用 no-op：stage 3 引 IR 时此处填真实转换。
- 现有 module 级 helper（usage 提取、stream tool-call delta 归一等）随类迁入本文件；prompt cache 相关全部删除（`AnthropicPromptCacheOptions`、`_select_anthropic_cache_targets`、`_prepare_messages`、`_add_*_cache_control`、`_CacheTarget` 等）。

### 4.4 dispatch 表（`llm_factory.py` 内，**不开 registry.py**）
```python
# tag(轴B) → builder（→ LLMProvider，轴A）
_TRANSPORT_BUILDERS = {
    "chat_completions": _build_chat_completions_transport,
}
```
- `build_provider_bundle` 从 `if transport == ... elif ...` 改为查表：`builder = _TRANSPORT_BUILDERS[transport]`，未命中 → 配置错误 fail-fast。
- value 契约是「→ `LLMProvider`」，而非「→ `Transport` 子类」（为将来非基类实现/装饰留口，但阶段二无此情况）。
- 阶段三只需往该 dict 加 `anthropic_messages` / `responses` 两条 + 写两个子类，**不改 factory 控制流**——这就是路线甲所要的接缝。
- 待表长大（≥3 条目 + 选择逻辑）时再抽 `registry.py`，那只是一次 move，阶段二不预设。

### 4.5 BYOK
- `build_byok_provider_bundle`：内部改走 dispatch 表构造 `ChatCompletionsTransport`；合成 profile 直接固定 `transport=chat_completions`（不依赖任何 provider→transport 映射表）。
- 对外签名与行为不变；`provider=byok`、`billing_mode=byok` 身份（阶段一已收束）保持。

---

## 5. 配置演进：引入 `providers:` 段

### 5.1 目标形态
两项变化：

1. 连接（`api_key`/`base_url`）从每个 profile 提到独立 `providers:` 段按 provider 去重；`transport` 在 provider 上声明。
2. **`routes:` 表整体删除**：profile key 直接作为对外标识（前端 `model_override` 即 profile key），profile 的 `model` 字段保留为真正下发给 wire 的模型 id。二者多数相等，少数对外名与 wire 名不同的 profile（如 `matmaster/dsk-v4p` → `aliyun/deepseek-v4-pro`）靠这两个字段分别承载。

```yaml
providers:
  litellm:
    transport: chat_completions
    api_key: ${LITELLM_PROXY_API_KEY}
    base_url: ${LITELLM_PROXY_API_BASE}

profiles:
  matmaster/qwen3.7-max:             # profile key = 对外标识（前端 model_override）
    provider: litellm
    model: matmaster/qwen3.7-max     # = 下发 wire 的模型 id（此处与 key 相等）
    reasoning_effort: high           # 只声明意图，无 protocol/kind（平铺，不嵌套）
    context_limit: 1000000
    supports_vision: true
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0
  matmaster/dsk-v4p:                 # 对外名 ≠ wire 名的示例
    provider: litellm
    model: aliyun/deepseek-v4-pro    # key 与 model 不同：间接层落到 model 字段
    reasoning_effort: max
    context_limit: 200000
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0
  # gemini-3.1-pro-preview / matmaster/gpt-5.5 / matmaster/DeepSeek-v4-Pro 同构
  # （matmaster/gpt-5.5 带 reasoning_summary: detailed；其余 key == model）

default: matmaster/qwen3.7-max       # default 指向 profile key
```

**为什么 profile 能完全吸收 routes**：routes 原本承担三职能——(1) 外名→profile 的间接层、(2) 多对一别名、(3) per-route `model` 覆盖。删 stage 2 的 claude/bedrock 后三者均退化：每条 route 恰好 1:1 指向一个 profile；per-route `model` 覆盖全仓从未使用；唯一的多对一别名（`cds/GPT-5.5` + `matmaster/gpt-5.5` → gpt55）中 `cds/GPT-5.5` 是从未使用的遗留误配，删除即可。于是「外名」收敛为 profile key 本身、「wire 名」收敛为 `profile.model`，间接层消失，routes 表纯属冗余。

### 5.2 schema（`matmaster/config/llm.py`，从头设计）
**推翻旧 schema，不为兼容保留任何旧类。** 最终只剩 3 个 Pydantic 模型 + 1 个解析结果。profile 是**纯数据**（无 `effective_*` / `build_extra_kwargs` 方法——语义→请求 kwargs 的翻译全部移到 transport 的 `build_kwargs`，见 4.3）：

```python
class ProviderConfig(BaseModel):
    """一个后端连接：怎么连到 provider。"""
    transport: str                      # 轴 B dispatch 标签
    api_key: str
    base_url: str | None = None

class LLMProfileConfig(BaseModel):
    """一个对外可选模型（profile key = 对外标识）。纯数据。"""
    provider: str                       # → providers[...] 的键
    model: str                          # 下发 wire 的模型 id
    # 推理意图（仅声明，无 protocol/kind；transport.build_kwargs 负责翻译）
    reasoning_effort: str | None = None
    reasoning_summary: Literal["auto", "concise", "detailed"] | None = None
    # 采样
    temperature: float = 0.7
    max_tokens: int | None = None
    # 限制 / 能力
    context_limit: int = Field(..., gt=0)
    supports_vision: bool = False
    vision_detail: Literal["low", "high", "auto"] | None = "high"
    # 超时 / 重试
    timeout: float = 300
    stream_timeout: float | None = None
    stream_idle_timeout: float | None = None
    max_retries: int = 3
    retry_delay: float = 1.0

class LLMConfig(BaseModel):
    """顶层：连接池 + 模型表 + 默认。无 routes。"""
    providers: dict[str, ProviderConfig]
    profiles: dict[str, LLMProfileConfig]
    default: str

    @model_validator(mode="after")
    def _check_refs(self) -> "LLMConfig":
        # default ∈ profiles；每个 profile.provider ∈ providers（仅配置内部引用）
        ...

    def resolve(self, model_override: str | None = None) -> "ResolvedModel":
        key = model_override or self.default
        profile = self.profiles[key]            # miss → KeyError，fail-fast
        return ResolvedModel(key, profile, self.providers[profile.provider])
```

解析结果不再是带一堆标量副本的 dataclass，而是只持有「键 + 两个源对象引用」的轻量 `NamedTuple`，provider/transport/model 全部按需从引用读、零反规范化：

```python
class ResolvedModel(NamedTuple):
    profile_key: str
    profile: LLMProfileConfig
    provider: ProviderConfig
    # 下游：resolved.profile.model / resolved.provider.transport / resolved.profile.provider
```

**相对旧 schema 的净删除（纯删，代码量下降）**：
- 删类：`PromptCacheConfig`、`LLMRouteConfig`、`ResolvedLLMRoute`；**不引入** `ReasoningConfig`（reasoning 两字段直接平铺，少一个类）。`ResolvedLLMRoute`（`route_key`/`provider`/`transport`/`model` 四个标量副本）→ `ResolvedModel`（持引用，无副本）。连接类只有一个 `ProviderConfig`。
- 删模块级硬编码：`MODEL_FAMILY_DEFAULTS`、`PROVIDER_TRANSPORT`、`PLATFORM_PROVIDERS`、`_infer_model_family`。
- 删 profile 死字段：`api_key`/`base_url`/`api_version`（连接移到 provider）、`model_family`、`reasoning_protocol`、`temperature_policy`、`bedrock_region`、`thinking_effort`（→ `reasoning_effort`）、`fallback_group`（全仓无消费者；fallback 留阶段三随实现加回）。
- 删 profile 方法：`effective_family` / `effective_transport` / `effective_temperature` / `build_extra_kwargs`（前三者失去意义，第四者迁到 `transport.build_kwargs`）。
- 删 `LLMConfig.get_profile`：旧调用方都是「`resolve_route()` 后再 `get_profile(resolved.profile_key)`」的二次查表，`ResolvedModel` 已持 `profile` 引用，调用方改读 `resolved.profile`。
- `resolve_route` → `resolve`（单参数，`route` 语义已空）；`llm_override` 参数删除（见 3.4）；`config/__init__.py` 导出同步更新（去 `LLMRouteConfig`/`ResolvedLLMRoute`，加 `ProviderConfig`/`ResolvedModel`）。

**校验分层**（fail-fast，仅校验配置内部引用，不依赖 provider 实现层）：
- `default` ∈ `profiles`；每个 `profile.provider` ∈ `providers`。provider 合法性由「是否在 `providers` 段声明」决定，不再有 `PLATFORM_PROVIDERS` 白名单。
- **transport tag 合法性不在此校验**（见 5.4）：`config` 是底层、被 `providers` 反向 import（`providers.llm_factory → config.llm`），validator 若 import `_TRANSPORT_BUILDERS` 即构成环形 import 且越权——「transport 字符串有无实现」是 provider 层知识，下沉到 factory dispatch（第 6 节）。transport 合法性由 dispatch 表唯一决定，无第二份 tag 集合。

### 5.4 分层约束（校验归属）
- `config` 层只校验**配置内部引用**（provider/profile/default 之间的指向完整性）——这些无需 provider 实现知识。
- `factory`（provider 层）校验 **transport 可构造性**（dispatch 表查表 miss → 配置错误）。
- 二者各校验自己拥有的东西；`config` 不得 import `providers.*`，杜绝环形 import 与越权。transport tag 的唯一真相源是 `_TRANSPORT_BUILDERS` 的键集合，不另设中立 tag 模块或 registry 层。

### 5.3 迁移
手动改 `config/llm_config.yaml`（clean migration，无主代码兜底）：删 Claude/bedrock profiles、删整个 `routes:` 段、profile key 重命名为对外模型 id（如 `qwen_3_7_max` → `matmaster/qwen3.7-max`，`gemini-pro` → `gemini-3.1-pro-preview`，`gpt55` → `matmaster/gpt-5.5`）、`default` 改指对外 id、删遗留别名 `cds/GPT-5.5`、提连接到 `providers.litellm`、`thinking_effort: X` → `reasoning_effort: X`（`reasoning_summary` 字段名不变）。loader 只认新结构。

---

## 6. 轻量 fail-fast（非 capability 子系统）
仅做静态、就地校验，不建独立 capability matrix / preflight 模块、不做运行时探测。校验按层归属（见 5.4）：

- **加载期（config 层，`LLMConfig` validator）**：profile 的 `provider` 不在 `providers` 段 → 报错；`default` 引用完整性。仅配置内部引用，不依赖 provider 实现。
- **dispatch 期（factory 层）**：未知 transport（`_TRANSPORT_BUILDERS` 查不到）→ 配置错误。这是 transport tag 合法性的**唯一**校验点（避免环形 import 与越权，见 5.4）。
- **构造/请求前（transport 层）**：profile 配了该 transport 明确不支持的特性 → 报错（阶段二 chat_completions 特性面窄，主要是占位约定）。

---

## 7. 响应归一与错误处理
- `normalize_response` / `normalize_stream` 从现 `ChatCompletionsProvider` 内联逻辑拆为可覆盖方法；**保留现有返回形状**：`content` / `reasoning_content` / `tool_calls` / `tool_call_deltas` / `finish_reason` / `usage`（scalar dict）/ `usage_vendor`（provider-native detail）。
- `classify_error`：把现有「翻译 SDK 异常 → `LLMError`」下沉为子类方法；`LLMError(retryable, error_category)` 与 kernel 重试循环不变；不引入自动恢复。

---

## 8. 不碰（阶段二明确不动）
- kernel 主循环、`to_api_dict()`、message IR（`messages.py`）。
- `provider_state`、native transport、fallback（全留阶段三）。
- `BillingLLMProvider`、`UsageCollectingProvider`（`__getattr__` 透传，对内部重构无感）。
- `exp.py` 装配、compaction（仍调 `chat`）、`call_llm_streaming`（阶段一已去 `_timeout`）。

---

## 9. 完成标准
- factory 由 dispatch 表驱动，无 `if/elif`；阶段三加 transport 只需加表项 + 子类。
- `Transport` 基类与 `ChatCompletionsTransport` 子类分离；基类不自满足 Protocol、子类满足。
- chat_completions 请求 kwargs 不再由 config model 生成，改由 `build_kwargs`；`reasoning` 仅声明意图。
- `reasoning_protocol` / `temperature_policy` / `MODEL_FAMILY_DEFAULTS` / `_infer_model_family` / `model_family` / bedrock 全套已删；净代码量下降。
- config schema 从头精简至 3 个 Pydantic 模型（`ProviderConfig` / `LLMProfileConfig` / `LLMConfig`）+ 1 个 `ResolvedModel` NamedTuple；`PromptCacheConfig` / `LLMRouteConfig` / `ResolvedLLMRoute` / `ReasoningConfig`（不引入）/ profile 上 `effective_*` / `build_extra_kwargs` / `get_profile` 全删；profile 为纯数据，reasoning 平铺。
- `providers:` 段落地，连接去重；loader 只认新结构、零兜底。
- `routes:` 表删除，profile key 直接作为对外标识，`resolve`（原 `resolve_route`）无表查找、收为单参数（`llm_override` / `req.llm` 旁路删除）；现有 openai 风格 profile（qwen / gemini / gpt55 / deepseek×2）行为等价；BYOK 行为等价。
- `convert_messages` / `build_kwargs` / `normalize_response` / `normalize_stream` 各有独立测试。

---

## 10. 测试策略（跟随现有测试文化）
- 改造：`test_openai_provider*` → 对 `ChatCompletionsTransport`；`test_llm_factory` → dispatch 表驱动 + `providers:` 段解析；`test_byok_provider` → 合成 profile/固定 transport。
- 新增（纯函数单测）：dispatch 查表命中 + 未知 transport fail-fast（factory 层，transport tag 合法性的唯一校验点）；`build_kwargs`（profile 平铺 reasoning 字段 → openai kwargs：`reasoning_effort` / `extra_body.reasoning`）；`normalize_response` / `normalize_stream`（含 usage scalar + vendor）；config 加载校验（provider 必须在 providers 段、default 引用完整——**不含 transport 合法性**，那归 factory dispatch 测试）；`resolve` 直解（`model_override == profile key` 命中、未知 key fail-fast，返回 `ResolvedModel` 持 profile/provider 引用）；compaction `tool_choice="none"` 路径回归。
- 删除：`test_bedrock_provider.py`；`test_openai_provider_prompt_cache.py` 的 anthropic 断点策略测试（断点策略本身随 opus_global 删除；阶段三 native anthropic 落地时重建相应测试）；涉及 route 表查找、`llm_override` 旁路的解析测试（routes / `llm_override` 删除后无对应路径）。

---

## 11. 阶段三衔接（本阶段为其搭好的基础设施）
- dispatch 表 + `Transport` 基类 seam = 加 `anthropic_messages` / `responses` 子类的插入点（加表项 + 子类，不改 factory）。
- `convert_messages` 接缝已立，stage 3 引中立 IR 时在此填真实转换、移除 `to_api_dict()`。
- `providers:` 段已就位，stage 3 直接加 `anthropic: {transport: anthropic_messages, ...}` / `openai-responses: {transport: responses, ...}`，并把 sonnet/opus 作为 `provider: anthropic` 的 profile 加回（key 为对外模型 id，**不复活 routes 表**）。
- `provider_state`、native thinking/encrypted reasoning、手动切协议 tag 丢弃、prompt cache 断点策略（native 注入）均为阶段三内容，本阶段不预埋实现、仅以接缝形状兼容。

### 11.1 外部参考（hermes-agent `agent/transports/`）
该仓库已落地多 transport（chat_completions / anthropic / bedrock / codex），可作阶段三参照：
- **响应归一收敛为单个 `provider_data` 袋**：其 `NormalizedResponse` 只把真正跨 provider 的字段（content / tool_calls / finish_reason / reasoning / usage）放顶层，全部协议私有状态（anthropic `reasoning_details`、codex items、gemini `thought_signature`）进一个 `provider_data` dict（response 级 + per-tool-call 级）。阶段三以此泛化本文 §7 的 `usage_vendor`。**注意**：hermes `transports/types.py` 为旧调用点保留了一批 backward-compat 影子属性（`tc.function.name`、`reasoning_content` 等）——本项目禁止此类兜底，只学 `provider_data` 袋、不抄影子属性。
- **provider 私有请求差异放 provider 钩子、不放 transport 分支**：hermes 用 `ProviderProfile.build_api_kwargs_extras()`（"transport 读 profile，而不是收 20+ 个 bool flag"）处理"reasoning 放 `extra_body` 还是顶层"这类**后端差异**，使 transport 仅按 wire 协议纯净分化。印证本阶段"剥离 anthropic 方言"的决定，并定下阶段三模式：新后端要求不同请求整形时加 provider 钩子，而非在 transport 里长 if。
- **dispatch 取舍已反证**：hermes 的 transport registry 用 import 副作用自动发现 + miss 返回 `None` 的渐进迁移软回退；本文 §4.4 的静态 dispatch 表 + miss 即 fail-fast 与项目"无兼容兜底"一致，更优，维持不变。
