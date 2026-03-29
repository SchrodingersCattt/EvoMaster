# Phase 24: emit_nowait Tech Debt Cleanup - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

将 matmaster/ 包内全部 hook 和 ContextCompactor 的 emit_nowait() 调用升级为 await bus.emit()，清理异步改造后遗留的过期注释和类型标注。不涉及 src/ 服务层（仍需 emit_nowait 的 sync 调用路径）。

</domain>

<decisions>
## Implementation Decisions

### D-01: emit_nowait 迁移范围
迁移 matmaster/ 内全部 12 处 emit_nowait() 为 await bus.emit()，而不仅限于 ROADMAP 中提到的 EventEmitterHook 7 处。原因：Phase 17 后 Kernel 已完全 async，所有 hook 和 ContextCompactor 代码均在 async 上下文中执行，使用 emit_nowait 已无必要。

具体文件和调用点：
- `matmaster/core/hooks.py` EventEmitterHook: 6 处 (pre_tool_call, post_tool_call, on_stream_chunk x2, on_segment_complete x2)
- `matmaster/hooks/assistant_state.py` AssistantStateHook: 1 处 (pre_llm_call)
- `matmaster/hooks/output_processor.py` OutputProcessorHook: 2 处 (post_tool_call x2)
- `matmaster/hooks/skill_hit.py` SkillHitHook: 1 处 (post_tool_call)
- `matmaster/core/context_compactor.py` ContextCompactor: 2 处 (compact_if_needed x2)

### D-02: MessageBus.emit_nowait 方法保留
保留 MessageBus.emit_nowait() 方法。src/services/agent_run_service.py 有 10 处 sync 调用需要它（service 层跑在独立线程，无法 await）。但应更新 bus.py 的 class docstring 以反映 emit() 现在是 matmaster/ 内部的主路径，emit_nowait 仅供 service 层。

### D-03: 过期注释清理范围
清理全部 4 个文件中的 "sync kernel context" 过期注释/docstring：
- `matmaster/core/hooks.py:185-186` — EventEmitterHook docstring
- `matmaster/hooks/assistant_state.py:29` — AssistantStateHook docstring
- `matmaster/hooks/output_processor.py:27` — OutputProcessorHook docstring
- `matmaster/hooks/skill_hit.py:28` — SkillHitHook docstring

### D-04: stop_event 类型标注修复
`src/services/agent_run_service.py:257` 的 `stop_event: Any` 改为 `stop_event: threading.Event`。

### Claude's Discretion
- bus.py docstring 的具体措辞
- 测试修改范围（如果 mock 使用了 emit_nowait 则需适配）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 16 设计决策（emit_nowait 的起源）
- `.planning/phases/16-messagebus-eventrouter/16-RESEARCH.md` -- emit_nowait 设计原因和用法说明
- `.planning/phases/16-messagebus-eventrouter/16-02-PLAN.md` -- emit_nowait 引入的具体 plan
- `.planning/phases/16-messagebus-eventrouter/16-VERIFICATION.md` -- 记录了 emit_nowait 作为 intentional deviation

### Milestone audit 中的 tech debt 识别
- `.planning/v2.0-MILESTONE-AUDIT.md` -- 识别了 "sync kernel context" stale comment

### 直接修改的文件
- `matmaster/core/hooks.py` -- EventEmitterHook (6 处 emit_nowait)
- `matmaster/core/bus.py` -- MessageBus emit_nowait 方法定义 + docstring
- `matmaster/core/context_compactor.py` -- ContextCompactor (2 处 emit_nowait)
- `matmaster/hooks/assistant_state.py` -- AssistantStateHook (1 处 emit_nowait + stale comment)
- `matmaster/hooks/output_processor.py` -- OutputProcessorHook (2 处 emit_nowait + stale comment)
- `matmaster/hooks/skill_hit.py` -- SkillHitHook (1 处 emit_nowait + stale comment)
- `src/services/agent_run_service.py` -- stop_event type annotation

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `MessageBus.emit()` (async) 已存在于 `matmaster/core/bus.py:38`，是直接的替换目标
- 所有 hook 方法已是 async def（Phase 15 完成），可直接使用 await

### Established Patterns
- Hook 内部通过 `self._bus` 属性访问 MessageBus 实例
- ContextCompactor 通过可选的 `self._bus` 属性访问（需 None 检查后 await）
- 迁移模式：`self._bus.emit_nowait(Event(...))` → `await self._bus.emit(Event(...))`
- ContextCompactor 的 `compact_if_needed` 已是 async def，可直接 await

### Integration Points
- service 层 emit_nowait 调用不动（out of scope）
- 测试中可能有 mock MessageBus 需要验证 emit vs emit_nowait 调用

</code_context>

<specifics>
## Specific Ideas

No specific requirements -- open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 24-emit-nowait-tech-debt*
*Context gathered: 2026-03-30*
