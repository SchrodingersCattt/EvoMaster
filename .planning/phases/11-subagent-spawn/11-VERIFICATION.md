---
phase: 11-subagent-spawn
verified: 2026-03-25T08:09:23Z
status: passed
score: 5/5 must-haves verified
re_verification: false
gaps: []
---

# Phase 11: SubAgent Spawn Verification Report

**Phase Goal:** Agent 可以通过 tool_call 触发子 agent 执行特定任务，子 agent 有独立配置但共享父环境，支持取消传播和事件路由
**Verified:** 2026-03-25T08:09:23Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth                                                                                           | Status     | Evidence                                                                               |
|-----|-------------------------------------------------------------------------------------------------|------------|----------------------------------------------------------------------------------------|
| 1   | Agent 可以通过 tool_call 触发子 agent 执行特定任务                                              | VERIFIED   | SubAgentTool registered in direct.toml; Exp.build_runtime wires spawn_fn closure       |
| 2   | 子 agent 拥有独立 tool 集和 system prompt，同时共享父 workspace/session                         | VERIFIED   | explore.toml 独立配置；spawn_fn 传递父 ctx (SUBA-03 test passes)                       |
| 3   | 子 agent 禁止再次 spawn（递归深度 = 1）                                                         | VERIFIED   | 双层防护：explore.toml 无 sub_agent；spawn_fn=None 返回 error string                  |
| 4   | 父 agent 取消时 stop_event 级联传播到子 agent                                                   | VERIFIED   | Exp.run() + agent_run_service.py 两处注入；子 kernel.run(stop_event=...) 传递已验证    |
| 5   | 子 agent 流式事件通过父 agent MessageBus 路由，前端可实时观察                                   | VERIFIED   | source_override="MatMaster:explore"；normalize 函数保留前缀；chat_history 兼容 prefix  |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact                                                          | Expected                                              | Status   | Details                                                               |
|-------------------------------------------------------------------|-------------------------------------------------------|----------|-----------------------------------------------------------------------|
| `matmaster/tools/builtin/sub_agent_tool.py`                       | SubAgentTool with spawn_fn closure injection          | VERIFIED | 93 lines; SubAgentTool(BuiltinTool); _stop_event; 3-arg spawn_fn call |
| `matmaster/exps/explore.toml`                                     | Exploration sub-agent exp config                      | VERIFIED | name="explore"; no sub_agent; PRMT-03 developer_instructions; 51 lines |
| `matmaster/tools/builtin/__init__.py`                             | SubAgentTool exported                                 | VERIFIED | Line 14: import; line 33: __all__ entry                               |
| `matmaster/core/exp.py`                                           | _make_spawn_fn + source_override + stop_event inject  | VERIFIED | _make_spawn_fn static method; source_override param; run() injection  |
| `matmaster/exps/direct.toml`                                      | sub_agent in tools.builtin                            | VERIFIED | Line 56: "sub_agent" in builtin list                                  |
| `src/utils/chat_event_source.py`                                  | MatMaster: prefix preserved                           | VERIFIED | startswith('MatMaster:') guard before fallback                        |
| `matmaster/integration/event_payloads.py`                         | MatMaster: prefix preserved                           | VERIFIED | startswith("MatMaster:") guard before fallback                        |
| `src/services/chat_history.py`                                    | _is_matmaster_source helper; 4 locations replaced     | VERIFIED | helper at line 18; all 4 judgment points use _is_matmaster_source     |
| `src/services/agent_run_service.py`                               | SubAgentTool stop_event injection                     | VERIFIED | Lines 335-340: SubAgentTool injection block after spec creation       |
| `tests/matmaster/tools/test_sub_agent_tool.py`                    | Unit tests >= 80 lines                                | VERIFIED | 164 lines; 13 tests; all pass                                         |
| `tests/matmaster/integration/test_subagent_spawn.py`              | Integration tests >= 60 lines                         | VERIFIED | 244 lines; 9 tests; all pass                                          |
| `tests/matmaster/integration/test_subagent_event_routing.py`      | Event routing tests >= 50 lines                       | VERIFIED | 100 lines; 10 tests; all pass                                         |

### Key Link Verification

| From                                     | To                                        | Via                                     | Status  | Details                                                        |
|------------------------------------------|-------------------------------------------|-----------------------------------------|---------|----------------------------------------------------------------|
| `sub_agent_tool.py`                      | `builtin/base.py`                         | `class SubAgentTool(BuiltinTool)`       | WIRED   | Line 20; inherits BuiltinTool ABC                              |
| `builtin/__init__.py`                    | `sub_agent_tool.py`                       | import + __all__                        | WIRED   | Line 14 import; line 33 __all__                               |
| `exp.py`                                 | `sub_agent_tool.py`                       | import SubAgentTool + spawn_fn          | WIRED   | Lazy import in build_runtime (4b) and run()                   |
| `exp.py`                                 | `config/loader.py`                        | load_exp_config in spawn_fn closure     | WIRED   | Line 103 inside spawn_fn                                      |
| `exp.py`                                 | `core/hooks.py`                           | EventEmitterHook with source_override   | WIRED   | Lines 175-177; emitter_source = source_override or self.exp_name |
| `chat_event_source.py`                   | `chat_history.py`                         | normalize_event_source imported/used    | WIRED   | chat_history.py line 15 imports; called at lines 273-349      |
| `event_payloads.py`                      | `core/hooks.py`                           | _normalize_public_source via SSE handlers| WIRED  | Used by PersistenceHandler and SSEHandler (existing wiring)   |
| `agent_run_service.py`                   | `sub_agent_tool.py`                       | SubAgentTool stop_event injection       | WIRED   | Lines 335-340; lazy import + isinstance check                 |

### Data-Flow Trace (Level 4)

| Artifact                         | Data Variable   | Source                      | Produces Real Data | Status    |
|----------------------------------|-----------------|-----------------------------|--------------------|-----------|
| `sub_agent_tool.py` _execute     | spawn_fn result | Exp._make_spawn_fn closure  | Yes (child kernel.run) | FLOWING |
| `exp.py` _make_spawn_fn          | child result    | child_runtime.kernel.run()  | Yes (AgentKernel)  | FLOWING   |
| `chat_event_source.py`           | source string   | EventEmitterHook source=    | Yes (bus events)   | FLOWING   |
| `chat_history.py`                | source field    | normalize_event_source      | Yes (DB events)    | FLOWING   |

### Behavioral Spot-Checks

| Behavior                                    | Command                                              | Result                                                      | Status  |
|---------------------------------------------|------------------------------------------------------|-------------------------------------------------------------|---------|
| SubAgentTool.name == "sub_agent"            | python import + print                               | "sub_agent"                                                 | PASS    |
| Recursion guard returns error               | SubAgentTool().execute(...)                         | "Error: SubAgent spawning is not available..."              | PASS    |
| explore.toml no sub_agent                   | load_exp_config('explore')                          | 'sub_agent' not in tools.builtin: True                      | PASS    |
| direct.toml has sub_agent                   | load_exp_config('direct')                           | 'sub_agent' in tools.builtin: True                          | PASS    |
| normalize_event_source preserves prefix     | normalize_event_source('MatMaster:explore')         | "MatMaster:explore"                                         | PASS    |
| _normalize_public_source preserves prefix   | _normalize_public_source('MatMaster:explore')       | "MatMaster:explore"                                         | PASS    |
| All 32 phase 11 tests pass                  | uv run pytest (3 test files)                        | 32 passed, 0 failed                                         | PASS    |

### Requirements Coverage

| Requirement | Source Plan | Description                                                                | Status    | Evidence                                                                         |
|-------------|-------------|----------------------------------------------------------------------------|-----------|----------------------------------------------------------------------------------|
| SUBA-01     | 11-01, 11-02| Agent 通过 SubAgent tool spawn 子 agent，结果作为 tool result 返回         | SATISFIED | SubAgentTool.execute -> spawn_fn -> child kernel.run -> returns final_content    |
| SUBA-02     | 11-01       | 子 agent 通过 ExpConfig 独立配置 tool 集和 system prompt                   | SATISFIED | explore.toml: independent builtin list, developer_instructions, mode_contract    |
| SUBA-03     | 11-02       | 子 agent 共享父 PlaygroundContext（workspace/session）                     | SATISFIED | spawn_fn closure captures ctx; test_spawn_fn_shared_context passes              |
| SUBA-04     | 11-01       | 子 agent 禁止再次 spawn（递归深度保护）                                    | SATISFIED | Dual guard: explore.toml no sub_agent + spawn_fn=None error return              |
| SUBA-05     | 11-02, 11-03| 父 agent 取消时 stop_event 级联传播到子 agent                              | SATISFIED | Exp.run() + agent_run_service.py both inject; test_stop_event_propagation passes |
| SUBA-06     | 11-03       | 子 agent 事件通过父 MessageBus 路由到前端                                  | SATISFIED | source_override + normalize prefix preservation + EventEmitterHook test passes   |
| PRMT-03     | 11-01       | SubAgent exp 定义包含针对子任务场景的专用 system prompt                    | SATISFIED | explore.toml developer_instructions: read-only exploration prompt, 28 lines      |

All 7 requirements satisfied. No orphaned requirements found for Phase 11.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `exp.py` `_init_mcp_tools` | 448-455 | `pass` stub body | Info | MCP stub pre-exists from Phase 09; not introduced in Phase 11; no impact on sub-agent goals |

No blockers or warnings from Phase 11 changes. The MCP stub is a known pre-existing artifact from an earlier phase.

### Human Verification Required

None. All success criteria are verifiable programmatically. The sub-agent spawn mechanism is tested end-to-end at the unit and integration levels. Real LLM execution is not required to verify the wiring, protocol compliance, or event routing.

### Gaps Summary

No gaps. All 5 observable truths are verified, all 12 artifacts are substantive and wired, all 7 requirements are satisfied, and 32 tests pass with 0 regressions.

---

_Verified: 2026-03-25T08:09:23Z_
_Verifier: Claude (gsd-verifier)_
