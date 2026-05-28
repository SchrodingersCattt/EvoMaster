# 2026-05-17 · core 重构后续待办：被 /simplify 推迟的简化项

> 上下文：分支 `refactor/context` 把 `matmaster/core/agent.py` 拆成多个子模块
> （`agent_compaction.py` / `agent_llm_stream.py` / `agent_tool_dispatch.py` /
> `runtime_context_assembly.py`），同时移除 `matmaster/core/context_builder.py`
> 与 `matmaster/core/context_compactor.py`，把对应逻辑下沉到 `matmaster/context/`。
>
> 2026-05-17 在该分支上跑了 `/simplify`，三个并行 review agent（reuse / quality /
> efficiency）一共产出 ~20 项发现。本次直接落地了 12 项可控的清理（见 `git log`
> 后续 commit；如未 commit，则见工作树 diff）。
>
> 下面列出本次**有意推迟**的 5 类问题，按工作量从小到大排列，方便后续作为
> 独立 PR / 单独 milestone 推进。

---

## R1–R5: 跨层级重复——4 个 helper 需要下沉到 `matmaster/context/`

### 现状

`matmaster/core/runtime_context_assembly.py` 在重构中引入了 4 个内部 helper，
它们与 `src/services/context_assembly_factory.py` / `context_assembly_ports.py`
里的对应实现**字节级或语义级重复**：

| # | matmaster/core/runtime_context_assembly.py | src/services/ 中的现存实现 | 重复程度 |
|---|--------------------------------------------|----------------------------|----------|
| R1 | `_hash_user_instructions(text)` (line 37–38) | `user_turn_context_service.py:hash_user_instructions(text)` + `context_assembly_ports.py:_hash_user_instructions(text)` | 字节级，三份完全相同 |
| R2 | `_RuntimeHistorySessionEventsPort` (line 58–70) | `context_assembly_factory.py:RuntimeHistorySessionEventsPort` (line 72–84) | 字节级，连 `coerce_session_events` 导入都一样 |
| R3 | `_EmptySessionJobsPort` (line 73–75) | `context_assembly_ports.py:AppSessionJobsPort` (line 116–121) | 语义级，都包装 `SessionJobs.empty()` |
| R4 | `_build_session_context_factory` (line 27, 41–55) | `context_assembly_factory.py:build_session_context_factory` (line 24, 27–43) | 字节级，类型别名 `SessionContextFactory` 也重复 |

### 为什么会重复

根因是分层架构约束：

- `matmaster/` 是 agent runtime 核心，不能依赖 `src/`（平台服务层）。
- `src/services/context_assembly_factory.py` 同样需要这些 helper。
- 重构时为了让 `matmaster/core/runtime_context_assembly.py` 自包含，作者就近
  在 `matmaster/core/` 里**复刻**了一份 helper，留下"两份正确实现"的局面。

### 推迟原因

修复方案需要：

1. 在 `matmaster/context/` 下新建 helper 模块（候选位置）：
   - `matmaster/context/_hashing.py`（专门放 `hash_user_instructions`）
   - 或扩展现有 `matmaster/context/ports.py` / `matmaster/context/assembly.py`
2. 同时改 `matmaster/core/runtime_context_assembly.py` 与
   `src/services/context_assembly_factory.py` / `context_assembly_ports.py`
   两侧的 import 与使用点。
3. 需要回归测试覆盖跨层调用（`tests/matmaster/services/test_context_assembly_*` +
   `tests/services/test_context_assembly_factory.py`）。

改动会跨 `matmaster/` 与 `src/services/`，影响 2–4 个文件之外的 import 与
**至少 3 个测试文件**。比纯 cosmetic 改动风险高，与本次 `/simplify` 的
"contained scope" 目标不符。

### 建议落地方案

提一个独立 PR：`refactor(context): consolidate runtime context assembly helpers`

PR 步骤：

1. 新建 `matmaster/context/runtime_factory.py`，把 4 个 helper 移过来并导出
   公开 API：
   - `hash_user_instructions(text: str) -> str`
   - `EmptySessionJobsPort`（class）
   - `RuntimeHistorySessionEventsPort`（class）
   - `build_session_context_factory(...) -> SessionContextFactory`
   - `SessionContextFactory`（type alias）
2. 让 `matmaster/core/runtime_context_assembly.py` 删除 4 个内部副本，
   全部 `from matmaster.context.runtime_factory import ...`。
3. 让 `src/services/context_assembly_factory.py` 与
   `src/services/context_assembly_ports.py` 同样 import，不再自定义。
4. `src/services/user_turn_context_service.py` 也改用 `hash_user_instructions`
   公共函数。
5. 跑全套 `tests/matmaster/services/` + `tests/services/` 验证。

风险点：

- `coerce_session_events` 当前在 `matmaster/context/scanner.py`，移 helper 时
  要注意 import 循环（建议把 `RuntimeHistorySessionEventsPort` 直接放在
  `scanner.py` 同模块里，或建一个 ports 子模块）。
- 跨 `matmaster/` 与 `src/` 的双 import 在 CI 中已有静态边界检查
  （`tests/matmaster/context/test_phase4_static_boundaries.py`），新模块需要
  确认满足边界规则。

---

## R6: SkillRegistry 在 service 层与 core 层重复构造，且行为已分叉

### 现状

两处独立构造 `SkillRegistry`，输入字段集合不一致：

| 字段 | `src/services/agent_run_service.py:164` `_build_skill_registry` | `matmaster/core/exp.py:705` `_init_skill_tools` |
|---|---|---|
| `skills_root` (str / list[str]) | ✅ | ✅ |
| `_local_user_skills_root(ctx.session)` | ❌ 不读 | ✅ append 到 roots |
| `remote_roots` | ✅ | ✅ |
| `_disabled_skill_names_from_settings(root)` + `skills_cfg.disabled_skill_names` | ❌ | ✅ |
| `registry.remove_skills(disabled)` | ❌ | ✅ |

service 层的 registry 喂给 `resolve_active_skills(events, registry)` 做 active
skill 还原（用于 prompt 的"已加载 skill"显示 + LazyMCP replay）；core 层的
registry 真正注册 `SkillTool` / `LazyMCPTool` 到运行时工具集（决定哪个工具能被
LLM 调用）。

### 为什么是问题

不是字节级 / 函数级重复，而是**行为已分叉**：

- 当 session 配了本地 user skill root，service 层 registry 看不到 → 该 root
  下的 skill 永远不会被 service 层视作 active → prompt 里"已加载 skill"列表
  与 runtime 实际可用 skill 不一致。
- 当 settings 或 config 禁用了某个 skill，service 层 registry 仍把它视作
  可恢复 → service 层可能把"已禁用但曾命中的 skill"塞回 active 集合 → core
  层不会注册它，但 prompt / active MCP replay 以为它在。

prompt rendering / LazyMCP activate / tool catalog 三者依据的"已加载 skill
集合"不一致，是真实的一致性 bug 风险，而不是 cosmetic 重复。

### 推迟原因

修复需要决策两件事：

1. **抽 helper 的归属**：候选位置 `matmaster/skills/build.py` 新模块或
   `matmaster/skills/registry.py` 顶层增加一个 `build_skill_registry(...)`，
   接受 `(skills_root, session, *, disabled_names, apply_settings_disable)`
   组合参数，service 层与 core 层都调它。
2. **disabled 规则是否对 service 层 rehydration 也生效**——这是产品决策。
   理论上 service 层与 core 层视图应一致，但需要先确认 active-skill
   rehydration 是否有意要"包含历史命中过但现在已禁用的 skill"用于审计 /
   显示。决策前不要偷偷统一。

涉及测试：
- `tests/services/test_agent_run_*` 覆盖 service 层 registry 构造
- `tests/matmaster/core/test_exp_skills.py` 覆盖 core 层 skill init
- 新增 invariant 测试：service 层 active-skill 集合 ⊆ core 层 registered-skill
  集合（除非 disabled 规则被显式豁免）

### 建议落地方案

独立 PR：`refactor(skills): consolidate SkillRegistry construction`

风险点：

- service 层 `_build_skill_registry` 在 `agent_run_service.py:224` 和 `:541`
  被调用，影响 prompt 里 `<active_skills>` 显示与 LazyMCP replay；统一前后
  prompt 输出可能变化，需要回归 prompt baseline 测试。
- helper 跨 `src/services/` 与 `matmaster/core/` 调用，需满足
  `tests/matmaster/context/test_phase4_static_boundaries.py` 的边界规则。

---

## R7: SessionEvent 反序列化逻辑错置在 matmaster/context/，导致跨层语义分叉

> 独立执行计划已迁移到
> [2026-05-17-r7-session-event-decoding.md](2026-05-17-r7-session-event-decoding.md)。
> 本节只保留问题摘要与关键设计决策；执行时以独立 plan 为准，完成后只开一次 PR。

### 现状

`matmaster/context/scanner.py:17-84` 定义了一组从 DAO row dict 反序列化为
`SessionEvent` 的函数：

- `_freeze_json_value` — 把 Python 值递归冻结为 `JsonValue` 树
- `_coerce_content` — 把任意 content 字段规整为 `JsonObject`
- `coerce_event_id` — 把任意 id 字段 try-int 化
- `_coerce_optional_str` — 把任意 optional string 字段 strip + null 化
- `coerce_session_events(rows: Iterable[Mapping]) -> tuple[SessionEvent, ...]`
  — 顶层反序列化入口，输入是 `list[dict]`，输出是 typed tuple

`src/services/context_assembly_ports.py:27-105` 又定义了一份**功能等价但语义
分叉**的反序列化：

- `_freeze_json_value` / `_freeze_json_object` / `AppSessionEventsPort._row_to_event`

两份代码的存在违反了仓库的分层约束（matmaster/* 不应依赖 src/*，也不应该
知道 DB row 的字段名）。matmaster 里出现 DAO row → typed object 的转换器是
**架构违规**——这段代码应该完全在 service 层。

#### 调用图

```
src/services/agent_run_service.py:545 (run_agent path)
  └─ build_context_assembler(events_table=...)
       └─ AppSessionEventsPort  ── 反序列化 Path B ──→ ContextAssembler

src/services/agent_run_service.py:225 (active skill rehydration)
  └─ coerce_session_events(raw_events) ── 反序列化 Path A ──→ resolve_active_skills

matmaster/core/runtime_context_assembly.py:128 (runtime compaction path)
  └─ RuntimeHistorySessionEventsPort (matmaster 内)
       └─ history_port.query_context_events ── 返回 list[dict]
            └─ coerce_session_events ── 反序列化 Path A ──→ ContextAssembler
```

同一份 DB rows，在 runtime compaction 路径走 Path A，在 prompt assembly
重建路径走 Path B，**两条路径产生 schema 不同的 `SessionEvent`** 喂给同一个
`ContextAssembler`。

### 为什么会形成这个结构

直接原因在 `RuntimeHistorySessionEventsPort` 这个 adapter
（`matmaster/core/runtime_context_assembly.py:58-70`）：

- 它接收的 `history_port` 来自 `_RunSessionEventHistory`
  （`src/services/agent_run_history_wiring.py:156-184`），后者实现
  `SessionEventHistoryPort` protocol（`matmaster/types/runtime_ports.py:52`），
  **该 protocol 的 `query_context_events` 返回 `list[dict[str, Any]]`**。
- 为了把 dict 升级为 typed `SessionEvent`，桥接逻辑被写在了 matmaster 里
  （在 service 层做更合理）。
- 后续 Phase 2A 引入 `SessionEventsPort` + `AppSessionEventsPort`，但
  `SessionEventHistoryPort` 这个早期 protocol 没同步升级，留下迁移期 shim。

scanner.py 这个文件名暗示"扫描 typed events"，但反序列化器和真正的扫描函数
（`scan_skill_hits` / `_skill_name_from_content` / `SkillHitRecord`）被混在
同一个文件，掩盖了职责违规。

### 10 项行为差异（B1 完整清单）

| # | 维度 | Path A（scanner） | Path B（ports） | 风险等级 |
|---|------|-------------------|------------------|----------|
| D1 | 未识别 Python 类型 | `str()` 降级 | 抛 `TypeError` | 高（生产稳健性 vs schema drift 防御对立） |
| D2 | 非 mapping content 包装 key | `{"content": ...}` | `{"value": ...}` | 高（`scan_skill_hits` 依赖 "content" key） |
| D3 | None content | `{}` | `{"value": None}` | 低（下游断言一致） |
| D4 | 无合法 id | 丢弃整行 | `id=0` sentinel | 高（正确性 bug） |
| D5 | 非 Mapping row | `continue` 跳过 | 抛 KeyError/TypeError | 低 |
| D6 | event_type 备用键 | 仅 "type" | "type" or "event_type" | 低 |
| D7 | event_type strip | ✅ | ❌ | 中 |
| D8 | source/task_id/... normalize | "" → None + strip | 原值透传 | 中 |
| D9 | 时间字段 | 注入 `content.created_at` | 无顶层字段；DAO context row 实际输出 `created_at_ms` | 中（skill hit 时间 metadata 需要顶层字段） |
| D10 | 顶层 schema 综合差异 | 含注入 created_at + "content" 包装 | 不含时间字段 + "value" 包装 | 综合后果 |

测试约定冲突（必须改测试才能统一）：

- `test_freeze_json_object_rejects_non_json_schema_drift`
  （`tests/matmaster/services/test_context_assembly_ports.py:184`）显式依赖
  Path B 的 `TypeError` 行为。
- `test_app_session_events_port_preserves_falsy_raw_content`
  （`tests/matmaster/services/test_context_assembly_ports.py:181`）显式断言
  `{"value": ""}`。
- `test_coerce_session_events_drops_rows_without_int_id`
  （`tests/matmaster/context/test_scanner.py:80`）显式约定丢弃无合法 id 的 row。
- `test_scan_skill_hits_accepts_legacy_string_content_via_coerce`
  （`tests/matmaster/context/test_scanner.py:107`）间接依赖 Path A 的
  "content" 包装 key。

### 终态方案（已对齐决策）

**决策 1**：反序列化器放在 `src/services/session_event_codec.py`（新模块）。

**决策 2**：行为对齐方向 — 保留 ports 严格行为 + 加
`SessionEvent.created_at_ms`：

- D1：抛 `TypeError`（schema drift 早期发现）
- D2：codec 写 `{"value": ...}`（更中性，避免
  `SessionEvent.content.content` 嵌套混淆）；`scan_skill_hits` 兼容
  `skill_name` / `value` / 迁移期 `content` 三种 key，避免 legacy string
  skill_hit 在中间态丢失。
- D3：`None` content 保留为 `{"value": None}`，与 ports 当前行为一致。
- D4：丢弃无合法 id 的 row（修正 ports 现行的 id=0 bug）
- D5：bulk `decode_session_events()` 跳过非 Mapping row；单 row
  `row_to_event()` 抛 `TypeError`。
- D6：codec 支持 `type` 或 `event_type`。
- D7：codec 对 event type 执行 strip。
- D8：codec 对 `source` / `task_id` / `invocation_id` / `spawn_id`
  执行 strip，并把空字符串归一为 None。
- D9：删除 content 注入，给 `SessionEvent` 加
  `created_at_ms: int | None = None` 顶层字段；codec 同时兼容
  DAO 的 `created_at_ms` 与原始 datetime `created_at` 输入。

**决策 3**：合并 `_RunSessionEventHistory` 与 `AppSessionEventsPort` —
让前者直接实现 `SessionEventsPort` protocol，删除 matmaster 里的
`RuntimeHistorySessionEventsPort` adapter。

#### Before / After 架构

```
Before:
  DAO → list[dict] ──┬─→ _RunSessionEventHistory (返回 list[dict])
                    │     └─→ RuntimeHistorySessionEventsPort (matmaster)
                    │           └─→ coerce_session_events (matmaster)
                    │                 └─→ ContextAssembler
                    └─→ AppSessionEventsPort._row_to_event (service)
                          └─→ ContextAssembler

After:
  DAO → list[dict] ──→ session_event_codec.row_to_event (唯一反序列化点)
                          │
                          ├─→ _RunSessionEventHistory (实现 SessionEventsPort)
                          │     └─→ ContextAssembler
                          └─→ AppSessionEventsPort
                                └─→ ContextAssembler

  matmaster/context/scanner.py:
    - 删除反序列化函数（coerce_session_events 等）
    - 保留 scan_skill_hits / _skill_name_from_content / SkillHitRecord
```

### 实施 Phase 拆分（单 PR 内分阶段 commit / checkpoint，可单独回滚）

以下 Phase 只作为实现顺序与验证 checkpoint，不作为 PR 边界。R7 应在所有
目标 Phase 完成、测试通过并确认中间兼容风险已消除后，再统一提交一次 PR。

#### Phase 1：新建 codec，零行为变化

- 新建 `src/services/session_event_codec.py`，把
  `AppSessionEventsPort._row_to_event` + `_freeze_json_value` +
  `_freeze_json_object` 抽出来变成模块级 `row_to_event` /
  `decode_session_events`。
- `AppSessionEventsPort._row_to_event` 改成转发到 codec。
- 新建 `tests/matmaster/services/test_session_event_codec.py`，迁移现有
  ports 测试 + 加边界 case。
- 风险：极低（纯重命名/移动）。验证：`tests/matmaster/services/`、
  `tests/matmaster/context/` 全绿。

#### Phase 2：行为对齐（关键风险点）

- 在 codec 里实现决策 2 选定的行为：
  - D2：保留 `{"value": ...}` 包装
  - D4：实现 `coerce_event_id` 等价逻辑，无合法 id 时返回 None；
    `decode_session_events` 过滤 None
  - D1：保留 `TypeError`
- 配套改测试断言（`test_freeze_json_object_rejects_non_json_schema_drift`
  保留；`test_app_session_events_port_preserves_falsy_raw_content` 保留；
  新增 D4 丢弃测试）。
- 修 D2 耦合点：`matmaster/context/scanner.py:_skill_name_from_content`
  改成读取 `content.get("skill_name") or content.get("value") or
  content.get("content")`，并保留 string content 直接处理的分支（兼容
  scanner 内现有调用 site）。
- 风险：中。涉及产品决策落地，需要团队 review。
- 验证：`tests/matmaster/context/`、`tests/matmaster/services/`、
  `tests/services/` 全绿；新增 D2 / D4 回归测试。

#### Phase 3：升级 `_RunSessionEventHistory` 到 typed contract

- 改 `matmaster/types/runtime_ports.py` 的 `SessionEventHistoryPort` protocol：
  - 方案 A：把 `query_context_events(...) -> list[dict]` 改成
    `load_events(query: SessionEventQuery) -> tuple[SessionEvent, ...]`
    （与 `SessionEventsPort` 完全对齐）。
  - 方案 B：保留 `query_context_events` 名字但改返回类型。
- 改 `agent_run_history_wiring._RunSessionEventHistory` 实现新签名，内部
  调用 codec。
- 删除 `matmaster/core/runtime_context_assembly.py` 的
  `RuntimeHistorySessionEventsPort` adapter，让 `_RunSessionEventHistory`
  直接作为 `ContextAssemblyPorts.session_events`。
- 改所有测试 mock 的 history_port（grep `query_context_events.*list\[dict\]`，
  预估 5-8 个 mock）。
- 风险：中-高。protocol 变更，多处 import 改动。
- 验证：`test_phase4_static_boundaries` + 全套 services 测试 +
  `test_hook_wiring` + `test_agent_kernel_compaction`。

#### Phase 4：删除 matmaster/context/scanner.py 的反序列化函数

- 从 scanner.py 删除 `_freeze_json_value` / `_coerce_content` /
  `coerce_session_events` / `coerce_event_id` / `_coerce_optional_str`。
- 保留 `scan_skill_hits` / `_skill_name_from_content` / `SkillHitRecord`。
- 删除或迁移 `matmaster/context/sources/attachments.py` 中只消费 raw row 的
  `scan_legacy_attachment_entries`，避免 Phase 4 删除 `coerce_event_id` 后出现
  确定性 ImportError；当前 grep 显示该 legacy scanner 只有测试引用，可直接
  删除函数与对应测试。
- 改 `src/services/agent_run_service.py:225`
  `coerce_session_events(raw_events)` 改用
  `session_event_codec.decode_session_events`。
- 改 `tests/matmaster/context/test_scanner.py` 删除反序列化相关测试（已迁移
  到 codec 测试）。
- 改 `tests/matmaster/context/sources/test_*.py` 里 `coerce_session_events`
  调用为直接构造 typed `SessionEvent` fixture；core context 测试不要 import
  service codec。
- 风险：中。删 public-ish API（很多测试 import 这些），但替换路径已经在
  Phase 1 准备好。
- 验证：全套 `tests/matmaster/context/`、`tests/matmaster/services/`、
  `tests/services/`。

#### Phase 5：`SessionEvent.created_at_ms` 字段提升

- 改 `matmaster/context/ports.py` 给 SessionEvent 加
  `created_at_ms: int | None = None`。
- codec 不再注入 created_at 到 content，改写顶层 `created_at_ms` 字段。
- `scan_skill_hits` 改用 `event.created_at_ms`。
- 更新所有 SessionEvent fixture（grep `SessionEvent(`，预估 30+ 处）。
- 风险：低-中。fixture 更新机械化但量大。
- 验证：全仓 grep 对比 fixture；history_checkpoint_codec 不依赖
  SessionEvent.created_at_ms（确认 schema 兼容）。

### 测试覆盖策略

新增三类测试：

1. **codec 单元测试**：D1-D9 维度各一个测试 + empty rows / 非 Mapping row /
   None content / bool id 等边界 case
2. **路径等价测试**：
   ```python
   async def test_run_session_event_history_equivalent_to_app_port():
       """同一份 DB row，两条路径返回完全相等的 SessionEvent。"""
       rows = [...]
       via_run_history = await _RunSessionEventHistory(table).load_events(query)
       via_app_port = await AppSessionEventsPort(table).load_events(query)
       assert via_run_history == via_app_port  # Phase 3 完成后必然成立
   ```
3. **边界检查测试加强**：`test_phase4_static_boundaries.py` 增加：
   `matmaster/context/scanner.py` 不应出现 `Mapping[str, Any]` 类型的输入参数
   （即不接受 raw dict），且 `matmaster/context/*` 不应 import
   `src.services.session_event_codec`

### 风险全景

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| D2 改 `_skill_name_from_content` 改错导致 legacy skill_hit 丢失 | 中 | active skill rehydration 漏 skill | Phase 2 加 `skill_name` / `value` / `content` 三种 key 的回归测试 |
| Phase 3 protocol 变更遗漏某个测试 mock | 中 | CI 测试失败 | Phase 3 提交前全仓 grep `query_context_events.*-> list` |
| Phase 4 删除 scanner 函数后 `attachments.py` 遗留 `coerce_event_id` import | 中 | 确定性 ImportError | Phase 4 删除只被测试使用的 `scan_legacy_attachment_entries`，并加 grep 检查 |
| Phase 5 SessionEvent schema 变更影响 history_checkpoint 持久化格式 | 低-中 | 老 checkpoint 反序列化失败 | 单独验证 history_checkpoint_codec 不依赖 SessionEvent.created_at_ms |

### 建议落地方案

只在完成后进行一次 PR：

- **PR**: `refactor(context): unify SessionEvent decoding`
  （覆盖 Phase 1-5，或在开工前明确移出不做的 optional scope）

Phase 1-4 是必须的根因修复；Phase 5 是 schema 改进。若 Phase 5 纳入本轮
R7，则必须在同一个最终 PR 中完成；若决定延期，应先从本轮 scope 中移出并在
PR 描述里说明，不再为 R7 单独开后续 PR。详细任务、测试命令与代码片段见
独立计划：
[2026-05-17-r7-session-event-decoding.md](2026-05-17-r7-session-event-decoding.md)。

---

## E3: `_run_items` 每轮重复 `canonicalize_messages_for_provider` + `normalize_and_validate_openai_messages` 是 O(turns²)

### 现状

`matmaster/core/agent.py` 主循环每轮调用：

```python
api_messages = normalize_and_validate_openai_messages(
    canonicalize_messages_for_provider(state.messages)
)
```

`state.messages` 在循环里**只增不减**（每轮 append assistant + N 个 tool
messages；如果 compactor 触发则可能 truncate，但平稳期持续增长）。每次都遍历
整个 `state.messages`，所以：

- 第 N 轮验证 N 条消息；
- N 轮累计验证次数 ≈ Σi = N(N+1)/2 条消息。

对一个跑了 50 轮、每轮 3 条新消息的会话：

- 朴素总条数 = 50 × 3 = 150 条
- 实际累计验证 = 50 × (75 + 1.5) ≈ **3,825 条**
- 复杂度 = **O(turns² × avg_msg_size)**

`canonicalize_messages_for_provider` + 两个 validator
（`validate_openai_messages` + `validate_openai_tool_turn_sequence`，
位于 `matmaster/types/message_normalization.py:128–230`）都会从头扫一遍。

### 推迟原因

E3 是**算法重设计**，不是 hacky pattern cleanup。要正确解决需要：

1. **增量 canonicalize**：跟踪上一次 canonicalize 截止的 message 索引，新一轮
   只合并尾部新增的消息到 canonical 列表。需要考虑：
   - 工具回合的合并语义（多个 `ToolMessage` 是否会落到同一个 canonical
     assistant turn 里）
   - compaction 改写历史后，整段 canonical 缓存如何失效
2. **增量 validation**：`validate_openai_tool_turn_sequence` 跟踪
   `pending_tool_ids` / `seen_tool_ids` 跨调用的状态。当前 validator 是
   纯函数，状态都在栈里，要改成会维护跨调用 state 的 class，或者改成
   "只校验 tail，依赖之前已校验过的 prefix 不变"的契约。

任何方案都需要：

- 写新的 invariant 测试（"prefix 已校验，append 之后无 panic"）
- 仔细处理 compaction 触发后的缓存失效
- 性能基准（确保改动确实带来收益，而不是隐藏新的 O(n) 工作）

这套改动比 `/simplify` 一次跑能消化的工作量大，且会显著动到
`matmaster/types/message_normalization.py` 这种平台基础设施。

### 建议落地方案

提一个独立 PR：`perf(kernel): incremental message canonicalization & validation`

需要的前置准备：

1. 加 benchmark：跑一个固定的多轮 fixture（30 轮、每轮含 tool calls），
   用 `pytest-benchmark` 或手写计时记录 baseline。
2. 决定增量缓存的归属——是放在 `_KernelState` 还是 `canonicalize_messages_for_provider`
   内部用 `functools.cache` + `id(messages)` + `len(messages)` 复合 key。
3. 决定 compaction 后的失效策略（最简单：compaction 后 `state.canonical_cache = None`）。

风险点：

- canonicalize 的"合并相邻同 role"逻辑，如果实现错误，新一轮 append 时会
  漏掉一次合并，导致 provider 收到错误格式。这是**正确性 bug**，必须有强测试覆盖。
- validate 是 defense-in-depth 校验层，弱化它就增加了上游/下游漏检风险，
  需要明确"哪些是一次性校验、哪些必须每轮跑"。

---

## E6: `state.usage_vendor_by_turn` 与 `state.messages` 无 compactor 时无界增长

### 现状

`matmaster/core/kernel_items.py` 的 `_KernelState`：

```python
@dataclass
class _KernelState:
    messages: list[Any]
    turn: int = 0
    total_usage: dict[str, int] = dc_field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = dc_field(default_factory=list)
    cached_tool_definitions: list[dict[str, Any]] | None = None
    last_catalog_version: int = -1
```

- `state.usage_vendor_by_turn` 每轮 append 一项，**永不截断**——即使
  `spec.compactor` 触发了 messages 的压缩，这个 list 不会同步收缩。
- `state.messages` 唯一的"截断方"是 `spec.compactor`；当
  `spec.compactor is None`（例如某些 devshell / 调试入口），messages 会跟着
  turn 单调增长，没有硬上限。
- `_TerminalItem` 在 `_terminal()` 里把 `state.usage_vendor_by_turn` 全量拷贝
  快照（`[dict(item) for item in state.usage_vendor_by_turn]`，
  `agent.py:215-217`），所以临时内存占用 = 2× list 大小。

### 推迟原因

E6 是**架构选择**而非显式 bug：

- 当前架构默认假设"任何生产链路都会配 compactor"，没有 compactor 是个 debug 配置；
- 真要加 hard cap，需要决策：cap 多少？cap 触发后是丢最旧的、合并、还是截断
  并打 warning？这些都是产品/可观测性决策，不该在 cleanup PR 里偷偷做。

### 建议落地方案

两个独立的小动作（每个都可以单独提）：

1. **可观测性**：在 `_KernelState` 给 `usage_vendor_by_turn` 加注释说明
   "无界增长，依赖 max_turns 限界"；在 `AgentRuntimeSpec` 校验时如果
   `compactor is None` 且 `max_turns > 50`（或类似阈值），打 warning。
2. **可选硬 cap**：给 `_KernelState` 加 `usage_vendor_window: int | None`，
   当达到上限时丢最旧的；配套写一个回归测试确保不丢 `total_usage` 累计正确性。

不建议在不和"是否要长会话"产品决策一起做之前贸然加 cap，否则会改变
`RunResultEvent.usage_vendor_by_turn` 的契约。

---

## E7: `ToolCatalog.register_overlay` 触发整目录定义重建

### 现状

`matmaster/tools/tool_catalog.py:43–50`：

```python
def register_overlay(self, tool: Tool, *, source: str = "mcp") -> None:
    self._registry.register(tool, source=source)
    self._compiled_tools[tool.name] = self._compiler.compile(...)
    self._version += 1
```

每次 MCP 工具激活（`exp.py` 里 lazy 加载 skill / MCP server 时会反复触发），
`_version` 都会自增。`matmaster/core/agent.py` 在每轮开头看 `version` 是否变化：

```python
if spec.tool_catalog.version != state.last_catalog_version:
    state.cached_tool_definitions = None
    state.last_catalog_version = spec.tool_catalog.version

if state.cached_tool_definitions is None:
    state.cached_tool_definitions = spec.tool_catalog.build_definitions(desc_ctx)
```

→ 任何一个新 overlay 注册，下一轮就会**重建整个 catalog 的 OpenAI 工具定义**
（`build_definitions` 遍历每个工具、逐个调用 `describe(ctx)` / `prompt(ctx)`）。

对工具数 K、新增工具数 ΔK：每次 overlay 都是 O(K) 而不是 O(ΔK)。

### 推迟原因

- 这**不是本次重构引入的回归**——`test` 分支的旧 `agent.py` 同样是
  整体重建（diff 里"version-aware caching"这块是新加的优化，但底层
  `build_definitions` 一直是全量构建）。
- 真要做增量重建，要在 `ToolCatalog` 内部缓存"每工具的 description 结果
  + ctx hash"，并在 `register_overlay` 时只 patch 新增项进 cache。
- 对小工具集（< 50）实际开销可能在 ms 量级，触发频率低（只在 skill 首次
  激活时），收益不大。

### 建议落地方案

- 短期**不动**。
- 如果未来发现 lazy MCP 链路下出现 turn 间延迟尖峰，profile 确认热点在
  `build_definitions` 后再做：给 `ToolCatalog` 加一个 `_definitions_cache:
  dict[str, dict]`，key 是 `(tool_name, ctx_hash)`；`register_overlay`
  时只 invalidate / patch 新增项；`build_definitions` 时从 cache 拉。

---

## E8: `Exp.build_runtime` 启动期同步 I/O

### 现状

`Exp.build_runtime()` 是 `async` 方法，但内部多处用同步 I/O：

- `exp.py:794–799`：MCP server 配置 JSON 加载 (`json.loads(Path.read_text())`)
- `exp.py:762–769`：`mcp_runtime_path.exists()` + `_load_raw`
- `exp.py:136–150`：`_disabled_skill_names_from_settings` 每个 root 读 `.settings.json`
- `playground.py:401, 512–514`：`open(log_file)` + `yaml.safe_load(config.yaml)`

每次 run 都会重新解析这些**幂等的、文件 mtime 决定结果的**配置。

### 推迟原因

- 这**也不是本次重构引入的**——重构只是把代码搬运到了 `playground.py` /
  `exp.py` 的当前位置，I/O 模式照搬。
- 阻塞事件循环的影响在生产路径上一般 < 10ms（热缓存）/ 几十 ms（冷缓存），
  不在每轮 hot path 上，只在每次启动一次。
- 真要修，要么用 `aiofiles`（改 async 调用风格，扩散性大），要么在
  `PlaygroundManager` 级别 memoize 解析结果（带 mtime 失效）。memoize 本身
  又会引入"配置改了为什么不生效"的开发体验问题。

### 建议落地方案

- 短期**不动**。
- 中期可以做的：在 `PlaygroundManager.__init__` 或 module-level 加一个
  `@functools.cache` 的 `_load_llm_config()` / `_load_mcp_config()`，
  expose 一个 `Exp.invalidate_caches()` 给 devshell / hot-reload 使用。
- 长期：等迁到 FastAPI lifespan / 后台 startup 任务时再统一处理。

---

## 维护这份清单

- 每条目都标注了**为什么不在 /simplify 里做**——后续读者不必再重新讨论一遍。
- 落地之后请把对应 section 整段删掉（不要留 ~~删除线~~ 痕迹，干净就是干净）。
- 如果产品方向变化（例如真的要支持无 compactor 的超长 session），把变化记到
  对应 section 顶部的"现状"里，免得后续 PR 改完又被推翻。
