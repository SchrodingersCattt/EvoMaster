# 工具结果图片通路（agent 看图能力）设计

- 日期：2026-06-10（rev2：按代码级评审重写生命周期为四道防线，补恢复层剥图与 PNG 尺寸校验，格式集合收缩为 Anthropic∩qwen 交集）
- 状态：已评审（设计阶段）
- 范围标签：types / core / tools / providers / context / src.services

## 1. 背景与问题

当前图片通路是单向的：用户上传图片经 `TurnInput.attachments.images` → `UserMessage.images` → 两个 transport 序列化，链路完整。但工具方向完全没有通路：

- `ToolResult.content` 是纯字符串（`matmaster/tools/tool_result.py`）；
- `ToolMessage` 没有 images 字段（`matmaster/types/messages.py`）;
- dispatch 回填只取字符串（`matmaster/core/agent_tool_dispatch.py`）。

后果：用户在对话中让 agent 查看 session 文件系统中的一张图片时，agent 没有任何办法把图片字节送进模型上下文。`ReadTool` 遇到图片文件走文本解码直接报错。

配置层的 `supports_vision` / `vision_detail`（`matmaster/config/llm.py`）目前只在**用户上传方向**被消费（`src/services/image_input_service.py` 的 `ensure_vision_supported` / `resolve_image_detail`）；工具方向没有任何消费点。用户上传方向还有既存的在途图片约束（`trim_history_images`：默认近 3 轮、最多 10 张，超出替换占位文本）——工具方向的新通路必须对齐这些既有不变量，而不是绕开它们。

## 2. 已确认决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 图片来源范围 | 仅 session 文件系统（local/ssh，经 `session.download`） | 核心场景最小闭环；公网 URL 图片、其他工具回图按需后续扩展 |
| 传输形式 | base64 data URI 直接进消息历史 | 两个协议、所有 vendor 方言（含 Bedrock）都支持；Bedrock 不支持 url source，URL 方案在该路径不可用；单一路径，不做 vendor 分流 |
| 工具入口 | 扩展 `ReadTool`，不新建工具 | 与 Claude Code 的 Read 行为一致（项目工具集本就对齐 CC）；模型心智自然，无需先判断文件类型再选工具 |
| kernel 机制 | 方案 A：ToolResult/ToolMessage 一等公民图片通路 | 图片归属于产生它的工具调用，持久化/重放/事件流语义一致；未来任何工具自动受益。否决方案 B（dispatch 注入合成 UserMessage）：历史中出现假 user 消息，归属关系丢失 |
| 格式集合 | PNG / JPEG / WEBP（无 GIF） | Anthropic 与 qwen-VL 的交集（qwen-VL 官方支持 jpeg/png/webp，无 gif）；与 AttachFigure 既有集合一致；科研绘图无 GIF 场景 |
| 图片进入历史后的约束 | 四道防线分层（§9）：工具层入口门控 → kernel 请求前在途预算 → compaction 估算与摘要剥图 → 恢复层 vision 剥图 | 带图历史一旦失控会让后续每个请求都失败且无法自愈（请求体 32MB 上限、非视觉模型重放），必须有显式约束与退出机制，不能依赖「概率极低」 |

## 3. 外部 API 事实（设计依据）

- Anthropic Messages API 原生支持 tool_result 的 `content` 为 block 数组，数组元素可为 `text` / `image` / `document`（官方 handle-tool-calls 文档含 image 示例）。
- Bedrock 与 Vertex 方言仅支持 base64 source，不支持 url source（官方 vision 文档）。
- Anthropic 系图片限制：第一方 API 单图 10MB（base64 后）、Bedrock/Vertex 5MB（base64 后）、尺寸上限 8000×8000px、请求体总限 32MB。
- OpenAI chat completions 协议的 `role: tool` 消息不能携带图片；user 消息的 `image_url` part 接受公网 URL 与 data URI。deepseek 当前无视觉模型。
- qwen-VL（DashScope OpenAI 兼容端点）：`image_url` 支持 data URI；官方支持格式 jpeg/png/webp（**无 gif**）；未公布权威的单图字节上限，主约束为图文总 token（Qwen2.5-VL 标注 480–2560px 为定位鲁棒区，仅为效果说明非硬限制）。本设计的 3 MiB 单图 + 16 MiB 在途预算相对该量级有充分余量。
- 会话中途切换模型是产品支持的操作（`src/apis/chat_api.py` 的每请求 `model_override`），任何「某 vendor 永远收不到某类消息」的论断都必须考虑历史重放。

## 4. 架构总览

```
ReadTool（图片分支：门控 + 校验）
   │ ToolResult(content=文本说明, images=[ImageContentPart(data URI)])
   ▼
dispatch_tool_calls
   ├─▶ ToolMessage(content, images)        # 进 state.messages
   └─▶ ToolResultEvent(..., images)        # 进 SSE + DB 事件流（checkpoint 同源）
            │
            └─（每个 user turn）restore：checkpoint/事件 → ToolMessage(images)
                                └─ vision 剥图（supports_vision=False 时全剥，§9 防线 4）
   ▼
kernel 请求前整形（feed_tail 管线）
   └─ apply_tool_image_budget：最近 4 张 / 16 MiB，超出剥离换占位（§9 防线 2）
   ▼
transport 序列化
   ├─ AnthropicMessagesTransport：tool_result block 的 content 变 [text, image...] 数组
   └─ ChatCompletionsTransport：tool 消息保持纯文本 + 组后插入带 image_url 的 user wire dict（relay）
```

关键约束一：会话历史在每个 user turn 都重建——优先从 history checkpoint 反序列化（`src/services/model_history_restore_service.py`，codec 为泛化 `model_dump`/`model_validate`，images 字段自动随行），事件流补尾部/兜底（`src/services/chat_history.py`）。因此图片必须随 `ToolResultEvent` 持久化，否则下一轮即丢。

关键约束二：图片进入历史之后必须有显式的约束与退出机制（§9 的四道防线）。入口校验只能挡新图，挡不住已持久化历史在体积、token、模型能力三个维度上的失配。

## 5. 数据模型与 kernel 通路

复用现有 `ImageContentPart`：`url` 承载 data URI，`mime_type` 填 magic bytes 判定出的实际格式，`detail` 填 ReadTool 注入的 profile `vision_detail`（为 None 时序列化不发该字段，与现有 `_user_message_to_dict` 行为一致）。

1. `ToolResult` 增加 `images: list[ImageContentPart] = Field(default_factory=list)`（`matmaster/tools/tool_result.py`）。
2. `ToolMessage` 增加同名字段（`matmaster/types/messages.py`）。
3. dispatch 构造 `ToolMessage` 时透传 `images=tool_result.images`（`matmaster/core/agent_tool_dispatch.py`）。`tool_runner` 的错误包装与截断只作用于 `content`（`matmaster/core/tool_runner.py` 的 `_truncate_result`），不影响 images。
4. `ToolResultEvent` 增加 `images` 字段（`matmaster/types/events.py`）；它同时是 SSE 推送物与 DB 持久化源。
5. 事件恢复路径共三个位点（`src/services/chat_history.py`）：`_tool_result_from_event` 的提取（现返回 `(call_id, name, content)` 三元组，需带出 images）、`events_to_dialog_messages` 的 tool_result 分支构造、`events_to_messages` 的 role=tool 分支构造。`_repair_incomplete_tool_turns` 合成的错误 ToolMessage 无 images，无需改动。
6. checkpoint 恢复路径零代码改动：`src/services/history_checkpoint_codec.py` 为泛化 `model_dump(mode="json")` / 按 role 分发 `model_validate`，images 自动往返。体积影响见 §9。
7. SSE 原样转发 data URI，前端可直接 `<img src>` 渲染；体积由单图上限约束（见 §6）。

## 6. ReadTool 图片分支与 vision 门控

判定时机与方式：`session.download` 拿到 raw bytes 之后，以 **magic bytes 主判定**，扩展名不参与图片分支判定。支持集合为 Anthropic 与 qwen-VL 的交集，与 AttachFigure 既有集合一致：

| 格式 | magic | media_type | 尺寸校验 |
|---|---|---|---|
| PNG | `\x89PNG` | image/png | 是（IHDR 固定偏移 bytes 16–24，约 10 行纯 Python） |
| JPEG | `\xFF\xD8\xFF` | image/jpeg | 否（见下） |
| WEBP | `RIFF....WEBP` | image/webp | 否（见下） |

图片分支流程：

```
（预检）stat_file.size > 3 MiB 且扩展名 ∈ {png,jpg,jpeg,webp}
                            ⇒ ToolResult(error, 报大小与上限)，不下载
download → magic 命中
  → vision_enabled 为 False ⇒ ToolResult(error, "当前模型 profile 不支持图像输入")
  → 原始字节 > 3 MiB        ⇒ ToolResult(error, "图片 X MB 超过 3 MiB 上限，可用 Bash 压缩后重试")
  → PNG 且任一边 > 8000px   ⇒ ToolResult(error, "图片 WxH 超过 8000px 上限，可用 Bash 缩放后重试")
  → base64 → data URI
  → ToolResult(
        content="Read image: <path> (<FMT>, <size>[, WxH 仅 PNG])",
        images=[ImageContentPart(url=data_uri, mime_type=..., detail=vision_detail)])
```

- **单图上限 3 MiB 原始字节**（base64 后约 4MB）：对 Bedrock 单图 5MB（base64 后）留余量，同时控制 DB 事件行与 SSE 帧体积。超限报错，不做自动缩放（与 AttachFigure 超 10MB 报错先例一致；agent 可用 Bash 自行压缩）。
- **PNG 必做尺寸校验**：科研绘图正是大面积纯色 PNG，上万像素宽的 matplotlib 长时序图可以压在 1MB 以内——不校验会让超尺寸图先持久化进历史、再在 provider 层报错，形成历史投毒（§9 防线 2 的预算剥的是旧图，毒图可能恰是最新一张，预算救不了）。PNG 宽高在 IHDR 固定偏移，纯 Python 即可读出。JPEG/WEBP 不校验：其压缩特性下「超 8000px 且 <3 MiB」的图在科研场景无实际来源，残余风险写入 §10。
- 预检用已有的 `session.stat_file` 先行（扩展名仅用于这一步的短路拒绝，不参与分支判定），避免先整体下载超大文件再拒绝。
- `offset` / `limit` / `encoding` 参数在图片分支忽略。
- 图片分支不写 `snapshot_seed`，也不标记 `mark_read`（两者均服务于 Edit 前置检查，图片不会被 Edit）。
- **vision 门控与 detail**：`ReadTool.__init__` 新增 `vision_enabled: bool = False` 与 `vision_detail: Literal["low","high","auto"] | None = None` 构造参数，在 `matmaster/core/exp.py` 工具构造处从 resolved LLM profile（`request.llm_model_profile` 可达）取 `supports_vision` / `vision_detail` 注入（与 session 注入同模式）。每个 user turn 重建工具，模型切换后门控自动刷新。这与用户上传方向消费同一对配置（`ensure_vision_supported` / `resolve_image_detail` 的既有模式），两个方向行为对齐。
- 非图片 magic 的文件走现有文本路径，行为不变。GIF 不在集合内（qwen-VL 不支持），按现状走文本解码报错。

## 7. Anthropic transport 序列化

唯一改动点 `_tool_result_block`（`matmaster/providers/transports/anthropic_messages.py`）：

```python
# 现状
{"type": "tool_result", "tool_use_id": ..., "content": message.content or ""}

# message.images 非空时
{"type": "tool_result", "tool_use_id": ...,
 "content": ([{"type": "text", "text": message.content}] if message.content else [])
            + [_image_block(img) for img in message.images]}
```

- `_image_block` 零改动复用（已支持 data URI → base64 source）。
- `BedrockAnthropicTransport` 继承同一序列化；base64 source 在 Bedrock 可用。
- prompt cache 兼容：`_mark_content_block` / `_message_text_size` 已处理 list 型 content（图片块对 flexible 断点的 size 统计贡献 0，不会被 base64 撑爆）；data URI 逐字节稳定，可参与缓存前缀，多轮重发成本由 cache read 摊平。

## 8. ChatCompletions transport（relay 模式）

`convert_messages`（`matmaster/providers/transports/chat_completions.py`）遍历时收集**连续 ToolMessage 组**中的带图消息；组结束（遇到非 ToolMessage 或列表尾）且有图片时，紧随其后插入一条 user wire dict，组内按消息归属交错排列 text/image parts：

```python
{"role": "user", "content": [
    {"type": "text", "text": "[Images from Read (tool_call toolu_xxx)]"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,...", "detail": ...}},
    {"type": "text", "text": "[Images from Read (tool_call toolu_yyy)]"},
    {"type": "image_url", "image_url": {"url": "data:image/webp;base64,..."}},
]}
```

- 归属标注用 `tool_name` + `tool_call_id`（均在 ToolMessage 上），同组多图时模型可对应到具体调用；tool 消息 content 中的 "Read image: <path>..." 提供路径侧对应。
- tool 消息本身保持纯文本（原 content）。
- 插入仅发生在 wire dict 层；kernel `Message` 列表不变，`validate_tool_turn_sequence` 不受影响；OpenAI 协议对 tool 消息组连续性的要求满足（插在整组之后）。
- qwen/deepseek 子类（reasoning replay、preserve_thinking）只动 assistant 字段与请求体字段，与 relay 正交。
- relay 实现位于基类，所有 OpenAI 风格 vendor 共享。**非视觉模型收到带图历史的防护不在这里**——transport 不做能力策略；由 §9 防线 4 在恢复层保证带图消息不会到达 `supports_vision=False` 的会话历史。

## 9. 生命周期：图片进入历史后的四道防线

入口校验只能挡新图。图片一旦进入事件流/checkpoint，就会在每个后续请求中重放；没有显式约束时，单次失配会演变为「后续每个请求都失败且无法自愈」（体积超 32MB 请求上限、token 估算失真、非视觉模型收图）。四道防线各管一段：

### 防线 1（入口）：工具层门控与校验

见 §6：`vision_enabled` 门控、3 MiB 单图上限、PNG 尺寸校验、stat 预检。挡住新图的产生。

### 防线 2（每次请求前）：kernel 在途图片预算

**不变量：任何发往 provider 的消息列表中，ToolMessage 携带的图片合计 ≤ 4 张且 base64 总字节 ≤ 16 MiB（先到为准，从最新往旧保留）。**

- 挂点：kernel 请求前整形管线（`feed_tail` 所经的 canonical 化，具体挂点由实施计划定位）。选这里而非恢复层：单个 run 内 agent 可多轮工具调用连续读图（dispatch 直接 append `state.messages`，不经恢复层），恢复层预算管不住 run 内累积；feed_tail 是每次 LLM 调用前的必经点，run 内与跨 turn 两种来源都覆盖。
- 纯视图层策略：`state.messages` 与事件流不动；超预算旧图以 `model_copy` 剥离 images 并在 content 追加占位行 `[image pruned from context: re-Read the file if needed]`。文件仍在 session 中，agent 需要时可重新 Read（窗口随之滑动）。
- 预算依据：请求体总限 32MB；16 MiB 图片 + compaction 约束下的文本历史（≪ 1MB）留出一倍余量；4 张远低于 Anthropic 单请求 100 张上限与 qwen 图文 token 上限的折算张数。
- 与用户上传方向的 `trim_history_images`（近 3 轮/10 张，OSS URL 轻量）互不干涉，各管一个方向。

### 防线 3（compaction）：token 估算与摘要剥图

两个现状缺口，分别修正：

- **估算盲区**：`_message_size_text`（`matmaster/context/compaction.py`）只统计 content/reasoning/tool_calls，对 images 全盲——带图历史在 `estimate_tokens` 眼里只有几千 token，压缩永远不触发。修正：估算对每张图累加常数 2000 token（Anthropic 满幅约 1600，取保守值；含 safety_margin 乘数路径）。
- **摘要请求剥图必须无条件、且先于 budget early-return**：现有流程中 `prepared_tokens <= message_budget` 会提前返回原始消息（含图直接进摘要请求）；按长度选材的 `_truncate_tool_message_for_summary`（候选条件 content ≥ 500 字符）永远选不中 content 只有一行的图片消息。修正：摘要输入准备的最前置步骤，对所有 `images` 非空的 ToolMessage 执行 `model_copy(update={"images": []})` 并在 content 追加 `[images omitted for summary: N]`——与按长度截断完全解耦，不挂在选材逻辑上。
- 保留尾部未压缩的近期 ToolMessage 原样保留 images（其在途体积由防线 2 约束）。

### 防线 4（恢复层）：按目标模型剥图

门控（防线 1）只阻止新图产生，挡不住历史重放：先用视觉模型（claude/qwen）读图，图片已持久化；中途切到 `supports_vision=False` 的模型（如 deepseek，`model_override` 是产品支持的操作），历史重建出带图 ToolMessage，relay 会把 `image_url` 发给无视觉能力的 API。

- 挂点：`model_history_restore_service`（已是 `trim_history_images` 的调用处，service 层同时持有 resolved profile 与完整消息列表）。恢复整形扩展为感知 `supports_vision`：False 时统一剥离 **UserMessage 与 ToolMessage 双方向**的全部图片，替换占位文本 `[image removed: current model does not support vision]`。
- 这同时修复用户上传方向的同款既有缺口（`trim_history_images` 目前不看目标模型）。
- ToolMessage 的 data URI 不得走 `validate_history_image_url`（其要求 https，会把所有 data URI 判为非法）；工具方向按字段存在性处理。
- run 内无需此防线：run 内新图已被防线 1 挡住（`vision_enabled` 随每 turn 工具重建刷新）。

### 持久化体积

- 事件行：单图 ≤ 3 MiB 原始字节（base64 后 ≤ 4 MiB），单次 Read 最多一张图。
- checkpoint 行：codec 泛化序列化会内嵌「保留尾部」中全部带图 ToolMessage 的 base64，上界为防线 2 预算（16 MiB）量级。实施时确认事件表列类型与 MySQL `max_allowed_packet` 容纳该量级。

## 10. 错误矩阵

全部复用现有 `ToolResult(status="error")` 通路，无新错误机制：

| 情形 | 行为 |
|---|---|
| magic 非已知图片格式（含 GIF） | 走现有文本解码路径，行为不变 |
| `supports_vision=False` 时读图 | error：明确告知当前模型不支持图像输入 |
| stat 预检或下载后字节 > 3 MiB | error：报实际大小与上限，提示用 Bash 压缩 |
| PNG 任一边 > 8000px | error：工具层报错，**先于持久化**，不投毒历史 |
| JPEG/WEBP 超 8000px 且 < 3 MiB | 残余风险（接受）：工具层不校验，provider 层报错。科研场景无此类图的实际来源；若发生，恢复手段是读入新图使其滑出防线 2 预算窗口，或开新会话 |
| `session.download` 失败 | 现有错误路径不变 |

## 11. 测试计划

- types：`ToolResult` / `ToolMessage` / `ToolResultEvent` 带 images 的 model_dump/model_validate 往返。
- checkpoint codec：带 images 的 `serialize_base_messages` / `deserialize_base_messages` 往返。
- ReadTool：PNG/JPEG fixture 走图片分支；3 MiB 超限报错（stat 预检与下载后两条路径）；PNG 超 8000px 报错（构造 IHDR fixture）；`vision_enabled=False` 报错；扩展名与 magic 不一致时以 magic 为准；GIF 走文本路径；非图片文件文本路径回归。
- AnthropicMessagesTransport：带图 `ToolMessage` 的 tool_result block 数组形状；无图保持字符串现状。
- ChatCompletionsTransport：relay 插入位置与归属标注（单 tool、同轮多 tool 部分带图、连续两轮 tool）；tool 消息保持纯文本。
- 事件恢复：tool_result 事件 → `ToolMessage.images` 往返（覆盖 `_tool_result_from_event` 提取与两处构造）。
- 防线 2：4 张 / 16 MiB 边界、从新往旧保留顺序、剥离消息的占位行。
- 防线 3：短 content（< 500 字符）带图 ToolMessage 在摘要准备中被无条件剥图，**覆盖 budget 内 early-return 路径**；`estimate_tokens` 图片常数生效（带图历史可触发压缩阈值）。
- 防线 4：带图历史（User + Tool 双方向）以 `supports_vision=False` profile 恢复后全部无图且含占位文本；`supports_vision=True` 时 ToolMessage 图片不被 `validate_history_image_url` 误剥。

## 12. 非目标

- 公网 URL 图片读取（按需后续扩展）。
- 图片自动缩放/重编码。
- GIF 支持（qwen-VL 不支持，科研场景无来源）。
- JPEG/WEBP 像素尺寸解析（PNG 解析在目标内，见 §6/§10）。
- OSS URL 传输形式与 vendor 分流（Bedrock 不支持 url source，且违背单一路径原则）。
- 用户上传方向的入口行为变更（恢复层 vision 剥图属双方向修复，见 §9 防线 4；入口校验 `ensure_vision_supported` 等不动）。
