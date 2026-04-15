# Chat Response Figures Design

## Problem

MatMaster 当前的 assistant 输出主链路是纯文本：

- `response` 事件承载正文文本
- `AssistantMessage` 与历史回放也按文本恢复
- agent 与展示环境分离，不能直接依赖本地或远端文件路径

如果把图片直接写进 markdown 正文，会同时引入三个问题：

- 运行环境里的本地路径或 Bohrium 远端路径对前端不可见，必须先上传 OSS
- markdown 内联图片会把布局决策绑死在正文生成阶段，不适合侧边栏优先的交互
- 历史回放与 PDF 导出会被迫重新解析正文中的图片语法，结构脆弱

本设计的目标是在不引入平台级图片子系统的前提下，为 chat response 增加回答级图片能力。

## Goals

- Web 第一版以侧边栏图片展示为主
- assistant 正文只保留轻量锚点，不直接嵌图
- 支持本地工作区与 Bohrium 远端环境产图
- 图片发现机制仅认 manifest，不做目录扫描兜底
- 用户看到正文时，图片已经完成上传并可预览
- 刷新页面或重新打开历史会话时，图片可随回答一起恢复
- 允许中等规模协议改动，但不引入独立图片表、独立 job 系统或完整 asset 平台

## Non-Goals

- markdown 正文内联图片渲染
- 图片异步补齐或上传进度流
- 独立 `figure_job` 状态机与专门图片表
- 用户手工上传图片并并入回答
- manifest 缺失时的目录扫描回退
- PDF 正文内联排版
- 图片编辑、裁剪、缩略图生成等二次处理

## Chosen Approach

采用 回答级图片绑定层 方案：

1. 工具或脚本按 manifest 契约产图
2. 工具 wrapper 同步校验并上传图片到产品侧 OSS
3. wrapper 将标准化后的图片描述写入 `ToolResult.payload.figures`
4. 服务层在本轮回答结束前，将多个 tool result 中的图片汇总成正式的回答级 `response_figures` 事件
5. 前端主区显示正文，侧边栏消费 `response_figures`
6. 历史回放与 PDF 后续复用同一份回答级图片绑定数据

未采用的方案：

- 仅挂在 `tool_result.payload`：落地快，但回答级语义会被推给前端自行拼装
- 独立图片平台：扩展性强，但对当前项目阶段明显过重

## System Boundaries

### Layer 1: Tool Output

工具负责：

- 产出图片文件
- 写 manifest 声明图片元数据

工具不负责：

- 直接上传 OSS
- 生成前端展示协议
- 维护历史回放数据

### Layer 2: Wrapper Normalization

wrapper 负责：

- 读取 manifest
- 校验图片文件
- 本地或远端取回图片
- 上传产品侧 OSS
- 生成标准化 `payload.figures`

### Layer 3: Response Binding

服务层负责：

- 按当前回答收集所有 `payload.figures`
- 生成正式 `response_figures` 事件
- 将图片绑定到当前父级回答的 `task_id` / `invocation_id` / `spawn_id=null`

### Layer 4: Renderers

- Web：正文 + 侧边栏图片
- PDF：正文 + Figures 附录

renderers 只消费回答级绑定数据，不直接读运行时文件路径。

## Storage Strategy

第一版图片上传统一走产品侧 Aliyun OSS 上传链路，而不是 Bohrium Tiefblue 输入工件链路。

原因：

- chat response 图片需要服务端历史回放与前端侧边栏长期复用
- Bohrium Tiefblue 更适合远端任务输入输出中转，不适合作为回答级展示资源主存储
- 现有产品侧 OSS helper 已返回稳定 HTTPS URL，更贴近 chat 展示需求

URL 策略：

- 第一版持久化 `asset_url` 为上传后得到的稳定 HTTPS URL
- 不在 `response_figures` 中持久化短期签名 URL
- 上传对象应使用可长期复用的不可变 key，避免历史回放时 URL 漂移
- 若后续需要私有 bucket + 动态签名，应新增独立的 URL 解析层；不在第一版实现范围内

## Data Model

### Manifest Schema

第一版 manifest 为 JSON 文件，顶层结构如下：

```json
{
  "figures": [
    {
      "figure_id": "band_structure",
      "path": "plots/band.png",
      "caption": "Si 的能带图",
      "alt": "Si 的能带结构图",
      "importance": "primary",
      "placement_hint": "sidebar_only"
    }
  ]
}
```

字段约束：

- 必填：`figure_id`、`path`、`caption`
- 可选：`alt`、`importance`、`placement_hint`
- `importance` 默认 `secondary`
- `placement_hint` 默认 `sidebar_only`
- `figure_id` 在单个 manifest 内必须唯一
- `path` 必须是相对于 `ARTIFACT_DIR` 的相对路径，不允许绝对路径

若同一回答中不同 tool call 产出重复 `figure_id`，第一版按 first-writer-wins 处理：

- 保留最早被回答绑定层接收的图片
- 后续同名图片丢弃
- 记录结构化 warning 日志

并发说明：

- 不保证跨 tool 的全局确定性顺序
- first 的定义以回答绑定层接收到 `tool_result` 的时序为准

### Normalized Figure Descriptor

wrapper 产出的 `ToolResult.payload.figures[]` 使用统一结构：

- `figure_id`
- `asset_url`
- `caption`
- `alt`
- `importance`
- `placement_hint`
- `source_tool_call_id`

运行态细节如本地路径、远端路径、临时下载目录不进入协议层，只写日志。

说明：

- `source_tool_call_id` 第一版只用于诊断与追踪，renderers 不消费该字段
- 后续若无稳定消费场景，可在下一版协议清理时移除

### Response-Level Binding

新增正式事件类型 `response_figures`，内容为：

- `task_id`
- `invocation_id`
- `spawn_id`
- `figures: list[FigureDescriptor]`

该事件是第一版唯一的回答级图片持久化载体，不新增独立表。

边界约束：

- 第一版仅为父级回答发射 `response_figures`
- `spawn_id` 固定为 `null`
- 子 agent 产出的图片可保留在各自 `tool_result.payload.figures` 中，但不进入父回答绑定层

## Runtime Flow

### Local Image Flow

1. wrapper 为本次 tool call 创建独立 `ARTIFACT_DIR` 与 `MANIFEST_PATH`
2. tool 或 bash 脚本在该隔离目录下生成图片与 manifest
3. wrapper 读取 manifest
4. wrapper 校验 manifest 中声明的每张图
5. wrapper 上传图片到产品侧 OSS
6. wrapper 返回带 `payload.figures` 的 `ToolResult`

目录作用域：

- `ARTIFACT_DIR` 以单次 tool call 为粒度隔离，不在多个 tool call 之间共享
- 本地 `ARTIFACT_DIR` 不在上传后立刻删除，遵循现有 run/workspace 清理时机统一回收

### Bohrium Remote Image Flow

1. wrapper 为本次 tool call 准备本地临时接收目录
2. tool 或脚本在远端 session 工作目录生成图片与 manifest
3. wrapper 通过 session 下载 manifest
4. wrapper 按 manifest 中的相对路径逐张下载远端图片到本地临时目录
5. wrapper 复用统一产品侧 OSS 上传逻辑上传图片
6. wrapper 删除远端下载形成的本地临时文件
7. wrapper 返回带 `payload.figures` 的 `ToolResult`

### Synchronization Rule

第一版严格采用同步流程：

- `ToolResult` 返回时，`payload.figures.asset_url` 必须已经可预览
- 不允许先返回占位图，再异步补齐

这保证：

- 正文锚点不会引用未就绪图片
- 前端无需额外处理图片状态流
- 历史回放天然使用最终数据

### Aggregation Rule

一次 assistant 回答最多发出一次 `response_figures`。

汇总规则：

- 服务层在本轮回答结束前统一收集本回答涉及的 `payload.figures`
- 仅当最终回答文本已经确定、且相关图片均已同步上传完成时，才发出 `response_figures`
- 聚合后的 `figures` 顺序按回答绑定层接收 `tool_result` 的时序拼接
- 单个 `tool_result.payload.figures` 内保持 manifest 的声明顺序
- `response_figures` 固定在对应 `run_result` 之前发出
- 若本回答没有图片，则不发该事件

## Manifest Contract

第一版先面向 bash 与具备文件产出的工具统一约定：

- 注入 `ARTIFACT_DIR`
- 注入 `MANIFEST_PATH`

脚本必须：

- 将最终图片写入 `ARTIFACT_DIR`
- 将 manifest 写入 `MANIFEST_PATH`
- 不得自行上传 OSS

第一版不支持 manifest 缺失时的目录扫描兜底。

若 manifest 缺失、不可解析或字段不合法：

- 该 tool call 视为无图片产出
- 不抛出回答级异常
- 记录 warning，便于排查脚本问题

## Protocol Changes

### Chat Protocol

现有文本主链路保持不变：

- `response` 仍只承载正文文本
- `AssistantMessage.content` 仍保持文本语义

新增正式事件类型：

- `response_figures`

该事件与当前回答通过 `task_id` / `invocation_id` / `spawn_id` 对齐，前端将其与正文合并展示。
对于第一版，`spawn_id` 固定为 `null`，表示父级回答作用域。

代码集成点：

- 在 `matmaster/types/events.py` 中新增 `ResponseFiguresEvent`
- 将其归入 `SystemEvent` / `BusEvent` 联合类型
- 在 `matmaster/integration/event_payloads.py` 中补充公开 payload 映射
- `PersistenceHandler` 与 `SSEHandler` 默认应接纳该事件，不做额外过滤

分类说明：

- `response_figures` 虽然语义上属于回答内容的一部分，但第一版仍归入 `SystemEvent`
- 原因是该事件由服务层在回答收尾阶段汇总生成，不是 kernel 直接发出的原生事件
- 前端与历史回放应基于 `BusEvent.type` 消费该事件，而不应假定回答重建只依赖 `AgentEvent`

发射时机固定为：

- 当前回答已经形成最终可展示文本
- 本回答涉及的图片已经全部完成同步上传
- `response_figures` 在对应 `run_result` 之前发出并持久化

若本回答没有任何图片，则不发 `response_figures` 事件。

### Tool Result Payload

现有 `ToolResult.payload` 扩展约定：

- 若工具产出图片，则写入 `payload.figures`
- 若工具无图片，则不写该字段

`tool_result.payload.figures` 是 tool-level 中间结果，`response_figures` 是 answer-level 正式绑定结果。

## Persistence Strategy

第一版不新增独立图片表，仍使用现有 chat events 持久化链路。

持久化规则：

- `tool_result` 继续照常入库，附带 `payload.figures`
- 新增 `response_figures` 事件入库

历史回放时：

- assistant 文本由既有 `response` / `run_result` 逻辑恢复
- 图片列表由同作用域的 `response_figures` 恢复

前端不再需要遍历所有历史 `tool_result` 来自行拼回答级图片列表。

## Frontend Rendering Rules

### Main Chat Area

- 渲染正文 markdown
- 正文允许出现轻量锚点，规范写法固定为 `[[fig:<figure_id>]]`
- 锚点不直接转成图片

锚点生成方式：

- 第一版通过 prompt / tool-use 约定，引导模型在正文中自然输出 `[[fig:<figure_id>]]`
- 不做正文后处理注入
- 侧边栏展示不依赖锚点是否出现
- 若正文存在悬空锚点，而 `response_figures` 中找不到对应 `figure_id`，前端保留原始文本，不做转换

### Sidebar

- 按 `response_figures.figures` 的顺序展示
- 每张图展示预览、caption、figure_id
- 若正文中存在对应锚点，可为侧边栏卡片增加被引用标记

第一版不做：

- 拖拽排序
- 侧边栏分组折叠
- 正文内图片回显

## PDF Rules

PDF 第一版复用回答级图片绑定，但固定为附录模式：

- 正文按纯 markdown 渲染
- `importance=primary` 的图片追加到 Figures 附录
- `secondary` 图片默认可跳过，或在附录后追加
- 不做正文自动插图
- 不读取运行时本地路径或远端路径，只消费 `asset_url`

## Error Handling

图片失败不默认拖垮整次回答，采用单图降级策略。

### Failure Types

- manifest 无效：丢弃该图片，写 warning
- 文件校验失败：丢弃该图片，写结构化错误日志
- Bohrium 远端下载失败：有限重试后丢弃该图片，写错误日志
- OSS 上传失败：有限重试后丢弃该图片，写错误日志

### Retry Rule

远端下载与 OSS 上传都采用有限重试。第一版建议：

- Bohrium 远端 manifest / 图片下载：1 次短重试
- OSS 上传：2-3 次指数退避重试

超过上限后：

- 该图片不进入 `payload.figures`
- 同一 tool call 中其余上传成功的图片照常进入 `payload.figures`
- 正文仍可继续完成
- tool result 文本可追加简短说明，默认包含失败数量与失败 `figure_id` 的紧凑列表，便于 agent 判断是否需要重试

### Validation Rule

第一版校验项保持克制：

- 文件存在
- 解析后的绝对路径必须位于 `ARTIFACT_DIR` 下，禁止路径穿越
- 扩展名与基本 mime type 合法
- 支持格式白名单：`png`、`jpg`、`jpeg`、`webp`
- `svg`、`tiff`、`eps` 等格式延后支持
- 单张图片大小上限 10 MB

不做复杂图像内容分析。

## Testing

1. Unit: manifest 解析与字段默认值
2. Unit: 本地图像收集与 OSS 上传标准化
3. Unit: Bohrium 远端 manifest 与图片下载后上传
4. Integration: `tool_result.payload.figures` 汇总为 `response_figures`
5. Integration: 历史回放时正文与侧边栏图片同时恢复
6. Integration: 重复 `figure_id` 的 first-writer-wins 规则
7. Regression: 无图片的普通对话流程不受影响

## Rollout Scope

本 spec 覆盖的第一版范围是：

- manifest-only 图片发现
- 本地与 Bohrium 远端产图
- 同步上传 OSS
- tool-level 图片描述
- answer-level `response_figures` 正式事件
- Web 侧边栏展示
- PDF 附录复用

不在本次实现范围内的内容必须显式延后，不得在实现过程中顺手扩展。
