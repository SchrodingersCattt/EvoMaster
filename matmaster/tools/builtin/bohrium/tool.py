"""matmaster/tools/builtin/bohrium/tool.py — Bohrium HPC platform tool.

Single tool with action-based dispatch for Bohrium HPC operations.
This tool handles pure communication: submit, poll (single-query), list_images,
list_machines. All software-specific knowledge lives in software skills.

Design decisions:
- poll is single-shot (non-blocking): returns current status, Agent controls retry
- submit auto-appends "> log 2>&1" if missing
- All API credentials read from environment variables at call time (not init)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

import requests

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bohrium API helpers (shared across actions)
# ---------------------------------------------------------------------------

def _resolve_env() -> tuple[str, int, str]:
    """Read Bohrium credentials from environment.

    Returns (access_key, project_id, base_url).
    """
    try:
        from src.utils.constant import BOHRIUM_OPENAPI_HOST
        base_url = BOHRIUM_OPENAPI_HOST
    except ImportError:
        base_url = os.environ.get(
            'BOHRIUM_BASE_URL', 'https://open.bohrium.com'
        ).rstrip('/')

    access_key = os.environ.get('BOHRIUM_ACCESS_KEY', '').strip()
    raw_pid = os.environ.get('BOHRIUM_PROJECT_ID', '-1') or '-1'
    try:
        project_id = int(raw_pid)
    except ValueError:
        project_id = -1

    return access_key, project_id, base_url


def _use_sandbox() -> bool:
    return os.environ.get('BOHRIUM_USE_SANDBOX', '1').strip() == '1'


def _get(base_url: str, path: str, access_key: str,
         params: dict | None = None, timeout: int = 30) -> dict:
    resp = requests.get(
        f'{base_url}{path}',
        headers={'accessKey': access_key, 'Accept': 'application/json'},
        params=params or {},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _post(base_url: str, path: str, access_key: str,
          payload: dict, timeout: int = 30) -> dict:
    resp = requests.post(
        f'{base_url}{path}',
        headers={'accessKey': access_key, 'Content-Type': 'application/json'},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Status code mappings (shared with poll)
# ---------------------------------------------------------------------------

_STATUS_MAP = {
    -10: 'Prepared', -2: 'Deleted', -1: 'Failed',
    0: 'Pending', 1: 'Running', 2: 'Finished',
    3: 'Scheduling', 6: 'Unknown',
}
_SUCCESS_CODE = 2
_RUNNING_CODES = {-10, 0, 1, 3}
_FAILURE_CODES = {-2, -1}


# ═══════════════════════════════════════════════════════════════════════════
# BohriumTool
# ═══════════════════════════════════════════════════════════════════════════

class BohriumTool(BuiltinTool):
    """Bohrium HPC platform operations via action-based dispatch."""

    name: ClassVar[str] = 'Bohrium'
    description: ClassVar[str] = (
        'Bohrium HPC platform operations. '
        'action="submit": package input directory and submit a job, returns job_id. '
        'action="poll": single query of current job status; downloads results when Finished. '
        'action="list_images": query available Docker images by keyword. '
        'action="list_machines": query available machine types (cpu/gpu).'
    )

    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'action': {
                'type': 'string',
                'enum': ['submit', 'poll', 'list_images', 'list_machines'],
                'description': 'Operation to perform.',
            },
            # --- submit ---
            'input_dir': {
                'type': 'string',
                'description': 'Directory containing all input files to upload. (submit)',
            },
            'image': {
                'type': 'string',
                'description': 'Docker image address, e.g. registry.dp.tech/dptech/cp2k:2024.1. (submit)',
            },
            'cmd': {
                'type': 'string',
                'description': 'Shell command to run inside the container. (submit)',
            },
            'machine': {
                'type': 'string',
                'description': 'Bohrium machine type. Default: c32_m128_cpu. (submit)',
            },
            'job_name': {
                'type': 'string',
                'description': 'Human-readable job name. (submit)',
            },
            'disk_size': {
                'type': 'integer',
                'description': 'Disk size in GB. Default: 50. (submit)',
            },
            # --- poll ---
            'job_id': {
                'type': ['integer', 'string'],
                'description': 'Job ID returned by submit. (poll)',
            },
            'result_dir': {
                'type': 'string',
                'description': 'Local directory for downloaded results. (poll)',
            },
            # --- list ---
            'keyword': {
                'type': 'string',
                'description': 'Filter keyword for images or machines. (list_images, list_machines)',
            },
            'machine_type': {
                'type': 'string',
                'enum': ['cpu', 'gpu'],
                'description': 'Machine type filter. Default: cpu. (list_machines)',
            },
            'max_results': {
                'type': 'integer',
                'description': 'Maximum entries to return. Default: 20. (list_images, list_machines)',
            },
        },
        'required': ['action'],
    }

    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource='bohrium-api', mode='counted', max_concurrent=3),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({
        'bohrium.submit', 'bohrium.query',
    })
    effect_level: ClassVar[str] = 'external_effect'
    fast_path_eligible: ClassVar[bool] = False
    plane: ClassVar[ToolPlane] = ToolPlane.EXTERNAL_SERVICE
    state_mode: ClassVar[str] = 'stateless'
    stop_mode: ClassVar[str] = 'cancellable'
    exposed_to_model: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 0

    # ------------------------------------------------------------------
    # prompt(): platform guidance injected into system prompt
    # ------------------------------------------------------------------

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return (
            '## Bohrium tool usage\n'
            '- Load the corresponding software skill first (cp2k, qe, abacus, orca, '
            'lammps, gromacs, pyscf, abinit, pyatb) to obtain image, machine, and cmd.\n'
            '- submit: cmd MUST end with "> log 2>&1" (auto-appended if missing).\n'
            '- poll: non-blocking single query. Returns Running/Finished/Failed. '
            'Call again to re-check a Running job.\n'
            '- When image or machine is unknown, call list_images / list_machines first.\n'
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        action = arguments.get('action', '')
        match action:
            case 'submit':
                return self._submit(arguments)
            case 'poll':
                return self._poll(arguments)
            case 'list_images':
                return self._list_images(arguments)
            case 'list_machines':
                return self._list_machines(arguments)
            case _:
                return ToolResult(
                    status='error',
                    content=f'Unknown action: {action!r}. '
                            f'Must be one of: submit, poll, list_images, list_machines.',
                )

    # ------------------------------------------------------------------
    # action: submit
    # ------------------------------------------------------------------

    def _submit(self, args: dict[str, Any]) -> ToolResult:
        access_key, project_id, base_url = _resolve_env()
        if not access_key:
            return ToolResult(status='error', content='BOHRIUM_ACCESS_KEY not set.')
        if project_id <= 0:
            return ToolResult(status='error', content='BOHRIUM_PROJECT_ID not set or invalid.')

        input_dir = args.get('input_dir', '')
        image = args.get('image', '')
        cmd = args.get('cmd', '')

        if not input_dir:
            return ToolResult(status='error', content='Missing required parameter: input_dir')
        if not image:
            return ToolResult(status='error', content='Missing required parameter: image')
        if not cmd:
            return ToolResult(status='error', content='Missing required parameter: cmd')

        input_path = Path(input_dir)
        if not input_path.is_dir():
            return ToolResult(status='error', content=f'input_dir not found: {input_dir}')

        machine = args.get('machine', 'c32_m128_cpu')
        job_name = args.get('job_name', f'matmaster-job')
        disk_size = args.get('disk_size', 50)

        # Auto-append log redirection
        cmd_stripped = cmd.rstrip()
        if not cmd_stripped.endswith('> log 2>&1'):
            cmd = cmd_stripped + ' > log 2>&1'

        sandbox = _use_sandbox()

        try:
            # Step 1: create job
            if sandbox:
                create_path = '/openapi/v1/sandbox/job/create'
                create_payload = {'projectId': project_id, 'name': job_name}
            else:
                create_path = '/openapi/v1/job/create'
                create_payload = {'projectId': project_id, 'jobName': job_name}

            create_resp = _post(base_url, create_path, access_key, create_payload)
            if create_resp.get('code') != 0:
                return ToolResult(status='error', content=f'job/create failed: {create_resp}')
            create_data = create_resp['data']

            # Step 2: zip and upload
            with tempfile.TemporaryDirectory(prefix='bohrium_submit_') as tmp_dir:
                zip_path = Path(tmp_dir) / 'input.zip'
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for file_path in input_path.rglob('*'):
                        if file_path.is_file():
                            zf.write(file_path, file_path.relative_to(input_path))

                store_path = create_data['storePath']
                store_host = create_data['storeHost'].rstrip('/')
                token = create_data['token']
                oss_key = store_path + 'input.zip'

                try:
                    from bohrium_open_sdk.opensdk._tiefblue_client import (
                        Tiefblue as _TiefblueClient,
                    )
                except ImportError:
                    return ToolResult(
                        status='error',
                        content='bohrium_open_sdk not installed. Run: pip install bohrium_open_sdk',
                    )

                tf_client = _TiefblueClient(base_url=store_host)
                upload_resp = tf_client.upload_from_file_multi_part(
                    object_key=oss_key,
                    file_path=str(zip_path),
                    custom_headers={'Authorization': f'Bearer {token}'},
                    progress_bar=False,
                )
                if isinstance(upload_resp, dict) and upload_resp.get('code') not in (0, None):
                    return ToolResult(status='error', content=f'Upload failed: {upload_resp}')

            # Build download URL for sandbox
            encoded_key = quote(oss_key, safe='/')
            download_url = (
                f'{store_host}/api/download/{encoded_key}?token={token}'
                '&Response-Content-Type=application/octet-stream'
            )

            # Step 3: add (start) job
            if sandbox:
                create_job_id = str(create_data.get('jobId') or '').strip()
                if not create_job_id:
                    return ToolResult(status='error', content='sandbox job/create missing jobId')
                add_payload = {
                    'imageName': image,
                    'scassType': machine,
                    'jobName': job_name,
                    'cmd': cmd,
                    'jobId': create_job_id,
                    'ossPath': [download_url],
                }
                add_path = '/openapi/v1/sandbox/job/add'
            else:
                add_payload = {
                    'projectId': project_id,
                    'jobName': job_name,
                    'jobType': 'indicate',
                    'scassType': machine,
                    'cmd': cmd,
                    'imageName': image,
                    'ossPath': [oss_key],
                    'inputFileMethod': 1,
                    'inputFileType': 3,
                    'diskSize': disk_size,
                    'logFiles': ['log'],
                }
                add_path = '/openapi/v2/job/add'

            add_resp = _post(base_url, add_path, access_key, add_payload)
            if add_resp.get('code') != 0:
                return ToolResult(status='error', content=f'job/add failed: {add_resp}')
            add_data = add_resp['data']

            # Extract job_id
            if sandbox:
                raw_jid = add_data.get('jobId')
                if raw_jid is None:
                    return ToolResult(status='error', content='Missing jobId in response')
                job_id: int | str = str(raw_jid).strip()
                bohr_raw = add_data.get('bohrJobId')
                bohr_job_id = str(bohr_raw).strip() if bohr_raw not in (None, '', 0) else job_id
            else:
                job_id = int(add_data['jobId'])
                bohr_job_id = int(add_data.get('bohrJobId') or add_data['jobId'])

            return ToolResult(
                status='success',
                content=json.dumps({
                    'success': True,
                    'job_id': job_id,
                    'bohr_job_id': bohr_job_id,
                    'status': 'Submitted',
                    'use_sandbox': sandbox,
                }, ensure_ascii=False),
            )

        except Exception as exc:
            logger.error('bohrium submit failed: %s', exc, exc_info=True)
            return ToolResult(status='error', content=f'Submit failed: {exc}')

    # ------------------------------------------------------------------
    # action: poll (single-shot, non-blocking)
    # ------------------------------------------------------------------

    def _poll(self, args: dict[str, Any]) -> ToolResult:
        access_key, _, base_url = _resolve_env()
        if not access_key:
            return ToolResult(status='error', content='BOHRIUM_ACCESS_KEY not set.')

        raw_job_id = args.get('job_id')
        if raw_job_id is None:
            return ToolResult(status='error', content='Missing required parameter: job_id')

        sandbox = _use_sandbox()
        job_id: int | str = str(raw_job_id).strip() if sandbox else int(raw_job_id)
        result_dir_str = args.get('result_dir') or f'results/run_{job_id}'
        result_dir = Path(result_dir_str)

        try:
            # Single query
            if sandbox:
                detail_path = f'/openapi/v1/sandbox/job/{job_id}'
            else:
                detail_path = f'/openapi/v1/job/{job_id}'

            detail = _get(base_url, detail_path, access_key)
            detail_data = detail.get('data', {})
            code = detail_data.get('status', 0)
            status_name = _STATUS_MAP.get(code, f'Unknown({code})')

            # Still running
            if code in _RUNNING_CODES:
                return ToolResult(
                    status='success',
                    content=json.dumps({
                        'success': True,
                        'job_id': job_id,
                        'status': status_name,
                        'message': f'Job is {status_name}. Call Bohrium(action="poll", job_id={job_id}) again later to check.',
                    }, ensure_ascii=False),
                )

            # Finished — download results
            if code == _SUCCESS_CODE:
                files, log_tail = self._download_results(
                    job_id, detail_data, result_dir, access_key, base_url,
                )
                return ToolResult(
                    status='success',
                    content=json.dumps({
                        'success': True,
                        'job_id': job_id,
                        'status': 'Finished',
                        'result_dir': str(result_dir),
                        'files': files,
                        'log_tail': log_tail,
                    }, ensure_ascii=False),
                )

            # Failed — try downloading whatever is available
            if code in _FAILURE_CODES:
                files: list[str] = []
                log_tail = ''
                try:
                    files, log_tail = self._download_results(
                        job_id, detail_data, result_dir, access_key, base_url,
                    )
                except Exception:
                    pass
                return ToolResult(
                    status='error',
                    content=json.dumps({
                        'success': False,
                        'job_id': job_id,
                        'status': status_name,
                        'result_dir': str(result_dir) if files else '',
                        'files': files,
                        'log_tail': log_tail,
                        'error': f'Job {status_name} on Bohrium.',
                    }, ensure_ascii=False),
                )

            # Unknown status
            return ToolResult(
                status='success',
                content=json.dumps({
                    'success': True,
                    'job_id': job_id,
                    'status': status_name,
                    'message': f'Unexpected status code {code}. Retry poll or check Bohrium console.',
                }, ensure_ascii=False),
            )

        except Exception as exc:
            logger.error('bohrium poll failed: %s', exc, exc_info=True)
            return ToolResult(status='error', content=f'Poll failed: {exc}')

    def _download_results(
        self,
        job_id: int | str,
        detail_data: dict,
        result_dir: Path,
        access_key: str,
        base_url: str,
    ) -> tuple[list[str], str]:
        """Download and extract result artifacts. Returns (file_list, log_tail)."""
        result_dir.mkdir(parents=True, exist_ok=True)

        if _use_sandbox():
            return self._sandbox_download(job_id, result_dir, access_key, base_url)

        # Standard HPC: download resultUrl zip
        result_url = detail_data.get('resultUrl', '')
        if not result_url:
            return [], '(no resultUrl in job detail)'

        zip_path = result_dir / 'out.zip'
        resp = requests.get(result_url, timeout=300, stream=True)
        resp.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        files = self._extract_zip(zip_path, result_dir)
        log_tail = self._read_log(result_dir)
        return files, log_tail

    def _sandbox_download(
        self,
        job_id: int | str,
        result_dir: Path,
        access_key: str,
        base_url: str,
    ) -> tuple[list[str], str]:
        """Download results in sandbox mode (iterate objects, find zip, extract)."""
        # Get file token
        token_resp = _post(
            base_url,
            f'/openapi/v1/sandbox/job/file/token',
            access_key,
            {'jobId': str(job_id)},
        )
        if token_resp.get('code') != 0:
            return [], f'(file token request failed: {token_resp})'

        token_data = token_resp.get('data', {})
        store_host = token_data.get('storeHost', '').rstrip('/')
        token = token_data.get('token', '')
        prefix = token_data.get('storePath', '')

        if not (store_host and token and prefix):
            return [], '(incomplete file token response)'

        # List objects
        list_url = f'{store_host}/api/list'
        list_params = {'prefix': prefix, 'token': token, 'limit': 500}
        list_resp = requests.get(list_url, params=list_params, timeout=30)
        list_resp.raise_for_status()
        objects = list_resp.json().get('data', {}).get('list', [])

        # Find the output zip
        zip_key = None
        for obj in objects:
            key = obj.get('key', '')
            if key.endswith('.zip') and 'out' in key.lower():
                zip_key = key
                break
        if not zip_key:
            # Fallback: any zip
            for obj in objects:
                if obj.get('key', '').endswith('.zip'):
                    zip_key = obj['key']
                    break

        if not zip_key:
            return [], '(no zip file found in sandbox artifacts)'

        # Download zip
        encoded_key = quote(zip_key, safe='/')
        dl_url = (
            f'{store_host}/api/download/{encoded_key}?token={token}'
            '&Response-Content-Type=application/octet-stream'
        )
        zip_path = result_dir / 'out.zip'
        dl_resp = requests.get(dl_url, timeout=300, stream=True)
        dl_resp.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in dl_resp.iter_content(chunk_size=8192):
                f.write(chunk)

        files = self._extract_zip(zip_path, result_dir)
        log_tail = self._read_log(result_dir)
        return files, log_tail

    @staticmethod
    def _extract_zip(zip_path: Path, extract_dir: Path) -> list[str]:
        """Extract a zip and return list of extracted filenames."""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
                return zf.namelist()
        except zipfile.BadZipFile:
            return [f'(bad zip: {zip_path.name})']

    @staticmethod
    def _read_log(extract_dir: Path, max_chars: int = 4000) -> str:
        """Read log tail from extracted result directory."""
        for name in ('log', 'STDOUTERR'):
            f = extract_dir / name
            if f.exists():
                try:
                    text = f.read_text(encoding='utf-8', errors='replace')
                    return text[-max_chars:] if len(text) > max_chars else text
                except Exception:
                    continue
        return '(no log file found in result directory)'

    # ------------------------------------------------------------------
    # action: list_images
    # ------------------------------------------------------------------

    def _list_images(self, args: dict[str, Any]) -> ToolResult:
        access_key, _, base_url = _resolve_env()
        if not access_key:
            return ToolResult(status='error', content='BOHRIUM_ACCESS_KEY not set.')

        keyword = (args.get('keyword') or '').strip().lower()
        max_results = args.get('max_results', 20)

        try:
            # Fetch all public image IDs
            data = _get(
                base_url,
                '/openapi/v2/image/public',
                access_key,
                params={'page': 1, 'pageSize': 1000},
            )
            all_images = (data.get('data') or {}).get('items') or []

            # Filter by keyword
            if keyword:
                filtered = [
                    r for r in all_images
                    if keyword in str(r.get('name') or r.get('imageName') or '').lower()
                    or keyword in str(r.get('description') or '').lower()
                ]
            else:
                filtered = all_images

            # Fetch version details for each
            results = []
            for record in filtered[:max_results]:
                img_id = record.get('id') or record.get('imageId')
                if img_id is None:
                    continue
                try:
                    ver_data = _get(
                        base_url,
                        f'/openapi/v2/image/public/{img_id}/version',
                        access_key,
                        params={'current': 1, 'pageSize': 10, 'page': 1,
                                'resourceType': '', 'version': ''},
                    )
                    versions = (ver_data.get('data') or {}).get('items') or []
                except Exception:
                    versions = []

                version_list = []
                for v in versions:
                    entry: dict[str, Any] = {}
                    for key in ('url', 'version', 'resourceType', 'desc', 'size'):
                        val = v.get(key)
                        if val is not None and val != '':
                            entry[key] = val
                    if entry:
                        version_list.append(entry)

                results.append({
                    'id': img_id,
                    'name': record.get('name') or record.get('imageName') or '',
                    'versions': version_list,
                })

            return ToolResult(
                status='success',
                content=json.dumps({
                    'success': True,
                    'total_found': len(filtered),
                    'returned': len(results),
                    'images': results,
                }, ensure_ascii=False),
            )

        except Exception as exc:
            logger.error('bohrium list_images failed: %s', exc, exc_info=True)
            return ToolResult(status='error', content=f'list_images failed: {exc}')

    # ------------------------------------------------------------------
    # action: list_machines
    # ------------------------------------------------------------------

    def _list_machines(self, args: dict[str, Any]) -> ToolResult:
        access_key, _, base_url = _resolve_env()
        if not access_key:
            return ToolResult(status='error', content='BOHRIUM_ACCESS_KEY not set.')

        choose_type = args.get('machine_type', 'cpu')
        keyword = (args.get('keyword') or '').strip().lower()
        max_results = args.get('max_results', 50)

        try:
            data = _get(
                base_url,
                '/openapi/v1/calc/list',
                access_key,
                params={
                    'page': 1, 'pageSize': 512,
                    'scene': 'job', 'isVirtualNode': 'false',
                    'chooseType': choose_type,
                    'productLine': 'bohrium',
                },
            )
            all_machines = (data.get('data') or {}).get('items') or []

            if keyword:
                filtered = [
                    r for r in all_machines
                    if keyword in str(r.get('skuEnName') or r.get('skuName') or '').lower()
                ]
            else:
                filtered = all_machines

            results = []
            for record in filtered[:max_results]:
                entry: dict[str, Any] = {}
                for key in ('skuEnName', 'cpuCoreNum', 'memory',
                            'gpu', 'gpuCoreNum', 'price', 'hasStock'):
                    val = record.get(key)
                    if val is not None and val != '':
                        entry[key] = val
                if entry:
                    results.append(entry)

            return ToolResult(
                status='success',
                content=json.dumps({
                    'success': True,
                    'type': choose_type,
                    'total_found': len(filtered),
                    'returned': len(results),
                    'machines': results,
                }, ensure_ascii=False),
            )

        except Exception as exc:
            logger.error('bohrium list_machines failed: %s', exc, exc_info=True)
            return ToolResult(status='error', content=f'list_machines failed: {exc}')
