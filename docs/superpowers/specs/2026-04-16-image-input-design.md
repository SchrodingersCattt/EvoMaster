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

## 代码现状与本次改动边界

当前代码已经支持把 User/query 的 `files` 和 `workspace_paths` 保存在 `chat_events.content` JSON 中，并在 SSE 历史回放时拆到事件顶层。但这些附件还没有恢复成 agent 内部 `UserMessage` 的结构化输入：

- `ChatHistoryConverter` 当前只从 User/query 事件恢复文本内容。
- `get_last_user_query()` 当前返回 `files`、`workspace_paths`、`mode`，但不返回 `images`。
- 当前 `files` 只会被拼入 prompt 的 `[Attached files]` 文本段。
- 当前 `UserMessage.content` 和出站校验仍是纯文本设计。

因此，本次实现不是只给现有历史恢复逻辑加一个 `images` 字段，而是要补齐 User/query 元数据在 API、历史回放、agent 内部消息和出站 normalization 之间的结构化路径。普通 `files` 仍不进入 vision，但需要继续随 User/query 历史事件回放给前端展示。

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

## 可访问性假设

第一版要求 `images` 中的 URL 对 LiteLLM proxy 和最终模型提供商的图片 fetch worker 可访问。当前前端上传链路在上传完成后调用文件服务 `setacl public-read`，因此第一版按长期可访问的 public-read URL 设计。

上线前必须确认：

- 产品文件服务返回的图片 URL 至少在会话生命周期内稳定可访问。
- LiteLLM proxy 所在网络和模型提供商 fetch worker 能访问该 URL。
- OSS bucket、CDN 或网关不会阻止模型侧 egress。
- 如果部署改为私有 bucket 或短期预签名 URL，必须先实现 URL 重签或刷新机制，再打开历史图片进入 LLM 上下文。

历史图片 URL 失效时采用降级策略：

- 历史回放 UI 不失败，仍显示历史事件中的附件 URL。
- 构造下一轮 LLM 上下文时，不主动探测历史图片 URL 是否仍可访问；只有静态校验失败或被动 fetch 错误能明确归因到某张历史图片时，该历史图片才不进入后续 `image_url` content part。
- 对被丢弃的历史图片，在对应 user message 中追加轻量文本占位，例如 `[历史图片不可访问: micrograph.png]`。
- 当前轮新上传图片校验失败时仍直接拒绝本轮请求，不降级为普通附件。

第一版不依赖预签名 URL 作为长期历史数据。若后续必须使用预签名 URL，应把持久化内容改为 OSS object key 或资产 ID，并在每次历史恢复时生成新 URL；这属于后续独立设计。

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
- 若未来需要按图片做管理端统计、检索或资产治理，再考虑抽独立图片表；第一版不把 `images` 变成查询热路径。

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
10. `AgentKernel` 为当前轮构造 `UserMessage(content=base_prompt, images=validated_images)`。
11. normalization 层将 user message 转成 OpenAI-compatible content parts。
12. LiteLLM 收到多模态 user message。

当前轮 `UserMessage.content` 的 text 部分保持现有 prompt 语义，即继续使用已经拼接 `[Attached files]` 和 workspace paths 的 `base_prompt`。`images` 只作为额外 content parts 进入同一个 user message，不替换或清空现有文本上下文。

### 历史恢复

1. DB events 读取 User/query。
2. `ChatHistoryConverter` 从 `content` JSON 中取出文本和 `images`。
3. 恢复为 `UserMessage(content=text, images=...)`。
4. 按 `history_policy` 裁剪历史图片。
5. 对裁剪后仍会进入 LLM 的历史图片，只做 URL 格式、scheme、host 和 path prefix 静态校验，不做主动 HEAD / Range GET 网络探测。
6. 下一轮上下文中保留策略允许的历史图片输入。
7. normalization 层再次转成 content parts。

历史图片按策略保留，避免长会话中图片数量无限累积。

默认策略：

- 当前轮图片总是进入本轮 LLM 请求。
- 历史图片只保留最近 3 个带 `images` 非空的 user turn。
- 历史图片总数最多 10 张，只统计实际进入 LLM 的 `image_url` content part，不统计文本占位。
- 超出策略的历史图片不进入 `image_url` content part，并在对应用户消息中保留轻量文本占位。

该策略应做成配置项：

```yaml
image_input:
  history_policy: "last_k_turns"
  history_last_k_turns: 3
  history_max_images: 10
```

可选策略包括：

- `only_last_turn`
- `last_k_turns`
- `all`

生产默认不得使用 `all`，除非另有成本和上下文窗口评估。

`last_k_turns` 中的 turn 指带图片的用户轮次，不是所有用户轮次。例如：

| 对话轮次 | 输入 | `last_k_turns=3` 是否保留图片 |
| --- | --- | --- |
| 第 1 轮 | 文本 + 图片 A | 保留，直到出现超过 3 个更新的带图用户轮次 |
| 第 2-5 轮 | 纯文本 | 不影响图片 A 的保留计数 |
| 第 6 轮 | 文本 + 图片 B | 保留图片 A 和图片 B |

历史图片不主动网络探测的原因是：这些 URL 在首次发送时已经经过当前轮强校验，反复刷新、多 tab 或 SSE 重连时再次探测会放大 OSS 请求量。若 LiteLLM 或模型 provider 在被动 fetch 历史图片时返回可明确归因到历史图片 URL 的 4xx/5xx 错误，运行层可以将该历史图片替换为文本占位并重试一次；如果错误无法归因，则按普通 LLM 调用错误暴露，不做静默降级。

## 内部消息模型

新增轻量图片输入类型：

```python
class ImageContentPart(BaseModel):
    url: str
    mime_type: str | None = None
    detail: Literal["low", "high", "auto"] | None = None
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
            image_url = {"url": image.url}
            if image.detail is not None:
                image_url["detail"] = image.detail
            parts.append({"type": "image_url", "image_url": image_url})
        return {"role": "user", "content": parts}
```

`mime_type` 第一版用于校验记录、日志和后续 provider adapter 分流，不默认写入 OpenAI-compatible payload。`detail` 用于支持 OpenAI-compatible `image_url.detail` 的模型；科研图像保真优先，支持该字段的 vision profile 第一版默认使用 `high`，若 provider 不支持或验证后兼容性有问题，可在 profile 中显式设为 `null` 以省略该字段。

出站校验调整：

- `role == "user"` 时，`content` 可为 string 或 content parts list。
- content parts list 只允许 `text` 和 `image_url`。
- `role == "system" | "assistant" | "tool"` 时，`content` 仍必须是 string。
- tool turn 顺序校验保持不变。

这样扩展范围只限 user message，不把整个消息校验变松。

## 图片输入校验

第一版采用强校验、原图 URL 透传策略。

### 请求级规则

- 单轮最多 5 张图片。
- 单个 URL 长度最多 4096 字符。
- 只允许 `https://`。
- 域名必须在白名单内，白名单来自配置或环境变量。
- URL path 必须匹配配置的允许前缀，不能只靠域名白名单放行整个 bucket。
- 生产环境拒绝 `data:`、`file:`、`http:`、相对路径和内网地址。
- 开发环境可通过 `IMAGE_INPUT_ALLOW_INSECURE_HOSTS=localhost,127.0.0.1` 放行指定 HTTP host，但生产环境必须忽略该放行配置或启动时报错。
- URL 去重并保持原顺序。
- `files` 和 `images` 必须互斥；同一个 URL 同时出现在两者中时，后端返回 422。前端发送前也要保证请求 body 中二者 disjoint。
- 前端本地展示仍可把所有上传项放进用户消息 `meta.files`，但请求 body 的 `files` 与 `images` 必须分离。

安全边界：

- 第一版不能只接受任意白名单 OSS 域名下的任意路径。
- 允许前缀应尽量绑定产品上传服务的对象路径，例如反馈附件、会话上传或用户上传前缀。
- 如果当前上传服务无法提供 session/user-scoped 前缀，图片输入功能应先使用更窄的产品上传前缀白名单，并在 rollout 记录剩余跨租户 URL 引用风险。
- 若后续需要强所有权校验，应升级为结构化 attachment 或资产 ID，由后端向文件服务校验 object owner。

### 资源探测规则

- 优先 HEAD 请求检查 `Content-Type` 和 `Content-Length`。
- 允许 MIME：
  - `image/png`
  - `image/jpeg`
  - `image/webp`
- 单张图片大小上限 10 MB。
- HEAD 失败、超时、返回 401/403/405，或缺少关键 header 时，fallback 到 Range GET。
- Range GET 使用 `Range: bytes=0-4095`，读取前 4096 字节做 magic bytes 判断 PNG、JPEG、WebP。
- 当前轮图片必须能得到可信大小。大小可来自 HEAD 的 `Content-Length`，或 Range GET 的 `Content-Range` / `Content-Length`。当前轮图片大小未知时拒绝。
- 历史图片不做主动网络探测，只在裁剪后做 URL 静态校验；被动 fetch 失败的处理见历史恢复策略。
- 不下载完整图片。
- 不做压缩、缩放、重编码、EXIF 清理或格式转换。
- 当前轮探测并发执行，单请求最多 5 张图片并发探测。
- 单张图片探测超时 3 秒。
- 单轮图片探测总超时 5 秒。
- 若生产中 OSS 出现速率限制或突发压力，应在请求级并发之外增加进程级或 Redis 协调的全局 semaphore。
- 如果实现能从本会话刚上传的可信上传元数据中获得 MIME 和大小，可跳过网络探测；但这些元数据不能由前端裸传后直接信任，必须来自服务端可信缓存或文件服务查询。

科研图像默认保真。超过限制或格式不支持时直接拒绝，不做无声变换。

### 探测判定树

以下判定树只适用于当前轮新上传图片的主动探测。

1. URL 基础校验失败：拒绝当前请求。
2. HEAD 成功且 MIME、大小合法：接受。
3. HEAD 不可用或信息不完整：尝试 Range GET。
4. Range GET 能确认 magic bytes 和大小合法：接受。
5. Range GET 能确认 magic bytes 但无法确认大小：拒绝。
6. HEAD 与 Range GET 都失败：拒绝。

## 模型能力检查

只要 `images` 非空，后端必须确认当前模型支持 vision。

第一版采用配置显式标记，而不是在运行路径依赖额外 LiteLLM 网络查询：

```yaml
profiles:
  gemini:
    supports_vision: true
  gpt55:
    supports_vision: true
  opus:
    supports_vision: true
```

`LLMProfileConfig` 新增：

```python
supports_vision: bool = False
vision_detail: Literal["low", "high", "auto"] | None = "high"
```

规则：

- 配置缺失时默认不支持 vision。
- 支持 vision 的 profile 默认使用 `vision_detail="high"`，以避免 provider 默认 `auto` 策略对显微图、谱图等大图做过强降采样。
- 对不支持 `image_url.detail` 的 provider，profile 应显式配置 `vision_detail: null`，adapter 在构造 payload 时省略该字段。
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

错误码建议：

| Code | 含义 |
| --- | --- |
| `IMAGE_INPUT_TOO_MANY` | 图片数量超过上限 |
| `IMAGE_INPUT_URL_TOO_LONG` | 单个 URL 过长 |
| `IMAGE_INPUT_INVALID_SCHEME` | scheme 不允许 |
| `IMAGE_INPUT_DOMAIN_BLOCKED` | host 不在白名单 |
| `IMAGE_INPUT_PATH_BLOCKED` | path 不在允许前缀内 |
| `IMAGE_INPUT_DUPLICATE_ATTACHMENT` | 同一 URL 同时出现在 `files` 和 `images` |
| `IMAGE_INPUT_UNREACHABLE` | 图片 URL 不可访问 |
| `IMAGE_INPUT_UNSUPPORTED_MIME` | MIME 或 magic bytes 不支持 |
| `IMAGE_INPUT_SIZE_UNKNOWN` | 当前轮图片无法确认大小 |
| `IMAGE_INPUT_TOO_LARGE` | 图片超过大小上限 |
| `VISION_MODEL_NOT_SUPPORTED` | 当前模型不支持图片输入 |

`VISION_MODEL_NOT_SUPPORTED` 的用户可见文案应明确下一步，例如：当前模型不支持图片输入，请切换到支持图片的模型后重试。

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
7. 发送前保证请求 body 中 `files` 与 `images` disjoint。
8. 不修改共享 `UploadFile` 的全局限制，避免影响普通附件和其它业务。

前端校验只用于提前反馈和减少无效请求，后端校验是权威来源。当前后端判断与前端判断不一致时，以后端错误码为准，前端只负责把错误码映射成用户可理解的文案。

## 兼容策略

- 老前端只传 `files`：后端按旧逻辑处理，不自动 vision。
- 新前端传 `images`：后端进入图片输入流程。
- 老历史没有 `images`：恢复为空列表。
- 新历史有 `images`：刷新后用户消息仍显示图片附件，下一轮模型上下文也能包含历史图片。
- 分享页只读，不允许发送消息，现状不变。
- `files` 普通附件行为保持：继续拼入 `[Attached files]`。
- `workspace_paths` 行为保持。
- 历史图片是否进入下一轮 LLM 请求由 `image_input.history_policy` 控制，不保证所有历史图片永久进入上下文。

## 测试范围

后端单测：

- `ChatSendRequest` 接受 `images`。
- User/query 持久化 JSON 包含 `images`，不需要 SQL migration。
- `get_session_events()` 能把 `images` 拆到顶层。
- `get_last_user_query()` 返回 `images`。
- `ChatHistoryConverter` 能把历史图片恢复为 `UserMessage(images=...)`。
- 当前轮 `UserMessage.content` 的文本部分保持既有 `[Attached files]` / workspace paths 拼接行为。
- `normalize_and_validate_openai_messages()` 允许 user content parts。
- `normalize_and_validate_openai_messages()` 仍拒绝 assistant/tool 的非字符串 content。
- images 非空且模型不支持 vision 时失败。
- 配置声称 `supports_vision: true` 但模型实际拒绝多模态请求时，错误应按 LLM 调用错误暴露，不得静默降级。
- URL scheme、域名、MIME、大小、数量校验失败时返回清晰错误。
- URL path prefix 不在允许范围内时失败。
- `files` 与 `images` 重复 URL 时失败。
- HEAD 成功路径。
- HEAD 失败但 Range GET 成功路径。
- HEAD / Range GET 都失败时，当前轮图片失败。
- 历史恢复不对历史图片发起 HEAD / Range GET 主动探测。
- 历史图片超过 `last_k_turns` 或 `history_max_images` 时被裁剪，裁剪发生在历史图片静态校验之前。
- `last_k_turns` 按带图片的 user turn 计数，纯文本 user turn 不消耗计数。
- 历史图片被 provider 被动 fetch 失败且错误可归因时，可替换为文本占位并重试；无法归因时暴露 LLM 调用错误。
- 无图片的纯文本对话不受影响。
- 只有普通附件的对话仍按旧逻辑拼 `[Attached files]`。

前端测试建议：

- 发送时图片上传项进入 `images`，非图片进入 `files`。
- 本地用户消息仍展示全部附件，包括图片。
- 历史 query 同时包含 `files` 和 `images` 时，UI 合并展示。
- 图片格式或数量不符合要求时，前端在发送前提示。
- 请求 body 中 `files` 和 `images` 不重复。

E2E 建议：

- 上传图片 -> 发送 -> 模型回答 -> 刷新历史 -> 下一轮在历史策略允许范围内仍能使用最近图片。
- 使用不支持 vision 的模型发送图片，前端展示可理解的错误。
- 用 2K 以上显微图或谱图对主力 vision profile 做 sanity check，确认 `vision_detail="high"` 或 provider 默认策略不会丢失关键视觉特征；若 provider 不支持 `detail`，需记录该 profile 的实际行为。

## 上线顺序

1. 后端先合入兼容性改动：支持 `images` 字段、历史回放字段、无图片路径不变。
2. 对齐 OSS URL 可访问性、允许 host/path prefix、LiteLLM egress 和历史 URL 生命周期。
3. 后端开启图片校验和 vision payload 构造，仅在请求显式带 `images` 时触发。
4. 前端合入发送分流，把图片 URL 放入 `images`。
5. 先给一个确认支持 vision 的 profile 标记 `supports_vision: true`。
6. 对该 profile 的 `vision_detail` 行为做科研图像 sanity check。
7. 确认 LiteLLM proxy 和模型提供商能访问 OSS URL 后，再给其它 vision 模型打开。
8. 补充用户可见错误文案。

## 成功标准

- 用户上传 PNG、JPEG、WebP 后，前端仍显示附件缩略图。
- 请求 body 中图片 URL 进入 `images`，普通附件进入 `files`。
- 后端不改 SQL table，也不保存 base64。
- Redis job 和 DB 只保存图片 URL。
- LiteLLM 收到 OpenAI-compatible 多模态 user message。
- 模型确实能基于图片回答，而不是只看到 URL。
- 不支持 vision 的模型明确失败。
- 刷新历史后，用户消息仍显示图片附件。
- 下一轮模型上下文能按 `history_policy` 包含最近历史图片。

## 后续扩展

- 若需要彻底解决跨租户 URL 引用风险，应从裸 URL 升级为结构化 attachment 或资产 ID，并由后端向文件服务校验 owner。
- 若需要支持私有 bucket，应持久化 object key 或 asset id，并在每次发送前重签可访问 URL。
- 若长对话成本仍偏高，应增加图片摘要、人工 pin 图片或 per-turn 图片引用机制。
- 用户重复点击发送的幂等性可通过 client-generated `request_id` 解决。这是通用发送链路问题，不是图片输入第一版的阻塞项；当前仍依赖既有 session run lock 和前端 loading 禁用机制。
