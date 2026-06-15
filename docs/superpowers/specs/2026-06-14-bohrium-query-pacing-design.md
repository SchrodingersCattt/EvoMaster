# Bohrium query 内置 pacing 设计

> 日期：2026-06-14
> 状态：设计稿，已获用户批准写入。
> 范围：修复短作业等待时 agent 高频调用 `Bohrium(action="query")` 导致 token 浪费的问题。保留 `query` action，不恢复 `poll` alias，不新增兼容入口。

## 1. 背景

`BohriumTool` 曾经历过三代状态查询设计：

1. 早期 `poll` 默认为单次查询，可通过 `wait=true` 进入 `time.sleep` 等待循环。
2. 后来 `poll` 改为内置短轮询：每次调用最多阻塞约 60 秒，每 5 秒查一次，直到作业离开运行态或窗口耗尽。
3. 当前 `query` 取代 `poll`，变成严格的 single-shot status query。长程监控交给后台 monitor，工具本身不阻塞、不内部等待。

当前生产出现了一个新的成本问题：提交一个预计约 2 分钟的 Bohrium job 后，agent 选择留在本轮等待该 job 完成。由于 `query` 每次立即返回 Running，agent 在 2 分钟内连续调用约 20 次 `query`。每一次工具结果都会回到模型循环，造成明显 token 浪费。

这不是单纯的 Bohrium API 调用次数问题，而是等待控制流被放在模型循环里。只靠 prompt 要求 agent 等 30 到 60 秒再查并不稳定，因为模型仍可能在看到 Running 结果后立刻继续查询。

## 2. 目标

- 把短时间等待的节流从模型循环下沉到工具代码，减少重复 query 带来的 token 成本。
- 保留 `query` action 名称和当前主要契约：查询某个 job 的状态，不下载结果。
- 让第一次 query 保持快速返回，避免 submit 后 sanity-check 被无故拖慢。
- 对同一 run 内、同一 job 的重复 running query 做内置 pacing，让 agent 即使连续调用也只能按最小间隔获得新结果。
- 保持后台 monitor 仍然是长程作业监控的主路径；工具 pacing 只解决 agent 本轮等待短作业时的成本问题。

## 3. 非目标

- 不恢复 `poll` action，不保留 `poll` alias。
- 不恢复 `wait=true`、`max_wait_seconds`、`poll_interval_seconds` 这一组外部参数。
- 不把所有 `query` 默认改成阻塞 60 秒。第一次查询必须仍然快速返回。
- 不把状态塞进 `run_meta`，不新增 RuntimePorts 字段，不引入服务 callback 或兜底 dict。
- 不改变 Bohrium job ledger 的状态机，不改 DB schema，不内联迁移逻辑。
- 不用 cached response 作为主要方案。cached response 能减少远端 API 调用，但仍会把工具结果返回给模型，不能解决 token 浪费的根因。

## 4. 决策

采用改良版内置 pacing：`query` 仍然是唯一状态查询 action，但在 `execute_with_context` 层对同一 run 内的重复 query 做节流。

核心规则：

1. 第一次 query 某个 job 时立即查平台并返回。
2. 如果同一 run 内再次 query 同一个仍处于运行态的 job，工具先等待到最小查询间隔，再查平台并返回。
3. 如果等待后作业已经 Finished、Failed、Stopped 或其他非运行态，立即返回终态，供 agent 下载或处理失败。
4. 如果等待后仍处于运行态，返回最新状态，并记录本次查询时间。
5. 每次 query 调用内部最多等待一个有界窗口，避免工具调用无限阻塞。

推荐初始配置：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `BOHRIUM_QUERY_MIN_INTERVAL_SECONDS` | 30 | 同一 run 内同一 job 两次真实 query 的最小间隔 |
| `BOHRIUM_QUERY_MAX_WAIT_SECONDS` | 60 | 单次 paced query 在已占用工具资源槽后的最长等待封顶；默认配置下实际等待由 30 秒 min interval 决定，此值主要约束更大 env override |

这组默认值面向 2 到 5 分钟的 quick job。以 2 分钟 job 为例，当前行为可能是约 20 次模型可见 query；新行为预期降到第一次立即确认，后续约每 30 秒一次模型可见结果，通常 3 到 5 次内完成。

## 5. 架构

改动集中在 `matmaster/tools/builtin/bohrium_tool/tool.py`。`execute_with_context` 当前由 `BuiltinTool` 基类提供，`BohriumTool` 需要在本文件中 override 它：`query` 走 pacing 分支，非 `query` action 必须继续透传到 `_execute`，保持 submit / download / kill / list actions 的现有行为。

```text
agent calls Bohrium(action="query", job_id=J)
  -> execute_with_context
       -> 读取 runner_state 中 Bohrium query pacing 状态
       -> 如果 J 是同 run 内首次 query，立即执行
       -> 如果 J 近期已 query 且上次状态仍是运行态，asyncio.sleep 到最小间隔
       -> asyncio.to_thread(self._execute, arguments)
            -> _query 单次 get_job_detail + ledger record_poll + log_tail
            -> ToolResult.meta 输出 bohrium_running / bohrium_status_code
       -> 根据 ToolResult.meta 更新 pacing 状态
       -> 返回 ToolResult 给模型
```

状态只存在于 `ToolRunnerState`，生命周期是一次 agent run。这样命中当前生产症状：同一轮里 agent 无缝重复 query。跨 run 的长期节流继续交给 Bohrium ledger 的 `next_poll_at` 与后台 monitor。

### 状态结构

在 `runner_state` 中使用一个专用 key，例如 `bohrium_query_pacing`：

```python
{
    normalized_job_id: {
        "last_checked_monotonic": float,
        "last_status": str,
        "running": bool,
    }
}
```

实现时不需要新增 typed RuntimePorts，也不需要把该状态写入 DB。`ToolRunnerState` 已用于工具运行期状态，且要求只在 asyncio event loop 线程访问。pacing 判断和状态更新都放在 `execute_with_context` 层，符合这个约束；同步 `_query()` 不直接访问 runner_state。

### 为什么不放进 `_query()`

`_query()` 是同步工具实现，负责参数校验、平台查询、ledger 写回和结果组装。pacing 需要 `asyncio.sleep`，也需要访问 `exec_ctx.runner_state`。因此应放在 async 的 `execute_with_context` 层：

- 避免在同步路径里 `time.sleep` 阻塞线程。
- 保持 `_query()` 仍然是单次平台查询，便于测试和复用。
- 让 direct `_execute()` 调用保持无 pacing 的低层语义。

## 6. 数据流与算法

### 6.1 查询前判断

`execute_with_context` 收到 `action == "query"` 后：

1. 解析 `job_id` 的原始字符串，缺失时不做 pacing，直接走 `_execute` 返回原有错误。
2. 从 `runner_state` 读取 pacing dict；没有则创建。
3. 使用 `str(raw_job_id).strip()` 作为 `normalized_job_id`，查找对应记录。
4. 如果没有记录，直接执行 `_query()`。
5. 如果上次记录不是运行态，直接执行 `_query()`，不 sleep。终态重复查询不应被延迟。
6. 如果上次记录是运行态，计算距离上次真实查询的间隔。
7. 若间隔小于 `min_interval`，等待 `min(min_interval - elapsed, max_wait)`。
8. 等待结束后执行 `_query()`。

第一版按规范化 `str(raw_job_id).strip()` 做 key 即可，因为同一 run 内同一个 job 的 query 参数来自同一个 tool result，实际 collision 风险很低。`sandbox` 与非 sandbox 的同字符串 job_id 在同一 run 内混用并不是当前 Bohrium tool 的正常路径；如果后续发现真实碰撞，再把 key 扩展为 `(sandbox, job_id)`。

### 6.2 查询后更新：读取 ToolResult.meta

`_query()` 已经在内部拿到了平台状态码，并用 `code in RUNNING_CODES` 判断运行态。因此不要在 pacing 层重新解析 `ToolResult.content` 里的展示文案，也不要用字符串集合猜测运行态。

`_query()` 成功返回时在 `ToolResult.meta` 带出机器可读信号：

```python
return ToolResult(
    status="success",
    content=json.dumps(result_payload, ensure_ascii=False),
    meta={
        "bohrium_running": code in RUNNING_CODES,
        "bohrium_status_code": int(code),
    },
)
```

pacing 层只读 meta：

```python
normalized = normalize_tool_result(result)
running = bool(normalized.meta.get("bohrium_running"))
```

更新规则：

- `normalized.status != "success"`：不记录 running pacing，避免错误结果把后续重试延迟。
- `meta["bohrium_running"] is True`：记录 `running=True` 和当前 `time.monotonic()`。
- `meta["bohrium_running"]` 缺失或为 False：记录 `running=False`，后续重复查询不 sleep。

这样避免 JSON 往返、大小写问题和状态文本漂移。`content` 继续只承担用户可见结果，`meta` 承担工具内部控制信号。

### 6.3 取消与阻塞语义

sleep 使用 `await asyncio.sleep(...)`。如果上层 run 被取消，任务取消会打断 sleep；工具不吞 CancelledError。

单次 paced query 的等待时长为 `min(min_interval - elapsed, max_wait)`。默认 `min_interval=30`、`max_wait=60` 时，实际等待上限是 30 秒；`max_wait` 的真实作用是当环境变量把 min interval 调大时，仍然封顶一次工具调用的挂起时间和资源槽占用时间。

## 7. Prompt 调整

当前 prompt 中 quick jobs 段落写着：

```text
if a job is expected to finish within a few minutes, you MAY wait for it in-turn:
sleep 30-60 s between polls or do other pending work
```

这会把等待策略交给 agent。实现 pacing 后应改为：

- 默认仍然不要为长作业等待完成，后台 monitor 会接管。
- quick job 可以在本轮继续 query，但 Bohrium 工具会自动对重复 query 做 pacing；不要用 Bash sleep 管理 Bohrium 查询节奏。
- 如果还有其它待办，agent 应先做其它待办，而不是先发 query 等结果；一旦发出 paced query，本轮会等待该工具调用返回，期间不能继续做其它事。
- 仍然最多等待约 5 分钟；超过后交给后台 monitor。

这样 prompt 与代码边界一致：agent 可以表达意图，工具负责控制节奏。

## 8. 错误语义

- 参数错误：直接返回原有 ToolResult error，不 sleep，不更新 pacing 状态。
- Bohrium API 错误：返回原有 query error，不更新 pacing 状态，允许 agent 或用户稍后重试。
- ledger 写失败：仍由 `_safe_ledger` 吞掉，不影响用户可见 query 结果，也不影响 pacing 记录。
- meta 缺失：视为非运行态，不触发后续 pacing；这能让非 query action、legacy string 结果或异常包装路径自然退化为无节流。
- 终态查询：不 sleep，便于用户反复确认和下载前检查。
- `download`、`kill`、`list_images`、`list_machines` 不参与 pacing。

## 9. 与后台 monitor 的关系

本设计不削弱后台 monitor。分层如下：

| 场景 | 负责机制 |
|---|---|
| 长作业无人等待 | 后台 monitor 根据 ledger `next_poll_at` 巡检并触发交付 |
| submit 后快速 sanity-check | 第一次 `query` 立即返回 |
| agent 本轮等待 2 到 5 分钟 quick job | 工具内置 pacing 限制重复 query 频率 |
| 作业终态后的结果交付与 ack | 既有 delivery snapshot / observed terminal / worker ack 链路 |

pacing 状态不跨 run 持久化，避免和 monitor 的调度状态形成双事实源。真实作业状态仍以 Bohrium 平台和 `bohrium_jobs` ledger 为准。

## 10. 测试计划

新增或调整 focused tests，不做大面积重构：

- `BohriumTool.execute_with_context` 连续 query 同一 running job 时，第二次调用会等待到最小间隔后再执行底层 `_execute`。
- 第一次 query 不等待。
- 上次状态为 Finished 或 Failed 时，重复 query 不等待。
- `_execute` 或 `_query` 返回 error 时不记录 running pacing，下一次不被错误状态延迟。
- 没有 `runner_state` 时，query 退化为当前 single-shot 行为。
- `_query` 成功返回时带 `meta["bohrium_running"]` 和 `meta["bohrium_status_code"]`；pacing 层不解析 `content` 中的 `status` 字符串。
- prompt 断言更新：不再建议 agent 自己用 Bash sleep 管理 Bohrium query 节奏，而是说明工具会自动 pacing。

测试中不要真实 sleep 30 秒。实现应允许通过类常量或 helper 注入较小间隔，或者 monkeypatch `asyncio.sleep` 与 monotonic clock。

## 11. 迁移与清理

- 直接修改 `query` 语义，不新增兼容 action。
- 不改已有历史记录，不改 DB。
- 不需要外部迁移脚本。
- 文档和 prompt 中删除对 agent 自行 sleep 的鼓励，避免与工具 pacing 冲突。

## 12. 风险与取舍

### 风险一：query 不再严格立即返回

严格说，重复 running query 会变成有界等待。这是有意改变。第一次 query 仍立即返回，终态 query 仍立即返回，因此用户主动查看当前状态和 submit 后 sanity-check 不受明显影响。只有同一 run 内对同一 running job 的高频重复 query 会被延迟。

### 风险二：短作业完成提示最多延迟到下一个 pacing 窗口

默认最小间隔 30 秒时，2 分钟 job 完成后 agent 可能最多晚约 30 秒看到 Finished。相比 20 次 query 的 token 成本，这个延迟可以接受。若后续产品更重视短作业实时性，可把默认间隔调到 15 秒。

### 风险三：paced sleep 占用 bohrium-api counted 槽

`BohriumTool` 声明 `ResourceClaim(resource="bohrium-api", mode="counted", max_concurrent=3)`。`FullToolRunner` 会先 acquire 资源槽，再调用 `tool_executor`，最后 release。因此在 `execute_with_context` 内部 `asyncio.sleep` 会占用本 run 内一个 `bohrium-api` counted 槽。

影响范围可接受：

- scheduler 是每个 run 一个实例，资源槽不会跨 agent run 或跨会话互相占用。
- 首次 query 不 sleep，所以批量 sanity-check 每个 job 查一次不会额外占槽。
- 只有同一轮里并发重复 query 多个运行态 job 时才可能互相影响。

这也是 `BOHRIUM_QUERY_MAX_WAIT_SECONDS` 的重要语义：它不仅限制等待时间，也限制一次 paced query 持有 `bohrium-api` 槽的最长时间。若后续要求等待不占槽，需要调整 scheduler acquire 包裹范围，属于更大的架构改动，不纳入本次。

### 风险四：同一 batch 内并发重复 query 不去重

pacing 是按每次工具调用进入 `execute_with_context` 时的 runner_state 记录节流。若同一 batch 中两个并发 query 同时读到相同的旧记录，它们可能各自 sleep 后都打平台；本设计不把并发重复查询合并成一次共享请求。该边界可接受，因为触发条件是同一 run 内并发重复 query 同一 running job，正常工作流应避免这种调用形态。

### 风险五：多 job 批量提交时状态键过多

pacing dict 是 run 内临时状态，键数等于本轮被 query 过的 job 数。批量场景下 prompt 已要求只抽查少数 job，不应查询所有 job。即使有几十个 key，也只存在于一次 run 生命周期内，可接受。

## 13. 推荐实施顺序

1. 在 `BohriumTool` 中 override 基类 `execute_with_context`：`action == "query"` 走 pacing 分支，非 query action 继续 `asyncio.to_thread(self._execute, arguments)` 透传。
2. 在 `_query` 成功返回处写入 `ToolResult.meta["bohrium_running"] = code in RUNNING_CODES` 和 `ToolResult.meta["bohrium_status_code"] = int(code)`。
3. pacing 层用 `normalize_tool_result` 保留 meta，并只根据 meta 更新 runner_state，不解析 `content`。
4. 用 `src.utils.constant.env_int` 读取 `BOHRIUM_QUERY_MIN_INTERVAL_SECONDS` / `BOHRIUM_QUERY_MAX_WAIT_SECONDS`，默认 30 秒 / 60 秒，并支持测试覆盖。
5. 更新 prompt 的 quick jobs 段落。
6. 添加 focused tests 覆盖 pacing、meta 信号、非 query action 透传和 prompt 文案。
7. 运行 targeted pre-commit 或 pytest，确认无格式和行为回归。
