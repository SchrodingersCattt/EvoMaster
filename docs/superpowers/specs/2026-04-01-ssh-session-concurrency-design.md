# SSHSession 并发改造设计

## 问题

SSHSession 存在两层串行化瓶颈，导致 Kernel 的并行工具调用在 SSH 场景下完全退化：

1. **tmux 单会话** — `_prev_command_status` 硬性拒绝并发命令。前一个命令未完成时，后续 `exec_bash` 调用直接返回错误。
2. **SFTP 全局锁** — 单 `_sftp_lock` (`threading.Lock`) 串行化所有文件 I/O（read_file、write_file、path_exists、is_file、upload_file）。
3. **跨瓶颈耦合** — `exec_bash` 的 0.5s 轮询通过 SFTP 读取 tmux log，与文件操作争同一把锁。长文件传输会延迟命令完成检测。

结果：`asyncio.gather` 派发的多个工具调用在 SSH 场景下退化为串行执行。

## 目标

- 解除长时间命令对后续 shell 操作的阻塞
- 使 Kernel 同轮并行工具调用真正并发执行
- 保留命令取消能力（stop_event → 终止远程进程）

## 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| Shell 状态持久性 | 放弃 | Bohrium/MonitorJob 不依赖；每条命令自行 `cd <workdir> &&` 前缀 |
| is_input 交互 | 移除 | Agent 命令应为非交互式；保留取消能力即可 |
| Session Protocol | 允许演进 | 仅移除 `is_input` 参数 |
| 架构拆分 | 不做 | Session 内部改造足够；exec_command 走 SSH channel 不碰 SFTP，锁竞争自然消失 |

## 方案：exec_command 多通道 + SFTP 池

### 1. Session Protocol 变更

`exec_bash` 签名移除 `is_input` 参数：

```python
# 之前
def exec_bash(self, command: str, timeout: int | None = None,
              is_input: bool = False,
              stop_event: threading.Event | Any | None = None) -> dict: ...

# 之后
def exec_bash(self, command: str, timeout: int | None = None,
              stop_event: threading.Event | Any | None = None) -> dict: ...
```

返回值不变：`{stdout, stderr, exit_code, working_dir, output}`

### 2. SSHSession 命令执行——exec_command 替代 tmux

每次 `exec_bash` 调用开一个独立的 paramiko SSH channel：

```python
def exec_bash(self, command, timeout=None, stop_event=None):
    self._ensure_connected()
    transport = self._client.get_transport()
    channel = transport.open_session()
    wrapped = f"bash -l -c {shlex.quote(f'cd {shlex.quote(self._workdir)} && {command}')}"
    channel.exec_command(wrapped)
    # 流式读取（见下方输出捕获）
```

**CWD 管理**：`self._workdir` 在 session 生命周期内固定（来自 `SSHSessionConfig.working_dir`），每条命令通过 `cd <workdir> && <command>` 前缀设置工作目录。

**Shell 环境**：命令通过 `bash -l -c "..."` 执行（login shell），确保 `/etc/profile` 和 `/etc/profile.d/` 被加载。HPC 容器镜像的关键环境变量（PATH、LD_LIBRARY_PATH、模块系统）依赖这些初始化脚本。

**输出捕获——流式读取避免 buffer 死锁**：

paramiko 的 SSH channel 有 64KB 窗口限制。如果先等 `exit_status_ready()` 再读 stdout，长输出命令会因 buffer 满而死锁。必须在等待退出状态的同时持续消费 buffer：

```python
stdout_chunks = []
stderr_chunks = []
deadline = time.monotonic() + timeout if timeout else None
while not channel.exit_status_ready():
    if channel.recv_ready():
        stdout_chunks.append(channel.recv(65536))
    if channel.recv_stderr_ready():
        stderr_chunks.append(channel.recv_stderr(65536))
    # 超时检查
    if deadline and time.monotonic() >= deadline:
        channel.close()
        return {"stdout": ..., "stderr": f"Command timed out after {timeout}s",
                "exit_code": -1, "working_dir": self._workdir, "output": ...}
    # stop_event 检查（见取消机制）
    is_set = getattr(stop_event, 'is_set', None)
    if callable(is_set) and is_set():
        channel.close()
        return {"stdout": ..., "stderr": "Command cancelled.",
                "exit_code": 130, "working_dir": self._workdir, "output": ...}
    time.sleep(0.05)
# drain 剩余数据
while channel.recv_ready():
    stdout_chunks.append(channel.recv(65536))
while channel.recv_stderr_ready():
    stderr_chunks.append(channel.recv_stderr(65536))
exit_code = channel.recv_exit_status()
```

**超时机制**：基于 `time.monotonic()` 的 deadline 计时。超时时 `channel.close()` 关闭 channel，返回 `exit_code=-1` 和超时提示。所有调用方（BashTool、GrepTool、GlobTool、ListDirTool）依赖 timeout 防止 worker 被长命令永久占住，此路径必须可靠。

**取消机制**：在同一读取循环内检查 `stop_event`，无需单独的 watcher 线程。

`stop_event` 兼容性：使用 `getattr(stop_event, 'is_set', None)` 检查，与现有代码模式一致（参见 `_lifecycle.py:104`）。

`channel.close()` 后 `recv_exit_status()` 返回 -1（paramiko 约定），代码路径需处理此情况返回 `exit_code=130`（取消）或 `exit_code=-1`（超时）。

**SIGHUP 局限性说明**：`channel.close()` 导致远程进程收到 SIGHUP，但部分 HPC 程序（LAMMPS、ABACUS）可能忽略 SIGHUP。如果实践中发现取消不可靠，后续可增强为通过独立 channel 发送 `kill -TERM`。当前阶段 SIGHUP 足够覆盖常见场景。

**并发能力**：删除 `_prev_command_status` 状态机，每次调用独立 channel，天然并发。

**删除的组件**：
- `_setup_tmux()` / `_tmux_send_keys()` / `_get_tmux_logs()` — tmux 机制
- `_prev_command_status` / `_last_ps1_count` — 状态机
- `PS1_PATTERN` / `BashMetadata` — PS1 marker 解析
- `ssh_bash_noninteractive()` — exec_command 本身即非交互式

### 3. SFTP 连接池——替代单实例 + 全局锁

```python
class SFTPPool:
    def __init__(self, transport: paramiko.Transport, max_size: int = 4):
        self._transport = transport
        self._max_size = max_size
        self._pool: collections.deque[paramiko.SFTPClient] = deque()
        self._created: int = 0  # 诊断/日志用途，不参与调度决策
        self._semaphore = threading.Semaphore(max_size)
        self._lock = threading.Lock()  # 仅保护 _pool 和 _created

    def acquire(self) -> paramiko.SFTPClient:
        self._semaphore.acquire()
        with self._lock:
            if self._pool:
                return self._pool.popleft()
            self._created += 1
        try:
            return self._transport.open_sftp_client()
        except Exception:
            self._semaphore.release()  # 创建失败时归还槽位，防止泄漏
            raise

    def release(self, sftp: paramiko.SFTPClient) -> None:
        # 检查连接是否存活；已断开的不放回池
        try:
            sftp.stat('.')
        except Exception:
            try:
                sftp.close()
            except Exception:
                pass
            with self._lock:
                self._created -= 1
            self._semaphore.release()
            return
        with self._lock:
            self._pool.append(sftp)
        self._semaphore.release()

    def close_all(self) -> None:
        with self._lock:
            while self._pool:
                try:
                    self._pool.popleft().close()
                except Exception:
                    pass
            self._created = 0
```

**设计要点**：
- `max_size=4`：SSH2 单 transport 并发 channel 软限制约 10，4 个 SFTP channel 覆盖典型并行度并留余量给 exec_command
- 懒创建：按需打开 SFTP channel，用完归还
- `_lock` 作用域极小：仅保护 deque 操作，不在 I/O 期间持锁
- 创建失败安全：`open_sftp_client()` 异常时 `semaphore.release()` 防止槽位泄漏
- 归还时健康检查：`sftp.stat('.')` 验证连接存活，已断开的直接关闭丢弃
- `_created` 计数仅用于诊断日志，不参与池调度决策

**Session 文件方法改造**（以 read_file 为例）：

```python
def read_file(self, path, encoding="utf-8"):
    sftp = self._sftp_pool.acquire()
    try:
        with sftp.open(path, "r") as f:
            raw = f.read()
        return raw.decode(encoding) if isinstance(raw, bytes) else raw
    finally:
        self._sftp_pool.release(sftp)
```

**重连处理**：`_ensure_connected()` 检测 transport 断开时，重建 SSH 连接后调用 `_sftp_pool.close_all()`，并用新 transport 重新初始化池。后续操作触发懒创建。并发开启后 `_ensure_connected()` 需要 session 级锁保护（`self._connect_lock: threading.Lock`），防止多线程同时检测断连时竞争重连导致双重连接和旧 transport 句柄泄漏。

**删除的组件**：
- `self._sftp` — 单 SFTP 实例
- `self._sftp_lock` — 全局锁

### 4. BashTool 及相关工具适配

**BashTool** (`bash_tool.py`)：
- `json_schema` 删除 `is_input` 字段
- `_execute` 中移除 `is_input` 解析和传递逻辑（涉及两处：LocalSession 路径和通用路径）
- 双路径逻辑不变（LocalSession → `asyncio.create_subprocess_exec`，其他 → `asyncio.to_thread`）

**其他传递 `is_input=False` 的工具**——移除关键字参数：
- `grep_tool.py:89` — `session.exec_bash(..., is_input=False, ...)` → 删除 `is_input=False`
- `glob_tool.py:82` — 同上
- `listdir_tool.py:41` — 同上

**测试文件**：
- `tests/matmaster/sessions/test_local.py:43-45` — 删除 `test_is_input_returns_error` 测试
- `tests/matmaster/tools/test_bash_tool.py:58-60, 141-149` — 删除 is_input 相关测试
- `tests/matmaster/types/test_session_protocol.py:44` — Protocol 一致性 mock 移除 `is_input` 参数

### 5. MonitorJobTool 迁移修复（顺带）

evomaster 时代的 duck-type 检查 `session._env` 在原生 SSHSession 上永远为 False，导致 SFTP 推送和远程日志读取为死代码。

**is_ssh 检测修复**——将 `session._env` 检查改为检测原生接口：

```python
# 之前
is_ssh = hasattr(session, '_env') and hasattr(getattr(session, '_env', None), 'upload_file')

# 之后
is_ssh = hasattr(session, 'upload_file') and callable(getattr(session, 'upload_file', None))
```

涉及位置（4 处 is_ssh 判断）：
- `_lifecycle.py:88`
- `_download.py:218`
- `_tool.py:129`
- `_logs.py:285`

**内部调用修复**——is_ssh 检测修复后，后续使用 `session._env.xxx` 的代码也必须同步改为原生接口：

- `_download.py` `_sftp_push_directory`：`session._env.upload_file()` → `session.upload_file()`
- `_logs.py` `_read_log_tail_remote`（line 256）：`session._env.read_file_content(log_path)` → `session.read_file(log_path)`
- `_logs.py` `_find_log_file_remote`（line 291）：`session._env.ssh_exec(...)` → `session.ssh_exec(...)`（原生 SSHSession 有此公共方法）。注意返回值适配：`ssh_exec()` 返回 `dict[str, Any]`（含 `stdout`/`stderr`/`exit_code`），而当前代码第 295 行对结果直接调用 `.strip()`。需改为 `result.get('stdout', '').strip()`

## 变更范围

| 文件 | 变更类型 |
|---|---|
| `matmaster/types/session.py` | Protocol 签名：`exec_bash` 移除 `is_input` |
| `matmaster/sessions/ssh.py` | 重写核心：tmux → exec_command，单 SFTP → SFTPPool，`_ensure_connected` 加锁 |
| `matmaster/sessions/local.py` | 小改：`exec_bash` 移除 `is_input` |
| `matmaster/tools/builtin/bash_tool.py` | schema 删 `is_input`，移除解析和传递逻辑 |
| `matmaster/tools/builtin/grep_tool.py` | 移除 `is_input=False` 关键字参数 |
| `matmaster/tools/builtin/glob_tool.py` | 移除 `is_input=False` 关键字参数 |
| `matmaster/tools/builtin/listdir_tool.py` | 移除 `is_input=False` 关键字参数 |
| `matmaster/tools/builtin/monitor_job/_lifecycle.py` | 修复：is_ssh 检测 |
| `matmaster/tools/builtin/monitor_job/_download.py` | 修复：is_ssh 检测 + `_sftp_push_directory` 内部调用 |
| `matmaster/tools/builtin/monitor_job/_tool.py` | 修复：is_ssh 检测 |
| `matmaster/tools/builtin/monitor_job/_logs.py` | 修复：is_ssh 检测 + 内部调用 + `ssh_exec` 返回值适配 |
| `tests/matmaster/sessions/test_local.py` | 删除 `test_is_input_returns_error` |
| `tests/matmaster/tools/test_bash_tool.py` | 删除 is_input 相关测试 |
| `tests/matmaster/types/test_session_protocol.py` | Protocol mock 移除 `is_input` |

## 不变部分

- Session Protocol 除 `is_input` 外的所有签名
- SessionConfig / LocalSessionConfig / SSHSessionConfig
- PlaygroundContext / Playground / PlaygroundManager
- Exp / AgentKernel / ToolRegistry
- EventRouter / MessageBus / SSEHandler
- BohriumSetupService / agent_run_bohrium.py（`ssh_exec()` 和 `upload_directory_tarball()` 保留为非 Protocol 公共方法，内部改用 exec_command / SFTPPool）
- ReadTool / WriteTool / EditTool（工具逻辑不变，通过 session 接口透明获得并发能力）
- SpawnTool（子 agent 共享父 session，改造后并发安全）

## 已知遗留问题（不在本次范围）

- `agent_run_bohrium.py` 的 `_sync_skills_to_ssh_session` 使用 `isinstance(ssh_session, SSHSession)` 检查 evomaster 的 SSHSession 类型，对原生 SSHSession 会返回 False 导致 skill 同步跳过。这是 evomaster 迁移的预存问题，需单独修复。
