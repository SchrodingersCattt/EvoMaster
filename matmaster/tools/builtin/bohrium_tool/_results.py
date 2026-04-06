"""Result download helpers for the builtin Bohrium tool package."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from ._api import _post, _ResolvedBohriumContext

logger = logging.getLogger(__name__)


def _download_to_file(url: str, dest: Path, *, timeout: int = 300) -> None:
    """Stream-download a URL to a local file."""
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    with open(dest, 'wb') as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            fh.write(chunk)


def _extract_zip(zip_path: Path, extract_dir: Path) -> list[str]:
    """Extract a zip and return list of extracted filenames."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)
            return zf.namelist()
    except zipfile.BadZipFile:
        return [f'(bad zip: {zip_path.name})']


def _read_log(extract_dir: Path, max_chars: int = 4000) -> str:
    """Read log tail from extracted result directory."""
    for name in ('log', 'STDOUTERR'):
        file_path = extract_dir / name
        if file_path.exists():
            try:
                size = file_path.stat().st_size
                with open(file_path, 'rb') as fh:
                    if size > max_chars * 4:
                        fh.seek(-(max_chars * 4), 2)
                    raw = fh.read()
                text = raw.decode('utf-8', errors='replace')
                return text[-max_chars:]
            except Exception:
                continue
    return '(no log file found in result directory)'


def _parse_sandbox_result_url(result_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(result_url)
    host = f'{parsed.scheme}://{parsed.netloc}'.rstrip('/')
    token = parse_qs(parsed.query).get('token', [''])[0].strip()
    object_path = unquote(parsed.path.removeprefix('/api/download/')).strip('/')
    if not host or not token or not object_path:
        raise ValueError(f'invalid sandbox resultUrl: {result_url}')
    prefix = object_path.rsplit('/', 1)[0] + '/' if '/' in object_path else ''
    return host, token, object_path, prefix


def _sandbox_iterate_objects(
    host: str, token: str, prefix: str
) -> list[dict[str, Any]]:
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    objects: list[dict[str, Any]] = []
    next_token = ''

    while True:
        payload: dict[str, Any] = {'prefix': prefix}
        if next_token:
            payload['nextToken'] = next_token

        response = requests.post(
            f'{host.rstrip("/")}/api/iterate',
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json() or {}
        if body.get('code') not in (None, 0):
            raise RuntimeError(f'sandbox iterate failed: {body}')

        data = body.get('data') or {}
        objects.extend(data.get('objects') or [])

        if not data.get('hasNext'):
            break
        next_token = str(data.get('nextToken') or '').strip()
        if not next_token:
            break

    return objects


def _sandbox_download_object(
    host: str, token: str, object_path: str, dest_path: Path
) -> None:
    encoded_path = quote(object_path, safe='/')
    url = (
        f'{host.rstrip("/")}/api/download/{encoded_path}?token={token}'
        '&Response-Content-Type=application/octet-stream'
    )
    _download_to_file(url, dest_path)


def _sandbox_choose_zip_object(
    job_id: int | str, objects: list[dict[str, Any]]
) -> str | None:
    file_paths = [
        str(obj.get('path') or obj.get('key') or '').strip()
        for obj in objects
        if isinstance(obj, dict)
        and not obj.get('isDir')
        and (obj.get('path') or obj.get('key'))
    ]

    preferred_name = f'{job_id}.zip'
    for path in file_paths:
        if Path(path).name == preferred_name:
            return path
    for path in file_paths:
        if path.endswith('.zip') and Path(path).name != 'task.zip':
            return path
    for path in file_paths:
        if path.endswith('.zip'):
            return path
    return None


def _sandbox_download_log(
    *,
    job_id: int | str,
    result_dir: Path,
    ctx: _ResolvedBohriumContext,
    root_host: str,
    root_token: str,
    objects: list[dict[str, Any]],
) -> bool:
    log_path = result_dir / 'log'

    try:
        token_resp = _post(
            ctx.base_url,
            '/openapi/v1/sandbox/job/file/token',
            ctx.access_key,
            {'filePath': 'log', 'jobId': str(job_id)},
        )
        if token_resp.get('code') == 0:
            token_data = token_resp.get('data') or {}
            log_host = str(token_data.get('host') or token_data.get('storeHost') or '')
            log_token = str(token_data.get('token') or '').strip()
            log_object_path = str(
                token_data.get('path') or token_data.get('storePath') or ''
            ).strip('/')
            if log_host and log_token and log_object_path:
                _sandbox_download_object(log_host, log_token, log_object_path, log_path)
                return True
    except Exception:
        logger.debug('sandbox log token download failed', exc_info=True)

    if root_host and root_token:
        log_object = next(
            (
                str(obj.get('path') or obj.get('key') or '').strip()
                for obj in objects
                if isinstance(obj, dict)
                and not obj.get('isDir')
                and Path(str(obj.get('path') or obj.get('key') or '')).name == 'log'
            ),
            '',
        )
        if log_object:
            _sandbox_download_object(root_host, root_token, log_object, log_path)
            return True

    return False


def _merge_log_file(files: list[str], log_downloaded: bool) -> list[str]:
    if not log_downloaded:
        return files
    if 'log' in files:
        return files
    return ['log', *files]


def _sandbox_download_results(
    job_id: int | str,
    detail_data: dict,
    result_dir: Path,
    *,
    ctx: _ResolvedBohriumContext,
) -> tuple[list[str], str]:
    """Download results in sandbox mode."""
    result_url = str(detail_data.get('resultUrl') or detail_data.get('result') or '')
    objects: list[dict[str, Any]] = []
    root_host = ''
    root_token = ''
    root_prefix = ''

    if result_url:
        try:
            root_host, root_token, _object_path, root_prefix = (
                _parse_sandbox_result_url(result_url)
            )
            objects = _sandbox_iterate_objects(root_host, root_token, root_prefix)
        except Exception as exc:
            logger.debug(
                'sandbox resultUrl iteration failed job_id=%s error=%s',
                job_id,
                exc,
                exc_info=True,
            )

    log_downloaded = _sandbox_download_log(
        job_id=job_id,
        result_dir=result_dir,
        ctx=ctx,
        root_host=root_host,
        root_token=root_token,
        objects=objects,
    )

    zip_key = _sandbox_choose_zip_object(job_id, objects)
    if zip_key and root_host and root_token:
        try:
            zip_path = result_dir / Path(zip_key).name
            _sandbox_download_object(root_host, root_token, zip_key, zip_path)
            files = _extract_zip(zip_path, result_dir)
            files = _merge_log_file(files, log_downloaded)
            log_tail = _read_log(result_dir)
            return files, log_tail
        except Exception as exc:
            logger.debug(
                'sandbox zip-object download failed job_id=%s zip=%s error=%s',
                job_id,
                zip_key,
                exc,
                exc_info=True,
            )

    if result_url:
        try:
            zip_path = result_dir / 'out.zip'
            _download_to_file(result_url, zip_path)
            files = _extract_zip(zip_path, result_dir)
            files = _merge_log_file(files, log_downloaded)
            log_tail = _read_log(result_dir)
            return files, log_tail
        except Exception as exc:
            logger.debug(
                'sandbox resultUrl zip download failed job_id=%s error=%s',
                job_id,
                exc,
                exc_info=True,
            )

    if log_downloaded:
        return ['log'], _read_log(result_dir)

    if objects:
        files = [
            str(obj.get('path') or obj.get('key') or '')
            for obj in objects
            if isinstance(obj, dict) and (obj.get('path') or obj.get('key'))
        ]
        if root_prefix:
            files = [
                path[len(root_prefix) :] if path.startswith(root_prefix) else path
                for path in files
            ]
        return files, _read_log(result_dir)

    if result_url:
        return [], '(sandbox resultUrl download failed)'
    return [], '(no resultUrl in job detail)'


def download_bohrium_results(
    job_id: int | str,
    detail_data: dict,
    result_dir: Path,
    *,
    ctx: _ResolvedBohriumContext,
    sandbox: bool,
) -> tuple[list[str], str]:
    """Download and extract Bohrium result artifacts."""
    result_dir.mkdir(parents=True, exist_ok=True)

    if sandbox:
        return _sandbox_download_results(job_id, detail_data, result_dir, ctx=ctx)

    result_url = detail_data.get('resultUrl', '')
    if not result_url:
        return [], '(no resultUrl in job detail)'

    zip_path = result_dir / 'out.zip'
    _download_to_file(result_url, zip_path)

    files = _extract_zip(zip_path, result_dir)
    log_tail = _read_log(result_dir)
    return files, log_tail
