"""OSS 上传/下载（阿里云 oss2）。

支持单文件上传、目录递归上传。使用环境变量：
OSS_ENDPOINT, OSS_BUCKET_NAME, OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET。
依赖：pip install oss2
"""

import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import oss2

logger = logging.getLogger(__name__)


def _get_bucket():
    """从环境变量创建 oss2 Bucket。"""
    try:
        import oss2
        from oss2.credentials import EnvironmentVariableCredentialsProvider
    except ImportError as e:
        raise ImportError('OSS 上传需要 oss2，请执行: pip install oss2') from e

    endpoint = (os.getenv('OSS_ENDPOINT') or '').strip()
    bucket_name = (os.getenv('OSS_BUCKET_NAME') or '').strip()
    if not endpoint or not bucket_name:
        raise RuntimeError(
            'OSS 上传需要设置环境变量 OSS_ENDPOINT 和 OSS_BUCKET_NAME；'
            '并设置 OSS_ACCESS_KEY_ID、OSS_ACCESS_KEY_SECRET'
        )
    auth = oss2.ProviderAuth(EnvironmentVariableCredentialsProvider())
    return oss2.Bucket(auth, endpoint, bucket_name), endpoint, bucket_name


def _oss_key_to_url(bucket_name: str, endpoint: str, key: str) -> str:
    """将 OSS object key 转为公网可访问的 URL。"""
    key = key.lstrip('/')
    host = endpoint.replace('https://', '').replace('http://', '').split('/')[0]
    return f"https://{bucket_name}.{host}/{key}"


def _key_from_url(url: str, bucket_name: str) -> str:
    """从完整 OSS URL 解析出 object key。"""
    parsed = urlparse(url)
    path = (parsed.path or '').lstrip('/')
    # 若 path 以 bucket_name 开头（某些 endpoint 形式），去掉
    if path.startswith(bucket_name + '/'):
        path = path[len(bucket_name) + 1 :]
    return path


def get_signed_url(
    oss_key_or_url: str,
    expire_seconds: int = 3600,
) -> str:
    """生成私有 Bucket 下对象的临时签名 URL，供前端直接访问。

    Args:
        oss_key_or_url: OSS 对象 key（如 matmaster_evo/chat_workspace/xxx/file.txt）
                        或已生成的完整 OSS URL（会解析出 key 再签名）
        expire_seconds: 签名有效时长（秒），默认 1 小时

    Returns:
        带签名的 HTTPS URL，在有效期内可直接访问
    """
    bucket, endpoint, bucket_name = _get_bucket()
    s = oss_key_or_url.strip()
    if s.startswith('http://') or s.startswith('https://'):
        key = _key_from_url(s, bucket_name)
    else:
        key = s.lstrip('/')
    if not key:
        raise ValueError('无法从 oss_key_or_url 解析出有效 key')
    expires = int(time.time()) + expire_seconds
    signed = bucket.sign_url('GET', key, expires)
    return signed


def get_object_bytes(oss_key: str) -> bytes:
    """按 OSS key 读取对象内容，返回字节。"""
    bucket, _e, _b = _get_bucket()
    key = oss_key.strip().lstrip('/')
    if not key:
        raise ValueError('oss_key 为空')
    result = bucket.get_object(key)
    return result.read()


def upload_file_to_oss(
    local_path: Path | str,
    key_prefix: str = 'evomaster/calculation',
) -> str:
    """将本地单文件上传到 OSS，返回该文件的公网 URL。

    Args:
        local_path: 本地文件路径
        key_prefix: OSS 对象 key 的前缀，文件名会带时间戳拼在其后

    Returns:
        上传后文件的 HTTPS URL
    """
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"不是文件或不存在: {path}")

    bucket, endpoint, bucket_name = _get_bucket()
    filename = path.name
    oss_key = f"{key_prefix.rstrip('/')}/{int(time.time())}_{filename}"

    with path.open('rb') as f:
        bucket.put_object(oss_key, f.read())

    url = _oss_key_to_url(bucket_name, endpoint, oss_key)
    logger.debug('OSS 上传文件 %s -> %s', path, url)
    return url


def upload_dir_to_oss(
    local_dir: Path | str,
    key_prefix: str = 'evomaster/calculation',
) -> tuple[list[str], list[str]]:
    """将本地目录下所有文件按相对路径递归上传到 OSS，保持目录层级。

    Args:
        local_dir: 本地目录路径
        key_prefix: OSS 对象 key 的前缀，目录内相对路径会拼在其后

    Returns:
        (urls, rel_paths): 各文件的完整 OSS URL 列表、以及相对目录的路径列表（一一对应）
    """
    root = Path(local_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"不是目录或不存在: {root}")

    bucket, endpoint, bucket_name = _get_bucket()
    prefix = key_prefix.rstrip('/')
    dir_name = root.name
    urls: list[str] = []
    rel_paths: list[str] = []

    for path in root.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        rel_posix = rel.as_posix()
        oss_key = f"{prefix}/{dir_name}/{rel_posix}"

        with path.open('rb') as f:
            bucket.put_object(oss_key, f.read())

        url = _oss_key_to_url(bucket_name, endpoint, oss_key)
        urls.append(url)
        rel_paths.append(rel_posix)

    logger.info(
        'OSS 上传目录 %s -> %d 个文件，key_prefix=%s/%s',
        root,
        len(urls),
        prefix,
        dir_name,
    )
    return urls, rel_paths


def list_workspace(
    oss_prefix: str,
    path: str = '',
) -> list[dict[str, str]]:
    """列出 OSS 上某 workspace 前缀下、指定 path 的一级子目录与文件（模拟目录列表）。

    Args:
        oss_prefix: OSS key 前缀，如 matmaster_evo/chat_workspace/{session_id}/{task_id}
        path: 相对 oss_prefix 的子路径，空字符串表示根。如 "" 或 "data" 或 "data/out"

    Returns:
        entries: [{"name": "...", "path": "...", "type": "directory"|"file"}, ...]
        - type="directory": path 用于下次 list 的 path 参数
        - type="file": path 为相对 workspace 根的路径，用于拼 download_url
    """

    bucket, _e, _b = _get_bucket()
    base = oss_prefix.rstrip('/') + '/'
    sub = (path.strip().rstrip('/') + '/') if path.strip() else ''
    full_prefix = base + sub

    entries: list[dict[str, str]] = []

    it = oss2.ObjectIterator(bucket, prefix=full_prefix, delimiter='/')

    for obj in it:
        key = getattr(obj, 'key', None) or getattr(obj, 'name', str(obj))
        if not key or not key.startswith(full_prefix):
            continue
        is_prefix = getattr(obj, 'is_prefix', None)
        if callable(is_prefix):
            is_prefix = is_prefix()
        elif is_prefix is None:
            is_prefix = key.endswith('/')
        rel = key[len(base) :]
        if is_prefix:
            # 如 data/subdir/ -> path="data/subdir", name="subdir"
            seg = rel.rstrip('/')
            name = seg.split('/')[-1] if '/' in seg else seg
            if name:
                entries.append({'name': name, 'path': seg, 'type': 'directory'})
        else:
            # 如 data/file.cif -> path="data/file.cif", name="file.cif"
            name = rel.split('/')[-1] if '/' in rel else rel
            if name:
                entries.append({'name': name, 'path': rel, 'type': 'file'})

    return entries
