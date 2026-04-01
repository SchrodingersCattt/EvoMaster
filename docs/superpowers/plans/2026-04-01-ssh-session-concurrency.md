# SSHSession 并发改造实现计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 SSHSession 从 tmux 单会话 + SFTP 全局锁改为 exec_command 多通道 + SFTP 连接池，使并行工具调用在 SSH 场景下真正并发执行。

**Architecture:** 每次 `exec_bash` 开独立 SSH channel（`exec_command`），文件操作走 `SFTPPool`（最多 4 个并发 SFTP channel）。两者使用不同资源，无共享锁。重连时通过世代隔离保证旧池操作安全完成。

**Tech Stack:** Python 3.10+, paramiko, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-04-01-ssh-session-concurrency-design.md`

---

## Chunk 1: Session Protocol + LocalSession is_input 移除

最小爆炸半径的基础变更。所有后续 chunk 依赖此变更。

### Task 1: Session Protocol 移除 is_input

**Files:**
- Modify: `matmaster/types/session.py:90-97`
- Test: `tests/matmaster/types/test_session_protocol.py`

- [ ] **Step 1: 更新 Protocol 签名**

`matmaster/types/session.py` 中 `exec_bash` 方法移除 `is_input` 参数：

```python
# 之前 (line 90-97):
def exec_bash(
    self,
    command: str,
    timeout: int | None = None,
    is_input: bool = False,
    stop_event: threading.Event | Any | None = None,
) -> dict[str, Any]: ...

# 之后:
def exec_bash(
    self,
    command: str,
    timeout: int | None = None,
    stop_event: threading.Event | Any | None = None,
) -> dict[str, Any]: ...
```

- [ ] **Step 2: 更新 Protocol 测试中的 FakeSession**

`tests/matmaster/types/test_session_protocol.py` line 44，`FakeSession.exec_bash` 签名同步移除 `is_input`：

```python
# 之前:
def exec_bash(self, command, timeout=None, is_input=False, stop_event=None): ...

# 之后:
def exec_bash(self, command, timeout=None, stop_event=None): ...
```

- [ ] **Step 3: 运行测试验证**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/types/test_session_protocol.py -v`
Expected: ALL PASS

- [ ] **Step 4: 提交**

```bash
git add matmaster/types/session.py tests/matmaster/types/test_session_protocol.py
git commit -m "refactor(session): remove is_input from Session Protocol"
```

### Task 2: LocalSession 移除 is_input

**Files:**
- Modify: `matmaster/sessions/local.py:44-89`
- Test: `tests/matmaster/sessions/test_local.py`

- [ ] **Step 1: 更新 LocalSession.exec_bash 签名和实现**

`matmaster/sessions/local.py` 中移除 `is_input` 参数及其处理逻辑：

```python
# 之前 (line 44-51):
def exec_bash(
    self,
    command: str,
    timeout: int | None = None,
    is_input: bool = False,
    stop_event: threading.Event | Any | None = None,
) -> dict[str, Any]:

# 之后:
def exec_bash(
    self,
    command: str,
    timeout: int | None = None,
    stop_event: threading.Event | Any | None = None,
) -> dict[str, Any]:
```

同时删除 is_input 相关的 early-return 逻辑（约 line 56-62 的 `if is_input:` 分支）。

- [ ] **Step 2: 删除 test_is_input_returns_error 测试**

`tests/matmaster/sessions/test_local.py` 删除 `test_is_input_returns_error` 方法（line 43-45）。

- [ ] **Step 3: 运行测试验证**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/sessions/test_local.py -v`
Expected: ALL PASS（比之前少 1 个测试）

- [ ] **Step 4: 提交**

```bash
git add matmaster/sessions/local.py tests/matmaster/sessions/test_local.py
git commit -m "refactor(local-session): remove is_input parameter and test"
```

---

## Chunk 2: SFTPPool 实现

独立新组件，不依赖 SSHSession 改造，可独立测试。

### Task 3: 实现 SFTPPool 类

**Files:**
- Create: `matmaster/sessions/sftp_pool.py`
- Create: `tests/matmaster/sessions/test_sftp_pool.py`

- [ ] **Step 1: 编写 SFTPPool 测试**

```python
# tests/matmaster/sessions/test_sftp_pool.py
"""Tests for SFTPPool — SFTP connection pool with semaphore-based concurrency control."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from matmaster.sessions.sftp_pool import SFTPPool


@pytest.fixture
def mock_transport():
    transport = MagicMock()
    # Each open_sftp_client() call returns a distinct mock
    transport.open_sftp_client.side_effect = lambda: MagicMock()
    return transport


class TestSFTPPoolAcquireRelease:
    def test_acquire_creates_new_client(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp = pool.acquire()
        assert sftp is not None
        mock_transport.open_sftp_client.assert_called_once()

    def test_release_and_reuse(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp = pool.acquire()
        sftp.stat.return_value = MagicMock()  # healthy
        pool.release(sftp)
        sftp2 = pool.acquire()
        assert sftp2 is sftp  # reused, not new
        assert mock_transport.open_sftp_client.call_count == 1

    def test_semaphore_limits_concurrency(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=1)
        sftp = pool.acquire()
        # Second acquire should block
        entered = threading.Event()
        acquired = threading.Event()
        def try_acquire():
            entered.set()  # 确认线程已启动
            pool.acquire()
            acquired.set()
        t = threading.Thread(target=try_acquire, daemon=True)
        t.start()
        entered.wait(timeout=1.0)  # 等线程进入 acquire
        import time; time.sleep(0.1)  # 让 semaphore.acquire 阻塞
        assert not acquired.is_set(), "Should block when pool exhausted"
        # Release first, now second should succeed
        sftp.stat.return_value = MagicMock()
        pool.release(sftp)
        t.join(timeout=2.0)
        assert acquired.is_set()

    def test_acquire_failure_releases_semaphore(self, mock_transport):
        mock_transport.open_sftp_client.side_effect = OSError("connection lost")
        pool = SFTPPool(mock_transport, max_size=1)
        with pytest.raises(OSError):
            pool.acquire()
        # Semaphore should be released; next acquire should not block
        mock_transport.open_sftp_client.side_effect = lambda: MagicMock()
        sftp = pool.acquire()  # should not hang
        assert sftp is not None


class TestSFTPPoolHealthCheck:
    def test_release_discards_dead_client(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp = pool.acquire()
        sftp.stat.side_effect = OSError("channel closed")
        pool.release(sftp)
        sftp.close.assert_called_once()
        # Next acquire should create a new client, not reuse dead one
        sftp2 = pool.acquire()
        assert sftp2 is not sftp
        assert mock_transport.open_sftp_client.call_count == 2


class TestSFTPPoolCloseAll:
    def test_close_all_clears_pool(self, mock_transport):
        pool = SFTPPool(mock_transport, max_size=2)
        sftp1 = pool.acquire()
        sftp2 = pool.acquire()
        sftp1.stat.return_value = MagicMock()
        sftp2.stat.return_value = MagicMock()
        pool.release(sftp1)
        pool.release(sftp2)
        pool.close_all()
        sftp1.close.assert_called_once()
        sftp2.close.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/sessions/test_sftp_pool.py -v`
Expected: FAIL（ImportError，模块不存在）

- [ ] **Step 3: 实现 SFTPPool**

```python
# matmaster/sessions/sftp_pool.py
"""SFTP connection pool with semaphore-based concurrency control.

Manages a bounded pool of paramiko SFTPClient instances on a single
SSH transport. Supports lazy creation, health-check on release, and
generation-safe close_all for reconnection scenarios.
"""
from __future__ import annotations

import collections
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None  # type: ignore[assignment]


class SFTPPool:
    """Bounded pool of SFTP clients sharing one SSH transport."""

    def __init__(self, transport: Any, max_size: int = 4) -> None:
        self._transport = transport
        self._max_size = max_size
        self._pool: collections.deque = collections.deque()
        self._created: int = 0
        self._semaphore = threading.Semaphore(max_size)
        self._lock = threading.Lock()

    def acquire(self) -> Any:
        """Acquire an SFTP client from the pool (blocking if exhausted)."""
        self._semaphore.acquire()
        with self._lock:
            if self._pool:
                return self._pool.popleft()
            self._created += 1
        try:
            client = self._transport.open_sftp_client()
            logger.debug("sftp_pool: created new client (total=%d)", self._created)
            return client
        except Exception:
            with self._lock:
                self._created -= 1
            self._semaphore.release()
            raise

    def release(self, sftp: Any) -> None:
        """Return an SFTP client to the pool (discards if unhealthy)."""
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
            logger.debug("sftp_pool: discarded dead client (total=%d)", self._created)
            return
        with self._lock:
            self._pool.append(sftp)
        self._semaphore.release()

    def close_all(self) -> None:
        """Close all pooled clients. In-flight clients are not affected."""
        with self._lock:
            while self._pool:
                try:
                    self._pool.popleft().close()
                except Exception:
                    pass
            self._created = 0
        logger.debug("sftp_pool: close_all completed")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/sessions/test_sftp_pool.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add matmaster/sessions/sftp_pool.py tests/matmaster/sessions/test_sftp_pool.py
git commit -m "feat(session): add SFTPPool with semaphore-based concurrency"
```

---

## Chunk 3: SSHSession 重写

核心改造：tmux → exec_command，单 SFTP → SFTPPool。

### Task 4: 重写 SSHSession.exec_bash

**Files:**
- Modify: `matmaster/sessions/ssh.py`
- Test: `tests/matmaster/sessions/test_ssh_session.py`

- [ ] **Step 1: 编写新 exec_bash 的测试**

在 `tests/matmaster/sessions/test_ssh_session.py` 中添加：

```python
class TestSSHSessionExecBash:
    """Tests for the new exec_command-based exec_bash."""

    def test_simple_command(self, ssh_config, mock_paramiko):
        """exec_bash returns stdout, exit_code from channel."""
        session = SSHSession(ssh_config)
        session.open()

        channel = MagicMock()
        recv_calls = [b"hello\n", b""]
        channel.recv_ready.side_effect = [True, False, False]
        channel.recv.side_effect = recv_calls
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.side_effect = [False, True]
        channel.recv_exit_status.return_value = 0
        mock_paramiko["transport"].open_session.return_value = channel

        result = session.exec_bash("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_timeout_returns_minus_one(self, ssh_config, mock_paramiko):
        """exec_bash returns exit_code=-1 on timeout."""
        session = SSHSession(ssh_config)
        session.open()

        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        mock_paramiko["transport"].open_session.return_value = channel

        result = session.exec_bash("sleep 999", timeout=0)
        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"].lower() or "timed out" in result["stdout"].lower()

    def test_stop_event_cancels(self, ssh_config, mock_paramiko):
        """exec_bash returns exit_code=130 when stop_event is set."""
        import threading
        session = SSHSession(ssh_config)
        session.open()

        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        mock_paramiko["transport"].open_session.return_value = channel

        stop = threading.Event()
        stop.set()
        result = session.exec_bash("sleep 999", stop_event=stop)
        assert result["exit_code"] == 130

    def test_concurrent_exec_bash(self, ssh_config, mock_paramiko):
        """Multiple exec_bash calls run concurrently (no _prev_command_status block)."""
        import concurrent.futures
        session = SSHSession(ssh_config)
        session.open()

        def make_channel():
            ch = MagicMock()
            ch.recv_ready.side_effect = [True, False, False]
            ch.recv.side_effect = [b"ok\n", b""]
            ch.recv_stderr_ready.return_value = False
            ch.exit_status_ready.side_effect = [False, True]
            ch.recv_exit_status.return_value = 0
            return ch

        mock_paramiko["transport"].open_session.side_effect = [make_channel(), make_channel()]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(session.exec_bash, "cmd1")
            f2 = ex.submit(session.exec_bash, "cmd2")
            r1, r2 = f1.result(timeout=5), f2.result(timeout=5)
        assert r1["exit_code"] == 0
        assert r2["exit_code"] == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/sessions/test_ssh_session.py::TestSSHSessionExecBash -v`
Expected: FAIL（测试引用新行为，旧实现不兼容）

- [ ] **Step 3: 重写 SSHSession**

在 `matmaster/sessions/ssh.py` 中进行以下改造（参照 spec 伪代码）：

**3a) Import 和属性变更**：
- 添加：`import shlex`（exec_command 包装用），`from matmaster.sessions.sftp_pool import SFTPPool`
- 删除：`from matmaster.sessions.tmux import PS1_PATTERN, BashMetadata`（line 24 附近）
- `__init__` 中：添加 `self._connect_lock = threading.Lock()`，`self._sftp_pool: SFTPPool | None = None`
- `__init__` 中：删除 `self._sftp`、`self._sftp_lock`、`self._tmux_session_name`、`self._tmux_log_path`、`self._last_ps1_count`、`self._prev_command_status`

**3b) 重写 `open()`**：
- 调用 `self._connect()` 建立 SSH 连接
- 初始化：`self._sftp_pool = SFTPPool(self._client.get_transport())`
- 通过 `_ssh_exec(f"mkdir -p {shlex.quote(self._workdir)}")` 确保远程 workdir 存在
- 不再调用 `_setup_tmux()` 和 `_open_sftp()`

**3c) 重写 `exec_bash()`**：
```python
def exec_bash(self, command, timeout=None, stop_event=None):
    self._ensure_connected()
    timeout = timeout or self.config.timeout
    transport = self._client.get_transport()
    channel = transport.open_session()
    wrapped = f"bash -l -c {shlex.quote(f'cd {shlex.quote(self._workdir)} && {command}')}"
    channel.exec_command(wrapped)
    # 流式读取 + deadline 超时 + stop_event 取消
    # （完整伪代码见 spec Section 2）
    # 返回 {stdout, stderr, exit_code, working_dir, output}
```

**3d) 重写文件方法**（read_file、write_file、path_exists、is_file）：
必须用局部引用防止重连期间池被替换：
```python
def read_file(self, path, encoding="utf-8"):
    self._ensure_connected()
    pool = self._sftp_pool  # 局部引用
    sftp = pool.acquire()
    try:
        with sftp.open(path, "r") as f:
            raw = f.read()
        return raw.decode(encoding) if isinstance(raw, bytes) else raw
    finally:
        pool.release(sftp)
```

**3e) 重写 `_ensure_connected()`**（double-check + connect_lock）：
```python
def _ensure_connected(self):
    if self._client and self._client.get_transport() and self._client.get_transport().is_active():
        return
    with self._connect_lock:
        # double-check：可能另一个线程已经重连
        if self._client and self._client.get_transport() and self._client.get_transport().is_active():
            return
        self._connect()
        old_pool = self._sftp_pool
        if old_pool:
            old_pool.close_all()
        self._sftp_pool = SFTPPool(self._client.get_transport())
```

**3f) 更新 `upload_file` 和 `upload_directory_tarball`**：
用 `pool = self._sftp_pool; sftp = pool.acquire()` ... `pool.release(sftp)` 替换 `self._sftp_lock` + `self._sftp.put()`。`upload_directory_tarball` 在 tarball sftp.put 时 acquire/release，解压仍走 `_ssh_exec`。

**3g) 重写 `close()`**：
- 调用 `self._sftp_pool.close_all()` 替代 `self._sftp.close()`
- 需要 guard：`if self._sftp_pool is not None`（防止 open 未调用时 close 报错）

**3h) 删除 tmux 相关代码**：
- 方法：`_setup_tmux`、`_tmux_send_keys`、`_get_tmux_logs`、`ssh_bash_noninteractive`
- 属性/常量：`_prev_command_status`、`_last_ps1_count`、`_tmux_session_name`、`_tmux_log_path`
- 注意：`matmaster/sessions/tmux.py` 文件本身保留（可能被 evomaster 引用），只是 ssh.py 不再 import 它

- [ ] **Step 4: 更新已有 SSH 测试**

`tests/matmaster/sessions/test_ssh_session.py`：

**mock_paramiko fixture 改造**：
- 移除：tmux 相关的 mock（`exec_command` for tmux setup、log file mock 等）
- 添加：`transport.open_session.return_value = channel_mock`（给 exec_bash 用）
- 改造：`client.open_sftp()` → `transport.open_sftp_client()`（给 SFTPPool 用），因为 SFTPPool 调用的是 `transport.open_sftp_client()` 不是 `client.open_sftp()`

**TestSSHSessionFileOps 改造**：
每个文件操作测试需要确保 mock transport 的 `open_sftp_client()` 返回 mock sftp，且 mock sftp 的 `stat('.')` 返回成功（SFTPPool health check）。

**TestSSHSessionProtocol** 和 **TestSSHSessionNotOpen**：
签名适配（exec_bash 不再有 is_input）。

**tmux 相关测试处理**：
`tests/matmaster/types/test_session_protocol.py` 中的 `TestBashMetadata` 和 `TestPS1Pattern` 类保留不动——它们测试的是 `matmaster/sessions/tmux.py` 模块，该文件未被删除。

- [ ] **Step 5: 运行全部 SSH 测试**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/sessions/test_ssh_session.py -v`
Expected: ALL PASS

- [ ] **Step 6: 同时运行 Protocol 测试确认兼容**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/types/test_session_protocol.py -v`
Expected: ALL PASS

- [ ] **Step 7: 提交**

```bash
git add matmaster/sessions/ssh.py tests/matmaster/sessions/test_ssh_session.py
git commit -m "feat(ssh-session): replace tmux with exec_command + SFTPPool"
```

---

## Chunk 4: 工具层 is_input 清理

### Task 5: BashTool 移除 is_input

**Files:**
- Modify: `matmaster/tools/builtin/bash_tool.py:91-245`
- Test: `tests/matmaster/tools/test_bash_tool.py`

- [ ] **Step 0: 运行基线测试确认当前状态**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_bash_tool.py -v`
Expected: ALL PASS（基线确认）

- [ ] **Step 1: 删除 BashTool 中的 is_input 逻辑**

`matmaster/tools/builtin/bash_tool.py`：

1. `json_schema`（line 113-118）删除 `is_input` 字段定义
2. `_execute_async`（line 154-155）删除 `is_input` 解析（`is_input_str = arguments.get('is_input', 'false')` 和 `is_input = is_input_str == 'true'`）以及 line 160 的 `if is_input:` 分支
3. `_execute`（line 214-215）同样删除 `is_input` 解析和 line 231 的 `is_input=is_input` 传递给 `session.exec_bash`
4. 更新文件头注释（line 4）移除 `is_input mode` 描述

- [ ] **Step 2: 删除 is_input 相关测试**

`tests/matmaster/tools/test_bash_tool.py`：
- 删除 `test_is_input_true_no_proxy_prefix` 方法（按名称查找，整个方法体）
- 删除 `test_is_input` 方法（按名称查找，整个方法体）
- 更新 `mock_session` fixture 的 `exec_bash` mock 签名（如果有 `is_input` 参数）

- [ ] **Step 3: 运行测试**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_bash_tool.py -v`
Expected: ALL PASS

- [ ] **Step 4: 提交**

```bash
git add matmaster/tools/builtin/bash_tool.py tests/matmaster/tools/test_bash_tool.py
git commit -m "refactor(bash-tool): remove is_input schema and logic"
```

### Task 6: GrepTool / GlobTool / ListDirTool 移除 is_input

**Files:**
- Modify: `matmaster/tools/builtin/grep_tool.py:89`
- Modify: `matmaster/tools/builtin/glob_tool.py:82`
- Modify: `matmaster/tools/builtin/listdir_tool.py:41`

- [ ] **Step 1: 逐文件移除 is_input=False**

每个文件中找到 `session.exec_bash(...)` 调用，删除 `is_input=False` 关键字参数：

`grep_tool.py:89`:
```python
# 之前:
result = self._session.exec_bash(cmd, timeout=30, is_input=False, stop_event=...)
# 之后:
result = self._session.exec_bash(cmd, timeout=30, stop_event=...)
```

`glob_tool.py:82` 和 `listdir_tool.py:41` 同样处理。

- [ ] **Step 2: 运行相关测试**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/ -v -k "not test_monitor"`
Expected: ALL PASS

- [ ] **Step 3: 提交**

```bash
git add matmaster/tools/builtin/grep_tool.py matmaster/tools/builtin/glob_tool.py matmaster/tools/builtin/listdir_tool.py
git commit -m "refactor(tools): remove is_input=False from grep/glob/listdir"
```

---

## Chunk 5: MonitorJobTool 迁移修复

### Task 7: 修复 is_ssh 检测和内部调用

**Files:**
- Modify: `matmaster/tools/builtin/monitor_job/_lifecycle.py:88`
- Modify: `matmaster/tools/builtin/monitor_job/_download.py:218,221`
- Modify: `matmaster/tools/builtin/monitor_job/_tool.py:129`
- Modify: `matmaster/tools/builtin/monitor_job/_logs.py:256,285,291,295`

- [ ] **Step 1: 修复 4 处 is_ssh 检测**

所有文件中将：
```python
is_ssh = hasattr(session, '_env') and hasattr(getattr(session, '_env', None), 'upload_file')
```
改为：
```python
is_ssh = hasattr(session, 'upload_file') and callable(getattr(session, 'upload_file', None))
```

涉及：`_lifecycle.py:88`、`_download.py:218`、`_tool.py:129`、`_logs.py:285`

- [ ] **Step 2: 修复 _sftp_push_directory 内部调用**

`_download.py` `_sftp_push_directory`（line 221 起）：
```python
# 之前:
env = session._env
...
env.upload_file(str(local_file), remote_path)

# 之后（删除 env 中间变量，直接用 session）:
session.upload_file(str(local_file), remote_path)
```

- [ ] **Step 3: 修复 _read_log_tail_remote**

`_logs.py` line 256：
```python
# 之前:
content = session._env.read_file_content(log_path)

# 之后:
content = session.read_file(log_path)
```

- [ ] **Step 4: 修复 _find_log_file_remote + 返回值适配**

`_logs.py` line 291-295：
```python
# 之前:
result = session._env.ssh_exec(
    f"find {workspace!r} -name {pat!r} -type f 2>/dev/null "
    f"| xargs ls -t 2>/dev/null | head -1"
)
path = (result or '').strip()

# 之后:
result = session.ssh_exec(
    f"find {workspace!r} -name {pat!r} -type f 2>/dev/null "
    f"| xargs ls -t 2>/dev/null | head -1"
)
path = result.get('stdout', '').strip()
```

- [ ] **Step 5: 运行 monitor_job 测试（如有）**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/ -v -k "monitor" 2>/dev/null || echo "No monitor tests found"`

- [ ] **Step 6: 运行全量工具测试**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/ -v`
Expected: ALL PASS

- [ ] **Step 7: 提交**

```bash
git add matmaster/tools/builtin/monitor_job/
git commit -m "fix(monitor-job): replace evomaster session._env with native SSHSession interface"
```

---

## Chunk 6: 集成验证

### Task 8: 全量测试 + 交叉验证

- [ ] **Step 1: 运行 matmaster 全量测试**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: 运行 evomaster 测试确认无回归**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/evomaster/ -v --tb=short -x`
Expected: ALL PASS（evomaster 测试使用自己的 SSHSession，不受影响）

- [ ] **Step 3: 检查 is_input 残留**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && grep -rn "is_input" matmaster/ tests/matmaster/ --include="*.py" | grep -v "__pycache__"`
Expected: 无匹配（evomaster 目录下的可以忽略）

- [ ] **Step 4: 检查 session._env 残留**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && grep -rn "session\._env" matmaster/tools/builtin/monitor_job/ --include="*.py"`
Expected: 无匹配

- [ ] **Step 5: 提交（如有修复）或确认完成**

如果前面步骤发现问题，修复后提交。否则确认所有 chunk 完成。
