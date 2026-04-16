# Response Figures Early Render Design

## 背景

MatMaster 已有回答级图片协议：

- 工具执行产生图片后，将图片描述放入 `ToolResultEvent.payload.figures`
- 服务端汇总父级 tool result 中的图片，发出正式的 `response_figures` 事件
- 前端以 `response_figures` 作为图片展示的唯一正式来源
- assistant 正文通过轻量锚点 `[[fig:<figure_id>]]` 引用图片

这里的 `invocation_id` 指一轮用户请求对应的 assistant 响应 ID。它不是
`ResponseFiguresEvent` 的 Pydantic 字段，而是 API 在接收用户消息时生成的
run 级上下文 ID，随后由 `SSEHandler` 与 `PersistenceHandler` 统一注入每条
公开 SSE payload 与持久化事件。前端用 `invocation_id + figure_id` 把同一轮的
正文锚点和图片组关联起来。

当前用户可见问题是：当 agent response 携带图片信息时，前端通常要等整轮 response 结束后才开始渲染图片。

经过代码调研，瓶颈主要在后端事件时机：

- `src/services/response_figures_service.py` 的 `ResponseFiguresAccumulator` 会先收集 `ToolResultEvent.payload.figures`
- `src/services/agent_run_service.py` 当前只在根级 `RunResultEvent` 到达前调用 `build_event()`
- 因此 `response_figures` 虽然位于 `run_result` 之前，但仍接近整轮结束

参考前端仓库 `../scimaster-bohr-chat` 后确认：

- 前端已有 `response_figures` dispatch helper
- 收到 `response_figures` 后会立即写入 `evoResponseFiguresByInvocation`
- 正文渲染会匹配完整 `[[fig:<figure_id>]]`
- 当图片 metadata 已存在且锚点完整闭合后，React 重渲染即可把锚点升级为 inline image

所以本设计的重点是提前发出图片 metadata，而不是让前端等待整轮回答结束。

## 目标

- 当工具已经生成并上传图片后，尽快向前端发出 `response_figures`
- 当前端收到图片 metadata 后，流式正文一旦输出完整 `[[fig:<figure_id>]]`，即可渲染图片
- 保持现有正式协议字段不变
- 保持前端不从 `tool_result` 反推回答级图片
- 保持历史回放可恢复正文和图片
- 支持同一轮回答中多个工具陆续产出图片

## 非目标

- 不把图片 JSON 直接塞进 assistant 正文
- 不新增 `[[fig]]...[[/fig]]` 块式协议
- 不要求后端解析 response 文本来寻找锚点
- 不让前端消费 `tool_result.payload.figures` 作为正式图片来源
- 不改变图片上传、校验、OSS asset key 规则
- 不覆盖子 agent 图片展示，第一版仍只处理 `spawn_id is None` 的父级回答图片
- 不做图片加载占位、缩放面板、拖拽排序、自动编号等 UI 扩展

## 现状数据流

当前事件顺序接近：

```text
tool_call Bash
tool_result Bash, payload.figures = [band]
response streaming chunks
run_result 到达前
response_figures, content.figures = [band]
run_result
stream_closed
```

这导致前端即使已经实现即时消费 `response_figures`，也要等到回答末尾附近才拿到图片 metadata。

## 推荐数据流

新的事件顺序应允许：

```text
tool_call Bash
tool_result Bash, payload.figures = [band]
response_figures, content.figures = [band]
response streaming chunk: 下面是能带结构图
response streaming chunk: [[fig:ba
response streaming chunk: nd]]
frontend render: band inline image
response streaming chunk: 从图中可以看到...
run_result
stream_closed
```

多工具场景应允许多次完整快照：

```text
tool_result Bash, payload.figures = [band]
response_figures, content.figures = [band]
tool_result Bash, payload.figures = [dos]
response_figures, content.figures = [band, dos]
response streaming chunks
run_result
stream_closed
```

`response_figures` 应被视为同一 `invocation_id` 下的图片组快照，而不是只允许出现一次的终局事件。

生产路径中，`invocation_id` 在 `ChatStreamService.prepare_send_message()` 入队前生成，
并随 Redis job 进入 `AgentRunService.run_agent()`。因此即使第一条
`response_figures` 早于第一段 assistant `response` 到达，二者仍应携带同一个
`invocation_id`。若测试或内部调用传入 `invocation_id=None`，公开 payload 不会带
`invocation_id`，参考前端会忽略该图片事件；早渲染能力的前置条件是生产流必须携带
非空 `invocation_id`。

## 后端设计

### 1. 增量快照 accumulator

将 `ResponseFiguresAccumulator` 从一次性事件构造器改为增量快照构造器。

建议状态：

```python
self._seen_ids: set[str]
self._ordered: list[FigureDescriptor]
self._last_emitted_count: int
```

建议接口：

```python
def add_tool_result(self, event: ToolResultEvent) -> bool:
    ...

def build_snapshot_event_if_dirty(self) -> ResponseFiguresEvent | None:
    ...

def mark_snapshot_emitted(self) -> None:
    ...
```

行为规则：

- `add_tool_result()` 仍只吸收 `spawn_id is None` 的父级 tool result
  - `spawn_id` 来自 `ToolResultEvent` 继承的 `EventBase.spawn_id`，不需要从外部 context 传入
  - 第一版不为未来子 agent 合并预留多维状态；如果后续要展示子 agent 图片，应另行定义父子作用域和前端分组规则
- 只接受 `payload.figures` 中合法的 `FigureDescriptor`
- `figure_id` 去重仍采用 first-writer-wins
  - 重复 `figure_id` 视为工具侧或模型侧异常，保留第一张，丢弃后续重复项
  - 丢弃重复项时应写 warning 日志，包含 `figure_id`、已保留的 `source_tool_call_id` 和新到达的 tool call id
- 如果本次新增了至少一张图片，返回 `True`
- `build_snapshot_event_if_dirty()` 在 `_ordered` 长度大于 `_last_emitted_count` 时构造完整快照，但不修改 `_last_emitted_count`
- `mark_snapshot_emitted()` 只在 `await fanout.dispatch(response_figures_event)` 返回后调用，用于更新 `_last_emitted_count`
- 没有新增图片时返回 `None`

完整快照示例：

```json
{
  "type": "response_figures",
  "source": "System",
  "session_id": "sess-1",
  "task_id": "sse_xxx",
  "invocation_id": "inv_xxx",
  "spawn_id": null,
  "content": {
    "figures": [
      {
        "figure_id": "band",
        "asset_url": "https://oss.example/band.png",
        "caption": "Band structure",
        "importance": "primary",
        "placement_hint": "sidebar_only",
        "source_tool_call_id": "call-band"
      }
    ]
  }
}
```

上面的 `session_id`、`task_id`、`invocation_id`、`spawn_id` 是公开 SSE / 历史事件
payload 的顶层字段，由 `build_public_sse_payload_from_bus_dump()` 基于 run 上下文补齐。
Accumulator 只构造内部 `ResponseFiguresEvent(source='System', figures=[...])`。

选择完整快照而不是 delta 的原因：

- 参考前端 store 以 `invocation_id` 为 key 保存图片组，天然适合整体 upsert
- 前端无需维护 delta 合并、删除、乱序补偿逻辑
- 历史事件中任意一条后来的快照都能表达当前已知完整状态
- 多次事件仍保持幂等，重复回放不会产生重复图片

### 2. AgentRunService 提前 dispatch

在 `src/services/agent_run_service.py` 的 event loop 中，处理 `ToolResultEvent` 时先吸收图片：

```python
if isinstance(event, ToolResultEvent):
    figure_accumulator.add_tool_result(event)
```

然后仍按原始事件流照常 dispatch tool result：

```python
await fanout.dispatch(event)
```

如果本次 tool result 新增了图片，紧接着发出 `response_figures` 快照：

```python
if isinstance(event, ToolResultEvent):
    response_figures_event = figure_accumulator.build_snapshot_event_if_dirty()
    if response_figures_event is not None:
        try:
            await fanout.dispatch(response_figures_event)
        except Exception:
            logger.warning("response_figures dispatch failed", exc_info=True)
        else:
            figure_accumulator.mark_snapshot_emitted()
```

推荐顺序是 `tool_result` 先于由它派生出来的 `response_figures`。这比先发图片事件更符合因果顺序，也避免前端已有 tool result UI 对事件顺序存在隐性假设。参考 `../scimaster-bohr-chat` 当前正式图片链路只消费 `response_figures`，没有从 `tool_result.payload.figures` 反推图片组；因此该顺序不会影响回答级图片渲染。

仍保留根级 `RunResultEvent` 前的 final flush：

```python
if isinstance(event, RunResultEvent) and event.spawn_id is None:
    response_figures_event = figure_accumulator.build_snapshot_event_if_dirty()
    if response_figures_event is not None:
        try:
            await fanout.dispatch(response_figures_event)
        except Exception:
            logger.warning("response_figures final flush failed", exc_info=True)
        else:
            figure_accumulator.mark_snapshot_emitted()
```

该 flush 作为兜底，不应在没有新增图片时重复发事件。

`response_figures` 是派生事件，派发失败不应中断 agent run。如果 `fanout.dispatch()`
出现未被 handler-isolation 捕获的异常，局部捕获并记录 warning，且不能调用
`mark_snapshot_emitted()`，后续 final flush 仍有机会补发同一快照。若某个 handler
内部失败，`RunEventFanout._safe_handle()` 会记录并吞掉该异常，`dispatch()` 仍返回；
这种情况下 accumulator 会将快照视为已发，不做无变化补发。后续如果有新增图片，新的完整快照会包含此前所有图片。

### 3. SSE 与持久化

`RunEventFanout` 已保证 SSE handler 先于 persistence handler，因此提前发 `response_figures` 后，实时前端能低延迟收到。

`PersistenceHandler` 当前会持久化 `response_figures`，因此多次快照会进入历史事件。

历史回放策略：

- 前端按 `invocation_id` upsert，最后一次快照自然覆盖前序快照
- 如果历史中存在 `[band]` 后又存在 `[band, dos]`，最终 store 应为 `[band, dos]`
- 如果用户在历史回放中逐帧看到中间态，也不会破坏 UI，只会先显示少量图片再补齐
- 多次快照会增加少量事件存储成本；第一版接受该成本，换取 SSE 与 persistence 使用同一事件语义
- 不在第一版做只持久化最后一次快照的优化，因为这会让实时流与历史流语义分叉，增加调试成本

`src/services/stream_service.py` 中关于 `response_figures` 固定位于 `response` 和 `run_result` 之间的注释需要更新。新的合法顺序是：

- `response_figures` 可以早于第一段 `response`
- `response_figures` 可以位于多个 `response` chunk 之间
- `response_figures` 可以出现多次
- `response_figures` 仍应早于对应的最终 `run_result`

实现 checklist 中必须覆盖 `stream_service` 的两个位置：

- `_dedupe_replayed_terminal_events()` 的 docstring：不能再写死 `response_figures` 位于 `response` 与 `run_result` 之间
- 对应回放测试：至少覆盖 `response_figures -> response -> run_result` 与 `response -> response_figures -> run_result` 两种顺序，且两者都应 dedupe trailing `run_result`

## 前端协同设计

参考 `../scimaster-bohr-chat`，前端主体逻辑可以保持：

- `response_figures` dispatch 收到事件后立即写入 store
- `response_figures` 不追加到 assistant 文本消息
- 正文仍只匹配 `[[fig:<figure_id>]]`
- 未解析到图片的锚点保留原始文本
- 一旦同一 `invocation_id` 的图片 metadata 存在，且正文中出现完整锚点，锚点升级为 inline image

前端硬性协议要求：

- `response_figures` payload 必须带非空顶层 `invocation_id`，否则前端必须忽略该事件
- 前端 store 必须以 `invocation_id` 为 key eager upsert 图片组
- eager upsert 不依赖 assistant message 是否已经创建
- 后续 assistant message 只要带同一 `invocation_id`，就能读取先到达的图片组
- 同一 `invocation_id` 多次收到 `response_figures` 时，后来的完整快照覆盖前一次
- 前端不得把 `tool_result.payload.figures` 当作正式回答级图片来源

本设计不要求前端实现新的流式 token parser。

原因是当前匹配规则已经自然具备闭合后渲染语义：

```text
[[fig:ba
```

不会匹配，也不会渲染。

```text
[[fig:band]]
```

完整闭合后匹配成功，下一次渲染即可升级。

前端交互行为：

- 新图片事件不应强制抢占用户当前右侧栏 tab，除非前端已有明确规则选择默认 active figure
- 需要对现有 `response_figures` dispatch、store、anchor render 做 event-ordering invariant 梳理，确认早于 assistant message 的图片组不会被清空或错绑

## 错误处理

工具图片收集失败时，沿用现有 `collect_figures_from_session()` 语义：

- 单张图片下载、校验、上传失败时，不进入 `figures`
- manifest 非法时，不产生合法图片
- `ToolResult.payload.figures` 为空时，不发 `response_figures`

`response_figures` 事件 dispatch 失败不应中断 agent run。`RunEventFanout` 已对 handler 异常做隔离；派生事件自身的 dispatch 调用仍应局部捕获异常，避免异常冒泡到整轮 run。

Accumulator 的 emitted 状态以 `mark_snapshot_emitted()` 为提交点。只有当
`fanout.dispatch()` 返回后才提交；如果 dispatch 本身抛出未隔离异常，不提交，从而保留
final flush 补发机会。

后端不新增前端可见的图片错误事件。图片加载失败仍由前端 img 组件处理。

## 测试计划

### 后端单元测试

覆盖 `ResponseFiguresAccumulator`：

- 首次吸收合法图片后，`build_snapshot_event_if_dirty()` 返回完整快照但不推进 `_last_emitted_count`
- 调用 `mark_snapshot_emitted()` 后，没有新增图片时不重复发
- 第二个 tool result 新增图片后，快照包含前后两张图片
- 重复 `figure_id` 仍 first-writer-wins，并写 warning 日志
- `spawn_id is not None` 的图片被忽略
- 非法 figure payload 被忽略，不阻塞其它合法 figure

### 后端集成测试

覆盖 `AgentRunService`：

- `ToolResultEvent(payload.figures)` 后立即出现 `response_figures`
- `tool_result` 与由它派生的 `response_figures` 保持因果顺序
- `response_figures` 可以早于 `RunResultEvent`
- `response_figures` 早于第一个 `response` chunk 时，两者携带同一个 `invocation_id`
- 多个 tool result 产生多次快照，后一条包含前序图片
- root `RunResultEvent` 的 final flush 不重复发无变化快照
- 子 agent tool result 不进入父级 response figures
- 注入一个 dispatch 抛异常场景，验证 agent run 不被 handler 失败中断，且未提交的 snapshot 能被 final flush 或下一次新增图片补发

### 历史回放测试

覆盖 `stream_service`：

- `response_figures` 早于 `response` 时仍被回放
- 多次 `response_figures` 快照均可回放
- `response` 后的 `run_result` 去重仍有效
- `response_figures` 不影响 terminal dedupe 状态
- `response` chunk 交织在两次 `response_figures` 之间时，回放顺序与持久化顺序一致

### 前端参考测试

建议在 `../scimaster-bohr-chat` 覆盖：

- 先收到 `response_figures`，再收到完整 `[[fig:band]]`，锚点升级为 inline image
- 收到未闭合 `[[fig:ba` 时仍保留文本
- 后续 chunk 补齐 `nd]]` 后渲染图片
- 同一 `invocation_id` 多次 `response_figures` 快照最终保留完整图片组
- `response_figures` 早于 assistant message 创建时，store 仍创建该 invocation 的图片槽位
- 多轮对话中 A 轮锚点不会引用 B 轮图片

## 文档更新

需要更新：

- `src/models/chat.py` 中 `response_figures` 的协议说明
- `docs/superpowers/specs/2026-04-14-chat-response-figures-design.md` 中关于一次性事件和固定位置的描述
- `docs/superpowers/plans/2026-04-14-chat-response-figures.md` 中关于一次性事件和固定位置的历史实现说明
- `src/services/stream_service.py` 中 `_dedupe_replayed_terminal_events()` 的注释

## 兼容性

对旧前端：

- 如果旧前端只认最后一次 `response_figures`，多次快照不会破坏最终展示
- 如果旧前端收到早发事件但暂时没有 assistant message，只要图片 store 以 `invocation_id` 保存，就不会丢失

对旧历史：

- 历史中只有一次 `response_figures` 的旧会话仍正常展示
- 新历史中多次 `response_figures` 会通过 upsert 收敛为最后完整状态

对 agent prompt：

- 仍要求正文引用图片时使用 `[[fig:<figure_id>]]`
- 不要求模型输出图片 URL
- 不要求模型等待图片事件后才能写正文

## 验收标准

- 后端在工具返回图片 metadata 后，不再等根级 `run_result` 才首次发 `response_figures`
- 前端在 `[[fig:<figure_id>]]` 完整闭合后可渲染对应图片
- 同一轮多个工具图片可以逐步出现
- 没有图片的回答不发 `response_figures`
- 子 agent 图片不混入父级回答
- 历史回放最终图片组与实时流一致
- 生产实时流中，`response_figures` 与同轮 `response` 带同一个非空 `invocation_id`
- 前端允许图片组早于 assistant message 创建并按 `invocation_id` eager upsert
- 现有 `response`、`tool_result`、`run_result`、`stream_closed` 行为不回归

## 自检

- 无待定项
- 无与现有正式字段冲突的协议变更
- 已明确 `invocation_id` 来源、生效时机与前端硬约束
- 已明确 dispatch 失败语义和 accumulator 提交点
- 设计聚焦事件时机，不扩大到 UI 重构
- 保留 `response_figures` 作为唯一正式图片来源
- 保留父级回答作用域限制
- 覆盖实时流、历史回放、多工具、子 agent 与失败场景
