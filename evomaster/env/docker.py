"""EvoMaster Docker 执行环境

基于 Docker 容器 + tmux 的隔离执行环境，供 DockerSession 使用。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

# Bash 提示符中嵌入的元数据 JSON，用于解析 exit_code 和 working_dir
# 格式示例: [{"exit_code":0,"working_dir":"/workspace"}]
PS1_PATTERN = re.compile(r"\[\{(\{[^]]+\})\}\]")


class BashMetadata:
    """从 PS1 捕获的 JSON 解析出的 bash 元数据。"""

    def __init__(self, exit_code: int = -1, working_dir: str = ""):
        self.exit_code = exit_code
        self.working_dir = working_dir or ""

    @classmethod
    def from_json(cls, s: str) -> BashMetadata:
        try:
            data = json.loads(s)
            return cls(
                exit_code=int(data.get("exit_code", -1)),
                working_dir=str(data.get("working_dir", "")),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls(exit_code=-1, working_dir="")


class DockerEnvConfig:
    """Docker 环境配置，包装 DockerSessionConfig 供 DockerEnv 使用。"""

    def __init__(self, session_config: Any):
        self.session_config = session_config
        self.image: str = getattr(session_config, "image", "python:3.11-slim")
        self.working_dir: str = getattr(
            session_config, "working_dir", "/workspace"
        )
        self.volumes: dict[str, str] = getattr(
            session_config, "volumes", {}
        ) or {}
        self.container_name: str | None = getattr(
            session_config, "container_name", None
        )
        self.auto_remove: bool = getattr(session_config, "auto_remove", True)
        self.use_existing_container: str | None = getattr(
            session_config, "use_existing_container", None
        )


class DockerEnv:
    """Docker 执行环境：启动容器、通过 tmux 执行命令、文件通过卷或 exec 操作。"""

    def __init__(self, config: DockerEnvConfig):
        self.config = config
        self._container = None
        self._ready = False
        self._client = None

    @property
    def is_ready(self) -> bool:
        return self._ready

    def _get_client(self):
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
            except Exception as e:
                raise RuntimeError(
                    "Docker 环境不可用，请安装 docker 并确保守护进程运行。"
                    f" 错误: {e}"
                ) from e
        return self._client

    def setup(self) -> None:
        """创建并启动容器，在容器内启动 tmux 与带元数据 PS1 的 shell。"""
        client = self._get_client()
        if self.config.use_existing_container:
            self._container = client.containers.get(
                self.config.use_existing_container
            )
            self._ready = True
            return
        # 使用 volumes 挂载；容器内安装 tmux 并设置 PS1
        # 使用 python 镜像时通常需 apt-get install tmux，这里用简化为：先跑起来，首次 exec 时再装
        host_config = None
        if self.config.volumes:
            try:
                host_config = client.api.create_host_config(
                    binds=[
                        f"{k}:{v}" for k, v in self.config.volumes.items()
                    ]
                )
            except Exception:
                pass
        create_kw: dict[str, Any] = {
            "image": self.config.image,
            "command": "sleep infinity",
            "detach": True,
            "working_dir": self.config.working_dir,
            "name": self.config.container_name,
        }
        if host_config is not None:
            create_kw["host_config"] = host_config
        self._container = client.containers.run(**create_kw)
        if not hasattr(self._container, "reload"):
            self._container = client.containers.get(self._container.id)
        self._ready = True

    def teardown(self) -> None:
        """停止并可选删除容器。"""
        if self._container is None:
            self._ready = False
            return
        try:
            self._container.stop(timeout=5)
            if self.config.auto_remove:
                self._container.remove()
        except Exception:
            pass
        self._container = None
        self._ready = False

    def get_tmux_logs(self) -> str:
        """获取容器内 tmux 当前 buffer 内容。若未使用 tmux 则返回空。"""
        if self._container is None:
            return ""
        try:
            out = self._container.exec_run(
                "tmux capture-pane -p -S -1000",
                workdir=self.config.working_dir,
            )
            if isinstance(out, tuple):
                return (out[1] or b"").decode("utf-8", errors="replace")
            return (out.output or b"").decode("utf-8", errors="replace")
        except Exception:
            return ""

    def tmux_send_keys(self, keys: str, enter: bool = True) -> None:
        """向 tmux 发送按键。"""
        if self._container is None:
            return
        cmd = f'tmux send-keys "{keys.replace(chr(34), chr(92)+chr(34))}"'
        if enter:
            cmd += " Enter"
        try:
            self._container.exec_run(
                ["sh", "-c", cmd],
                workdir=self.config.working_dir,
            )
        except Exception:
            pass

    def _resolve_in_container(self, remote_path: str) -> str:
        """将逻辑路径转为容器内路径。若在 volumes 中有挂载则用挂载路径。"""
        if remote_path.startswith(self.config.working_dir):
            return remote_path
        if remote_path.startswith("/"):
            return remote_path
        return f"{self.config.working_dir.rstrip('/')}/{remote_path.lstrip('/')}"

    def _host_path_for_volume(self, container_path: str) -> Path | None:
        """若 container_path 在某个挂载卷内，返回宿主机路径，否则 None。"""
        for host_path, mount_path in self.config.volumes.items():
            mount_path = mount_path.rstrip("/")
            if container_path == mount_path or container_path.startswith(
                mount_path + "/"
            ):
                rel = container_path[len(mount_path) :].lstrip("/")
                return Path(host_path) / rel
        return None

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """上传文件到容器；若目标在挂载卷则直接写宿主机。"""
        container_path = self._resolve_in_container(remote_path)
        host_path = self._host_path_for_volume(container_path)
        if host_path is not None:
            src = Path(local_path)
            if src.is_dir():
                if host_path.exists():
                    shutil.rmtree(host_path)
                shutil.copytree(src, host_path)
            else:
                host_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, host_path)
            return
        # 通过 put_archive 写入容器
        import tarfile
        import io
        src = Path(local_path)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(local_path, arcname=Path(remote_path).name)
        buf.seek(0)
        self._container.put_archive(
            path=str(Path(container_path).parent),
            data=buf.read(),
        )

    def read_file_content(self, remote_path: str, encoding: str = "utf-8") -> str:
        """读取容器内文件；若在挂载卷则读宿主机。"""
        container_path = self._resolve_in_container(remote_path)
        host_path = self._host_path_for_volume(container_path)
        if host_path is not None:
            return host_path.read_text(encoding=encoding)
        out = self._container.exec_run(
            ["cat", container_path],
            workdir=self.config.working_dir,
        )
        raw = out.output if hasattr(out, "output") else out[1]
        return (raw or b"").decode(encoding, errors="replace")

    def write_file_content(
        self, remote_path: str, content: str, encoding: str = "utf-8"
    ) -> None:
        """写入容器内文件；若在挂载卷则写宿主机。"""
        container_path = self._resolve_in_container(remote_path)
        host_path = self._host_path_for_volume(container_path)
        if host_path is not None:
            host_path.parent.mkdir(parents=True, exist_ok=True)
            host_path.write_text(content, encoding=encoding)
            return
        self._container.exec_run(
            ["sh", "-c", f"mkdir -p $(dirname '{container_path}')"],
            workdir=self.config.working_dir,
        )
        import base64
        b64 = base64.b64encode(content.encode(encoding)).decode("ascii")
        self._container.exec_run(
            ["sh", "-c", f"echo {b64} | base64 -d > '{container_path}'"],
            workdir=self.config.working_dir,
        )

    def download_file(self, remote_path: str, timeout: int | None = None) -> bytes:
        """从容器下载文件；若在挂载卷则从宿主机读。"""
        container_path = self._resolve_in_container(remote_path)
        host_path = self._host_path_for_volume(container_path)
        if host_path is not None:
            return host_path.read_bytes()
        out = self._container.exec_run(
            ["cat", container_path],
            workdir=self.config.working_dir,
        )
        raw = out.output if hasattr(out, "output") else out[1]
        return raw or b""

    def path_exists(self, remote_path: str) -> bool:
        """检查容器内路径是否存在。"""
        container_path = self._resolve_in_container(remote_path)
        host_path = self._host_path_for_volume(container_path)
        if host_path is not None:
            return host_path.exists()
        out = self._container.exec_run(
            ["test", "-e", container_path],
            workdir=self.config.working_dir,
        )
        code = out.exit_code if hasattr(out, "exit_code") else out[0]
        return code == 0

    def is_file(self, remote_path: str) -> bool:
        container_path = self._resolve_in_container(remote_path)
        host_path = self._host_path_for_volume(container_path)
        if host_path is not None:
            return host_path.is_file()
        out = self._container.exec_run(
            ["test", "-f", container_path],
            workdir=self.config.working_dir,
        )
        code = out.exit_code if hasattr(out, "exit_code") else out[0]
        return code == 0

    def is_directory(self, remote_path: str) -> bool:
        container_path = self._resolve_in_container(remote_path)
        host_path = self._host_path_for_volume(container_path)
        if host_path is not None:
            return host_path.is_dir()
        out = self._container.exec_run(
            ["test", "-d", container_path],
            workdir=self.config.working_dir,
        )
        code = out.exit_code if hasattr(out, "exit_code") else out[0]
        return code == 0
