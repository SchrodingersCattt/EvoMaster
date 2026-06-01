# Incremental Message Pipeline Design (E3 Fix)

- Date: 2026-05-17
- Status: Draft (awaiting user approval)
- Author: Kealdoom + Claude (brainstorming session)
- Related prior work:
  - [2026-05-17-core-refactor-deferred-simplifications.md](../plans/2026-05-17-core-refactor-deferred-simplifications.md)
    (section **E3**)
- Scope: 完整方案，覆盖 json.dumps 缓存 + 增量 canonicalize +
  增量 validate + 测试 + benchmark + 渐进式落地。

## 1. Context & Motivation

### 1.1 当前行为

`AgentKernel._run_items` 主循环每轮在 LLM 调用前都跑一次：

```python
api_messages = normalize_and_validate_openai_messages(
    canonicalize_messages_for_provider(state.messages)
)
```

见 [matmaster/core/agent.py:287-289](../../../matmaster/core/agent.py).
`state.messages` 在循环里单调增长（除非 compactor 触发）：

- 入轮初始化塞 `system + history + user`：
  [agent.py:229-235](../../../matmaster/core/agent.py)
- 每轮 append 一条带 tool_calls 的 assistant：
  [agent.py:377](../../../matmaster/core/agent.py)
- 每个 tool result append 一条 tool message：
  [agent_tool_dispatch.py:64](../../../matmaster/core/agent_tool_dispatch.py)
- compactor 是唯一会"砍前缀"的入口：
  [agent_compaction.py:53-57](../../../matmaster/core/agent_compaction.py)
  把 `state.messages` 直接传给 `apply_compaction_plan` 做 in-place mutate。

### 1.2 两层性能问题

**第一层：嵌套循环里每轮重做全量。**
设轮数为 T、每轮新增 k 条消息、单条处理代价为 c，则累计开销
≈ `c × k × T(T+1)/2 = O(T² × k × c)`。
T = 50、k = 3 的典型会话：朴素总条数 150，实际累计处理 3,825 条。

**第二层：单条处理的乘性因子。**
`AssistantMessage.to_api_dict`
（[matmaster/types/messages.py:223-243](../../../matmaster/types/messages.py)）
里每个 tool_call 重新 `json.dumps(tc.arguments)`。`arguments` 通常是嵌套
dict（搜索/编辑工具的 `arguments` 实际能到 5KB+）。50 轮里同一条 assistant
被序列化 49 次，等效冗余编码量 ≈ 50 × 5KB = 245KB。

整体复杂度因此被文档归纳为 **O(turns² × avg_msg_size)**。

### 1.3 为什么不是 cosmetic

- 同步执行在主循环 hot path，用户在等下一个 token 的延迟窗口里。
- 触发频率高，且增长非线性——会话越长越卡。
- 同样的代码路径在 [history_checkpoint_codec.py:74](../../../src/services/history_checkpoint_codec.py)
  也存在；checkpoint 不是 hot path，但会强化"长会话尾部任何操作都要付出
  O(n) 验证成本"的整体感受。

## 2. Design Decisions

下表记录 brainstorming 中已锁定的四个核心决策（含被淘汰候选与放弃理由）。

| # | 决策点 | 选择 | 放弃的候选 |
|---|--------|------|------------|
| D1 | Scope | 完整方案（json.dumps 缓存 + 增量 canonicalize + 增量 validate + 测试 + benchmark） | 仅 json.dumps / 仅增量化跳过 validator / 先量化再决定 |
| D2 | 增量状态归属 | 抽出 `IncrementalMessagePipeline` class，`_KernelState` 持有实例 | _KernelState 加多个 cache 字段；module-level `WeakKeyDictionary` 隐式缓存 |
| D3 | Validator 增量形态 | 完全增量 + `revalidate_full()` 应急通道 | 仍全量重跑作为 defense-in-depth；每 N 轮听诊一次 |
| D4 | Cache invalidation | 显式 `reset()` + fingerprint 自动检测双保险 | 仅显式 reset；仅自动检测 |

## 3. 模块边界

- 新建 [matmaster/core/message_pipeline.py](../../../matmaster/core/message_pipeline.py)：
  - 公共：`IncrementalMessagePipeline`
  - 私有 helper：`_ToolTurnValidator`
- 不改动 [matmaster/types/message_normalization.py](../../../matmaster/types/message_normalization.py)
  的 4 个纯函数。`history_checkpoint_codec` 那条冷路径继续走纯函数，保留
  defense-in-depth；同时让纯函数成为 `revalidate_full()` 的唯一行为锚点，
  避免增量/纯函数双实现漂移。
- 改 [matmaster/core/kernel_items.py](../../../matmaster/core/kernel_items.py)
  的 `_KernelState`，新增一个 `pipeline` 字段：

  ```python
  pipeline: IncrementalMessagePipeline = dc_field(default_factory=IncrementalMessagePipeline)
  ```

- 改 [matmaster/core/agent.py:287-289](../../../matmaster/core/agent.py)：

  ```python
  api_messages = state.pipeline.feed_tail(state.messages)
  ```

## 4. API Surface

```python
class IncrementalMessagePipeline:
    def __init__(self) -> None: ...

    def feed_tail(self, messages: list[Message]) -> list[dict[str, Any]]:
        """处理 messages tail 并返回 API-ready dicts。

        复用 prefix 缓存；只对 messages[self._source_len:] 做工作。

        **fingerprint 检测是 best-effort，不是正确性保证**。它只能捕获：
        - prefix 缩短（len 变小）
        - 首条 message 被替换（id(messages[0]) 变化）
        - 上次处理过的最末一条被替换（id(messages[source_len-1]) 变化）

        它**捕获不到**：
        - 中间位置替换（如把 messages[5] 换成新对象但首尾不变）
        - 原地 mutation（同一对象的 `content` 被改）

        所以**所有 prefix rewriting 路径都必须显式调用 `reset()`**。
        fingerprint 只是兜底警报，用来发现遗漏 reset 的代码 bug，不能
        替代显式契约。

        **使用约束**：
        - 仅作为 provider payload 入口使用（即调用 LLM 之前的同步调用点）。
          返回时保证整体 messages 已通过 validate_openai_tool_turn_sequence 的
          末尾 invariant（pending tool ids 必须为空）；不满足时抛 LLMError。
        - 返回的 list 是顶层 + 每条 dict 的浅拷贝。调用者**不得** mutate
          任何层级（包括 `tool_calls`、`function`、`content` 等 nested 结构）。
          深层 mutation 会污染 pipeline cache，行为未定义。
        - 失败时（normalize/validate 抛错）pipeline 自动 reset，调用者
          下次 feed_tail 会触发全量重建。
        """

    def reset(self) -> None:
        """清空所有缓存。下次 feed_tail 全量重建。

        compactor 完成后必须显式调用，作为正向契约。
        """

    def revalidate_full(self, api_messages: list[dict[str, Any]]) -> None:
        """对**已 normalize 的** api_messages 跑全量 validators。

        **入参契约**：传入的 dicts 必须已经过 normalize_messages_for_openai
        处理（即 content=None 已被替换为 ""）。本方法不做 normalize；它
        只调 validate_openai_messages + validate_openai_tool_turn_sequence。
        典型用法是把 pipeline 内部 `_api_cache`（已 normalize）作为输入
        做 paranoia 检查；不适合直接喂原始 messages。

        不读写 pipeline 内部缓存。
        """
```

对外仅这三个方法。`_ToolTurnValidator` 是 module-private helper，不暴露。

## 5. 关键算法

### 5.1 Canonicalize 增量

内部状态：

```python
_canonical_cache: list[Message]                       # 与 api_cache 一一对应
_api_cache: list[dict[str, Any]]
_source_len: int                                       # 上次 feed_tail 处理到的 index
_prefix_fingerprint: tuple[int, int, int] | None       # (len, id(messages[0]), id(messages[source_len-1]))
```

`feed_tail(messages)` 流程：

1. **截短检测**。若 `len(messages) < self._source_len`，说明 prefix 被砍短
   （典型来源是 compactor 漏调 reset），先 logger.warning 再 `self.reset()`：

   ```python
   if len(messages) < self._source_len:
       logger.warning(
           "pipeline prefix shrunk; auto-reset",
           extra={"observed_len": len(messages),
                  "expected_source_len": self._source_len},
       )
       self.reset()
   ```

2. **指纹比对**（未截短场景）。reset 后若 `self._source_len > 0`，比较
   prefix 三元组。**两个边界**：
   (a) fingerprint 只描述"上次处理到的 prefix 部分"，所以等值比较项
       **不**用 `len(messages)`——append-only 增长时
       `len(messages) > self._source_len` 是正常的，不能当 mismatch。
   (b) 这是 best-effort 检测，只能捕获 head/tail identity change；
       中间位置替换（messages[5] 换对象但首尾不变）和原地 mutation
       （同一对象 `.content = ...`）都**捕获不到**。所有 prefix
       rewriting 路径必须显式 `reset()`，不能依赖 fingerprint 兜底。

   截短场景已在 step 1 处理掉，这里可以安全索引 `messages[self._source_len - 1]`：

   ```python
   if self._source_len > 0 and self._prefix_fingerprint is not None:
       current = (
           self._source_len,
           id(messages[0]),
           id(messages[self._source_len - 1]),
       )
       if current != self._prefix_fingerprint:
           logger.warning(
               "pipeline prefix mutation detected; auto-reset",
               extra={"observed": current,
                      "expected": self._prefix_fingerprint},
           )
           self.reset()
   ```

3. **tail 切片**：`tail = messages[self._source_len:]`。
   `tail` 为空：直接返回 `[dict(m) for m in self._api_cache]`（见 step 9）。

4. **事务边界**。step 5–8 全部在 try 块里。任何子步抛错则
   `self.reset()` 后重抛——失败恢复语义是"缓存可能被污染，强制全量
   重建"，调用者必须重试。选择 reset-on-failure 而非 staging 是因为
   staging 需要 deep-snapshot validator 的两个 set + 双份 list cache，
   实现成本过高；失败本就是异常路径，下次重建成本可接受。

   ```python
   orig_api_len = len(self._api_cache)
   try:
       ...  # step 5-8
   except Exception:
       self.reset()
       raise
   ```

5. **canonicalize + normalize 单条**（合并循环）。对每条 tail item 看
   `_canonical_cache[-1]` 是否为 UserMessage 且当前 item 也是
   UserMessage，是则合并改写 cache 末尾，否则 append。这一步同时覆盖了
   "cache 末尾 + tail 头部一次合并"和"tail 内部连续 user 合并"两个
   case——第一次循环看到的 `_canonical_cache[-1]` 就是 cache 原末尾，
   合并后 `_canonical_cache[-1]` 更新为 merged，下一次循环看到的又是
   新的末尾。

   ```python
   was_merged = False
   for i, msg in enumerate(tail):
       if (
           self._canonical_cache
           and isinstance(self._canonical_cache[-1], UserMessage)
           and isinstance(msg, UserMessage)
       ):
           merged = _merge_user_messages(self._canonical_cache[-1], msg)
           self._canonical_cache[-1] = merged
           self._api_cache[-1] = _to_normalized_api_dict(merged)
           if i == 0:
               was_merged = True
       else:
           self._canonical_cache.append(msg)
           self._api_cache.append(_to_normalized_api_dict(msg))
   ```

   `_merge_user_messages` 复用 [message_normalization.py:16-25](../../../matmaster/types/message_normalization.py)。
   `_to_normalized_api_dict` 是 module-private helper，**关键步骤**——
   把 `to_api_dict()` 的输出补上 `content=None → ""` 的 normalize：

   ```python
   def _to_normalized_api_dict(msg: Message) -> dict[str, Any]:
       payload = msg.to_api_dict()
       if "content" not in payload or payload.get("content") is None:
           payload["content"] = ""
       return payload
   ```

   这一步**不能省**：`AssistantMessage(content=None, tool_calls=[...])` 在
   [messages.py:230](../../../matmaster/types/messages.py) 会保留 `content=None`；
   纯函数路径靠 [message_normalization.py:62-67](../../../matmaster/types/message_normalization.py)
   把它替换成 `""` 之后才送进 `validate_openai_messages`（[message_normalization.py:142-148](../../../matmaster/types/message_normalization.py)
   要求 assistant content 必须是 `str`）。漏掉这步会让常见的 tool-call
   assistant 在 feed_tail 时抛 LLMError，且 pipeline 输出与纯函数不
   位级等价。

6. **validator 增量**。把 step 5 之后 `_api_cache` 的新增/改写段切片
   传给 validator：

   ```python
   start = orig_api_len - 1 if was_merged else orig_api_len
   new_api_segment = self._api_cache[start:]
   self._validator.feed_tail(new_api_segment)
   ```

   合并 case 下 `_api_cache[orig_api_len - 1]` 是被改写的最后一条，
   validator 需要看到它的新内容。

7. **末尾 invariant 校验**。tail 处理完后必须保证
   `validate_openai_tool_turn_sequence` 的末尾断言
   （[message_normalization.py:225-230](../../../matmaster/types/message_normalization.py)）
   依然成立——pending tool ids 必须为空。这是 `feed_tail` 作为 provider
   payload 入口的**硬约束**：

   ```python
   if self._validator.pending_tool_ids:
       raise LLMError(
           f"missing tool_result ids for assistant turn: "
           f"{sorted(self._validator.pending_tool_ids)}",
           retryable=False,
           error_category="bad_request",
       )
   ```

   这一段也在 step 4 的 try 块内，抛错时 except 触发 reset。
   这条 invariant 与纯函数 `validate_openai_tool_turn_sequence` 末尾
   `if pending_tool_ids: raise ...` 严格对齐。

8. **事务提交**。step 5–7 全部成功，更新指纹与索引：

   ```python
   self._source_len = len(messages)
   self._prefix_fingerprint = (
       self._source_len,
       id(messages[0]),
       id(messages[self._source_len - 1]),
   )
   ```

   注意：`_source_len - 1` 索引此刻等于 `len(messages) - 1`，即
   `messages[-1]`，两者数值等价；写成 `_source_len - 1` 是和 step 2
   的比对写法对齐，强调"fingerprint 描述的是已处理的 prefix"。

9. **返回**。两层浅拷贝（外 list + 内 dict）：

   ```python
   return [dict(m) for m in self._api_cache]
   ```

   只防顶层 dict 被改坏。nested `tool_calls` / `function` / list-form
   `content` 仍是 cache 里同一对象——调用者**不得** mutate 任何层级
   （已在 §4 文档化）。深层 isolation（deepcopy）每轮成本是 ms 级，
   不值得；具体测试见 §9 `test_no_caller_mutation_pollutes_cache`。

### 5.2 `json.dumps(arguments)` 缓存

[messages.py:223-243](../../../matmaster/types/messages.py) `AssistantMessage.to_api_dict`
里每个 tool_call 都会重新 `json.dumps(tc.arguments)`。
改 [ToolCallData](../../../matmaster/types/messages.py) 加 cached property：

```python
class ToolCallData(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]

    @cached_property
    def arguments_json(self) -> str:
        return json.dumps(self.arguments)
```

`to_api_dict` 里 `"arguments": json.dumps(tc.arguments)` →
`"arguments": tc.arguments_json`。

**关键不变性**：`ToolCallData` 实例**及其 nested `arguments` dict** 在
agent 生命周期内不被 mutate。两层都要：

- 第一层（字段重绑定）：`tc.arguments = ...` 形式的整体替换。pydantic v2
  `ConfigDict(frozen=True)` 可挡——如未启用顺手加上。
- 第二层（深层 mutation）：`tc.arguments["key"] = ...` 或
  `tc.arguments.update(...)`。**frozen 挡不住**。必须靠约定 + 测试守住：
  tool executor 不得 mutate 入参 arguments；如需修改参数，做副本传
  `modified_args` 或自己构造新 dict。

**model_config 写法**：frozen 与 cached_property 兼容要一起设：

```python
class ToolCallData(BaseModel):
    model_config = ConfigDict(frozen=True)
    # 注：functools.cached_property 在 pydantic v2 默认能正常工作，
    # 实测不会进入 model_dump()。不需要额外加 ignored_types。

    id: str
    name: str
    arguments: dict[str, Any]

    @cached_property
    def arguments_json(self) -> str:
        return json.dumps(self.arguments)
```

**model_copy 陷阱**：`tc.model_copy(update={"arguments": new_args})` 会
带着旧 `arguments_json` cache。当前仓库无此用法，但**禁止**未来引入；
要换 arguments 必须构造新的 `ToolCallData(...)`。这条写进类型 docstring。

落地步骤（在 commit step 2 一起完成）：

1. 全仓 grep `\.arguments\s*=\b`、`\.arguments\[`、`\.arguments\.update`、
   `\.arguments\.pop`、`\.model_copy\(update=.*arguments` 等深层 mutation
   痕迹，确认无生产 mutation 点。
2. 加三层测试（见 §9 R2 守护测试组）。
3. 把"不 mutate `arguments`、不 model_copy(update={'arguments': ...})"
   明文写进 tool executor contract（[matmaster/core/tool_runner.py:239](../../../matmaster/core/tool_runner.py)
   附近的 docstring 或 module-level 文档）。

通过 grep 守护比类型层兜底更可靠（grep 是 PR 静态检查级别）。

### 5.3 Validator 增量

```python
class _ToolTurnValidator:
    def __init__(self) -> None:
        self._pending_tool_ids: set[str] = set()
        self._seen_tool_ids: set[str] = set()

    @property
    def pending_tool_ids(self) -> set[str]:
        """供 pipeline.feed_tail 在 step 7 末尾断言用。"""
        return self._pending_tool_ids

    def feed_tail(self, new_msgs: list[dict[str, Any]]) -> None:
        """增量校验。复用 validate_openai_tool_turn_sequence 的状态机逻辑，
        但 pending/seen 是 instance state，不每次重置。

        **tail 中间** pending 非空是合法的（一段 tool turn 的 assistant +
        tool messages 可能跨越同一次 feed 内部）；**tail 末尾** pending
        是否非空由 pipeline.feed_tail step 7 决定是否抛错——本方法
        内部不做末尾断言，避免重复实现纯函数的两份语义。"""
        ...

    def reset(self) -> None:
        self._pending_tool_ids.clear()
        self._seen_tool_ids.clear()
```

注意：[message_normalization.py:225-230](../../../matmaster/types/message_normalization.py)
纯函数版本的 validator 在末尾会断言 "`pending_tool_ids` 必须为空"。增量
版本在 `feed_tail` 末尾**不**断言，因为我们处理的是轮间过渡态：本轮
可能刚 append 完 assistant + tool_calls，对应的 tool messages 要等
`dispatch_tool_calls` 完成后才会 append。下一次 `feed_tail` 进来时这些
tool messages 已就位，pending 会被消化干净。

`validate_openai_messages`（[message_normalization.py:128-148](../../../matmaster/types/message_normalization.py)）
是 per-message 无状态校验，直接对 tail 跑现有循环即可，不需要单独 class。

### 5.4 revalidate_full

```python
def revalidate_full(self, api_messages: list[dict[str, Any]]) -> None:
    from matmaster.types.message_normalization import (
        validate_openai_messages,
        validate_openai_tool_turn_sequence,
    )
    validate_openai_messages(api_messages)
    validate_openai_tool_turn_sequence(api_messages)
```

刻意复用纯函数避免逻辑漂移。不在 hot path 上，调用方目前没有，预留给：

- compactor 完成后的 paranoia 检查（可选，默认不开）
- 测试中作为参考实现做 invariant 对比
- 未来若发现增量 validator 有 bug 时的应急 bypass 通道

## 6. 失效策略

### 6.1 显式 reset()

[agent_compaction.py:54-88](../../../matmaster/core/agent_compaction.py) 的
`run_compaction_plan` 有两条 mutate 路径（try 内 `apply_summary` 成功，
except 内 `apply_fallback` 兜底）。reset 放在 try/except **之后**、
`messages_after = len(state.messages)`（[agent_compaction.py:89](../../../matmaster/core/agent_compaction.py)）
**之前**，一处覆盖两条路径：

```python
    try:
        ...
        result = await spec.compactor.apply_summary(plan, state.messages, ...)
    except Exception as exc:
        ...
        result = await spec.compactor.apply_fallback(plan, state.messages, ...)

    state.pipeline.reset()              # ← 新增：两条路径都走到这里
    messages_after = len(state.messages)
```

`run_preflight_compaction_if_needed` / `run_runtime_compaction_if_needed`
都走 `run_compaction_plan`，所以这一处 reset 覆盖 preflight + runtime ×
summary + fallback 四种组合。

注意：reset **必须**显式调用，不能依赖 §6.2 的 fingerprint 检测——
compactor in-place mutate 可能只动中间位置（例如把 messages[1:5] 替换
为 summary message），首尾对象 id 都不变，fingerprint 不会触发。

### 6.2 自动检测（fingerprint）— best-effort 兜底，**非**正确性保证

§5.1 step 1–2 的两段校验：

- **step 1（截短分支）**：`len(messages) < self._source_len`——直接判定
  prefix 缩短，logger.warning + reset。这一支用 `<` 而不是 `!=`，因为
  append-only 时 `len(messages) > self._source_len` 是正常情况。
- **step 2（等长 prefix 指纹分支）**：三元组只看两端：
  - `self._source_len`：sanity 项
  - `id(messages[0])`：首条被替换必变
  - `id(messages[self._source_len - 1])`：上次处理过的最末一条 identity 变化

**明确的检测盲区**——以下场景 fingerprint 完全无能为力：

- **中间位置替换**：`messages[N]` 被替换为新对象（N 不在 0 / source_len-1），
  首尾 id 不变。例如 compactor 把 messages[2:7] 切除并插入一条 summary
  message，首尾 id 不变，长度也可能与上次相同。
- **原地修改**：同一对象 `messages[N].content = "..."` 或
  `messages[N].tool_calls.append(...)`，`id()` 完全不变。

这两类情况**只能靠显式 `reset()`**。所有 prefix rewriting 路径都必须
主动调 reset；fingerprint 是兜底警报，用来发现遗漏 reset 的代码 bug，
不是替代显式契约。运维可在日志里搜 `"pipeline prefix"` 主动发现。

校验本身只是几个整数比较 + id()，纳秒级，可以放在 hot path。

若要真正自动检测所有 prefix 修改，需要维护完整 prefix object id 序列
或版本号——每轮 O(n) 比较，会直接抵消 E3 的性能收益。这是 E3 scope
外的设计选择，不在本方案中。

### 6.3 边界 case：messages 为空 / 极短

- `messages = []`：step 1 触发 `0 < self._source_len`（若 _source_len > 0）
  导致 reset；reset 后 step 2 因 `self._source_len = 0` 被守卫跳过；
  step 3 tail 为空，返回 `[]`。**关键**：返回前不更新 fingerprint（step
  8 在 try 块外，只有 step 5–7 都跑过才执行）。
- `self._source_len = 0`（首次 feed）：step 1、2 都跳过，直接走 step 3 之后。
- `self._source_len = 1`：step 2 的 `messages[self._source_len - 1] = messages[0]`，
  与 `id(messages[0])` 重叠，三元组退化为两个独立字段+一个冗余字段，仍正确。

## 7. checkpoint codec 路径

[src/services/history_checkpoint_codec.py:74](../../../src/services/history_checkpoint_codec.py)
**保持现状**，继续调用 `normalize_and_validate_openai_messages` 纯函数。
理由：

- 这条路径在写 checkpoint 时跑，是冷路径，每次都拿到不同的 messages
  快照，无增量复用机会。
- 让纯函数 `normalize_and_validate_openai_messages` 作为 schema validate
  的完整参考实现（normalize + validate + validate_tool_turn 三步）。
  pipeline 的 `revalidate_full` 只复用其中两个 validator——故意不包含
  normalize——契约见 §4 docstring，因为它的预期输入是 pipeline 内部
  `_api_cache`（已 normalize），不是任意外部 messages。

## 8. 风险登记

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| R1 | §5.1 step 3 合并 user 时 `_api_cache[-1]` 漏同步更新 | 中 | provider 收到陈旧消息内容，LLM 行为异常 | invariant 测试 `test_user_merge_at_cache_boundary` 必须断言 `_api_cache[-1] == merged_message.to_api_dict()` |
| R2 | `ToolCallData.arguments` 的 **nested mutation**（`tc.arguments[k] = v` / `.update(...)` / `.pop(...)`）导致 `arguments_json` 陈旧；frozen 只挡字段重绑定（`tc.arguments = new`），挡不住深层 mutation | 低-中 | tool_call 参数发错给 provider | 全仓 grep `\.arguments\s*=\b` / `\.arguments\[` / `\.arguments\.update` / `\.arguments\.pop` 确认无生产 mutation 点；加 `test_existing_tools_do_not_mutate_arguments`；把"不 mutate arguments"写进 tool executor contract（[tool_runner.py:239 附近](../../../matmaster/core/tool_runner.py)）。frozen 仅作为字段层兜底，不被宣称为 nested 保护 |
| R3 | compactor 之外的未来 prefix-rewriting 路径漏调 reset() | 中 | 增量缓存错乱 | fingerprint auto-reset + warning，运维可观察并补 reset 调用 |
| R4 | 增量 validator 与纯函数 validator 行为漂移 | 低 | 同一份 messages 两条路径校验结果不一致 | invariant 双约束测试 `test_pipeline_output_equals_pure_pipeline_for_clean_prefixes` + `test_pipeline_and_pure_both_raise_on_pending_tool_boundary` 对 fixture 强制等价；`revalidate_full` 入参契约是"已 normalize 的 api messages"，内部直接转发 `validate_openai_messages` + `validate_openai_tool_turn_sequence` 两个纯函数 validator |
| R5 | benchmark fixture 不代表真实负载，优化方向偏 | 中 | 优化前后 wall time 改善不真实 | large fixture 用真实 session log（从生产 redis 抓一条典型 50 轮带工具的 trace） |
| R6 | `cached_property` 与 pydantic v2 `model_config` 配置遗漏 frozen | 低 | frozen 不生效；或 `model_copy(update={"arguments": ...})` 导致 `arguments_json` 陈旧 | model_config 写成 `ConfigDict(frozen=True)`（cached_property 在 pydantic v2 默认正常工作，不需要 ignored_types）；在 ToolCallData docstring 明文禁止 `model_copy(update={"arguments": ...})`；要换 arguments 必须构造新实例 |
| R7 | 调用者 mutate `feed_tail()` 返回 dict 的**顶层** key 污染 pipeline cache | 低 | 后续 feed_tail 返回脏数据 | 顶层通过 `[dict(m) for m in self._api_cache]` 两层浅拷贝防御；`test_top_level_caller_mutation_does_not_pollute_cache` 守护此防御 |
| R7b | 调用者对返回 dict 的 **nested** 结构（`tool_calls`、`function`、list-form `content`）做 mutate | 低 | pipeline cache 被污染；行为未定义 | **不防御**——通过 §4 docstring + §12 非目标明确声明 nested 是 unsupported contract；调用者自负 |
| R8 | step 5 边界 `was_merged` 标志计算错误，导致 step 6 validator 切片错位 | 低 | validator 漏校验合并条 / 重复校验已校验条 | step 5 代码片段已固定 `if i == 0: was_merged = True`；测试 `test_user_merge_at_cache_boundary` + `test_user_merge_within_tail` 各覆盖一种情况 |
| R9 | fingerprint **盲区**：中间位置 message 替换（首尾 id 不变）或原地 mutation（同一对象 .content 被改） | 中（取决于未来代码） | pipeline 继续复用旧缓存条目，输出错误 | **不由 fingerprint 检测**——所有 prefix rewriting 路径必须显式 `reset()`；详见 §6.2"明确的检测盲区"段落 |

## 9. 测试覆盖

新建 [tests/matmaster/core/test_message_pipeline.py](../../../tests/matmaster/core/test_message_pipeline.py)：

| 测试 | 验证内容 |
|------|----------|
| `test_empty_then_first_feed` | 第一次 feed 等价于纯函数 |
| `test_append_only_growth` | 多次 append 后 cache 增量正确 |
| `test_user_merge_at_cache_boundary` | cache 尾 user + tail 头 user 触发合并；同步检查 `_canonical_cache[-1]` 与 `_api_cache[-1]` |
| `test_user_merge_within_tail` | tail 内部相邻 user 合并 |
| `test_tool_call_assistant_then_tool_messages` | 一轮内 append assistant(tool_calls)，下一轮 append 对应 tool messages；validator 跨调用 state 正确 |
| `test_invalid_tool_id_raises_lazily` | 不匹配的 tool_call_id 在 feed_tail 时抛 LLMError；cache 不被污染（再次 feed 同样的合法 tail 应仍能恢复） |
| `test_explicit_reset_drops_cache` | reset 后下次 feed_tail 全量重建 |
| `test_prefix_truncation_auto_reset` | 模拟 compactor：messages 变短，feed_tail 触发 auto-reset + warning（caplog 断言） |
| `test_prefix_replacement_auto_reset` | 模拟 compactor：首条被换为新对象，feed_tail 触发 auto-reset |
| `test_revalidate_full_matches_pure_validators_on_normalized_payloads` | revalidate_full 与 `validate_openai_messages` + `validate_openai_tool_turn_sequence` 行为位级等价；**入参必须已 normalize**（content=None 已替换为 ""），不验证 normalize 自身行为 |
| `test_arguments_json_cached_once` | `ToolCallData.arguments_json` 命中缓存（通过 `id()` 断言或 mock json.dumps 调用次数） |
| `test_top_level_caller_mutation_does_not_pollute_cache` | feed_tail 返回 list，调用者 `result[i]["content"] = "X"` 后；下一次 feed_tail 返回的相应位置应仍是原内容（验证 step 9 顶层 dict 浅拷贝防御）。**注**：nested mutation（如 `result[i]["tool_calls"][0]["id"] = ...`）是 unsupported contract，**不**在本测试覆盖范围内，见 §12 |
| `test_pending_tool_call_raises_on_feed_tail_exit` | 输入 messages 末尾为 assistant(tool_calls) 但缺 tool messages：feed_tail 抛 LLMError（与纯函数行为对齐），并 reset cache |

**R2 守护测试组**（三层守护，替代不可操作的"对每个 builtin tool 跑
execute_batch"）：

| 测试 / 检查 | 层级 | 验证内容 |
|-------------|------|----------|
| `scripts/lint_no_arguments_mutation.py`（CI 静态 grep） | 第一层 | grep `\.arguments\s*=\b` / `\.arguments\[` / `\.arguments\.update` / `\.arguments\.pop` / `\.model_copy\(update=.*arguments` 等 mutation 痕迹，命中即 CI failure |
| `test_pure_local_tools_do_not_mutate_arguments` | 第二层 | 对一组纯本地、易构造的 builtin tools（如 `FilesystemTool` 子集、`PythonExecTool` mock 等，**不**含需要 Bohrium credential / shell session / spawn_fn / 网络的工具）跑 `execute_batch`，前后 `deepcopy(arguments)` 比对相等 |
| `test_full_tool_runner_does_not_mutate_arguments_via_synthetic_tool` | 第三层 | 在 `FullToolRunner` 上注入一个 `SyntheticMutatingTool`（其 `execute` 故意做 `arguments["x"] = "mutated"`），验证 `FullToolRunner` 当前 contract 是否会复制入参——决定 contract 边界：若 runner 不复制，必须在 contract 写明"tool 实现不得 mutate"，并禁止 SyntheticMutatingTool 这种代码合入 |

这一组替代单一的 `test_existing_tools_do_not_mutate_arguments`——后者
会拉进 Bohrium credential、shell session、spawn_fn、网络等环境依赖，
对 E3 而言是脆弱的集成测试。三层守护把 contract 边界、纯本地样例、
PR 静态检查分开，各层职责清晰、单独可维护。

外加 invariant 测试。注意 P1.3 修正后：**任意 prefix 不再恒等于纯函数输出**，
因为切在 assistant(tool_calls) 之后、tool messages 之前的 prefix 对纯函数
本来就是非法的（纯函数末尾会因 pending 非空抛 LLMError）。所以 invariant
要分两类：

```python
def test_pipeline_output_equals_pure_pipeline_for_clean_prefixes():
    """合法 prefix（结束在 clean tool-turn boundary）上两条路径输出位级相等。"""
    messages = build_complex_fixture()  # system + user-user-merge + assistant(tool_calls) + tool messages + 多轮
    clean_prefix_lens = collect_clean_boundary_indices(messages)
    # 例如：每轮 dispatch_tool_calls 完成后的索引位置，以及自然终止后
    pipeline = IncrementalMessagePipeline()
    for prefix_len in clean_prefix_lens:
        pipeline_out = pipeline.feed_tail(messages[:prefix_len])
        pure_out = normalize_and_validate_openai_messages(
            canonicalize_messages_for_provider(messages[:prefix_len])
        )
        assert pipeline_out == pure_out, f"divergence at clean prefix_len={prefix_len}"

def test_pipeline_and_pure_both_raise_on_pending_tool_boundary():
    """切在 pending tool turn 中间的 prefix：两条路径同样抛 LLMError。"""
    messages = build_complex_fixture()
    pending_prefix_lens = collect_pending_boundary_indices(messages)
    # 例如：assistant(tool_calls) 之后、对应 tool messages 之前的索引
    for prefix_len in pending_prefix_lens:
        pipeline = IncrementalMessagePipeline()  # 每次新实例，避免上次 raise 后 reset 干扰
        with pytest.raises(LLMError, match="missing tool_result ids"):
            pipeline.feed_tail(messages[:prefix_len])
        with pytest.raises(LLMError, match="missing tool_result ids"):
            normalize_and_validate_openai_messages(
                canonicalize_messages_for_provider(messages[:prefix_len])
            )
```

这是核心 invariant 双约束：合法 prefix 上输出位级相等；非法 prefix 上
两条路径同样抛错。任一约束不满足即定位到具体 prefix_len，便于 debug。

## 10. 性能验证

新建 [benchmarks/test_message_pipeline_perf.py](../../../benchmarks/test_message_pipeline_perf.py)
（pytest-benchmark）：

| Fixture | 轮数 | 每轮 tool calls | 平均 arguments 大小 |
|---------|------|-----------------|---------------------|
| small | 10 | 3 | 500 B |
| medium | 30 | 3 | 2 KB |
| large | 50 | 5 | 5 KB |

每个 fixture 两条 bench：

- `pure`：每轮调 `normalize_and_validate_openai_messages(canonicalize_messages_for_provider(...))`
- `pipeline`：复用同一个 `IncrementalMessagePipeline` 实例 feed_tail

记录指标：

- wall time（pytest-benchmark）
- `json.dumps` 调用次数（用 `unittest.mock.patch("json.dumps", wraps=json.dumps)` 计数）
- 累计处理的消息条数（pipeline 内部 counter）

预期方向（基于代码分析，落地后以实测为准）：

- small 档：~30% 改善（主要来自 json.dumps 缓存的常数因子消除）
- large 档：~10x 改善（O(turns²) → O(turns × k)，乘以 json.dumps 缓存的乘性消除）

## 11. 渐进式实施顺序

5 个 commit，每步可独立验证、独立 ship。
**Commit type 限定在仓库现行约定 `feat / fix / refactor / chore / test / style / merge`**
（参考 [`git log --oneline`](../../../) 近 30 条 commit 的格式）：

1. **`test(perf): add message pipeline baseline benchmark`** — 新建 benchmark
   fixture，只跑纯函数路径，落库 baseline 数字。无代码变动，纯加测试。
2. **`refactor(types): cache ToolCallData.arguments_json`** — 加 `cached_property`，
   改 `AssistantMessage.to_api_dict` 用缓存；同步加 R2 守护测试。bench 数字第一次跳。
3. **`feat(core): add IncrementalMessagePipeline skeleton`** — 新模块、class
   骨架、`_ToolTurnValidator`、§9 全部单元测试 + invariant 双约束测试。
   **暂不接入主循环**，方便单独 review。
4. **`feat(core): wire IncrementalMessagePipeline into kernel`** —
   `_KernelState` 加字段、[agent.py:287-289](../../../matmaster/core/agent.py)
   换用 pipeline、[agent_compaction.py](../../../matmaster/core/agent_compaction.py)
   加 `pipeline.reset()` 调用。
5. **`chore(perf): record E3 fix benchmark + retire deferred item`** — 跑 bench，
   对比 step 1 baseline，更新 [2026-05-17-core-refactor-deferred-simplifications.md](../plans/2026-05-17-core-refactor-deferred-simplifications.md)
   把 E3 那节整段删掉（按文档"维护这份清单"约定，干净删除不留删除线）。

## 12. 非目标

明确**不**做的事情，避免 scope creep：

- 不动 [message_normalization.py](../../../matmaster/types/message_normalization.py)
  的 4 个纯函数（它们是 codec 路径 + revalidate_full 的权威实现）。
- 不动 [history_checkpoint_codec.py](../../../src/services/history_checkpoint_codec.py)。
- 不优化 E6（`usage_vendor_by_turn` 无界增长）、E7（`ToolCatalog` overlay
  重建）、E8（启动期同步 I/O）。这些有独立的 deferred 条目，单独 PR。
- 不重写 compactor 的 in-place mutate 风格——pipeline 通过 reset() +
  fingerprint 适配它，不是要求它改。
- **不**提供 `feed_tail()` 返回值的深层 isolation。返回是 read-only
  contract：调用者不得 mutate 顶层 dict、`tool_calls` 列表、`function`
  嵌套 dict、list-form `content` 等任何层级。深层 isolation（deepcopy）
  每轮 ms 级开销，与 E3 的 perf 目标冲突；调用者唯一的合法用法是把
  `feed_tail` 输出直接交给 provider 调用，不存盘、不二次加工。如未来
  有 path 需要可变副本，自己做 deepcopy。
- 不在 `feed_tail` 内部容忍 pending tool ids 末尾非空状态。该方法是
  provider payload 入口，强制末尾合法。若未来出现"需要看半轮状态"
  的内部场景，单独开 `_inspect_state()` 之类的 debug-only API，不要
  弱化 feed_tail 的契约。
