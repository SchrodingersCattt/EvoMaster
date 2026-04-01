"""Result download helpers for monitor_job."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

from ._constants import _AUTO_DOWNLOAD_MAX_BYTES

logger = logging.getLogger(__name__)


def _download_results_to_local_dir(
    download_dir: Path,
    bohr_job_id: str,
    access_key: str | None,
) -> dict[str, Any]:
    """Download files referenced by results.txt into *download_dir* (local path).

    Returns a dict with 'downloaded', 'download_dir', and optionally
    'download_skipped' / 'download_errors' / 'referenced_files'.
    """
    # Lazy import: matmaster.adaptors.calculation (not triggered at module load time)
    from matmaster.adaptors.calculation.job_service import (
        download_job_directory,
        download_job_file,
        get_file_token,
        iterate_job_files,
    )

    download_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: fetch results.txt
    results_txt_local = download_dir / 'result_0_results.txt'
    try:
        download_job_file(
            'results.txt', bohr_job_id, results_txt_local, access_key=access_key
        )
    except Exception as exc:
        return {
            'status': 'failed',
            'download_errors': [f'results.txt: {exc}'],
        }

    text = results_txt_local.read_text(encoding='utf-8', errors='replace')
    try:
        parsed: Any = json.loads(text)
    except Exception:
        parsed = {}

    if not isinstance(parsed, dict):
        return {
            'downloaded': [results_txt_local.resolve().as_posix()],
            'download_dir': download_dir.resolve().as_posix(),
            'download_errors': ['results.txt payload is not a JSON object'],
        }

    # Step 2: extract referenced file paths
    _SHELL_CMD_MARKERS = frozenset(
        {'&&', '||', '; ', 'mpirun', 'source ', 'export ', ' -n ', ' | '}
    )

    def _looks_like_shell_cmd(s: str) -> bool:
        """Return True when s looks like a shell command rather than a file path."""
        return any(m in s for m in _SHELL_CMD_MARKERS)

    def _extract_path_from_py_reduce(v: Any) -> str | None:
        if not isinstance(v, dict):
            return None
        reduce_items = v.get('py/reduce')
        if not isinstance(reduce_items, list) or len(reduce_items) < 2:
            return None
        tuple_item = reduce_items[1]
        if not isinstance(tuple_item, dict):
            return None
        tuple_vals = tuple_item.get('py/tuple')
        if not isinstance(tuple_vals, list) or not tuple_vals:
            return None
        parts: list[str] = []
        for item in tuple_vals:
            if not isinstance(item, str):
                continue
            segment = item.replace('\\', '/').strip()
            if segment:
                parts.append(segment)
        if not parts:
            return None
        return str(PurePosixPath(parts[0], *parts[1:]))

    referenced_files: list[str] = []
    for v in parsed.values():
        if isinstance(v, str) and v.strip():
            if '/' in v or '\\' in v or '.' in v:
                if not _looks_like_shell_cmd(v):
                    referenced_files.append(v.replace('\\', '/').strip())
            continue
        extracted = _extract_path_from_py_reduce(v)
        if extracted:
            referenced_files.append(extracted)

    # Step 3: resolve root prefix for path normalisation
    root_prefix = ''
    try:
        _, token_root_path, _ = get_file_token('', bohr_job_id, access_key=access_key)
        root_prefix = str(token_root_path or '').replace('\\', '/')
        if root_prefix and not root_prefix.endswith('/'):
            root_prefix += '/'
    except Exception:
        root_prefix = ''

    def _to_rel(remote_path: str) -> str:
        p = remote_path.replace('\\', '/').strip()
        if root_prefix and p.startswith(root_prefix):
            return p[len(root_prefix) :].lstrip('/')
        return p

    # Step 4: get file sizes and directory membership for size-gating / dir detection
    size_map: dict[str, int] = {}
    dir_set: set[str] = set()
    try:
        for obj in iterate_job_files(bohr_job_id, access_key=access_key):
            if not isinstance(obj, dict):
                continue
            p = obj.get('path')
            s = obj.get('size')
            is_dir = bool(obj.get('isDir'))
            if isinstance(p, str):
                norm_p = p.replace('\\', '/')
                if is_dir:
                    dir_set.add(norm_p.rstrip('/'))
                elif isinstance(s, int):
                    size_map[norm_p] = s
    except Exception:
        pass

    # Step 5: download each referenced file (or directory)
    downloaded: list[str] = [results_txt_local.resolve().as_posix()]
    skipped: list[str] = []
    errors: list[str] = []

    for i, remote_path in enumerate(referenced_files, start=1):
        if not isinstance(remote_path, str) or not remote_path.strip():
            continue
        rp = remote_path.strip()
        rel_rp = _to_rel(rp)

        # Detect whether this path is a directory (either flagged by iterate_job_files
        # or has no file extension and no matching size entry -- conservative heuristic).
        norm_rp = rp.replace('\\', '/')
        is_directory = (
            norm_rp.rstrip('/') in dir_set
            or rel_rp.rstrip('/') in dir_set
            or (
                '.' not in rp.rsplit('/', 1)[-1]
                and norm_rp not in size_map
                and rel_rp not in size_map
            )
        )

        if is_directory:
            # Download all files inside the directory recursively.
            segment = rel_rp.rstrip('/').rsplit('/', 1)[-1] or f'artifact_{i}'
            segment = re.sub(r'[^\w.\-]', '_', segment) or f'artifact_{i}'
            dest_dir_path = download_dir / f'result_{i}_{segment}'
            try:
                dir_files = download_job_directory(
                    rel_rp,
                    bohr_job_id,
                    dest_dir_path,
                    access_key=access_key,
                    max_bytes_per_file=_AUTO_DOWNLOAD_MAX_BYTES,
                )
                downloaded.extend(p.resolve().as_posix() for p in dir_files)
                if not dir_files:
                    skipped.append(
                        f'{rp}: directory is empty or all files exceeded size limit'
                    )
            except Exception as exc:
                errors.append(f'{rp} (directory): {exc}')
            continue

        size = size_map.get(norm_rp) or size_map.get(rel_rp)
        if isinstance(size, int) and size > _AUTO_DOWNLOAD_MAX_BYTES:
            skipped.append(f'{rp}: skipped ({size} bytes > {_AUTO_DOWNLOAD_MAX_BYTES})')
            continue
        segment = rp.rsplit('/', 1)[-1] or f'artifact_{i}'
        segment = re.sub(r'[^\w.\-]', '_', segment) or f'artifact_{i}'
        dest = download_dir / f'result_{i}_{segment}'
        try:
            path = download_job_file(rel_rp, bohr_job_id, dest, access_key=access_key)
            downloaded.append(path.resolve().as_posix())
        except Exception as exc:
            errors.append(f'{rp}: {exc}')

    info: dict[str, Any] = {
        'downloaded': downloaded,
        'download_dir': download_dir.resolve().as_posix(),
        'referenced_files': referenced_files,
    }
    if skipped:
        info['download_skipped'] = skipped
    if errors:
        info['download_errors'] = errors
    return info


def _sftp_push_directory(
    session: Any, local_dir: Path, remote_dir: str
) -> list[str]:
    """Upload all files in *local_dir* to *remote_dir* on the SSH node.

    Returns list of remote paths uploaded.
    """
    # Duck-type session instead of isinstance(session, SSHSession)
    is_ssh = hasattr(session, '_env') and hasattr(getattr(session, '_env', None), 'upload_file')
    if not is_ssh:
        return []
    env = session._env
    pushed: list[str] = []
    for local_file in local_dir.rglob('*'):
        if not local_file.is_file():
            continue
        rel = local_file.relative_to(local_dir).as_posix()
        remote_path = f'{remote_dir}/{rel}'
        try:
            env.upload_file(str(local_file), remote_path)
            pushed.append(remote_path)
        except Exception as exc:
            logger.warning(
                'monitor_job: SFTP push failed %s -> %s: %s',
                local_file,
                remote_path,
                exc,
            )
    return pushed
