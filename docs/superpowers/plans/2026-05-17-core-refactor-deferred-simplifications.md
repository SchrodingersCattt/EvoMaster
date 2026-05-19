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
