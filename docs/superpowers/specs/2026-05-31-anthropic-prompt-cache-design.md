# Anthropic Prompt Cache 设计

- Date: 2026-05-31
- Status: Draft, 待审阅
- Author: Kealdoom + Codex
- 影响范围:
  - `config/llm_config.yaml`
  - `matmaster/config/llm.py`
  - `matmaster/providers/llm_factory.py`
  - `matmaster/providers/openai_provider.py`
  - `tests/matmaster/config/test_llm.py`
  - `tests/matmaster/providers/test_llm_factory.py`
  - `tests/matmaster/providers/test_openai_provider.py`

## 1. 背景

当前 `opus` 和 `opus_global` 不是通过 Anthropic SDK 直连，而是以
OpenAI-compatible Chat Completions 形态请求 LiteLLM 代理：

- `config/llm_config.yaml` 中 `opus` 对应 `claude-opus-4-6`。
- `config/llm_config.yaml` 中 `opus_global` 对应
  `global.anthropic.claude-opus-4-6-v1`。
- 二者都使用 `provider: openai`，由 `OpenAIProvider` 发起请求。
- Anthropic thinking 参数已经通过 `LLMProfileConfig.build_extra_kwargs()`
  生成 `extra_body`，再由 `OpenAIProvider` 透传。

当前缺口是：请求里没有任何 `cache_control`。因此即使 system prompt、工具定义和
历史前缀在多轮对话中高度重复，Anthropic 侧也不会主动写入或读取 prompt cache。

本设计要为 `opus` 与 `opus_global` 开启两类缓存：

- 在 system prompt 末尾增加显式 cache breakpoint。
- 在请求顶层增加 automatic `cache_control`，让多轮消息历史随对话增长自动前移缓存点。

## 2. 外部约束

Anthropic Prompt Caching 当前约束如下：

- Automatic caching 通过请求顶层 `cache_control` 开启，系统会自动把 cache breakpoint
  放到最后一个可缓存 block。
- 显式 breakpoint 通过 content block 上的 `cache_control` 开启，适合固定 system prompt
  这类稳定前缀。
- Automatic caching 可以和显式 block-level breakpoint 一起使用，但 automatic breakpoint
  会占用 4 个 breakpoint slot 中的 1 个。
- cache prefix 的顺序是 `tools -> system -> messages`。因此 system block 上的
  breakpoint 实际缓存的是工具定义加 system prompt 这一段前缀。
- 默认 cache TTL 是 5 分钟；`ttl: "1h"` 可用，但写入成本更高。
- `cache_creation_input_tokens` 和 `cache_read_input_tokens` 是判断写入与命中的关键
  usage 字段。
- Claude API、Claude Platform on AWS 和 Microsoft Foundry 支持 automatic caching；
  Bedrock 和 Vertex AI 不支持 automatic caching。

LiteLLM 也提供 `cache_control_injection_points`，可由代理侧把 `cache_control` 注入到
指定 role 的最后一个 content block。但本项目不把代理注入作为主路径。应用层显式生成请求
payload 更容易测试，也能同时覆盖顶层 automatic cache。

参考资料：

- Anthropic Prompt Caching:
  https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- LiteLLM prompt caching injection:
  https://docs.litellm.com.cn/docs/tutorials/prompt_caching

## 3. 目标

- 只对 `opus` 和 `opus_global` 开启 prompt cache。
- 在 system prompt 之后设置一个显式 cache breakpoint。
- 自动配置顶层 `cache_control`，启用 automatic caching。
- 不改变 `AgentKernel`、`Message`、`IncrementalMessagePipeline` 和历史持久化中的消息
  内部格式。
- 不通过 `run_meta`、`RuntimePorts`、Redis 或 DB 传递 cache 状态。
- cache 行为必须完全由本轮 provider 请求 payload 决定，适配 API/Worker 分离架构。
- 配置和请求构造必须可单元测试。

## 4. 非目标

- 不给 `sonnet`、`haiku`、`gemini`、`gpt54`、`qwen`、`deepseek` 等模型开启 cache。
- 不给 `opus_bedrock` 开启 automatic caching。Bedrock 需要单独映射 Converse
  `cachePoint` 语义，本设计不覆盖。
- 不做 cache pre-warm。
- 不在主代码中添加兼容兜底或旧配置 alias。
- 不让 LiteLLM 代理配置成为唯一实现路径。
- 不把 system message 的 content parts 格式写入 checkpoint 或 DB。

## 5. 架构决策

| # | 决策点 | 选择 | 放弃的候选 | 理由 |
|---|---|---|---|---|
| D1 | cache 配置入口 | 在 `LLMProfileConfig` 增加 typed `prompt_cache` | 根据模型名硬编码判断 | 配置显式，便于只启用 `opus` 与 `opus_global` |
| D2 | system breakpoint 位置 | `OpenAIProvider` 请求发送前临时改写 payload copy | 修改 `SystemMessage.to_api_dict()` | 避免污染核心消息模型、pipeline、history restore 和 checkpoint |
| D3 | automatic cache 位置 | 合并进 profile 的 `extra_body.cache_control` | 通过 LiteLLM 代理自动注入 | 当前 thinking 已走 `extra_body`，同一路径更可测 |
| D4 | TTL 默认值 | 默认 5 分钟，可配置 1 小时 | 默认 1 小时 | 活跃对话中 5 分钟会被命中刷新，成本更低 |
| D5 | 缺失 system prompt | fail-fast 抛出非重试错误 | 静默跳过 cache | 配置开启但无 system prompt 是调用链错误，不做隐式兜底 |
| D6 | Bedrock | 暂不启用 | 复用 OpenAI provider 逻辑 | Anthropic 文档明确 Bedrock 不支持 automatic caching |

## 6. 目标结构

### 6.1 配置模型

`matmaster/config/llm.py` 新增：

```python
class PromptCacheConfig(BaseModel):
    provider: Literal["anthropic"] = "anthropic"
    system_prompt_breakpoint: bool = False
    automatic: bool = False
    ttl: Literal["5m", "1h"] = "5m"

    def cache_control(self) -> dict[str, str]:
        data = {"type": "ephemeral"}
        if self.ttl == "1h":
            data["ttl"] = "1h"
        return data
```

`LLMProfileConfig` 新增：

```python
prompt_cache: PromptCacheConfig | None = None
```

`build_extra_kwargs()` 改为合并式构造。Anthropic thinking 与 automatic cache 共用
`extra_body`：

```python
{
    "extra_body": {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "max"},
        "cache_control": {"type": "ephemeral"},
    }
}
```

如果 `ttl` 是 `1h`，则 `cache_control` 为：

```python
{"type": "ephemeral", "ttl": "1h"}
```

### 6.2 YAML 配置

只在 `opus` 和 `opus_global` profile 上启用：

```yaml
prompt_cache:
  provider: "anthropic"
  system_prompt_breakpoint: true
  automatic: true
  ttl: "5m"
```

不在其他 profile 上配置 `prompt_cache`。

### 6.3 Provider-local options

`matmaster/providers/openai_provider.py` 增加 provider-local dataclass，避免 provider 层
反向依赖 config model：

```python
@dataclass(frozen=True)
class AnthropicPromptCacheOptions:
    system_prompt_breakpoint: bool
    cache_control: dict[str, str]
```

`llm_factory.py` 从 profile 读取 `prompt_cache`，只把 provider 需要的窄数据传给
`OpenAIProvider`。

### 6.4 请求改写边界

`OpenAIProvider.chat()` 和 `OpenAIProvider.chat_stream()` 在组装 kwargs 前统一调用：

```python
request_messages = self._prepare_messages(messages)
```

当 `system_prompt_breakpoint` 开启时，`_prepare_messages()` 对 messages 做 deep copy，
找到第一条 `role == "system"` 的消息，并把字符串 content 转成 Anthropic/LiteLLM 可接受的
content block：

```python
{
    "role": "system",
    "content": [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ],
}
```

其他 messages 保持原状。这个改写只发生在 provider 请求边界，不回写 `state.messages`，
不回写 pipeline cache，不进入持久化历史。

## 7. 数据流

目标链路：

```text
config/llm_config.yaml
  -> LLMProfileConfig.prompt_cache
  -> LLMProfileConfig.build_extra_kwargs()
  -> llm_factory.build_provider_bundle()
  -> OpenAIProvider(prompt_cache_options=...)
  -> OpenAIProvider._prepare_messages()
  -> AsyncOpenAI.chat.completions.create(extra_body=..., messages=...)
```

内部 agent 消息仍然是：

```python
{"role": "system", "content": "system prompt text"}
```

发送到 provider 的请求临时变为：

```python
{
    "extra_body": {
        "cache_control": {"type": "ephemeral"},
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "max"},
    },
    "messages": [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "system prompt text",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {"role": "user", "content": "..."},
    ],
}
```

## 8. 失败语义

开启 `system_prompt_breakpoint` 后，以下情况直接抛出非重试 `LLMError`：

- messages 中没有 system message。
- system message content 不是非空字符串。

如果 Anthropic/LiteLLM 返回与 `cache_control` 格式相关的 bad request，也应归类为
非重试 bad request。该错误表示配置或 payload 构造错误，重试不会改善。

不做以下隐式处理：

- 不自动关闭 cache。
- 不自动降级为无 cache 请求。
- 不自动把非字符串 system content 拼回字符串。

## 9. Usage 与可观测性

现有 `OpenAIProvider` 已支持读取：

- `prompt_tokens_details.cached_tokens`
- `cache_read_input_tokens`

本次实现应补充保留以下 vendor usage 字段：

- `cache_creation_input_tokens`
- `cache_read_input_tokens`
- `cache_creation`
- `input_tokens`
- `output_tokens`

`usage_vendor` 应尽量保留 provider 原始结构，便于前端、日志和评测脚本确认是否真的命中。

验证口径：

- 第一次相同前缀请求通常应看到 `cache_creation_input_tokens > 0`。
- 5 分钟内第二次相同前缀请求通常应看到 `cache_read_input_tokens > 0`。
- 如果二者均为 0，可能是 prompt 未达到 Anthropic 对当前模型的平台最小 cache token
  阈值，也可能是代理未透传对应字段。

## 10. 测试计划

### 10.1 Config tests

覆盖 `tests/matmaster/config/test_llm.py`：

- `PromptCacheConfig.cache_control()` 默认返回 `{"type": "ephemeral"}`。
- `ttl == "1h"` 时返回 `{"type": "ephemeral", "ttl": "1h"}`。
- Anthropic thinking 与 automatic cache 合并到同一个 `extra_body`。
- 未配置 `prompt_cache` 的 profile 行为不变。

### 10.2 Factory tests

覆盖 `tests/matmaster/providers/test_llm_factory.py`：

- `opus` profile 构造出的 `OpenAIProvider` 带 `AnthropicPromptCacheOptions`。
- `opus_global` profile 构造出的 `OpenAIProvider` 带 `AnthropicPromptCacheOptions`。
- `sonnet` 或其他未配置 profile 不带 cache options。
- `BedrockProvider` 不接收 automatic cache options。

### 10.3 OpenAIProvider tests

覆盖 `tests/matmaster/providers/test_openai_provider.py`：

- `chat()` 请求会把 system content 字符串转为带 `cache_control` 的 text block。
- `chat_stream()` 请求会做同样转换。
- 原始 `messages` 入参不被原地修改。
- 没有 system message 时抛出非重试 `LLMError`。
- system content 为空或非字符串时抛出非重试 `LLMError`。
- 顶层 `extra_body.cache_control` 与已有 thinking 参数同时存在。

### 10.4 验证命令

实现后运行：

```bash
uv run pytest \
  tests/matmaster/config/test_llm.py \
  tests/matmaster/providers/test_llm_factory.py \
  tests/matmaster/providers/test_openai_provider.py
```

如果要做真实链路验证，可在配置了 LiteLLM 与 Anthropic 后，用 `opus` 或 `opus_global`
连续发起两轮相同 system 前缀请求，观察 `usage_vendor` 中的 cache write/read 字段。

## 11. 实施顺序

1. 在 `matmaster/config/llm.py` 增加 `PromptCacheConfig` 与 `prompt_cache` 字段。
2. 调整 `LLMProfileConfig.build_extra_kwargs()`，支持 thinking 与 automatic cache 合并。
3. 在 `config/llm_config.yaml` 的 `opus` 与 `opus_global` 中启用 `prompt_cache`。
4. 在 `matmaster/providers/openai_provider.py` 增加 provider-local cache options 与
   `_prepare_messages()`。
5. 在 `matmaster/providers/llm_factory.py` 把 profile cache 配置转成 provider options。
6. 扩展 usage vendor 保留字段。
7. 补齐 config、factory、provider 单元测试。
8. 运行目标 pytest 命令。

## 12. 边界说明

这次变更不影响 API/Worker 分离：

- API 进程不需要知道 prompt cache 状态。
- Worker 进程在每次 LLM 调用时按 profile 配置构造请求。
- Redis 不承载 cache 状态。
- `run_meta` 不承载 cache 状态。
- `RuntimePorts` 不新增 cache port。

这次变更也不影响历史恢复：

- 历史消息仍然保持 OpenAI-compatible 字符串形态。
- Provider 发送前的 content block 改写不持久化。
- checkpoint 里不会出现 `cache_control`。
