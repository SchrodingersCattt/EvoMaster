"""构建版本信息：从环境变量解析当前服务版本，供运行时埋点使用。"""

from pathlib import Path


def _resolve_build_version() -> str:
    """
    返回构建版本：
    - 如仓库根目录存在 .build-version，则读取其内容。
    - 否则回退为 'dev'。
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    path = project_root / '.build-version'
    if path.exists():
        try:
            content = path.read_text(encoding='utf-8').strip()
        except OSError:
            content = ''
        if content:
            return content
    return 'dev'


_BUILD_VERSION = _resolve_build_version()


def get_build_version() -> str:
    """返回当前进程解析到的构建版本。"""
    return _BUILD_VERSION
