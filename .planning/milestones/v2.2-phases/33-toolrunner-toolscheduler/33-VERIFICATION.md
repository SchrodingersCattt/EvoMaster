---
phase: 33-toolrunner-toolscheduler
verified: 2026-04-02T13:12:50Z
status: passed
score: 4/4 success criteria verified
gaps: []
human_verification: []
warnings:
  - "Full tests/ collection is currently blocked by tests/test_chat_session_list.py importing app.py, which tries to create /data/logs on a read-only filesystem. This is outside Phase 33 codepaths."
---

# Phase 33: ToolRunner 完整实现 + ToolScheduler 验证报告

**Phase Goal:** 工具执行通过完整的 查找->校验->调度->执行->释放 链路运行，资源调度支持 exclusive/shared_read/counted 三种模式；gap closure 收口 canonical effect_level、ToolCompiler、Session.capabilities
**Verified:** 2026-04-02T13:12:50Z
**Status:** passed
**Re-verification:** Yes — initial gaps on `effect_level` 已收口，advancement gap（ToolCompiler + Session.capabilities）已补齐

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | 完整 ToolRunner 执行链 ToolCatalog 查找 -> StructuralValidation -> RunStateGuard -> CapabilityPolicy -> ToolScheduler -> executor -> 释放 端到端可运行 | VERIFIED | `uv run pytest tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_tool_runner.py -x -q` 包含在 phase 33 专项套件中，通过 |
| SC2 | ToolScheduler 对 exclusive/shared_read/counted 资源实现正确调度 | VERIFIED | `tests/matmaster/core/test_tool_scheduler.py` 在 phase 33 专项套件中通过 |
| SC3 | StructuralValidation 与 CapabilityPolicy 对参数/plane/capability/effect_level 约束返回正确 deny | VERIFIED | `tests/matmaster/core/test_structural_validation.py`、`tests/matmaster/core/test_capability_policy.py` 通过；真实 `BUILTIN_META` 回归测试覆盖 `external_write` 路径 |
| SC4 | Phase 33 gap closure 项完成：canonical effect_level、ToolCompiler、Session.capabilities 全部落地 | VERIFIED | `33-04-SUMMARY.md`、`33-05-SUMMARY.md` 已存在；`tests/matmaster/tools/test_tool_compiler.py` 与 `tests/matmaster/sessions/test_session_capabilities.py` 通过 |

**Score:** 4/4 success criteria verified

## Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `matmaster/core/structural_validation.py` | VERIFIED | Layer A 三步校验实现保留，相关测试通过 |
| `matmaster/core/capability_policy.py` | VERIFIED | Layer C effect_level + capability 匹配实现保留，真实 builtin metadata 回归通过 |
| `matmaster/core/tool_scheduler.py` | VERIFIED | exclusive/shared_read/counted 调度测试通过 |
| `matmaster/core/tool_runner.py` | VERIFIED | FullToolRunner fast path 已统一到 `pure_read`，执行链测试通过 |
| `matmaster/tools/tool_catalog.py` | VERIFIED | 通过 `ToolCompiler` 编译/缓存 ToolInstance，且兼容现有 `ToolCatalog(registry)` 调用 |
| `matmaster/tools/tool_compiler.py` | VERIFIED | 新增 89 行，内建工具 plane/effect/resource 规则集中实现 |
| `matmaster/types/session.py` | VERIFIED | `Session` Protocol 正式声明 `capabilities` |
| `matmaster/sessions/local.py` | VERIFIED | 返回 native/stateless/exec_cancel=True 的 SessionCapabilities |
| `matmaster/sessions/ssh.py` | VERIFIED | 返回 sftp/upload_support=True/exec_cancel=True 的 SessionCapabilities |
| `tests/matmaster/tools/test_tool_compiler.py` | VERIFIED | 6 个 ToolCompiler/ToolCatalog delegation 测试通过 |
| `tests/matmaster/sessions/test_session_capabilities.py` | VERIFIED | 5 个 session capability 测试通过 |

## Key Link Verification

| From | To | Via | Pattern | Status |
|------|----|-----|---------|--------|
| `matmaster/tools/tool_catalog.py` | `matmaster/tools/tool_compiler.py` | ToolCatalog 委托编译 | `self._compiler.compile` | VERIFIED |
| `matmaster/tools/tool_compiler.py` | `matmaster/types/tool_spec.py` | 产出 `ToolInstance`/`ToolBinding`/`ToolSpec` | `ToolInstance(` | VERIFIED |
| `matmaster/tools/tool_compiler.py` | `matmaster/types/topology.py` | 编译接口显式接收拓扑 | `RuntimeTopology` | VERIFIED |
| `matmaster/types/session.py` | `matmaster/sessions/local.py` | 协议 -> 本地实现 | `def capabilities` | VERIFIED |
| `matmaster/types/session.py` | `matmaster/sessions/ssh.py` | 协议 -> SSH 实现 | `def capabilities` | VERIFIED |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase 33 专项测试 | `uv run pytest tests/matmaster/core/test_structural_validation.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_tool_scheduler.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_builtin_claims.py tests/matmaster/core/test_tool_runner.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/tools/test_tool_catalog.py tests/matmaster/sessions/test_session_capabilities.py tests/matmaster/types/test_session_protocol.py -x -q` | `112 passed in 0.57s` | PASS |
| Cross-phase regression gate | `uv run pytest tests/matmaster/types/test_topology.py tests/matmaster/types/test_tool_spec.py tests/matmaster/types/test_tool_decision.py tests/matmaster/types/test_session_protocol.py tests/matmaster/tools/test_tool_catalog.py tests/matmaster/tools/test_web_search_tool.py tests/matmaster/tools/test_spawn_tool.py tests/matmaster/core/test_tool_runner.py tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_exp.py tests/matmaster/sessions/test_local.py tests/matmaster/sessions/test_ssh_session.py -x -q` | `189 passed, 5 warnings in 1.46s` | PASS |
| Full suite smoke | `uv run pytest tests/ -x --tb=short` | collection blocked by `tests/test_chat_session_list.py` importing `app.py` → `/data/logs` on read-only FS | WARNING |

## Requirements Coverage

| Requirement | Status | Evidence |
|-------------|--------|----------|
| TRUN-03 | SATISFIED | FullToolRunner + ToolCatalog/ToolCompiler path verified by `test_full_tool_runner.py`, `test_tool_compiler.py`, `test_tool_catalog.py` |
| TRUN-04 | SATISFIED | ToolScheduler scheduling tests continue to pass |
| TCON-01 | SATISFIED | StructuralValidation behavior remains green; Session.capabilities data source now exists concretely |
| TCON-03 | SATISFIED | Canonical `pure_read / local_mutation / external_write` enum aligned; real builtin metadata policy tests pass |

## Gap Closure Verification

### 33-04 effect_level canonicalization

- `BUILTIN_META` 使用 `pure_read / local_mutation / external_write`
- `FullToolRunner` fast path 检查 `pure_read`
- 真实 `BUILTIN_META` 值驱动的 CapabilityPolicy 回归测试通过

### 33-05 ToolCompiler + Session.capabilities

- `ToolCompiler.compile()` 对 builtin/unknown tool 返回正确 plane/resource/effect/source
- `ToolCatalog` 通过可选注入的 `compiler/topology` 委托编译，同时保持旧构造方式兼容
- `LocalSession` / `SSHSession` 正式暴露 `capabilities`

## Issues Encountered

- 全量 `tests/` 在 `tests/test_chat_session_list.py` 的导入期被环境阻塞：`app.py` 启动日志时尝试创建 `/data/logs`，当前环境该路径为只读文件系统。该错误未经过 Phase 33 代码路径，且与本次变更文件无交集，因此记录为外部/环境型 warning，而非本 phase gap。

## Conclusion

Phase 33 的原始目标与后续 gap closure 都已满足，且 cross-phase regression gate 通过。当前唯一未绿项是全量测试收集阶段的环境型 `/data/logs` 问题，和 Tool Runtime v2 / Session.capabilities / ToolCompiler 的交付无关。

---
*Verified: 2026-04-02T13:12:50Z*
*Verifier: Codex (inline fallback, gsd-verifier unavailable/unreliable in this runtime)*
