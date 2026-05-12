# Context 模块统一重构 — Design v3

- 日期: 2026-05-12
- 状态: 草稿 v3（根据 spec review + 源码对照 review 重写），待作者复核
- 作者: kealdoom + Claude
- 范围: `matmaster/` 与 `src/services/` 中所有模型可见上下文相关的代码与数据流

---

## 0. v3 相对 v2 的关键变化

| 项 | v2 | v3 |
|----|-----|-----|
| 事件模型 | `user_context_snapshot`（每次 LLM 调用前一帧） | `model_user_input`（每个真实用户 turn 一条），无 snapshot 概念 |
| AGENT.md 响应性 | 冻结到 anchor，下次压缩才更新 | hash 变化即触发新 anchor model_user_input，**下一轮立即生效** |
| DAO 改造 | 隐含 | 显式前置为 Phase 0 |
| 文件拆分 | 未考虑 1000 行限制 | 显式前置为 Phase 0 |
| Case 3（oversized input） | 阶段 2 硬目标 | 拆出，本 spec 仅在 `transform` 字段预留 |
| Fallback (`sliding_window` / `tool_truncation`) | 删除 | 保留，先埋点 |
| Prompt 形态（`<turn_materials>` 拆分） | 阶段 2 直接落地 | 阶段 2 完成后做 offline A/B，通过再启用 |
| Restore 路径 | 单一新算法 | v0/v1 schema-aware 分流 |
| Sink 错误处理 | 未规定 | model_user_input fail-fast；history_checkpoint best-effort |
| 不变量校验 | 文档级 | dataclass `__post_init__` + `from_sources` 运行时校验 |
| `UserContextSnapshot` 类型 | 存在 | 删除 |

如果你只读 v2 留下的差异，请直接跳到 §3（事件模型）与 §8（AGENT.md hash-triggered anchor），其余章节是连带修订。

---

## 1. 背景与问题

当前项目里"模型可见 user context"的组装逻辑分散在 6 个位置，命名与边界混乱：

| 位置 | 现状职责 |
|------|---------|
| [matmaster/core/context_builder.py](../../matmaster/core/context_builder.py) | 混合 system prompt 装配、user request 装配、compact bundle 装配三种职责 |
| [matmaster/core/context_compactor.py](../../matmaster/core/context_compactor.py) | 压缩算法 + 手写 tag 字符串 + checkpoint 边界 |
| [matmaster/manifests/](../../matmaster/manifests/) | 名为 manifests，实际在做"从 events 重建模型可见 context sections" |
| [matmaster/types/current_input.py](../../matmaster/types/current_input.py) | 当前轮输入的 dataclass，里面写 `<current_instruction>` 标签 |
| [matmaster/types/context.py](../../matmaster/types/context.py) | `PlaygroundContext`，占用了 `context` 这个名字但实际是 playground 运行环境快照 |
| [matmaster/core/agent.py:337-347](../../matmaster/core/agent.py) | kernel 入口处直接拼字符串、构造 UserMessage |

更严重的是，**raw transcript history**（前端回放需要）与 **model-visible history**（后端续跑、压缩恢复、prompt cache 需要）这两个不同语义的历史，被混在同一份 `User/query.content` 字段里推导，导致：

- UI 想看到原始用户输入；
- backend 想从同一条记录恢复 provider-facing `UserMessage`；
- 但 provider-facing `UserMessage` 已经被系统加了 user instructions、available attachments、compacted summary、current instruction、active tools 等内容；
- 压缩后真实 LLM 可见上下文已经不是原始对话事件的简单回放。

本次重构的目标是把这套混乱的数据流提升为一套**前端回放 / 后端续跑 / 压缩恢复 / prompt cache 四个用例统一的数据模型**。

---

## 2. 核心不变量

```
1. User/query 永远只保存用户原始输入（user_text + files + images + workspace_paths），
   服务前端回放和审计。不承载系统改造后的 LLM prompt。

2. model_user_input 是一个真实用户 turn 对应的 provider-facing UserMessage 的事实记录。
   每个 source_query_event_id 对应**最多一条** model_user_input。
   不是「每次 LLM 调用前快照」，工具循环内不再写入新事件。

3. history_checkpoint.base_messages 保存压缩生成的 anchor user message。
   该 anchor 只包含 model-visible user context（不含 SystemMessage）。
   SystemMessage 由 kernel 在恢复时用 spec.system_prompt 重新构造；
   system prompt 不在 checkpoint 中冻结。

4. History restore 分流：
   - frontend display restore: 从 raw transcript (User/query, response, tool events)
     消费现有 ChatHistoryConverter 即可，不引入新路径。
   - backend model restore: schema-aware 分流。
     - v1 checkpoint 存在: checkpoint.base_messages
                          + 后续 model_user_input + assistant_state
                          + response/run_result + tool_result
     - 无 v1 检查点: 沿用 ChatHistoryConverter.events_to_dialog_messages
```

这四条是本次重构的**硬约束**。所有后续设计决策都要回到这四条来验证。

---

## 3. 事件模型（核心）

本节是 v3 的支柱章节。新模型只引入**一种新事件**，并扩展 `history_checkpoint` payload。

### 3.1 事件清单

| 事件 | 来源 | 频率 | 用途 |
|------|------|------|------|
| `User/query` | API/stream 层 | 每个真实用户请求一条 | 前端回放、审计 |
| `model_user_input` | service 层（kernel 调用前） | 每个真实用户请求**最多**一条 | backend model restore |
| `assistant_state` | kernel（有 tool_calls 时） | 每次 tool-call 轮一条 | model restore（assistant 侧） |
| `response` / `run_result` | kernel（自然结束时） | 每次自然结束一条 | model restore（assistant 侧）+ 前端回放 |
| `tool_result` | tool runner | 每次 tool 调用一条 | model restore + 前端回放 |
| `history_checkpoint` | compactor（扩展 payload） | 压缩触发时 | model restore 的重启锚点 |

**注意**：`model_user_input` 写入在 kernel `run_stream` 之前（service 层负责），不在 kernel 内部。kernel 不感知该事件。

### 3.2 `model_user_input` payload

```json
{
  "schema_version": "model_user_input.v1",
  "kind": "anchor",
  "message": {
    "role": "user",
    "content": "<user_instructions>...</user_instructions>\n\n<current_instruction>...</current_instruction>",
    "images": [{"url": "...", "mime_type": "image/png", "detail": "auto"}]
  },
  "source_query_event_id": 42,
  "user_instructions_hash": "sha256:abcdef...",
  "transform": "raw",
  "render_version": "user_context_render.v1"
}
```

字段说明：

- `kind`: `"anchor"` | `"continuation"`
  - `anchor`：装配了完整长尾 sections（UserInstructions + SessionContext sections + ActiveTurn）。session 首轮 + AGENT.md hash 变化的轮，都生成 anchor。
  - `continuation`：只装配 ActiveTurn。后续未触发 hash 变化的轮。
- `message`: `UserMessage.model_dump(mode="json")`，含 content + images 全部字段。多模态附件必须完整保留。
- `source_query_event_id`: 关联的 `User/query` 事件 id。**必填**，由 service 层从 DAO 改造后的返回值取得（见 §14 Phase 0）。
- `user_instructions_hash`: AGENT.md 文本的 sha256 hash。anchor 时必填；continuation 时可选（continuation 隐含与最近 anchor 同 hash）。
- `transform`: `"raw"` | `"preflight_compacted"` | `"oversized_summary"`
  - `raw`: 当前轮没触发 preflight，message 是普通装配产物
  - `preflight_compacted`: 当前轮触发了 preflight compaction，message 是压缩后的 runtime user message
  - `oversized_summary`: 当前轮走 oversized 输入路径（Case 3）。**本 spec 阶段不实现，仅预留字段**。
- `schema_version`/`render_version`: 见 §6.6 的版本号策略。

### 3.3 `history_checkpoint` payload v1 扩展

```json
{
  "schema_version": "history_checkpoint.v1",
  "render_version": "user_context_render.v1",
  "covered_until_event_id": 123,
  "base_messages": [
    {"role": "user",
     "content": "<user_instructions>...</user_instructions>\n\n<compacted_history>...</compacted_history>\n\n<loaded_skills>...</loaded_skills>\n\n<active_tools>...</active_tools>\n\n<past_attached_files>...</past_attached_files>",
     "images": []}
  ],
  "reason": "summary",
  "user_instructions_text": "...",
  "user_instructions_hash": "sha256:..."
}
```

新增字段：
- `schema_version` / `render_version`
- `user_instructions_text`: 压缩当时 service 层读到的 AGENT.md 文本
- `user_instructions_hash`: 同上的 hash

旧字段 (`covered_until_event_id` / `base_messages` / `reason`) 保留语义不变。

**`covered_until_event_id` 语义**：checkpoint 等价于「从 session 起点重放到该 event_id 为止的所有 LLM 可见消息」。具体到本 spec：

- 普通 runtime compaction：`covered_until_event_id` 指向当前事件流末尾（含 assistant_state / tool_result，**不含**尚未写入的下一轮 model_user_input）。
- Preflight compaction（运行时触发，对应 `transform=preflight_compacted` 的 model_user_input）：`covered_until_event_id` 指向 `ActiveTurnContext.pre_turn_history_event_id`，**不包含**当前轮的 User/query 和 model_user_input；checkpoint 之后会有对应的 model_user_input 事件被恢复追加。

### 3.4 写入时序

每个真实用户请求的事件序列：

```
普通延续轮（hash 未变）：
  User/query (id=N) → model_user_input(kind=continuation, source_query=N) → kernel.run_stream → [assistant_state | response/run_result + tool_result]*

AGENT.md 改动后第一轮：
  User/query (id=N) → model_user_input(kind=anchor, source_query=N, user_instructions_hash=NEW) → kernel.run_stream → ...

Session 首轮：
  User/query (id=N) → model_user_input(kind=anchor, source_query=N, user_instructions_hash=...) → kernel.run_stream → ...

运行中触发 preflight compaction：
  User/query (id=N) → history_checkpoint (covered_until < N) → model_user_input(kind=anchor, transform=preflight_compacted, source_query=N) → kernel.run_stream → ...

运行中触发 runtime compaction（无新用户输入，kernel 工具循环内）：
  ... → history_checkpoint (covered_until=末尾) → (kernel 继续 LLM 调用) → assistant_state/response/tool_result ...
```

**关键约束**：tool 循环内不再写任何 `model_user_input` —— v2 的"每次 LLM 调用前一帧"语义彻底废止。

### 3.5 SSE replay 与 live handler 改造

新增事件 `model_user_input` 必须同时加到两个过滤器：

- 历史回放：[stream_service.py:_should_emit_event_to_sse](../../src/services/stream_service.py:66) 加 `model_user_input` 到 hidden list（与现有的 `assistant_state` / `skill_hit` / checkpoint events 一起）。
- 实时流：现有 `matmaster.integration.event_router.SSEHandler._should_skip()` 同步加。

`display_history_restore_service.py` 不建 stub。前端 replay 现状是 `generate_subscribe_stream → get_session_events + filter`，本次不改这条路径，只更新 filter 即可。

### 3.6 Sink 错误处理（fail-fast vs best-effort）

| 写入 | 失败策略 | 理由 |
|------|----------|------|
| `User/query` 写入失败 | fail-fast | API 层已有处理，本次不动 |
| `model_user_input` 写入失败 | **fail-fast，本轮终止** | 是 model restore 的权威输入；继续跑 LLM 会制造未来无法正确恢复的会话 |
| `history_checkpoint` 写入失败 | **best-effort，记录 failure_reason** | 失败后最多从更老 checkpoint 或 raw events 重放，不影响本轮 LLM 调用 |
| `assistant_state` 写入失败 | best-effort，log | 同上 |

`CompactionResult.failure_reason` 字段保留，专门记录 checkpoint sink 错误信息。

---

## 4. 硬约束清单

放在显眼位置，所有 reviewer 与实现者必读。

1. 每个 `source_query_event_id` 在 events 表中对应**最多一条** `model_user_input` 事件。多于一条是 bug，不是 dedup 常态。
2. `model_user_input` 写入失败时，本轮 fail-fast；不允许继续 LLM 调用。
3. `history_checkpoint` 写入失败时，本轮可继续；compaction 路径必须记录 `failure_reason`。
4. 前端 SSE 回放与实时流永远不发 `model_user_input` / `assistant_state` / `history_checkpoint`。
5. `invocation_id` 明确为一次用户请求的标识，**不是**一次 LLM API call 的标识。
6. `spawn_id` 在本次重构中只保持现有 root/child 过滤语义，不扩展 child checkpoint 语义。
7. AGENT.md 读取设置 size cap（建议 **50KB**），超限走 truncate + warning（不 fail-fast，保持 UX）。
8. `schema_version` 决定 payload codec，`render_version` 决定 message content 的解释方式；restore 优先按 schema 分发，不重新渲染历史 sections。
9. ContextSection 的 view 不变量 `RUNTIME ⊇ CHECKPOINT` 必须在 dataclass `__post_init__` 中校验。
10. `UserContextMessage.from_sources` 必须校验 section `key` 唯一性，冲突时 raise。
11. 渲染层 `wrap_tag` 必须对用户可控内容做最小 escape，防止 `</tag>` 注入破坏 section 边界（具体见 §6.4）。

---

## 5. 模块边界与目录结构

### 5.1 `matmaster/context/` — 新模块（本次重构的主体）

```
matmaster/context/
  __init__.py
  sections.py              # ContextSection, ContextView, SectionOrder
  user_message.py          # UserContextMessage 聚合根
  rendering.py             # wrap_tag (含 escape), render_sections
  system_prompt.py         # 原 ContextBuilder.build_system_prompt
  compaction.py            # 原 core/context_compactor.py（保留 fallback）
  session.py               # 替代 manifests/rehydrator.py 的 SessionContextBuilder
  history_restore.py       # ModelHistoryRestorer (DI 注入 events 访问)
  scanner.py               # 从 manifests/scanner.py 迁移，底层 events 扫描工具
  sources/
    __init__.py
    user_instructions.py
    active_turn.py         # TurnRequestContext / TurnMaterialsContext / ActiveTurnContext
    compacted_history.py
    attachments.py
    skills.py
    tools.py               # active tools（替代 mcp.py，第一阶段保留 mcp.py shim）
    session_jobs.py        # 占位，留待 bohrium job table 系统建好
    workspace.py           # 占位
    artifacts.py           # 占位
```

**v3 不包含 `snapshot.py`**。`UserContextSnapshot` 类型废弃，事件落到 events 表的 `model_user_input` 直接序列化 UserMessage。

### 5.2 `matmaster/core/` 收缩后

只保留"运行时执行 + 编排"职责：

```
matmaster/core/
  agent.py                 # AgentKernel
  tool_runner.py
  tool_scheduler.py
  hooks.py
  exp.py
  playground.py            # 已有文件，本次把 PlaygroundContext / WorkspaceArchivalConfig 收回（不是新建）
  kernel_items.py
  finish_diagnostics.py
  capability_policy.py
  structural_validation.py
  stream_drain.py
  config_loader.py
```

**`core/` 不再 import `context_builder` / `context_compactor`**，统一从 `matmaster.context` 进入。

### 5.3 删除 / 迁移清单

| 旧路径 | 处理 |
|--------|------|
| `matmaster/types/context.py` | 阶段 3 删除。`PlaygroundContext` / `WorkspaceArchivalConfig` 迁入**已有的** `core/playground.py`（注意：v2 误标"新建"，实际为已有文件，需处理反向 import 循环）。阶段 1-2 保留 shim re-export |
| `matmaster/types/current_input.py` | 迁到 `context/sources/active_turn.py`，类型一并重命名 |
| `matmaster/manifests/` | 整目录重写为 `matmaster/context/` 内部模块 |
| `matmaster/core/context_builder.py` | 拆三段（见 §13）|
| `matmaster/core/context_compactor.py` | 迁到 `context/compaction.py`，**保留 fallback 路径** |
| `src/services/history_restore_service.py` | 改名 `model_history_restore_service.py`，内部委托 `matmaster/context/history_restore.py` 的 schema-aware 分流。**不**新建 `display_history_restore_service.py`（见 §3.5）|

---

## 6. 核心类型

### 6.1 `ContextView`

```python
# matmaster/context/sections.py
from __future__ import annotations
from enum import Enum

class ContextView(str, Enum):
    """同一组 sections 投影到 user message 时的视图选择。

    用于 *渲染时机*。一旦渲染产出 UserMessage 字符串，
    结果立即冻结（写入 model_user_input 事件或 history_checkpoint），
    任何后续恢复都不得依赖 view 重渲染。

    不变量: RUNTIME ⊇ CHECKPOINT。任何在 CHECKPOINT 视图中出现的 section
    必然也在 RUNTIME 视图中出现。
    """
    RUNTIME = "runtime"        # 下一轮 LLM 调用要看到的完整内容（含本轮 ActiveTurn）
    CHECKPOINT = "checkpoint"  # 写 checkpoint 时的视图（剥离本轮 ActiveTurn）
```

### 6.2 `ContextSection`（含 `__post_init__` 不变量校验）

```python
# matmaster/context/sections.py
from dataclasses import dataclass
from enum import IntEnum


class SectionOrder(IntEnum):
    USER_INSTRUCTIONS = 10
    COMPACTED_HISTORY = 100
    SESSION_JOBS = 200
    LOADED_SKILLS = 300
    ACTIVE_TOOLS = 400
    PAST_ATTACHMENTS = 500
    WORKSPACE = 600
    ARTIFACTS = 700
    TURN_MATERIALS = 1000
    TURN_REQUEST = 1100


@dataclass(frozen=True)
class ContextSection:
    key: str
    tag: str
    content: str
    order: int
    views: frozenset[ContextView]

    def __post_init__(self):
        if ContextView.CHECKPOINT in self.views and ContextView.RUNTIME not in self.views:
            raise ValueError(
                f"Section {self.key!r}: CHECKPOINT view requires RUNTIME view "
                f"(invariant RUNTIME ⊇ CHECKPOINT)"
            )
        if not self.key:
            raise ValueError("ContextSection.key must be non-empty")
        if not self.tag:
            raise ValueError("ContextSection.tag must be non-empty")
```

### 6.3 `UserContextMessage`（含 key 唯一性校验）

```python
# matmaster/context/user_message.py
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView
from matmaster.context.rendering import render_sections
from matmaster.types.messages import ImageContentPart, UserMessage


@dataclass(frozen=True)
class UserContextMessage:
    """用户侧模型可见上下文的聚合根。

    组合若干 ContextSection,最终投影为 provider-facing
    matmaster.types.messages.UserMessage。
    """

    sections: tuple[ContextSection, ...]
    images: tuple[ImageContentPart, ...] = ()

    @classmethod
    def from_sources(
        cls,
        *section_groups: Iterable[ContextSection],
        images: Iterable[ImageContentPart] = (),
    ) -> "UserContextMessage":
        merged: list[ContextSection] = []
        seen_keys: set[str] = set()
        for group in section_groups:
            for section in group:
                if section.key in seen_keys:
                    raise ValueError(
                        f"Duplicate section key {section.key!r} in UserContextMessage "
                        f"sources. Keys must be unique across all sources."
                    )
                seen_keys.add(section.key)
                merged.append(section)
        return cls(sections=tuple(merged), images=tuple(images))

    def render(self, view: ContextView) -> str:
        return render_sections(self.sections, view=view)

    def to_message(self, view: ContextView) -> UserMessage:
        return UserMessage(content=self.render(view), images=list(self.images))
```

### 6.4 `rendering.py`（含 tag escape）

```python
# matmaster/context/rendering.py
from __future__ import annotations
from collections.abc import Iterable

from matmaster.context.sections import ContextSection, ContextView


def _escape_close_tag(content: str, tag: str) -> str:
    """防止用户可控内容含 </tag> 关闭当前 section,破坏 prompt 边界。

    这是 prompt convention 防御,不是安全边界。policy:
    - 把 </tag> 替换为 </ tag> 形式(中间加空格)。
    - LLM 仍能理解原意,但不会破坏外层 tag 结构。

    若发现此 escape 触发(content 中含字面 </tag>),log warning,
    便于运营定位被 escape 的 prompt 注入或意外。
    """
    close = f"</{tag}>"
    if close in content:
        import logging
        logging.getLogger(__name__).warning(
            "rendering._escape_close_tag triggered: tag=%r content contains close form, "
            "escaping to avoid breaking section boundary",
            tag,
        )
        content = content.replace(close, f"</ {tag}>")
    return content


def wrap_tag(tag: str, content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    text = _escape_close_tag(text, tag)
    return f"<{tag}>\n{text}\n</{tag}>"


def render_sections(
    sections: Iterable[ContextSection],
    *,
    view: ContextView,
    separator: str = "\n\n",
) -> str:
    visible = [s for s in sections if view in s.views and s.content.strip()]
    visible.sort(key=lambda s: s.order)
    return separator.join(wrap_tag(s.tag, s.content) for s in visible)
```

`rendering.py` 是**唯一**知道 tag 怎么写的地方。

### 6.5 Prompt 形态决策（v3 改动）

v2 把当前 `<current_instruction>` 含 `user_text + [Current attachments]` 拆为 `<turn_materials>` + `<current_instruction>` 两个顶级 XML 块。这是 prompt 形态变化，有 quality regression 风险。

v3 决策：
- **Phase 1/2 期间保留现状 prompt 形态**（即沿用 `<current_instruction>` 含 attachments 列表的单 block）
- **Phase 3 前**做 offline A/B：
  - A: 现状 `<current_instruction>` 内含 user_text + `[Current attachments]`
  - B: 拆分版 `<turn_materials>` + `<current_instruction>`
- 评估维度：
  - 是否正确引用本轮附件
  - 是否正确选择 tool
  - 是否把附件当作任务而不是背景
  - 多图片输入是否还能稳定进入 provider
- A/B 通过再切换；不通过则 spec 中关于 `<turn_materials>` 的设计可选放弃或调整 tag 名

为支持兼容，`TurnMaterialsContext` 在 sources 中作为独立类型存在（见 §7.3），但默认渲染合并到 `<current_instruction>`，由一个 feature flag 控制是否拆分。flag 默认关闭。

### 6.6 `schema_version` / `render_version` 演化策略

两个版本号独立演化：

- `schema_version`：仅当 event payload 字段结构改变时升级
  - 新增字段（向下兼容）：minor 升级，旧 codec 读时忽略新字段
  - 删字段或字段语义变更：major 升级，需明确旧 codec 处理路径
- `render_version`：仅当 user message content 的渲染算法变化时升级（tag 名、tag 顺序、separator、escape 规则）

恢复路径：

1. 先看 `schema_version`，决定 payload 反序列化 codec
2. 再看 `render_version`，决定 content 字符串的语义（如果需要解析）
3. **不**重新渲染历史。content 字符串始终被当作权威字节使用

(schema_version, render_version) 不匹配时（例如 schema v2 配 render v1），按各自版本独立处理；不允许混合 codec。

---

## 7. Source 接口契约与清单

### 7.1 接口

每个 source 是 frozen dataclass，只暴露：

```python
class ContextSource(Protocol):
    def to_sections(self) -> tuple[ContextSection, ...]: ...
```

空内容返回空 tuple。Sources 之间**完全独立**，构造时只接外部数据源（events / 文件 / API / DI loader），互相不 import。

### 7.2 全局 order 与视图分布

| Source | 文件 | order | 视图 | 备注 |
|--------|------|-------|------|------|
| `UserInstructionsContext` | `sources/user_instructions.py` | `SectionOrder.USER_INSTRUCTIONS` (10) | RUNTIME + CHECKPOINT | 通过 DI 注入 AGENT.md 文本 |
| `CompactedHistoryContext` | `sources/compacted_history.py` | `SectionOrder.COMPACTED_HISTORY` (100) | RUNTIME + CHECKPOINT | summary LLM 产物 |
| `SessionJobsContext` | `sources/session_jobs.py` | `SectionOrder.SESSION_JOBS` (200) | RUNTIME + CHECKPOINT | 占位 |
| `LoadedSkillsContext` | `sources/skills.py` | `SectionOrder.LOADED_SKILLS` (300) | RUNTIME + CHECKPOINT | 从 events 重建 |
| `ActiveToolsContext` | `sources/tools.py` | `SectionOrder.ACTIVE_TOOLS` (400) | RUNTIME + CHECKPOINT | 替代 `<active_tools>` |
| `PastAttachmentsContext` | `sources/attachments.py` | `SectionOrder.PAST_ATTACHMENTS` (500) | RUNTIME + CHECKPOINT | 跨轮累积附件清单 |
| `WorkspaceContext` | `sources/workspace.py` | `SectionOrder.WORKSPACE` (600) | RUNTIME + CHECKPOINT | 占位 |
| `ArtifactsContext` | `sources/artifacts.py` | `SectionOrder.ARTIFACTS` (700) | RUNTIME + CHECKPOINT | 占位 |
| `TurnMaterialsContext` | `sources/active_turn.py` | `SectionOrder.TURN_MATERIALS` (1000) | **RUNTIME only** | 本轮附件清单 |
| `TurnRequestContext` | `sources/active_turn.py` | `SectionOrder.TURN_REQUEST` (1100) | **RUNTIME only** | 本轮 user_text |

CHECKPOINT 视图自动剥离 `TurnMaterialsContext` 和 `TurnRequestContext`。

### 7.3 关键 source 实现

#### `UserInstructionsContext`

```python
# matmaster/context/sources/user_instructions.py
from __future__ import annotations
from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})

# AGENT.md size cap (硬约束 #7)
USER_INSTRUCTIONS_MAX_BYTES = 50 * 1024  # 50KB


@dataclass(frozen=True)
class UserInstructionsContext:
    """工作空间级用户指令(AGENT.md)的模型可见上下文 source。

    text 字段由 service 层通过 loader 注入。matmaster/ 不感知任何文件路径,
    不感知 .matmaster/AGENT.md 这个具体约定。size cap 由 service 层强制。
    """
    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (ContextSection(
            key="user_instructions",
            tag="user_instructions",
            content=self.text,
            order=SectionOrder.USER_INSTRUCTIONS,
            views=_VIEWS,
        ),)
```

service 层调用示例：

```python
# src/services/agent_run_service.py 改造后片段
import hashlib
from matmaster.context.sources.user_instructions import (
    UserInstructionsContext,
    USER_INSTRUCTIONS_MAX_BYTES,
)


def _load_user_instructions(workspace_root: Path) -> tuple[str, str]:
    """读 AGENT.md, 返回 (text, hash)。

    Size cap (硬约束 #7): 超过 50KB truncate 并 log warning。
    """
    path = workspace_root / ".matmaster" / "AGENT.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "", _hash("")

    if len(raw.encode("utf-8")) > USER_INSTRUCTIONS_MAX_BYTES:
        logger.warning(
            "AGENT.md exceeds %d bytes, truncating",
            USER_INSTRUCTIONS_MAX_BYTES,
        )
        raw = raw.encode("utf-8")[:USER_INSTRUCTIONS_MAX_BYTES].decode(
            "utf-8", errors="ignore"
        )
    return raw, _hash(raw)


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
```

#### `active_turn.py`（v3 保留双 source 设计，但默认合并渲染）

```python
# matmaster/context/sources/active_turn.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlparse

from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.types.messages import ImageContentPart

_RUNTIME = frozenset({ContextView.RUNTIME})


def _display_name(value: str) -> str:
    parsed = urlparse(value)
    return PurePosixPath(parsed.path or value).name or value


@dataclass(frozen=True)
class TurnRequestContext:
    """本轮用户文本指令。仅 RUNTIME 可见。"""
    user_text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.user_text.strip():
            return ()
        return (ContextSection(
            key="current_instruction",
            tag="current_instruction",
            content=self.user_text.strip(),
            order=SectionOrder.TURN_REQUEST,
            views=_RUNTIME,
        ),)


@dataclass(frozen=True)
class TurnMaterialsContext:
    """本轮附带的文件 / 图片 / workspace 路径。仅 RUNTIME 可见。

    Phase 3 前的过渡期: 这里渲染出的 section 与 TurnRequestContext 输出可以
    合并到同一个 <current_instruction> block。具体由 feature flag 控制
    (见 §6.5 的 A/B 决策)。
    """
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    workspace_paths: tuple[str, ...] = ()

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not (self.files or self.images or self.workspace_paths):
            return ()
        lines: list[str] = []
        for i, v in enumerate(self.files, 1):
            lines.append(f"file_{i} {_display_name(v)} {v}")
        for i, v in enumerate(self.workspace_paths, 1):
            lines.append(f"workspace_{i} {v}")
        for i, v in enumerate(self.images, 1):
            lines.append(f"image_{i} {_display_name(v)} {v}")
        return (ContextSection(
            key="turn_materials",
            tag="turn_materials",
            content="\n".join(lines),
            order=SectionOrder.TURN_MATERIALS,
            views=_RUNTIME,
        ),)

    def images_as_parts(self) -> tuple[ImageContentPart, ...]:
        return tuple(ImageContentPart(url=u) for u in self.images)


@dataclass(frozen=True)
class ActiveTurnContext:
    """本轮请求的原子单元。"""
    request: TurnRequestContext = field(default_factory=TurnRequestContext)
    materials: TurnMaterialsContext = field(default_factory=TurnMaterialsContext)
    pre_turn_history_event_id: int | None = None
    source_query_event_id: int | None = None  # 必填(由 service 层从 DAO 返回值取)

    def to_sections(self) -> tuple[ContextSection, ...]:
        return (*self.materials.to_sections(), *self.request.to_sections())

    def has_effective_input(self) -> bool:
        return bool(
            self.request.user_text.strip()
            or self.materials.files
            or self.materials.images
            or self.materials.workspace_paths
        )
```

#### `SessionContextBuilder`

与 v2 §5.4 等价，本节略。完整实现见原 v2 spec 第 5.4 节，关键点：
- 替代 `manifests/rehydrator.py` 的 `CompactionRehydrator`
- 删除 v2 标记的 unused `playground_ctx` 参数
- 暴露 `build_sections(until_event_id, include_attachments)` 方法支持 Case 3 旁路

---

## 8. AGENT.md 处理（hash-triggered anchor）

v3 核心新设计章节。回应"AGENT.md 改动响应性回退"的 review 意见。

### 8.1 设计目标

- 用户改 AGENT.md 后，**下一轮请求立即生效**
- 不依赖 compaction 触发
- 不每轮都重复装载长 prefix（hash 未变时复用旧 anchor）
- restore 路径自然 work，不需要状态机或字符串替换魔法

### 8.2 写入决策（service 层每轮）

```python
# src/services/agent_run_service.py 改造后片段

async def _prepare_and_dispatch(req: SendMessageRequest) -> ...:
    # 1. 读 AGENT.md
    current_instr_text, current_instr_hash = _load_user_instructions(workspace_root)

    # 2. 写 raw User/query, 拿到 event id (依赖 Phase 0 DAO 改造)
    user_query_event_id = await events_service.add_history_event(
        session_id,
        payload={
            "source": "User",
            "type": "query",
            "content": req.content,
            "files": req.files,
            "images": req.images,
            "workspace_paths": req.workspace_paths,
            "task_id": task_id,
            "invocation_id": invocation_id,
        },
        user_id=user_id,
    )  # 返回 inserted event id

    # 3. 决定 kind
    last_anchor_hash = await _latest_anchor_user_instructions_hash(
        events_service, session_id, spawn_id,
    )
    is_first_turn_of_session = last_anchor_hash is None

    if is_first_turn_of_session or current_instr_hash != last_anchor_hash:
        kind = "anchor"
    else:
        kind = "continuation"

    # 4. 构造 ActiveTurnContext
    active_turn = ActiveTurnContext(
        request=TurnRequestContext(user_text=req.content),
        materials=TurnMaterialsContext(
            files=tuple(req.files),
            images=tuple(req.images),
            workspace_paths=tuple(req.workspace_paths),
        ),
        pre_turn_history_event_id=pre_query_scope_event_id,
        source_query_event_id=user_query_event_id,
    )

    # 5. 装配 user_ctx
    if kind == "anchor":
        # 完整 sections + ActiveTurn
        session_sections = session_context_builder.build_sections(
            until_event_id=pre_query_scope_event_id,
        )
        user_ctx = UserContextMessage.from_sources(
            UserInstructionsContext(text=current_instr_text).to_sections(),
            SessionJobsContext.empty().to_sections(),
            session_sections,
            active_turn.to_sections(),
            images=active_turn.materials.images_as_parts(),
        )
    else:
        # 仅 ActiveTurn
        user_ctx = UserContextMessage.from_sources(
            active_turn.to_sections(),
            images=active_turn.materials.images_as_parts(),
        )

    rendered_message = user_ctx.to_message(ContextView.RUNTIME)

    # 6. 写 model_user_input (fail-fast)
    try:
        await events_service.add_history_event(
            session_id,
            payload={
                "source": "matmaster",
                "type": "model_user_input",
                "content": {
                    "schema_version": "model_user_input.v1",
                    "kind": kind,
                    "message": rendered_message.model_dump(mode="json"),
                    "source_query_event_id": user_query_event_id,
                    "user_instructions_hash": current_instr_hash if kind == "anchor" else None,
                    "transform": "raw",
                    "render_version": "user_context_render.v1",
                },
                "task_id": task_id,
                "invocation_id": invocation_id,
            },
            user_id=user_id,
        )
    except Exception:
        logger.exception("model_user_input write failed; aborting turn")
        raise  # 硬约束 #2: fail-fast

    # 7. 调 kernel
    history = await model_history_restore_service.restore(
        session_id, spawn_id=spawn_id,
    )
    # history 已包含本轮的 model_user_input 渲染后的 UserMessage 作为最后一条
    # kernel 不再做 active_turn 装配,直接用 history

    async for event in exp.run_stream(
        pg_ctx,
        history=history,
        cancel_token=cancel_token,
        skills=...,
    ):
        ...
```

### 8.3 `_latest_anchor_user_instructions_hash` 查询

扫最近 N 条事件，找最近的 anchor 事件的 hash：

```python
async def _latest_anchor_user_instructions_hash(
    events_service: ChatEventsService,
    session_id: str,
    spawn_id: str | None,
) -> str | None:
    """返回最近一次 anchor 来源(model_user_input.kind=anchor 或 history_checkpoint)
    的 user_instructions_hash。无 anchor 时返回 None。

    优化: 实际实现应通过 SQL ORDER BY id DESC LIMIT N 取最近 N 条
    (N=50 已足够覆盖典型 session),在 Python 端按时间倒序找首个 anchor。
    """
    recent_events = events_service.get_recent_events(
        session_id, spawn_id=spawn_id, limit=50,
    )
    for ev in reversed(recent_events):
        if ev["type"] == "model_user_input":
            content = ev["content"]
            if content.get("kind") == "anchor":
                return content.get("user_instructions_hash")
        elif ev["type"] == "history_checkpoint":
            return ev["content"].get("user_instructions_hash")
    return None
```

如果扫 50 条仍未找到 anchor（极少见，意味着有 50+ 条 continuation 没有压缩），降级为"按 first turn 处理"（生成 anchor）。这是防御性兜底，不应在实践中触发。

### 8.4 几个场景演化

| 场景 | events 序列 | restore 后 messages |
|------|-------------|---------------------|
| Session 首轮，AGENT.md v1 | User/query, model_user_input(anchor, hash=v1) | [Sys, anchor_v1+turn] |
| 第 2 轮，AGENT.md 未变 | + User/query, model_user_input(continuation) | [Sys, anchor_v1+turn1, Asst1, anchor_v1 序列, turn2] |
| 第 3 轮，AGENT.md 改到 v2 | + User/query, model_user_input(anchor, hash=v2) | [Sys, anchor_v1+turn1, Asst1, turn2, ..., anchor_v2+turn3] |
| 第 4 轮，AGENT.md 仍 v2 | + User/query, model_user_input(continuation) | 同上 + turn4 |
| 第 5 轮，触发 preflight compaction | + User/query, history_checkpoint(...), model_user_input(anchor, transform=preflight_compacted, hash=v2) | [Sys, base_anchor_v2+turn5] |

**关键观察**：anchor 在 messages 中**不一定是第一条**。第 3 轮之后，messages 序列里同时存在 `anchor_v1` 和 `anchor_v2`。LLM 自然理解"更靠近末尾的 instructions 是最新版"（近端 attention 偏向）。

这与 v2 "anchor 必须是 messages[0]" 的假设不同，但更简洁：不需要字符串重写、不需要 restore 状态机维护。

### 8.5 与 history_checkpoint 的交互

压缩触发时，compactor 写 `history_checkpoint`，payload 包含 `user_instructions_text` / `user_instructions_hash`（service 层在调 compactor 前传入）。compactor 内部装配的 anchor base_messages[0] 用的就是这个 hash 对应的 AGENT.md 内容。

下一轮请求时，`_latest_anchor_user_instructions_hash` 会找到 `history_checkpoint.user_instructions_hash`，并与 service 层当前读到的 AGENT.md hash 比对。如果用户在压缩后又改了 AGENT.md，下一轮会再写一条 `model_user_input(kind=anchor, hash=新)`。

### 8.6 hash 计算细节

```python
def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
```

empty AGENT.md（不存在或全空白）也要算 hash（sha256("")），保证 None 状态可比较。

---

## 9. `compaction.py` — `ContextCompactor` 改造

### 9.1 v3 改造原则

- 改造前后行为等价（保留 fallback、保留 strategy 字段、保留 retained_turns 字段）
- 装配方式从手写字符串改为 `UserContextMessage` + view 投影
- 接受 service 层注入的 `user_instructions_text` 和 `user_instructions_hash`
- 写入的 `history_checkpoint` payload 含新字段

### 9.2 改造后 `apply_compaction_plan`

```python
async def apply_compaction_plan(
    self,
    plan: CompactionPlan,
    messages: list[Message],
    *,
    active_turn: ActiveTurnContext | None = None,
    user_instructions_text: str = "",
    user_instructions_hash: str = "",
    preset_summary: str | None = None,  # 预留供 oversized input (Phase 4) 用
    preset_past_attachments: PastAttachmentsContext | None = None,  # 同上
) -> CompactionResult:
    """执行压缩并替换 messages。

    preset_* 参数是 oversized input (Case 3 / Phase 4) 的预留旁路,
    本 spec 阶段不实现端到端调用,仅保留参数位避免接口变更。
    """
    if not messages:
        raise ValueError("Cannot compact an empty message list")
    if not isinstance(messages[0], SystemMessage):
        raise TypeError(f"messages[0] must be SystemMessage, got {type(messages[0])}")

    system_msg = messages[0]

    # === Summary 阶段 (保留 fallback) ===
    if preset_summary is not None:
        summary = preset_summary
        strategy = "summary"
        durability = "durable"
        failure_reason = None
    else:
        summary_input = self._select_summary_input(messages, active_turn)
        if not summary_input:
            raise ValueError("Cannot compact messages without history")
        try:
            summary = await self._summarize(summary_input)
            strategy = "summary"
            durability = "durable"
            failure_reason = None
        except Exception as e:
            # Fallback 保留: 不删除 sliding_window / tool_truncation
            logger.warning("Summary failed, falling back: %s", e)
            summary, strategy, failure_reason = self._fallback(messages, e)
            durability = "ephemeral"

    # === 装配 anchor user message ===
    until_event_id = (
        active_turn.pre_turn_history_event_id if active_turn else None
    )

    if preset_past_attachments is not None:
        past_attachments_sections = preset_past_attachments.to_sections()
        other_session_sections = self._session_builder.build_sections(
            until_event_id=until_event_id,
            include_attachments=False,
        )
    else:
        past_attachments_sections = ()
        other_session_sections = self._session_builder.build_sections(
            until_event_id=until_event_id,
        )

    user_ctx = UserContextMessage.from_sources(
        UserInstructionsContext(text=user_instructions_text).to_sections(),
        CompactedHistoryContext(summary=summary).to_sections(),
        SessionJobsContext.empty().to_sections(),
        past_attachments_sections,
        other_session_sections,
        active_turn.to_sections() if active_turn else (),
        images=active_turn.materials.images_as_parts() if active_turn else (),
    )

    runtime_msg = user_ctx.to_message(ContextView.RUNTIME)
    messages[:] = [system_msg, runtime_msg]

    checkpoint_msg = user_ctx.to_message(ContextView.CHECKPOINT)
    base_snapshot = [checkpoint_msg.model_dump(mode="json")]

    return CompactionResult(
        compaction_id=plan.compaction_id,
        compaction_count=plan.compaction_count,
        phase=plan.phase,
        strategy=strategy,
        durability=durability,
        trigger_tokens=plan.trigger_tokens,
        retained_turns=0,
        failure_reason=failure_reason,
        base_snapshot=base_snapshot,
        checkpoint_covered_until_event_id=until_event_id,
        # 新字段 (会被 sink 写到 history_checkpoint payload)
        user_instructions_text=user_instructions_text,
        user_instructions_hash=user_instructions_hash,
    )


def _fallback(
    self,
    messages: list[Message],
    error: Exception,
) -> tuple[str, str, str]:
    """Summary LLM 失败时的降级。

    保留为 Phase 3 默认行为,不在本 spec 删除。删除决策延后至有埋点数据后单独评估。

    返回 (summary_str, strategy, failure_reason)。
    strategy 可能是 "sliding_window" 或 "tool_truncation"。
    """
    # 实现等价于现 context_compactor.py:364-385,本节略
    ...
```

**关键点**：
- `runtime_msg` 与 `checkpoint_msg` 的差异完全靠 view 过滤实现：同一份 `user_ctx` 投影两次
- `user_instructions_text` / `user_instructions_hash` 通过参数注入，service 层负责读盘并传入
- **fallback 路径保留**。`sliding_window` / `tool_truncation` 删除决策延后到 Phase 3 完成、有埋点数据后单独评估
- `CompactionResult` 新增 `user_instructions_text` / `user_instructions_hash` 字段，sink 写 history_checkpoint payload 时一并写入

### 9.3 类型保留 + shim 链路

`ContextCompactor` 类名保留，文件名用 `compaction.py`。命名一致性见 §13。

阶段 2 期间，旧路径 `matmaster/core/context_compactor.py` 改为薄 shim：

```python
# matmaster/core/context_compactor.py (Phase 2 shim)
from matmaster.context.compaction import (  # noqa: F401
    ContextCompactor,
    CompactionPlan,
    CompactionResult,
    estimate_tokens,
    parse_turns,
)
```

---

## 10. `core/agent.py` 改造

### 10.1 v3 关键变化

v3 把 v2 的 snapshot_sink 整套机制**从 kernel 删除**。kernel 不再感知 `model_user_input` 事件。

- service 层在调 `kernel.run_stream` 之前已经写完 `User/query` + `model_user_input` 两条事件
- service 层调 `ModelHistoryRestoreService.restore(...)` 拿到完整 `history`（含本轮 model_user_input 渲染后的 UserMessage 作为最后一条）
- kernel.run_stream 直接用 history，不做 active_turn 装配
- kernel.run_stream 的 `task` 参数语义改变：v3 中 task 可以是空字符串（因为 user message 已经在 history 末尾），或 deprecated

### 10.2 kernel 入口简化

```python
# matmaster/core/agent.py 改造后伪代码

async def _run_items(self, spec, task, history, ...):
    """v3: history 已经是完整的 LLM 视图,包含本轮 user message。
    kernel 不再装配 active_turn。
    """
    if not history:
        raise ValueError("v3 kernel.run_stream requires non-empty history")
    if not isinstance(history[-1], UserMessage):
        raise ValueError("history[-1] must be UserMessage in v3")

    state = _KernelState(
        messages=[
            SystemMessage(content=spec.system_prompt),
            *history,
        ]
    )

    # 主 turn 循环 (agentic tool loop): v3 不再写任何 snapshot 事件
    while state.turn < spec.max_turns:
        state.turn += 1

        # ── runtime compaction (可能改写 state.messages) ──
        if spec.compactor:
            plan = await spec.compactor.plan_runtime_compaction(...)
            if plan is not None:
                async for item in self._run_compaction_plan(
                    plan, state,
                    user_instructions_text=spec.user_instructions_text,
                    user_instructions_hash=spec.user_instructions_hash,
                ):
                    yield item

        api_messages = normalize_and_validate_openai_messages(
            canonicalize_messages_for_provider(state.messages)
        )

        # ── LLM 调用 ──
        response = await self._call_llm(api_messages, ...)
        ...
```

### 10.3 `AgentRuntimeSpec` 字段变更

| 字段 | v2 | v3 |
|------|-----|-----|
| `context_builder: ContextBuilder` | 必填 | 保持（Phase 3 才改名为 `system_prompt_builder`） |
| `active_turn: ActiveTurnContext` | 新增 | **删除**（kernel 不再装配 active_turn） |
| `user_instructions_text: str` | 新增 | 保留（compactor 在 runtime compaction 时需要） |
| `user_instructions_hash: str` | 未规定 | **新增**（compactor 写 checkpoint 时需要） |
| `runtime_ports.snapshot_sink` | 新增 | **删除** |
| `runtime_ports.checkpoint_sink` | 已有 | 保留，payload 扩展（见 §3.3） |

废除散落字段：

- `spec.meta["current_input_context"]`
- `spec.meta["attachment_manifest"]`
- `spec.meta["current_user_images"]`
- service 层的 `_apply_user_instructions_to_initial_user_query`（不再需要，AGENT.md 通过 `UserInstructionsContext` 经 service 层装配）

---

## 11. `ModelHistoryRestorer` — backend model restore（v0/v1 分流）

### 11.1 接口与算法

`matmaster/context/history_restore.py` 暴露**纯算法**，不依赖 DB。通过回调接收 events 访问能力。

```python
# matmaster/context/history_restore.py
from __future__ import annotations
from collections.abc import Callable
from typing import Any

from matmaster.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolMessage,
    UserMessage,
)


class ModelHistoryRestorer:
    """重建 backend 视角的 LLM 真实历史。"""

    def __init__(
        self,
        *,
        get_latest_checkpoint: Callable[[str, str | None], dict[str, Any] | None],
        get_events_after: Callable[[str, int | None, str | None], list[dict[str, Any]]],
        legacy_restore: Callable[[str, str | None], list[Message]],
    ) -> None:
        """
        Args:
            get_latest_checkpoint(session_id, spawn_id) -> checkpoint dict or None
            get_events_after(session_id, after_id, spawn_id) -> list of events
            legacy_restore(session_id, spawn_id) -> messages
                v0 路径委托给 ChatHistoryConverter.events_to_dialog_messages 的包装函数
        """
        self._get_latest_checkpoint = get_latest_checkpoint
        self._get_events_after = get_events_after
        self._legacy_restore = legacy_restore

    def restore(
        self,
        session_id: str,
        *,
        spawn_id: str | None = None,
    ) -> list[Message]:
        """Schema-aware 分流。

        v1 路径: 存在 v1 history_checkpoint 时使用
        v0 路径: 兼容旧 session,委托给 ChatHistoryConverter
        """
        checkpoint = self._get_latest_checkpoint(session_id, spawn_id)
        schema_v1 = (
            checkpoint is not None
            and checkpoint.get("content", {}).get("schema_version") == "history_checkpoint.v1"
        )

        # 判断是否走 v1: 有 v1 checkpoint OR (无 checkpoint 但 events 含 model_user_input)
        if not schema_v1:
            has_model_user_input = self._session_has_model_user_input(session_id, spawn_id)
            if not has_model_user_input:
                return self._legacy_restore(session_id, spawn_id)

        return self._restore_v1(session_id, spawn_id, checkpoint)

    def _restore_v1(
        self,
        session_id: str,
        spawn_id: str | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[Message]:
        if checkpoint is not None and checkpoint.get("content", {}).get("schema_version") == "history_checkpoint.v1":
            content = checkpoint["content"]
            messages = self._deserialize_messages(content["base_messages"])
            after = content["covered_until_event_id"]
        else:
            messages = []
            after = None

        events = self._get_events_after(session_id, after, spawn_id)

        for event in events:
            etype = event.get("type")
            payload = event.get("content", {})

            if etype == "model_user_input":
                msg_dict = payload.get("message", {})
                messages.append(UserMessage.model_validate(msg_dict))

            elif etype == "assistant_state":
                # tool_calls 分支的权威 (kernel agent.py:535)
                from matmaster.types.message_normalization import restore_persisted_assistant_state
                inner = payload.get("state") or payload
                try:
                    msg = restore_persisted_assistant_state(inner)
                    messages.append(msg)
                except Exception:
                    logger.warning("assistant_state restore failed, skipping")

            elif etype in ("response", "run_result", "finish"):
                # 自然结束分支 (kernel agent.py:498-513)
                # 注意: 同一 turn 不会同时有 assistant_state(tool_calls) 和 response,
                # 因为 kernel 二选一。重复出现按出现顺序 append (defensive)。
                content = payload.get("content") or payload.get("text") or ""
                reasoning = payload.get("reasoning_content")
                messages.append(AssistantMessage(
                    content=content,
                    reasoning_content=reasoning,
                ))

            elif etype == "tool_result":
                messages.append(ToolMessage(
                    content=payload.get("result", ""),
                    tool_call_id=payload["call_id"],
                    tool_name=payload.get("tool_name", ""),
                ))
            # 其他类型(skill_hit, thought, planner_reply, log_line, ...)跳过

        return messages

    def _session_has_model_user_input(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        """Quick scan: 检查 session 是否有任何 model_user_input 事件。

        用于无 checkpoint 时判定走 v1 还是 v0。实现可以查 events 表 EXISTS。
        """
        events = self._get_events_after(session_id, None, spawn_id)
        return any(e.get("type") == "model_user_input" for e in events[:200])

    @staticmethod
    def _deserialize_messages(raw: list[dict[str, Any]]) -> list[Message]:
        """checkpoint.base_messages 只含 UserMessage (与现有 codec 契约一致)。"""
        result: list[Message] = []
        for m in raw:
            role = m.get("role")
            if role == "user":
                result.append(UserMessage.model_validate(m))
            elif role == "assistant":
                result.append(AssistantMessage.model_validate(m))
            elif role == "tool":
                result.append(ToolMessage.model_validate(m))
            else:
                logger.warning(
                    "Unexpected role %r in checkpoint.base_messages; dropping", role,
                )
        return result
```

### 11.2 v1 路径 assistant 侧的保守消费

v3 的 v1 restore **同时消费** `assistant_state`（tool_calls 分支）和 `response`/`run_result`/`finish`（自然结束分支）。

**这是有意保守的设计**。Spec review 提议两种方案：
- 方案 A：保留现状 kernel 行为，restore 同时消费两类事件。复杂度在 restore 端
- 方案 B：扩展 kernel 让所有 LLM response 都写 assistant_state，restore 算法简化

v3 选 A，理由：

1. kernel 改动回归风险大，本次 spec 目标是上下文模块清理而非 kernel 持久化重构
2. 同一 turn 不会同时有 `assistant_state` 和 `response` —— kernel 二选一（[agent.py:498-513](../../matmaster/core/agent.py:498) 自然结束直接 return；[agent.py:535](../../matmaster/core/agent.py:535) tool 路径才写 assistant_state）
3. v0 路径的 `ChatHistoryConverter.events_to_dialog_messages` 已经在生产环境验证过这套消费规则

方案 B 留作未来 phase（独立 spec）。

### 11.3 `src/services/` 的 DI 实现

```python
# src/services/model_history_restore_service.py (从原 history_restore_service.py 改名)
from matmaster.context.history_restore import ModelHistoryRestorer
from src.services.chat_history import ChatHistoryConverter


def build_model_restorer(events_dao: ChatEventsTable) -> ModelHistoryRestorer:
    def get_latest_checkpoint(session_id: str, spawn_id: str | None) -> dict | None:
        row = events_dao.query_latest_by_type(
            session_id, event_type="history_checkpoint", spawn_id=spawn_id,
        )
        return {"content": row.content, "id": row.id} if row else None

    def get_events_after(
        session_id: str, after_event_id: int | None, spawn_id: str | None,
    ) -> list[dict]:
        rows = events_dao.query_after(
            session_id, after_event_id, spawn_id=spawn_id,
        )
        return [{"id": r.id, "type": r.event_type, "content": r.content} for r in rows]

    def legacy_restore(session_id: str, spawn_id: str | None) -> list[Message]:
        events = events_dao.get_session_events(session_id, include_spawn=False)
        dialog = ChatHistoryConverter.events_to_dialog_messages(events)
        return [Message.model_validate(m) for m in dialog]

    return ModelHistoryRestorer(
        get_latest_checkpoint=get_latest_checkpoint,
        get_events_after=get_events_after,
        legacy_restore=legacy_restore,
    )
```

### 11.4 多次压缩链路

最新 checkpoint 已经把更老的 compact bundle 合并到新 summary 中（现 [context_compactor.py:46](../../matmaster/core/context_compactor.py:46) 的 `SUMMARY_SYSTEM_PROMPT` 显式要求 "merge older compact bundle with later events"）。

所以 `ModelHistoryRestorer.restore` 只取最新 checkpoint 即可，不叠加。

### 11.5 `history_checkpoint_codec.py` 兼容性

现 [src/services/history_checkpoint_codec.py:89-91](../../src/services/history_checkpoint_codec.py:89) 强制 `<previous_session_summary>` marker。v3 要把 codec 改为接受两种 marker：

```python
# v3 改造后
MARKERS_V0 = {"<previous_session_summary>"}
MARKERS_V1 = {"<compacted_history>"}

def _has_acceptable_marker(content: str) -> bool:
    return any(m in content for m in (MARKERS_V0 | MARKERS_V1))

# validate_base_messages 中
if not _has_acceptable_marker(first_content):
    raise ValueError(
        "checkpoint base_messages[0] must contain compact context bundle marker"
    )
```

base_messages 不含 SystemMessage 的约束（[codec line 86](../../src/services/history_checkpoint_codec.py:86)）保留。

v0 marker 退役在 Phase 4（独立 phase，30+ 天后评估）。

---

## 12. 四个 Case 的 source 装配（v3 修订）

| Case | 触发点 | 装配的 sources | model_user_input.kind |
|------|--------|---------------|------------------------|
| **1. 首轮无压缩** | service 层 stage X | UserInstructions + SessionJobs(empty) + SessionContextBuilder(skills/tools, 无 past_attachments) + ActiveTurn | `anchor` |
| **1b. 普通延续轮，hash 未变** | service 层 stage X | **仅 ActiveTurn** | `continuation` |
| **1c. 延续轮，hash 变了** | service 层 stage X | 完整 sections + ActiveTurn | `anchor` |
| **2. 运行中 runtime compaction（无新输入）** | kernel 内 compactor | UserInstructions + CompactedHistory + SessionJobs + SessionContextBuilder | （不写 model_user_input，只写 history_checkpoint）|
| **3. Oversized input（Case 3）** | **本 spec 不实现，Phase 4 独立 spec** | 预留 `transform="oversized_summary"` | 预留 |
| **4. Preflight compaction（新输入 + 立即压缩）** | service 层（kernel 内调用 compactor） | 完整 sections + ActiveTurn | `anchor` + `transform="preflight_compacted"` |

最终渲染顺序：长期约束 → 历史摘要 → 会话状态 → 可用能力 → 过去材料 → 本轮材料 → 本轮任务。

---

## 13. 命名清理表

### 类型与变量

| 旧名 | 新名 | 原因 |
|------|------|------|
| `ContextVisibility` | `ContextView` | 表达"视图选择"，不是"可见性" |
| ~~`ModelVisibleUserContext`~~ ~~`UserContextSnapshot`~~ | `UserContextMessage` + `model_user_input` event | snapshot 概念废弃 |
| `CompactionRehydrator` | `SessionContextBuilder` | 不是 hydration，是 session context collection |
| `pre_query_scope_event_id` | `pre_turn_history_event_id` | 实现细节剥离，语义直接 |
| `attachment_manifest` | `attachment_context` | 不是 manifest，是 context |
| `skill_manifest` | `skill_context` | 同上 |
| `mcp_manifest` | `tool_context` | 同上 + "tools" 是模型可见语义 |
| `HistoryRestoreService` | `ModelHistoryRestoreService` | 当前名字暗示"通用历史恢复"，实际只服务 model restore 路径 |

### 文件与目录

| 旧 | 新 |
|----|-----|
| `matmaster/manifests/` | `matmaster/context/` 内部模块 |
| `matmaster/manifests/rehydrator.py` | `matmaster/context/session.py` |
| `matmaster/manifests/attachment.py` | `matmaster/context/sources/attachments.py` |
| `matmaster/manifests/skill.py` | `matmaster/context/sources/skills.py` |
| `matmaster/manifests/mcp.py` | `matmaster/context/sources/tools.py`（保留 `mcp.py` shim） |
| `matmaster/manifests/scanner.py` | `matmaster/context/scanner.py` |
| `matmaster/manifests/artifact.py` | `matmaster/context/sources/artifacts.py` |
| `matmaster/manifests/bohrium.py` | `matmaster/context/sources/session_jobs.py` |
| `matmaster/manifests/workspace.py` | `matmaster/context/sources/workspace.py` |
| `matmaster/types/context.py` | 阶段 3 删除；定义迁回**已有的** `matmaster/core/playground.py`；shim 保留至阶段 3 |
| `matmaster/types/current_input.py` | `matmaster/context/sources/active_turn.py` |
| `matmaster/core/context_builder.py` | 拆三段（见 §15） |
| `matmaster/core/context_compactor.py` | `matmaster/context/compaction.py`（shim 路径保留至阶段 3） |
| `src/services/history_restore_service.py` | `src/services/model_history_restore_service.py` |
| `tests/matmaster/manifests/` | `tests/matmaster/context/` |

### `legal_mcp_servers` 重命名

`SessionContextBuilder.__init__` 的参数 `legal_mcp_servers` 改名 `allowed_mcp_servers`。原命名暗示"法律合规"，实际是"已注册"语义。

---

## 14. 阶段迁移路线（v3：4 阶段 + Phase 0 前置）

### Phase 0: 前置改造（独立 PR，无功能变化）

**目标**: 解除 v3 实现的两条硬依赖。这些是纯 mechanical refactor，可以**任何时候并行**于其他工作推进。

**0a. DAO 改造**:
- [chat_events_table.py:301-325](../../src/dao/chat_events_table.py:301) `add_event` 改为 `INSERT ... RETURNING id`，返回 `int | None`
- [events_service.py:24-73](../../src/services/events_service.py:24) `add_history_event` 返回 inserted event id
- 所有 caller 透传 id（grep `add_event(` / `add_history_event(`）

**0b. 文件拆分（解除 1000 行限制风险）**:
- [matmaster/core/agent.py](../../matmaster/core/agent.py)（975 行）：抽出 snapshot/checkpoint sink wiring、preflight compaction 装配、tool 调度辅助到独立 helper 模块
- [src/services/agent_run_service.py](../../src/services/agent_run_service.py)（930 行）：抽出 instructions loading、history restore wiring、user-input event 写入、bohrium rebuild
- 目标行数 < 800 行/文件，预留 Phase 1-3 扩展空间
- [src/services/stream_service.py](../../src/services/stream_service.py)（960 行）：抽出 SSE filter 逻辑

**测试目标**: 现有测试全部通过；不引入新测试。

---

### Phase 1: 事件语义阶段（核心）

**目标**: 落地两事件模型 + v0/v1 restore 分流 + SSE filter 改造 + AGENT.md hash anchor 决策。**不**改 prompt 形态，**不**做 Case 3，**不**动 ContextSection 内核（保留现 ContextBuilder 内的字符串拼接为现状渲染）。

**1a. 新事件类型注册**:
- 在 `ChatEventsTable` 接受 `event_type = "model_user_input"` 的写入
- SSE filter [stream_service.py:_should_emit_event_to_sse](../../src/services/stream_service.py:66) 加 `model_user_input` 到 hidden list
- live SSE handler `SSEHandler._should_skip()` 同步加

**1b. AGENT.md hash anchor 决策**:
- 实现 §8.2 的 service 层装配决策（is_anchor 判定 + 写 model_user_input）
- 实现 `_latest_anchor_user_instructions_hash` 查询
- 实现 size cap (50KB) + hash 计算
- **保留** `_apply_user_instructions_to_initial_user_query` 共存一段时间，behind flag 控制
- 默认 flag 关闭（仍走现状），灰度开启走新路径

**1c. history_checkpoint payload 扩展**:
- `HistoryCheckpointService.build_checkpoint_sink` payload 加 `schema_version`、`render_version`、`user_instructions_text`、`user_instructions_hash`
- [history_checkpoint_codec.py](../../src/services/history_checkpoint_codec.py) 接受 v0/v1 双 marker（`<previous_session_summary>` / `<compacted_history>`）
- 写入时仍输出 v0 marker（Phase 3 切到 v1）

**1d. ModelHistoryRestoreService 分流**:
- 改名 [history_restore_service.py](../../src/services/history_restore_service.py) → `model_history_restore_service.py`
- 内部实现 §11.1 的 schema-aware 分流
- v0 路径委托 `ChatHistoryConverter.events_to_dialog_messages`
- v1 路径同时消费 `model_user_input` + `assistant_state` + `response`/`run_result` + `tool_result`

**1e. 测试目标**:
- 单元测试：`ModelHistoryRestorer._restore_v1` 各分支
- 单元测试：AGENT.md hash 决策（首轮 / hash 未变 / hash 变 / 50KB 超限 / 文件不存在）
- 集成测试：完整 session 写入 + 恢复
- 兼容测试：v0 session 的 restore 等价于现 `HistoryRestoreService`

---

### Phase 2: Context 模块阶段

**目标**: 新建 `matmaster/context/` 内核，让输出**等价于现状**。不改任何业务行为，只重组渲染路径。

**2a. 新增文件**:

按依赖顺序：
1. `matmaster/context/sections.py`（含 `__post_init__` 校验）
2. `matmaster/context/rendering.py`（含 tag escape）
3. `matmaster/context/user_message.py`（含 key 唯一性校验）
4. `matmaster/context/sources/active_turn.py`
5. `matmaster/context/sources/user_instructions.py`
6. `matmaster/context/sources/attachments.py`
7. `matmaster/context/sources/skills.py`
8. `matmaster/context/sources/tools.py`
9. `matmaster/context/sources/compacted_history.py`
10. `matmaster/context/sources/session_jobs.py`（占位）
11. `matmaster/context/sources/workspace.py`（占位）
12. `matmaster/context/sources/artifacts.py`（占位）
13. `matmaster/context/scanner.py`
14. `matmaster/context/session.py`
15. `matmaster/context/history_restore.py`（Phase 1 接口骨架的完整实现）
16. `matmaster/context/system_prompt.py`

**2b. shim 改造**:
- `matmaster/manifests/*` 改为薄 shim 委托新 source
- `matmaster/types/current_input.py` re-export `ActiveTurnContext`
- `matmaster/types/context.py` re-export `PlaygroundContext`（迁回 `core/playground.py`，注意现有反向 import 循环：[playground.py:26](../../matmaster/core/playground.py:26) 现 import from types/context；要小心拆环）

**2c. 业务代码切换 import**:
- [matmaster/core/agent.py](../../matmaster/core/agent.py) import 从 `matmaster.manifests` 切到 `matmaster.context`
- [src/services/agent_run_service.py](../../src/services/agent_run_service.py) 装配 `ActiveTurnContext`（含 `source_query_event_id`），调 `UserContextMessage.from_sources`，再投影为 UserMessage，再写 model_user_input
- **此时**可以移除 Phase 1 的 flag：service 层完全走新路径，删除 `_apply_user_instructions_to_initial_user_query`

**2d. Prompt 形态**:
- **沿用现状**: `TurnRequestContext` 和 `TurnMaterialsContext` 合并到一个 `<current_instruction>` block（兼容当前 `[Current attachments]` 拼接方式）
- 拆分版可由 `__init__` 参数或 feature flag 切换，但**默认关闭**

**2e. 测试目标**:
- Phase 1 测试全部仍通过
- 单元测试覆盖每个 source 的 `to_sections`
- 单元测试覆盖 `wrap_tag` escape（含 `</tag>` 注入用例）
- 单元测试覆盖 `from_sources` 的 key 唯一性校验
- 单元测试覆盖 `__post_init__` 不变量校验

---

### Phase 3: Compaction 接入 + Prompt 形态决策

**目标**: 把 preflight / runtime compaction 接入新 renderer，切到 v1 marker，做 prompt 形态 A/B。

**3a. compaction.py 迁移**:
- 把 `core/context_compactor.py` 内容迁到 `context/compaction.py`
- `core/context_compactor.py` 改为薄 shim
- 装配方式从手写字符串改为 `UserContextMessage` + view 投影
- **fallback 保留**（`sliding_window` / `tool_truncation`），但加埋点（命中率、成功率）

**3b. checkpoint payload 切到 v1**:
- compaction sink 写 `schema_version="history_checkpoint.v1"` + `<compacted_history>` marker
- codec 仍接受双 marker（向后兼容）

**3c. Prompt 形态 A/B**:
- 在 Phase 2 末或 Phase 3 起手时做 offline eval（见 §6.5 评估维度）
- 通过则启用 `<turn_materials>` 拆分（默认）
- 不通过则保留合并形态，调整 tag 名后再 A/B

**3d. 测试目标**:
- 压缩前后 `restore_v1` 行为正确
- 多次压缩链路正确
- prompt 形态切换不破坏 tool 调用
- fallback 路径仍可触发并写 ephemeral checkpoint

---

### Phase 4: 清理 + Oversized Input（独立 spec）

**4a. 清理**:
- 删除所有 shim（`matmaster/manifests/`、`matmaster/core/context_builder.py`、`matmaster/core/context_compactor.py`、`matmaster/types/context.py`、`matmaster/types/current_input.py`）
- `AgentRuntimeSpec.context_builder: ContextBuilder` → `system_prompt_builder: SystemPromptBuilder` rename（一次性 PR）
- 测试目录从 `tests/matmaster/manifests/` 迁到 `tests/matmaster/context/`

**4b. v0 兼容性退役**:
- `history_checkpoint_codec.py` 移除 v0 marker
- 前提：所有线上 session 的最新 checkpoint 已超过 30 天

**4c. Oversized Input 独立 spec**:
- 不在本 spec 范围
- 需要单独设计 `InputSummaryConfig`、原文写盘策略、路径安全、失败处理
- 接口预留点：`ContextCompactor.apply_compaction_plan(preset_summary, preset_past_attachments)` + `model_user_input.transform="oversized_summary"` 已就位

**4d. Fallback 删除决策**:
- 基于 Phase 3 埋点的命中率与成功率数据
- 删除决策独立 PR，不在 Phase 4 主线

---

## 15. `ContextBuilder` 拆解 + `AgentRuntimeSpec` 演化

[matmaster/core/context_builder.py](../../matmaster/core/context_builder.py) 中的 `ContextBuilder` 类**废弃**，三段职责拆分：

| 旧方法 | 新位置 |
|--------|--------|
| `build_system_prompt(...)` | `matmaster/context/system_prompt.py` 中的 `SystemPromptBuilder` 类 |
| `build_user_request(...)` | `matmaster/context/user_message.py` (`UserContextMessage.from_sources`) + `sources/active_turn.py` + `sources/attachments.py` |
| `build_compact_bundle(...)` | `matmaster/context/sources/compacted_history.py` + `user_message.py` |
| `_tag(...)` 等 helper | `matmaster/context/rendering.py` (`wrap_tag`) |

`AgentRuntimeSpec.context_builder` 字段演化（与 v2 保持一致）：

- Phase 1: 字段不变
- Phase 2: 字段不变，`ContextBuilder` 内部退化为只暴露 `build_system_prompt(...)` 的 wrapper
- Phase 4: 字段重命名为 `system_prompt_builder: SystemPromptBuilder`，删除 shim

---

## 16. 测试覆盖

```
tests/matmaster/context/
  test_sections.py
    - empty content filter
    - view filter (RUNTIME / CHECKPOINT)
    - order stable sort
    - __post_init__: invariant RUNTIME ⊇ CHECKPOINT（负向 case）
    - __post_init__: empty key / tag（负向 case）
  test_rendering.py
    - wrap_tag basic
    - wrap_tag escape: content 含 </tag> 时被替换
    - render_sections multi-section ordering
  test_user_message.py
    - from_sources key 唯一性校验（负向 case）
    - render(view) 输出等价（双视图对比）
  test_compaction.py
    - apply_compaction_plan with active_turn / without
    - summary 成功 → durable
    - summary 失败 → ephemeral + fallback strategy
    - preset_summary 旁路
    - user_instructions_text / hash 传递到 CompactionResult
  test_session.py
    - build_sections include / exclude attachments
    - until_event_id 边界
  test_history_restore.py
    - 无 checkpoint + 无 model_user_input → legacy_restore 委托
    - 无 checkpoint + 有 model_user_input → v1 路径
    - v1 checkpoint → v1 路径
    - v0 checkpoint → legacy_restore 委托
    - 多 model_user_input 顺序追加
    - response 与 assistant_state 同 turn 不冲突（不存在该情况，但 defensive）
    - tool_result restore
    - spawn_id 过滤
    - ImageContentPart 嵌套反序列化
  sources/
    test_user_instructions.py
      - size cap truncate + warning
      - empty 返回空 sections
      - hash 计算稳定（同输入同 hash）
    test_active_turn.py
      - has_effective_input 边界
      - images_as_parts 转换
      - source_query_event_id 必填
    test_attachments.py
      - from_events
      - with_added (Case 3 预留)
    test_skills.py / test_tools.py
      - 从 events 重建 skill 列表
      - allowed_mcp_servers 过滤
    test_compacted_history.py
      - empty summary → 空 section
  integration/
    test_compaction_roundtrip.py
      - Case 1 / 1b / 1c / 2 / 4 端到端
      - 写 model_user_input → restore_v1 等价
    test_multi_compaction.py
      - 两次压缩链路
    test_codec_v0_v1_compat.py
      - 旧 checkpoint 仍能 restore
      - 新 checkpoint v1 marker 校验通过
    test_agent_md_responsiveness.py
      - 首轮 → 改 AGENT.md → 第 2 轮立即反映
      - 改 AGENT.md → 压缩触发 → anchor 含新内容
    test_sse_filter.py
      - model_user_input 不出现在 replay
      - assistant_state 不出现在 replay
      - history_checkpoint 不出现在 replay
```

Phase 1 内 `tests/matmaster/manifests/` 保留并继续通过（验证 shim 等价性）；Phase 4 删除并迁到 `tests/matmaster/context/`。

---

## 17. 风险与未决问题

### 高优先级

1. **Phase 0 DAO 改造的并发安全**: `INSERT ... RETURNING id` 与现有事务边界的兼容性需在 Phase 0 落地时验证。`prepare_send_message` 流程中 User/query 与 model_user_input 是否需要同事务？建议**同事务**，防止部分失败导致孤立的 User/query 没有 model_user_input。

2. **Phase 1 灰度策略**: Phase 1 默认 flag 关闭（仍走 `_apply_user_instructions_to_initial_user_query`）。如何评估"可以默认打开"？建议：
   - 灰度 5% 用户 7 天
   - 监控 model_user_input 写入失败率（应 < 0.1%）
   - 监控 restore_v1 vs legacy_restore 的 messages 序列等价性（offline diff）
   - 监控 AGENT.md 修改后首轮生效率

3. **多次压缩 + AGENT.md 变更的边界**: 用户在两次压缩之间多次改 AGENT.md，会生成多条 anchor model_user_input。如果其中夹杂 continuation，messages 序列中会有多个 anchor。预期 LLM 自然理解，但需要 prompt 评估验证。Phase 1 末做一次小规模 case study。

### 中优先级

4. **`SessionJobsContext` 占位与未来接入的接口契约**: 数据接入留待 bohrium tool job table + hot cache 系统建好。接入时只需修改 `SessionJobsContext`（增加 classmethod 如 `from_job_ledger(...)`），不动 source 接口和 order 表。

5. **fallback 命中率埋点**: Phase 3 加埋点，30 天后评估是否删 `sliding_window` / `tool_truncation`。在没有数据前**不删**。

6. **prompt cache 命中率**: AGENT.md hash 变化产生新 anchor 会让 prompt prefix 变化，cache miss。当前实现每轮都重写 first user message，cache miss 是已有现实。新设计在 cache 维度**至少不退步**。Phase 3 可加埋点对比。

### 低优先级

7. **`UserContextMessage` 与未来 sub-agent handoff 视图**: 当前 ContextView 只有 RUNTIME / CHECKPOINT。未来 sub-agent 可能需要新视图（如 SUBAGENT_HANDOFF），届时再加。本 spec 不预留。

8. **`schema_version` / `render_version` 演化矩阵**: Phase 1 后只有 v1，未引入 v2。未来 v2 升级时再单独设计 codec 分发表。

9. **`run_meta` 字段整改**: `run_meta` 字典本身仍是 god bag。后续重构应考虑把 `run_meta` 替换为 typed dataclass（如 `AgentRunMeta`），但**不在本次范围内**。

---

## 18. 不在本次范围

- bohrium job table + hot cache 系统的搭建（`SessionJobsContext` 只占位）
- Oversized input offload（Case 3 / Phase 4 独立 spec）
- `run_meta` 整体 typed 化
- LLM provider 抽象层重构
- Tool calling schema 重写
- 前端 chat 历史展示组件改造（仍走现 SSE replay）
- AGENT.md `/reload-agent-md` 显式命令机制
- kernel `assistant_state` 写入条件扩展（自然结束写 assistant_state）
- Sub-agent checkpoint 语义扩展
- Fallback 路径删除（依赖 Phase 3 埋点数据）

---

## 附录 A: 名词对照

| 概念 | 描述 |
|------|------|
| Raw transcript history | 由 User/query / response / tool_result 等原始事件组成的对话流，前端回放与审计的数据源 |
| Model-visible history | 后端发给 LLM 的真实消息序列，由 system prompt + base_messages (from checkpoint) + 后续 model_user_input / assistant_state / response / tool_result 重建 |
| `model_user_input` | 新事件类型，每个真实用户 turn 最多一条，记录 provider-facing UserMessage 事实 |
| Anchor | 装配了完整长尾 sources（UserInstructions / SessionContext / ActiveTurn）的 user message。出现条件：session 首轮 OR AGENT.md hash 变化的轮 OR 压缩触发后的轮 |
| Continuation | 只装配 ActiveTurn 的 user message，依赖更早 anchor 提供长尾 sections |
| Source | `matmaster/context/sources/` 下的 frozen dataclass，自带 `to_sections() -> tuple[ContextSection, ...]`，互相独立不依赖 |
| Section | `ContextSection` 实例，渲染单元 |
| View | `ContextView`，渲染时的视图选择（RUNTIME / CHECKPOINT），不参与恢复。不变量 `RUNTIME ⊇ CHECKPOINT` |
| Checkpoint | `history_checkpoint` event 的 v1 payload，含 base_messages、user_instructions_text/hash、schema_version、render_version、covered_until_event_id |
| `source_query_event_id` | 关联 `User/query` 事件的 id，每个 `model_user_input` 必填 |
| `pre_turn_history_event_id` | 本轮 User/query 和 model_user_input 事件写入前的最后 event id，用于 preflight compaction 划定 checkpoint 覆盖边界 |
| `user_instructions_hash` | AGENT.md 文本的 sha256，service 层用于判定是否需要新 anchor |
| `transform` | model_user_input.payload 字段，`"raw"` / `"preflight_compacted"` / `"oversized_summary"`（最后一个 Phase 4 落地）|

---

## 附录 B: 与现有代码的具体衔接点

本附录列出 v3 实现期间必须改动的具体文件和函数，方便 PR 切分时对照。

### Phase 0 改动

- [src/dao/chat_events_table.py:301-325](../../src/dao/chat_events_table.py:301) `add_event`: `INSERT ... RETURNING id`
- [src/services/events_service.py:24-73](../../src/services/events_service.py:24) `add_history_event`: 返回 inserted id
- 所有 caller: `prepare_send_message`、worker entry、其他 `add_event(` 调用点

### Phase 1 改动

- [src/services/stream_service.py:66](../../src/services/stream_service.py:66) `_should_emit_event_to_sse`: 加 `model_user_input` hidden
- `matmaster.integration.event_router.SSEHandler._should_skip()`: 同步加
- [src/services/agent_run_service.py:775-780](../../src/services/agent_run_service.py:775) `_apply_user_instructions_to_initial_user_query`: 保留共存，behind flag
- [src/services/agent_run_service.py:182-209](../../src/services/agent_run_service.py:182) `_apply_user_instructions_to_initial_user_query` 实现: Phase 2 后删除
- [src/services/history_checkpoint_service.py:26-55](../../src/services/history_checkpoint_service.py:26) `build_checkpoint_sink`: payload 加新字段
- [src/services/history_checkpoint_codec.py:89-91](../../src/services/history_checkpoint_codec.py:89) marker 校验: 接受 v0 + v1 双 marker
- [src/dao/chat_events_table.py:327-](../../src/dao/chat_events_table.py:327) `add_history_checkpoint`: content payload 加新字段
- [src/services/history_restore_service.py](../../src/services/history_restore_service.py) 改名 + 内部委托新模块

### Phase 2 改动

- [matmaster/manifests/](../../matmaster/manifests/) 整目录改 shim
- [matmaster/types/current_input.py](../../matmaster/types/current_input.py) shim
- [matmaster/types/context.py](../../matmaster/types/context.py) shim（注意拆解 [playground.py:26](../../matmaster/core/playground.py:26) 的反向 import 循环）
- [matmaster/core/agent.py:336-347](../../matmaster/core/agent.py:336) kernel 入口改造：用 history 末尾的 UserMessage，不再装配 active_turn
- [src/services/agent_run_service.py](../../src/services/agent_run_service.py) 完整切到新路径，删除 `_apply_user_instructions_to_initial_user_query`

### Phase 3 改动

- [matmaster/core/context_compactor.py](../../matmaster/core/context_compactor.py) → shim
- [matmaster/core/context_builder.py](../../matmaster/core/context_builder.py) → shim
- Checkpoint 写入切到 v1 marker
- prompt 形态 A/B 评估 + 切换

### Phase 4 改动

- 删除所有 shim
- 字段 rename
- v0 marker 退役
