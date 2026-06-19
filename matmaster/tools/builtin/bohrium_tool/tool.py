"""Bohrium builtin tool orchestration.

Single tool with action-based dispatch for Bohrium HPC operations.
This tool handles pure communication: submit, query (single-shot status),
download, kill, list_images, list_machines. All software-specific knowledge
lives in software skills.

Design decisions:
- query returns the job's current status in a single call; long-running
  monitoring lives in the separate monitor process, not in the agent
- submit auto-appends "> log 2>&1" if missing
- kill is asynchronous; callers must query to confirm terminal state
- Credentials resolved via runtime bridge (session > env fallback)
- Remote /share paths require active session with upload_directory
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, ClassVar

from matmaster.bohrium.artifacts import download_job_artifacts
from matmaster.bohrium.client import (
    add_job,
    confirm_terminal_status,
    create_job,
    get_file_token,
    get_job_detail,
    list_images,
    list_machines,
    mask_secret,
    terminate_job,
)
from matmaster.bohrium.credentials import build_bohrium_context
from matmaster.bohrium.endpoints import get_bohrium_service_env
from matmaster.bohrium.errors import BohriumError, BohriumTransferError
from matmaster.bohrium.status import (
    FAILURE_CODES,
    RUNNING_CODES,
    SUCCESS_CODE,
    status_name,
)
from matmaster.bohrium.types import BohriumContext
from matmaster.bohrium.upload import upload_input_archive
from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult, normalize_tool_result
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .errors import BohriumJobStateError
from .models import BohriumSubmittedJob
from .paths import resolve_download_target, resolve_input_source
from .submit_review import (
    BohriumSubmitReviewProvider,
    normalize_execution_args,
)
from .transfers import (
    download_remote_results,
    prepare_input_archive,
    publish_download_target,
    upload_input_source,
)

logger = logging.getLogger(__name__)


def _created_job_ref(create_data: dict[str, Any]) -> str:
    for key in ("jobId", "id"):
        value = create_data.get(key)
        if value not in (None, ""):
            return str(value)
    return "(unknown)"


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
) -> BohriumSubmittedJob:
    if not cmd.rstrip().endswith("> log 2>&1"):
        raise BohriumError(
            "cmd not normalized (missing log redirection); "
            "normalize_execution_args must run before submit_job_via_runtime"
        )

    ctx = build_bohrium_context(session=session, require_project=True)
    source = resolve_input_source(
        raw_path=str(input_dir),
        workdir=workdir,
        session=session,
    )

    cmd = cmd.rstrip()

    if source.kind == "remote_share_dir":
        create_data = create_job(ctx, job_name=job_name)
        try:
            upload = upload_input_source(
                source,
                create_data=create_data,
                session=session,
            )
        except Exception as exc:
            created_ref = _created_job_ref(create_data)
            error = BohriumTransferError(
                "Remote input upload failed after job/create; "
                "compute job was not submitted; "
                f"created_job_ref={created_ref}: {exc}",
            )
            error.created_job_ref = created_ref
            raise error from exc
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
    else:
        with prepare_input_archive(source, session=session) as zip_path:
            create_data = create_job(ctx, job_name=job_name)
            try:
                upload = upload_input_archive(
                    create_data=create_data,
                    zip_path=zip_path,
                )
            except Exception as exc:
                created_ref = _created_job_ref(create_data)
                error = BohriumTransferError(
                    "Local input upload failed after job/create; "
                    "compute job was not submitted; "
                    f"created_job_ref={created_ref}: {exc}",
                )
                error.created_job_ref = created_ref
                raise error from exc
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
        job_id = str(raw_jid).strip()
        return BohriumSubmittedJob(
            job_id=job_id,
            raw_add_response=dict(add_data),
        )

    job_id = str(add_data["jobId"]).strip()
    return BohriumSubmittedJob(
        job_id=job_id,
        raw_add_response=dict(add_data),
    )


# ═══════════════════════════════════════════════════════════════════════════
# BohriumTool
# ═══════════════════════════════════════════════════════════════════════════


class BohriumTool(BuiltinTool):
    """Bohrium HPC platform operations via action-based dispatch."""

    name: ClassVar[str] = "Bohrium"
    description: ClassVar[str] = (
        "Bohrium HPC platform operations: submit / query / download / kill "
        "jobs, list available images / machines."
    )

    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "submit",
                    "query",
                    "download",
                    "kill",
                    "list_images",
                    "list_machines",
                ],
                "description": "Operation to perform.",
            },
            # --- submit ---
            "input_dir": {
                "type": "string",
                "description": "Directory containing and only containing all input files to upload. (submit)",
            },
            "image": {
                "type": "string",
                "description": "Docker image address, e.g. registry.dp.tech/dptech/cp2k:2024.1. (submit)",
            },
            "cmd": {
                "type": "string",
                "description": "Shell command to run inside the container. (submit)",
            },
            "machine": {
                "type": "string",
                "description": "Bohrium machine type. Default: c32_m128_cpu. (submit)",
            },
            "job_name": {
                "type": "string",
                "description": "Human-readable job name. (submit)",
            },
            "disk_size": {
                "type": "integer",
                "description": "Disk size in GB. Default: 50. (submit)",
            },
            # --- query ---
            "job_id": {
                "type": ["integer", "string"],
                "description": "Job ID returned by submit. (query, download, kill)",
            },
            "result_dir": {
                "type": "string",
                "description": "Directory where downloaded artifacts will be stored. (download)",
            },
            # --- list ---
            "keyword": {
                "type": "string",
                "description": "Filter keyword for images or machines. (list_images, list_machines)",
            },
            "machine_type": {
                "type": "string",
                "enum": ["cpu", "gpu"],
                "description": "Machine type filter. Default: cpu. (list_machines)",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum entries to return. Default: 20. (list_images, list_machines)",
            },
        },
        "required": ["action"],
    }

    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="bohrium-api", mode="counted", max_concurrent=3),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset(
        {
            "bohrium.submit",
            "bohrium.query",
            "bohrium.download",
            "bohrium.kill",
        }
    )
    effect_level: ClassVar[str] = "external_effect"
    fast_path_eligible: ClassVar[bool] = False
    plane: ClassVar[ToolPlane] = ToolPlane.EXTERNAL_SERVICE
    state_mode: ClassVar[str] = "stateless"
    stop_mode: ClassVar[str] = "cancellable"
    exposed_to_model: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 0
    submit_review_provider: ClassVar[BohriumSubmitReviewProvider] = (
        BohriumSubmitReviewProvider()
    )

    # In-turn query pacing. Minimum seconds between two real platform queries
    # for the same running job within one agent run. Kept as class attributes
    # so this stays a fixed runtime policy while tests can monkeypatch it.
    _QUERY_MIN_INTERVAL_SECONDS: ClassVar[float] = 30.0
    _QUERY_PACING_STATE_KEY: ClassVar[str] = "bohrium_query_pacing"

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        path_access_roots: Any = (),
        job_ledger: Any | None = None,
    ) -> None:
        super().__init__(
            session=session,
            workdir=workdir,
            path_access_roots=path_access_roots,
        )
        self._job_ledger = job_ledger

    # prompt() keeps workflow + cross-skill rules only. Per-software image/machine/cmd
    # belong in matmaster/skills/<name>/SKILL.md — do not paste full default tables here
    # (duplicates skills, drifts on tag bumps; see evaluation/AGENTS_evaluation.md DevShell).
    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return (
            "## Bohrium tool usage\n"
            "- **Always** load the corresponding software skill first (cp2k, qe, abacus, orca, "
            "lammps, gromacs, pyscf, abinit, pyatb, mlips) to obtain image, machine, "
            "and cmd — do this **before** calling list_images or list_machines.\n"
            "- Only call list_images / list_machines when the loaded skill does not "
            "provide a default image or machine, or you need to verify availability.\n"
            "\n"
            "### Actions\n"
            "- **submit**: package input directory and submit a job, returns job_id. "
            "input_dir MUST be a dedicated directory that contains ONLY the files "
            "required by this job. NEVER pass a shared / catch-all directory (e.g. "
            "`/share`, the workspace root, or any folder holding unrelated structures, "
            "prior outputs, or other jobs' inputs) — the whole directory is packaged "
            "and uploaded as-is. If the needed inputs are scattered in a shared "
            "location, first create a fresh job-specific subdirectory, copy or "
            "symlink ONLY the necessary files into it, then use that path as "
            "input_dir. "
            "cmd runs in the directory where input files are unpacked — do NOT "
            'prepend "cd <path> &&" or any directory change. '
            'cmd MUST end with "> log 2>&1" (auto-appended if missing).\n'
            "- **query**: query a job's current status in a single call and "
            "by single job_id. The first query for a job returns immediately; "
            "repeated in-turn query calls for the same running job may be "
            "paced by the tool. Only call query when you actively need to "
            "confirm one job's current status.\n"
            "- **download**: download artifacts for a finished or failed job into result_dir. "
            "Use only after query reports Finished or Failed. Requires result_dir; "
            "retrieves logs and artifacts for analysis.\n"
            "- **kill**: request termination of a previously submitted job. Use only when "
            "the user explicitly wants to stop a running job. The call is "
            "asynchronous; follow up with query to confirm terminal state.\n"
            "- **list_images**: list the user's own private Docker images (filtered by keyword).\n"
            "- **list_machines**: query available machine types (cpu / gpu).\n"
            "\n"
            "### Handoff & exit\n"
            "- After submit succeeds, sanity-check the job ONCE with query "
            "before ending your turn: Failed → triage immediately (download "
            "logs, fix and resubmit, or report); Running → started cleanly; "
            "still queued (Pending/Scheduling) → safe to end as well, a later "
            "failure will wake you. For a batch sharing one "
            "image/machine/config, checking a few jobs is enough — once any "
            "job reaches Running the shared config is validated; do NOT "
            "verify every job.\n"
            "- By default do NOT wait for completion: no sleep loops, no "
            "poll-until-finished. Background monitoring takes over after "
            "submit; when jobs reach terminal states (or the first failure "
            "appears) you will be invoked again automatically with current "
            "job state in context.\n"
            "- Exception — quick jobs: if a job is expected to finish within "
            "a few minutes, you MAY keep querying it in-turn. The Bohrium tool "
            "automatically paces repeated query calls for the same running "
            "job, so do NOT manage query cadence yourself with Bash sleep. If "
            "you have other pending work, do that FIRST instead of firing a "
            "query and waiting — once a paced query is issued, this turn "
            "blocks until that tool call returns and you cannot do other work "
            "meanwhile. Still wait at most ~5 minutes in total; after that "
            "hand off to background monitoring and end your turn.\n"
            "- A submit error means NO job was created: fix and resubmit, or "
            "report it. Never end your turn implying a failed submit "
            "succeeded.\n"
            "- When ending your turn, summarize submitted jobs (job_id, "
            "job_name) and tell the user results will be delivered "
            "automatically.\n"
        )

    def _build_context(self, *, require_project: bool = False) -> BohriumContext:
        return build_bohrium_context(
            session=self._session,
            require_project=require_project,
        )

    def _log_request_context(
        self,
        *,
        action: str,
        ctx: BohriumContext,
        sandbox: bool | None,
    ) -> None:
        logger.info(
            "Bohrium request context action=%s source=%s base_url=%s "
            "project_id=%s sandbox=%s service_env=%s access_key=%s",
            action,
            ctx.credential_source,
            ctx.credentials.base_url,
            ctx.credentials.project_id,
            sandbox if sandbox is not None else "n/a",
            get_bohrium_service_env(),
            mask_secret(ctx.credentials.access_key),
        )

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        """Pace repeated query calls for the same running job within one run."""
        if arguments.get("action") != "query":
            return await super().execute_with_context(arguments, exec_ctx)

        raw_job_id = arguments.get("job_id")
        runner_state = exec_ctx.runner_state if exec_ctx is not None else None
        if raw_job_id is None or runner_state is None:
            return await super().execute_with_context(arguments, exec_ctx)

        pacing = runner_state.get(self._QUERY_PACING_STATE_KEY)
        if pacing is None:
            pacing = {}
            runner_state.set(self._QUERY_PACING_STATE_KEY, pacing)

        normalized_job_id = str(raw_job_id).strip()
        record = pacing.get(normalized_job_id)
        if record is not None and record["running"]:
            wait = self._QUERY_MIN_INTERVAL_SECONDS - (
                time.monotonic() - record["last_checked_monotonic"]
            )
            if wait > 0:
                await asyncio.sleep(wait)

        result = await asyncio.to_thread(self._execute, arguments)

        normalized = normalize_tool_result(result)
        if normalized.status == "success":
            pacing[normalized_job_id] = {
                "last_checked_monotonic": time.monotonic(),
                "running": bool(normalized.meta.get("bohrium_running")),
            }
        return result

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        action = arguments.get("action", "")
        match action:
            case "submit":
                return self._submit(arguments)
            case "query":
                return self._query(arguments)
            case "download":
                return self._download(arguments)
            case "kill":
                return self._kill(arguments)
            case "list_images":
                return self._list_images(arguments)
            case "list_machines":
                return self._list_machines(arguments)
            case _:
                return ToolResult(
                    status="error",
                    content=f"Unknown action: {action!r}. "
                    f"Must be one of: submit, query, download, kill, "
                    f"list_images, list_machines.",
                )

    def _safe_ledger(self, method: str, /, **kwargs: Any) -> None:
        """调用 ledger port，吞掉异常：ledger 写失败不阻断工具主流程。"""
        if self._job_ledger is None:
            return
        try:
            getattr(self._job_ledger, method)(**kwargs)
        except Exception:  # noqa: BLE001
            logger.warning(
                "bohrium ledger %s failed job_id=%s",
                method,
                kwargs.get("job_id"),
                exc_info=True,
            )

    def _submit(self, args: dict[str, Any]) -> ToolResult:
        try:
            exec_args = normalize_execution_args(args).arguments
        except ValueError as exc:
            return ToolResult(
                status="error", content=f"Submit arguments rejected: {exc}"
            )

        input_dir = exec_args.get("input_dir", "")
        image = exec_args.get("image", "")
        cmd = exec_args.get("cmd", "")

        if not input_dir:
            return ToolResult(
                status="error", content="Missing required parameter: input_dir"
            )
        if not image:
            return ToolResult(
                status="error", content="Missing required parameter: image"
            )
        if not cmd:
            return ToolResult(status="error", content="Missing required parameter: cmd")

        machine = exec_args["machine"]
        job_name = exec_args["job_name"]
        disk_size = exec_args["disk_size"]

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context(require_project=True)
            self._log_request_context(action="submit", ctx=ctx, sandbox=ctx.sandbox)
            submitted = submit_job_via_runtime(
                input_dir=str(input_dir),
                image=str(image),
                cmd=str(cmd),
                machine=str(machine),
                job_name=str(job_name),
                disk_size=disk_size,
                workdir=self._workdir or Path("."),
                session=self._session,
            )
            self._safe_ledger(
                "record_submit",
                job_id=str(submitted.job_id),
                job_name=str(job_name),
                project_id=ctx.credentials.project_id,
                sandbox=ctx.sandbox,
                input_dir=str(input_dir),
            )
            return ToolResult(
                status="success",
                content=json.dumps(
                    {
                        "success": True,
                        "job_id": submitted.job_id,
                        "status": "Submitted",
                        "use_sandbox": ctx.sandbox,
                    },
                    ensure_ascii=False,
                ),
                meta={
                    "submit_execution_audit": {
                        "execution_attempted": True,
                        "external_effect_started": True,
                        "job_create_attempted": True,
                        "job_id": submitted.job_id,
                        "input_upload_attempted": True,
                        "job_add_attempted": True,
                    }
                },
            )
        except BohriumTransferError as exc:
            return ToolResult(
                status="error",
                content=str(exc),
                meta={
                    "submit_execution_audit": {
                        "execution_attempted": True,
                        "external_effect_started": True,
                        "job_create_attempted": True,
                        "job_id": exc.created_job_ref,
                        "input_upload_attempted": True,
                        "job_add_attempted": False,
                    }
                },
            )
        except (BohriumError, ValueError) as exc:
            return ToolResult(
                status="error",
                content=str(exc),
                meta={
                    "submit_execution_audit": {
                        "execution_attempted": True,
                        "external_effect_started": False,
                    }
                },
            )
        except Exception as exc:
            logger.error(
                "bohrium submit failed action=submit base_url=%s sandbox=%s error=%s",
                ctx.credentials.base_url if ctx is not None else "",
                ctx.sandbox if ctx is not None else "n/a",
                exc,
                exc_info=True,
            )
            return ToolResult(status="error", content=f"Submit failed: {exc}")

    def _query(self, args: dict[str, Any]) -> ToolResult:
        raw_job_id = args.get("job_id")
        if raw_job_id is None:
            return ToolResult(
                status="error", content="Missing required parameter: job_id"
            )

        if args.get("result_dir"):
            return ToolResult(
                status="error",
                content=(
                    "query no longer downloads artifacts. "
                    f'Use Bohrium(action="download", job_id={raw_job_id!r}, '
                    f'result_dir="results/run_{raw_job_id}") instead.'
                ),
            )

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            sandbox = ctx.sandbox
            self._log_request_context(action="query", ctx=ctx, sandbox=sandbox)
            job_id: int | str = str(raw_job_id).strip() if sandbox else int(raw_job_id)
            detail_data = get_job_detail(ctx, job_id=job_id)
            code = detail_data.get("status", 0)

            if code in FAILURE_CODES or (
                code not in RUNNING_CODES and code != SUCCESS_CODE
            ):
                code, _, detail_data = confirm_terminal_status(
                    ctx,
                    job_id=job_id,
                    detail_data=detail_data,
                )

            status_label = status_name(code)
            self._safe_ledger(
                "record_poll",
                job_id=str(job_id),
                sandbox=sandbox,
                status_code=int(code),
            )

            if code in RUNNING_CODES:
                message = f"Job is {status_label}. " "Continue other work."
            elif code == SUCCESS_CODE:
                message = (
                    "Job is Finished. Call "
                    f'Bohrium(action="download", job_id={job_id!r}, '
                    f'result_dir="results/run_{job_id}") '
                    "to retrieve artifacts."
                )
            elif code in FAILURE_CODES:
                message = (
                    "Job is Failed. Call "
                    f'Bohrium(action="download", job_id={job_id!r}, '
                    f'result_dir="results/run_{job_id}") '
                    "to retrieve logs and artifacts."
                )
            else:
                message = f"Unexpected status code {code}. Retry query or check Bohrium console."

            # Attempt to fetch live log tail for running/terminal jobs
            log_tail = ""
            if sandbox:
                try:
                    log_tail = self._fetch_log_tail(ctx, str(job_id))
                except Exception:
                    pass

            result_payload: dict[str, Any] = {
                "success": True,
                "job_id": job_id,
                "status": status_label,
                "message": message,
            }
            if log_tail:
                result_payload["log_tail"] = log_tail

            return ToolResult(
                status="success",
                content=json.dumps(result_payload, ensure_ascii=False),
                meta={
                    "bohrium_running": code in RUNNING_CODES,
                    "bohrium_status_code": int(code),
                },
            )

        except Exception as exc:
            logger.error(
                "bohrium query failed action=query base_url=%s sandbox=%s error=%s",
                ctx.credentials.base_url if ctx is not None else "",
                ctx.sandbox if ctx is not None else "n/a",
                exc,
                exc_info=True,
            )
            return ToolResult(status="error", content=f"Query failed: {exc}")

    def _fetch_log_tail(
        self, ctx: BohriumContext, job_id: str, max_lines: int = 15
    ) -> str:
        """Best-effort fetch of live log tail from a sandbox job."""
        host, path, token = get_file_token(ctx, file_path="log", job_id=job_id)
        if not (host and path and token):
            return ""
        import urllib.request
        from urllib.parse import quote

        encoded_path = quote(path, safe="/")
        url = f"{host.rstrip('/')}/api/download/{encoded_path}?token={token}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        tail = raw[-4096:] if len(raw) > 4096 else raw
        lines = tail.strip().splitlines()
        return "\n".join(lines[-max_lines:])

    def _download(self, args: dict[str, Any]) -> ToolResult:
        raw_job_id = args.get("job_id")
        if raw_job_id is None:
            return ToolResult(
                status="error", content="Missing required parameter: job_id"
            )

        result_dir_str = str(args.get("result_dir") or "").strip()
        if not result_dir_str:
            return ToolResult(
                status="error",
                content="Missing required parameter: result_dir",
            )

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            sandbox = ctx.sandbox
            self._log_request_context(action="download", ctx=ctx, sandbox=sandbox)
            job_id: int | str = str(raw_job_id).strip() if sandbox else int(raw_job_id)
            target = resolve_download_target(
                raw_path=result_dir_str,
                workdir=self._workdir,
                session=self._session,
            )
            detail_data = get_job_detail(ctx, job_id=job_id)
            code = detail_data.get("status", 0)
            status_label = status_name(code)
            if code in RUNNING_CODES:
                return ToolResult(
                    status="error",
                    content=(
                        f"Job is {status_label}. "
                        "download is only available after terminal status."
                    ),
                )

            if code not in FAILURE_CODES and code != SUCCESS_CODE:
                raise BohriumJobStateError(
                    f"Unexpected job status: {status_label} (code={code})"
                )

            if target.kind == "remote_share_dir":
                files, log_tail, report_dir = download_remote_results(
                    target,
                    job_id=job_id,
                    detail_data=detail_data,
                    ctx=ctx,
                    session=self._session,
                )
            else:
                files, log_tail = download_job_artifacts(
                    job_id=job_id,
                    detail_data=detail_data,
                    result_dir=target.staging_dir,
                    ctx=ctx,
                )
                report_dir = publish_download_target(target, session=self._session)

            if code == SUCCESS_CODE:
                return ToolResult(
                    status="success",
                    content=json.dumps(
                        {
                            "success": True,
                            "job_id": job_id,
                            "status": "Finished",
                            "result_dir": report_dir,
                            "files": files,
                            "log_tail": log_tail,
                        },
                        ensure_ascii=False,
                    ),
                )

            return ToolResult(
                status="error",
                content=json.dumps(
                    {
                        "success": False,
                        "job_id": job_id,
                        "status": status_label,
                        "result_dir": report_dir,
                        "files": files,
                        "log_tail": log_tail,
                        "error": f"Job {status_label}.",
                    },
                    ensure_ascii=False,
                ),
            )
        except BohriumError as exc:
            return ToolResult(status="error", content=str(exc))
        except Exception as exc:
            logger.error(
                "bohrium download failed action=download base_url=%s error=%s",
                ctx.credentials.base_url if ctx is not None else "",
                exc,
                exc_info=True,
            )
            return ToolResult(status="error", content=f"Download failed: {exc}")

    def _kill(self, args: dict[str, Any]) -> ToolResult:
        raw_job_id = args.get("job_id")
        if raw_job_id is None:
            return ToolResult(
                status="error", content="Missing required parameter: job_id"
            )

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            sandbox = ctx.sandbox
            self._log_request_context(action="kill", ctx=ctx, sandbox=sandbox)
            job_id: int | str = str(raw_job_id).strip() if sandbox else int(raw_job_id)
            response = terminate_job(ctx, job_id=job_id)
            self._safe_ledger(
                "record_kill",
                job_id=str(job_id),
                sandbox=sandbox,
            )
            return ToolResult(
                status="success",
                content=json.dumps(
                    {
                        "success": True,
                        "job_id": job_id,
                        "status": "Terminating",
                        "message": (
                            "Kill requested. The Bohrium kill RPC is "
                            "asynchronous — call "
                            f'Bohrium(action="query", job_id={job_id!r}) '
                            "to confirm the job reaches a terminal state "
                            "(Stopped/Failed/Finished)."
                        ),
                        "response": response,
                    },
                    ensure_ascii=False,
                ),
            )
        except BohriumError as exc:
            return ToolResult(status="error", content=str(exc))
        except Exception as exc:
            logger.error(
                "bohrium kill failed action=kill base_url=%s sandbox=%s error=%s",
                ctx.credentials.base_url if ctx is not None else "",
                ctx.sandbox if ctx is not None else "n/a",
                exc,
                exc_info=True,
            )
            return ToolResult(status="error", content=f"Kill failed: {exc}")

    def _list_images(self, args: dict[str, Any]) -> ToolResult:
        keyword = (args.get("keyword") or "").strip().lower()
        max_results = int(args.get("max_results", 20))

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            payload = list_images(
                ctx,
                keyword=keyword,
                max_results=max_results,
            )
            self._log_request_context(
                action="list_images",
                ctx=ctx,
                sandbox=bool(
                    ctx.sandbox and payload.get("source") == "sandbox_catalog"
                ),
            )

            return ToolResult(
                status="success",
                content=json.dumps(payload, ensure_ascii=False),
            )

        except Exception as exc:
            logger.error(
                "bohrium list_images failed action=list_images base_url=%s error=%s",
                ctx.credentials.base_url if ctx is not None else "",
                exc,
                exc_info=True,
            )
            return ToolResult(status="error", content=f"list_images failed: {exc}")

    def _list_machines(self, args: dict[str, Any]) -> ToolResult:
        choose_type = args.get("machine_type", "cpu")
        keyword = (args.get("keyword") or "").strip().lower()
        max_results = int(args.get("max_results", 50))

        ctx: BohriumContext | None = None
        try:
            ctx = self._build_context()
            payload = list_machines(
                ctx,
                machine_type=choose_type,
                keyword=keyword,
                max_results=max_results,
            )
            self._log_request_context(
                action="list_machines",
                ctx=ctx,
                sandbox=bool(
                    ctx.sandbox and payload.get("source") == "sandbox_catalog"
                ),
            )

            return ToolResult(
                status="success",
                content=json.dumps(payload, ensure_ascii=False),
            )

        except Exception as exc:
            logger.error(
                "bohrium list_machines failed action=list_machines "
                "base_url=%s machine_type=%s error=%s",
                ctx.credentials.base_url if ctx is not None else "",
                choose_type,
                exc,
                exc_info=True,
            )
            return ToolResult(status="error", content=f"list_machines failed: {exc}")
