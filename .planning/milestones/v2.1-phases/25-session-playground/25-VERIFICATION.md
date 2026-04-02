---
phase: 25-session-playground
verified: 2026-04-01T10:00:00Z
status: passed
score: 8/8 must-haves verified
re_verification: false
gaps: []
---

# Phase 25: Session 与 Playground 原生化 Verification Report

**Phase Goal:** matmaster 具备自有的 session 抽象、config 加载与 playground 环境准备能力，切断对 evomaster session/config/mixin 的运行时依赖
**Verified:** 2026-04-01T10:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth                                                                                                    | Status     | Evidence                                                                           |
|----|----------------------------------------------------------------------------------------------------------|------------|------------------------------------------------------------------------------------|
| 1  | Session Protocol 存在 8 个方法签名                                                                       | ✓ VERIFIED | `matmaster/types/session.py` 定义 `@runtime_checkable class Session(Protocol)` with is_open, open, close, exec_bash, read_file, write_file, path_exists, is_file |
| 2  | LocalSession 满足 Session Protocol                                                                       | ✓ VERIFIED | `isinstance(LocalSession('/tmp'), Session)` 返回 True（运行验证通过）                |
| 3  | LocalSession 追踪 is_open 状态，接受 encoding 参数                                                       | ✓ VERIFIED | `_is_open: bool = False`，`@property is_open`，构造函数含 `encoding: str = "utf-8"` |
| 4  | SSHSession 满足 Session Protocol，零 evomaster import                                                    | ✓ VERIFIED | `isinstance(SSHSession(cfg), Session)` 为 True；AST scan 无 evomaster 导入          |
| 5  | playground.py 零 evomaster import（7 处全消除）                                                          | ✓ VERIFIED | AST scan：0 evomaster ImportFrom/Import；不含 ConfigManager、PlaygroundSessionMixin、BaseSession |
| 6  | PlaygroundManager 通过 yaml.safe_load + 参数化构造 Playground                                           | ✓ VERIFIED | `PlaygroundManager.get_or_create` 调用 `_load_raw_config()`（yaml.safe_load），传参给 `Playground(session_type=..., session_config=..., ...)` |
| 7  | agent_run_service.py 不访问 playground.config_path 或 playground.config                                 | ✓ VERIFIED | grep 无匹配；服务层通过 `_MATMASTER_CONFIG_DIR = _project_root / 'matmaster_config'` 直接读取配置 |
| 8  | PlaygroundContext.session 类型为 Session \| None                                                         | ✓ VERIFIED | `context.py` 第 59 行：`session: Session | None = None`；`from matmaster.types.session import Session` |

**Score:** 8/8 truths verified

---

### Required Artifacts

| Artifact                                              | Expected                                               | Status     | Details                                          |
|-------------------------------------------------------|--------------------------------------------------------|------------|--------------------------------------------------|
| `matmaster/types/session.py`                          | Session Protocol + 3 Config models                     | ✓ VERIFIED | 114 行；含 Session, SessionConfig, LocalSessionConfig, SSHSessionConfig，全部导出至 `matmaster/types/__init__.py` |
| `matmaster/sessions/tmux.py`                          | PS1_PATTERN, PS1_BEGIN, PS1_END, BashMetadata          | ✓ VERIFIED | 导出至 `matmaster/sessions/__init__.py`           |
| `matmaster/sessions/local.py`                         | 升级后的 LocalSession（满足 Protocol）                 | ✓ VERIFIED | 108 行；含 is_open property，encoding 参数，全部 8 个 Protocol 方法 |
| `matmaster/sessions/ssh.py`                           | SSHSession 原生实现（min 300 行）                      | ✓ VERIFIED | 777 行；无 evomaster import；含全部 8 个 Protocol 方法 + upload_directory_tarball, ssh_exec, ssh_bash_noninteractive |
| `matmaster/core/playground.py`                        | 参数化 Playground + 内联 Mixin + 零 evomaster import   | ✓ VERIFIED | 含 `class Playground:`，`def __init__(self, *, session_type`，attach_session, attach_ssh_session, detach_session 方法；yaml.safe_load 在 PlaygroundManager |
| `matmaster/types/context.py`                          | session: Session \| None                               | ✓ VERIFIED | 第 59 行：`session: Session | None = None`        |
| `src/services/agent_run_service.py`                   | 直接读取 matmaster_config/                             | ✓ VERIFIED | `_MATMASTER_CONFIG_DIR = _project_root / 'matmaster_config'`；无 playground.config_path/playground.config |
| `tests/matmaster/core/test_playground_no_evomaster.py` | import audit 测试（min 15 行）                        | ✓ VERIFIED | 67 行；6 个 audit 测试（no evomaster import、no PlaygroundSessionMixin、no ConfigManager、no BaseSession、parameterized constructor、inlined methods）|

---

### Key Link Verification

| From                                   | To                              | Via                                            | Status     | Details                                                                  |
|----------------------------------------|---------------------------------|------------------------------------------------|------------|--------------------------------------------------------------------------|
| `matmaster/sessions/local.py`          | `matmaster/types/session.py`    | structural typing Protocol                     | ✓ WIRED    | `isinstance(LocalSession('/tmp'), Session)` 运行通过；LocalSession 实现全部 8 个方法 |
| `matmaster/sessions/ssh.py`            | `matmaster/types/session.py`    | structural typing Protocol                     | ✓ WIRED    | `isinstance(SSHSession(cfg), Session)` 运行通过                           |
| `matmaster/sessions/ssh.py`            | `matmaster/sessions/tmux.py`    | `from matmaster.sessions.tmux import`          | ✓ WIRED    | 第 24 行：`from matmaster.sessions.tmux import PS1_PATTERN, BashMetadata` |
| `matmaster/types/context.py`           | `matmaster/types/session.py`    | `from matmaster.types.session import Session`  | ✓ WIRED    | 第 17 行：`from matmaster.types.session import Session`                   |
| `matmaster/core/playground.py`         | `matmaster/sessions/local.py`   | `from matmaster.sessions.local import LocalSession` | ✓ WIRED | 第 25 行顶层 import；用于 `_create_session_from_config` local 分支          |
| `matmaster/core/playground.py`         | `matmaster/sessions/ssh.py`     | lazy import in attach_ssh_session / _create_session_from_config | ✓ WIRED | `from matmaster.sessions.ssh import SSHSession`（在方法内按需导入）        |
| `matmaster/core/playground.py` (PlaygroundManager) | `config.yaml`      | `yaml.safe_load` 解析后参数化构造                | ✓ WIRED    | `_load_raw_config` 调用 `yaml.safe_load`；`get_or_create` 解析 session 块传给 `Playground(...)` |
| `src/services/agent_run_service.py`    | `matmaster_config/llm_config.yaml` | `_project_root / 'matmaster_config' / 'llm_config.yaml'` | ✓ WIRED | 第 338 行；`_MATMASTER_CONFIG_DIR` 模块级常量                             |

---

### Data-Flow Trace (Level 4)

不适用——本 phase 交付物为 Protocol 定义、session 实现、配置读取和服务层适配，不包含渲染动态数据的组件。

---

### Behavioral Spot-Checks

| Behavior                                                   | Command / Check                                     | Result                            | Status   |
|------------------------------------------------------------|-----------------------------------------------------|-----------------------------------|----------|
| LocalSession 可在不安装 evomaster 时创建                    | `from matmaster.sessions.local import LocalSession` | 无 evomaster 模块被加载             | ✓ PASS   |
| LocalSession isinstance Session                            | `isinstance(LocalSession('/tmp'), Session)`         | True                              | ✓ PASS   |
| SSHSession isinstance Session                              | `isinstance(SSHSession(cfg), Session)`              | True                              | ✓ PASS   |
| SSHSession 无 evomaster import（AST）                      | `ast.parse(ssh.py)` evomaster 节点数                | 0                                 | ✓ PASS   |
| playground.py 无 evomaster import（AST）                   | `ast.parse(playground.py)` evomaster 节点数         | 0                                 | ✓ PASS   |
| playground.py 无 ConfigManager / PlaygroundSessionMixin    | grep                                                | 不含这两个名称                      | ✓ PASS   |
| agent_run_service.py 无 playground.config_path             | grep                                                | 无匹配                            | ✓ PASS   |
| PlaygroundContext(session=LocalSession) 构造成功            | Python 构造调用                                     | 成功                              | ✓ PASS   |
| 全部 101 个测试通过（含 1 xpassed）                         | `uv run pytest ... -x --tb=short -q`                | 101 passed, 1 xpassed in 7.58s   | ✓ PASS   |

---

### Requirements Coverage

| Requirement | Source Plan | Description                                                                                                                                         | Status      | Evidence                                                                                    |
|-------------|-------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|-------------|---------------------------------------------------------------------------------------------|
| PLAY-01     | 25-01       | 开发者可以在不安装 evomaster 的环境中创建并使用 `matmaster.sessions.local.LocalSession`，供 builtin tools 执行本地命令与文件操作                       | ✓ SATISFIED | LocalSession 导入后无 evomaster 模块加载；PlaygroundContext 可用 LocalSession 构造；BashTool 等 builtin tool 正常使用 matmaster LocalSession |
| PLAY-02     | 25-02, 25-03 | 开发者可以通过 matmaster 原生 session factory 创建 local/docker/ssh session，而 `matmaster.core.playground.Playground` 不再直接 import `evomaster.agent.session.*` | ✓ SATISFIED | SSHSession 原生实现（777 行，无 evomaster）；playground.py AST 扫描 0 evomaster 节点；local + ssh 分支均在 `_create_session_from_config` 中实现 |
| PLAY-03     | 25-03       | 开发者可以通过 `matmaster.core.playground.Playground` 加载主配置、准备 workspace、logging 和 session，而不依赖 `evomaster.config.ConfigManager` 或 `PlaygroundSessionMixin` | ✓ SATISFIED | playground.py 不含 ConfigManager 或 PlaygroundSessionMixin；PlaygroundManager 通过 yaml.safe_load 独立读取 config.yaml；Playground.prepare() 完整实现 workspace/logging/session 准备 |

所有 3 个 requirements 均已满足。REQUIREMENTS.md Traceability 表显示 PLAY-01 和 PLAY-03 标注 Pending，但代码实现已完全交付。

---

### Anti-Patterns Found

无 blocker 或 warning 级别的 anti-pattern。

以下为 info 级别（注释中的 evomaster 字符串，不触发运行时导入）：

| File                                    | Line | Pattern                                          | Severity | Impact                    |
|-----------------------------------------|------|--------------------------------------------------|----------|---------------------------|
| `matmaster/core/playground.py`          | 147  | 注释 `# Session management (inlined from evomaster mixin)` | ℹ Info | 仅注释，不影响运行时依赖   |

---

### Human Verification Required

无——所有可观测目标均已通过程序化验证。

以下为可选的端到端确认项（非阻塞）：

1. **Bohrium 执行路径完整性**

   **Test:** 触发一次真实的 Bohrium 任务，验证 `agent_run_bohrium.py` 通过 `pg.session = ssh_session` 直接赋值后 session 正常工作
   **Expected:** SSH 命令执行成功，日志无 evomaster session 相关错误
   **Why human:** 需要真实 Bohrium 容器环境，无法在本地程序化验证

---

## Gaps Summary

无 gap。Phase 25 的全部目标已实现：

- Session Protocol (8 方法) 定义完毕，LocalSession 与 SSHSession 均满足 Protocol
- playground.py 消除全部 evomaster 运行时依赖（包括 ConfigManager、PlaygroundSessionMixin、BaseSession、evomaster session 模块）
- PlaygroundManager 通过 YAML 解析 + 参数化构造独立管理 Playground 生命周期
- agent_run_service.py 直接访问 `matmaster_config/` 目录，不再依赖 playground.config_path 或 playground.config
- 101 个测试通过，含 6 个 import audit 测试和 16 个 mocked SSH session 测试

REQUIREMENTS.md Traceability 中 PLAY-01 和 PLAY-03 仍标注 Pending，但实现代码完全符合要求。建议在 REQUIREMENTS.md 中将 PLAY-01 和 PLAY-03 更新为 Complete。

---

_Verified: 2026-04-01T10:00:00Z_
_Verifier: Claude (gsd-verifier)_
