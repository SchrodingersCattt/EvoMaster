# 工具结果图片通路（agent 看图能力）设计

- 日期：2026-06-10
- 状态：已评审（设计阶段）
- 范围标签：types / core / tools / providers / context / src.services

## 1. 背景与问题

当前图片通路是单向的：用户上传图片经 `TurnInput.attachments.images` → `UserMessage.images` → 两个 transport 序列化，链路完整。但工具方向完全没有通路：

- `ToolResult.content` 是纯字符串（`matmaster/tools/tool_result.py`）；
- `ToolMessage` 没有 images 字段（`matmaster/types/messages.py`）;
- dispatch 回填只取字符串（`matmaster/core/agent_tool_dispatch.py`）。

后果：用户在对话中让 agent 查看 session 文件系统中的一张图片时，agent 没有任何办法把图片字节送进模型上下文。`ReadTool` 遇到图片文件走文本解码直接报错。配置层的 `supports_vision` / `vision_detail`（`matmaster/config/llm.py`）定义后从未被消费。

## 2. 已确认决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 图片来源范围 | 仅 session 文件系统（local/ssh，经 `session.download`） | 核心场景最小闭环；公网 URL 图片、其他工具回图按需后续扩展 |
| 传输形式 | base64 data URI 直接进消息历史 | 两个协议、所有 vendor 方言（含 Bedrock）都支持；Bedrock 不支持 url source，URL 方案在该路径不可用；单一路径，不做 vendor 分流 |
| 工具入口 | 扩展 `ReadTool`，不新建工具 | 与 Claude Code 的 Read 行为一致（项目工具集本就对齐 CC）；模型心智自然，无需先判断文件类型再选工具 |
| kernel 机制 | 方案 A：ToolResult/ToolMessage 一等公民图片通路 | 图片归属于产生它的工具调用，持久化/重放/事件流语义一致；未来任何工具自动受益。否决方案 B（dispatch 注入合成 UserMessage）：历史中出现假 user 消息，归属关系丢失 |

## 3. 外部 API 事实（设计依据）

- Anthropic Messages API 原生支持 tool_result 的 `content` 为 block 数组，数组元素可为 `text` / `image` / `document`（官方 handle-tool-calls 文档含 image 示例）。
- Bedrock 与 Vertex 方言仅支持 base64 source，不支持 url source（官方 vision 文档）。
- 图片限制：第一方 API 单图 10MB（base64 后）、Bedrock/Vertex 5MB（base64 后）、尺寸上限 8000×8000px、请求体总限 32MB。
- OpenAI chat completions 协议的 `role: tool` 消息不能携带图片；user 消息的 `image_url` part 接受公网 URL 与 data URI（qwen-VL 两者均支持）。deepseek 当前无视觉模型。

## 4. 架构总览

```
ReadTool（图片分支）
   │ ToolResult(content=文本说明, images=[ImageContentPart(data URI)])
   ▼
dispatch_tool_calls
   ├─▶ ToolMessage(content, images)        # 进 state.messages
   └─▶ ToolResultEvent(..., images)        # 进 SSE + DB 事件流
            │
            └─（每个 user turn）chat_history 事件 → ToolMessage(images) 重建历史
   ▼
transport 序列化
   ├─ AnthropicMessagesTransport：tool_result block 的 content 变 [text, image...] 数组
   └─ ChatCompletionsTransport：tool 消息保持纯文本 + 组后插入带 image_url 的 user wire dict（relay）
```

关键约束：会话历史在每个 user turn 都从 DB 事件流重建（`src/services/chat_history.py` 的 `events_to_messages`），因此图片必须随 `ToolResultEvent` 持久化，否则下一轮即丢。

## 5. 数据模型与 kernel 通路

复用现有 `ImageContentPart`（`url` 承载 data URI，`mime_type` 填 magic bytes 判定出的实际格式，`detail` 保持 None——序列化时不发 detail 字段，由 API 端取默认）。

1. `ToolResult` 增加 `images: list[ImageContentPart] = Field(default_factory=list)`（`matmaster/tools/tool_result.py`）。
2. `ToolMessage` 增加同名字段（`matmaster/types/messages.py`）。
3. dispatch 构造 `ToolMessage` 时透传 `images=tool_result.images`（`matmaster/core/agent_tool_dispatch.py`）。
4. `ToolResultEvent` 增加 `images` 字段（`matmaster/types/events.py`）；它同时是 SSE 推送物与 DB 持久化源。
5. 恢复路径：`src/services/chat_history.py` 中两处 `ToolMessage` 构造（events_to_dialog_messages 的 tool_result 分支、events_to_messages 的 role=tool 分支）带上 images。
6. SSE 原样转发 data URI，前端可直接 `<img src>` 渲染；体积由单图上限约束（见 §6）。

## 6. ReadTool 图片分支与 vision 门控

判定时机与方式：`session.download` 拿到 raw bytes 之后，以 **magic bytes 主判定**，扩展名不参与判定。支持集合与 Anthropic API 的 media_type 一致，qwen-VL 同样支持：

| 格式 | magic | media_type |
|---|---|---|
| PNG | `\x89PNG` | image/png |
| JPEG | `\xFF\xD8\xFF` | image/jpeg |
| GIF | `GIF8` | image/gif |
| WEBP | `RIFF....WEBP` | image/webp |

图片分支流程：

```
magic 命中
  → vision_enabled 为 False ⇒ ToolResult(error, "当前模型 profile 不支持图像输入")
  → 原始字节 > 3MB        ⇒ ToolResult(error, "图片 X MB 超过 3MB 上限，可用 Bash 压缩后重试")
  → base64 → data URI
  → ToolResult(
        content="Read image: <path> (<FMT>, <size>)",
        images=[ImageContentPart(url=data_uri, mime_type=...)])
```

- **单图上限 3 MiB 原始字节**（base64 后约 4MB）：对 Bedrock 单图 5MB（base64 后）留余量，同时控制 DB 事件行与 SSE 帧体积。超限报错，不做自动缩放（与 AttachFigure 超 10MB 报错先例一致；agent 可用 Bash 自行压缩）。
- content 文本只报格式与字节大小，不解析像素尺寸（避免无 Pillow 时手写四种格式的 header 解析）。
- `offset` / `limit` / `encoding` 参数在图片分支忽略。
- 图片分支不写 `snapshot_seed`，也不标记 `mark_read`（两者均服务于 Edit 前置检查，图片不会被 Edit）。
- **vision 门控**：`ReadTool.__init__` 新增 `vision_enabled: bool = False` 构造参数，在 `matmaster/core/exp.py` 工具构造处从 resolved LLM profile 的 `supports_vision` 取值注入（与 session 注入同模式）。`supports_vision` 配置自此首次被消费。deepseek 在此处被挡，下游 relay 不会触发。
- 非图片 magic 的文件走现有文本路径，行为不变。

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
- prompt cache 兼容：`_mark_content_block` / `_message_text_size` 已处理 list 型 content；data URI 逐字节稳定，可参与缓存前缀，多轮重发成本由 cache read 摊平。

## 8. ChatCompletions transport（relay 模式）

`convert_messages`（`matmaster/providers/transports/chat_completions.py`）遍历时收集**连续 ToolMessage 组**中的 images；组结束（遇到非 ToolMessage 或列表尾）且有图片时，紧随其后插入一条 user wire dict：

```python
{"role": "user", "content": [
    {"type": "text", "text": "[Images from the tool results above]"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    ...
]}
```

- tool 消息本身保持纯文本（其 content 已含 "Read image: ..." 说明，模型可对应）。
- 插入仅发生在 wire dict 层；kernel `Message` 列表不变，`validate_tool_turn_sequence` 不受影响；OpenAI 协议对 tool 消息组连续性的要求满足（插在整组之后）。
- qwen/deepseek 子类（reasoning replay、preserve_thinking）只动 assistant 字段与请求体字段，与 relay 正交。
- deepseek 路径因 §6 门控永远收不到带图消息；relay 实现位于基类，由 qwen 等视觉 vendor 消费。

## 9. 生命周期

- **Compaction**（`matmaster/context/compaction.py`）：`_truncate_tool_message_for_summary` 重建 `ToolMessage` 时不携带 images（摘要请求不含图片）；truncation marker 追加一行 `images_dropped: N`。保留尾部未压缩的近期 `ToolMessage` 原样保留 images。
- **持久化体积**：由单图 3 MiB 上限约束；Read 单次调用最多产生一张图。
- **不做**：全局在途图片数量限制、自动缩放、vendor 差异化大小上限（YAGNI，按单一最严值执行）。

## 10. 错误矩阵

全部复用现有 `ToolResult(status="error")` 通路，无新错误机制：

| 情形 | 行为 |
|---|---|
| magic 非已知图片格式 | 走现有文本解码路径，行为不变 |
| `supports_vision=False` | error：明确告知当前模型不支持图像输入 |
| 原始字节 > 3 MiB | error：报实际大小与上限，提示用 Bash 压缩 |
| `session.download` 失败 | 现有错误路径不变 |
| 像素尺寸超 API 上限（8000×8000px） | 工具层不校验（不解析尺寸）；由 provider 层 API 报错。3 MiB 字节上限使该情形概率极低 |

## 11. 测试计划

- types：`ToolResult` / `ToolMessage` / `ToolResultEvent` 带 images 的 model_dump/model_validate 往返。
- ReadTool：PNG/JPEG fixture 走图片分支；超限报错；`vision_enabled=False` 报错；扩展名与 magic 不一致时以 magic 为准；非图片文件文本路径回归。
- AnthropicMessagesTransport：带图 `ToolMessage` 的 tool_result block 数组形状；无图保持字符串现状。
- ChatCompletionsTransport：relay 插入位置（单 tool、同轮多 tool 部分带图、连续两轮 tool）；tool 消息保持纯文本。
- 恢复：tool_result 事件 → `ToolMessage.images` 往返（chat_history 两处构造）。
- compaction：摘要路径丢图且 marker 注明；保留尾部留图。

## 12. 非目标

- 公网 URL 图片读取（按需后续扩展）。
- 图片自动缩放/重编码。
- 像素尺寸解析与 8000px 工具层校验（见 §10）。
- OSS URL 传输形式与 vendor 分流（Bedrock 不支持 url source，且违背单一路径原则）。
- 用户上传方向（`UserMessage.images`）的任何行为变更。
- 全局在途图片数量限制。
