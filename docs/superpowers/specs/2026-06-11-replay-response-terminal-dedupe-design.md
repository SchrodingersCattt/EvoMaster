# Replay response 终态去重修复设计

- 日期: 2026-06-11
- 状态: 已按 2026-06-11 评审意见修订并实施（同日）
- 范围: 后端历史回放事件过滤，主要涉及 `src/services/stream_sse_filter.py`
- 作者: Kealdoom + Codex

## 1. 背景与问题陈述

当前 agent 运行过程中，前端可以实时收到 `response`、`tool_call`、`tool_result`
等事件。若在本轮 agent 尚未结束时刷新页面，历史回放也能看到 tool_call
同级的 `response` 信息。

但当 agent 结束并发送 `stream_closed` 后，再次刷新会话，前面这些
tool_call 同级的 `response` 消失，只剩 `tool_call` / `tool_result` /
`run_result` 等事件。

这不是 `stream_closed` 删除了内容，而是 run 完成后 `run_result` 已经落库，
刷新回放执行终态去重时，把同一 `(task_id, spawn_id)` 下的所有 `response`
都隐藏了。当前去重粒度是 task 级，无法区分最终答案重复副本和工具调用前的
中间可见文本。

本设计采用方案 A：只删除与最终 `run_result` 等价的那条最终 `response`，
保留同一 run 中更早的中间 `response`。

## 2. 当前事实链

### 2.1 实时流与刷新回放是两条不同路径

实时路径由 `SSEHandler` 处理 bus event，Worker 通过 Redis 发布事件，API
订阅后直接转发给前端。`generate_send_stream()` 收到 Redis payload 后只补
`elapsed_ms`、`stream_started_at`、`invocation_id` 等信封字段，然后发出 SSE。

刷新回放路径在 `ChatStreamService._iter_history_replay_batches()` 中执行：

1. `get_session_events(..., include_spawn=True, exclude_types=REPLAY_DISCARDED_EVENT_TYPES)`
   读取历史事件。
2. `_normalize_replayed_compaction_events(events)` 修正 compaction 生命周期。
3. `_dedupe_replayed_terminal_events(events)` 做 response / run_result 去重。
4. `_iter_replayed_sse_batches(...)` 输出 SSE frame。

因此，运行中刷新和结束后刷新出现差异，优先看 replay filter，而不是看
实时 SSE handler 或前端渲染。

### 2.2 run_result 在 stream_closed 之前发出

`AgentRunService.run_agent()` 消费 `exp.run_stream()` 时，先 dispatch
generator 内部的 `RunResultEvent`，随后才 dispatch `StreamClosedEvent`。

成功路径的顺序是：

```text
... agent events
run_result
stream_closed
```

`stream_closed` 是传输层关闭标记，表示这条实时 SSE 可以结束；业务终态由
`run_result` 表达。

### 2.3 complete response 会持久化，但实时 SSE 会跳过

`PersistenceHandler` 会跳过 streaming delta：

- `stream_state=start`
- `stream_state=streaming`
- `stream_state=segment_end`
- `stream_state=end`

但它会保留 `stream_state=complete` 的 `ResponseEvent`，除非内容只是平凡符号。
这类 complete response 的 content 会通过 `_public_content_for_event()` 保存到
事件表。

`SSEHandler` 对实时前端则会跳过 `stream_state=complete` 和
`stream_state=segment_end` 的 `ResponseEvent`，避免实时路径重复渲染聚合快照。

这解释了一个看起来反直觉的现象：运行中刷新可能看到持久化的 complete
`response`，实时流本身却不会把 complete `response` 原样推给前端。

### 2.4 当前 replay 去重粒度过粗

当前 `_dedupe_replayed_terminal_events()` 的逻辑是：

1. 扫描所有事件，只要同一 `(task_id, spawn_id)` 存在 replayable `run_result`，
   就把这个 key 记录到 `terminal_keys`。
2. 再次扫描事件，凡是同一 key 下的 `response`，全部跳过。

它的原始目的合理：避免刷新后同时显示最终 `response` 和 `run_result`，
导致最终答案重复。

但同一 agent run 内可能有多个 LLM turn。例如：

```text
response(turn_index=0, content=我先检查文件)
tool_call
tool_result
response(turn_index=1, content=最终结论)
run_result(num_turns=2, final_content=最终结论)
stream_closed
```

这几条事件共享同一个 `task_id` 和同一个父级 `spawn_id=None`。当前逻辑在
`run_result` 存在后会把两个 `response` 都删除，导致刷新后丢失 tool_call
同级的中间说明。

## 3. 设计目标

1. 刷新后保留 agent 运行过程中真实产生过的中间 `response`。
2. 刷新后仍避免最终答案重复显示，不同时显示等价的最终 `response` 和
   `run_result`。
3. `run_result` 继续作为本轮业务终态事件。
4. `stream_closed` 继续只是传输层关闭标记，不参与业务内容去重。
5. 不在主代码中加入旧数据兼容、自动迁移、兜底猜测。取不到可比较文本的事件
   按保留处理，必要时用外部脚本或手动清理。
6. 不改实时 SSE 路径，不改 Redis/Worker 通信，不要求前端改渲染逻辑。

## 4. 非目标

- 不改变 `RunResultEvent`、`ResponseEvent`、`StreamClosedEvent` 的事件模型。
- 不新增 `terminal_turn_index` 字段。
- 不把去重责任转移给前端。
- 不删除 `run_result`，也不恢复旧的 `finish` 事件语义。
- 不修改 `assistant_state` 的回放策略；它仍然是内部事件，不回放给 SSE。

## 5. 推荐方案

将 `_dedupe_replayed_terminal_events()` 从 task 级去重改为最终答案级去重。

### 5.1 去重单位

保留现有 `_replay_terminal_dedupe_key(event)`：

```python
(task_id, spawn_id)
```

这个 key 仍然有价值，因为父 agent 和子 agent 可能共享 `task_id`，但 `spawn_id`
不同。去重必须继续保证子 agent 的 `response` 不会被父 agent 的 `run_result`
隐藏。

### 5.2 删除条件

只删除满足以下全部条件的 `response`：

1. 与某条 replayable `run_result` 属于同一 `(task_id, spawn_id)`。
2. `response` 的规范化可见文本等于该 `run_result` 的规范化最终文本。
3. 在同一分组内存在多个匹配项时，只删除最后一个匹配项。

不引入 `turn_index` / `num_turns` 匹配条件（评审修订，原条件 3 已删除）。
原因是救援式自然结束路径——`matmaster/core/agent.py` 中
`is_valid_natural_finish` 失败但 `finish_reason == "stop"` 的分支——会用更早
turn 的 `last_emitted_content` 作为 `final_content` 收尾，且 `num_turns` 计入
了最后那个无可见输出的 turn。此时携带最终文本的 `response` 满足
`turn_index < num_turns - 1`，按 turn index 匹配必然漏删，造成最终答案双显，
而该路径字段齐全，不属于旧数据豁免场景。

逐一推演全部终态路径（正常 natural、救援 natural、interrupted、cancelled、
max_turns、invalid_finish、internal_error）可确认：文本相等 + 只删最后一个
匹配，在每条路径上都给出期望行为；turn index 条件唯一改变行为的场景恰好是
它出错的救援路径，没有额外保护作用。

### 5.3 缺文本与空文本处理

删除判定只依赖两段文本。任一侧取不到有效文本时，对应 `response` 不删除：

- `response` 取不到可见文本：content 为 dict 但无 `content` 键，或文本归一化
  后为空。
- `run_result.content` 不是 dict，或其 `content` 文本归一化后为空。此时该
  terminal 不参与任何删除。

注意持久化层对 terminal 事件恒写 `content` 键（`final_content or ''`，见
`_public_content_for_event()`），所以"缺键"只出现在手工构造的数据里；真正
需要防护的是空字符串形态，例如 cancelled / max_turns 终态的
`final_content=None` 落库为 `''`。空文本经
`normalize_visible_response_text()` 归一为 None，自然落入保留分支。

原则不变：宁可保留重复最终答案，也不误删中间 response。不做内联兼容或自动
迁移。

### 5.4 文本比较

比较使用规范化后的可见文本，避免因为空值形态导致误判。最小规则如下：

1. 从 `response.content` 中取文本：
   - dict 形态取 `content["content"]`
   - str 形态取自身。注意 str 形态是当前合法持久化形态而非旧格式：
     `_response_public_content()` 在 payload 既无 usage 又无 model identity
     时直接返回原始 content，不包 dict。
2. 从 `run_result.content` 中取文本：
   - dict 形态取 `content["content"]`；非 dict 形态视为无终态文本。
3. 两边都先转换为字符串，再经
   `matmaster.response_text.normalize_visible_response_text()` 归一：空白串
   与 none/null sentinel 归一为 None，不参与比较；其余原样返回。

归一化不改变可见文本本身，比较仍是严格相等。不要为了兼容旧数据添加宽松
匹配，例如包含关系、前后缀匹配、忽略 Markdown 标记等。

## 6. 目标行为示例

事件标注里的 turn_index / num_turns 等持久化字段可以存在，但不参与判定，
示例从略。

### 6.1 单 turn 最终答案，仍去重

输入历史：

```text
response(task=t1, spawn=None, content=最终答案)
run_result(task=t1, spawn=None, content=最终答案)
```

回放输出：

```text
run_result(task=t1, spawn=None, content=最终答案)
```

目的：继续避免最终答案重复显示。

### 6.2 多 turn 工具调用，中间 response 保留

输入历史：

```text
response(task=t1, spawn=None, content=我先检查文件)
tool_call(task=t1, spawn=None, name=bash)
tool_result(task=t1, spawn=None, result=ok)
response(task=t1, spawn=None, content=最终结论)
run_result(task=t1, spawn=None, content=最终结论)
```

回放输出：

```text
response(task=t1, spawn=None, content=我先检查文件)
tool_call(task=t1, spawn=None, name=bash)
tool_result(task=t1, spawn=None, result=ok)
run_result(task=t1, spawn=None, content=最终结论)
```

目的：保留 tool_call 同级的中间可见文本，同时避免最终结论重复显示。

### 6.3 多 turn 中最终 response 文本不同，不删除

输入历史：

```text
response(task=t1, spawn=None, content=部分草稿)
run_result(task=t1, spawn=None, content=最终结论)
```

回放输出：

```text
response(task=t1, spawn=None, content=部分草稿)
run_result(task=t1, spawn=None, content=最终结论)
```

目的：只删除等价的最终答案副本，不删除不同内容。

### 6.4 子 agent response 不被父 run_result 删除

输入历史：

```text
response(task=t1, spawn=sub-1, content=同一段最终文本)
run_result(task=t1, spawn=None, content=同一段最终文本)
```

回放输出：

```text
response(task=t1, spawn=sub-1, content=同一段最终文本)
run_result(task=t1, spawn=None, content=同一段最终文本)
```

目的：继续保留现有 `(task_id, spawn_id)` 隔离语义，即使文本相等也只在同组
内删除。

注：当前 kernel 只在根 run（`spawn_id is None`）发出 complete
`ResponseEvent`（`matmaster/core/agent.py` 的 `is_root_run` 分支），子 agent
的持久化 `response` 在当前主路径下不会出现。本例是防御性约束，不是当前可
观测行为，实施时不需要寻找子 agent response 的产生路径。

### 6.5 救援式自然结束，最终文本来自更早 turn，仍去重

最后一个 turn 无可见输出但 `finish_reason=stop` 时，kernel 用更早 turn 的
`last_emitted_content` 收尾。携带最终文本的那条 `response` 是终态副本，应
当删除：

输入历史：

```text
response(task=t1, spawn=None, content=结论 X)
tool_call(task=t1, spawn=None, name=bash)
tool_result(task=t1, spawn=None, result=ok)
run_result(task=t1, spawn=None, content=结论 X)
```

回放输出：

```text
tool_call(task=t1, spawn=None, name=bash)
tool_result(task=t1, spawn=None, result=ok)
run_result(task=t1, spawn=None, content=结论 X)
```

目的：turn index 匹配方案在此路径必然漏删导致双显；文本相等方案正确处理。

### 6.6 终态文本为空，不删除

cancelled / max_turns 等终态的 `final_content=None` 落库为空串：

输入历史：

```text
response(task=t1, spawn=None, content=部分草稿)
run_result(task=t1, spawn=None, content=)
```

回放输出：

```text
response(task=t1, spawn=None, content=部分草稿)
run_result(task=t1, spawn=None, content=)
```

目的：空文本不参与删除，避免误删运行中产生的可见内容。

## 7. 实现边界

### 7.1 修改文件

主要修改：

- `src/services/stream_sse_filter.py`

需要补测试：

- `tests/test_stream_replay_skill_hit.py`
- `tests/test_chat_stream_direct.py` 或相邻 replay 测试文件

不需要修改：

- `matmaster/integration/sse_handler.py`
- `matmaster/integration/persistence_handler.py`
- `matmaster/types/events.py`
- `src/services/stream_service.py`
- `src/worker/agent_worker.py`

### 7.2 建议 helper

在 `src/services/stream_sse_filter.py` 内新增小型私有 helper：

```python
def _replayed_response_text(event: dict) -> str | None:
    ...


def _replayed_run_result_text(event: dict) -> str | None:
    ...
```

这些 helper 只负责读取当前持久化 public payload 并做空值归一（复用
`normalize_visible_response_text`），不引入旧格式兼容。

### 7.3 建议算法

新算法分两步：

1. 先按 `(task_id, spawn_id)` 收集 replayable `run_result` 的规范化最终
   文本；同组多个 terminal 按事件顺序保留为列表。
2. 再扫描同组 `response`，为每个 terminal 文本删除最后一个尚未删除的文本
   相等 `response`，记录删除下标。

删除下标选择规则：

```text
for each run_result group:
    for each terminal final_text (event order):
        candidates = responses in same group where
            normalized(response_text) == final_text
            and index not already removed
        remove only the last candidate
```

最后输出时跳过这些下标。

每个 terminal 只删除一个最后匹配项，是为了避免同一文本异常重复写入时过度
删除。正常情况下 kernel 每 turn 至多发出一条 complete response，最终答案
副本恰好是最后一个匹配项。

## 8. 测试要求

样本中的 `turn_index` / `num_turns` 等持久化字段可以照真实形态保留，但不参
与判定。

### 8.1 单元测试：最终 response 仍被去重

覆盖原有行为，防止回归为重复最终答案：

```python
events = [
    {
        "task_id": "t1",
        "spawn_id": None,
        "type": "response",
        "source": "MatMaster",
        "content": {"content": "final", "turn_index": 0},
    },
    {
        "task_id": "t1",
        "spawn_id": None,
        "type": "run_result",
        "source": "MatMaster",
        "content": {
            "content": "final",
            "status": "completed",
            "reason": "natural",
            "num_turns": 1,
        },
    },
]
```

期望类型：

```text
run_result
```

### 8.2 单元测试：tool_call 前中间 response 保留

覆盖本问题的核心失败样本：

```python
events = [
    {
        "task_id": "t1",
        "spawn_id": None,
        "type": "response",
        "source": "MatMaster",
        "content": {"content": "I will inspect files.", "turn_index": 0},
    },
    {
        "task_id": "t1",
        "spawn_id": None,
        "type": "tool_call",
        "source": "MatMaster",
        "content": {"name": "bash"},
    },
    {
        "task_id": "t1",
        "spawn_id": None,
        "type": "tool_result",
        "source": "MatMaster",
        "content": {"result": "ok"},
    },
    {
        "task_id": "t1",
        "spawn_id": None,
        "type": "response",
        "source": "MatMaster",
        "content": {"content": "final", "turn_index": 1},
    },
    {
        "task_id": "t1",
        "spawn_id": None,
        "type": "run_result",
        "source": "MatMaster",
        "content": {
            "content": "final",
            "status": "completed",
            "reason": "natural",
            "num_turns": 2,
        },
    },
]
```

期望类型：

```text
response
tool_call
tool_result
run_result
```

### 8.3 单元测试：救援式自然结束的终态副本被删除

对应 §6.5：`response(content=结论 X)` + tool_call + tool_result +
`run_result(content=结论 X)`，期望删除该 response，输出
`tool_call / tool_result / run_result`。

### 8.4 单元测试：终态文本为空时不删除

对应 §6.6：`run_result.content.content` 为 `''`（cancelled 形态）时，同组
response 全部保留。

### 8.5 单元测试：str 形态 response content 参与比较

`response.content` 为纯字符串（无 usage / model identity 的合法持久化形
态）且与终态文本相等时，仍被删除。

### 8.6 单元测试：子 agent 隔离保持

现有子 agent 隔离测试改为 child response 文本与 parent `run_result` 终态文
本相等的形态，继续断言 child response 不被 parent run_result 隐藏，证明隔
离来自 `(task_id, spawn_id)` 分组而非文本差异。

### 8.7 集成测试：send stream replay 帧顺序

构造 `events_service.get_session_events.return_value`，覆盖刷新后 send
stream replay：

```text
status
query        # 旧历史 query
response     # 中间 response，文本与终态不同，保留
tool_call
tool_result
run_result   # 终态，文本与已删除的 final response 相同
query        # 新 query
```

其中旧历史里的 final response 被去掉，中间 response 保留，新 query 仍在历史
回放之后发送。

### 8.8 现有测试应继续通过

现有 dedupe 测试里 `run_result.content` 的纯字符串简写需要更新为持久化
dict 形态（含 `content` 键）；文本与终态不同的 response 期望从隐藏改为保
留。至少运行：

```bash
uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/test_chat_stream_direct.py::test_generate_send_stream_replay_prefers_run_result_over_response \
  tests/test_chat_stream_direct_response_figures.py
```

如果实现只改 `stream_sse_filter.py`，还应补跑：

```bash
uv run pytest tests/matmaster/integration/test_sse_handler_mode_filter.py
```

确认没有误改实时 SSE handler 行为。

## 9. 风险与取舍

### 9.1 最终答案重复显示风险

如果最终 `response` 与 `run_result` 的文本不一致（异常历史，或终态文本被
非常规路径改写），新逻辑会同时保留两者，可能产生重复最终答案。

这是有意取舍：文本不一致时无法断定该 response 是终态副本，误删中间
response 比重复显示一条最终答案更严重。开发期旧数据可通过外部脚本或清空
事件表处理。

### 9.2 文本等价判断过窄

只做严格文本相等，可能保留一些语义相同但格式不同的最终 response。例如一个多了
尾部换行，一个没有。

这是有意取舍：严格匹配更安全，不会误删用户可见的不同文本。后续如确实需要更宽
的归一化，可以单独设计，不在本修复中扩展。

### 9.3 多个 run_result 的异常历史

正常情况下同一 `(task_id, spawn_id)` 只有一个业务终态 `run_result`。如果异常
历史中出现多个 `run_result`，实现可按事件顺序分别计算候选，但每个 terminal
只删除一个最后匹配的 response。不要引入复杂恢复逻辑。

## 10. 验收标准

1. agent 运行中刷新仍能看到已落库的中间 `response`。
2. agent 结束后再次刷新，tool_call 前的中间 `response` 仍可见。
3. agent 结束后再次刷新，最终答案不因 `response` + `run_result` 重复显示。
4. 子 agent 的 `response` 不被父 agent 的 `run_result` 隐藏。
5. `response_figures` 继续保留，不受终态去重影响。
6. `stream_closed` 的 payload 和发送顺序不变。
7. 所有定向 replay 测试通过。

## 11. 后续计划入口

本 spec 通过评审后，实施计划应拆为两步：

1. 先写失败测试，复现结束后刷新丢失 tool_call 同级 `response` 的问题。
2. 再修改 `_dedupe_replayed_terminal_events()`，只删除最终答案级重复 response。

实现时应保持改动集中，不做文件拆分，不加入旧数据兼容分支。

## 12. 修订记录

2026-06-11 评审后修订（评审：Claude，逐条对照代码验证）：

1. 删除原删除条件 3（turn_index 匹配）及 `num_turns - 1` 推导链。救援式自
   然结束路径（`agent.py` 中 `last_emitted_content` 收尾分支）字段齐全但
   `turn_index < num_turns - 1`，按 turn index 匹配必然漏删；逐终态路径推
   演表明该条件没有额外保护作用。连带删去 §7.2 中的三个 turn index 相关
   helper，缺字段矩阵减半。
2. §5.4 明确 `response.content` 的 str 形态是当前合法持久化形态（
   `_response_public_content()` 无 usage / model identity 时的早退分支），
   参与文本比较，而非按旧格式保护性处理。
3. §5.3 从"缺字段"改为"缺文本/空文本"规则：terminal 落库恒有 `content`
   键（最差为 `''`），真正需要防护的是空串；归一化后为 None 即保留。
4. §6.4 标注子 agent 示例为防御性约束：当前 kernel 仅根 run 发出 complete
   `ResponseEvent`（`is_root_run` 分支），该输入形态在主路径下不会出现。
