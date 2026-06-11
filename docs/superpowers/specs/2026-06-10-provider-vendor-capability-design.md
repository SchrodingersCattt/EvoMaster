# Provider vendor 能力位（详细设计）：顶层 prompt cache 方言收口

- 日期：2026-06-10
- 状态：brainstorming 逐段确认完成（方向 A 收敛实现），待落实施
- 上游：
  - `docs/superpowers/specs/2026-06-06-provider-aggregation-design.md`（三阶段总方向；第 4 节硬约束，尤其决策 #6 provider/transport 双层、#9 静态 capability 声明不建 matrix）
  - `docs/superpowers/specs/2026-06-08-provider-aggregation-stage3b-design.md`（native `anthropic_messages` transport，本设计改动落在它产出的代码上）
- 范围：在已落地的 `anthropic_messages` transport 之上，把「同协议、不同 vendor 的请求体方言」收口成一个挂在 `ProviderConfig` 上的静态能力位。**首个、也是当前唯一的能力维度**：vendor 是否接受 native 的顶层（请求级）`cache_control` 自动缓存字段。其余 vendor 方言（直连 Bedrock/Vertex 的 client 构造、model-id 解析）不在本次，仅说明缝留在何处。

> 母文档第 4 节决策仍为硬约束。本设计不新增协议、不动 kernel、不改持久化 schema、不动任何 profile；只在 provider 连接层加一个布尔能力位，并把 transport 里被耦合的 `automatic` 拆成两个独立内部门。

---

## 1. 背景与触发

母文档把 provider 与 transport 分成两层（决策 #6）：`transport` 表达 wire 协议（Messages / ChatCompletions / Responses），`provider` 表达「连到哪个后端」。但当前 `provider` 在代码里仅承载 `transport + api_key + base_url`（`config/llm.py:10` `ProviderConfig`），factory 也纯按 `provider.transport` 分发——这只能表达「换 base_url + 换 key、仍用同一个 SDK client 就能打通」的 vendor 差异，表达不了**同协议下 vendor 的请求体方言**。

真实触发：`global.anthropic.claude-opus-4-6-v1` profile → `litellm-anthropic` provider（`transport: anthropic_messages`，`base_url` 指向 LiteLLM 代理的 Anthropic 兼容端点）→ 代理转发到 Bedrock。该链路上：

- native（`api.anthropic.com` 直连，或本就接受该字段的端点）：接受**顶层** `cache_control`（请求级自动缓存——服务端自动把断点落在最后一个可缓存块上）。
- Bedrock 转发链：**拒绝**这个顶层字段；但**块级** `cache_control`（打在 system / content / tool_result 块上，含 `ttl:"1h"`）照常接受（已实测确认）。

根因在 `transports/anthropic_messages.py:489`：`build_kwargs` 在 `options.automatic` 为真时，往 `extra_body` 注入 `{"cache_control": ...}`，经 SDK 成为请求体顶层字段。opus profile 的 `prompt_cache.automatic = True`（`config/llm_config.yaml`），于是命中。

这是一个干净的 **vendor 能力差异**：同一个 opus、同一个 Messages 协议，仅因 vendor 不同而「能否发顶层自动缓存字段」不同。能力归属的轴是 **vendor/provider**，既非 model、也非协议——因此应挂在 `ProviderConfig` 上，由 transport 的 `build_kwargs` 消费。

---

## 2. 现状基线（以当前代码为准）

| 关注点 | 当前代码事实（截至 2026-06-10） |
|---|---|
| `ProviderConfig` 字段 | 仅 `transport` / `api_key` / `base_url`（`config/llm.py:10-15`），无任何能力/方言字段 |
| factory 分发 | `_TRANSPORT_BUILDERS[provider.transport]`（`llm_factory.py:133-153`），provider 间唯一差别是塞给同一 transport 的 base_url + key |
| `litellm-anthropic` 与 `anthropic` | `config/llm_config.yaml` 中**已有两个 provider 共用 `anthropic_messages`**：`anthropic`（直连）、`litellm-anthropic`（代理→Bedrock）。provider/transport 双层的骨架已存在，只是 provider 不带方言 |
| `automatic` 的语义耦合 | `AnthropicPromptCacheOptions.automatic`（`anthropic_messages.py:33`）同时驱动两件事：(a) 在 `_select_anthropic_cache_targets` 里 gate `latest_user`/`tool_result`/`flexible` **块级**断点启发式（`:132/:137/:150`）；(b) 在 `build_kwargs:489` 注入**顶层**自动缓存字段。两者被绑死成一个开关 |
| 断点槽预留 | `_select_anthropic_cache_targets:114` `max_block_targets = max_breakpoints - (1 if options.automatic else 0)`——当 `automatic` 真时预留 1 个槽给顶层自动断点 |
| `ttl` | `PromptCacheConfig.cache_control()`（`config/llm.py:30`）对 `ttl=="1h"` 产 `{"type":"ephemeral","ttl":"1h"}`；Bedrock 对块级 `ttl:"1h"` 不拒，被拒的只有顶层字段本身 |

官方口径核准（claude-api skill / prompt-caching 文档）：顶层 `cache_control={"type":"ephemeral"}` 是 native 的自动缓存特性（auto-place 在最后一个可缓存块）；块级 `cache_control` 支持显式 `ttl:"1h"`。两者均为 native 支持，与上面观测一致。

**结论**：被 Bedrock 转发链拒绝的就是顶层自动缓存字段；块级断点（含 `ttl:1h`）无须改动。修复 = 让顶层字段的发送与否由 vendor 能力决定，块级断点不受影响。

---

## 3. 设计原则与决策对齐

- **决策 #6（provider/transport 双层）**：本设计让 `provider` 真正承担「vendor 方言」而非只承担连接参数，是对该决策的落实而非偏离。
- **决策 #9（静态 capability 声明、请求前 fail-fast、不做运行时探测、不建 capability matrix/preflight 子系统）**：能力位是**静态、就地**声明的单个布尔，不引入能力矩阵、不做运行时探测。
- **transport 保持 vendor 无关**：transport 不出现 `if vendor == "bedrock"`。vendor 知识只活在 `ProviderConfig`（声明）与 factory（装配/合流）；transport 只读合流后的内部门 `emit_top_level_auto`。
- **clean migration、零兜底**：不在主代码写「检测到 Bedrock 就自动剥字段」的内联兜底；能力由 config 显式声明，主代码只读声明。
- **收敛实现**：单布尔起步，不预设嵌套能力对象（YAGNI；真出现第二个 vendor quirk 再加字段）。

---

## 4. 设计：拆开 `automatic` + provider 能力位

两步：

**(1) 拆 `automatic` 的语义耦合。** 在 `AnthropicPromptCacheOptions` 内把「发顶层自动字段」从 `automatic` 中拆出为独立内部门 `emit_top_level_auto`：

- `automatic`（保留语义）：是否启用**块级**自动断点启发式（`latest_user`/`tool_result`/`flexible`）。
- `emit_top_level_auto`（新增）：是否**额外**发送 native 顶层自动缓存字段，并为它预留 1 个断点槽。

**(2) 能力归 vendor，在 factory 合流。** `ProviderConfig` 加静态能力位 `supports_automatic_cache: bool = True`（默认 True = native 行为）。在 factory 的 prompt-cache options 装配点把「profile 策略」与「provider 能力」合流：

```
emit_top_level_auto = profile.prompt_cache.automatic and provider.supports_automatic_cache
```

`_build_anthropic_prompt_cache_options` 本就是 profile→options 的装配点（`llm_factory.py:67`），合流逻辑落在这里最自然——它是同时看得见 profile 与 provider 的层，transport 不必知道 vendor。

数据流：`ProviderConfig.supports_automatic_cache`（声明）→ factory 合流 → `AnthropicPromptCacheOptions.emit_top_level_auto`（解析后的内部门）→ `build_kwargs` 消费。kernel、profile schema、IR 全程不参与。

---

## 5. 精确改动点

| # | 文件:位置 | 改动 |
|---|---|---|
| 1 | `config/llm.py` `ProviderConfig`（:10） | 加字段 `supports_automatic_cache: bool = True`（顶层 auto cache_control 能力；默认 native 行为） |
| 2 | `transports/anthropic_messages.py` `AnthropicPromptCacheOptions`（:27） | 加字段 `emit_top_level_auto: bool = False`（紧随 `automatic`，保持其余字段顺序；factory 全 keyword 构造，不破坏调用） |
| 3 | `transports/anthropic_messages.py` `_select_anthropic_cache_targets`（:114） | 预留槽改为按顶层发送与否：`1 if options.automatic` → `1 if options.emit_top_level_auto`（不发顶层即不占槽，Bedrock 可用满 4 个块级断点而非 3 个） |
| 4 | `transports/anthropic_messages.py` `build_kwargs`（:489） | extra_body 门改判：`if options.automatic:` → `if options.emit_top_level_auto:`（块级标记逻辑不变，仍由 `automatic` 经 `_select_anthropic_cache_targets` 内部 gate） |
| 5 | `providers/llm_factory.py` `_build_anthropic_prompt_cache_options`（:67，调用点 :100） | 签名加入参 `provider: ProviderConfig`；options 构造加 `emit_top_level_auto=prompt_cache.automatic and provider.supports_automatic_cache`；`_build_anthropic_messages_transport` 调用处传入 `provider` |
| 6 | `config/llm_config.yaml` `litellm-anthropic` provider 块 | 加一行 `supports_automatic_cache: false`（native `anthropic` provider 保持默认 True，不写该行） |
| 7 | 对应 anthropic transport 测试 | 补纯函数单测：`emit_top_level_auto=False` → `build_kwargs` 仍放块级断点、但 `kwargs` 无 `extra_body`（或其内无顶层 `cache_control`）；`=True` → 有。对齐母文档决策 #12（新 transport 补纯函数单测）的测试文化 |

实现注记：

- `AnthropicPromptCacheOptions` 为 `frozen` dataclass，factory（`llm_factory.py:73-82`）以全 keyword 构造，新增带默认值字段不破坏现有构造；其余非 factory 构造路径（测试）默认 `False`，安全。
- 改动 #4 是单行判据替换；块级断点（system 经 `system_prompt_breakpoint`、其余经 `automatic`）的放置逻辑完全不变。

---

## 6. 净效果

- **opus-via-bedrock（`litellm-anthropic`）**：`prompt_cache.automatic` 仍 True（块级 system/latest_user/tool_result/flexible 照常，`ttl:1h` 照常），但 `supports_automatic_cache=false` 使 `emit_top_level_auto` 合流为 False——不再发顶层字段，400 消失，块级缓存收益保住；且因改动 #3，块级断点上限回到 4。
- **native `anthropic`**：`supports_automatic_cache` 默认 True，`emit_top_level_auto` 仍随 `automatic`，行为零变化；现有断言顶层字段的测试在默认 True 下继续通过，测试改动最小。
- **profile 零改动**：所有 `profiles:` 一行不动；只有 `litellm-anthropic` 这个 **provider** 加一行。能力声明落在 vendor 轴，未来多个 Bedrock-转发 profile 自动受益，无需逐 profile 记忆。

---

## 7. 取舍与未来扩展

- **单布尔 vs 嵌套能力对象**：选单布尔 `supports_automatic_cache`。理由：决策 #9 明确不建 capability matrix；当前只有一个真实方言维度。若日后出现第二个请求体方言（如某 vendor 不支持某 thinking 选项），再在 `ProviderConfig` 平铺加字段即可，不预先抽象。
- **本设计是 provider/vendor 层的首个落点，也是缝**：用户的原始诉求是「在 transport 基础上再构建一层 provider」以容纳同协议不同 vendor 的方言。本次落地的是**请求体能力**维度。另一条维度——**client 构造方言**（直连 AWS Bedrock 需 `AnthropicBedrock` + SigV4 + region + ARN/inference-profile model-id；直连 GCP Vertex 需 `AnthropicVertex` + OAuth + project/location）——同样应挂在 `ProviderConfig` 上（未来加 `vendor`/`flavor` 判别字段，由 transport 的 `_open_client` 与 model-id 解析消费）。transport 协议逻辑（`build_kwargs`/`convert_messages`/`normalize_*`/prompt cache 断点/`classify_error`）在三种 vendor 间完全共享（anthropic SDK 的 `AsyncAnthropic`/`AsyncAnthropicBedrock`/`AsyncAnthropicVertex` 暴露同一 `.messages` 接口与同一返回类型）。本次不实现该维度，仅确认其落点与本能力位同源。

---

## 8. 测试策略

跟随现有测试文化（决策 #12），仅补纯函数单测，不引入运行时/集成测试：

- `build_kwargs` × `emit_top_level_auto=False`：断言 `kwargs` 不含顶层 `cache_control`（无 `extra_body` 或其内无该键），且块级断点仍按 `automatic` 放置（system + 命中的 latest_user/tool_result/flexible 块带 `cache_control`）。
- `build_kwargs` × `emit_top_level_auto=True`：断言顶层 `cache_control` 存在（保护 native 行为不回归）。
- 断点槽：`emit_top_level_auto=True` 时块级目标 ≤ `max_breakpoints-1`；`=False` 时可达 `max_breakpoints`。
- factory 合流：`supports_automatic_cache=False` + `prompt_cache.automatic=True` → 解析出的 options `emit_top_level_auto=False`；`supports_automatic_cache=True` 同条件 → `True`。

---

## 9. 不做

- 不新增协议/transport，不动 kernel 主循环，不改持久化 schema 或 IR 字段。
- 不改任何 `profiles:` 条目，不改 `PromptCacheConfig` schema（`automatic` 仍是唯一面向用户的 prompt-cache 自动化旋钮）。
- 不实现直连 Bedrock/Vertex 的 client 构造方言（仅在 §7 标注落点）。
- 不建能力矩阵/preflight 子系统，不做运行时 capability 探测。
- 不在主代码写「检测到 Bedrock 自动剥字段」之类内联兜底——能力由 config 显式声明。
