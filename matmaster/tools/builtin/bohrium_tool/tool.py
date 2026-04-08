"""Bohrium builtin tool orchestration.

Single tool with action-based dispatch for Bohrium HPC operations.
This tool handles pure communication: submit, poll (single-query), list_images,
list_machines. All software-specific knowledge lives in software skills.

Design decisions:
- poll is single-shot and non-blocking
- submit auto-appends "> log 2>&1" if missing
- Credentials resolved via runtime bridge (session > env fallback)
- Remote /share paths require active session with upload_directory
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar

from matmaster.bohrium.credentials import credentials_from_env
from matmaster.bohrium.endpoints import (
    get_bohrium_base_url,
    get_bohrium_service_env,
)
from matmaster.bohrium.runtime import get_runtime
from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.bohrium_tool.registry import JobRegistry, next_interval
from matmaster.tools.tool_result import ToolResult, normalize_tool_result
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .api import (
    _FAILURE_CODES,
    _RUNNING_CODES,
    _STATUS_MAP,
    _SUCCESS_CODE,
    _get,
    _mask_secret,
    add_job,
    confirm_terminal_status,
    create_job,
    get_job_detail,
    use_sandbox,
)
from .errors import BohriumError, BohriumJobStateError
from .models import BohriumContext
from .open_sdk import upload_input_archive
from .paths import resolve_download_target, resolve_input_source
from .transfers import (
    download_job_artifacts,
    prepare_input_archive,
    publish_download_target,
)

logger = logging.getLogger(__name__)


def build_bohrium_context(*, session, require_project: bool = False) -> BohriumContext:
    runtime = get_runtime(session) if session is not None else None
    if runtime is not None:
        cred = runtime.credentials()
        source = "runtime"
    else:
        cred = credentials_from_env()
        if cred is None:
            raise BohriumError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )
        source = "env"

    ctx = BohriumContext.from_credentials(
        cred,
        sandbox=use_sandbox(),
        source=source,
    )
    if require_project and ctx.project_id <= 0:
        raise BohriumError(
            "Bohrium project ID unavailable. Provide via session or BOHRIUM_PROJECT_ID."
        )
    if not ctx.base_url:
        ctx = BohriumContext(
            access_key=ctx.access_key,
            project_id=ctx.project_id,
            base_url=get_bohrium_base_url(),
            credential_source=ctx.credential_source,
            sandbox=ctx.sandbox,
            user_id=ctx.user_id,
            user_no=ctx.user_no,
        )
    return ctx


def submit_job_via_runtime(
    *,
    input_dir: str | Path,
    image: str,
    cmd: str,
    machine: str,
    job_name: str,
    disk_size: int,
    workdir: Path,
    session,
) -> tuple[int | str, int | str]:
    ctx = build_bohrium_context(session=session, require_project=True)
    source = resolve_input_source(
        raw_path=str(input_dir),
        workdir=workdir,
        session=session,
    )

    cmd_stripped = cmd.rstrip()
    if not cmd_stripped.endswith("> log 2>&1"):
        cmd = cmd_stripped + " > log 2>&1"

    with prepare_input_archive(source, session=session) as zip_path:
        create_data = create_job(ctx, job_name=job_name)
        upload = upload_input_archive(create_data=create_data, zip_path=zip_path)
        add_data = add_job(
            ctx,
            create_data=create_data,
            upload=upload,
            image=image,
            cmd=cmd,
            machine=machine,
            job_name=job_name,
            disk_size=disk_size,
        )

    if ctx.sandbox:
        raw_jid = add_data.get("jobId")
        if raw_jid is None:
            raise BohriumError("Missing jobId in sandbox add response")
        job_id: int | str = str(raw_jid).strip()
        bohr_raw = add_data.get("bohrJobId")
        bohr_job_id = str(bohr_raw).strip() if bohr_raw not in (None, "", 0) else job_id
        return job_id, bohr_job_id

    job_id = int(add_data["jobId"])
    bohr_job_id = int(add_data.get("bohrJobId") or add_data["jobId"])
    return job_id, bohr_job_id


# ═══════════════════════════════════════════════════════════════════════════
# BohriumTool
# ═══════════════════════════════════════════════════════════════════════════


class BohriumTool(BuiltinTool):
    """Bohrium HPC platform operations via action-based dispatch."""

    name: ClassVar[str] = 'Bohrium'
    description: ClassVar[str] = (
        'Bohrium HPC platform operations. '
        'action="submit": package input directory and submit a job, returns job_id. '
        'action="poll": single-shot job status check, returns current status immediately. '
        'action="download": download artifacts for a finished or failed job into result_dir. '
        'action="list_images": query available Docker images by keyword. '
        'action="list_machines": query available machine types (cpu/gpu).'
    )

    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'action': {
                'type': 'string',
                'enum': ['submit', 'poll', 'download', 'list_images', 'list_machines'],
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
                'description': 'Job ID returned by submit. (poll, download)',
            },
            'result_dir': {
                'type': 'string',
                'description': 'Directory where downloaded artifacts will be stored. (download)',
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
            'bohrium.download',
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
            '- poll: single-shot status query only. It returns immediately and does '
            'not download artifacts.\n'
            '- download: use action="download" only after poll reports Finished or '
            'Failed. Requires result_dir; retrieves logs and artifacts for analysis.\n'
            '- When image or machine is unknown, call list_images / list_machines first.\n'
        )

    def _build_context(self, *, require_project: bool = False) -> BohriumContext:
        return build_bohrium_context(
            session=self._session,
            require_project=require_project,
        )

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: Any,
    ) -> str | ToolResult:
        """Registry-aware execution with async-side throttle checks."""
        import asyncio

        action = arguments.get("action")
        registry: JobRegistry | None = None
        if exec_ctx is not None and hasattr(exec_ctx, "runner_state"):
            runner_state = exec_ctx.runner_state
            if runner_state is not None and hasattr(runner_state, "get"):
                registry = runner_state.get("bohrium_job_registry")

        if action == "poll" and registry is not None:
            job_id = str(arguments.get("job_id", ""))
            throttled, remaining = registry.should_throttle(job_id)
            if throttled:
                rec = registry.get(job_id)
                payload: dict[str, Any]
                if rec is not None and rec.last_result:
                    try:
                        payload = json.loads(rec.last_result)
                    except (json.JSONDecodeError, TypeError):
                        payload = {}
                else:
                    payload = {}
                payload.update(
                    {
                        "success": True,
                        "job_id": job_id,
                        "status": payload.get(
                            "status",
                            rec.status.title() if rec is not None else "Unknown",
                        ),
                        "cached": True,
                        "seconds_until_fresh": remaining,
                        "message": (
                            f"Cached status. Fresh check available in {remaining}s. "
                            "Do other work first."
                        ),
                    }
                )
                return ToolResult(
                    status="success",
                    content=json.dumps(payload, ensure_ascii=False),
                )

        try:
            result = await asyncio.to_thread(self._execute, arguments)
        except Exception as exc:
            self.logger.error("Tool %s failed: %s", self.name, exc, exc_info=True)
            return f"Error: {exc}"

        normalized = normalize_tool_result(result)
        if registry is not None and action in ("submit", "poll", "download"):
            normalized = self._update_registry(
                registry,
                action,
                arguments,
                normalized,
            )

        return normalized

    def _update_registry(
        self,
        registry: JobRegistry,
        action: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        """Update the in-memory registry after a successful tool call."""
        if result.status == "error":
            return result

        try:
            data = json.loads(result.content)
        except (json.JSONDecodeError, TypeError):
            return result

        job_id = str(data.get("job_id", arguments.get("job_id", "")))
        if not job_id:
            return result

        if action == "submit":
            registry.register(job_id, job_name=str(arguments.get("job_name", "")))
            return result

        if action == "download":
            registry.update_download(job_id)
            return result

        if action != "poll":
            return result

        status_name = str(data.get("status", "unknown")).lower()
        if status_name == "finished":
            reg_status = "finished"
        elif status_name == "failed":
            reg_status = "failed"
        else:
            reg_status = "running"

        registry.update_poll(job_id, status=reg_status, result=result.content)

        if reg_status != "running":
            return result

        rec = registry.get(job_id)
        if rec is None:
            return result

        interval = next_interval(rec.poll_count - 1)
        data["next_check_seconds"] = interval
        data["message"] = (
            f'Job is {data.get("status", "Running")}. '
            f"Suggested next check in {interval}s. "
            "Continue other work before polling again."
        )
        updated = ToolResult(
            status=result.status,
            content=json.dumps(data, ensure_ascii=False),
            payload=result.payload,
            meta=result.meta,
        )
        rec.last_result = updated.content
        return updated

    def _log_request_context(
        self,
        *,
        action: str,
        ctx: BohriumContext,
        sandbox: bool | None,
    ) -> None:
        logger.info(
            'Bohrium request context action=%s source=%s base_url=%s '
            'project_id=%s sandbox=%s service_env=%s access_key=%s',
            action,
            ctx.credential_source,
            ctx.base_url,
            ctx.project_id,
            sandbox if sandbox is not None else 'n/a',
            get_bohrium_service_env(),
            _mask_secret(ctx.access_key),
        )

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        action = arguments.get('action', '')
        match action:
            case 'submit':
                return self._submit(arguments)
            case 'poll':
                return self._poll(arguments)
            case 'download':
                return self._download(arguments)
            case 'list_images':
                return self._list_images(arguments)
            case 'list_machines':
                return self._list_machines(arguments)
            case _:
                return ToolResult(
                    status='error',
                    content=f'Unknown action: {action!r}. '
                    f'Must be one of: submit, poll, download, list_images, list_machines.',
                )

    def _submit(self, args: dict[str, Any]) -> ToolResult:
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
        disk_size = int(args.get('disk_size', 50))

        ctx: BohriumContext | None = None
        sandbox: bool | str = 'n/a'
        try:
            ctx = self._build_context(require_project=True)
            sandbox = ctx.sandbox
            self._log_request_context(action='submit', ctx=ctx, sandbox=sandbox)
            job_id, bohr_job_id = submit_job_via_runtime(
                input_dir=str(input_dir),
                image=str(image),
                cmd=str(cmd),
                machine=str(machine),
                job_name=str(job_name),
                disk_size=disk_size,
                workdir=self._workdir or Path("."),
                session=self._session,
            )
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
        except (BohriumError, ValueError) as exc:
            return ToolResult(status='error', content=str(exc))
        except Exception as exc:
            logger.error(
                'bohrium submit failed action=submit base_url=%s sandbox=%s error=%s',
                ctx.base_url if ctx is not None else '',
                sandbox,
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'Submit failed: {exc}')

    def _poll(self, args: dict[str, Any]) -> ToolResult:
        raw_job_id = args.get('job_id')
        if raw_job_id is None:
            return ToolResult(
                status='error', content='Missing required parameter: job_id'
            )

        if args.get('result_dir'):
            return ToolResult(
                status='error',
                content=(
                    'poll no longer downloads artifacts. '
                    f'Use Bohrium(action="download", job_id={raw_job_id!r}, '
                    f'result_dir="results/run_{raw_job_id}") instead.'
                ),
            )

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            sandbox = ctx.sandbox
            self._log_request_context(action='poll', ctx=ctx, sandbox=sandbox)
            job_id: int | str = str(raw_job_id).strip() if sandbox else int(raw_job_id)
            detail_data = get_job_detail(ctx, job_id=job_id)
            code = detail_data.get('status', 0)
            status_name = _STATUS_MAP.get(code, f'Unknown({code})')

            if code in _RUNNING_CODES:
                return ToolResult(
                    status='success',
                    content=json.dumps(
                        {
                            'success': True,
                            'job_id': job_id,
                            'status': status_name,
                            'message': (
                                f'Job is {status_name}. '
                                'Continue other work before polling again.'
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )

            if code == _SUCCESS_CODE:
                return ToolResult(
                    status='success',
                    content=json.dumps(
                        {
                            'success': True,
                            'job_id': job_id,
                            'status': 'Finished',
                            'message': (
                                'Job is Finished. Call '
                                f'Bohrium(action="download", job_id={job_id!r}, '
                                f'result_dir="results/run_{job_id}") '
                                'to retrieve artifacts.'
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )

            if code in _FAILURE_CODES:
                return ToolResult(
                    status='success',
                    content=json.dumps(
                        {
                            'success': True,
                            'job_id': job_id,
                            'status': status_name,
                            'message': (
                                'Job is Failed. Call '
                                f'Bohrium(action="download", job_id={job_id!r}, '
                                f'result_dir="results/run_{job_id}") '
                                'to retrieve logs and artifacts.'
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )

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
                ctx.base_url if ctx is not None else '',
                ctx.sandbox if ctx is not None else 'n/a',
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'Poll failed: {exc}')

    def _download(self, args: dict[str, Any]) -> ToolResult:
        raw_job_id = args.get('job_id')
        if raw_job_id is None:
            return ToolResult(
                status='error', content='Missing required parameter: job_id'
            )

        result_dir_str = str(args.get('result_dir') or '').strip()
        if not result_dir_str:
            return ToolResult(
                status='error',
                content='Missing required parameter: result_dir',
            )

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            sandbox = ctx.sandbox
            self._log_request_context(action='download', ctx=ctx, sandbox=sandbox)
            job_id: int | str = str(raw_job_id).strip() if sandbox else int(raw_job_id)
            target = resolve_download_target(
                raw_path=result_dir_str,
                workdir=self._workdir,
                session=self._session,
            )
            detail_data = get_job_detail(ctx, job_id=job_id)
            code, status_name, detail_data = confirm_terminal_status(
                ctx,
                job_id=job_id,
                detail_data=detail_data,
            )
            if code in _RUNNING_CODES:
                return ToolResult(
                    status='error',
                    content=(
                        f'Job is {status_name}. '
                        'download is only available after terminal status.'
                    ),
                )

            if code not in _FAILURE_CODES and code != _SUCCESS_CODE:
                raise BohriumJobStateError(
                    f'Unexpected job status: {status_name} (code={code})'
                )

            files, log_tail = download_job_artifacts(
                job_id=job_id,
                detail_data=detail_data,
                target=target,
                ctx=ctx,
            )
            report_dir = publish_download_target(target, session=self._session)

            if code == _SUCCESS_CODE:
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

            return ToolResult(
                status='error',
                content=json.dumps(
                    {
                        'success': False,
                        'job_id': job_id,
                        'status': status_name,
                        'result_dir': report_dir,
                        'files': files,
                        'log_tail': log_tail,
                        'error': f'Job {status_name}.',
                    },
                    ensure_ascii=False,
                ),
            )
        except BohriumError as exc:
            return ToolResult(status='error', content=str(exc))
        except Exception as exc:
            logger.error(
                'bohrium download failed action=download base_url=%s error=%s',
                ctx.base_url if ctx is not None else '',
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'Download failed: {exc}')

    def _list_images(self, args: dict[str, Any]) -> ToolResult:
        keyword = (args.get('keyword') or '').strip().lower()
        max_results = args.get('max_results', 20)

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            self._log_request_context(action='list_images', ctx=ctx, sandbox=None)
            data = _get(
                ctx.base_url,
                '/openapi/v2/image/public',
                ctx.access_key,
                params={'page': 1, 'pageSize': 1000},
            )
            all_images = (data.get('data') or {}).get('items') or []

            if keyword:
                filtered = [
                    r
                    for r in all_images
                    if keyword in str(r.get('name') or r.get('imageName') or '').lower()
                    or keyword in str(r.get('description') or '').lower()
                ]
            else:
                filtered = all_images

            # Prepare fetch targets
            to_fetch: list[tuple[Any, str]] = []
            for record in filtered[:max_results]:
                img_id = record.get('id') or record.get('imageId')
                if img_id is not None:
                    name = record.get('name') or record.get('imageName') or ''
                    to_fetch.append((img_id, name))

            def _fetch_versions(item: tuple[Any, str]) -> dict[str, Any]:
                img_id, name = item
                try:
                    ver_data = _get(
                        ctx.base_url,
                        f'/openapi/v2/image/public/{img_id}/version',
                        ctx.access_key,
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

                return {'id': img_id, 'name': name, 'versions': version_list}

            # Parallel version queries to avoid N+1 latency
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(_fetch_versions, to_fetch))

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
                ctx.base_url if ctx is not None else '',
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'list_images failed: {exc}')

    def _list_machines(self, args: dict[str, Any]) -> ToolResult:
        choose_type = args.get('machine_type', 'cpu')
        keyword = (args.get('keyword') or '').strip().lower()
        max_results = args.get('max_results', 50)

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            self._log_request_context(action='list_machines', ctx=ctx, sandbox=None)
            data = _get(
                ctx.base_url,
                '/openapi/v1/calc/list',
                ctx.access_key,
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
                ctx.base_url if ctx is not None else '',
                choose_type,
                exc,
                exc_info=True,
            )
            return ToolResult(status='error', content=f'list_machines failed: {exc}')
