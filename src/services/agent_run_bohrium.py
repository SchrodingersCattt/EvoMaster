"""Bohrium runtime helpers for agent runs."""

import logging
import os
import time
from typing import Any, Callable, NamedTuple

from src.dao.bohrium_nodes_table import get_bohrium_nodes_table
from src.services.bohrium_node_service import get_bohrium_node_service
from src.services.sessions_service import SESSIONS
from src.services.user_service import UserService
from src.utils.constant import BOHRIUM_DEFAULT_IMAGE_ID, BOHRIUM_DEFAULT_IMAGE_NAME

logger = logging.getLogger(__name__)


def _clear_remote_proxy_shell() -> str:
    """Bash snippet for root on Bohrium SSH nodes: wget/curl/git + env.

    GNU wget only accepts ``use_proxy = on|off``; ``use_proxy = no`` is invalid and
    leaves /etc/wgetrc proxy (e.g. ga.dp.tech:8118) in effect.
    """
    return (
        'rm -f /root/speedUp.sh /speedUp.sh; '
        'printf %s\\n '
        "'# matmaster-evo: disable platform proxy for OSS/outbound' "
        "'use_proxy = off' "
        "'proxy =' "
        "'http_proxy =' "
        "'https_proxy =' "
        "'ftp_proxy =' "
        '> /root/.wgetrc; '
        'printf %s\\n '
        "'# matmaster-evo: disable curl default proxy' "
        "'proxy = \"\"' "
        "'noproxy = \"*\"' "
        '> /root/.curlrc; '
        'git config --global --unset-all http.proxy 2>/dev/null; true; '
        'git config --global --unset-all https.proxy 2>/dev/null; true; '
        'export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= '
        'NO_PROXY= no_proxy= ftp_proxy= FTP_PROXY=; '
        'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY '
        'NO_PROXY no_proxy ftp_proxy FTP_PROXY WGETRC 2>/dev/null; '
    )


def _run_clear_remote_proxy(pg: Any, phase: str) -> None:
    try:
        session = getattr(pg, 'session', None)
        if session is None or not hasattr(session, 'exec_bash'):
            logger.warning(
                'run_agent_sync: clear_remote_proxy (%s) skipped: '
                'no session or no exec_bash',
                phase,
            )
            return
        logger.info(
            'run_agent_sync: clear_remote_proxy (%s) running (wgetrc/curlrc + env)',
            phase,
        )
        script = _clear_remote_proxy_shell()
        env = getattr(session, '_env', None)
        if env is not None and hasattr(env, 'ssh_bash_noninteractive'):
            # Avoid tmux send-keys on very long lines (remote tmux: unknown command C-m).
            result = env.ssh_bash_noninteractive(script, timeout=20)
        else:
            result = session.exec_bash(script, timeout=20)
        exit_code = result.get('exit_code', -1)
        out = (result.get('output') or result.get('stdout') or '').strip()
        if exit_code == 0:
            logger.info(
                'run_agent_sync: clear_remote_proxy (%s) ok exit_code=0',
                phase,
            )
        else:
            tail = out[:500] + ('...' if len(out) > 500 else '')
            logger.warning(
                'run_agent_sync: clear_remote_proxy (%s) non-zero exit_code=%s output=%r',
                phase,
                exit_code,
                tail,
            )
    except Exception as clear_err:
        logger.warning(
            'run_agent_sync: clear_remote_proxy (%s) failed: %s',
            phase,
            clear_err,
        )


class BohriumSetupResult(NamedTuple):
    """Result of Bohrium setup for a run."""

    ssh_attached: bool
    abort_result: tuple[Any, int] | None


def load_run_credentials(
    sessions_service: Any, session_id: str
) -> tuple[dict[str, Any], str | None, str]:
    """Load run credentials from the session store."""
    row = sessions_service.get_session(session_id)
    run_creds: dict[str, Any] = {}
    if row:
        uid = row.get('user_id')
        if uid is not None:
            run_creds['user_id'] = str(uid)
        oid = row.get('org_id')
        if oid is not None and str(oid).strip():
            run_creds['org_id'] = str(oid).strip()
        pid = row.get('project_id')
        if pid is not None:
            run_creds['project_id'] = int(pid)
    user_id_for_ak = sessions_service.get_session_user_id(session_id)
    if run_creds.get('user_id') is None and user_id_for_ak is not None:
        run_creds['user_id'] = user_id_for_ak
    org_id = (run_creds.get('org_id') or '').strip()
    if user_id_for_ak and org_id:
        run_creds['access_key'] = (
            UserService.get_bohrium_access_key(user_id_for_ak, org_id) or ''
        )
    return run_creds, user_id_for_ak, org_id


def apply_run_credentials_to_session(session: Any, run_creds: dict[str, Any]) -> None:
    """Attach transient Bohrium credentials to the active session object."""
    if run_creds and session:
        session._bohrium_credentials = run_creds


def _creator_id_from_user(user_id: str | None) -> int:
    """Convert user id to creator id."""
    if user_id is None:
        return 0
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return 0


def _emit_node_status(
    event_callback: Callable[..., None],
    node_id: int | None,
    status: str,
    message: str,
    ip: str | None = None,
) -> None:
    """Emit a Bohrium node status event."""
    payload: dict[str, Any] = {
        'status': status,
        'message': message,
    }
    if node_id is not None:
        payload['node_id'] = node_id
    if ip:
        payload['ip'] = ip
    event_callback('System', 'bohrium_node', payload)


def setup_bohrium_for_run(
    *,
    session_id: str,
    pg: Any,
    base: Any,
    run_creds: dict[str, Any],
    user_id_for_ak: str | None,
    org_id: str,
    event_callback: Callable[..., None],
    run_started_at: float,
) -> BohriumSetupResult:
    """Prepare Bohrium node and SSH session for the run when credentials exist."""
    if not run_creds:
        return BohriumSetupResult(False, None)

    project_id = run_creds.get('project_id')
    if project_id is not None:
        project_id = int(project_id)
    access_key = (run_creds.get('access_key') or '').strip()
    if not access_key or project_id is None:
        return BohriumSetupResult(False, None)

    try:
        node_svc = get_bohrium_node_service()
        node_id = None
        node_ip = None
        node_pwd = None
        use_reuse_table = bool(user_id_for_ak and org_id)
        if not use_reuse_table and (user_id_for_ak or org_id):
            logger.info(
                'run_agent_sync: skip reuse table (missing user_id or org_id); '
                'user_id=%s org_id=%r — 请确保请求带 X-User-Id 且上游带 X-Org-Id',
                user_id_for_ak,
                org_id or '(empty)',
            )
        if use_reuse_table:
            nodes_table = get_bohrium_nodes_table()
            creator_id = _creator_id_from_user(user_id_for_ak)
            try:
                tracked_node_ids = nodes_table.list_node_ids_for_user_org(
                    user_id_for_ak, org_id
                )
                cleanup_node_name = 'matmaster-session'
                destroyed_node_ids = node_svc.destroy_untracked_nodes_by_name(
                    access_key,
                    tracked_node_ids,
                    node_name=cleanup_node_name,
                    creator_id=creator_id,
                )
                if destroyed_node_ids:
                    logger.info(
                        'run_agent_sync: destroyed untracked nodes user_id=%s org_id=%s '
                        'name=%s node_ids=%s',
                        user_id_for_ak,
                        org_id,
                        cleanup_node_name,
                        destroyed_node_ids,
                    )
            except Exception as cleanup_err:
                logger.warning(
                    'run_agent_sync: cleanup untracked nodes failed user_id=%s org_id=%s: %s',
                    user_id_for_ak,
                    org_id,
                    cleanup_err,
                )
            row = nodes_table.find_one_for_reuse(user_id_for_ak, org_id, project_id)
            expected_image_name = (
                os.environ.get('BOHRIUM_EXPECTED_IMAGE_NAME')
                or os.environ.get('BOHRIUM_IMAGE_NAME')
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

            if row:
                node_id = int(row['node_id'])
                node_info = node_svc.get_node_info(access_key, node_id)
                logger.info(
                    'run_agent_sync: node image check (ready) node_id=%s '
                    'node_image_name=%s expected_image_name=%s',
                    node_id,
                    node_info.get('image_name') if node_info else None,
                    expected_image_name,
                )
                if node_info and node_info.get('ip'):
                    if _node_image_outdated(node_info.get('image_name')):
                        logger.info(
                            'run_agent_sync: reuse skipped, node image outdated '
                            'node_id=%s node_image_name=%s expected_image_name=%s, destroy and create new',
                            node_id,
                            node_info.get('image_name'),
                            expected_image_name,
                        )
                        nodes_table.delete_by_node(
                            user_id_for_ak,
                            org_id,
                            project_id,
                            node_id,
                        )
                        try:
                            node_svc.destroy_node(
                                access_key,
                                node_id,
                                project_id,
                                creator_id=creator_id,
                            )
                        except Exception as destroy_err:
                            logger.warning(
                                'run_agent_sync: destroy outdated node_id=%s failed: %s',
                                node_id,
                                destroy_err,
                            )
                        node_id = None
                        node_info = None
                    else:
                        node_ip = node_info.get('ip')
                        node_pwd = node_info.get('password')
                        logger.info(
                            'run_agent_sync: reusing Bohrium node node_id=%s ip=%s',
                            node_id,
                            node_ip,
                        )
                else:
                    node_detail = node_svc.get_node_detail(access_key, node_id)
                    logger.info(
                        'run_agent_sync: node image check (not ready) node_id=%s '
                        'node_image_name=%s expected_image_name=%s',
                        node_id,
                        node_detail.get('image_name') if node_detail else None,
                        expected_image_name,
                    )
                    if node_detail is not None and _node_image_outdated(
                        node_detail.get('image_name')
                    ):
                        logger.info(
                            'run_agent_sync: node image outdated node_id=%s '
                            'node_image_name=%s expected_image_name=%s, destroy and create new',
                            node_id,
                            node_detail.get('image_name'),
                            expected_image_name,
                        )
                        nodes_table.delete_by_node(
                            user_id_for_ak,
                            org_id,
                            project_id,
                            node_id,
                        )
                        try:
                            node_svc.destroy_node(
                                access_key,
                                node_id,
                                project_id,
                                creator_id=creator_id,
                            )
                        except Exception as destroy_err:
                            logger.warning(
                                'run_agent_sync: destroy outdated node_id=%s failed: %s',
                                node_id,
                                destroy_err,
                            )
                        node_id = None
                    else:
                        try:
                            node_svc.restart_node(
                                access_key,
                                node_id,
                                project_id,
                                creator_id=creator_id,
                            )
                            _emit_node_status(
                                event_callback,
                                node_id,
                                'created',
                                '节点已重启，正在等待就绪...',
                            )
                            node_info = node_svc.wait_until_ready(access_key, node_id)
                            node_ip = node_info.get('ip')
                            node_pwd = node_info.get('password')
                            logger.info(
                                'run_agent_sync: restarted Bohrium node node_id=%s ip=%s',
                                node_id,
                                node_ip,
                            )
                        except Exception as restart_err:
                            logger.warning(
                                'run_agent_sync: restart node_id=%s failed, will create new: %s',
                                node_id,
                                restart_err,
                            )
                            nodes_table.delete_by_node(
                                user_id_for_ak,
                                org_id,
                                project_id,
                                node_id,
                            )
                            node_id = None
        if node_id is None or node_ip is None:
            node_info = node_svc.create_node(access_key, project_id)
            node_id = node_info.get('node_id')
            if node_id is not None:
                _emit_node_status(
                    event_callback,
                    node_id,
                    'created',
                    '节点已创建，正在等待就绪...',
                )
                node_info = node_svc.wait_until_ready(access_key, node_id)
                node_ip = node_info.get('ip')
                node_pwd = node_info.get('password')
                if use_reuse_table and user_id_for_ak and org_id:
                    try:
                        get_bohrium_nodes_table().insert_node(
                            user_id_for_ak,
                            org_id,
                            project_id,
                            node_id,
                        )
                        logger.info(
                            'run_agent_sync: inserted node into evo_bohrium_nodes '
                            'user_id=%s org_id=%s project_id=%s node_id=%s',
                            user_id_for_ak,
                            org_id,
                            project_id,
                            node_id,
                        )
                    except Exception as insert_err:
                        logger.warning(
                            'run_agent_sync: insert_node failed (table missing?): %s',
                            insert_err,
                            exc_info=True,
                        )
        if node_id is not None and node_ip:
            if session_id not in SESSIONS:
                SESSIONS[session_id] = {}
            SESSIONS[session_id]['bohrium_node_id'] = node_id
            _emit_node_status(
                event_callback,
                node_id,
                'ready',
                'Bohrium 节点已就绪',
                ip=node_ip,
            )
            pg.attach_ssh_session(
                host=node_ip,
                password=node_pwd,
                working_dir='/personal/workspace',
                session_id=session_id,
            )
            apply_run_credentials_to_session(base.session, run_creds)
            logger.info(
                'run_agent_sync: SSH session attached to Bohrium node ip=%s',
                node_ip,
            )
            _run_clear_remote_proxy(pg, 'post_ssh')
            try:
                pg.sync_skills_to_remote()
                _emit_node_status(
                    event_callback,
                    node_id,
                    'skills_synced',
                    'Skills 已同步到远程节点',
                    ip=node_ip,
                )
            except Exception as sync_err:
                logger.warning(
                    'sync_skills_to_remote failed: %s',
                    sync_err,
                    exc_info=True,
                )
            _run_clear_remote_proxy(pg, 'post_skills_sync')
            _emit_node_status(
                event_callback,
                node_id,
                'connected',
                f'已连接到 Bohrium 节点 {node_ip}',
                ip=node_ip,
            )
        return BohriumSetupResult(bool(node_ip), None)
    except Exception as e:
        reason = f'Bohrium 节点创建失败: {e}'
        logger.warning(
            'run_agent_sync: auto create Bohrium node failed: %s',
            e,
            exc_info=True,
        )
        _emit_node_status(event_callback, None, 'failed', reason)
        event_callback('System', 'error', reason)
        elapsed_ms = int((time.monotonic() - run_started_at) * 1000)
        try:
            event_callback(
                'System',
                'stream_closed',
                'Bohrium 节点创建失败，会话已结束.',
                elapsed_ms=elapsed_ms,
            )
        except Exception:
            pass
        return BohriumSetupResult(False, ((False, reason), elapsed_ms))


def cleanup_bohrium_after_run(
    *,
    session_id: str,
    sessions_service: Any,
    event_callback: Callable[..., None],
    pg_for_run: Any,
    ssh_attached: bool,
) -> None:
    """Restore session state and cleanup or release Bohrium node."""
    if ssh_attached and pg_for_run is not None:
        try:
            pg_for_run.detach_session()
            pg_for_run._setup_session()
            logger.info(
                'run_agent_sync: SSH session detached, default session restored'
            )
        except Exception as e:
            logger.warning('run_agent_sync: session restore failed: %s', e)

    session_data = SESSIONS.get(session_id, {})
    node_id = session_data.pop('bohrium_node_id', None)
    row = sessions_service.get_session(session_id)
    org_id = ''
    project_id = None
    if row:
        org_id = (row.get('org_id') or '').strip()
        project_id = row.get('project_id')
        if project_id is not None:
            project_id = int(project_id)
    user_id = sessions_service.get_session_user_id(session_id)
    access_key = ''
    if user_id and org_id:
        access_key = UserService.get_bohrium_access_key(user_id, org_id) or ''
    if node_id is not None and user_id and org_id and project_id is not None:
        try:
            get_bohrium_nodes_table().update_last_used_at(
                user_id, org_id, int(project_id), int(node_id)
            )
            logger.info(
                'run_agent_sync: updated last_used_at for node_id=%s (reuse table)',
                node_id,
            )
        except Exception as e:
            logger.warning(
                'run_agent_sync: update_last_used_at failed node_id=%s: %s',
                node_id,
                e,
            )
    elif node_id is not None:
        try:
            _emit_node_status(event_callback, int(node_id), 'destroyed', '节点已销毁')
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
                    'run_agent_sync: auto destroy Bohrium node node_id=%s failed: %s',
                    node_id,
                    e,
                    exc_info=True,
                )
