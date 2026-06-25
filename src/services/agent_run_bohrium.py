"""Bohrium runtime helpers and setup service for agent runs."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any, NamedTuple

from matmaster.bohrium.credentials import normalize_bohrium_credentials
from matmaster.bohrium.endpoints import get_bohrium_base_url
from matmaster.bohrium.runtime import (
    BohriumRuntimeHandle,
    attach_local_bohrium_runtime_from_run_credentials,
    attach_runtime,
    detach_runtime,
)
from matmaster.bohrium.types import BohriumExecutionContext, BohriumRuntimeSnapshot
from matmaster.sessions.ssh import SSHSession, SSHSessionConfig
from src.dao.bohrium_nodes_table import get_bohrium_nodes_table
from src.services.bohrium_node_service import (
    DEFAULT_SKU_ID,
    get_bohrium_node_service,
    get_default_node_name,
)
from src.services.bohrium_run_support import (
    _build_access_key_failure_reason,
    _creator_id_from_user,
    _emit_node_status,
    _load_run_credentials,
    _remote_session_workspace_root,
)
from src.services.sessions_service import SESSIONS
from src.services.user_service import BohriumAccessKeyFetchResult, UserService
from src.utils.constant import BOHRIUM_DEFAULT_IMAGE_ID, BOHRIUM_DEFAULT_IMAGE_NAME

logger = logging.getLogger(__name__)

_BOHRIUM_REMOTE_USER_SKILLS_ROOT = "/personal/.matmaster/skills"
_BOHRIUM_REMOTE_USER_PLUGINS_ROOT = "/personal/.matmaster/plugins"


def _resolve_bohrium_node_sku_id(sku_id: int | None) -> int:
    """Resolve the run-level Bohrium node SKU, matching BohriumNodeService defaults."""
    if sku_id is not None:
        parsed = int(sku_id)
        if parsed > 0:
            return parsed
    return int(os.environ.get("BOHRIUM_SKU_ID", DEFAULT_SKU_ID))


# Bash snippet for root on Bohrium SSH nodes: wget/curl/git/pip + env.
# GNU wget only accepts ``use_proxy = on|off``; ``use_proxy = no`` is invalid and
# leaves /etc/wgetrc proxy (e.g. ga.dp.tech:8118) in effect.
# Pip reads ``~/.pip/pip.conf`` / ``/etc/pip.conf`` ``[global] proxy=`` independently
# of shell env; strip those lines so ``pip install`` does not force ga.dp.tech.
_CLEAR_REMOTE_PROXY_SCRIPT: str = (
    "rm -f /root/speedUp.sh /speedUp.sh; "
    "printf %s\\n "
    "'# matmaster-evo: disable platform proxy for OSS/outbound' "
    "'use_proxy = off' "
    "'proxy =' "
    "'http_proxy =' "
    "'https_proxy =' "
    "'ftp_proxy =' "
    "> /root/.wgetrc; "
    "printf %s\\n "
    "'# matmaster-evo: disable curl default proxy' "
    "'proxy = \"\"' "
    "'noproxy = \"*\"' "
    "> /root/.curlrc; "
    "git config --global --unset-all http.proxy 2>/dev/null; true; "
    "git config --global --unset-all https.proxy 2>/dev/null; true; "
    "[ -f /root/.pip/pip.conf ] && sed -i "
    "'/^[[:space:]]*proxy[[:space:]]*=/d' "
    "/root/.pip/pip.conf 2>/dev/null; true; "
    "[ -f /etc/pip.conf ] && sed -i "
    "'/^[[:space:]]*proxy[[:space:]]*=/d' "
    "/etc/pip.conf 2>/dev/null; true; "
    "export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= "
    "NO_PROXY= no_proxy= ftp_proxy= FTP_PROXY=; "
    "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY "
    "NO_PROXY no_proxy ftp_proxy FTP_PROXY WGETRC 2>/dev/null; "
)


def _store_bohrium_runtime(
    session_id: str,
    *,
    original_session: Any,
    original_owns_session: bool,
    ssh_session: Any,
) -> None:
    """Persist Bohrium SSH swap state for cleanup."""
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {}
    SESSIONS[session_id]["bohrium_runtime"] = {
        "original_session": original_session,
        "original_owns_session": original_owns_session,
        "ssh_session": ssh_session,
    }


def _restore_playground_session(
    pg: Any,
    original_session: Any,
    original_owns_session: bool,
) -> None:
    """Restore pg.session / _owns_session after a transient SSH swap."""
    pg.session = original_session
    pg._owns_session = original_owns_session


def _restore_bohrium_runtime_state(session_id: str, pg: Any | None) -> None:
    """Pop runtime state, close SSH session, restore original playground session."""
    sess = SESSIONS.get(session_id)
    if not sess:
        return
    runtime = sess.pop("bohrium_runtime", None)
    if not runtime:
        return
    ssh = runtime.get("ssh_session")
    orig = runtime.get("original_session")
    orig_owns = runtime.get("original_owns_session", True)
    if orig is not None:
        detach_runtime(orig)
    if ssh is not None:
        try:
            detach_runtime(ssh)
            if getattr(ssh, "is_open", False):
                ssh.close()
        except Exception as close_err:
            logger.warning(
                "run_agent: close Bohrium SSH session during cleanup failed: %s",
                close_err,
            )
    if pg is not None:
        _restore_playground_session(pg, orig, orig_owns)


def _configure_remote_user_skill_root(ssh_session: Any) -> None:
    ssh_session.remote_user_skills_root = _BOHRIUM_REMOTE_USER_SKILLS_ROOT
    ssh_session.remote_skill_roots = [
        _BOHRIUM_REMOTE_USER_PLUGINS_ROOT,
        _BOHRIUM_REMOTE_USER_SKILLS_ROOT,
    ]


def _run_clear_remote_proxy(pg: Any, phase: str) -> None:
    try:
        session = getattr(pg, "session", None)
        if session is None or not hasattr(session, "exec_bash"):
            logger.warning(
                "run_agent: clear_remote_proxy (%s) skipped: "
                "no session or no exec_bash",
                phase,
            )
            return
        logger.info(
            "run_agent: clear_remote_proxy (%s) running (wgetrc/curlrc/pip.conf + env)",
            phase,
        )
        result = session.exec_bash(_CLEAR_REMOTE_PROXY_SCRIPT, timeout=20)
        exit_code = result.get("exit_code", -1)
        out = (result.get("output") or result.get("stdout") or "").strip()
        if exit_code == 0:
            logger.info(
                "run_agent: clear_remote_proxy (%s) ok exit_code=0",
                phase,
            )
        else:
            tail = out[:500] + ("..." if len(out) > 500 else "")
            logger.warning(
                "run_agent: clear_remote_proxy (%s) non-zero exit_code=%s output=%r",
                phase,
                exit_code,
                tail,
            )
    except Exception as clear_err:
        logger.warning(
            "run_agent: clear_remote_proxy (%s) failed: %s",
            phase,
            clear_err,
        )


class BohriumSetupResult(NamedTuple):
    """Result of Bohrium setup for a run."""

    ssh_attached: bool
    abort_result: tuple[Any, int] | None
    execution_session: Any | None
    execution_workdir: str | None
    session_type: str | None
    runtime_snapshot: BohriumRuntimeSnapshot | None

    @classmethod
    def no_op(cls) -> BohriumSetupResult:
        """Sentinel for 'no Bohrium setup performed' (e.g. missing creds)."""
        return cls(False, None, None, None, None, None)

    @classmethod
    def aborted(cls, reason: str, elapsed_ms: int) -> BohriumSetupResult:
        """Sentinel for an explicit abort with a user-facing reason."""
        return cls(False, ((False, reason), elapsed_ms), None, None, None, None)


class BohriumSetupService:
    """Owns Bohrium setup/cleanup orchestration for agent runs."""

    def __init__(
        self,
        sessions_service: Any,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        self._sessions_service = sessions_service
        self._event_sink = event_sink

    def _load_run_credentials(
        self, session_id: str
    ) -> tuple[dict[str, Any], str | None, str]:
        return _load_run_credentials(self._sessions_service, session_id)

    def _setup_bohrium_for_run(
        self,
        *,
        session_id: str,
        pg: Any,
        run_creds: dict[str, Any],
        user_id_for_ak: str | None,
        org_id: str,
        event_callback: Callable[..., None],
        run_started_at: float,
        workspace: str | None = None,
        bohrium_node_sku_id: int | None = None,
    ) -> BohriumSetupResult:
        return _setup_bohrium_for_run(
            session_id=session_id,
            pg=pg,
            run_creds=run_creds,
            user_id_for_ak=user_id_for_ak,
            org_id=org_id,
            event_callback=event_callback,
            run_started_at=run_started_at,
            workspace=workspace,
            bohrium_node_sku_id=bohrium_node_sku_id,
        )

    def _cleanup_bohrium_after_run(
        self,
        *,
        session_id: str,
        event_callback: Callable[..., None],
        pg_for_run: Any,
        ssh_attached: bool,
    ) -> None:
        _cleanup_bohrium_after_run(
            session_id=session_id,
            sessions_service=self._sessions_service,
            event_callback=event_callback,
            pg_for_run=pg_for_run,
            ssh_attached=ssh_attached,
        )

    def _make_event_bridge(
        self,
        loop: asyncio.AbstractEventLoop,
    ) -> Callable[..., None]:
        """Create a thread-safe callback that bridges bohrium events into event_sink.

        Maps callback types to concrete BusEvent objects:
        - 'error' -> ErrorEvent + StreamClosedEvent
        - 'stream_closed' -> StreamClosedEvent
        - all others -> BohriumNodeEvent

        The sink is responsible for the actual thread handoff. In production,
        AgentRunService injects fanout.dispatch_from_thread(), so the bridge
        builds BusEvent objects in the worker thread and fanout owns the single
        call_soon_threadsafe hop onto the run loop.
        """
        from matmaster.types.events import (
            BohriumNodeEvent,
            ErrorEvent,
            StreamClosedEvent,
        )

        sink = self._event_sink
        if sink is None:
            raise RuntimeError("event_sink required for _make_event_bridge")

        def _cb(source: Any, event_type: str, content: Any, **extra: Any) -> None:
            try:
                if event_type == "error":
                    msg = content if isinstance(content, str) else str(content)
                    sink(ErrorEvent(source=str(source), message=msg))
                    sink(
                        StreamClosedEvent(
                            source=str(source),
                            end_reason="error",
                            task_completed=False,
                            treat_as_failure=True,
                        )
                    )
                    return
                if event_type == "stream_closed":
                    body = "" if content is None else str(content)
                    sink(
                        StreamClosedEvent(
                            source=str(source),
                            content=body,
                            task_completed=False,
                            end_reason="error",
                            treat_as_failure=True,
                        )
                    )
                    return
                sink(
                    BohriumNodeEvent(
                        source=str(source),
                        payload={
                            "type": event_type,
                            "content": content,
                            **extra,
                        },
                    )
                )
            except Exception:
                logger.debug("bohrium event bridge error type=%s", event_type)

        return _cb

    async def run_setup(
        self,
        *,
        session_id: str,
        playground: Any,
        run_started_at: float,
        bohrium_required: bool = False,
        workspace: str | None = None,
        bohrium_node_sku_id: int | None = None,
    ) -> BohriumSetupResult:
        """Load credentials, bridge events, and run setup in the executor."""
        loop = asyncio.get_running_loop()
        event_cb = self._make_event_bridge(loop)

        return await loop.run_in_executor(
            None,
            lambda: self._run_setup_sync(
                session_id=session_id,
                pg=playground,
                event_callback=event_cb,
                run_started_at=run_started_at,
                bohrium_required=bohrium_required,
                workspace=workspace,
                bohrium_node_sku_id=bohrium_node_sku_id,
            ),
        )

    def _run_setup_sync(
        self,
        *,
        session_id: str,
        pg: Any,
        event_callback: Callable[..., None],
        run_started_at: float,
        bohrium_required: bool = False,
        workspace: str | None = None,
        bohrium_node_sku_id: int | None = None,
    ) -> BohriumSetupResult:
        run_creds, user_id_for_ak, org_id = self._load_run_credentials(session_id)
        access_key = str(run_creds.get("access_key") or "").strip()
        project_id = run_creds.get("project_id")
        if access_key:
            ak_result = BohriumAccessKeyFetchResult(
                status="success",
                access_key=access_key,
                retryable=False,
            )
        elif bohrium_required or project_id is not None:
            ak_result = UserService.fetch_bohrium_access_key_result(
                user_id_for_ak,
                org_id,
            )
            if ak_result.access_key:
                run_creds["access_key"] = ak_result.access_key
        else:
            # No Bohrium project requested and not required — skip the AK lookup.
            ak_result = BohriumAccessKeyFetchResult(
                status="not_attempted",
                retryable=False,
            )
        if bohrium_required and project_id is None:
            reason = "Bohrium project_id 缺失，无法建立 Bohrium 运行环境"
            logger.warning(
                "run_setup: required Bohrium project_id missing "
                "session_id=%s user_id=%s org_id=%s status=%s attempts=%s",
                session_id,
                user_id_for_ak,
                org_id,
                ak_result.status,
                ak_result.attempts,
            )
            event_callback("System", "error", reason)
            elapsed_ms = int((time.monotonic() - run_started_at) * 1000)
            return BohriumSetupResult.aborted(reason, elapsed_ms)

        if bohrium_required and not ak_result.access_key:
            reason = _build_access_key_failure_reason(ak_result)
            logger.warning(
                "run_setup: required Bohrium access_key lookup failed "
                "session_id=%s user_id=%s org_id=%s project_id=%s status=%s "
                "attempts=%s http_status=%s api_code=%s",
                session_id,
                user_id_for_ak,
                org_id,
                project_id,
                ak_result.status,
                ak_result.attempts,
                ak_result.http_status,
                ak_result.api_code,
            )
            event_callback("System", "error", reason)
            elapsed_ms = int((time.monotonic() - run_started_at) * 1000)
            return BohriumSetupResult.aborted(reason, elapsed_ms)

        return self._setup_bohrium_for_run(
            session_id=session_id,
            pg=pg,
            run_creds=run_creds,
            user_id_for_ak=user_id_for_ak,
            org_id=org_id,
            event_callback=event_callback,
            run_started_at=run_started_at,
            workspace=workspace,
            bohrium_node_sku_id=bohrium_node_sku_id,
        )

    async def run_cleanup(
        self,
        *,
        session_id: str,
        pg_for_run: Any,
        ssh_attached: bool,
    ) -> None:
        """Bridge cleanup events and run cleanup in the executor."""
        loop = asyncio.get_running_loop()
        event_cb = self._make_event_bridge(loop)

        await loop.run_in_executor(
            None,
            lambda: self._cleanup_bohrium_after_run(
                session_id=session_id,
                event_callback=event_cb,
                pg_for_run=pg_for_run,
                ssh_attached=ssh_attached,
            ),
        )


def _setup_bohrium_for_run(
    *,
    session_id: str,
    pg: Any,
    run_creds: dict[str, Any],
    user_id_for_ak: str | None,
    org_id: str,
    event_callback: Callable[..., None],
    run_started_at: float,
    workspace: str | None = None,
    bohrium_node_sku_id: int | None = None,
) -> BohriumSetupResult:
    """Prepare Bohrium node and SSH session for the run when credentials exist."""
    if not run_creds:
        return BohriumSetupResult.no_op()

    project_id = run_creds.get("project_id")
    if project_id is not None:
        project_id = int(project_id)
    access_key = (run_creds.get("access_key") or "").strip()
    if not access_key or project_id is None:
        return BohriumSetupResult.no_op()
    effective_sku_id = _resolve_bohrium_node_sku_id(bohrium_node_sku_id)

    attach_local_bohrium_runtime_from_run_credentials(
        getattr(pg, "session", None), run_creds
    )

    node_id: int | None = None
    node_ip = None
    node_pwd = None
    try:
        node_svc = get_bohrium_node_service()
        use_reuse_table = bool(user_id_for_ak and org_id)
        if not use_reuse_table and (user_id_for_ak or org_id):
            logger.info(
                "run_agent: skip reuse table (missing user_id or org_id); "
                "user_id=%s org_id=%r — 请确保请求带 X-User-Id 且上游带 X-Org-Id",
                user_id_for_ak,
                org_id or "(empty)",
            )
        if use_reuse_table:
            nodes_table = get_bohrium_nodes_table()
            creator_id = _creator_id_from_user(user_id_for_ak)
            try:
                tracked_node_ids = nodes_table.list_node_ids_for_user_org(
                    user_id_for_ak, org_id
                )
                cleanup_node_name = get_default_node_name()
                destroyed_node_ids = node_svc.destroy_untracked_nodes_by_name(
                    access_key,
                    tracked_node_ids,
                    node_name=cleanup_node_name,
                    creator_id=creator_id,
                )
                if destroyed_node_ids:
                    logger.info(
                        "run_agent: destroyed untracked nodes user_id=%s org_id=%s "
                        "name=%s node_ids=%s",
                        user_id_for_ak,
                        org_id,
                        cleanup_node_name,
                        destroyed_node_ids,
                    )
            except Exception as cleanup_err:
                logger.warning(
                    "run_agent: cleanup untracked nodes failed user_id=%s org_id=%s: %s",
                    user_id_for_ak,
                    org_id,
                    cleanup_err,
                )
            row = nodes_table.find_one_for_reuse(
                user_id_for_ak, org_id, project_id, effective_sku_id
            )
            expected_image_name = (
                os.environ.get("BOHRIUM_EXPECTED_IMAGE_NAME")
                or os.environ.get("BOHRIUM_IMAGE_NAME")
                or BOHRIUM_DEFAULT_IMAGE_NAME
            )
            if isinstance(expected_image_name, str):
                expected_image_name = expected_image_name.strip() or None
            else:
                expected_image_name = None
            if expected_image_name is None:
                expected_image_name = node_svc.get_image_name_by_id(
                    access_key, BOHRIUM_DEFAULT_IMAGE_ID
                )

            def _node_image_outdated(node_image_name: str | None) -> bool:
                if not expected_image_name or not node_image_name:
                    return False
                return node_image_name != expected_image_name

            def _destroy_outdated_node(nid: int) -> None:
                """Delete tracking row and destroy a Bohrium node with outdated image."""
                nodes_table.delete_by_node(
                    user_id_for_ak, org_id, project_id, effective_sku_id, nid
                )
                try:
                    node_svc.destroy_node(
                        access_key,
                        nid,
                        project_id,
                        creator_id=creator_id,
                    )
                except Exception as destroy_err:
                    logger.warning(
                        "run_agent: destroy outdated node_id=%s failed: %s",
                        nid,
                        destroy_err,
                    )

            if row:
                node_id = int(row["node_id"])
                node_info = node_svc.get_node_info(access_key, node_id)
                logger.info(
                    "run_agent: node image check (ready) node_id=%s "
                    "node_image_name=%s expected_image_name=%s",
                    node_id,
                    node_info.get("image_name") if node_info else None,
                    expected_image_name,
                )
                if node_info and node_info.get("ip"):
                    if _node_image_outdated(node_info.get("image_name")):
                        logger.info(
                            "run_agent: reuse skipped, node image outdated "
                            "node_id=%s node_image_name=%s expected_image_name=%s, destroy and create new",
                            node_id,
                            node_info.get("image_name"),
                            expected_image_name,
                        )
                        _destroy_outdated_node(node_id)
                        node_id = None
                        node_info = None
                    else:
                        node_ip = node_info.get("ip")
                        node_pwd = node_info.get("password")
                        logger.info(
                            "run_agent: reusing Bohrium node node_id=%s ip=%s",
                            node_id,
                            node_ip,
                        )
                else:
                    node_detail = node_svc.get_node_detail(access_key, node_id)
                    logger.info(
                        "run_agent: node image check (not ready) node_id=%s "
                        "node_image_name=%s expected_image_name=%s",
                        node_id,
                        node_detail.get("image_name") if node_detail else None,
                        expected_image_name,
                    )
                    if node_detail is not None and _node_image_outdated(
                        node_detail.get("image_name")
                    ):
                        logger.info(
                            "run_agent: node image outdated node_id=%s "
                            "node_image_name=%s expected_image_name=%s, destroy and create new",
                            node_id,
                            node_detail.get("image_name"),
                            expected_image_name,
                        )
                        _destroy_outdated_node(node_id)
                        node_id = None
                    else:
                        try:
                            node_svc.restart_node(
                                access_key,
                                node_id,
                                project_id,
                                creator_id=creator_id,
                                sku_id=effective_sku_id,
                            )
                            _emit_node_status(
                                event_callback,
                                node_id,
                                "created",
                                "节点已重启，正在等待就绪...",
                            )
                            node_info = node_svc.wait_until_ready(access_key, node_id)
                            node_ip = node_info.get("ip")
                            node_pwd = node_info.get("password")
                            logger.info(
                                "run_agent: restarted Bohrium node node_id=%s ip=%s",
                                node_id,
                                node_ip,
                            )
                        except Exception as restart_err:
                            logger.warning(
                                "run_agent: restart node_id=%s failed, will create new: %s",
                                node_id,
                                restart_err,
                            )
                            nodes_table.delete_by_node(
                                user_id_for_ak,
                                org_id,
                                project_id,
                                effective_sku_id,
                                node_id,
                            )
                            node_id = None
        if node_id is None or node_ip is None:
            node_info = node_svc.create_node(
                access_key, project_id, sku_id=effective_sku_id
            )
            node_id = node_info.get("node_id")
            if node_id is not None:
                _emit_node_status(
                    event_callback,
                    node_id,
                    "created",
                    "节点已创建，正在等待就绪...",
                )
                node_info = node_svc.wait_until_ready(access_key, node_id)
                node_ip = node_info.get("ip")
                node_pwd = node_info.get("password")
                if use_reuse_table and user_id_for_ak and org_id:
                    try:
                        get_bohrium_nodes_table().insert_node(
                            user_id_for_ak,
                            org_id,
                            project_id,
                            effective_sku_id,
                            node_id,
                        )
                        logger.info(
                            "run_agent: inserted node into evo_bohrium_nodes "
                            "user_id=%s org_id=%s project_id=%s sku_id=%s node_id=%s",
                            user_id_for_ak,
                            org_id,
                            project_id,
                            effective_sku_id,
                            node_id,
                        )
                    except Exception as insert_err:
                        logger.warning(
                            "run_agent: insert_node failed (table missing?): %s",
                            insert_err,
                            exc_info=True,
                        )
        if node_id is not None and node_ip:
            if session_id not in SESSIONS:
                SESSIONS[session_id] = {}
            SESSIONS[session_id]["bohrium_node_id"] = node_id
            SESSIONS[session_id]["bohrium_node_sku_id"] = effective_sku_id
            remote_workspace_root = _remote_session_workspace_root()
            _emit_node_status(
                event_callback,
                node_id,
                "ready",
                "Bohrium 节点已就绪",
                ip=node_ip,
            )
            ssh_workspace_path = (workspace or remote_workspace_root).rstrip("/") or "/"
            original_session = pg.session
            original_owns_session = pg._owns_session
            ssh_config = SSHSessionConfig(
                host=node_ip,
                password=node_pwd,
                workspace_path=ssh_workspace_path,
            )
            ssh_session = SSHSession(ssh_config)
            swapped = False
            ssh_session.open()
            _configure_remote_user_skill_root(ssh_session)
            try:
                attach_local_bohrium_runtime_from_run_credentials(
                    ssh_session, run_creds
                )
                pg.session = ssh_session
                pg._owns_session = False
                swapped = True
                _store_bohrium_runtime(
                    session_id,
                    original_session=original_session,
                    original_owns_session=original_owns_session,
                    ssh_session=ssh_session,
                )
                logger.info(
                    "run_agent: SSH session attached to Bohrium node ip=%s workspace=%s",
                    node_ip,
                    ssh_workspace_path,
                )
                _run_clear_remote_proxy(pg, "post_ssh")
                _emit_node_status(
                    event_callback,
                    node_id,
                    "connected",
                    f"已连接到 Bohrium 节点 {node_ip}",
                    ip=node_ip,
                )
            except Exception:
                if swapped:
                    _restore_playground_session(
                        pg, original_session, original_owns_session
                    )
                    SESSIONS.get(session_id, {}).pop("bohrium_runtime", None)
                try:
                    ssh_session.close()
                except Exception as close_err:
                    logger.debug(
                        "run_agent: ssh_session.close failed during swap rollback: %s",
                        close_err,
                    )
                raise

            remote_project_root = getattr(ssh_session, "remote_project_root", "")
            if not isinstance(remote_project_root, str) or not remote_project_root:
                remote_project_root = ""

            runtime = BohriumRuntimeHandle(
                credentials=normalize_bohrium_credentials(
                    {
                        **run_creds,
                        "base_url": run_creds.get("base_url") or get_bohrium_base_url(),
                    }
                ),
                execution=BohriumExecutionContext(
                    session_type="ssh",
                    execution_workdir=ssh_workspace_path,
                    remote_workspace_root=remote_workspace_root,
                    remote_project_root=remote_project_root,
                    node_id=node_id,
                    node_ip=node_ip,
                    ssh_attached=True,
                ),
                execution_session=ssh_session,
            )
            attach_runtime(ssh_session, runtime)
            return BohriumSetupResult(
                True,
                None,
                ssh_session,
                ssh_workspace_path,
                "ssh",
                runtime.snapshot(),
            )
        return BohriumSetupResult.no_op()
    except Exception as e:
        reason = f"Bohrium 节点创建失败: {e}"
        logger.warning(
            "run_agent: auto create Bohrium node failed: %s",
            e,
            exc_info=True,
        )
        _emit_node_status(event_callback, node_id, "failed", reason)
        # The 'error' bridge mapping emits both ErrorEvent and StreamClosedEvent
        # (treat_as_failure=True); do not follow up with a separate stream_closed.
        event_callback("System", "error", reason)
        elapsed_ms = int((time.monotonic() - run_started_at) * 1000)
        return BohriumSetupResult.aborted(reason, elapsed_ms)


def _cleanup_bohrium_after_run(
    *,
    session_id: str,
    sessions_service: Any,
    event_callback: Callable[..., None],
    pg_for_run: Any,
    ssh_attached: bool,
) -> None:
    """Restore session state and cleanup or release Bohrium node."""
    logger.debug(
        "cleanup_bohrium_after_run: session_id=%s ssh_attached=%s",
        session_id,
        ssh_attached,
    )
    # Runtime restore is keyed off stored Bohrium swap state, not ssh_attached.
    _restore_bohrium_runtime_state(session_id, pg_for_run)
    if pg_for_run is not None:
        detach_runtime(getattr(pg_for_run, "session", None))

    session_data = SESSIONS.get(session_id, {})
    node_id = session_data.pop("bohrium_node_id", None)
    node_sku_id = session_data.pop("bohrium_node_sku_id", None)
    row = sessions_service.get_session(session_id)
    org_id = ""
    project_id = None
    user_id: str | None = None
    if row:
        org_id = (row.get("org_id") or "").strip()
        project_id = row.get("project_id")
        if project_id is not None:
            project_id = int(project_id)
        raw_uid = row.get("user_id")
        if raw_uid is not None:
            user_id = str(raw_uid)
    access_key = ""
    if user_id and org_id:
        access_key = UserService.get_bohrium_access_key(user_id, org_id) or ""
    if (
        node_id is not None
        and node_sku_id is not None
        and user_id
        and org_id
        and project_id is not None
    ):
        try:
            get_bohrium_nodes_table().update_last_used_at(
                user_id, org_id, int(project_id), int(node_sku_id), int(node_id)
            )
            logger.info(
                "run_agent: updated last_used_at for node_id=%s sku_id=%s (reuse table)",
                node_id,
                node_sku_id,
            )
        except Exception as e:
            logger.warning(
                "run_agent: update_last_used_at failed node_id=%s: %s",
                node_id,
                e,
            )
    elif node_id is not None:
        try:
            _emit_node_status(event_callback, int(node_id), "destroyed", "节点已销毁")
        except Exception:
            pass
        if access_key and project_id is not None:
            try:
                get_bohrium_node_service().destroy_node(
                    access_key,
                    int(node_id),
                    int(project_id),
                    creator_id=_creator_id_from_user(user_id),
                )
            except Exception as e:
                logger.warning(
                    "run_agent: auto destroy Bohrium node node_id=%s failed: %s",
                    node_id,
                    e,
                    exc_info=True,
                )
