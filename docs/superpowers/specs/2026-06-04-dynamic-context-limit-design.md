# Dynamic Context Limit Design

## 背景

当前 agent 在 compaction 场景使用固定上下文上限：

- `CompactionConfig.context_limit = 200_000`
- `auto_threshold = context_limit - reserved_summary_tokens - auto_compact_buffer_tokens`
- summary 调用前的历史消息预算为 `context_limit - reserved_summary_tokens - summary_safety_margin_tokens - compact_request_tokens`

这会让所有模型共享同一个压缩触发阈值和 summary 输入预算。但当前平台模型与 BYOK 模型的真实上下文窗口可能不同，例如小窗口模型应更早压缩，大窗口模型应允许更长历史后再压缩。

## 目标

- 让每次运行的 compaction context limit 根据实际使用的模型动态确定。
- 新增字段统一命名为 `context_limit`，不引入 `context_window`。
- 平台模型从 LLM profile 读取 `context_limit`。
- BYOK 模型支持 credential 级 `context_limit`，未配置时沿用默认 `200_000`。
- 保持 compaction 内部现有 `CompactionConfig.context_limit` 契约。
- 不把 `context_limit` 作为 provider 请求参数发送给模型服务。

## 非目标

- 不通过模型名字符串推断上下文窗口。
- 不改普通 provider request body 的参数语义。
- 不把 `context_limit` 放进 BYOK `model_params`。
- 不引入兼容别名、自动迁移或主代码内联兜底迁移逻辑。
- 不重构 compaction 算法、token estimator 或 summary prompt。

## 当前代码事实

- `CompactionConfig` 位于 `matmaster/types/runtime.py`，已有 `context_limit` 字段。
- `ExpConfig.compaction` 默认使用 `CompactionConfig()`，当前 exp TOML 基本没有覆盖 compaction。
- `AgentRunService` 已通过 `build_provider_bundle()` 解析出本轮模型、profile、route。
- `AgentRunRequest` 已传递 `llm_model`、`llm_model_profile`、`llm_model_route`。
- `Exp.build_runtime()` 把 `self._config.compaction` 原样传给 `build_runtime_context_assembly()` 和 `AgentKernelSpec`。
- `ContextCompactor` 使用 `CompactionConfig.context_limit` 判断 preflight/runtime 是否触发压缩。
- `run_compaction_plan()` 把 `kernel_spec.compaction.context_limit` 传给 `call_summary_llm_response()`。
- `prepare_messages_for_summary_call()` 使用 `context_limit` 计算 summary 输入预算。
- BYOK credential 当前只返回 `model`、`base_url`、`api_key`、`model_params`；`model_params` 会被原样合并进 provider `extra_body`。
- devshell 已解析 `resolved_route`，但当前 `DevRunner` 构造 `AgentRunRequest` 时没有填入模型 identity。

## 设计决策

### 1. 统一新增字段名为 context_limit

新增字段统一叫 `context_limit`：

- `LLMProfileConfig.context_limit`
- `ByokCredential.context_limit`
- `LLMProviderBundle.context_limit`
- `AgentRunRequest.context_limit`

已有的 `CompactionConfig.context_limit` 保持不变。运行时通过 effective compaction 把模型侧解析出的 `context_limit` 注入 compaction：

```python
effective_compaction = exp_config.compaction.model_copy(
    update={"context_limit": request.context_limit}
)
```

### 2. 平台模型 profile 必须配置 context_limit

平台模型的 `context_limit` 写在 `config/llm_config.yaml` profile 顶层：

```yaml
profiles:
  qwen_3_7_max:
    provider: "openai"
    model: "matmaster/qwen3.7-max"
    context_limit: 1000000
```

`LLMProfileConfig` 增加正整数校验。平台模型缺失该字段应 fail-fast，因为平台模型配置由本项目维护，不能继续依赖全局固定默认。

### 3. BYOK credential 可选配置 context_limit

BYOK credential response 顶层可带 `context_limit`：

```json
{
  "model": "qwen-max",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "context_limit": 1000000,
  "model_params": {
    "enable_thinking": true
  }
}
```

`context_limit` 不属于 `model_params`。`model_params` 仍只代表要透传给 provider 的 `extra_body`。

### 4. BYOK 缺省沿用 200_000

BYOK credential 未返回 `context_limit` 时，使用：

```python
BYOK_DEFAULT_CONTEXT_LIMIT = 200_000
```

这保持当前系统行为，不会让 BYOK 相比现状更激进或更保守。用户确认需要拓展时，再在 credential 中显式配置更大的 `context_limit`。

### 5. 记录 context_limit_source 便于排查

`LLMProviderBundle` 增加只用于诊断的来源字段：

```python
context_limit_source: Literal[
    "profile",
    "byok_credential",
    "byok_default",
]
```

该字段不参与预算计算，只用于日志、事件或 debug。典型排查场景是 BYOK 用户认为自己使用 1M 模型但系统很早压缩，此时可以确认本轮实际来源是否为 `byok_default`。

## 数据流

平台模型：

```text
config/llm_config.yaml profile.context_limit
  -> LLMProfileConfig.context_limit
  -> LLMProviderBundle.context_limit
  -> AgentRunRequest.context_limit
  -> effective CompactionConfig.context_limit
  -> ContextCompactor / summary caller
```

BYOK 模型：

```text
tools-server credential.context_limit 可选
  -> ByokCredential.context_limit | None
  -> build_byok_provider_bundle
  -> None 时使用 BYOK_DEFAULT_CONTEXT_LIMIT = 200_000
  -> LLMProviderBundle.context_limit
  -> AgentRunRequest.context_limit
  -> effective CompactionConfig.context_limit
  -> ContextCompactor / summary caller
```

## 组件改动

### matmaster/config/llm.py

- `LLMProfileConfig` 增加 `context_limit: int`。
- 平台 profile 要求显式配置正整数。
- `ResolvedLLMRoute` 不需要增加字段，因为它只表达 route/profile/provider/model identity。

### config/llm_config.yaml

- 为所有平台 profiles 补充 `context_limit`。
- 值由当前平台实际模型能力维护，不通过代码猜测。

### matmaster/providers/llm_factory.py

- `LLMProviderBundle` 增加 `context_limit` 和 `context_limit_source`。
- `build_provider_bundle()` 从 profile 复制 `context_limit`，source 为 `profile`。
- `build_byok_provider_bundle()` 接收可选 `context_limit`。
- BYOK 未传时使用 `BYOK_DEFAULT_CONTEXT_LIMIT = 200_000`，source 为 `byok_default`。
- BYOK 显式传入时 source 为 `byok_credential`。

### src/services/llm_credential_client.py

- `ByokCredential` 增加 `context_limit: int | None = None`。
- 从 response 顶层读取 `context_limit`。
- `model_params` 继续只进入 `extra_body`。
- 对显式 `context_limit` 做正整数校验；缺失时保留 `None`，交给 BYOK provider bundle 使用默认值。

### matmaster/core/run_context.py

- `AgentRunRequest` 增加 `context_limit: int | None = None`。
- service 和 devshell 都应填入该字段。

### src/services/agent_run_service.py

- 平台模型路径从 `llm_bundle.context_limit` 填入 `AgentRunRequest.context_limit`。
- BYOK 路径把 credential 的 `context_limit` 传给 `build_byok_provider_bundle()`。
- 不向 `run_meta` 注入 context limit。

### matmaster/devshell/runner.py

- 使用 `resolved_route` 和 provider bundle 信息补齐 devshell 的模型 identity 与 `context_limit`。
- devshell 路径应和 API 路径一样进入 effective compaction，避免本地调试继续固定 200_000。

### matmaster/core/exp.py

- 在 `build_runtime()` 中派生 effective compaction：

```python
compaction = (
    self._config.compaction.model_copy(update={"context_limit": request.context_limit})
    if request.context_limit is not None
    else self._config.compaction
)
```

- 将同一个 `compaction` 同时传给 `build_runtime_context_assembly()` 和 `AgentKernelSpec`。
- 这样 trigger planning 与 summary budget 使用同一个值。

## 错误处理

- 平台 profile 缺少 `context_limit`：配置错误，加载或 provider bundle 构造时失败。
- 平台 profile 的 `context_limit <= 0`：配置错误。
- BYOK credential 缺少 `context_limit`：使用 `BYOK_DEFAULT_CONTEXT_LIMIT`。
- BYOK credential 显式传入非法 `context_limit`：凭证配置错误，不使用默认值掩盖错误。

## 示例

32K BYOK 模型未显式配置时：

```text
context_limit = 200_000
context_limit_source = byok_default
```

这是当前行为的延续。若用户发现 provider 上下文不足，应在 credential 中配置：

```json
{
  "context_limit": 32768
}
```

1M BYOK 模型显式配置时：

```text
context_limit = 1_000_000
context_limit_source = byok_credential
auto_threshold = 1_000_000 - 8_000 - 13_000 = 979_000
```

## 测试计划

- `LLMProfileConfig` 解析 `context_limit`，并拒绝缺失或非正值。
- `build_provider_bundle()` 暴露 profile context limit。
- `build_byok_provider_bundle()` 在缺失时使用 `BYOK_DEFAULT_CONTEXT_LIMIT`。
- `build_byok_provider_bundle()` 在显式传入时使用 credential context limit。
- `fetch_byok_credential()` 从顶层读取 `context_limit`，且不把它放入 `extra_body`。
- `AgentRunService` 将 bundle context limit 传入 `AgentRunRequest`。
- `Exp.build_runtime()` 使用 effective compaction，同时影响 `ContextCompactor` 与 `AgentKernelSpec`。
- 现有 summary budget 测试继续覆盖 `CompactionConfig.context_limit` 到 `prepare_messages_for_summary_call()` 的预算公式。

## 风险与边界

- BYOK 默认 200_000 对小窗口模型可能仍然过宽，但这是当前行为的显式延续；用户需要通过 credential 配置更小值。
- 平台模型要求显式配置 `context_limit`，会要求同步更新 `config/llm_config.yaml`。
- 不使用模型名推断，避免把供应商能力表散落进主代码。
- `context_limit_source` 只做诊断，不应进入 compaction 预算公式。
