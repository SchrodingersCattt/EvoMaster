# Thought 提前入库设计

- 日期: 2026-06-14
- 状态: 已批准(选项 B), 待实现
- 范围: 仅内核流式层与内核循环, 零 schema 改动
- 修订: 经一轮代码审查(F1-F6)后改为"选项 B", 见第 10 节

## 1. 背景与问题

实际运行中, `evo_chat_events` 的入库顺序偶发错位, 典型表现为按 `(created_at, id)`
读出时顺序为: response → run_result → response 之前的 thought → stream_closed。

根因(详见入库链路分析, 此处摘要):

- 经 `RunEventFanout` 落库的事件走 fire-and-forget 后台任务
  (`asyncio.create_task` + `asyncio.to_thread`), 各自新建连接、并发 INSERT,
  落库顺序由线程池完成顺序决定, 与事件产生顺序解耦。
- `add_event` 写入的 `created_at` 是裸 `NOW()`, 秒精度。同一秒内多条事件
  `created_at` 相同, 排序退化为只看自增 `id`, 而 `id` = INSERT 物理执行顺序。
- `thought(complete)` 与 `response(complete)` 在同一轮 LLM 流结束后**背靠背**
  产生、并发入库; `thought` 的 reasoning 文本通常是全场最大的 payload,
  `json.dumps` + INSERT 更慢, 系统性地拿到更大的 id, 于是排到 response 之后,
  甚至排到更晚产生但 payload 更小的 run_result 之后。

关键洞察: 本项目是重 IO 的 agent, reasoning 在 LLM 流的**前段**(reasoning 阶段)
就已完整, 到 content 流完之间存在数秒甚至数分钟的 IO 时间差。但当前 thought 的
可持久化事件被压到整轮流结束后才产生, 白白放弃了这段时间差。

## 2. 目标与非目标

目标:

- 把绝大多数轮的 `thought(complete)` 入库时机, 从"整轮 LLM 流结束后"提前到
  reasoning 流刚结束的 transition 点, 使 **thought complete 早于同轮 response
  complete 入库**(靠真实 IO 时间差, 不靠人造序号)。
- 抗崩溃: 长流式 content 阶段中途崩溃/被 kill/部署中断时, 完整 reasoning 已落库。
- 零 schema 改动。实时 SSE 流式增量不变。

非目标:

- 不解决其他事件之间的乱序(`assistant_state` / `tool_call` / `tool_result` /
  `run_result` / `stream_closed`)。那是全局 `dispatch_seq` 定序的范畴, 本次不做。
- 不改 `response` 的入库时机。
- 不改持久化、SSE handler、event_payloads、回放排序等下游机制。

## 3. 事实基线(已逐条核对真实代码)

`stream_llm_items` (`matmaster/core/agent_llm_stream.py`) 单个 chunk 的处理顺序:

1. `chunk.reasoning_content` → yield `thought(streaming)`。
2. `chunk.content` → 先进入 response streaming 分支并 yield `response(streaming)`
   (释放 sentinel 前缀后), 见 `agent_llm_stream.py:133`。
3. **之后**在累积阶段才发现 `producing_reasoning` 为真, yield `thought(segment_end)`
   并把 `producing_reasoning` 置 False, 见 `agent_llm_stream.py:149`。
   即 reasoning→content 的 segment_end **晚于**首个 `response(streaming)`。
4. `chunk.tool_call_deltas` → 同样先发 reasoning→tool_call 的 segment_end
   (`agent_llm_stream.py:167`), 再累积 tool_call deltas。
5. 流末 `finally`(`agent_llm_stream.py:206`)对未收尾的段补发 `segment_end` /
   `end`, 该 `finally` 在**正常结束、`LLMError`、取消、`GeneratorExit`** 所有路径
   都会执行; 取消时是先走完 `finally` 再抛 `_KernelStopRequested`。

事件落库与过滤:

- 流式事件 `stream_state ∈ {start, streaming, segment_end, end}` 被
  `PersistenceHandler` 全部过滤, 不入库; SSE 据此实时显示 thought。
- 真正入库的 `thought(complete)` 现在只在 `agent.py` 的 `_run_items` 里、拿到完整
  `llm_response` 之后产生(`agent.py:373` 一段, 唯一产生点), 带
  `turn_usage / total_usage / turn_index`。
- `complete` 事件: live SSE skip(`sse_handler.py:120`, `stream_state in
  {complete, segment_end}`), 持久化放行。即 complete 是纯持久化事件。

持久化只存 `_public_content_for_event` 的产物(`persistence_handler.py:63`):
对 thought, **当 `turn_usage`/`total_usage` 都为空时直接返回纯文本 content**
(`event_payloads.py:241`), `turn_index` 等结构字段不会进 DB。

## 4. 设计(选项 B)

### 4.1 分层职责

流式层 (`agent_llm_stream.py`) —— 判定"reasoning 何时完成", 只在流推进中发:

- 在两个 transition 点(reasoning→content `:149`、reasoning→tool_call `:167`)的
  `if producing_reasoning:` 块内, 在现有 `segment_end` 旁额外 yield 一条
  `stream_state="complete"` thought, content = 当前完整累积 reasoning,
  **不带 usage、不带 turn_index**。
- 用流内本地标志 `reasoning_complete_emitted` 保证一次流最多发一条
  (防御 interleaved reasoning: content 后又出现 reasoning 时 `producing_reasoning`
  会再次为真, 标志阻止第二次 emit)。
- `finally`(`:206`)**不发 complete**, 只保留现有 ephemeral `segment_end` / `end`。
  这是 F1 的修复: 异常 / 取消 / `GeneratorExit` 路径绝不持久化半截 reasoning。

内核层 (`agent.py` 的 `_run_items` 流式消费循环) —— 去重 + 流末兜底:

- 每轮循环体内、`_call_llm_streaming` 调用前 reset `thought_persisted_this_turn = False`。
- 消费循环拦截流式上来的 `complete` thought: 若 `thought_persisted_this_turn` 为
  False 则置 True 并 yield(入库); 否则丢弃(retry 同轮重复)。**不补 turn_index**
  (补了也会被 `event_payloads` 丢弃, 见 F2)。
- 流末兜底: 拿到 `llm_response` 后, 若 `not thought_persisted_this_turn and
  response.reasoning_content`, emit 一条 `complete` thought, content = 最终
  `llm_response.reasoning_content`, 同样不带 usage/turn_index。覆盖纯 reasoning /
  reasoning_only 轮(这类轮无 content/tool_call transition, 不会提前 emit)。
- 删除原 `agent.py:373` 处无条件的 thought complete 产生(被上面的拦截 + 兜底取代)。
  `response(complete)` 的产生逻辑完全不动。

伪代码(消费循环):

```
thought_persisted_this_turn = False
async for item in self._call_llm_streaming(...):
    if item.llm_response is not None:
        llm_response = item.llm_response
    elif isinstance(item.event, ThoughtEvent) and item.event.stream_state == "complete":
        if not thought_persisted_this_turn:
            thought_persisted_this_turn = True
            yield item                       # 入库; 纯文本, 不补 turn_index
        # else: retry 同轮重复, 丢弃
    else:
        yield self._with_model_identity(item, state)
...
# 流末兜底(原 thought complete 产生处)
if not thought_persisted_this_turn and response.reasoning_content:
    yield <complete thought, content=response.reasoning_content, 无 usage/turn_index>
```

### 4.2 路径覆盖

- reasoning→content: transition 点提前(`:149`)。注意它落在首个 `response(streaming)`
  **之后**(见第 3 节); 但 `response(streaming)` 不入库, 故 thought complete 仍是
  该轮第一条入库的模型输出, 早于流末的 `response(complete)`。目标因此达成。
- reasoning→tool_call: transition 点提前(`:167`)。该路径更有价值, 后续 tool 执行
  可能很久。
- 纯 reasoning / reasoning_only(无 content 无 tool_call): transition 不触发, 走
  4.1 的流末兜底, 用最终 reasoning。
- 无 reasoning 的轮: 流式层不发、兜底条件不成立, 不 emit —— 与现状一致。

### 4.3 字段

- 所有 `complete` thought(transition 提前 + 流末兜底)统一**不带**
  `turn_usage / total_usage / usage_vendor / turn_index`, 在 DB 中是纯文本 content。
- 依据(F2): `event_payloads` 对无 usage 的 thought 只存纯文本, `turn_index` 本就
  进不了 DB; 且核查无任何消费方读取 DB 中 thought 的 `turn_index`(历史恢复
  `chat_history.py:487` 仅取文本累加, 回放仅展示文本)。该轮 usage 仍由
  `response / tool_call / assistant_state` 携带, 信息不丢。

## 5. Retry 与正确性

不变量(改动前): "持久化的 reasoning == 最终采纳 attempt 的 reasoning", 流末用
`response.reasoning_content`, 与 response 匹配。历史恢复据此把 reasoning 配回
`AssistantMessage.reasoning_content`(`chat_history.py:503`), DeepSeek/Qwen transport
再把它作为同级字段回灌 provider(`chat_completions.py:630`)。提前入库会冲击该不变量,
因此 retry 处理是本设计的正确性核心(F3)。

选项 B 的处理:

- `incomplete` retry(含 reasoning_only, 即 attempt 只产 reasoning 无答案 —— 差异
  最大的场景): 这类 attempt 没有 content/tool_call, **不触发** transition 提前 →
  走流末兜底 → 用最终采纳 attempt 的 reasoning → **零污染**。
- `LLMError` retry(reasoning 已完整、已进入 content/tool 流, 中途断连被重试):
  transition 点已提前入库首个 attempt 的 reasoning, per-turn 标志使流末不再 emit。
  **残留**: 入库的是首个 attempt 的 reasoning, 与最终 response 可能不完全一致, 并会
  随历史恢复回灌 provider。

LLMError 残留是**已知接受的 trade-off**: 触发条件苛刻(reasoning 完整 + 已进
content/tool + 网络断 + 被重试 + 两次 reasoning 不同), 且这种 attempt 已有实质产出、
其 reasoning 接近最终。不引入 DB 覆盖/作废机制(复杂度远超收益)。

两层去重分工(正交, 缺一不可):

- 流式层 `reasoning_complete_emitted`(单次 `stream_llm_items` 内): 防同一 attempt 内
  interleaved reasoning 导致的重复 emit。
- 内核层 `thought_persisted_this_turn`(轮循环体内 reset, 覆盖该轮所有 retry attempt;
  retry 在 `_call_llm_streaming` 内部, 不跨出轮循环体): 防跨 attempt 重复入库。

## 6. 不变量(均不改)

- `PersistenceHandler`、`SSEHandler`、`event_payloads`、回放排序键全部不动。
- `complete` thought 继续走现有过滤: live SSE skip、持久化放行。
- 零 schema 改动。

前端影响的准确表述(F5): 实时 SSE 流式增量**不变**(complete 始终被 live SSE skip);
但历史回放过滤器不丢弃 thought(`stream_sse_filter.py:21` 的丢弃集合不含 thought),
所以**刷新 / 重连 / 崩溃后回放会更早出现 persisted thought** —— 这正是抗崩溃目标的
体现, 前端验收需按此预期, 不能描述为"完全不变"。

## 7. 文件改动清单

- `matmaster/core/agent_llm_stream.py`: 两个 transition 点额外 emit `complete`
  thought + `reasoning_complete_emitted` 标志; `finally` 保持不发 complete。
- `matmaster/core/agent.py`: 消费循环拦截 complete thought(per-turn 去重, 不补
  turn_index); 原无条件 thought complete 产生改为"未提前过则用最终 reasoning"的
  流末兜底。
- 测试: 见第 8 节(含必须迁移的旧基线)。

## 8. 测试点

必须迁移的旧基线:

- `tests/matmaster/core/test_agent_kernel_stream.py:317`
  `test_segment_end_on_reasoning_to_content` 现断言 `thought_completes == []`;
  改动后 transition 点会产生 complete, 该断言需更新为"transition 点产生恰好一条
  complete, 且其 content 为完整 reasoning"。

新增/补充用例:

- 三类完成路径: reasoning→content、reasoning→tool_call 各提前 emit 恰好一条;
  纯 reasoning / reasoning_only 轮走流末兜底 emit 恰好一条。
- F1: retryable `LLMError` / 取消 / `GeneratorExit` 中途退出后, `finally` **不**
  持久化 complete(不产生半截 reasoning 的 complete)。
- F3: `incomplete`(reasoning_only)retry 后, 最终入库的 reasoning 来自被采纳的
  attempt(走兜底); 并对 `LLMError` retry 残留补一条断言, 明确"入库首个 attempt 的
  reasoning"是当前接受的行为(防止后续误判为回归)。
- F2: complete thought 入库后 DB content 为纯文本, 不含 `turn_index` 结构。
- 顺序: 在有 content 流式间隔的场景, thought complete 先于 response complete 被
  yield(内核生成顺序层面断言, 不依赖 DB)。
- interleaved reasoning(reasoning→content→reasoning): 流内只 emit 一条 complete。

(用户全局规则中"严禁加测试"限定于删死代码/移除兼容场景; 本次为功能行为变更,
按 TDD 补测。)

## 9. 验证

- `uv run python -c "import matmaster.core.agent, matmaster.core.agent_llm_stream"`
- `uv run --extra dev pytest tests/matmaster/core/test_agent_kernel_stream.py`
  及内核相关用例。
- 既有 lint(ruff)。

## 10. 修订记录(代码审查 F1-F6)

- F1[P0]: `finally` 不再产生持久化 complete, 改为只在流推进中的 transition 点 +
  agent 流末兜底, 杜绝异常/取消路径写入半截 reasoning。
- F2[P1]: 删除"补 turn_index / 按 turn_index 去重"承诺; complete thought 在 DB 中
  为纯文本(无消费方读取 DB thought 的 turn_index)。
- F3[P1]: 由"首个 attempt 无条件入库"改为"transition 提前 + 流末兜底", 使
  incomplete/reasoning_only retry 走兜底零污染; 仅保留极低概率 `LLMError` 残留, 并
  在第 5 节明确为已知 trade-off。
- F4[P2]: 修正事实基线(reasoning→content 的 segment_end 晚于首个 response
  streaming); 目标表述降为"thought complete 早于 response complete"。
- F5[P2]: 前端影响表述改为"实时 SSE 不变, 回放会更早看到 persisted thought"。
- F6[P3]: 明确旧基线测试需迁移, 补 retry/cancel/纯 reasoning/顺序/payload 用例,
  验证命令改 `uv run`。
