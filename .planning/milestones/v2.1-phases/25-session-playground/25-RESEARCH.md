# Phase 25: Session 与 Playground 原生化 - Research

**Researched:** 2026-04-01
**Domain:** matmaster Session/Playground 层 evomaster 依赖切断
**Confidence:** HIGH

## Summary

Phase 25 的目标是切断 `matmaster/core/playground.py` 对 evomaster 的全部运行时 import（当前 7 处），建立 matmaster 自有的 Session Protocol、LocalSession/SSHSession 原生实现，以及参数化 Playground 构造。

核心改造涉及三个维度：(1) 在 `matmaster/types/session.py` 定义 `@runtime_checkable Protocol`，在 `matmaster/sessions/` 提供 LocalSession 和 SSHSession 原生实现；(2) Playground 类从继承 `PlaygroundSessionMixin` + `ConfigManager` 驱动，改为参数化构造（接受 session_type、archival 等参数），PlaygroundManager 负责读 YAML 后拆参数；(3) `agent_run_service.py` 和 `bohrium_setup.py` 中对 `playground.config.agents` / `playground.config_path` 的借用迁移到 service 层自行处理。

**Primary recommendation:** 按 Protocol 定义 -> LocalSession 升级 -> SSHSession 原生化 -> Playground 参数化 -> PlaygroundManager YAML 解析 -> 调用方适配 的顺序逐步实施，每步可独立测试。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Session 抽象使用 `@runtime_checkable Protocol`（与 matmaster 其他抽象一致：Tool/Hook/Guard/LLMProvider 全部是 Protocol）
- **D-02:** Protocol 包含核心 5 方法 + 生命周期：`exec_bash`, `read_file`, `write_file`, `path_exists`, `is_file`, `open`, `close`, `is_open`
- **D-03:** Protocol 定义放在 `matmaster/types/session.py`（与 context.py/runtime.py/messages.py 一致），实现放在 `matmaster/sessions/`
- **D-04:** SessionConfig 用精简版 Pydantic model，只保留 `timeout` + `workspace_path` + `working_dir`。LocalSessionConfig 继承加 `encoding`。不复制 gpu_devices/cpu_devices/symlinks 等未使用字段
- **D-05:** Playground 不再读 config.yaml，改为参数化构造（接受 session_type、archival 等参数）。与 DevRunner 已有的干净模式一致
- **D-06:** YAML 解析逻辑放在 PlaygroundManager 内部，读 config.yaml 后拆分参数传给 Playground 构造函数
- **D-07:** `playground.config.agents` 和 `playground.config_path.parent`（被 agent_run_service 借用来定位 LLM config）迁移到 service 层自行处理，不再通过 Playground 代理
- **D-08:** Phase 25 同时原生化 LocalSession + SSHSession，两者一步到位
- **D-09:** DockerSession 废弃，playground.py 中删除 docker 分支，不迁移到 matmaster
- **D-10:** SSHSession 原生实现放在 `matmaster/sessions/ssh.py`，复用 paramiko（evomaster 已有的依赖），接口匹配 Session Protocol
- **D-11:** PlaygroundSessionMixin 的 `attach_session`/`attach_ssh_session` 内联到 Playground 类，删除 Mixin 继承关系
- **D-12:** 内联后使用 matmaster 原生 SSHSession 替代 evomaster 的

### Claude's Discretion
- SSHSession 原生实现的内部结构（连接池、重连策略等）由 Claude 根据 evomaster 现有实现判断复制范围
- PlaygroundManager 内部 YAML 解析的具体字段提取方式

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PLAY-01 | 开发者可以在不安装 evomaster 的环境中创建并使用 `matmaster.sessions.local.LocalSession`，供 builtin tools 执行本地命令与文件操作 | Session Protocol 定义 + LocalSession 升级（添加 `is_open`/`config` 属性）+ BuiltinTool base 已通过 `_require_session()` 使用 duck typing |
| PLAY-02 | matmaster 原生 session factory 可以创建 local/ssh session，而 `matmaster.core.playground.Playground` 运行时不再导入 `evomaster.agent.session` 下的任何模块 | SSHSession 原生实现 + Playground `_create_session_from_config()` 改用 matmaster session + 删除 docker 分支 |
| PLAY-03 | `matmaster.core.playground.Playground` 可以独立完成主配置加载、workspace 准备、logging 初始化与 session 装配，不再依赖 `evomaster.config.ConfigManager` 或 `PlaygroundSessionMixin` | Playground 参数化构造 + PlaygroundManager YAML 解析 + Mixin 内联 |
</phase_requirements>

## Architecture Patterns

### Current evomaster Import Map in playground.py (7 points to eliminate)

| Line | Import | Used By | Elimination Strategy |
|------|--------|---------|---------------------|
| 26 | `evomaster.agent.session.base.BaseSession` | Type hint: `self.session`, `attach_session` 参数, `_create_session_from_config` 返回 | 替换为 matmaster Session Protocol |
| 27 | `evomaster.agent.session.local.LocalSession, LocalSessionConfig` | `_create_session_from_config` local 分支, `attach_session` isinstance 检查 | 替换为 `matmaster.sessions.local.LocalSession` + `matmaster.types.session.LocalSessionConfig` |
| 28 | `evomaster.config.ConfigManager` | `__init__` 加载 config | 消除：参数化构造，ConfigManager 移至 PlaygroundManager |
| 29 | `evomaster.core.playground_session.PlaygroundSessionMixin` | 类继承 | 消除：内联 `attach_session`/`attach_ssh_session`/`detach_session` |
| 150 | `evomaster.agent.session.docker.DockerSession, DockerSessionConfig` | `_create_session_from_config` docker 分支（lazy import） | 直接删除（D-09 废弃 Docker） |
| 159 | `evomaster.agent.session.ssh.SSHSession, SSHSessionConfig` | `_create_session_from_config` ssh 分支（lazy import） | 替换为 `matmaster.sessions.ssh.SSHSession, SSHSessionConfig` |
| 374 | `import evomaster` | `validate_startup` 中检测 evomaster 并 warn | 删除整个 try/except 块 |

### Recommended Project Structure (Post Phase 25)

```
matmaster/
├── types/
│   ├── session.py          # NEW: Session Protocol + SessionConfig + LocalSessionConfig + SSHSessionConfig
│   ├── context.py          # MODIFIED: session: Any -> session: Session | None
│   └── ...
├── sessions/
│   ├── __init__.py         # MODIFIED: export all session types
│   ├── local.py            # MODIFIED: upgrade to match Protocol
│   ├── ssh.py              # NEW: SSHSession 原生实现
│   └── tmux.py             # NEW: PS1_PATTERN + BashMetadata (从 evomaster/env/docker.py 迁移)
├── core/
│   ├── playground.py       # MODIFIED: 参数化构造, 0 evomaster imports
│   └── ...
└── ...
```

### Pattern 1: Session Protocol (D-01, D-02, D-03)

**What:** 使用 `@runtime_checkable Protocol` 定义 session 接口，与 matmaster 其他 Protocol（Tool, Hook, Guard, LLMProvider）保持一致风格。

**Why Protocol instead of ABC:** matmaster 的 BuiltinTool 已通过 `_require_session()` 返回 `Any` 并用 duck typing 调用 session 方法。Protocol 是 structural typing，天然兼容已有 evomaster session（无需修改 evomaster 代码），也兼容 mock 对象。

**Example:**
```python
# matmaster/types/session.py
from __future__ import annotations
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class SessionConfig(BaseModel):
    """Session base config -- immutable."""
    model_config = ConfigDict(frozen=True)

    timeout: int = Field(default=300)
    workspace_path: str = Field(default="/workspace")
    working_dir: str = Field(default="/workspace")


class LocalSessionConfig(SessionConfig):
    """Local session config."""
    encoding: str = Field(default="utf-8")


class SSHSessionConfig(SessionConfig):
    """SSH session config."""
    host: str
    port: int = Field(default=22)
    username: str = Field(default="root")
    password: str | None = Field(default=None)
    key_file: str | None = Field(default=None)
    key_data: str | None = Field(default=None)
    passphrase: str | None = Field(default=None)
    connect_timeout: int = Field(default=10)
    keepalive_interval: int = Field(default=30)
    max_retries: int = Field(default=3)

    def __repr_args__(self):
        for k, v in super().__repr_args__():
            if k in ("password", "key_data", "passphrase") and v is not None:
                yield k, "***"
            else:
                yield k, v


@runtime_checkable
class Session(Protocol):
    """Session contract -- structural typing, no inheritance needed."""

    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...
    def close(self) -> None: ...

    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        is_input: bool = False,
        stop_event: threading.Event | Any | None = None,
    ) -> dict[str, Any]: ...

    def read_file(self, path: str, encoding: str = "utf-8") -> str: ...
    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None: ...
    def path_exists(self, path: str) -> bool: ...
    def is_file(self, path: str) -> bool: ...
```

### Pattern 2: Playground 参数化构造 (D-05, D-06)

**What:** Playground 不再接受 `config_path`，改为接受已解析的参数（session_type, workspace_base, archival_config 等）。PlaygroundManager 负责 YAML -> 参数 的转换。

**Current pattern (to be replaced):**
```python
# playground.py __init__
self.config_manager = ConfigManager(config_dir=..., config_file=...)
self.config = self.config_manager.load()
```

**Target pattern (after Phase 25):**
```python
# playground.py __init__
class Playground:
    def __init__(
        self,
        *,
        session_type: str = "local",
        session_config: dict[str, Any] | None = None,
        archival: WorkspaceArchivalConfig | None = None,
        workspace_base: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        # No ConfigManager, no config_path, no evomaster imports
        ...

# playground_manager.py or playground.py PlaygroundManager
class PlaygroundManager:
    def get_or_create(self, session_id: str) -> Playground:
        config = self._load_yaml_config()  # reads config.yaml
        return Playground(
            session_type=config["session"]["type"],
            session_config=config["session"].get(config["session"]["type"], {}),
            archival=self._build_archival(config),
            ...
        )
```

**Reference:** `matmaster/devshell/runner.py:82-89` -- DevRunner already constructs PlaygroundContext directly without ConfigManager.

### Pattern 3: SSHSession 原生化 (D-10)

**What:** 将 `evomaster/agent/session/ssh.py` + `evomaster/env/ssh.py` 的核心逻辑合并为 `matmaster/sessions/ssh.py`。

**SSHSession 的依赖链分析：**
```
evomaster/agent/session/ssh.py
  -> evomaster/agent/session/base.py (BaseSession ABC)  -- replaced by Protocol
  -> evomaster/env/docker.py (PS1_PATTERN, BashMetadata) -- copy to matmaster
  -> evomaster/env/ssh.py (SSHEnv, SSHEnvConfig)         -- inline into SSHSession
     -> evomaster/env/base.py (BaseEnv, EnvConfig)        -- not needed, SSHEnv 方法直接内联
     -> paramiko                                          -- keep, already a dependency
```

**Design decision: inline SSHEnv into SSHSession.** evomaster 的 Session->Env 双层架构（Session 调 Env，Env 封装底层操作）在 SSH 场景下增加了不必要的间接层。matmaster 的 SSHSession 应直接持有 `paramiko.SSHClient` 和 `SFTPClient`，将 SSHEnv 的核心方法（`_connect`, `_open_sftp`, `_ensure_connected`, `_setup_tmux`, `tmux_send_keys`, `get_tmux_logs`, SFTP 文件操作）内联为 SSHSession 的私有方法。

**需要迁移的核心组件：**

| Component | Source | Target | Notes |
|-----------|--------|--------|-------|
| `PS1_PATTERN` | `evomaster/env/docker.py:33` | `matmaster/sessions/tmux.py` | 正则常量，SSH 和 Docker 共用 |
| `BashMetadata` | `evomaster/env/docker.py:39-79` | `matmaster/sessions/tmux.py` | PS1 解析，to_ps1_prompt/from_json |
| `SSHEnv._connect` | `evomaster/env/ssh.py:135-199` | `matmaster/sessions/ssh.py:_connect` | paramiko 连接 + 重试 |
| `SSHEnv._open_sftp` | `evomaster/env/ssh.py:203-208` | `matmaster/sessions/ssh.py:_open_sftp` | SFTP channel |
| `SSHEnv._ensure_connected` | `evomaster/env/ssh.py:210-219` | `matmaster/sessions/ssh.py:_ensure_connected` | 断线检测 + 重连 |
| `SSHEnv._setup_tmux` | `evomaster/env/ssh.py:288-338` | `matmaster/sessions/ssh.py:_setup_tmux` | tmux 初始化 |
| `SSHEnv.tmux_send_keys` | `evomaster/env/ssh.py:340-359` | `matmaster/sessions/ssh.py:_tmux_send_keys` | tmux 键入 |
| `SSHEnv.get_tmux_logs` | `evomaster/env/ssh.py:361-412` | `matmaster/sessions/ssh.py:_get_tmux_logs` | SFTP 读日志 + 超时回退 |
| `SSHEnv.ssh_exec` | `evomaster/env/ssh.py:225-264` | `matmaster/sessions/ssh.py:_ssh_exec` | 非 tmux 单命令执行 |
| `SSHEnv.read_file_content` | `evomaster/env/ssh.py:570-582` | `matmaster/sessions/ssh.py:read_file` | SFTP 读文件 |
| `SSHEnv.write_file_content` | `evomaster/env/ssh.py:584-597` | `matmaster/sessions/ssh.py:write_file` | SFTP 写文件 |
| `SSHEnv.path_exists` | `evomaster/env/ssh.py:599-607` | `matmaster/sessions/ssh.py:path_exists` | SFTP stat |
| `SSHEnv.is_file` | `evomaster/env/ssh.py:609-618` | `matmaster/sessions/ssh.py:is_file` | SFTP stat mode |
| `SSHEnv.upload_file` | `evomaster/env/ssh.py:422-435` | `matmaster/sessions/ssh.py:upload_file` | 单文件上传 |
| `SSHEnv.upload_directory_tarball` | `evomaster/env/ssh.py:489-553` | `matmaster/sessions/ssh.py:upload_directory_tarball` | 目录上传（tarball 打包） |

**不需要迁移的组件：**
- `SSHEnv.ssh_bash_noninteractive` -- 仅被 Skill 脚本执行调用，当前由 `script_env.py` 通过 session 引用直接调用 `_env` 方法。Phase 25 scope 中不改 `script_env.py`（属于 Phase 28 INVR 范围），但 SSHSession 应暴露此方法以保持兼容
- `SSHEnv.download_file` -- BuiltinTool 不使用 download，但 BaseSession 定义了，保留以保持 evomaster 兼容性。Protocol 不要求此方法
- `SSHEnv.upload_directory` (per-file SFTP) -- 有 tarball 版本，per-file 版本是 fallback。迁移 tarball 版本即可

### Pattern 4: Mixin 内联 (D-11, D-12)

**What:** `PlaygroundSessionMixin` 的 3 个方法内联到 Playground 类。

**PlaygroundSessionMixin 方法分析：**

| Method | Lines | Used By | Action |
|--------|-------|---------|--------|
| `attach_session(session)` | 24-51 | `bohrium_setup.py`(间接), `agent_run_bohrium.py`(直接设 pg.session) | 内联到 Playground，改用 matmaster Session Protocol |
| `attach_ssh_session(host, ...)` | 53-91 | 未被直接调用（`agent_run_bohrium.py` 直接构造 SSHSession 并赋值 `pg.session`） | 保留为便捷方法，但不再是 Mixin |
| `detach_session()` | 94-111 | `agent_run_bohrium.py` cleanup 路径 | 内联到 Playground |
| `sync_skills_to_remote(...)` | 114-158 | `_sync_skills_to_ssh_session` in `agent_run_bohrium.py` | 不迁移。当前 `agent_run_bohrium.py` 已有独立的 skill sync 逻辑 |

**关键发现：** `agent_run_bohrium.py` 不调用 `attach_ssh_session`，而是直接操作 `pg.session` 属性：
```python
pg.session = ssh_session
pg._owns_session = False
```
这意味着 Playground 需要保持 `session` 和 `_owns_session` 属性可写。内联后的 `attach_session` 方法仍然有用（封装了 close-old + open-new + propagate-to-agent 的逻辑），但 `agent_run_bohrium.py` 的直接赋值模式也需要继续工作。

### Pattern 5: agent_run_service 解耦 (D-07)

**Current coupling points in agent_run_service.py:**

| Line | Usage | What It Accesses | Migration |
|------|-------|-----------------|-----------|
| 316 | `playground.config_path.parent / 'llm_config.yaml'` | config_path -> config 目录路径 | Service 层自行用 `matmaster_config/` 路径 |
| 319 | `playground.config.agents` | EvoMasterConfig.agents | Service 层自行读 config.yaml 的 agents 段 |

**Solution:** PlaygroundManager 已知 `_config_dir`（`matmaster_config/`）。在 `get_or_create()` 返回 Playground 时，可以额外提供 config_dir 给调用方。或者 `agent_run_service.py` 直接用 `_project_root / "matmaster_config"` 定位 LLM config。

### Anti-Patterns to Avoid

- **Anti-pattern: SessionConfig frozen=True.** CONTEXT 中 D-04 说 SessionConfig 用 frozen Pydantic model，但当前 `_sync_workspace_to_session_config` 需要修改 `cfg.workspace_path` 和 `cfg.working_dir`。Playground 重新创建 session 时需要传入正确的 workspace_path，而非创建后再改。解决方案：构造 session 时直接传入 workspace_path，不做 post-hoc sync。如果 config 必须 frozen，那么在需要改 workspace 时用 `config.model_copy(update=...)` 创建新 config。

- **Anti-pattern: 直接复制 evomaster 的 Session-Env 双层架构.** matmaster 不需要 BaseEnv 这个抽象层（没有 Docker 和 Kubernetes 调度）。SSHSession 应直接持有 paramiko client。

- **Anti-pattern: 在 Playground 中保留 config 代理.** `playground.config.agents` 不应该通过 Playground 访问。Service 层自行读配置。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SSH 连接管理 | 自写 socket + channel 管理 | paramiko 4.0.0（已安装） | SSH 协议复杂度极高，paramiko 是 Python SSH 事实标准 |
| tmux PS1 解析 | 自定义输出解析 | 直接迁移 `BashMetadata` + `PS1_PATTERN` | 已验证的 JSON PS1 机制，在 evomaster 中长期稳定运行 |
| YAML config 解析 | 自写 config 框架 | PyYAML `yaml.safe_load` + Pydantic | matmaster 已有此模式（`matmaster/config/loader.py`） |
| Session Config 验证 | 手动校验字段 | Pydantic BaseModel | matmaster 的标准 Config 模式 |

## Common Pitfalls

### Pitfall 1: SessionConfig frozen vs workspace_path mutation

**What goes wrong:** 当前 `_sync_workspace_to_session_config` 在 `prepare()` 中修改 session config 的 `workspace_path` 和 `working_dir`。如果 SessionConfig 设为 frozen=True（D-04），这段代码会抛 ValidationError。

**Why it happens:** Playground 在 `prepare()` 时才知道最终的 workspace path，但 session 是在此之前从 config 创建的。

**How to avoid:** 两种策略（二选一）：
1. SessionConfig 设 `frozen=False`（实用但不符合 matmaster 惯例）
2. 延迟创建 session：先解析 workspace_path，再用正确的参数构造 session。这是更干净的方案，也符合参数化构造的方向。

**Recommendation:** 采用策略 2 -- 在 `prepare()` 中先解析 workspace_path，然后用它作为参数创建 session。这样 SessionConfig 可以保持 frozen。

### Pitfall 2: BashTool dual-path isinstance check

**What goes wrong:** `BashTool.execute()` 第 76-79 行做 `isinstance(self._session, (_EvoLocal, _MatLocal))` 来决定走 async 路径还是 sync 路径。这个 isinstance check 会在 Phase 25 后 import evomaster LocalSession。

**Why it happens:** BashTool 属于 Phase 26 (TOOL-08) 的改造范围，不在 Phase 25 scope。但 Phase 25 的 Playground 改造会让所有 local session 都是 matmaster LocalSession，从而这个 isinstance 仍然能工作。

**How to avoid:** Phase 25 不需要改 BashTool。但需确认 matmaster LocalSession 已在 isinstance check 列表中（当前已有：line 77 imports `matmaster.sessions.local.LocalSession as _MatLocal`）。

### Pitfall 3: agent_run_bohrium.py 直接操作 pg.session

**What goes wrong:** `agent_run_bohrium.py:598` 直接赋值 `pg.session = ssh_session` 而非调用 `attach_session()`。Phase 25 改造 Playground 后，如果 `session` 变成 property 或有其他封装，这段代码会 break。

**Why it happens:** `agent_run_bohrium.py` 不在 Phase 25 scope（属于 Phase 28 INVR-01），但它是 Playground 的重要消费者。

**How to avoid:** Playground 必须继续允许直接赋值 `pg.session = ...` 和 `pg._owns_session = ...`。不要在 Phase 25 把这些改成 property 或加保护。保持属性直接可写。

### Pitfall 4: evomaster deprecated import in validate_startup

**What goes wrong:** `PlaygroundManager.validate_startup()` 第 374 行 `import evomaster` 用于检测 evomaster 是否安装并发 DeprecationWarning。Phase 25 后这段逻辑应删除，但如果测试环境仍安装 evomaster 会持续 warn。

**How to avoid:** 直接删除整个 try/except 块。evomaster 的存在与否不影响 matmaster 的运行。

### Pitfall 5: PlaygroundContext.session 字段类型

**What goes wrong:** `PlaygroundContext.session` 当前类型为 `Any`（with comment "EvoMaster BaseSession instance"）。改为 `Session` Protocol 类型后，evomaster 的 BaseSession 实例仍然能满足 Protocol（因为是 structural typing），但需要确认 Pydantic 的 `arbitrary_types_allowed=True` 对 Protocol 类型的支持。

**How to avoid:** Pydantic v2 已支持 `arbitrary_types_allowed=True` 配合 Protocol 类型。测试中验证：evomaster BaseSession 实例可以赋给 `Session` Protocol 字段。

## Code Examples

### Example 1: matmaster LocalSession 升级 (from existing code)

```python
# matmaster/sessions/local.py -- 需要添加的部分
class LocalSession:
    def __init__(
        self,
        workspace_path: Path | str,
        *,
        timeout: int = 300,
        encoding: str = "utf-8",
    ) -> None:
        self._workspace_path = Path(workspace_path)
        self._timeout = timeout
        self._encoding = encoding
        self._is_open: bool = False
        # 保持向后兼容：config 属性供 _sync_workspace_to_session_config 使用
        self.config = LocalSessionConfig(
            timeout=timeout,
            workspace_path=str(workspace_path),
            working_dir=str(workspace_path),
            encoding=encoding,
        )

    @property
    def is_open(self) -> bool:
        """Session open state. LocalSession is always usable, but tracks state for Protocol."""
        return self._is_open

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False
```

**Key changes from current:**
- 添加 `_is_open` 状态追踪（当前无，open/close 是 no-op）
- 添加 `is_open` property（Protocol 要求）
- 添加 `config` 属性（Playground 的 `_sync_workspace_to_session_config` 使用）
- 构造函数增加 `encoding` 参数（D-04）

### Example 2: Playground 参数化构造

```python
# matmaster/core/playground.py -- target structure
class Playground:
    def __init__(
        self,
        *,
        session_type: str = "local",
        session_config: dict[str, Any] | None = None,
        archival: WorkspaceArchivalConfig | None = None,
        workspace_base: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._session_type = session_type
        self._session_config = session_config or {}
        self._archival = archival
        self._workspace_base = workspace_base
        self._cache_dir = cache_dir
        self.logger = logging.getLogger(self.__class__.__name__)

        self.session: Session | None = None
        self.agent: Any = None
        self._owns_session: bool = False
        self._prepare_run_meta: dict[str, Any] | None = None
        self._log_file_handler: logging.FileHandler | None = None
        self._log_file_stream = None
```

### Example 3: PlaygroundManager YAML 解析

```python
# PlaygroundManager.get_or_create
def get_or_create(self, session_id: str) -> Playground:
    with self._lock:
        if session_id in self._playgrounds:
            return self._playgrounds[session_id]

        raw_config = self._load_raw_config()
        session_block = raw_config.get("session", {})
        session_type = session_block.get("type", "local")
        session_config = session_block.get(session_type, {})
        playground_block = raw_config.get("playground", {})

        pg = Playground(
            session_type=session_type,
            session_config=session_config,
            archival=self._build_archival(playground_block),
            workspace_base=raw_config.get("workspace"),
            cache_dir=playground_block.get("cache_dir"),
        )
        self._playgrounds[session_id] = pg
        return pg

def _load_raw_config(self) -> dict[str, Any]:
    """Load config.yaml as raw dict (no EvoMasterConfig)."""
    config_path = self._config_dir / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
```

### Example 4: tmux 辅助模块

```python
# matmaster/sessions/tmux.py -- PS1/BashMetadata 从 evomaster 迁移
import json
import re

PS1_BEGIN = "\n===PS1JSONBEGIN===\n"
PS1_END = "\n===PS1JSONEND===\n"
PS1_PATTERN = re.compile(
    f"{PS1_BEGIN.strip()}(.*?){PS1_END.strip()}",
    re.DOTALL | re.MULTILINE,
)

class BashMetadata:
    """Bash execution metadata parsed from PS1 JSON prompt."""

    def __init__(self, exit_code: int = -1, working_dir: str = "", pid: int = -1):
        self.exit_code = exit_code
        self.working_dir = working_dir
        self.pid = pid

    @classmethod
    def to_ps1_prompt(cls) -> str: ...  # same as evomaster
    @classmethod
    def from_json(cls, json_str: str) -> BashMetadata: ...  # same as evomaster
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| evomaster ConfigManager + EvoMasterConfig | matmaster 参数化构造 + raw YAML dict | Phase 25 | Playground 不再需要 evomaster 安装 |
| BaseSession ABC + Env 双层架构 | Session Protocol + 直接实现 | Phase 25 | Session 层级减少，SSHSession 直接持有 paramiko |
| PlaygroundSessionMixin 继承 | 方法内联到 Playground | Phase 25 | 消除 mixin 继承链 |
| Docker/Local/SSH 三种 session | Local + SSH 两种 | Phase 25 (D-09) | Docker 废弃 |

## Open Questions

1. **SSHSession.upload_directory_tarball 是否在 Phase 25 scope?**
   - What we know: `agent_run_bohrium.py:_sync_skills_to_ssh_session` 调用 `ssh_session._env.upload_directory_tarball()`。Phase 25 的 SSHSession 不再有 `_env`。
   - What's unclear: Phase 28 (INVR-01) 才处理 `agent_run_bohrium.py` 的依赖。Phase 25 需要确保 SSHSession 暴露 `upload_directory_tarball` 方法供外部调用。
   - Recommendation: SSHSession 应暴露 `upload_directory_tarball` 和 `ssh_exec`/`ssh_bash_noninteractive` 作为公开方法（非 Protocol 要求，但实际调用者需要），以保持 Phase 28 之前的兼容。

2. **PlaygroundContext.session 类型标注何时从 Any 改为 Session?**
   - What we know: D-02 和 D-03 定义了 Session Protocol。evomaster session 满足此 Protocol（structural typing）。
   - What's unclear: 是在 Phase 25 一起改还是等到 Phase 28/30 质量阶段。
   - Recommendation: Phase 25 改。Protocol 是 structural typing，不会 break 任何使用 evomaster session 的代码。可以用 `Session | None` 类型替换 `Any`。

3. **PlaygroundManager.validate_startup 的 agents key 检查是否保留?**
   - What we know: 当前检查 config.yaml 是否有 'agents' key。但 Phase 25 后 Playground 不再读 agents。
   - Recommendation: 保留检查但改为 warning（而非 error），因为 agents 段仍被 service 层使用。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | `pyproject.toml` (pytest section) |
| Quick run command | `uv run pytest tests/matmaster/ -x --tb=short -q` |
| Full suite command | `uv run pytest tests/matmaster/ --tb=short -q` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PLAY-01 | matmaster LocalSession 独立工作（无 evomaster 依赖） | unit | `uv run pytest tests/matmaster/sessions/test_local_session.py -x` | Wave 0 |
| PLAY-01 | LocalSession 满足 Session Protocol | unit | `uv run pytest tests/matmaster/types/test_session_protocol.py -x` | Wave 0 |
| PLAY-01 | BuiltinTool 通过 matmaster LocalSession 执行文件/命令操作 | integration | `uv run pytest tests/matmaster/tools/test_builtin_with_native_session.py -x` | Wave 0 |
| PLAY-02 | SSHSession 满足 Session Protocol | unit | `uv run pytest tests/matmaster/sessions/test_ssh_session.py -x` | Wave 0 |
| PLAY-02 | Playground._create_session_from_config 创建 local/ssh（不 import evomaster） | unit | `uv run pytest tests/matmaster/core/test_playground.py -x` | Exists (needs update) |
| PLAY-03 | Playground 参数化构造无 evomaster import | unit | `uv run pytest tests/matmaster/core/test_playground.py -x` | Exists (needs update) |
| PLAY-03 | PlaygroundManager 从 YAML 构造 Playground | unit | `uv run pytest tests/matmaster/core/test_playground_manager.py -x` | Wave 0 |
| PLAY-03 | playground.py 无 evomaster import（静态检查） | audit | `uv run python -c "import ast; ..."` or grep check | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/ tests/matmaster/sessions/ tests/matmaster/types/test_session_protocol.py -x --tb=short -q`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x --tb=short -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/types/test_session_protocol.py` -- Session Protocol conformance tests (LocalSession, SSHSession, mock)
- [ ] `tests/matmaster/sessions/test_local_session.py` -- upgraded LocalSession unit tests (is_open, config, exec_bash, file ops)
- [ ] `tests/matmaster/sessions/test_ssh_session.py` -- SSHSession unit tests (mocked paramiko)
- [ ] `tests/matmaster/tools/test_builtin_with_native_session.py` -- BashTool/ReadTool with matmaster LocalSession
- [ ] `tests/matmaster/core/test_playground_manager.py` -- PlaygroundManager YAML -> Playground 参数化
- [ ] `tests/matmaster/core/test_playground_no_evomaster.py` -- import audit: playground.py 无 evomaster import

## Sources

### Primary (HIGH confidence)
- Direct code reading of all canonical references listed in CONTEXT.md
  - `matmaster/core/playground.py` -- 7 evomaster imports identified and analyzed
  - `matmaster/sessions/local.py` -- current 5-method implementation
  - `matmaster/types/context.py` -- PlaygroundContext frozen model
  - `evomaster/agent/session/base.py` -- BaseSession ABC + SessionConfig
  - `evomaster/agent/session/ssh.py` -- SSHSession + SSHSessionConfig
  - `evomaster/env/ssh.py` -- SSHEnv full implementation (630 lines)
  - `evomaster/env/docker.py` -- PS1_PATTERN + BashMetadata
  - `evomaster/core/playground_session.py` -- PlaygroundSessionMixin (3 methods)
  - `evomaster/config.py` -- ConfigManager + EvoMasterConfig
- `src/services/agent_run_service.py` -- service layer consumption patterns
- `src/services/agent_run_bohrium.py` -- SSH session creation + attachment
- `matmaster/integration/bohrium_setup.py` -- BohriumSetupService wrapper
- `matmaster/devshell/runner.py` -- clean construction reference pattern
- `matmaster/tools/builtin/base.py` -- BuiltinTool session consumption
- `matmaster/tools/builtin/bash_tool.py` -- dual-path isinstance check

### Secondary (MEDIUM confidence)
- Existing test files verified with `uv run pytest` (17 tests passing)
- `matmaster_config/config.yaml` -- production config structure

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all dependencies already installed (paramiko 4.0.0, pydantic, PyYAML)
- Architecture: HIGH -- direct code reading, all coupling points enumerated
- Pitfalls: HIGH -- derived from actual code patterns and cross-referencing caller code

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (stable internal architecture, no external API changes)
