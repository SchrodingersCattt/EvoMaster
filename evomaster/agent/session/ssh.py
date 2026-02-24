"""SSH Session 实现

通过 SSH 连接远端容器执行命令和操作文件。
使用 tmux + PS1 提示符实现持久化 shell 状态跟踪（与 DockerSession 相同机制）。
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")

from pydantic import Field

from evomaster.env.ssh import SSHEnv, SSHEnvConfig
from evomaster.env.docker import PS1_PATTERN, BashMetadata

from .base import BaseSession, SessionConfig


class SSHSessionConfig(SessionConfig):
    """SSH Session 配置"""
    host: str = Field(description="远端主机 IP 或域名")
    port: int = Field(default=22, description="SSH 端口")
    username: str = Field(default="root", description="SSH 用户名")
    password: str | None = Field(default=None, description="SSH 密码")
    key_file: str | None = Field(default=None, description="SSH 私钥文件路径")
    key_data: str | None = Field(default=None, description="SSH 私钥内容（从环境变量注入）")
    passphrase: str | None = Field(default=None, description="私钥密码")
    working_dir: str = Field(default="/workspace", description="远端工作目录")
    connect_timeout: int = Field(default=10, description="SSH 连接超时（秒）")
    keepalive_interval: int = Field(default=30, description="心跳间隔（秒）")
    max_retries: int = Field(default=3, description="连接失败最大重试次数")

    def __repr_args__(self):
        """Hide sensitive fields from repr/logs."""
        for k, v in super().__repr_args__():
            if k in ("password", "key_data", "passphrase") and v is not None:
                yield k, "***"
            else:
                yield k, v


class SSHSession(BaseSession):
    """SSH Session 实现

    使用 SSH 连接远端容器，通过 tmux 提供持久 bash 环境。
    exec_bash 的 PS1 轮询逻辑与 DockerSession 完全一致。
    """

    def __init__(self, config: SSHSessionConfig | None = None):
        super().__init__(config)
        self.config: SSHSessionConfig = config or SSHSessionConfig(host="localhost")
        env_config = SSHEnvConfig(session_config=self.config)
        self._env = SSHEnv(env_config)
        self._last_ps1_count: int = 0
        self._prev_command_status: Literal["completed", "timeout"] = "completed"

    def open(self) -> None:
        """建立 SSH 连接并初始化 tmux shell。"""
        if self._is_open:
            self.logger.warning("Session already open")
            return

        if not self._env.is_ready:
            self._env.setup()

        logs = self._env.get_tmux_logs()
        matches = list(PS1_PATTERN.finditer(logs))
        self._last_ps1_count = len(matches)

        self._is_open = True
        self.logger.info("SSH session opened (%s:%s)", self.config.host, self.config.port)

    def close(self) -> None:
        """关闭 SSH 连接（不关闭远端容器）。"""
        if not self._is_open:
            return

        if self._env.is_ready:
            self._env.teardown()

        self._is_open = False
        self.logger.info("SSH session closed")

    # ------------------------------------------------------------------
    # exec_bash — same tmux+PS1 mechanism as DockerSession
    # ------------------------------------------------------------------

    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        is_input: bool = False,
    ) -> dict[str, Any]:
        """通过 tmux 执行 bash 命令（持久环境，状态保持）。"""
        if not self._is_open:
            raise RuntimeError("Session not open")

        timeout = timeout or self.config.timeout
        command = command.strip()

        # is_input mode
        if is_input:
            if self._prev_command_status == "completed":
                if command == "":
                    return {
                        "stdout": "ERROR: No previous running command to retrieve logs from.",
                        "stderr": "",
                        "exit_code": 1,
                    }
                else:
                    return {
                        "stdout": "ERROR: No previous running command to interact with.",
                        "stderr": "",
                        "exit_code": 1,
                    }

            if command.startswith("C-") and len(command) == 3:
                self._env.tmux_send_keys(command, enter=False)
            elif command == "":
                pass
            else:
                self._env.tmux_send_keys(command, enter=True)
        else:
            if self._prev_command_status != "completed" and command != "":
                return {
                    "stdout": "[Previous command is still running. Use is_input=true to interact.]",
                    "stderr": "",
                    "exit_code": 1,
                }

            if command != "":
                self._env.tmux_send_keys(command, enter=True)

        # Poll for completion
        start_time = time.time()
        poll_interval = 0.5
        self._prev_command_status = "timeout"

        while time.time() - start_time < timeout:
            logs = self._env.get_tmux_logs()
            matches = list(PS1_PATTERN.finditer(logs))
            ps1_count = len(matches)

            if ps1_count > self._last_ps1_count:
                self._prev_command_status = "completed"
                break

            time.sleep(poll_interval)

        # Parse output
        logs = self._env.get_tmux_logs()
        matches = list(PS1_PATTERN.finditer(logs))
        ps1_count = len(matches)

        output = ""
        exit_code = -1
        working_dir = ""

        if ps1_count > self._last_ps1_count:
            if self._last_ps1_count > 0:
                prev_match = matches[self._last_ps1_count - 1]
                curr_match = matches[ps1_count - 1]
                output = logs[prev_match.end():curr_match.start()]
            else:
                curr_match = matches[ps1_count - 1]
                output = logs[:curr_match.start()]

            try:
                metadata = BashMetadata.from_json(matches[-1].group(1))
                exit_code = metadata.exit_code
                working_dir = metadata.working_dir
            except Exception:
                pass

            self._last_ps1_count = ps1_count
        else:
            if self._last_ps1_count > 0 and matches:
                prev_match = matches[self._last_ps1_count - 1]
                output = logs[prev_match.end():]

        output = _ANSI_RE.sub("", output).strip()
        if command and output.startswith(command):
            output = output[len(command):].strip()

        result: dict[str, Any] = {
            "stdout": output,
            "stderr": "",
            "exit_code": exit_code,
            "working_dir": working_dir,
            "output": output,
        }

        if self._prev_command_status == "timeout":
            result["stdout"] += f"\n[Command timed out after {timeout}s]"
            result["exit_code"] = -1

        return result

    # ------------------------------------------------------------------
    # File operations — delegate to SSHEnv
    # ------------------------------------------------------------------

    def upload(self, local_path: str, remote_path: str) -> None:
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._env.upload_file(local_path, remote_path)

    def read_file(self, remote_path: str, encoding: str = "utf-8") -> str:
        if not self._is_open:
            raise RuntimeError("Session not open")
        return self._env.read_file_content(remote_path, encoding)

    def write_file(self, remote_path: str, content: str, encoding: str = "utf-8") -> None:
        if not self._is_open:
            raise RuntimeError("Session not open")
        self._env.write_file_content(remote_path, content, encoding)

    def download(self, remote_path: str, timeout: int | None = None) -> bytes:
        if not self._is_open:
            raise RuntimeError("Session not open")
        return self._env.download_file(remote_path, timeout)

    def path_exists(self, remote_path: str) -> bool:
        if not self._is_open:
            raise RuntimeError("Session not open")
        return self._env.path_exists(remote_path)

    def is_file(self, remote_path: str) -> bool:
        if not self._is_open:
            raise RuntimeError("Session not open")
        return self._env.is_file(remote_path)

    def is_directory(self, remote_path: str) -> bool:
        if not self._is_open:
            raise RuntimeError("Session not open")
        return self._env.is_directory(remote_path)
