# 阶段三 a：中立 IR 与 provider_state 地基（详细设计）

- 日期：2026-06-07
- 状态：brainstorming 逐段确认完成，待落实施计划
- 上游：
  - `docs/superpowers/specs/2026-06-06-provider-aggregation-design.md`（三阶段总方向，第 4 节硬约束 + 第 7 节阶段三蓝图）
  - `docs/superpowers/specs/2026-06-07-provider-aggregation-stage2-design.md`（阶段二聚合核心，建立 `Transport` 基类 / dispatch 表 / `convert_messages` 接缝）
- 范围：把母文档第 7 节的「阶段三」拆为 3a/3b/3c 三个子阶段；本文只定义 **3a**——中立 IR 落地与 provider_state 全链路贯穿，仅在 `chat_completions` 上验证（它不产 provider_state，用于证明通道惰性正确）。

> 本文是阶段三第一子阶段的落地详细设计。母文档第 4 节「已确认决策」仍为硬约束。
> 本次 brainstorming 在母文档基础上把阶段三拆分为 3a/3b/3c（见第 2 节），以本拆分为准。

---

## 1. 前置依赖与现状基线

3a 的地基是**阶段二完成后的状态**，依赖以下阶段二产物（写 3a plan 前这些必须已落地）：

- `matmaster/providers/transport.py`：`Transport` 基类（生命周期 + timeout/retry property + seam 声明）。
- `matmaster/providers/transports/chat_completions.py`：`ChatCompletionsTransport(Transport)`，已有 `build_kwargs` / `convert_messages` / `normalize_response` / `normalize_stream` / `classify_error` 接缝。阶段二的 `convert_messages` 是 **identity 直通**（吃 `list[dict]`）。
- `matmaster/providers/llm_factory.py`：dispatch 表 `_TRANSPORT_BUILDERS`，未命中 fail-fast。
- `matmaster/config/llm.py`：纯数据 profile + `providers:` 段 + `ResolvedModel`。
- provider runtime 主链路中的 bedrock、litellm-Claude、prompt cache、routes 表均已在阶段二删除。

**硬前置验收（review 修订，3a plan 启动前必须确认 stage2 已清干净）**：截至 2026-06-08 当前分支，stage2 聚合核心已经落地，`matmaster/providers/llm_factory.py` 已无 `BedrockProvider` import / `bedrock_converse` 构造分支，`matmaster/devshell/repl.py` 读取 `rr.provider.base_url` 属于新 schema 下合法 provider 连接字段，不是旧 `prof.base_url` 残留。仍需由 stage2 收尾清理的是 evaluation/devshell 的旧 route/fallback 消费者；它们若残留会使 3a 基线不稳定，必须由 stage2 处理完毕、3a plan 在启动门槛上显式核验：

- `evaluation/scripts/devshell/eval_model_routes.py`：默认模型 / fallback 仍引用 `bedrock-claude-opus` 和 `global.anthropic.claude-opus-4-6-v1`。
- `matmaster/devshell/debug_run.py`：默认模型仍引用 Claude route。
- `tests/evaluation/test_devshell_agent_subprocess.py` 与 `tests/matmaster/devshell/test_run_devshell_eval_script.py`：仍显式测 bedrock / Claude fallback argv。
- `evaluation/devshell_agent/*` 与 evaluation docs：如仍描述 Bedrock/botocore fallback，应随 stage2 收尾同步更新或删除相关语义。

3a 不依赖 native transport（那是 3b/3c）。3a 真正改造的核心文件（与阶段二无关、现在就存在）：

| 文件 | 现状角色 | 3a 改造 |
|---|---|---|
| `matmaster/types/messages.py` | `Message.to_api_dict()` 写死 OpenAI；`AssistantMessage` / `LLMResponse` / `StreamChunk` | 移除发送路径 `to_api_dict`；三类内容分离；加 `provider_state` |
| `matmaster/types/llm_provider.py` | `LLMProvider` Protocol，`chat/chat_stream(messages: list[dict])` | 签名改 `list[Message]` |
| `matmaster/types/message_normalization.py` | canonicalize + OpenAI 序列化/校验 + restore | 序列化/校验下沉 transport；canonicalize 留 kernel |
| `matmaster/core/message_pipeline.py` | 增量管线，缓存已校验 OpenAI dict 前缀 | 收窄为只产 canonical `list[Message]` |
| `matmaster/core/agent_llm_stream.py` | `stream_llm_items` 聚合 StreamChunk → LLMResponse | 聚合 `provider_state` |
| `matmaster/core/agent.py` | 由 `LLMResponse` 组装 `AssistantMessage` | 写入 `provider_state` |
| `matmaster/context/compaction.py` | `estimate_tokens` 调 `to_api_dict` 估 token | 改中立序列化 |

---

## 2. 阶段三子阶段切分（3a/3b/3c）

母文档第 7 节的阶段三体量明显大于阶段二（中立 IR + provider_state 贯穿 + 两个 native transport + 手动切协议 + 持久化迁移），拆为三个风险递进、各自独立 spec→plan→实施、各自有绿点的子阶段：

| 子阶段 | 内容 | 是否产真实 provider_state | 关键风险 |
|---|---|---|---|
| **3a（本文）** | 中立 IR 落地、`to_api_dict` 退场、provider_state 全链路通道（字段/聚合/持久化/契约），**仅 chat_completions 验证** | 否（chat_completions 恒 None，证明通道惰性） | 触及 kernel↔transport 边界与持久化 schema |
| **3b** | native `anthropic_messages` transport；sonnet/opus 迁回 `provider: anthropic`；prompt cache 断点策略搬迁（native 注入）；native signed thinking；**真实 provider_state 产出**；手动切协议 tag 丢弃**实际生效**；inline thinking 剥离 | 是 | native SDK 转换、signed block 回放 |
| **3c** | native OpenAI `responses` transport；function_call / function_call_output 转换；encrypted reasoning + response item id 回放 | 是 | Responses item 模型、encrypted reasoning 回放 |

3a 是纯地基：**不引入任何 native transport，不产真实 provider_state，不搬 prompt cache，不做 inline thinking 剥离、fallback、Gemini**。它把字段、聚合、持久化、契约全部建好，使 3b/3c 只需「填真实转换 + 产 state」，不再动 kernel 边界与持久化 schema。

---

## 3. 消息契约重构（kernel↔transport）

### 3.1 决策：`chat/chat_stream` 改收 `list[Message]` 中立 IR

`LLMProvider` Protocol（`llm_provider.py`）签名变更：

```python
async def chat(
    self,
    messages: list[Message],                 # 原 list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None,
    *,
    tool_choice: str | dict | None = None,
) -> LLMResponse: ...

async def chat_stream(
    self,
    messages: list[Message],                 # 原 list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None,
    *,
    timeout: float | None = None,
) -> AsyncIterator[StreamChunk]: ...
```

`Message` 层级（`SystemMessage` / `UserMessage` / `AssistantMessage` / `ToolMessage`）即中立 IR。`tools` 暂不动（仍为 OpenAI function dict；tools 的 IR 化非 3a 范围，3b/3c 各自在 transport 内转 native tool 形状）。

### 3.2 职责重新划分

| 职责 | 现在 | 3a 之后 |
|---|---|---|
| canonicalize（合并连续 `UserMessage`） | `message_normalization.canonicalize_messages_for_provider` / `message_pipeline` | **留 kernel 侧**（协议无关） |
| OpenAI 序列化（`to_api_dict` + content=None→""） | `Message.to_api_dict()` / `message_normalization.normalize_messages_for_openai` | **下沉** `ChatCompletionsTransport.convert_messages(list[Message]) → list[dict]` |
| OpenAI 形状校验（`validate_openai_messages`：role/content shape） | `message_normalization` / `message_pipeline` | **下沉** `ChatCompletionsTransport.convert_messages`（wire 专属） |
| tool-turn 配对校验（tool_call ↔ tool_result 配对） | `validate_openai_tool_turn_sequence`（OpenAI dict 形态） | **留 kernel 侧**，改写为**中立 Message 级**校验 `validate_tool_turn_sequence(list[Message])`（见下） |

划分原则：**协议无关的归 kernel，wire 专属的归 transport**。

- canonicalize 是语义层合并（任何协议都先合并连续用户消息），留 kernel。
- OpenAI 形状的序列化、role/content shape 校验是 chat_completions wire 知识，归该 transport。
- **tool-turn 配对校验是协议无关语义不变量**（每个 tool_result 必须配前一个 assistant 的 tool_call）——review 指出它有非主循环的中立消费者（`history_checkpoint_codec.validate_base_messages` 校验 checkpoint 的 `base_messages` 完整性，见 P1#3）。故不下沉 transport，而是从「读 OpenAI dict 字段」改写为「读 `Message` 字段」（`AssistantMessage.tool_calls[].id` / `ToolMessage.tool_call_id`）的中立校验 `validate_tool_turn_sequence(list[Message])`，留 `message_normalization`，供 kernel、checkpoint、transport 三方调用。chat_completions transport 的 `convert_messages` 在 wire 序列化后**额外**做 OpenAI 形状校验（`validate_openai_messages`），但 tool-turn 配对复用中立校验。

### 3.3 `ChatCompletionsTransport.convert_messages` 在 3a 的实化

阶段二该方法是 identity 直通（吃 dict）。3a 改为吃 `list[Message]`、做真实转换：

```python
def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
    """canonical list[Message] → OpenAI-compatible wire dicts（含 content=None→"" 规范化）。

    序列化逻辑（原 Message.to_api_dict）+ 校验（原 validate_openai_*）下沉于此。
    provider_state 的 tag-丢弃在此执行（见 §4.4）——3a 单 transport 不触发。
    """
    validate_tool_turn_sequence(messages)        # 中立配对校验（留 message_normalization，复用）
    wire = _messages_to_openai_dicts(messages)   # 原 to_api_dict 各分支 + content 规范化
    _validate_openai_messages(wire)              # 原 validate_openai_messages（role/content shape，wire 专属）
    return wire
```

- 原 `Message.to_api_dict()` 的各 role 分支（`SystemMessage`/`UserMessage` 含 image parts/`AssistantMessage` 含 tool_calls/`ToolMessage` 含 tool_call_id）逻辑迁入本文件的 module 级 helper（按 role dispatch）。
- `build_kwargs`（阶段二已存在）内部 `kwargs["messages"] = self.convert_messages(messages)`，其 `messages` 形参类型同步由 `list[dict]` 改 `list[Message]`。
- transport **无状态**：每轮全量序列化+校验（纯 CPU dict 操作、无 I/O）。不接管增量优化，避免把管线状态复杂度压进每个 transport。

### 3.4 `to_api_dict` 退场

- `Message.to_api_dict()` 及子类覆盖**从发送路径彻底移除**，逻辑迁入 `ChatCompletionsTransport`。
- 非发送消费者连带改造：
  - `compaction.estimate_tokens`（`compaction.py:114`）：`json.dumps(msg.to_api_dict())` → 改用中立序列化估 token（直接拼 `content` + `reasoning_content` + tool_calls 文本，或新增协议无关的 `Message.to_size_estimate_text()`）。估算是启发式，无需精确 wire 形状。
  - `message_pipeline._to_normalized_api_dict`（`message_pipeline.py:25`）：随管线收窄删除（见 §5）。
- `message_normalization` 函数去向（review 修订后精确划分）：
  - **迁入** `chat_completions.py`（移动非复制，净代码不增）：`normalize_messages_for_openai` / `validate_openai_messages` / `_validate_user_content` / `_message_to_api_dict`（这些是 OpenAI wire 形状专属）。
  - **留 `message_normalization`、改写为中立 Message 级**：`validate_openai_tool_turn_sequence` → `validate_tool_turn_sequence(list[Message])`（读 Message 字段而非 OpenAI dict），供 kernel / checkpoint / transport 复用。
  - **留 `message_normalization`**：`canonicalize_messages_for_provider` / `_merge_user_messages`（kernel 侧 canonicalize）、`restore_persisted_assistant_state` / `_is_assistant_like_payload`（持久化用，见 §6）。
  - **删除**：`normalize_and_validate_openai_messages`（其「normalize + 形状校验」职责已进 transport.convert_messages，「tool-turn 校验」已拆为中立 `validate_tool_turn_sequence`）。两个原调用方（`history_checkpoint_codec.validate_base_messages`、`compaction` summary call）改造见 §6 / §7。

---

## 4. provider_state 中立 IR

### 4.1 `ProviderState` 模型

`matmaster/types/messages.py` 新增：

```python
class ProviderState(BaseModel):
    """Provider 回放状态：对 kernel 不透明、transport 私有、带 transport tag。

    kernel 原样存取、不解读 payload；只有 tag 匹配的 transport 在 convert 时认领。
    """
    model_config = ConfigDict(frozen=True)

    transport: str              # tag：哪个 transport 拥有它（如 "anthropic_messages"）
    payload: dict[str, Any]     # 对 kernel 不透明的回放数据；**必须是 JSON-compatible 值**
```

**payload 的 JSON 契约（review 修订 P2#5，硬约束）**：`payload` 虽对 kernel 不透明，但**必须只含 JSON-serializable 值**（dict/list/str/int/float/bool/None），不得塞 SDK 对象、`bytes`、response model 实例、回调对象等。原因：事件持久化统一走 `model_dump(mode="json")`（`integration/persistence_handler.py:63`），history checkpoint 也用 `message.model_dump(mode="json")`（`history_checkpoint_codec.py:44`）——非 JSON 值会在持久化层炸。3b/3c 的 transport 在打包 `provider_state` 时**负责把 native SDK 对象降解为 JSON dict**（signed thinking block / encrypted reasoning / item id 都序列化成纯 JSON）。3a 在 `ProviderState` 产出点加 JSON-serializability 测试（用 `model_dump(mode="json")` 不抛 + round-trip），即便 3a 假 transport 才会真正产出。

### 4.2 三类内容分离正式成型

`AssistantMessage` / `LLMResponse` / `StreamChunk` 各加 `provider_state: ProviderState | None = None`：

| 类别 | 字段 | 消费方 | 特性 |
|---|---|---|---|
| 可见内容 | `content` | 用户/前端/session | 已剥离 inline thinking（剥离实现 3b 落地；3a 字段就位） |
| 展示用 reasoning | `reasoning_content` | thought stream/日志/debug | 明文，不回传 API |
| provider 回放状态 | `provider_state` | 仅写它的那个 transport | 不透明、不展示、仅供回放 |

`ToolCallData` **不动**（母文档决策 #8）；tool-call 级回放信息（Responses item id、Anthropic tool_use 与 thinking 关联）归入 `AssistantMessage.provider_state.payload`，3b/3c 各自打包。

### 4.3 流式产出与聚合模型

- **transport 侧**：流内部缓冲协议私有回放信息，**流末发一个聚合 `StreamChunk(provider_state=...)`**（在 content/reasoning/tool 增量之后）。chat_completions 在 3a **永不发**（恒 None）。
- **kernel 侧**（`agent_llm_stream.stream_llm_items`，聚合处 `agent_llm_stream.py:242`）：新增局部变量捕获 `chunk.provider_state`（最后一个非 None 胜），写入组装的 `LLMResponse(... provider_state=captured)`。
- **agent 侧**（`agent.py:400` / `agent.py:409` 两处 `AssistantMessage(...)` 组装）：加 `provider_state=response.provider_state`。

kernel 全程把 `provider_state` 当不透明黑盒搬运，不读 `payload`。

### 4.4 tag 不匹配丢弃契约（3a 定义、不触发）

母文档 §7.3：session 中途手动切模型跨协议时，`convert_messages` 中 transport 只认自己 tag 的 `provider_state`，tag 不符就**丢弃该回放状态、保留 `content` + `tool_calls`**（避免把别家签名/加密块发出去致 400）。

3a 交付：
- 在 `Transport` 基类提供 helper：`_claim_provider_state(msg: AssistantMessage) -> dict | None`——tag 匹配 `self.transport_tag` 返回 `payload`，否则 None。`transport_tag` 为子类声明的常量（chat_completions = `"chat_completions"`）。
- `ChatCompletionsTransport.convert_messages` 调用该 helper：3a 里 chat_completions 不消费 provider_state（OpenAI wire 无回放块），故即便认领也不注入；但 helper + 契约就位，**3b 引入第二个 transport 后 tag 丢弃才真正生效**。
- 3a 只有一个 transport、无跨协议切换，故契约为惰性；用纯函数单测验证 helper 的 tag 匹配/丢弃逻辑（见 §8）。

---

## 5. message_pipeline 收窄

`message_pipeline.py` 现状：增量缓存已校验的 OpenAI dict 前缀（含 `_ToolTurnValidator` 状态机），避免每轮全量 canonicalize + normalize + validate。

3a 收窄：序列化与 OpenAI 校验已下沉 transport（§3.3），管线**只负责产 canonical `list[Message]`**——即增量维护「合并连续 `UserMessage` 后的 Message 前缀」，输出 `list[Message]` 交给 `chat_stream`。

- 删除：`_to_normalized_api_dict`、`_ToolTurnValidator`、dict 化与 OpenAI 校验相关机制（这些是 wire 专属，已迁 transport）。
- 保留：canonical Message 前缀缓存（合并是协议无关、便宜的语义操作；缓存避免长历史每轮重合并）。
- 若收窄后管线复杂度不再值当（merge 本身极廉价），实施计划可进一步将其塌缩为「每轮直接调 `canonicalize_messages_for_provider`」并删除模块（净代码下降）；该取舍留 plan 依实测决定，本设计只锁定「管线输出 canonical `list[Message]`、不再产 dict / 不再做 OpenAI 校验」。

---

## 6. 持久化 / resume 贯通（review 修订：真实链路而非理想链路）

> 初版只看了 `AssistantMessage.model_dump/model_validate` 这条理想路径，**低估了真实持久化/恢复链路**。review（P1#1/P1#2/P1#3）指出三个会导致普通文本回复的 `provider_state` 实际丢失的断点。3a 必须把这三处纳入改造，否则 3b/3c 产出的 signed/encrypted state 在重启/跨 worker resume 后会丢。

### 6.1 自动贯通的部分

- `AssistantStateEvent.state`（`events.py:147`）= `AssistantMessage.model_dump(mode="json")`（`agent.py:433`）。`AssistantMessage` 加 `provider_state` 后 dump 自动含它。
- `assistant_state` 已是 **internal-only**、对 SSE/前端隐藏（`integration/sse_handler.py:127`、`src/services/stream_sse_filter.py`），`provider_state` 天然不外泄。
- `history_checkpoint_codec.serialize/deserialize_base_messages`（`history_checkpoint_codec.py:44/64`）走 `model_dump(mode="json")` / `model_cls.model_validate(raw)`——checkpoint 内的 `base_messages` 自动带上 `provider_state`。

### 6.2 必改点一：assistant_state 发射缺口（P1#1）

**问题**：`AssistantStateEvent` 当前**只在 tool-call 分支发**（`agent.py:431`，`if assistant_msg.tool_calls:`）。自然完成分支（`agent.py:400`）只把 `AssistantMessage` append 到内存并 terminal 返回，**不发 assistant_state**；terminal 持久化只投影 final content/status/usage、不保留 `messages`（`integration/event_payloads.py:320`）。于是普通文本回复（无 tool_calls）的 `provider_state` 同进程下一轮还在内存里，但重启/恢复/跨 worker resume 后丢失。

**3a 改造**：自然完成分支（`agent.py:400` 那条 append `AssistantMessage` 的合法自然完成路径）在 `response.provider_state is not None` 时，也发一个 internal-only `AssistantStateEvent`（携带该 `AssistantMessage.model_dump(mode="json")`）。

- 选**条件发射**（仅 `provider_state` 非 None 时）而非无条件发射：chat_completions 在 3a 恒不产 provider_state，故 3a 自然完成分支**行为等价**（不新增事件量）；3b/3c 一旦给文本回复产 state，持久化自动跟上。这是 3a 唯一新增的 kernel 发射点。
- **不会造成 resume 重复**（已验证）：`events_to_dialog_messages` 遇到 `assistant_state` 时（`chat_history.py:543-548`）会 pop 掉同回合前面那条 `response` 文本消息、替换为 assistant_state 还原的消息（走 `restore_persisted_assistant_state` → `model_validate`，再 `model_dump()`）。故同回合「response 出内容 + assistant_state 出 provider_state（无 tool_calls）」被合并为**一条** assistant 消息，`provider_state` 在这一层自动保留。plan 需验证无 tool_calls 时该 pop-替换路径确实命中（`last_assistant_text_idx` 邻接）。
- 用假 transport（对自然完成产 provider_state）测试：自然完成 → assistant_state 发射 → 合并 → 持久化 → resume 还原 `provider_state`。

### 6.3 必改点二：tail restore 的 dict→Message 重建丢弃 provider_state（P1#2）

**问题精确定位**：恢复链路两层——
1. `events_to_dialog_messages`（事件→扁平 dict）：如上，`assistant_state` 经 `model_dump()`（`chat_history.py:548`）**已含 `provider_state`**。
2. `events_to_messages`（扁平 dict→`Message`，`chat_history.py:667-681`，由 `model_history_restore_service.py:139` 调用）：assistant 分支按 role 手写重建 `AssistantMessage`，**只拷 `content` / `reasoning_content` / `tool_calls`，丢 `provider_state`**。

即第一层保留、第二层丢弃。checkpoint 之后的 assistant_state tail 因此在 resume 时丢状态。

**3a 改造**：`events_to_messages` 的 assistant 分支携带 `provider_state`——优先改为直接 `AssistantMessage.model_validate(d)`（扁平 dict 字段已齐备，最省、最不易漏字段），或至少显式拷 `provider_state`。

- 测试：构造「checkpoint + 之后带 provider_state 的 assistant_state tail」恢复，断言还原后的 `AssistantMessage.provider_state` 一致（覆盖**无 tool_calls** 的情形）。

### 6.4 必改点三：checkpoint validation 退到中立校验（P1#3 持久化侧）

**问题**：`history_checkpoint_codec.validate_base_messages`（`history_checkpoint_codec.py:73`）直接 import 并调 `normalize_and_validate_openai_messages` 校验 base_messages 的 tool-turn 完整性。该函数 3a 要删（§3.4）。

**3a 改造**：`validate_base_messages` 改调中立 `validate_tool_turn_sequence(list[Message])`（§3.2 拆出的中立校验），不再依赖 OpenAI wire 序列化。语义等价（tool_call↔tool_result 配对），且符合「checkpoint 校验的是消息序列完整性，不该绑死 OpenAI wire」。

### 6.5 其它

- `_is_assistant_like_payload`（`message_normalization.py:48`）：判定键集合保持兼容（`provider_state` 可选，不破坏现有判定）。
- `compaction`：被摘要压缩掉的消息，其 `provider_state` 随消息一并丢弃（合理——回放信息绑定具体消息）。摘要产出的合成 `AssistantMessage` 无 `provider_state`。
- `src/services/chat_history.py` 的 `_assistant_reasoning_content` 等展示字段提取不受影响（只新增 `provider_state`，不改 `content`/`reasoning_content`）。
- 3a 不引入持久化迁移脚本（`provider_state` 为可选新增字段，旧记录缺它时 `model_validate` 默认 None——新增可选字段的自然 forward 读取，非主代码兼容兜底）。

---

## 7. LLMProvider 装饰器与集成点

- `BillingLLMProvider` / `UsageCollectingProvider`：`__getattr__` 透传，对 `provider_state` 与签名变更天然无感（母文档决策 #10）；只需确认 `chat/chat_stream` 透传的形参类型注解同步（如显式声明则改 `list[Message]`）。
- `exp.py` 装配、BYOK（`build_byok_provider_bundle`）：不改（仍 `transport=chat_completions`）。
- **`stream_llm_items` / `call_llm_streaming`（review 修订 P2#4）**：重试/stream 聚合**控制流不改**，但参数契约改：`api_messages: list[dict[str, Any]]` → `canonical_messages: list[Message]`（`agent_llm_stream.py:73/254`），直接把 `list[Message]` 传给 `provider.chat_stream`。调用侧 `agent.py:328`（`state.pipeline.feed_tail()` 返回值原命名 `api_messages`）随之改名为 `canonical_messages`；相关测试桩同步改传 `list[Message]`。
- **compaction summary call（review 修订 P1#3）**：`compaction.py:340` 当前 `normalize_and_validate_openai_messages(canonicalize_messages_for_provider(summary_messages))` 产 dict 再传 `llm_provider.chat`。改为：只做 `canonicalize_messages_for_provider(summary_messages)` 得 `list[Message]` 直接交 `chat`，wire 序列化+校验交 transport.convert_messages（与主路径一致）。确认 compaction 内部构造的 `summary_messages` 是 `Message` 实例。

---

## 8. 测试策略（跟随现有测试文化）

- **`convert_messages` 等价性**（chat_completions）：canonical `list[Message]` → OpenAI dict，断言与原 `to_api_dict` + `normalize_messages_for_openai` 输出逐字段等价（迁移/改造 `test_message_normalization*` 相关断言到 transport 测试）；含 image parts、tool_calls、tool_call_id、content=None→"" 各分支。
- **OpenAI 形状校验下沉**：`validate_openai_messages` 的报错用例（非法 role、user content shape）迁到对 `convert_messages` 的断言。
- **中立 tool-turn 校验**：`validate_tool_turn_sequence(list[Message])` 纯函数单测（orphan tool、duplicate tool_call id、missing tool_result 等 fail-fast），覆盖 kernel/checkpoint/transport 三方复用同一份。
- **provider_state 通道（核心新增）**：用**假 transport**（测试桩）在流末产 `StreamChunk(provider_state=ProviderState(transport="fake", payload={...}))` → `stream_llm_items` 聚合 → `LLMResponse.provider_state` → `AssistantMessage.provider_state` → `model_dump()`/`restore_persisted_assistant_state` round-trip 一致；断言 kernel 全程不读 payload。
- **持久化真实链路（review 修订）**：
  - **无 tool_calls 的 provider_state 持久化**（P1#1）：假 transport 对自然完成产 provider_state → 断言自然完成分支发 internal-only assistant_state → 持久化 → resume 还原。
  - **tail restore 携带 provider_state**（P1#2）：构造「checkpoint + 之后带 provider_state 的 assistant_state tail」→ `events_to_messages` 还原 → 断言 `AssistantMessage.provider_state` 一致（无 tool_calls 情形）。
  - **checkpoint 中立校验**（P1#3）：`validate_base_messages` 改用 `validate_tool_turn_sequence` 后，非法/合法 base_messages 回归。
- **payload JSON 契约（P2#5）**：`ProviderState` 产出点用 `model_dump(mode="json")` 不抛 + round-trip；含非 JSON 值时（如测试故意塞对象）能被产出方测试捕获。
- **chat_completions 惰性回归**：chat_completions 流不产 provider_state，`LLMResponse.provider_state is None`、`AssistantMessage.provider_state is None`；自然完成分支不新增 assistant_state 事件（行为等价）。
- **tag 丢弃 helper 纯函数单测**：`_claim_provider_state` 在 tag 匹配/不匹配时分别返回 payload / None（即便 3a 不触发注入）。
- **estimate_tokens**：改中立序列化后 token 估算回归（量级稳定即可，非逐字节）。
- **签名变更连带**：现有传 `list[dict]` 给 `chat/chat_stream` / `stream_llm_items` / `call_llm_streaming` 的测试桩/集成测试改传 `list[Message]`。

---

## 9. 3a 明确不做

- 不引入 native `anthropic_messages` / `responses` transport（3b/3c）。
- 不产真实 provider_state（chat_completions 恒 None）。
- 不搬 prompt cache 断点策略、不做 native signed thinking / encrypted reasoning（3b/3c）。
- 不做 inline thinking 剥离（3b；3a 只立 `content` 已剥离的字段语义）。
- 不做 automatic fallback、不做手动切协议的**实际**丢弃触发（契约+helper 就位，3b 生效）。
- 不做 Gemini native。
- 不引入持久化迁移脚本（`provider_state` 为可选新增字段）。
- 不改 kernel 主循环**控制流**（改动收敛在：`messages.py` IR / `message_normalization.py` 校验拆分 / `message_pipeline.py` 收窄 / `agent_llm_stream.py` 聚合点 + 参数契约 / `agent.py` 组装点 + 自然完成分支条件发射 / transport 边界 / 持久化恢复点 `chat_history.events_to_messages`、`history_checkpoint_codec.validate_base_messages`、`compaction` summary call）。自然完成分支的 assistant_state 条件发射是新增一条 internal-only 发射、非控制流重构。

---

## 10. 完成标准

- `LLMProvider.chat/chat_stream`（及 `stream_llm_items` / `call_llm_streaming` 参数契约）收 `list[Message]`；`to_api_dict` 从发送路径移除，OpenAI 序列化 + role/content 校验下沉 `ChatCompletionsTransport.convert_messages`。
- `Message.to_api_dict()` 删除；`compaction.estimate_tokens` 改中立序列化；`compaction` summary call 改传 `list[Message]`；`message_normalization` 中 wire 专属函数迁入 chat_completions transport，tool-turn 校验改写为中立 `validate_tool_turn_sequence(list[Message])`（净代码不增）。
- `message_pipeline` 收窄为产 canonical `list[Message]`，不再产 dict / 不再做 OpenAI 校验。
- `ProviderState` 模型落地（payload JSON 契约 + 测试）；`AssistantMessage` / `LLMResponse` / `StreamChunk` 三类内容分离（`content` / `reasoning_content` / `provider_state`）。
- provider_state 全链路通道贯通：transport 流末产出 → `stream_llm_items` 聚合 → `LLMResponse` → `AssistantMessage` → 持久化 → resume round-trip，kernel 全程不透明搬运。
- **持久化真实链路补齐**（review）：自然完成分支在 `provider_state` 非 None 时发 internal-only assistant_state；`events_to_messages` tail restore 携带 `provider_state`；`history_checkpoint_codec.validate_base_messages` 改中立校验。无 tool_calls 的 provider_state 持久化/resume 有测试覆盖。
- tag 丢弃 helper + 契约就位（3a 惰性、单 transport 不触发）。
- chat_completions 路径行为等价（恒不产 provider_state、不新增 assistant_state 事件）；现有 openai 风格 profile + BYOK 行为等价。
- `convert_messages` / 中立 tool-turn 校验 / provider_state 聚合 / tag helper / estimate_tokens / 持久化真实链路各有独立测试。

---

## 11. 3a→3b/3c 衔接（本子阶段搭好的接缝）

- **`convert_messages(list[Message])` 接缝已实化**：3b/3c 各写 native transport 的 `convert_messages`，把 `list[Message]`（含 `AssistantMessage.provider_state`）转 native wire（message/tool_use/tool_result/input item），并在此**认领自己 tag 的 provider_state、丢弃他家**。
- **provider_state 通道已贯通**：3b/3c 只需在各自 `normalize_stream` 流末 `StreamChunk(provider_state=ProviderState(transport=自身tag, payload=...))`，聚合/持久化/resume 全程无需再动。
- **tag 丢弃契约已就位**：3b 引入第二个 transport（anthropic_messages）后，手动切协议时 `_claim_provider_state` 的 tag 不匹配丢弃**自动生效**，无需再改 kernel。
- **三类内容字段已就位**：3b 的 inline thinking 剥离只需填 `content` 剥离逻辑（复用 `response_text.py`）+ 把 signed thinking block 装进 `provider_state.payload`，字段无需再加。
- **持久化 schema 已稳**：3b/3c 产真实 provider_state 后，`assistant_state` 持久化/resume 无 schema 变更（payload 是不透明 dict，任何 **JSON-compatible** native 结构都装得下；3b/3c transport 负责把 native SDK 对象降解为 JSON——见 §4.1 契约）。
- **发射/恢复缺口已补**：3a 已补自然完成分支的 assistant_state 发射、tail restore 携带 provider_state、checkpoint 中立校验（§6），3b/3c 给文本回复产 state 时持久化/resume 自动跟上、无需再动持久化链路。
