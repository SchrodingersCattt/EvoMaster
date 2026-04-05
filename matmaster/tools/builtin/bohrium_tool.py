"""matmaster/tools/builtin/bohrium_tool.py — Bohrium HPC platform tool.

Single tool with action-based dispatch for Bohrium HPC operations.
This tool handles pure communication: submit, poll (single-query), list_images,
list_machines. All software-specific knowledge lives in software skills.

Design decisions:
- poll is single-shot (non-blocking): returns current status, Agent controls retry
- submit auto-appends "> log 2>&1" if missing
- Credentials resolved via runtime bridge (session > env fallback)
- Remote /share paths require active session with upload_directory
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, Iterator, NamedTuple
from urllib.parse import quote
from uuid import uuid4

import requests

from matmaster.integration.runtime_bridge import resolve_output_path
from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bohrium API helpers (shared across actions)
# ---------------------------------------------------------------------------


def _use_sandbox() -> bool:
    return os.environ.get('BOHRIUM_USE_SANDBOX', '1').strip() == '1'


def _get(
    base_url: str,
    path: str,
    access_key: str,
    params: dict | None = None,
    timeout: int = 30,
) -> dict:
    url = f'{base_url}{path}'
    resp = requests.get(
        url,
        headers={'accessKey': access_key, 'Accept': 'application/json'},
        params=params or {},
        timeout=timeout,
    )
    if not getattr(resp, 'ok', True):
        _log_http_error('GET', url, resp)
    resp.raise_for_status()
    return resp.json()


def _post(
    base_url: str, path: str, access_key: str, payload: dict, timeout: int = 30
) -> dict:
    url = f'{base_url}{path}'
    resp = requests.post(
        url,
        headers={'accessKey': access_key, 'Content-Type': 'application/json'},
        json=payload,
        timeout=timeout,
    )
    if not getattr(resp, 'ok', True):
        _log_http_error('POST', url, resp)
    resp.raise_for_status()
    return resp.json()


def _mask_secret(secret: str) -> str:
    raw = (secret or '').strip()
    if not raw:
        return '(empty)'
    if len(raw) <= 4:
        return raw[0] + '...'
    return raw[:4] + '...'


def _compact_log_text(text: str, *, max_chars: int = 200) -> str:
    compact = ' '.join((text or '').split())
    if not compact:
        return '(empty)'
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + '...'


def _log_http_error(method: str, url: str, response: Any) -> None:
    logger.warning(
        'Bohrium HTTP error method=%s url=%s status=%s response_body=%s',
        method,
        url,
        getattr(response, 'status_code', 'unknown'),
        _compact_log_text(getattr(response, 'text', '')),
    )


# ---------------------------------------------------------------------------
# Status code mappings (shared with poll)
# ---------------------------------------------------------------------------

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


class _ResolvedBohriumContext(NamedTuple):
    access_key: str
    project_id: int
    base_url: str
    source: str


# ═══════════════════════════════════════════════════════════════════════════
# input_dir preparation helpers
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_bohrium_input_dir(
    *,
    input_dir: str,
    workdir: Path | None,
    session: Any | None,
) -> tuple[str, Path | str]:
    """Resolve submit input_dir into a validated local or remote directory."""
    decision = resolve_output_path(
        raw_path=input_dir,
        execution_workdir=str(workdir or '.'),
        session=session,
    )

    if decision.kind == 'remote_share':
        if session is None:
            raise ValueError(
                f"input_dir '{input_dir}' requires an active remote session, "
                'but none is available'
            )
        if not getattr(session, 'is_open', False):
            raise ValueError(
                f"input_dir '{input_dir}' requires an open remote session, "
                'but the current session is not open'
            )

        remote_dir = decision.normalized_path
        if not session.path_exists(remote_dir):
            raise ValueError(f'Remote input_dir not found: {remote_dir}')
        if session.is_file(remote_dir):
            raise ValueError(f'Remote input_dir is not a directory: {remote_dir}')
        return decision.kind, remote_dir

    local_dir = Path(decision.normalized_path)
    if not local_dir.exists():
        raise ValueError(f'input_dir not found: {input_dir}')
    if not local_dir.is_dir():
        raise ValueError(f'input_dir is not a directory: {input_dir}')
    return decision.kind, local_dir


def _zip_local_input_dir(input_dir: Path, zip_path: Path) -> None:
    """Create input.zip from a local input directory."""
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in input_dir.rglob('*'):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(input_dir))


def _remote_zip_command(input_dir: str, remote_zip_path: str) -> str:
    """Build a remote python3 command that packages input_dir into a zip file."""
    return (
        "python3 - <<'PY'\n"
        "import pathlib\n"
        "import zipfile\n\n"
        f"source = pathlib.Path({json.dumps(input_dir)})\n"
        f"archive = pathlib.Path({json.dumps(remote_zip_path)})\n"
        "archive.parent.mkdir(parents=True, exist_ok=True)\n"
        "with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:\n"
        "    for path in source.rglob('*'):\n"
        "        if path.is_file():\n"
        "            zf.write(path, path.relative_to(source))\n"
        "print(archive)\n"
        "PY"
    )


def _prepare_remote_input_zip(
    *,
    input_dir: str,
    session: Any,
    zip_path: Path,
) -> None:
    """Package a remote input directory and download the resulting zip locally."""
    remote_zip_path = f'/tmp/bohrium_input_{uuid4().hex}.zip'
    cleanup_cmd = f'rm -f {remote_zip_path}'

    try:
        result = session.exec_bash(_remote_zip_command(input_dir, remote_zip_path))
        if result.get('exit_code') != 0:
            detail = str(
                result.get('stderr')
                or result.get('output')
                or result.get('stdout')
                or 'unknown error'
            ).strip()
            raise RuntimeError(
                f"Failed to package remote input_dir '{input_dir}': {detail}"
            )

        try:
            zip_bytes = session.download(remote_zip_path)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to download remote input_dir '{input_dir}': {exc}"
            ) from exc
        zip_path.write_bytes(zip_bytes)
    finally:
        try:
            session.exec_bash(cleanup_cmd)
        except Exception:
            logger.warning(
                'Failed to clean up temporary remote input zip %s',
                remote_zip_path,
                exc_info=True,
            )


@contextmanager
def prepare_bohrium_input_zip(
    *,
    input_dir: str,
    workdir: Path | None,
    session: Any | None,
) -> Iterator[Path]:
    """Yield a local input.zip for Bohrium submit from local or remote input_dir."""
    input_kind, resolved_input = _resolve_bohrium_input_dir(
        input_dir=input_dir,
        workdir=workdir,
        session=session,
    )

    with tempfile.TemporaryDirectory(prefix='bohrium_submit_') as tmp_dir:
        zip_path = Path(tmp_dir) / 'input.zip'
        if input_kind == 'remote_share':
            _prepare_remote_input_zip(
                input_dir=str(resolved_input),
                session=session,
                zip_path=zip_path,
            )
        else:
            _zip_local_input_dir(Path(resolved_input), zip_path)
        yield zip_path


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
    capabilities: ClassVar[frozenset[str]] = frozenset(
        {
            'bohrium.submit',
            'bohrium.query',
        }
    )
    effect_level: ClassVar[str] = 'external_effect'
    fast_path_eligible: ClassVar[bool] = False
    plane: ClassVar[ToolPlane] = ToolPlane.EXTERNAL_SERVICE
    state_mode: ClassVar[str] = 'stateless'
    stop_mode: ClassVar[str] = 'cancellable'
    exposed_to_model: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 0

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

    def _resolve_credentials(self) -> _ResolvedBohriumContext:
        """Resolve Bohrium credentials via runtime bridge.

        Precedence: explicit > session > env > none.
        Returns resolved credential context for diagnostics and requests.
        """
        from matmaster.integration.runtime_bridge.adapters.bohrium import (
            resolve_bohrium_credentials,
        )

        cred = resolve_bohrium_credentials(session=self._session)
        access_key = str(cred.values.get("access_key") or "").strip()
        project_id_raw = cred.values.get("project_id")
        try:
            project_id = int(project_id_raw) if project_id_raw is not None else -1
        except (TypeError, ValueError):
            project_id = -1
        base_url = str(cred.values.get("base_url") or "").strip()
        if not base_url:
            from matmaster.integration.bohrium_env import BOHRIUM_OPENAPI_HOST

            base_url = BOHRIUM_OPENAPI_HOST
        return _ResolvedBohriumContext(
            access_key=access_key,
            project_id=project_id,
            base_url=base_url,
            source=cred.source,
        )

    def _log_request_context(
        self,
        *,
        action: str,
        ctx: _ResolvedBohriumContext,
        sandbox: bool | None,
    ) -> None:
        logger.info(
            'Bohrium request context action=%s source=%s base_url=%s '
            'project_id=%s sandbox=%s service_env=%s access_key=%s',
            action,
            ctx.source,
            ctx.base_url,
            ctx.project_id,
            sandbox if sandbox is not None else 'n/a',
            (os.getenv('SERVICE_ENV', 'test') or '').strip() or 'test',
            _mask_secret(ctx.access_key),
        )

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

    def _submit(self, args: dict[str, Any]) -> ToolResult:
        ctx = self._resolve_credentials()
        access_key = ctx.access_key
        project_id = ctx.project_id
        base_url = ctx.base_url
        if not access_key:
            return ToolResult(
                status='error',
                content='Bohrium credentials unavailable. '
                'Provide via session or BOHRIUM_ACCESS_KEY env var.',
            )
        if project_id <= 0:
            return ToolResult(
                status='error',
                content='Bohrium project ID unavailable. '
                'Provide via session or BOHRIUM_PROJECT_ID env var.',
            )

        input_dir = args.get('input_dir', '')
        image = args.get('image', '')
        cmd = args.get('cmd', '')

        if not input_dir:
            return ToolResult(
                status='error', content='Missing required parameter: input_dir'
            )
        if not image:
            return ToolResult(
                status='error', content='Missing required parameter: image'
            )
        if not cmd:
            return ToolResult(status='error', content='Missing required parameter: cmd')

        machine = args.get('machine', 'c32_m128_cpu')
        job_name = args.get('job_name', 'matmaster-job')
        disk_size = args.get('disk_size', 50)

        # Auto-append log redirection
        cmd_stripped = cmd.rstrip()
        if not cmd_stripped.endswith('> log 2>&1'):
            cmd = cmd_stripped + ' > log 2>&1'

        sandbox = _use_sandbox()
        self._log_request_context(action='submit', ctx=ctx, sandbox=sandbox)

        try:
            with prepare_bohrium_input_zip(
                input_dir=input_dir,
                workdir=self._workdir,
                session=self._session,
            ) as zip_path:
                # Step 1: create job
                if sandbox:
                    create_path = '/openapi/v1/sandbox/job/create'
                    create_payload = {'projectId': project_id, 'name': job_name}
                else:
                    create_path = '/openapi/v1/job/create'
                    create_payload = {'projectId': project_id, 'jobName': job_name}

                create_resp = _post(base_url, create_path, access_key, create_payload)
                if create_resp.get('code') != 0:
                    return ToolResult(
                        status='error', content=f'job/create failed: {create_resp}'
                    )
                create_data = create_resp['data']

                # Step 2: upload prepared input.zip
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
                if isinstance(upload_resp, dict) and upload_resp.get('code') not in (
                    0,
                    None,
                ):
                    return ToolResult(
                        status='error', content=f'Upload failed: {upload_resp}'
                    )

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
                    return ToolResult(
                        status='error', content='sandbox job/create missing jobId'
                    )
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
                    return ToolResult(
                        status='error', content='Missing jobId in response'
                    )
                job_id: int | str = str(raw_jid).strip()
                bohr_raw = add_data.get('bohrJobId')
                bohr_job_id = (
                    str(bohr_raw).strip() if bohr_raw not in (None, '', 0) else job_id
                )
            else:
                job_id = int(add_data['jobId'])
                bohr_job_id = int(add_data.get('bohrJobId') or add_data['jobId'])

            return ToolResult(
                status='success',
                content=json.dumps(
                    {
                        'success': True,
                        'job_id': job_id,
                        'bohr_job_id': bohr_job_id,
                        'status': 'Submitted',
                        'use_sandbox': sandbox,
                    },
                    ensure_ascii=False,
                ),
            )

        except (ValueError, RuntimeError) as exc:
            return ToolResult(status='error', content=str(exc))
        except Exception as exc:
            logger.error(
                'bohrium submit failed action=submit base_url=%s sandbox=%s error=%s',
                base_url,
                sandbox,
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'Submit failed: {exc}')

    def _poll(self, args: dict[str, Any]) -> ToolResult:
        ctx = self._resolve_credentials()
        access_key = ctx.access_key
        base_url = ctx.base_url
        if not access_key:
            return ToolResult(
                status='error',
                content='Bohrium credentials unavailable. '
                'Provide via session or BOHRIUM_ACCESS_KEY env var.',
            )

        raw_job_id = args.get('job_id')
        if raw_job_id is None:
            return ToolResult(
                status='error', content='Missing required parameter: job_id'
            )

        sandbox = _use_sandbox()
        self._log_request_context(action='poll', ctx=ctx, sandbox=sandbox)
        job_id: int | str = str(raw_job_id).strip() if sandbox else int(raw_job_id)
        result_dir_str = args.get('result_dir') or f'results/run_{job_id}'

        decision = resolve_output_path(
            raw_path=result_dir_str,
            execution_workdir=str(self._workdir or '.'),
            session=self._session,
        )
        if decision.requires_remote_session:
            return ToolResult(
                status='error',
                content=f"result_dir '{result_dir_str}' requires an active remote session "
                'but none is available. Use a local path instead.',
            )

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
                    content=json.dumps(
                        {
                            'success': True,
                            'job_id': job_id,
                            'status': status_name,
                            'message': f'Job is {status_name}. Call Bohrium(action="poll", job_id={job_id}) again later to check.',
                        },
                        ensure_ascii=False,
                    ),
                )

            # Finished — download results
            if code == _SUCCESS_CODE:
                files, log_tail = self._download_results(
                    job_id,
                    detail_data,
                    result_dir,
                    access_key,
                    base_url,
                )

                # Remote share: upload local results to remote session
                report_dir = str(result_dir)
                if (
                    decision.kind == 'remote_share'
                    and self._session is not None
                    and hasattr(self._session, 'upload_directory')
                ):
                    try:
                        self._session.upload_directory(str(result_dir), result_dir_str)
                        report_dir = result_dir_str
                    except Exception as upload_exc:
                        logger.warning(
                            'Failed to upload results to remote share %s: %s',
                            result_dir_str,
                            upload_exc,
                        )

                return ToolResult(
                    status='success',
                    content=json.dumps(
                        {
                            'success': True,
                            'job_id': job_id,
                            'status': 'Finished',
                            'result_dir': report_dir,
                            'files': files,
                            'log_tail': log_tail,
                        },
                        ensure_ascii=False,
                    ),
                )

            # Failed — try downloading whatever is available
            if code in _FAILURE_CODES:
                files: list[str] = []
                log_tail = ''
                try:
                    files, log_tail = self._download_results(
                        job_id,
                        detail_data,
                        result_dir,
                        access_key,
                        base_url,
                    )
                except Exception:
                    pass
                return ToolResult(
                    status='error',
                    content=json.dumps(
                        {
                            'success': False,
                            'job_id': job_id,
                            'status': status_name,
                            'result_dir': str(result_dir) if files else '',
                            'files': files,
                            'log_tail': log_tail,
                            'error': f'Job {status_name} on Bohrium.',
                        },
                        ensure_ascii=False,
                    ),
                )

            # Unknown status
            return ToolResult(
                status='success',
                content=json.dumps(
                    {
                        'success': True,
                        'job_id': job_id,
                        'status': status_name,
                        'message': f'Unexpected status code {code}. Retry poll or check Bohrium console.',
                    },
                    ensure_ascii=False,
                ),
            )

        except Exception as exc:
            logger.error(
                'bohrium poll failed action=poll base_url=%s sandbox=%s error=%s',
                base_url,
                sandbox,
                exc,
                exc_info=True,
            )
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
            '/openapi/v1/sandbox/job/file/token',
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

    def _list_images(self, args: dict[str, Any]) -> ToolResult:
        ctx = self._resolve_credentials()
        access_key = ctx.access_key
        base_url = ctx.base_url
        if not access_key:
            return ToolResult(
                status='error',
                content='Bohrium credentials unavailable. '
                'Provide via session or BOHRIUM_ACCESS_KEY env var.',
            )

        keyword = (args.get('keyword') or '').strip().lower()
        max_results = args.get('max_results', 20)
        self._log_request_context(action='list_images', ctx=ctx, sandbox=None)

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
                    r
                    for r in all_images
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
                        params={
                            'current': 1,
                            'pageSize': 10,
                            'page': 1,
                            'resourceType': '',
                            'version': '',
                        },
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

                results.append(
                    {
                        'id': img_id,
                        'name': record.get('name') or record.get('imageName') or '',
                        'versions': version_list,
                    }
                )

            return ToolResult(
                status='success',
                content=json.dumps(
                    {
                        'success': True,
                        'total_found': len(filtered),
                        'returned': len(results),
                        'images': results,
                    },
                    ensure_ascii=False,
                ),
            )

        except Exception as exc:
            logger.error(
                'bohrium list_images failed action=list_images base_url=%s error=%s',
                base_url,
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'list_images failed: {exc}')

    def _list_machines(self, args: dict[str, Any]) -> ToolResult:
        ctx = self._resolve_credentials()
        access_key = ctx.access_key
        base_url = ctx.base_url
        if not access_key:
            return ToolResult(
                status='error',
                content='Bohrium credentials unavailable. '
                'Provide via session or BOHRIUM_ACCESS_KEY env var.',
            )

        choose_type = args.get('machine_type', 'cpu')
        keyword = (args.get('keyword') or '').strip().lower()
        max_results = args.get('max_results', 50)
        self._log_request_context(action='list_machines', ctx=ctx, sandbox=None)

        try:
            data = _get(
                base_url,
                '/openapi/v1/calc/list',
                access_key,
                params={
                    'page': 1,
                    'pageSize': 512,
                    'scene': 'job',
                    'isVirtualNode': 'false',
                    'chooseType': choose_type,
                    'productLine': 'bohrium',
                },
            )
            all_machines = (data.get('data') or {}).get('items') or []

            if keyword:
                filtered = [
                    r
                    for r in all_machines
                    if keyword
                    in str(r.get('skuEnName') or r.get('skuName') or '').lower()
                ]
            else:
                filtered = all_machines

            results = []
            for record in filtered[:max_results]:
                entry: dict[str, Any] = {}
                for key in (
                    'skuEnName',
                    'cpuCoreNum',
                    'memory',
                    'gpu',
                    'gpuCoreNum',
                    'price',
                    'hasStock',
                ):
                    val = record.get(key)
                    if val is not None and val != '':
                        entry[key] = val
                if entry:
                    results.append(entry)

            return ToolResult(
                status='success',
                content=json.dumps(
                    {
                        'success': True,
                        'type': choose_type,
                        'total_found': len(filtered),
                        'returned': len(results),
                        'machines': results,
                    },
                    ensure_ascii=False,
                ),
            )

        except Exception as exc:
            logger.error(
                'bohrium list_machines failed action=list_machines '
                'base_url=%s machine_type=%s error=%s',
                base_url,
                choose_type,
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'list_machines failed: {exc}')
