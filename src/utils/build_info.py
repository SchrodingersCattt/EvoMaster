"""构建版本信息：从 .build-version 解析当前服务版本与构建序号。

.build-version 格式: "<commit_hash>:<pipeline_id>" 或旧格式 "<commit_hash>"。
"""

from pathlib import Path


def _resolve_build_version() -> tuple[str, int]:
    """返回 (版本字符串, build_seq)。

    新格式 "hash:pipeline_id" → (hash, pipeline_id)。
    旧格式仅 hash → (hash, 0)。
    文件不存在 → ('dev', 0)。
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    path = project_root / '.build-version'
    if path.exists():
        try:
            content = path.read_text(encoding='utf-8').strip()
        except OSError:
            content = ''
        if content:
            if ':' in content:
                parts = content.split(':', 1)
                try:
                    return parts[0], int(parts[1])
                except (ValueError, IndexError):
                    return content, 0
            return content, 0
    return 'dev', 0


_BUILD_VERSION, _BUILD_SEQ = _resolve_build_version()


def get_build_version() -> str:
    """返回当前进程解析到的构建版本（commit hash）。"""
    return _BUILD_VERSION


def get_build_seq() -> int:
    """返回构建序号（CI pipeline ID），0 表示未知。"""
    return _BUILD_SEQ
