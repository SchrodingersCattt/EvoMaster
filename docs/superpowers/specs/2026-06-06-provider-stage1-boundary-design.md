# 阶段一：Provider 身份与协议边界收束 — 详细设计

- 日期：2026-06-06
- 状态：设计已确认，待写实现计划
- 范围：provider 聚合重构三阶段中的**阶段一**详细设计。让 provider 语义变诚实、未知配置启动期 fail-fast、现有行为等价。
- 上位文档：`docs/superpowers/specs/2026-06-06-provider-aggregation-design.md`（三阶段总方向 + 硬约束）
- 本文细化总文档第 5 节与第 8.1 节，并在一处刻意偏离总文档的早期草图（见 §2 备注）。

---

## 1. 目标与边界

让当前系统的 provider 语义变诚实：

- provider 与 transport 的层级关系显式化：`provider`（厂商/网关，用户友好）声明走哪个 `transport`（后端真正实现的 API 协议契约）。
- 误配置启动期 fail-fast，不再静默走 OpenAI 兼容路径。
- `OpenAIProvider` 改名收束，不再暗示只代表 OpenAI 厂商。
- `LLMProvider` Protocol 补全 kernel 实际依赖的 timeout/retry 属性，kernel 不再读 provider 私有 `_timeout`。
- BYOK 身份收束，不再伪装成平台 openai provider。

**现有 chat_completions 与 bedrock 路径行为必须等价。** 不动 kernel 主循环、不动 message IR、不引入 provider_state、不加 native transport、不删 Bedrock、不做 fallback。

### 1.1 provider 与 transport 的关系（本次架构基准）

- `transport` 是后端唯一真实的 API 协议契约：阶段一只有 `chat_completions` 与 `bedrock_converse`，代码只认 transport。
- `provider` 是架在 transport 之上的一层用户友好封装，代表厂商/网关，作用是声明走哪个 transport 并携带该来源的连接信息（`base_url`/`api_key` 等）。
- 方向是单向的：**provider → 决定 → transport**。多个 provider 可共享同一个 transport。
- 阶段一的映射用代码内置小字典表达（轻量 registry），vendor 级细分（openai/qwen/deepseek 各自独立 provider + 连接）留到阶段二的 `providers:` 段。

---

## 2. 配置模型改动（`matmaster/config/llm.py`）

### 2.1 内置 provider→transport 映射

新增两个模块级常量，作为阶段一的轻量 registry。**刻意拆成两层，互不等同**：

```python
# (1) transport 查找表：所有真实 provider → 其 transport。含运行时 BYOK。
PROVIDER_TRANSPORT: dict[str, str] = {
    "litellm": "chat_completions",
    "bedrock": "bedrock_converse",
    "byok":    "chat_completions",
}

# (2) 平台 YAML 白名单：允许出现在 config/llm_config.yaml profile 里的 provider。
#     不含 byok —— byok 只能由运行时凭证路径构造，不能写进静态平台配置。
PLATFORM_PROVIDERS: frozenset[str] = frozenset({"litellm", "bedrock"})
```

- `PROVIDER_TRANSPORT` 用于 `effective_transport()` 的协议查找（含 byok）。
- `PLATFORM_PROVIDERS` 用于 `LLMConfig` 加载期校验（§2.4），是 YAML profile 合法 provider 的受控集合。
- 阶段一所有经 LiteLLM proxy 的 profile 统一标 `provider: litellm`；Bedrock 标 `provider: bedrock`；BYOK 在代码里用 `provider: byok`，**不出现在 YAML**。

> **为什么拆两层（采纳 review P1）**：若 YAML 校验直接用 `PROVIDER_TRANSPORT` 的 key 集合，则有人在 `llm_config.yaml` 误写 `provider: byok` 不会 fail-fast，而会进入非 BYOK 的平台路径（`build_provider_bundle` + `billing_mode="platform"`，见 `agent_run_service.py` 的 else 分支）——与阶段一"误配置 fail-fast、provider 身份诚实"的目标直接冲突。BYOK 真正生效只在 `byok_credential_id` 非空的运行时分支。故 transport 查找与平台白名单必须拆开。

> **与总文档 8.1 的偏离（刻意）**：总文档 8.1 草图在 profile 上写了显式 `transport:` 字段。本阶段确认改为 **provider 为主、transport 由 provider 推出、无显式 transport override 字段**。理由：provider→transport 是单向决定关系，profile 只需声明 provider；显式 transport 字段会让两者看起来像平行独立维度，与架构基准（§1.1）相悖。vendor 级 provider 细分仍按总文档留待阶段二。

### 2.2 `LLMProfileConfig`

- **不新增** `transport` 字段。
- `provider` 字段默认值从 `"openai"` 改为 `"litellm"`（新的常见网关；保持字段可选，独立构造时落到合法默认）。
- 新增方法：

  ```python
  def effective_transport(self) -> str:
      return PROVIDER_TRANSPORT[self.provider]
  ```

  调用前 provider 合法性已由 §2.4 校验保证；独立构造的 BYOK profile 用合法的 `byok`。
- **不碰** `reasoning_protocol` / `reasoning_summary` / `temperature_policy` / `thinking_effort` / `model_family` / `_infer_model_family` / `build_extra_kwargs` / `MODEL_FAMILY_DEFAULTS`。这些是阶段二的 reasoning 整合范围。

### 2.3 `ResolvedLLMRoute`

- 新增字段 `transport: str`。
- `resolve_route` 在两条返回分支里都用 `profile.effective_transport()` 带出 transport，让路由解析结果显式携带 provider + transport（满足完成标准、便于测试）。

### 2.4 校验位置：config 加载期 fail-fast

- 在 `LLMConfig._validate_internal_references`（`model_validator(mode="after")`）里增加一轮：遍历 `self.profiles`，任一 profile 的 `provider` 不在 **`PLATFORM_PROVIDERS`** 即 raise，错误信息带上 profile key 与可用 provider 列表（沿用现有 default/route 校验的风格）。因 `byok ∉ PLATFORM_PROVIDERS`，YAML 误写 `provider: byok` 会启动期 fail-fast。
- 选择 `LLMConfig` 层而非 `LLMProfileConfig` 层，是为了错误信息能定位到具体 profile key。
- BYOK 走独立构造的 `LLMProfileConfig`（不进 `LLMConfig`，不受 `PLATFORM_PROVIDERS` 校验约束），其 `provider="byok"` 在 `PROVIDER_TRANSPORT` 内，`effective_transport()` 可正常解析为 `chat_completions`。

---

## 3. Factory 显式分发（`matmaster/providers/llm_factory.py`）

- 删除 `if profile.provider == "bedrock": ... else: OpenAI` 的隐式分支。
- 改为按 `resolved.transport` 显式分发：
  - `chat_completions` → `ChatCompletionsProvider`
  - `bedrock_converse` → `BedrockProvider`
  - 其他 → raise 配置错误（启动期校验已拦截未知 provider，这里是防御性兜底）
- `build_provider_bundle` 两条分支的 bundle 字段保持现状（`provider_name=profile.provider` 等）。

---

## 4. Provider 命名收束（clean rename）

- 文件：`matmaster/providers/openai_provider.py` → `matmaster/providers/chat_completions_provider.py`
- 类：`OpenAIProvider` → `ChatCompletionsProvider`
- 仍用 OpenAI SDK、仍调 `chat.completions.create(...)`，行为不变。重构成 transport 子类是阶段二。
- `AnthropicPromptCacheOptions` 及同文件内的 prompt cache 逻辑**原样保留在该文件**（搬迁到 native anthropic transport 是阶段三）。
- 更新引用点：
  - `matmaster/providers/__init__.py`（import + `__all__`）
  - `matmaster/providers/llm_factory.py`
  - **`scripts/lint_no_arguments_mutation.py` 的 `ALLOWLIST_PREFIXES`**（采纳 review P2a）：把 `"matmaster/providers/openai_provider.py"` 改为新路径 `"matmaster/providers/chat_completions_provider.py"`。该 provider 内有流式工具参数累积（`current.arguments += ...`、`current.arguments = merged_args`），改名后会落入 pre-commit 的 `lint-no-arguments-mutation` 扫描；不更新 allowlist 会被旧白名单卡住。脚本测试 `tests/scripts/test_lint_no_arguments_mutation.py` 不 pin 该路径字符串，无需改动（已核实）。
- 测试文件改名（迁移，不新增）：
  - `tests/matmaster/providers/test_openai_provider.py` → `test_chat_completions_provider.py`
  - `test_openai_provider_errors.py` → `test_chat_completions_provider_errors.py`
  - `test_openai_provider_tool_choice.py` → `test_chat_completions_provider_tool_choice.py`
  - `test_openai_provider_prompt_cache.py` → `test_chat_completions_provider_prompt_cache.py`
  - 其余引用 `OpenAIProvider` 的测试（`test_llm_factory.py`、`test_tool_protocol_guardrails.py`、`devshell/test_devshell_mcp_skill_filter.py`）更新 import/符号名。

---

## 5. 补全 `LLMProvider` Protocol + 去私有读取

### 5.1 Protocol（`matmaster/types/llm_provider.py`）

显式声明 kernel 依赖的属性：

```python
stream_timeout: float
stream_idle_timeout: float
max_retries: int
retry_delay: float
```

### 5.2 两个 provider 的 property 收口

现状两 provider 的 `stream_timeout` / `stream_idle_timeout` property 直接返回可能为 `None` 的私有字段，而 `__aenter__` 内部真正用的是 `_stream_timeout or _timeout`。把这个兜底折进 property，使其返回具体 `float`、永不返回 `None`，与既有有效语义一致：

- `ChatCompletionsProvider.stream_timeout` → `self._stream_timeout if self._stream_timeout is not None else self._timeout`
- `ChatCompletionsProvider.stream_idle_timeout` → `self._stream_idle_timeout if self._stream_idle_timeout is not None else self._timeout`
- `BedrockProvider` 两个对应 property 同样处理。
- `max_retries` / `retry_delay` 已是具体值，不变。

### 5.3 kernel 去私有读取（`matmaster/core/agent_llm_stream.py:263-267`）

改为直接读公共属性，删除对私有 `_timeout` 的兜底：

```python
current_timeout = provider.stream_timeout
max_retries = provider.max_retries
retry_delay = provider.retry_delay
```

因 §5.2 保证 `stream_timeout` 永不为 None，**行为等价**。

### 5.4 Protocol 一致性 mock 更新（采纳 review P2b）

给 `@runtime_checkable` 的 `LLMProvider` 增加四个数据成员后，缺这些属性的 mock 会不再满足 `isinstance(..., LLMProvider)`。需更新两处测试 mock，补上 `stream_timeout`/`stream_idle_timeout`/`max_retries`/`retry_delay`：

- `tests/matmaster/types/test_llm_provider.py` 的 `CompleteLLMProvider`（其 `test_protocol_check_complete` 断言 `isinstance==True`，不补会变 False）。
- `tests/conftest.py` 的 `MockAsyncLLMProvider`（被多处 kernel 流式测试复用；kernel 改为直读 `provider.stream_timeout` 后，mock 缺该属性会 `AttributeError`）。

**不改两个 wrapper**：`BillingLLMProvider` / `UsageCollectingProvider` 已用 `def __getattr__(self, name): return getattr(self._inner, name)` 全量透传，运行时读 `wrapper.stream_timeout` 自动委托给 inner（真 provider 有该属性）；且 matmaster/src 内**无任何运行时 `isinstance(provider, LLMProvider)`**（已核实，0 处），wrapper 的 Protocol 一致性不被运行时依赖。给 wrapper 加冗余 pass-through property 无收益，不做。

---

## 6. BYOK 身份收束（`build_byok_provider_bundle`）

- 构造的临时 `LLMProfileConfig` 由 `provider="openai"` 改为 `provider="byok"`。
- `LLMProviderBundle.provider_name` 由硬编码 `"openai"` 改为 `"byok"`。
  - 已确认 `bundle.provider_name` 下游无任何消费（仅在 factory 内赋值），改动零风险。
- BYOK 仍直接构造 `ChatCompletionsProvider`（不经 factory 的 transport 分发），其语义即 `provider=byok` / `transport=chat_completions`。
- `billing_mode="byok"` 在 `agent_run_service.py` 调用侧决定，**不动**。
- **未来项（非本阶段）**：BYOK 携带具体 vendor provider（需 tools-server 在凭证里下发 provider 字段）。阶段一用 `provider=byok` 占位。

---

## 7. 配置迁移（clean migration，手改 `config/llm_config.yaml`）

阶段一无自动兼容，手动迁移：

- 以下 7 个 profile 的 `provider: "openai"` 改为 `provider: "litellm"`：
  `sonnet` / `gemini-pro` / `gpt55` / `qwen_3_7_max` / `deepseek_v4_pro` / `deepseek_v4_pro_mm` / `opus_global`
- `opus_bedrock` 保持 `provider: "bedrock"`。
- `prompt_cache.provider: "anthropic"`（`PromptCacheConfig` 内）与本次无关，不动。
- loader 只认新结构，零兜底。任一 profile 留旧/未知 provider 将启动期 fail-fast。

---

## 8. 测试策略（跟随现有测试文化）

### 8.1 改造
- `test_llm_factory`：验证按 transport 分发（`chat_completions → ChatCompletionsProvider`、`bedrock_converse → BedrockProvider`）、未知 provider 启动期 fail-fast。
- `test_chat_completions_provider*`（由 `test_openai_provider*` 改名迁移）：符号名更新，断言不变。

### 8.2 新增纯函数单测
- `effective_transport()`：`litellm/byok → chat_completions`、`bedrock → bedrock_converse`。
- 未知 provider 的 profile 进 `LLMConfig` 时加载期 raise，错误信息含 profile key。
- **YAML profile 写 `provider: byok` 时加载期 fail-fast**（`byok ∉ PLATFORM_PROVIDERS`，采纳 review P1）。
- `resolve_route` 结果携带正确 `transport`。
- `LLMProvider` Protocol 四属性（`stream_timeout`/`stream_idle_timeout`/`max_retries`/`retry_delay`）在两 provider 上可读且为具体值。
- BYOK identity：`build_byok_provider_bundle` 产出 `provider_name="byok"`，内部 profile `provider="byok"`、`effective_transport()=="chat_completions"`。

### 8.3 测试夹具更新（采纳 review P2b）
- `tests/matmaster/types/test_llm_provider.py:CompleteLLMProvider` 与 `tests/conftest.py:MockAsyncLLMProvider` 补齐四个新属性，保持 Protocol 一致性与 kernel 直读不报错。

### 8.4 等价性
- chat_completions / bedrock 现有行为断言保持不变。

---

## 9. 完成标准（对齐总文档 §5.4）

- 所有 routes 解析出明确 provider + transport；未知 provider 启动期 fail-fast。
- `OpenAIProvider` 命名不再误导（已改名 `ChatCompletionsProvider`）。
- kernel 不再读 provider 私有 `_timeout`。
- BYOK 不再伪装成平台 openai provider（`provider=byok`）。
- 当前 chat_completions 与 bedrock 路径行为等价。

---

## 10. 不做（守住阶段边界）

- 不改 kernel 主循环结构、不动 message IR / `to_api_dict()`。
- 不引入 provider_state、不新增 native transport、不删 Bedrock、不做 fallback。
- 不删 `reasoning_protocol` / `temperature_policy` / `_infer_model_family`（阶段二 reasoning 整合范围）。
- 不做 vendor 级 provider 细分与 `providers:` 连接段（阶段二）。
- 不清理 `bundle.provider_name` / `model_family` / `context_limit_source` 等下游未消费字段（超出阶段一范围）。
