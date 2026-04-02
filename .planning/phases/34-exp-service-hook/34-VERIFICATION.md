---
phase: 34-exp-service-hook
verified: 2026-04-02T16:30:00Z
status: passed
score: 15/15 must-haves verified
re_verification: true
  previous_status: gaps_found
  previous_score: 11/15
  gaps_closed:
    - "FullToolRunner 激活为默认执行路径"
    - "Generator 事件流贯穿 Kernel -> Exp -> Service 全链路"
    - "ToolCatalog version bump from register_overlay triggers Kernel tool_definitions refresh"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Run run_agent_stream() end-to-end with a real LLM and tool call"
    expected: "Agent processes tool call, returns (True, elapsed_ms), SSE stream shows tool_call and tool_result events"
    why_human: "Requires live LLM + SSE connection. Programmatic tests verify service structure but not the full LLM path."
---

# Phase 34: Exp/Service/Hook Verification Report

**Phase Goal:** FullToolRunner 激活为默认执行路径，Generator 事件流贯穿 Kernel -> Exp -> Service 全链路，4 个 Hook 全部退役，Hook->Bus 间接事件路径移除
**Verified:** 2026-04-02T16:30:00Z
**Status:** passed
**Re-verification:** Yes — after gap closure Plan 34-04

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | _stream_llm_items() yields ThoughtEvent/ResponseEvent per chunk with segment-complete semantics | ✓ VERIFIED | agent.py L1078+: full streaming logic. 10 stream tests pass. (No regression from 34-01.) |
| 2 | Exp.build_runtime() injects FullToolRunner + ToolCatalog + RuntimeTopology into AgentRuntimeSpec | ✓ VERIFIED | exp.py L259: FullToolRunner constructed. ToolCatalog at L194. RuntimeTopology at L186-191. All injected via model_copy. |
| 3 | FullToolRunner is the default execution path | ✓ VERIFIED | agent.py L585-626: `if spec.tool_runner is not None:` guard routes to `spec.tool_runner.execute_batch(all_tcs, exec_ctx)`. FullToolRunner receives all tool_calls, bypassing legacy guard/hook gating. test_run_items_uses_tool_runner_when_present passes. |
| 4 | run_stream() yields BusEvent (not _KernelItem), with RunResultEvent as the final yield | ✓ VERIFIED | agent.py L368-415: `_consume_and_yield()` inner generator extracts `item.event`, skips None-event items, converts terminal _KernelItem to `RunResultEvent`. Return type: `AsyncIterator[Any]`. test_run_stream_yields_bus_event_not_kernel_item + test_run_stream_with_tool_calls_yields_bus_events pass. |
| 5 | AgentRunService.run_agent_stream() consumes Exp.run_stream() generator events and bridges to bus | ✓ VERIFIED | agent_run_service.py L676: `isinstance(event, RunResultEvent)` now matches correctly — real kernel yields RunResultEvent as terminal event. No longer reliant on _FakeExp mock contract. |
| 6 | Source normalization: generator events normalized to MatMaster/MatMaster:<exp> before bus | ✓ VERIFIED | agent_run_service.py L668-671: hasattr(event, 'source') guard + _normalize_public_source call. (No regression from 34-02.) |
| 7 | ToolResult.payload maps to SSE info field (event_payloads.py contract) | ✓ VERIFIED | event_payloads.py L115: dual-key mapping. (No regression from 34-02.) |
| 8 | run_agent_stream() emits StreamClosedEvent after terminal event | ✓ VERIFIED | agent_run_service.py L697-703. (No regression from 34-02.) |
| 9 | Existing run_agent() behavior is completely unchanged (REGR-02) | ✓ VERIFIED | run_agent() untouched. 39 kernel regression tests pass. |
| 10 | on_skill_hit calls catalog.register_overlay() instead of registry.register() | ✓ VERIFIED | exp.py L607-609. (No regression from 34-01.) |
| 11 | ContextCompactor accepts event_sink callback instead of bus reference | ✓ VERIFIED | context_compactor.py L139. (No regression from 34-01.) |
| 12 | _run_items() yields AssistantStateEvent on tool_calls turns | ✓ VERIFIED | agent.py L565-572. (No regression from 34-01.) |
| 13 | _run_items() yields SkillHitEvent on use_skill calls (FullToolRunner path) | ✓ VERIFIED | agent.py L617-626: SkillHitEvent yield inside the FullToolRunner result loop. (No regression from 34-01.) |
| 14 | EventEmitterHook deleted; Exp.build_runtime() no longer creates it | ✓ VERIFIED | hooks.py: no EventEmitterHook class. matmaster/hooks/ has only confirmation.py. (No regression from 34-03.) |
| 15 | AssistantStateHook, SkillHitHook, OutputProcessorHook deleted; _build_service_hooks() ConfirmationHook only | ✓ VERIFIED | matmaster/hooks/ contains only __init__.py and confirmation.py. (No regression from 34-03.) |
| 16 | ToolCatalog version bump triggers _run_items() tool_definitions refresh | ✓ VERIFIED | agent.py L482-502: version comparison at L485, cache invalidation at L487, `state.last_catalog_version` update at L488, rebuild via `spec.tool_catalog.build_definitions()` at L495. test_catalog_version_invalidates_tool_definitions + test_catalog_version_no_refresh_when_unchanged pass. |

**Score:** 15/15 truths verified (was 11/15 before Plan 34-04)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/core/agent.py` | FullToolRunner path in _run_items(), BusEvent yield in run_stream(), catalog version check | ✓ VERIFIED | All 3 gap closures present. L585-626 (tool_runner path), L391-415 (BusEvent yield), L482-502 (version check). |
| `matmaster/core/exp.py` | run_stream() passes through kernel.run_stream() with cleanup | ✓ VERIFIED | L392-397: `async for event in runtime.kernel.run_stream(...)`, try/finally cleanup at L396-397. Now yields BusEvent end-to-end. |
| `matmaster/core/context_compactor.py` | event_sink parameter instead of bus | ✓ VERIFIED | L139: event_sink parameter. No MessageBus import. |
| `src/services/agent_run_service.py` | run_agent_stream() method with RunResultEvent detection | ✓ VERIFIED | L676: `isinstance(event, RunResultEvent)` now correctly matches real kernel output. |
| `matmaster/core/hooks.py` | BaseHook + dispatch functions (no EventEmitterHook) | ✓ VERIFIED | EventEmitterHook absent. |
| `matmaster/hooks/__init__.py` | Only ConfirmationHook export | ✓ VERIFIED | Only confirmation.py in matmaster/hooks/. |
| `matmaster/integration/event_payloads.py` | ToolResult.payload -> info mapping | ✓ VERIFIED | Dual-key mapping. |
| `tests/matmaster/core/test_agent_kernel_stream.py` | Gap closure tests (3 new) | ✓ VERIFIED | TestGap1FullToolRunnerActivation, TestGap2RunStreamYieldsBusEvent, TestGap3CatalogVersionInvalidation — all substantive, all pass. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `agent.py::_run_items()` | `tool_runner.py::FullToolRunner.execute_batch()` | `spec.tool_runner.execute_batch(all_tcs, exec_ctx)` | ✓ WIRED | agent.py L597-599. FullToolRunner now invoked for ALL tool_calls when spec.tool_runner is not None. |
| `agent.py::run_stream()` | `types/events.py::RunResultEvent` | `yield RunResultEvent(...)` from terminal _KernelItem | ✓ WIRED | agent.py L399-404: `yield RunResultEvent(source="agent", status=..., reason=..., final_content=...)` |
| `agent.py::_run_items()` | `tools/tool_catalog.py::ToolCatalog.version` | `spec.tool_catalog.version != state.last_catalog_version` | ✓ WIRED | agent.py L485: version comparison; L495: `spec.tool_catalog.build_definitions()` on cache miss. |
| `agent.py::run_stream()` | `agent.py::_run_items()` | `async for item in self._run_items(...)` inside `_consume_and_yield()` | ✓ WIRED | agent.py L392. |
| `exp.py::run_stream()` | `agent.py::run_stream()` | `async for event in runtime.kernel.run_stream(...)` | ✓ WIRED | exp.py L392-395. Now correctly yields BusEvent end-to-end. |
| `agent_run_service.py::run_agent_stream()` | `exp.py::run_stream()` | `async with aclosing(exp.run_stream(...))` | ✓ WIRED | agent_run_service.py L657. |
| `agent_run_service.py::run_agent_stream()` | `bus.py::MessageBus` | `bus.emit_nowait(event)` | ✓ WIRED | agent_run_service.py L673. |
| `exp.py::_init_skill_tools::on_skill_hit` | `tool_catalog.py::register_overlay()` | `catalog.register_overlay(lazy_tool, source='mcp')` | ✓ WIRED | exp.py L608. Version bump now consumed by _run_items(). |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `agent.py::_stream_llm_items()` | ThoughtEvent/ResponseEvent | spec.llm_provider.chat_stream() | Yes — live chunks from LLM stream | ✓ FLOWING |
| `agent.py::_run_items()` | _KernelItem events | _stream_llm_items() + FullToolRunner.execute_batch() | Yes — events from real execution | ✓ FLOWING |
| `agent.py::run_stream()` | BusEvent (item.event) | _consume_and_yield() consuming _run_items() | Yes — yields item.event (BusEvent), terminal _KernelItem becomes RunResultEvent | ✓ FLOWING |
| `exp.py::run_stream()` | BusEvent | kernel.run_stream() | Yes — now correctly typed; service isinstance(event, RunResultEvent) check succeeds | ✓ FLOWING |
| `agent_run_service.py::run_agent_stream()` | run_result_event | exp.run_stream() events | Yes — RunResultEvent detected at L676, run_agent_stream() returns (True, elapsed_ms) | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase 34 core test suite (kernel + stream + service + exp) | `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_agent_kernel_extended.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/core/test_exp_runtime_v2.py -x -q` | 72 passed in 0.50s | ✓ PASS |
| FullToolRunner execute_batch invoked | `grep -n 'spec.tool_runner.execute_batch' matmaster/core/agent.py` | L597: `runner_results = await spec.tool_runner.execute_batch(` | ✓ PASS |
| run_stream() BusEvent yield | `grep -n 'yield item.event\|yield RunResultEvent' matmaster/core/agent.py` | L399: `yield RunResultEvent(...)`, L407: `yield item.event` | ✓ PASS |
| Catalog version check | `grep -n 'spec.tool_catalog.version' matmaster/core/agent.py` | L485: `spec.tool_catalog.version != state.last_catalog_version` | ✓ PASS |
| FullToolRunner path bypasses legacy guard/hook | `grep -n 'if spec.tool_runner is not None' matmaster/core/agent.py` | L585: `if spec.tool_runner is not None:` | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| KGEN-06 | 34-01 | _run_items() yields ToolCallEvent/ToolResultEvent | ✓ SATISFIED | agent.py L574-583 (ToolCallEvent), L607-616 (ToolResultEvent in FullToolRunner path) |
| ESIN-01 | 34-01, 34-04 | Exp.run_stream() transparently yields kernel generator as BusEvent | ✓ SATISFIED | exp.py L392-395 passes through BusEvent from kernel.run_stream() which now yields BusEvent |
| ESIN-02 | 34-02, 34-04 | AgentRunService.run_agent_stream() consumes generator events | ✓ SATISFIED | agent_run_service.py L676: isinstance(event, RunResultEvent) correctly matches real kernel output |
| ESIN-03 | 34-01 | _do_stream_llm() refactored to _stream_llm_items() sub-generator | ✓ SATISFIED | _stream_llm_items() at L1078+. 10 stream tests pass. |
| ESIN-04 | 34-01, 34-04 | FullToolRunner is default execution path | ✓ SATISFIED | agent.py L585-626: FullToolRunner path active when spec.tool_runner is not None. Bypasses legacy guard/hook gating. |
| ESIN-05 | 34-01, 34-04 | on_skill_hit uses catalog.register_overlay(); catalog.version triggers tool_definitions refresh | ✓ SATISFIED | exp.py L607-609 (register_overlay call), agent.py L482-502 (version-aware cache invalidation) |
| ESIN-06 | 34-02 | Generator event source normalized to MatMaster/MatMaster:<exp> | ✓ SATISFIED | agent_run_service.py L668-671. |
| ESIN-07 | 34-02 | ToolResult.payload -> SSE info mapping | ✓ SATISFIED | event_payloads.py L115. |
| HRET-01 | 34-01 | _run_items() yields ThoughtEvent/ResponseEvent/ToolCallEvent/ToolResultEvent | ✓ SATISFIED | All 4 event types yielded. |
| HRET-02 | 34-01 | _run_items() yields AssistantStateEvent on tool_calls turn | ✓ SATISFIED | agent.py L565-572. |
| HRET-03 | 34-01 | _run_items() yields SkillHitEvent on use_skill | ✓ SATISFIED | agent.py L617-626 (FullToolRunner path). |
| HRET-04 | 34-03 | OutputProcessorHook retired | ✓ SATISFIED | matmaster/hooks/output_processor.py deleted. |
| HRET-05 | 34-01 | ContextCompactor bus dependency replaced by event_sink | ✓ SATISFIED | context_compactor.py: event_sink parameter, no MessageBus import. |
| HRET-06 | 34-03 | Hook->Bus indirect path removed | ✓ SATISFIED | EventEmitterHook deleted from hooks.py, matmaster/hooks/ has only confirmation.py. |
| REGR-02 | 34-02 | Exp.run() and AgentRunService.run_agent() unchanged | ✓ SATISFIED | run_agent() untouched. 39 kernel regression tests pass. |

**Orphaned requirements check:** All phase 34 requirement IDs (KGEN-06, ESIN-01 through ESIN-07, HRET-01 through HRET-06, REGR-02) are accounted for across plans 34-01 through 34-04. ESIN-08 correctly deferred per D-03. No orphaned requirements.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None | — | — | — | All 3 previously-flagged blockers closed by Plan 34-04. |

No new anti-patterns detected in the modified files.

### Human Verification Required

#### 1. Full Generator Event Chain E2E

**Test:** Configure a real Exp, call run_agent_stream() with a user_prompt that requires a tool call. Verify SSE events reach the browser/curl client with correct source labels and that run_result_event is detected.
**Expected:** SSE stream shows thought/response/tool_call/tool_result/run_result/stream_closed events. run_agent_stream() returns (True, elapsed_ms).
**Why human:** Requires live LLM + SSE + EventRouter stack. Programmatic tests use _FakeExp or mock providers which bypass the real LLM path. The generator chain is now structurally correct end-to-end; the remaining uncertainty is behavioral under live conditions.

### Re-verification Summary

**Three gaps from the initial verification are now closed:**

**Gap 1 (FullToolRunner not activated) — CLOSED.** `_run_items()` now checks `if spec.tool_runner is not None:` at L585. When true, it calls `spec.tool_runner.execute_batch(all_tcs, exec_ctx)` with a `ToolExecutionContext`, bypassing the entire legacy guard+pre_hook+execute+post_hook block (because FullToolRunner's seven-step chain handles all of those concerns internally). The fallback to `spec.tool_registry.execute()` is preserved for the `else:` branch.

**Gap 2 (run_stream() yields _KernelItem not BusEvent) — CLOSED.** `run_stream()` now uses a `_consume_and_yield()` inner async generator that extracts `item.event` from each `_KernelItem`, skips items where `item.event is None` (llm_response, messages_delta), and converts terminal `_KernelItem` into `RunResultEvent(source="agent", status=..., reason=..., final_content=...)` as the final yield. The return type annotation is `AsyncIterator[Any]`. The service layer's `isinstance(event, RunResultEvent)` check at agent_run_service.py:676 now correctly matches real kernel output — `run_agent_stream()` returns `(True, elapsed_ms)` in production rather than `(False, 'no_result')`.

**Gap 3 (ToolCatalog version bump not consumed) — CLOSED.** `_run_items()` now compares `spec.tool_catalog.version` against `state.last_catalog_version` at the start of each turn (L482-488). When the version has changed (because `register_overlay()` was called on a skill hit), `state.cached_tool_definitions` is set to `None` and `state.last_catalog_version` is updated. The cache miss at L490 triggers a rebuild from `spec.tool_catalog.build_definitions()`. The initial sentinel `last_catalog_version = -1` ensures the first-turn build always fires.

All 15 must-have truths for phase 34 are now verified. The phase goal is achieved.

---

_Verified: 2026-04-02T16:30:00Z_
_Verifier: Claude (gsd-verifier)_
