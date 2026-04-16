# Image Input Design

## 背景

MatMaster 当前对用户上传文件的处理以普通附件为主。前端上传文件到 OSS 后，把 URL 放入 `files`，后端再把这些 URL 追加到 prompt 的 `[Attached files]` 文本段中。这个方式适合 CIF、CSV、PDF、日志等文件引用，但不等价于模型真正看到图片。

当前 agent 内部消息也以纯文本为核心：

- `ChatSendRequest.files` 是普通 URL 列表
- `User/query` 历史事件可保存 `files` 和 `workspace_paths`
- `UserMessage.content` 是 `str | None`
- OpenAI-compatible 出站校验要求 message `content` 必须是 string
- LiteLLM 调用层只是把构造好的 messages 传给 `chat.completions.create`

因此，补齐图片输入能力需要补上一个显式的多模态输入边界，而不是继续把图片 URL 当普通文本附件传给模型。

## 目标

- 第一版支持通用视觉问答，用户上传 PNG、JPEG、WebP 后，模型能基于图片回答。
- 科研图像第一版以保真为优先，不自动压缩、缩放、裁剪、重编码或做内容识别。
- 新增显式 `images` 协议字段，区分普通附件和进入 vision 上下文的图片。
- 前端展示仍沿用现有附件列表和缩略图交互，避免第一版 UI 大改。
- 后端不保存 base64，不把图片二进制放进 Redis 或 DB。
- 不修改 SQL table，不新增列、不新增表、不做 migration。
- 图片输入失败时明确报错，不静默降级成普通文本 URL。
- 不支持 vision 的模型收到图片输入时明确失败，提示用户切换支持图片的模型。

## 非目标

- 不支持 base64 / data URL 作为请求输入。
- 不支持本地路径、远端 `/share/...` 路径或 workspace path 作为图片输入。
- 不支持 TIFF、GIF、BMP、HEIC、多页图片或动图。
- 不做 OCR、谱图数字化、显微图分割、结构图识别等专用科学图像处理。
- 不新增独立图片资产表或完整 attachment 平台。
- 不让后端自动把 `files` 中识别出的图片送进 vision。
- 不在第一版里实现前端独立图片输入区；图片仍作为附件展示。

## 已选方案

采用新增 `images: list[str]` 的显式协议方案。

语义约定：

- `files`：普通附件 URL。用于前端展示和文本上下文提示，不进入模型视觉输入。
- `images`：图片 URL。进入模型视觉输入，同时也作为用户消息附件展示。
- `workspace_paths`：工作区路径，保持现有语义。

第一版前端上传流程不大改。上传组件已经会给图片项标记 `isPicture`、`mimeType`、`type`、`url`。发送时，chat-evo 将上传完成的图片 URL 放进 `images`，非图片 URL 放进 `files`。本地用户消息和历史用户消息仍把全部上传项作为附件卡片展示。

未选方案：

- 继续扩展 `files` 并由后端自动识别图片。成本最低，但语义隐式，用户无法判断图片是否真正进入 vision。
- 引入结构化 `attachments`。长期更规范，但第一版改动面过大，会牵动当前前端大量 `files` 逻辑。

## 前端现状判断

只读查看 `../scimaster-bohr-chat` 后，前端新增 `images` 的成本可控：

- `UploadFile` 已有 `isImageFile(file)`，上传项包含 `isPicture`、`mimeType`、`type`、`url`。
- chat-evo 输入区复用该上传组件。
- `useEvoHandleSendMessage` 当前从 `evoUploadList` 取上传完成的项，全部塞进 `files`。
- `postEvoStream` 当前只发送 `files` 和 `workspace_paths`。
- 历史回放和用户消息展示只认 `files`。

第一版前端配套只需要发送层分流和历史展示合并，不需要重做上传 UI。

## API 合同

后端请求模型新增 `images` 字段：

```python
class ChatSendRequest(BaseModel):
    content: str = ''
    files: list[str] | None = None
    images: list[str] | None = None
    workspace_paths: list[str] | None = None
    mode: str = 'direct'
    llm: str | None = None
    model: str | None = None
    bohrium_project_id: int | str | None = None
    bohrium_user_id: int | str | None = None
```

示例请求：

```json
{
  "content": "请分析这张显微图中的主要形貌特征",
  "images": [
    "https://oss.example.com/chat/session-1/micrograph.png"
  ],
  "files": [
    "https://oss.example.com/chat/session-1/metadata.csv"
  ],
  "mode": "direct",
  "model": "gemini-3-flash-preview"
}
```

`images` 只接受 HTTPS URL。请求体中不接受图片二进制，不接受 data URL。

## 持久化合同

不修改 SQL table。继续使用现有 `chat_events.content` JSON 字符串保存 User/query 的扩展字段。

User/query 的 `content` payload 从现有结构扩展为：

```json
{
  "content": "请分析这张显微图中的主要形貌特征",
  "files": ["https://oss.example.com/chat/session-1/metadata.csv"],
  "images": ["https://oss.example.com/chat/session-1/micrograph.png"],
  "workspace_paths": []
}
```

兼容规则：

- 旧历史没有 `images` 时按空列表恢复。
- 新历史有 `images` 时，历史 SSE 顶层返回 `images`，供前端合并展示。
- `get_last_user_query()` 返回 `images`，用于中断提示和后续重跑信息保留。
- 不新增 migration，不新增索引，不调整 `chat_events` 表结构。

## 数据流

### 当前轮发送

1. 前端上传文件到 OSS。
2. 前端发送时将上传完成项分流：
   - `isPicture === true` 且格式允许的 URL 进入 `images`
   - 非图片 URL 进入 `files`
3. 后端接收 `ChatSendRequest`。
4. 后端校验 `images`，得到标准化后的图片 URL 列表。
5. 后端持久化 User/query，`content` JSON 中包含 `files`、`images`、`workspace_paths`。
6. `files` 继续追加进 prompt 的 `[Attached files]` 文本段。
7. `images` 不追加成普通 URL 文本。
8. Redis job 带 `images` URL 列表。
9. Worker 读取 job，将 `images` 传给 `AgentRunService.run_agent()`。
10. `AgentKernel` 为当前轮构造 `UserMessage(content=task, images=validated_images)`。
11. normalization 层将 user message 转成 OpenAI-compatible content parts。
12. LiteLLM 收到多模态 user message。

### 历史恢复

1. DB events 读取 User/query。
2. `ChatHistoryConverter` 从 `content` JSON 中取出文本和 `images`。
3. 恢复为 `UserMessage(content=text, images=...)`。
4. 下一轮上下文中保留历史图片输入。
5. normalization 层再次转成 content parts。

第一版保留历史图片的语义完整性。后续若图片上下文成本过高，可单独设计历史图片保留策略。

## 内部消息模型

新增轻量图片输入类型：

```python
class ImageContentPart(BaseModel):
    url: str
    mime_type: str | None = None
```

扩展 `UserMessage`：

```python
class UserMessage(Message):
    role: Role = Role.USER
    images: list[ImageContentPart] = Field(default_factory=list)

    def to_api_dict(self) -> dict[str, Any]:
        if not self.images:
            return {"role": "user", "content": self.content}

        parts: list[dict[str, Any]] = []
        if self.content:
            parts.append({"type": "text", "text": self.content})
        for image in self.images:
            parts.append({"type": "image_url", "image_url": {"url": image.url}})
        return {"role": "user", "content": parts}
```

出站校验调整：

- `role == "user"` 时，`content` 可为 string 或 content parts list。
- content parts list 只允许 `text` 和 `image_url`。
- `role == "system" | "assistant" | "tool"` 时，`content` 仍必须是 string。
- tool turn 顺序校验保持不变。

这样扩展范围只限 user message，不把整个消息校验变松。

## 图片输入校验

第一版采用强校验、原图 URL 透传策略。

请求级规则：

- 单轮最多 5 张图片。
- 单个 URL 长度最多 4096 字符。
- 只允许 `https://`。
- 域名必须在白名单内，白名单来自配置或环境变量。
- 拒绝 `data:`、`file:`、`http:`、相对路径和内网地址。
- URL 去重并保持原顺序。
- 如果同一个 URL 同时出现在 `files` 和 `images`，后端从 `files` 中移除该 URL，图片语义优先。

资源探测规则：

- 优先 HEAD 请求检查 `Content-Type` 和 `Content-Length`。
- 允许 MIME：
  - `image/png`
  - `image/jpeg`
  - `image/webp`
- 单张图片大小上限 10 MB。
- HEAD 缺少关键 header 时，可做小范围 GET 读取前 512 到 4096 字节，用 magic bytes 判断 PNG、JPEG、WebP。
- 不下载完整图片。
- 不做压缩、缩放、重编码、EXIF 清理或格式转换。

科研图像默认保真。超过限制或格式不支持时直接拒绝，不做无声变换。

## 模型能力检查

只要 `images` 非空，后端必须确认当前模型支持 vision。

第一版采用配置显式标记，而不是在运行路径依赖额外 LiteLLM 网络查询：

```yaml
profiles:
  gemini:
    supports_vision: true
  gpt54:
    supports_vision: true
  opus:
    supports_vision: true
```

`LLMProfileConfig` 新增：

```python
supports_vision: bool = False
```

规则：

- 配置缺失时默认不支持 vision。
- `images` 非空且 profile 不支持 vision 时，请求失败。
- 不自动切换模型。
- 不把图片 URL 静默降级成普通文件附件。
- 错误文案提示用户切换支持图片的模型。

## 错误处理

图片输入错误应尽量在入队前失败，避免产生半截 run。

适合 HTTP 400 / 422 的错误：

- `images` 数量超限
- URL 格式不合法
- URL scheme 不允许
- 域名不在白名单
- 图片类型不支持
- 图片过大
- 图片 URL 不可访问
- 当前模型不支持图片输入

如果实现路径中已经开始 SSE，则用现有 `error` + `stream_closed` 模式关闭流。但设计目标是将图片校验放在 `prepare_send_message()` 和 Redis 入队之前。

错误不应被吞掉。全局 error handler 负责将异常返回给调用方，各层不要在 DAO 中捕获并返回假成功。

## 前端配套要求

前端第一版只做发送层和历史展示层的小改动：

1. `postEvoStream` options/body 增加 `images?: string[]`。
2. `useEvoHandleSendMessage` 将上传完成项分流：
   - 图片 URL 进入 `images`
   - 非图片 URL 进入 `files`
3. 本地用户消息 `meta.files` 仍包含全部上传项，保持现有附件展示。
4. 历史 query 同时读 `files` 和 `images`，合并传给 `ShowFileList`。
5. `images` 生成展示项时补 `isPicture: true`，保证历史图片仍以缩略图展示。
6. 发送前做体验层校验：
   - 图片数量不超过后端上限
   - 格式为 PNG、JPEG、WebP
   - 若上传项有 size，则检查大小
7. 不修改共享 `UploadFile` 的全局限制，避免影响普通附件和其它业务。

## 兼容策略

- 老前端只传 `files`：后端按旧逻辑处理，不自动 vision。
- 新前端传 `images`：后端进入图片输入流程。
- 老历史没有 `images`：恢复为空列表。
- 新历史有 `images`：刷新后用户消息仍显示图片附件，下一轮模型上下文也能包含历史图片。
- 分享页只读，不允许发送消息，现状不变。
- `files` 普通附件行为保持：继续拼入 `[Attached files]`。
- `workspace_paths` 行为保持。

## 测试范围

后端单测：

- `ChatSendRequest` 接受 `images`。
- User/query 持久化 JSON 包含 `images`，不需要 SQL migration。
- `get_session_events()` 能把 `images` 拆到顶层。
- `get_last_user_query()` 返回 `images`。
- `ChatHistoryConverter` 能把历史图片恢复为 `UserMessage(images=...)`。
- `normalize_and_validate_openai_messages()` 允许 user content parts。
- `normalize_and_validate_openai_messages()` 仍拒绝 assistant/tool 的非字符串 content。
- images 非空且模型不支持 vision 时失败。
- URL scheme、域名、MIME、大小、数量校验失败时返回清晰错误。
- 无图片的纯文本对话不受影响。
- 只有普通附件的对话仍按旧逻辑拼 `[Attached files]`。

前端测试建议：

- 发送时图片上传项进入 `images`，非图片进入 `files`。
- 本地用户消息仍展示全部附件，包括图片。
- 历史 query 同时包含 `files` 和 `images` 时，UI 合并展示。
- 图片格式或数量不符合要求时，前端在发送前提示。

## 上线顺序

1. 后端先合入兼容性改动：支持 `images` 字段、历史回放字段、无图片路径不变。
2. 后端开启图片校验和 vision payload 构造，仅在请求显式带 `images` 时触发。
3. 前端合入发送分流，把图片 URL 放入 `images`。
4. 先给一个确认支持 vision 的 profile 标记 `supports_vision: true`。
5. 确认 LiteLLM proxy 和模型提供商能访问 OSS URL 后，再给其它 vision 模型打开。
6. 补充用户可见错误文案。

## 成功标准

- 用户上传 PNG、JPEG、WebP 后，前端仍显示附件缩略图。
- 请求 body 中图片 URL 进入 `images`，普通附件进入 `files`。
- 后端不改 SQL table，也不保存 base64。
- Redis job 和 DB 只保存图片 URL。
- LiteLLM 收到 OpenAI-compatible 多模态 user message。
- 模型确实能基于图片回答，而不是只看到 URL。
- 不支持 vision 的模型明确失败。
- 刷新历史后，用户消息仍显示图片附件。
- 下一轮模型上下文能包含历史图片。
