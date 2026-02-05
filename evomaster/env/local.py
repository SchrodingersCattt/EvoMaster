"""EvoMaster 本地执行环境

在宿主机直接执行命令、读写文件，无需容器。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class LocalEnvConfig:
    """本地环境配置，包装 Session 配置供 LocalEnv 使用。"""

    def __init__(self, session_config: Any):
        self.session_config = session_config
        self.workspace_path: str = getattr(
            session_config, "workspace_path", None
        ) or getattr(session_config, "working_dir", "/workspace")
        self.timeout: int = getattr(session_config, "timeout", 300)
        self.encoding: str = getattr(session_config, "encoding", "utf-8")
        self.symlinks: dict[str, str] = getattr(
            session_config, "symlinks", {}
        ) or {}
        self.config_dir: str | None = getattr(
            session_config, "config_dir", None
        )


class LocalEnv:
    """本地执行环境：创建工作目录、执行命令、文件操作。"""

    def __init__(self, config: LocalEnvConfig):
        self.config = config
        self._workspace: Path = Path(config.workspace_path).resolve()
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def _resolve_remote(self, remote_path: str) -> Path:
        """将逻辑上的远程路径解析为宿主机绝对路径。"""
        p = Path(remote_path)
        if not p.is_absolute():
            return (self._workspace / p).resolve()
        # 容器风格绝对路径 /workspace/... 映射到本地 workspace
        if remote_path.startswith("/workspace"):
            rel = remote_path[len("/workspace") :].lstrip("/")
            return (self._workspace / rel).resolve()
        if str(p).startswith(str(self._workspace)):
            return p.resolve()
        return (self._workspace / remote_path.lstrip("/")).resolve()

    def setup(self) -> None:
        """创建 workspace 并应用 symlinks。"""
        self._workspace.mkdir(parents=True, exist_ok=True)
        config_dir = self.config.config_dir
        for src, dest in self.config.symlinks.items():
            src_path = Path(src)
            if not src_path.is_absolute() and config_dir:
                src_path = Path(config_dir) / src_path
            dest_path = self._workspace / dest if not dest.startswith("/") else self._workspace / dest.lstrip("/")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if dest_path.exists():
                dest_path.unlink()
            try:
                src_path = src_path.resolve()
                if src_path.exists():
                    dest_path.symlink_to(src_path)
            except OSError:
                pass
        self._ready = True

    def teardown(self) -> None:
        """清理（本地模式通常无需释放资源）。"""
        self._ready = False

    def local_exec(self, command: str, timeout: int | None = None) -> dict[str, Any]:
        """在 workspace 下执行 shell 命令。"""
        timeout = timeout or self.config.timeout
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=str(self._workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding=self.config.encoding,
                errors="replace",
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            output = (stdout + "\n" + stderr).strip() if stderr else stdout
            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": result.returncode,
                "output": output,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "output": "",
            }

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """将本地文件/目录复制到 workspace 内。"""
        src = Path(local_path).resolve()
        dest = self._resolve_remote(remote_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)

    def read_file_content(self, remote_path: str, encoding: str = "utf-8") -> str:
        """读取 workspace 内文件内容。"""
        path = self._resolve_remote(remote_path)
        return path.read_text(encoding=encoding)

    def write_file_content(
        self, remote_path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """写入内容到 workspace 内文件。"""
        path = self._resolve_remote(remote_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)

    def download_file(self, remote_path: str, timeout: int | None = None) -> bytes:
        """读取 workspace 内文件为字节。"""
        path = self._resolve_remote(remote_path)
        return path.read_bytes()

    def path_exists(self, remote_path: str) -> bool:
        """检查路径是否存在。"""
        return self._resolve_remote(remote_path).exists()

    def is_file(self, remote_path: str) -> bool:
        """检查路径是否为文件。"""
        return self._resolve_remote(remote_path).is_file()

    def is_directory(self, remote_path: str) -> bool:
        """检查路径是否为目录。"""
        return self._resolve_remote(remote_path).is_dir()
