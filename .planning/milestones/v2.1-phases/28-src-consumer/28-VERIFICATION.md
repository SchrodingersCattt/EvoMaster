---
phase: 28-src-consumer
verified: 2026-04-01T13:30:00Z
status: passed
score: 9/9 must-haves verified
gaps: []
human_verification: []
---

# Phase 28: src 反向依赖反转与 Consumer 迁移 — 验证报告

**Phase Goal:** 消除 matmaster 对 src 的反向依赖（bohrium_setup + script_env），同时迁移 src 消费者到 matmaster 原生数据结构与 session 抽象
**Verified:** 2026-04-01T13:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (来自 ROADMAP Success Criteria + Plan must_haves)

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | `matmaster/integration/bohrium_env.py` 提供 `BOHRIUM_OPENAPI_HOST`、`get_bohrium_credentials`、`get_bohrium_storage_config`、`inject_bohrium_executor`、`build_bohrium_skill_remote_env`、`BohriumSetupResult` | VERIFIED | 文件 220 行，全部 6 个符号已实现，无 evomaster/src/playground 依赖 |
| 2 | `bohrium_env.py` 不 import evomaster、src 或 playground | VERIFIED | `grep "from src\|from evomaster\|from playground" bohrium_env.py` 返回 0 行 |
| 3 | import audit 测试覆盖 matmaster/ 对 src 和 evomaster.env.bohrium 的反向依赖检测 | VERIFIED | `TestNoSrcImportsInMatmaster`、`TestNoEvomasterEnvBohriumImportsAnywhere` 已存在且无 xfail，测试通过 |
| 4 | `bohrium_setup.py` 不再包含 src lazy import，构造函数接受 4 个 callable 参数 | VERIFIED | L88-101 构造函数签名含 `load_credentials_fn`、`apply_credentials_fn`、`setup_fn`、`cleanup_fn`；无 `from src` |
| 5 | `script_env.py` 不再包含 `from src.utils.constant` 语句，改为 matmaster 侧 `BOHRIUM_OPENAPI_HOST` | VERIFIED | L58: `from matmaster.integration.bohrium_env import BOHRIUM_OPENAPI_HOST`；无 `from src` |
| 6 | `path_adaptor.py` 和 `job_service.py` 不再包含 `from evomaster.env.bohrium` 语句 | VERIFIED | `path_adaptor.py` L521/638 和 `job_service.py` L63 均已切换为 `from matmaster.integration.bohrium_env import` |
| 7 | `chat_history.py` 使用 `matmaster.types.messages` 消息类型，输出 matmaster 扁平 tool_calls 格式 | VERIFIED | L7-12 `from matmaster.types.messages import AssistantMessage, ToolCallData, ToolMessage, UserMessage`；`_tool_call_from_event` 返回 flat `{id, name, arguments}`；`ToolMessage` 构造使用 `tool_name=` |
| 8 | `agent_run_bohrium.py` 使用 `matmaster.sessions.ssh.SSHSession`，`_sync_skills_to_ssh_session` 不再通过 `_env` 属性穿透 | VERIFIED | L10: `from matmaster.sessions.ssh import SSHSession, SSHSessionConfig`；L171: `_upload_directory(ssh_session, ...)` 直接传入，无 `ssh_session._env` |
| 9 | `agent_run_service.py` 构造 `BohriumSetupService` 时传入 4 个回调函数 | VERIFIED | L304-315: `BohriumSetupService(load_credentials_fn=partial(...), apply_credentials_fn=..., setup_fn=..., cleanup_fn=partial(...), bus=bus)` |

**Score:** 9/9 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/integration/bohrium_env.py` | Bohrium 常量、纯函数、BohriumSetupResult | VERIFIED | 220 行，6 个符号，仅 import os/copy/typing |
| `tests/matmaster/test_bohrium_env.py` | bohrium_env 单元测试 | VERIFIED | 存在，含 `test_bohrium_openapi_host_default`，12 个测试通过 |
| `tests/matmaster/test_import_audit.py` | 扩展的 import audit 规则 | VERIFIED | 含 `TestNoSrcImportsInMatmaster`、`TestNoEvomasterSessionImportsInMatmaster`（xfail）、`TestNoEvomasterEnvBohriumImportsAnywhere` |
| `matmaster/integration/bohrium_setup.py` | 回调注入模式的 `BohriumSetupService` | VERIFIED | 含 `load_credentials_fn: Callable`，无 `from src`，无 `self._sessions_service` |
| `matmaster/tools/script_env.py` | matmaster 侧 `BOHRIUM_OPENAPI_HOST` 常量引用 | VERIFIED | L58: `from matmaster.integration.bohrium_env import BOHRIUM_OPENAPI_HOST` |
| `tests/matmaster/test_bohrium_setup_injection.py` | 回调注入模式单元测试 | VERIFIED | 存在，含 `test_load_credentials_calls_injected_fn`，6 个测试通过 |
| `src/services/chat_history.py` | matmaster 原生消息类型消费 | VERIFIED | L7: `from matmaster.types.messages import`；使用 `ToolCallData`、`tool_name=` |
| `src/services/agent_run_bohrium.py` | matmaster 原生 SSHSession 消费 | VERIFIED | L10: `from matmaster.sessions.ssh import SSHSession, SSHSessionConfig`；`_env` 已消除 |
| `src/services/agent_run_service.py` | BohriumSetupService 回调注入构造 | VERIFIED | L304-315: 含 `load_credentials_fn=` |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `bohrium_setup.py` | `bohrium_env.py` | `BohriumSetupResult import` | WIRED | L21: `from matmaster.integration.bohrium_env import BohriumSetupResult` |
| `script_env.py` | `bohrium_env.py` | `BOHRIUM_OPENAPI_HOST import` | WIRED | L58: lazy import within `_collect()` |
| `path_adaptor.py` | `bohrium_env.py` | `inject_bohrium_executor import` | WIRED | L521: lazy import，L638: `get_bohrium_storage_config` lazy import |
| `job_service.py` | `bohrium_env.py` | `get_bohrium_credentials import` | WIRED | L63: lazy import within `_get_access_key()` |
| `chat_history.py` | `matmaster/types/messages.py` | `AssistantMessage, ToolCallData, ToolMessage, UserMessage` | WIRED | L7-12: 顶层 import，L385/L349/L489 使用 |
| `agent_run_bohrium.py` | `matmaster/sessions/ssh.py` | `SSHSession, SSHSessionConfig import` | WIRED | L10: 顶层 import；L155 `isinstance`；L587 构造 |
| `agent_run_service.py` | `agent_run_bohrium.py` | 回调函数传入 BohriumSetupService | WIRED | L45-50 顶层 import；L305-313 `partial` 绑定 |

---

### Data-Flow Trace (Level 4)

不适用。本 Phase 无渲染动态数据的前端组件。涉及的是后端依赖反转和 import 路径切换，而非数据流管道。

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| bohrium_env 模块可独立 import | `uv run python -c "from matmaster.integration.bohrium_env import BOHRIUM_OPENAPI_HOST, BohriumSetupResult; print('OK')"` | 通过（import audit 测试间接验证） | PASS |
| 全量 Phase 28 测试套件 | `uv run pytest tests/matmaster/test_import_audit.py tests/matmaster/test_bohrium_setup_injection.py tests/matmaster/test_bohrium_env.py tests/matmaster/integration/test_events_to_messages.py -x -q` | `47 passed, 1 xfailed in 0.39s` | PASS |
| matmaster/ 无 src.* 运行时依赖 | `grep -rn "from src\." matmaster/` | 0 行输出 | PASS |
| matmaster/ 无 evomaster.env.bohrium 依赖 | `grep -rn "from evomaster.env.bohrium" matmaster/` | 0 行输出 | PASS |

---

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| **INVR-01** | 28-01, 28-02, 28-03 | `bohrium_setup.py` 不再 lazy import `src.services.agent_run_bohrium` 的 5 个函数，改为回调注入 | SATISFIED | `bohrium_setup.py` 构造函数使用 4 callable 参数；`grep "from src" bohrium_setup.py` 返回 0；`agent_run_service.py` L304-315 通过 `partial` 绑定回调 |
| **INVR-02** | 28-01, 28-02 | `script_env.py` 不再 lazy import `src.utils.constant.BOHRIUM_OPENAPI_HOST` | SATISFIED | `script_env.py` L58: `from matmaster.integration.bohrium_env import BOHRIUM_OPENAPI_HOST`；无 `from src` |
| **CONS-03** | 28-03 | `chat_history.py` 可消费 matmaster 原生 message / tool_call 数据结构 | SATISFIED | `from matmaster.types.messages import AssistantMessage, ToolCallData, ToolMessage, UserMessage`；flat 格式 `_tool_call_from_event`；17 个 events_to_messages 测试通过 |
| **CONS-04** | 28-03 | `agent_run_bohrium.py` 切换到 matmaster session abstraction | SATISFIED | `from matmaster.sessions.ssh import SSHSession, SSHSessionConfig`；`ssh_session._env` 属性访问已消除 |

无孤立需求（ORPHANED requirements）。

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tests/matmaster/test_import_audit.py` | 167 | `@pytest.mark.xfail` on `TestNoEvomasterSessionImportsInMatmaster` | INFO | `bash_tool.py` 仍有 `evomaster.agent.session.local` lazy import，已明确标注为 Phase 28 范围外，`strict=False` |

无 Blocker 或 Warning 级别的反模式。

---

### Human Verification Required

无。所有关键改动均可通过静态代码分析和自动化测试验证：
- import 路径切换通过 AST-based import audit 验证
- 回调注入模式通过 `test_bohrium_setup_injection.py` 6 个单元测试验证
- `events_to_dialog_messages` 行为兼容性通过 17 个 `test_events_to_messages.py` 测试验证
- matmaster/ 模块隔离通过 `grep` 全量扫描验证

---

### Gaps Summary

无 Gap。Phase 28 的 4 个需求（INVR-01、INVR-02、CONS-03、CONS-04）全部满足：

1. **INVR-01**：bohrium_setup.py 回调注入完成，4 个 src lazy import 消除，agent_run_service.py 适配新签名
2. **INVR-02**：script_env.py BOHRIUM_OPENAPI_HOST 从 src 切换到 matmaster 侧常量
3. **CONS-03**：chat_history.py 全量使用 matmaster 消息类型（ToolCallData、AssistantMessage、ToolMessage），保持 events_to_dialog_messages 历史恢复行为兼容
4. **CONS-04**：agent_run_bohrium.py 使用 matmaster SSHSession，_env 属性穿透消除

唯一已知残留：`bash_tool.py` 的 `evomaster.agent.session.local` lazy import，已被 xfail 标记跟踪，明确在本 Phase 范围之外（Phase 28 RESEARCH.md 记录为 out-of-scope）。

---

_Verified: 2026-04-01T13:30:00Z_
_Verifier: Claude (gsd-verifier)_
