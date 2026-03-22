---
milestone: v1
audited: 2026-03-22T23:30:00Z
status: tech_debt
scores:
  requirements: 29/29
  phases: 7/7
  integration: 28/31
  flows: 4/6
gaps:
  requirements: []
  integration:
    - id: "ASBL-02"
      status: "partial"
      phase: "Phase 3 + Phase 6"
      claimed_by_plans: ["03-01-PLAN.md", "06-02-PLAN.md"]
      completed_by_plans: ["03-01-SUMMARY.md", "06-02-SUMMARY.md"]
      verification_status: "passed"
      evidence: "ToolRegistry 支持 builtin/mcp/skill 三种 source 注册，但 service layer 构造 DirectExp 时未提供 mcp_manager_factory/skill_registry_factory 参数，MCP/Skill 工具路径在生产中未接线"
    - id: "ASBL-06"
      status: "partial"
      phase: "Phase 3 + Phase 6"
      claimed_by_plans: ["03-02-PLAN.md", "06-02-PLAN.md"]
      completed_by_plans: ["03-02-SUMMARY.md", "06-02-SUMMARY.md"]
      verification_status: "passed"
      evidence: "WorkerRegistry Protocol 已定义，WorkerRegistryServiceAdapter 已实现，但 adapter 在 agent_run_service.py 中未引用，Protocol 未注入 matmaster 管道"
    - id: "MIGR-01 / MIGR-02"
      status: "partial"
      phase: "Phase 5 + Phase 6"
      claimed_by_plans: ["05-01 to 05-03-PLAN.md", "06-01/06-02-PLAN.md"]
      completed_by_plans: ["05-01 to 05-03-SUMMARY.md", "06-01/06-02-SUMMARY.md"]
      verification_status: "passed"
      evidence: "matmaster pipeline E2E 测试通过（mock LLM），但 run_agent_sync() 返回 None 而 agent_worker.py 期望 tuple，生产运行时返回值解析失效"
  flows:
    - flow: "MCP/Skill 工具注册流程"
      breaks_at: "DirectExp.__init__ mcp_manager_factory=None"
      severity: "tech_debt"
      note: "ToolRegistry 注册路径完整，service layer 未提供 EvoMaster factory callables；src/ 层重构在 out of scope"
    - flow: "agent_worker.py 返回值解析"
      breaks_at: "run_agent_sync() returns None, agent_worker expects tuple"
      severity: "tech_debt"
      note: "matmaster pipeline 内部正确完成，返回值不匹配属 src/ Web Service 层迁移范围（out of scope）"
tech_debt:
  - phase: 06-service-layer-wiring
    items:
      - "agent_run_service.py 构造 DirectExp 时缺少 mcp_manager_factory/skill_registry_factory 参数"
      - "WorkerRegistryServiceAdapter 已创建但未在 agent_run_service.py 中注入"
      - "run_agent_sync() 返回 None，需更新为 tuple 或更新 agent_worker.py 调用方"
  - phase: 03-exp-assembly-layer
    items:
      - "ManuscriptGateGuard/AuthFailureGateGuard shell 类已删除（Phase 6），业务 Guard 注入点保留但 service layer 未传入任何业务 guard"
  - phase: 05-integration-quality
    items:
      - "FinishEvent 未通过 bus.emit() 发送到 MessageBus，前端 SSE 流中无 finish 类型的 matmaster 事件通知"
nyquist:
  compliant_phases: [5]
  partial_phases: [1, 2, 3, 4, 6]
  missing_phases: [7]
  overall: PARTIAL
---

# Milestone v1 Audit Report — MatMaster Framework Refactoring (v2)

**Audited:** 2026-03-22T23:30:00Z
**Status:** tech_debt (all requirements met at framework level, no critical blockers, service layer integration debt needs review)
**Previous audit:** 2026-03-22T10:00:00Z (Phases 1-5 only, status: tech_debt)

---

## 1. Milestone Scope

**Definition of Done (from ROADMAP.md):**
将 matmaster 的 playground/exp/agent 三层架构从继承驱动改为契约驱动。从类型化契约出发，构建纯执行 kernel，然后分别重构 exp 装配层和 playground 环境层，最终在新骨架上完成 mat_master 和 minimal 的端到端迁移与质量验证。

**Phases:** 7 (all complete — includes Phase 6/7 gap closure from first audit)
**Requirements:** 29 v1 (all mapped to phases, all satisfied)

---

## 2. Phase Verification Summary

| Phase | Name | Score | Status | Verified |
|-------|------|-------|--------|----------|
| 01 | Foundation Contracts | 13/13 | PASSED | 2026-03-21 |
| 02 | Agent Kernel | 22/22 | PASSED (re-verified) | 2026-03-22 |
| 03 | Exp Assembly Layer | 11/11 | PASSED (re-verified) | 2026-03-22 |
| 04 | Playground Layer | 13/13 | PASSED | 2026-03-22 |
| 05 | Integration and Quality | 8/8 | PASSED | 2026-03-22 |
| 06 | Service Layer Wiring | 9/9 | PASSED | 2026-03-22 |
| 07 | Cleanup and Traceability | 13/13 | PASSED | 2026-03-22 |

**Total:** 89/89 must-have truths verified across all 7 phases.

**Re-verifications:**
- Phase 2: LLMP-01 gap closed (chat_with_retry added to Protocol + OpenAIProvider)
- Phase 3: Circular import fixed (lazy import + __getattr__ pattern)

**Gap closure phases (from first audit):**
- Phase 6: Closed _build_llm_provider stub, _get_builtin_tools stub, guard shell cleanup, WorkerRegistry adapter
- Phase 7: QueueBridge removed, directory restructured to core/tools/types/, EBUS-02 traceability fixed

---

## 3. Requirements Coverage (3-Source Cross-Reference)

### Source availability
- **VERIFICATION.md:** All 7 phases have VERIFICATION.md with per-requirement status tables
- **SUMMARY.md frontmatter:** `requirements-completed` field not present; `provides` field used as proxy
- **REQUIREMENTS.md:** Checkbox status + Traceability table (all 29 [x] Complete)

### Full cross-reference

| REQ-ID | Description | VERIFICATION | REQUIREMENTS [x] | Traceability | Final Status |
|--------|-------------|--------------|-------------------|--------------|-------------|
| CONT-01 | PlaygroundContext frozen model | SATISFIED | [x] | Complete | **satisfied** |
| CONT-02 | AgentRuntimeSpec frozen model | SATISFIED | [x] | Complete | **satisfied** |
| CONT-03 | AgentEvent discriminated union | SATISFIED | [x] | Complete | **satisfied** |
| CONT-04 | Guard Protocol interface | SATISFIED | [x] | Complete | **satisfied** |
| CONT-05 | TerminationPolicy type | SATISFIED | [x] | Complete | **satisfied** |
| EBUS-01 | MessageBus sync queue | SATISFIED | [x] | Complete | **satisfied** |
| EBUS-02 | QueueBridge → SSEHandler replacement | SATISFIED | [x] | Complete | **satisfied** |
| KERN-01 | AgentKernel execution loop | SATISFIED | [x] | Complete | **satisfied** |
| KERN-02 | Built-in guards (loop/max_turns) | SATISFIED | [x] | Complete | **satisfied** |
| KERN-03 | GuardPipeline chaining | SATISFIED | [x] | Complete | **satisfied** |
| KERN-04 | Hook Point API | SATISFIED | [x] | Complete | **satisfied** |
| LLMP-01 | LLMProvider Protocol (3 methods) | SATISFIED | [x] | Complete | **satisfied** |
| ASBL-01 | Exp base class assemble() | SATISFIED | [x] | Complete | **satisfied** |
| ASBL-02 | ToolRegistry unified registration | SATISFIED | [x] | Complete | **satisfied** |
| ASBL-03 | Business Guard injection | SATISFIED | [x] | Complete | **satisfied** |
| ASBL-04 | Solver as Exp subclass | SATISFIED | [x] | Complete | **satisfied** |
| ASBL-05 | ContextBuilder multi-source | SATISFIED | [x] | Complete | **satisfied** |
| ASBL-06 | WorkerRegistry Protocol definition | SATISFIED | [x] | Complete | **satisfied** |
| WKSP-01 | Unified Playground prepare/cleanup | SATISFIED | [x] | Complete | **satisfied** |
| WKSP-02 | mat_master config path | SATISFIED | [x] | Complete | **satisfied** |
| WKSP-03 | minimal config path | SATISFIED | [x] | Complete | **satisfied** |
| WKSP-04 | WorkspaceArchivalConfig | SATISFIED | [x] | Complete | **satisfied** |
| MIGR-01 | mat_master E2E migration | SATISFIED | [x] | Complete | **satisfied** |
| MIGR-02 | minimal E2E migration | SATISFIED | [x] | Complete | **satisfied** |
| QUAL-01 | Contract unit tests | SATISFIED | [x] | Complete | **satisfied** |
| QUAL-02 | E2E migration tests | SATISFIED | [x] | Complete | **satisfied** |
| QUAL-03 | Migration documentation | SATISFIED | [x] | Complete | **satisfied** |
| QUAL-04 | Upstream scenario tests | SATISFIED | [x] | Complete | **satisfied** |
| QUAL-05 | Quota pipeline tests | SATISFIED | [x] | Complete | **satisfied** |

**Score:** 29/29 requirements satisfied
**Orphaned requirements:** 0
**REQUIREMENTS.md updates needed:** 0 (all checkboxes and traceability updated since first audit)

---

## 4. Cross-Phase Integration

### Verified connections (28/31 wired)

**Core pipeline (fully wired):**

| Connection | Direction | Status |
|-----------|-----------|--------|
| Playground.prepare() → PlaygroundContext | Phase 4 internal | WIRED |
| PlaygroundContext → DirectExp.assemble(ctx) | Phase 4→3 | WIRED |
| AgentRuntimeSpec → AgentKernel.run(spec) | Phase 3→2 | WIRED |
| MessageBus → EventEmitterHook → EventRouter → handlers | Phase 1→2→5 | WIRED |
| ToolRegistry.execute() in kernel | Phase 3→2 | WIRED |
| ContextBuilder.build() in DirectExp | Phase 3 internal | WIRED |
| EvoToolAdapter bridges evomaster tools | Phase 4→3 | WIRED |
| 4 business hooks → spec.hooks | Phase 5→2 | WIRED |
| ChatHistoryConverter → kernel history | Phase 5 internal | WIRED |
| PlaygroundContext.with_bohrium() | Phase 5 internal | WIRED |
| PlaygroundContext.session/config_dir → DirectExp | Phase 6→4→3 | WIRED |
| _build_llm_provider → OpenAIProvider | Phase 6→2 | WIRED |
| _init_builtin_tools → ToolRegistry | Phase 6→3 | WIRED |
| Lazy import circular resolution | Phase 3↔2 | WIRED |
| All imports use new core/tools/types paths | Phase 7 | WIRED |

### Integration gaps (3 items, all tech_debt at service layer boundary)

1. **MCP/Skill factory not injected** (ASBL-02 partial)
   - `agent_run_service.py:425-432` — DirectExp 构造缺少 mcp_manager_factory/skill_registry_factory
   - ToolRegistry 注册路径完整（builtin/mcp/skill），但 service layer 未提供 EvoMaster factories
   - src/ Web Service 层重构在 out of scope

2. **WorkerRegistryServiceAdapter orphaned** (ASBL-06 partial)
   - `src/services/worker_registry_adapter.py` 存在且通过测试，但 agent_run_service.py 未引用
   - WorkerRegistry Protocol 未注入 matmaster 管道
   - 需求范围为"接口定义"（已完成），实际注入属后续集成

3. **run_agent_sync() return type mismatch** (MIGR-01/MIGR-02 partial)
   - run_agent_sync() 返回 None，agent_worker.py 期望 tuple
   - matmaster pipeline 内部正确完成，返回值协议属 src/ 层迁移范围

---

## 5. E2E Flow Verification

| Flow | Status | Notes |
|------|--------|-------|
| mat_master E2E (mock LLM) | COMPLETE | test_mat_master_e2e_pipeline 通过 |
| minimal E2E (mock LLM) | COMPLETE | test_minimal_e2e_pipeline 通过 |
| Upstream scenarios (mock) | COMPLETE | 15 tests: run_interrupted, cross-pod, workspace, Bohrium |
| Quota pipeline (mock) | COMPLETE | 5 tests: success/cancel/error/async/sync |
| MCP/Skill tool registration | BROKEN at factory injection | DirectExp 能力初始化路径存在但 service layer 未传入 factory |
| agent_worker return value parsing | BROKEN at return type | run_agent_sync() returns None vs expected tuple |

---

## 6. Tech Debt Summary

### Phase 6: Service Layer Wiring
- agent_run_service.py 构造 DirectExp 时缺少 mcp_manager_factory/skill_registry_factory 参数
- WorkerRegistryServiceAdapter 已创建但未在 agent_run_service.py 中注入
- run_agent_sync() 返回 None，需更新为 tuple 或更新 agent_worker.py 调用方

### Phase 3: Exp Assembly Layer
- ManuscriptGateGuard/AuthFailureGateGuard shell 类已删除（Phase 6），业务 Guard 注入点保留但 service layer 未传入任何业务 guard

### Phase 5: Integration and Quality
- FinishEvent 未通过 bus.emit() 发送到 MessageBus，前端 SSE 流中无 finish 类型的 matmaster 事件通知

### Total: 5 items across 3 phases

**Note:** 所有 tech debt 项目均位于 matmaster 框架与 src/ Web Service 层的集成边界。matmaster 框架内部（types/core/tools/hooks/integration/providers）的实现完整且无 debt。src/ 层重构在 REQUIREMENTS.md 中明确标记为 out of scope。

---

## 7. Nyquist Compliance

| Phase | VALIDATION.md | nyquist_compliant | wave_0_complete | Status |
|-------|---------------|-------------------|-----------------|--------|
| 01-foundation-contracts | exists | false | false | PARTIAL |
| 02-agent-kernel | exists | false | false | PARTIAL |
| 03-exp-assembly-layer | exists | false | false | PARTIAL |
| 04-playground-layer | exists | false | false | PARTIAL |
| 05-integration-quality | exists | true | false | COMPLIANT |
| 06-service-layer-wiring | exists | false | false | PARTIAL |
| 07-cleanup-traceability | missing | — | — | MISSING |

**Overall:** PARTIAL — 6/7 phases have VALIDATION.md, only Phase 5 is nyquist_compliant. Phases 1-4, 6 have draft VALIDATION.md (created at phase start, never filled). Phase 7 has no VALIDATION.md.

---

## 8. Test Suite

| Suite | Count | Result |
|-------|-------|--------|
| matmaster/types/ | 66 | PASSED |
| matmaster/core/ | ~170 | PASSED |
| matmaster/tools/ | ~25 | PASSED |
| matmaster/hooks/ | 15 | PASSED |
| matmaster/integration/ | ~75 | PASSED |
| matmaster/providers/ | 12 | PASSED |
| matmaster/playground/ (in core) | ~16 | PASSED |
| **Total** | **380** | **ALL PASSED** |

Note: Test count breakdown approximate due to Phase 7 directory restructure. Total of 380 verified by `pytest tests/matmaster/ -q` in Phase 7 verification.

---

## 9. Anti-Patterns

No critical anti-patterns. All intentional stubs from Phase 5 have been resolved in Phase 6. No TODO/FIXME/PLACEHOLDER in matmaster/ codebase. Zero references to old import paths (matmaster.engine/assembly/bus/playground).

---

## 10. Comparison with First Audit

| Metric | First Audit (Phases 1-5) | This Audit (Phases 1-7) |
|--------|--------------------------|-------------------------|
| Phases | 5/5 complete | 7/7 complete |
| Requirements | 29/29 satisfied | 29/29 satisfied |
| Integration | 26/29 (3 gaps) | 28/31 (3 gaps) |
| Tests | 368 | 380 |
| Tech debt items | 7 | 5 |
| Resolved since first | — | _build_llm_provider stub, _get_builtin_tools stub, QueueBridge orphan, REQUIREMENTS.md status updates, guard shell classes |

**Progress since first audit:**
- _build_llm_provider NotImplementedError → fully implemented config-driven LLM factory
- _get_builtin_tools empty list → builtin tools constructed in DirectExp.assemble() via ctx.session
- QueueBridge orphaned → completely removed, SSEHandler confirmed as production path
- Guard shell classes → removed, injection point retained
- REQUIREMENTS.md 5 status updates + 2 checkbox updates → all 29 now [x] Complete
- Directory restructure: engine/assembly/bus/playground → core/tools/types

---

_Audited: 2026-03-22T23:30:00Z_
_Auditor: Claude (gsd-audit-milestone)_
_Re-audit: Yes — after Phase 6/7 gap closure from first audit_
