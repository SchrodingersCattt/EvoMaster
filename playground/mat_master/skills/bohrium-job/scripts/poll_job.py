"""poll_job.py - monitor a Bohrium job by job_id and download results.

Uses GET /openapi/v1/sandbox/job/{id} when ``BOHRIUM_USE_SANDBOX=1`` (default when unset),
else ``GET /openapi/v1/job/{id}`` when ``0``. Same env rule as submit_job.py.
"""

import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv()
except ImportError:
    pass

ACCESS_KEY = os.environ.get('BOHRIUM_ACCESS_KEY', '').strip()
try:
    from src.utils.constant import BOHRIUM_OPENAPI_HOST

    OPENAPI_BASE = BOHRIUM_OPENAPI_HOST
except ImportError:
    OPENAPI_BASE = os.environ.get(
        'BOHRIUM_BASE_URL', 'https://open.bohrium.com'
    ).rstrip('/')

_HEADER = {'accessKey': ACCESS_KEY}
_JSON_HEADER = {'accessKey': ACCESS_KEY, 'Content-Type': 'application/json'}


def _use_sandbox() -> bool:
    """True when BOHRIUM_USE_SANDBOX is ``1`` (default when unset: sandbox)."""
    return os.environ.get('BOHRIUM_USE_SANDBOX', '1').strip() == '1'


def _job_detail_path(job_id: int | str) -> str:
    if _use_sandbox():
        return f'/openapi/v1/sandbox/job/{job_id}'
    return f'/openapi/v1/job/{job_id}'


_STATUS_MAP = {
    -10: 'Prepared',
    -2: 'Deleted',
    -1: 'Failed',
    0: 'Pending',
    1: 'Running',
    2: 'Finished',
    3: 'Scheduling',
    6: 'Unknown',
}
_SUCCESS_CODE = 2
_RUNNING_CODES = {-10, 0, 1, 3}
_FAILURE_CODES = {-2, -1}
_MAX_FAILURE_CONFIRMS = 3
_MAX_UNKNOWN_COUNT = 3


def _get(path: str, timeout: int = 30, headers: dict | None = None, base_url: str | None = None) -> dict:
    response = requests.get(
        f"{(base_url or OPENAPI_BASE).rstrip('/')}{path}",
        headers=headers or _HEADER,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _post(
    path: str,
    payload: dict,
    timeout: int = 30,
    headers: dict | None = None,
    base_url: str | None = None,
) -> dict:
    response = requests.post(
        f"{(base_url or OPENAPI_BASE).rstrip('/')}{path}",
        headers=headers or _JSON_HEADER,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _get_job_detail(job_id: int | str) -> dict:
    response = _get(_job_detail_path(job_id))
    return response.get('data', {})


def poll_until_done(
    job_id: int | str, max_polls: int, interval: int
) -> tuple[str, int]:
    """Poll job detail until terminal status (path depends on BOHRIUM_USE_SANDBOX).

    Returns a tuple ``(status, polls_done)`` where *polls_done* is the number
    of poll iterations actually executed. When the budget exhausts while the
    job is still in a running state the returned status is ``'Timeout'``
    (sentinel); the caller converts this to ``'still_running'`` with exit 0.
    """
    print(
        f"[poll] job_id={job_id}, max_polls={max_polls}, interval={interval}s",
        flush=True,
    )
    failure_confirms = 0
    unknown_count = 0
    last_non_running_status = 'Timeout'
    polls_done = 0

    for idx in range(max_polls):
        polls_done = idx + 1
        detail = _get_job_detail(job_id)
        code = detail.get('status', 0)
        name = _STATUS_MAP.get(code, f"Unknown({code})")
        print(f"[poll] [{idx + 1:02d}/{max_polls}] {name}", flush=True)

        if code == _SUCCESS_CODE:
            return name, polls_done

        if code in _FAILURE_CODES:
            failure_confirms += 1
            last_non_running_status = name
            print(
                f"[poll] failure confirm {failure_confirms}/{_MAX_FAILURE_CONFIRMS}",
                flush=True,
            )
            if failure_confirms >= _MAX_FAILURE_CONFIRMS:
                return name, polls_done
            time.sleep(min(interval, 10))
            continue

        if code not in _RUNNING_CODES:
            unknown_count += 1
            last_non_running_status = f"Unknown({code})"
            print(
                f"[poll] unknown code={code} count={unknown_count}/{_MAX_UNKNOWN_COUNT}",
                flush=True,
            )
            if unknown_count >= _MAX_UNKNOWN_COUNT:
                return f"Unknown({code})", polls_done
            time.sleep(min(interval, 10))
            continue

        failure_confirms = 0
        unknown_count = 0
        time.sleep(interval)

    return last_non_running_status, polls_done


def read_log_from_dir(extract_dir: Path, max_chars: int = 4000) -> str:
    """Read log tail from extracted result directory.

    Priority: log > STDOUTERR
    Returns the last max_chars characters of the first matching file.
    """
    for name in ('log', 'STDOUTERR'):
        f = extract_dir / name
        if f.exists():
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
                return text[-max_chars:] if len(text) > max_chars else text
            except Exception:
                continue
    return '(no log file found in result directory)'


def _download_url_to_file(
    url: str, dest_path: Path, timeout: int = 300, headers: dict | None = None
) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with open(dest_path, 'wb') as file_obj:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file_obj.write(chunk)


def _extract_zip_to_dir(zip_path: Path, extract_dir: Path) -> list[str]:
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zip_file:
        names = zip_file.namelist()
        zip_file.extractall(extract_dir)
    return names


def _sandbox_file_token(job_id: int | str, file_path: str) -> dict:
    response = _post(
        '/openapi/v1/sandbox/job/file/token',
        {'filePath': file_path, 'jobId': str(job_id)},
    )
    if response.get('code') != 0:
        raise RuntimeError(f"sandbox job/file/token failed: {response}")
    return response.get('data', {})


def _sandbox_parse_result_url(result_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(result_url)
    host = f'{parsed.scheme}://{parsed.netloc}'
    token = parse_qs(parsed.query).get('token', [''])[0]
    object_path = unquote(parsed.path.removeprefix('/api/download/'))
    prefix = object_path.rsplit('/', 1)[0] + '/'
    if not host or not token or not object_path:
        raise ValueError(f'invalid sandbox resultUrl: {result_url}')
    return host.rstrip('/'), token, object_path, prefix


def _sandbox_iterate_objects(host: str, token: str, prefix: str) -> list[dict]:
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    objects: list[dict] = []
    next_token = ''
    while True:
        payload = {'prefix': prefix}
        if next_token:
            payload['nextToken'] = next_token
        response = _post('/api/iterate', payload, headers=headers, base_url=host)
        if response.get('code') != 0:
            raise RuntimeError(f'sandbox iterate failed: {response}')
        data = response.get('data') or {}
        objects.extend(data.get('objects') or [])
        if not data.get('hasNext'):
            break
        next_token = data.get('nextToken') or ''
        if not next_token:
            break
    return objects


def _sandbox_download_object(
    host: str, token: str, object_path: str, dest_path: Path, timeout: int = 300
) -> None:
    encoded_path = quote(object_path, safe='/')
    url = (
        f"{host.rstrip('/')}/api/download/{encoded_path}?token={token}"
        '&Response-Content-Type=application/octet-stream'
    )
    _download_url_to_file(url, dest_path, timeout=timeout)


def _choose_sandbox_zip_object(job_id: int | str, objects: list[dict]) -> str | None:
    file_paths = [
        obj.get('path', '')
        for obj in objects
        if isinstance(obj, dict) and obj.get('path') and not obj.get('isDir')
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


def _download_result_url_zip(detail: dict, result_dir: Path) -> tuple[list[str], str]:
    result_url = detail.get('resultUrl') or detail.get('result') or ''
    if not result_url:
        out_files = (detail.get('jobFiles') or {}).get('outFiles') or []
        if out_files and isinstance(out_files[0], dict):
            result_url = out_files[0].get('url', '')
    if not result_url:
        raise RuntimeError('resultUrl not found in job detail response')

    zip_path = result_dir / 'out.zip'
    _download_url_to_file(result_url, zip_path)
    extract_dir = result_dir / 'extracted'
    names = _extract_zip_to_dir(zip_path, extract_dir)
    return names, str(extract_dir.resolve())


def _sandbox_download_and_extract(
    job_id: int | str, result_dir: Path
) -> tuple[list[str], str]:
    result_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = result_dir / 'extracted'
    extract_dir.mkdir(parents=True, exist_ok=True)

    detail = _get_job_detail(job_id)
    files: list[str] = []
    errors: list[str] = []
    objects: list[dict] = []
    result_url = detail.get('resultUrl') or detail.get('result') or ''
    root_host = ''
    root_token = ''
    root_prefix = ''

    if result_url:
        try:
            root_host, root_token, _object_path, root_prefix = _sandbox_parse_result_url(
                result_url
            )
            objects = _sandbox_iterate_objects(root_host, root_token, root_prefix)
            files = [
                obj.get('path', '')
                for obj in objects
                if isinstance(obj, dict) and obj.get('path')
            ]
        except Exception as exc:
            errors.append(f'iterate failed: {exc}')

    log_downloaded = False
    try:
        log_token_data = _sandbox_file_token(job_id, 'log')
        _sandbox_download_object(
            log_token_data['host'],
            log_token_data['token'],
            log_token_data['path'],
            extract_dir / 'log',
        )
        log_downloaded = True
    except Exception as exc:
        errors.append(f'log token/download failed: {exc}')
        if root_host and root_token:
            log_object = next(
                (
                    obj.get('path', '')
                    for obj in objects
                    if isinstance(obj, dict)
                    and not obj.get('isDir')
                    and Path(obj.get('path', '')).name == 'log'
                ),
                '',
            )
            if log_object:
                try:
                    _sandbox_download_object(root_host, root_token, log_object, extract_dir / 'log')
                    log_downloaded = True
                except Exception as log_exc:
                    errors.append(f'log fallback download failed: {log_exc}')

    zip_object = _choose_sandbox_zip_object(job_id, objects)
    if zip_object and root_host and root_token:
        zip_path = result_dir / Path(zip_object).name
        _sandbox_download_object(root_host, root_token, zip_object, zip_path)
        names = _extract_zip_to_dir(zip_path, extract_dir)
        return names, str(extract_dir.resolve())

    if result_url:
        try:
            return _download_result_url_zip(detail, result_dir)
        except Exception as exc:
            errors.append(f'resultUrl download failed: {exc}')

    if log_downloaded or files:
        relative_files = files
        if root_prefix:
            relative_files = [
                path[len(root_prefix) :] if path.startswith(root_prefix) else path
                for path in files
            ]
        return relative_files, str(extract_dir.resolve())

    if errors:
        raise RuntimeError('; '.join(errors))
    raise RuntimeError('sandbox artifacts not found')


def download_and_extract(job_id: int | str, result_dir: Path) -> tuple[list[str], str]:
    """Download result artifacts and extract them to a local directory."""
    if _use_sandbox():
        return _sandbox_download_and_extract(job_id, result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    detail = _get_job_detail(job_id)
    return _download_result_url_zip(detail, result_dir)


def _parse_job_id_arg(value: str) -> int | str:
    """Follows BOHRIUM_USE_SANDBOX: sandbox uses UUID string; standard uses int."""
    s = value.strip()
    if _use_sandbox():
        return s
    return int(s)


def main() -> None:
    parser = argparse.ArgumentParser(description='Monitor Bohrium job by job_id')
    parser.add_argument(
        '--job-id',
        required=True,
        help='Job id: UUID string (sandbox, default); integer when BOHRIUM_USE_SANDBOX=0 (standard HPC)',
    )
    parser.add_argument(
        '--max-polls',
        type=int,
        default=2880,
        help='Max polling attempts (2880 x 30s = 24 hours)',
    )
    parser.add_argument(
        '--poll-interval', type=int, default=30, help='Seconds between polls'
    )
    parser.add_argument(
        '--timeout-minutes',
        type=float,
        default=None,
        help=(
            'Optional convenience shortcut: compute max_polls from '
            'timeout_minutes * 60 / poll_interval. '
            'Overrides --max-polls when provided.'
        ),
    )
    parser.add_argument(
        '--result-dir',
        default=None,
        help='Directory to save results (default: results/run_<job_id>)',
    )
    args = parser.parse_args()

    if not ACCESS_KEY:
        print(json.dumps({'success': False, 'error': 'BOHRIUM_ACCESS_KEY not set'}))
        sys.exit(1)

    max_polls = args.max_polls
    if args.timeout_minutes is not None:
        max_polls = max(1, int(args.timeout_minutes * 60 / args.poll_interval))

    job_id = _parse_job_id_arg(args.job_id)
    start_time = time.time()
    status, polls_done = poll_until_done(job_id, max_polls, args.poll_interval)
    elapsed_seconds = time.time() - start_time

    detail = {}
    try:
        detail = _get_job_detail(job_id)
    except Exception:
        detail = {}
    bohr_job_id = detail.get('bohrJobId') or job_id

    if status == 'Timeout':
        print(
            json.dumps(
                {
                    'success': True,
                    'job_id': job_id,
                    'bohr_job_id': bohr_job_id,
                    'status': 'still_running',
                    'polls_done': polls_done,
                    'elapsed_seconds': round(elapsed_seconds, 1),
                    'log_tail': '',
                    'message': (
                        f"Poll budget exhausted ({polls_done}/{max_polls} polls, "
                        f"{round(elapsed_seconds / 60, 1)} min). "
                        f"Job is still running on Bohrium (job_id={job_id}). "
                        'Re-invoke poll_job.py with the same --job-id to continue monitoring, '
                        'or finish with task_completed=partial.'
                    ),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(0)

    result_dir = (
        Path(args.result_dir) if args.result_dir else Path(f"results/run_{job_id}")
    )
    extract_dir: Path | None = None
    files: list[str] = []
    download_error: str | None = None

    try:
        files, extract_dir_str = download_and_extract(job_id, result_dir)
        extract_dir = Path(extract_dir_str)
    except Exception as exc:
        download_error = str(exc)

    log_tail = ''
    if extract_dir and extract_dir.exists():
        log_tail = read_log_from_dir(extract_dir)

    if status != 'Finished':
        error_msg = f"job ended with status: {status}"
        if download_error:
            error_msg += f'; download error: {download_error}'
        print(
            json.dumps(
                {
                    'success': False,
                    'job_id': job_id,
                    'bohr_job_id': bohr_job_id,
                    'status': status,
                    'polls_done': polls_done,
                    'elapsed_seconds': round(elapsed_seconds, 1),
                    'result_dir': str(extract_dir.resolve()) if extract_dir else None,
                    'files': files,
                    'log_tail': log_tail,
                    'error': error_msg,
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    if download_error:
        print(
            json.dumps(
                {
                    'success': False,
                    'job_id': job_id,
                    'bohr_job_id': bohr_job_id,
                    'status': status,
                    'polls_done': polls_done,
                    'elapsed_seconds': round(elapsed_seconds, 1),
                    'log_tail': log_tail,
                    'error': f'download failed: {download_error}',
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    print(
        json.dumps(
            {
                'success': True,
                'job_id': job_id,
                'bohr_job_id': bohr_job_id,
                'status': status,
                'polls_done': polls_done,
                'elapsed_seconds': round(elapsed_seconds, 1),
                'result_dir': str(extract_dir.resolve()),
                'files': files,
                'log_tail': log_tail,
            },
            ensure_ascii=False,
        )
    )


if __name__ == '__main__':
    main()
