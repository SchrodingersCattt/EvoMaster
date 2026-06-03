# Runtime Compaction 当轮信息注入设计

## 背景与问题

agent kernel 有两种上下文压缩：

- **preflight compaction**：在一轮的首个 LLM 调用之前触发。当轮 user 输入此时正好是
  `messages[-1]`（刚 append、尚未发送）。preflight 会把当轮输入从摘要输入中切走
  （`messages[:-1]`），摘要只覆盖此前历史，然后把当轮 `turn_input` 逐字重新拼装成
  `<current_instruction>` 块注入压缩后的 query。当轮诉求永远不经摘要，逐字保真。

- **runtime compaction**：在一轮内部、若干次 LLM 调用与工具调用之后、轮间触发。此时
  `messages[-1]` 是 AssistantMessage 或 ToolMessage，当轮 user 消息埋在列表中间。

问题出在 runtime：**当轮 user 文本指令被完全交给摘要 LLM**，摘要完成后并不会把当轮信息
重新拼装进新 query。一旦摘要把用户诉求改写、压缩失真甚至丢弃，agent 会带着一个走样的目标
继续推进，而此时它正处在执行该诉求的中途，影响比 preflight 更直接。

### 根因

`apply_summary`（`matmaster/context/compaction.py:465`）里的 `current_split` 是单个布尔量，
却同时承担了四个本应分开的职责：

| `current_split` 控制的事 | preflight | runtime（现状） |
|---|---|---|
| 摘要输入是否剔除当轮 user 消息（`_summary_base_messages`） | 是（`messages[:-1]`） | 否 |
| intent 选 PREFLIGHT 还是 RUNTIME | PREFLIGHT | RUNTIME |
| 是否把 turn_input 传给 assembler（即是否重拼 `<current_instruction>`） | 是 | **否 ← 问题所在** |
| covered_until 如何解析 | 用 turn_input 边界 | 用 provider 边界 |

而 `_should_split_current_input_for_preflight`（`compaction.py:169`）第一个条件就硬编码
`phase == "preflight"`，导致 runtime 永远拿不到第三件事——当轮 query 整个被甩给摘要。

## 目标

让 runtime compaction 在摘要完成后，把当轮 user **文本指令**逐字重新注入压缩后的 query，
机制对齐 preflight 的 `<current_instruction>`。

**非目标（本次明确不做）**：

- 不改 preflight 的任何行为。
- 不改 assembly 层。
- 不改 checkpoint 持久化格式与 durable base 内容。
- 不为 runtime 引入"长期保护所有历史 user 消息"的策略——只保当轮这一份。
- 不接线死代码 `CURRENT_INPUT_CONTINUATION_INSTRUCTION`（见下文"顺带发现"）。

## 关键洞察

逐行核对后，有三点让方案大幅简化：

1. **assembly 层零改动**。`_resolve_covered_until`（`assembly.py:109`）在 RUNTIME_COMPACTION
   分支只认 `covered_until_event_id`、完全无视 `turn_input`。因此给 runtime 的
   `CompactionAssemblyRequest` 传一个非空 `turn_input`，不会破坏覆盖边界——它只会被
   `_step_turn_input` 用来生成 `<current_instruction>`。`COMPACTED_COMPOSITION` 是
   preflight 与 runtime 共用的（`assembly.py:100-101`），已具备处理非空 turn_input 的能力。

2. **checkpoint 天然干净**。`<current_instruction>` 段落用 `RUNTIME_ONLY_VIEWS`
   （`turn_input.py:50`），只进 `ContextView.RUNTIME`，进不了 `ContextView.CHECKPOINT`。
   `apply_summary` 持久化的是 `checkpoint_user_msg = ...to_message(CHECKPOINT)`
   （`compaction.py:497`），所以重新注入当轮指令绝不会污染 durable base。

3. **preflight 与 runtime 对附件的处理天然不对称**：
   - preflight 的 `covered_until` 在当轮之前，当轮附件不在 rehydrate 范围内，只能靠
     `<current_instruction>` 携带。
   - runtime 的 `covered_until` 是最新事件，当轮附件已经被
     `SessionAttachmentsSource.from_events`（`session.py:62-67`）rehydrate 成**文本清单**塞进
     session 段落。因此 runtime 若再注入附件，就是与该清单重复。

   补充：rehydrate 出来的是纯文本引用（`image_1 名字 url` 这类），**不是**图片 vision part；
   图片 vision part 只在 `ContextComposition.apply`（`compositions.py:44-45`）里由
   `turn_input.attachments.images_as_parts()` 产生。

## 设计决策

### 决策一：runtime 只注入指令文本，不注入附件与图片

依据洞察 3：被摘要失真风险的只有文本指令；附件是结构化事件数据，已被忠实 rehydrate，
未经摘要。所以注入纯文本既精准解决问题，又不产生附件文本重复、也不重复计 vision token。

### 决策二：只把"注入什么 turn_input"从 `current_split` 解耦

`current_split` 现在承担四件事，但本次**只需要动其中一件**——"传给 assembler 的 turn_input"。
其余三件保持逐字不变，这是最小且最安全的改法：

- **intent 选择不动**：仍 `PREFLIGHT_COMPACTION if current_split else RUNTIME_COMPACTION`。
  这一点至关重要：`current_split=False` 同时覆盖 runtime **和** "preflight 计划但无当轮输入可切分"
  两种情况，它们都应落到 RUNTIME_COMPACTION + provider 边界（摘要覆盖全部消息至最新）。若改成
  "intent 由 plan.phase 决定"，后一种角落会变成 PREFLIGHT intent 并要求 turn_input，
  turn_input 为 None 时直接抛错——这是不可接受的行为回退。
- **covered_until 分支不动**：仍 `if intent == RUNTIME_COMPACTION: 用 provider 边界`。
  因 intent 取值未变，此分支逐字保持。
- **摘要输入剔除不动**：仍由 `_should_split_current_input_for_preflight` 判定、只对 preflight
  生效（runtime 的当轮 user 消息在列表中间，定位剔除既脆弱又无必要——留在摘要输入里反而给摘要
  提供上下文）。
- **唯一改动——注入什么 turn_input**：新增 `_resolve_injected_turn_input`，
  `current_split=True`（preflight 切分）返回完整 turn_input、runtime 返回纯文本变体、
  其余返回 None。

换言之，把原来 `turn_input if current_split else None` 这一个表达式替换掉，其余不碰。

## 改动清单（4 个文件）

### 1. `matmaster/context/sources/turn_input.py`

新增纯文本变体方法：

```python
def instruction_only(self) -> TurnInput:
    """只保留指令文本，清空附件与图片。"""
    return dataclasses.replace(self, attachments=TurnAttachmentsSource())
```

清空 `attachments` 后：`to_sections()` 只产出 `<current_instruction>` 文本段
（`_merged_current_instruction_text` 无附件行时仅返回文本）；`images_as_parts()` 返回空元组，
`ContextComposition.apply` 不会附加任何 vision part。`instruction` 与
`pre_turn_history_event_id` 保留。

### 2. `matmaster/context/compaction.py`

`apply_summary` 里 `current_split`、`intent`、`covered_until_event_id` 的计算**全部保持原样**。
唯一改动是传给 `assemble_compaction` 的 turn_input 参数：

```python
# 原：turn_input=turn_input if current_split else None,
# 新：
turn_input=_resolve_injected_turn_input(
    phase=plan.phase, current_split=current_split, turn_input=turn_input
),
```

新增解析函数：

```python
def _resolve_injected_turn_input(
    *,
    phase: Literal["preflight", "runtime"],
    current_split: bool,
    turn_input: TurnInput | None,
) -> TurnInput | None:
    if current_split:
        # preflight 切分：当轮输入在尾部、前面有历史可摘要，注入完整 turn_input（含附件）。
        return turn_input
    if (
        phase == "runtime"
        and turn_input is not None
        and turn_input.has_effective_input()
    ):
        # runtime：纯文本，附件已在 covered 范围内被 rehydrate。
        return turn_input.instruction_only()
    # preflight 无可切分输入等其余情况：保持原 None 行为。
    return None
```

要点：`current_split=True` 已蕴含 preflight + 有效输入，故直接返回完整 turn_input。
`current_split=False` 同时覆盖 runtime 与 "preflight 无可切分输入"；前者注入纯文本、后者返回 None。
传给 RUNTIME_COMPACTION 的 `instruction_only` turn_input 不影响覆盖边界——assembler RUNTIME 分支
只认 `covered_until_event_id`、无视 turn_input（`assembly.py:109`）。

**`intent` / `covered_until_event_id` / `_summary_base_messages` /
`_should_split_current_input_for_preflight` 一律不改**——intent 仍
`PREFLIGHT if current_split else RUNTIME`，covered_until 分支仍按 intent 走，摘要输入剔除仍只对
preflight 生效，runtime 摘要继续覆盖全部消息。

### 3. `matmaster/core/agent_compaction.py`

- `run_runtime_compaction_if_needed` 新增 `turn_input: TurnInput | None = None` 参数，
  转发给 `run_compaction_plan(..., turn_input=turn_input, ...)`。
- `run_compaction_plan` 无需结构改动：它早已把 `turn_input` 同时传给
  `call_summary_llm_response`（line 68）与 `apply_summary`（line 82）。对 runtime 而言，
  `call_summary_llm_response` 里的 turn_input 只被 `_summary_base_messages` 消费，而后者
  phase-gated 到 preflight，故为安全 no-op。
- 修订两处误导性 docstring：`run_compaction_plan`（line 38-40）与
  `run_runtime_compaction_if_needed`（line 221-223）现声称 "runtime 不使用 turn_input"，需更新。

### 4. `matmaster/core/agent.py`

第 317 行 `run_runtime_compaction_if_needed(...)` 调用补 `turn_input=turn_input`。
`turn_input` 是第 264 行 `turn_input = turn_request.turn_input` 的局部变量，
作用域覆盖整个 `_run_items` 生成器，包含第 309-324 行的 while 循环。零额外插管。

## 改动后行为

runtime 压缩后的消息，从

```
[system, user(<user_instructions> + <compacted_history>(摘要) + 附件清单 + jobs)]
```

变为在末尾多一个 `<current_instruction>` 块（deferred → `TURN_INSTRUCTION_LAST` 序，
渲染在尾部），内含当轮 user 文本逐字原文。摘要即便把诉求改写丢失，模型仍能看到权威指令。

## 被保住的不变量

- **checkpoint durable base 不变**——`<current_instruction>` 是 `RUNTIME_ONLY_VIEWS`，
  不进 CHECKPOINT 视图。
- **intent 与 covered_until 逐字不变**——两者计算未触碰；runtime 仍 RUNTIME_COMPACTION +
  provider 边界，"preflight 无可切分输入"角落仍回退 RUNTIME + provider。
- **无附件/图片重复**——纯文本注入，`images_as_parts()` 为空。
- **preflight 行为逐字不变**——`current_split=True` 时 `_resolve_injected_turn_input` 仍返回
  完整 turn_input，等价于原 `turn_input if current_split else None` 的真分支。

## 边界情况

- **空文本轮（纯 continuation）**：`has_effective_input()` 为假 → 不注入，与现状一致。
- **一次 `_run_items` 内多次 runtime 压缩**：每次都把同一份当轮诉求重新浮出，诉求持续可见。
- **摘要失败走 `apply_fallback`**：该路径不消费 turn_input，不受影响（保持现有 sliding-window）。

## 顺带发现（不在本次改动内）

`CURRENT_INPUT_CONTINUATION_INSTRUCTION`（`compaction.py:95`，内容为"别向用户复述摘要、
当前指令在 `<current_instruction>` 块中、基于摘要背景直接执行"）经全仓库检索**只有定义、
无任何消费点**，是死代码——preflight 也未使用。是否将其接线（会同时影响 preflight 与
runtime）属独立决定，本次不动；"参考 preflight"恰恰意味着保持一致地不碰它。

## 测试策略（TDD）

按测试先行推进，每个改动先写失败用例。

**`tests/matmaster/context/sources/test_turn_input.py`**
- `instruction_only()` 清空附件与图片、保留文本与 `pre_turn_history_event_id`；
  其 `to_sections()` 仅产出 `<current_instruction>` 文本段；`images_as_parts()` 为空。

**`tests/matmaster/context/test_compaction.py`**（`apply_summary` 单元行为）
- runtime 压缩 + 带文本的 turn_input → 结果 RUNTIME 消息含 `<current_instruction>` 且文本逐字。
- 关键保真用例：喂一个**故意丢掉当轮诉求**的摘要字符串，断言诉求仍通过
  `<current_instruction>` 出现（证明不再依赖摘要保真）。
- runtime + turn_input 带附件/图片 → 断言 RUNTIME 消息**不**含重复附件引用、不含 image
  vision part（纯文本注入）。
- 回归：runtime 的 `base_messages`（CHECKPOINT 视图）仍无 `<current_instruction>`。
- 回归：preflight 仍注入完整 turn_input（含附件），行为逐字不变。

**`tests/matmaster/core/test_agent_kernel_compaction.py`**（内核插管）
- 内核在 runtime 压缩路径把当轮 `turn_input` 传入 compactor。

## 验证命令

```bash
uv run pytest \
  tests/matmaster/context/sources/test_turn_input.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  -q
```

随后对改动文件跑钩子：

```bash
uv run pre-commit run --files \
  matmaster/context/sources/turn_input.py \
  matmaster/context/compaction.py \
  matmaster/core/agent_compaction.py \
  matmaster/core/agent.py
```
