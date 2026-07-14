"""Agent Worker 入口：从 Redis 队列 BLPOP 任务，执行 run_agent；事件由 run_agent 内 event_callback 写 DB，本处仅 publish 到 Redis。
供独立 Worker Deployment 使用，与 API 共用同一代码库与镜像（Dockerfile --target worker）。
Worker 需周期刷新 worker_alive，否则 API 在用户刷新页面时会误判 run 为 stale 并推送 run_interrupted。
"""

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from matmaster.config.exp import DEFAULT_MODE, SUPPORTED_MODES
from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.cancellation import CancellationController
from src.dao.redis_dao import get_redis_dao
from src.models.chat import DeliverySpec
from src.services import bohrium_delivery_ack
from src.services.agent_run_service import get_agent_run_service
from src.services.sessions_service import get_sessions_service
from src.services.user_service import UserService
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import SERVICE_ENV
from src.utils.feishu_notifier import (
    CARD_TEMPLATE_BLUE,
    CARD_TEMPLATE_GREEN,
    CARD_TEMPLATE_ORANGE,
    CARD_TEMPLATE_RED,
    format_llm_model_for_notify,
    format_usage_rows,
    notify_post_async,
)
from src.utils.logger import LogContext, LoggingConfig, setup_logging
from src.utils.support_notifier import send_session_complete_email_async
from src.utils.worker_id import get_worker_id
from utils.tracing import configure_tracing, shutdown_tracing

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 失败原因 code -> 运营/用户可读文案（飞书卡片、完成邮件复用）。未命中的 code 原样展示。
_FAIL_REASON_DISPLAY: dict[str, str] = {
    # in-run 成本熔断：额度耗尽被系统止损中止，区别于「用户取消」。
    "quota_exhausted": "额度已用完，本轮已自动停止",
}

# BLPOP 超时（秒），超时后继续循环，便于进程能快速响应 SIGTERM draining
_BLPOP_TIMEOUT = int(os.environ.get("AGENT_WORKER_BLPOP_TIMEOUT", "5"))
# 存活心跳间隔（秒），需小于 Redis WORKER_ALIVE_TTL_SEC(30)，否则 API 会误判本进程已死
_WORKER_HEARTBEAT_INTERVAL = 10.0
# 当前正在跑的 session_id（由主循环设置/清除），供心跳线程刷新 run_owner TTL，避免长任务超过 SESSION_RUN_OWNER_TTL 后 API 误判 stale
_current_session_id: str | None = None
# 优雅退出：SIGTERM 时设为 True，主循环在「当前 run 结束后」或「空闲时」退出，不再接新任务
_drain_requested = False
_active_controller: CancellationController | None = None


def _session_url(session_id: str) -> str:
    """根据当前环境拼接前端会话链接。"""
    sid = (session_id or "").strip()
    if not sid:
        return "-"
    env = (SERVICE_ENV or "").strip().lower()
    suffix = "" if not env or env == "prod" else f".{env}"
    return f"https://matmaster{suffix}.bohrium.com/matmaster/chat-evo/{sid}"


def _format_run_duration(duration_sec: float) -> str:
    """把运行秒数格式化为「X 秒 / X 分 X 秒 / X 小时 X 分」。"""
    if duration_sec < 60:
        return f"{duration_sec:.1f} 秒"
    if duration_sec < 3600:
        m = int(duration_sec // 60)
        s = int(duration_sec % 60)
        return f"{m} 分 {s} 秒"
    h = int(duration_sec // 3600)
    m = int((duration_sec % 3600) // 60)
    return f"{h} 小时 {m} 分"


def _should_notify_completion(delivery: dict | None) -> bool:
    """job.delivery 控制完成通知；缺省语义唯一来源是 DeliverySpec 的字段默认值。"""
    try:
        return DeliverySpec.model_validate(delivery or {}).notify
    except Exception:  # noqa: BLE001
        return True


def _build_completion_card(
    *,
    session_id: str,
    session_url: str,
    user_info_display: str,
    model: str | None,
    user_question: str,
    run_success: bool,
    fail_reason: str | None,
    fail_reason_str: str,
    duration_str: str,
    active_count: int,
    queue_len: int,
    usage_summary: dict | None,
) -> tuple[str, list[tuple[str, str]], str]:
    """构建会话完成/失败/取消的飞书卡片，返回 ``(title, rows, template)``。

    纯函数，无副作用，便于单测（含失败原因行与 token 明细行的插入位置）。
    """
    rows: list[tuple[str, str]] = [
        ("会话ID", session_id),
        ("会话地址", session_url),
        ("用户", user_info_display),
        ("模型", format_llm_model_for_notify(model)),
        ("用户问题", user_question or "-"),
        ("执行节点", get_worker_id()),
        (
            "结果",
            (
                "成功"
                if run_success
                else ("已取消" if fail_reason == "cancelled" else "失败")
            ),
        ),
        ("运行时间", duration_str),
        ("执行中", str(active_count)),
        ("排队数", str(queue_len)),
    ]
    if not run_success and fail_reason_str and fail_reason_str != "cancelled":
        reason = (fail_reason_str or "-")[:500]
        if len(fail_reason_str) > 500:
            reason = reason + "…"
        rows.insert(7, ("失败原因", reason))  # 插在「结果」之后
    # token 消耗明细插在「运行时间」之后、「执行中/排队数」之前
    usage_rows = format_usage_rows(usage_summary)
    if usage_rows:
        try:
            anchor = next(i for i, (label, _) in enumerate(rows) if label == "运行时间")
            insert_at = anchor + 1
        except StopIteration:
            insert_at = len(rows)
        rows[insert_at:insert_at] = usage_rows
    if fail_reason == "cancelled":
        return "用户取消运行", rows, CARD_TEMPLATE_ORANGE
    title = "Worker 执行成功" if run_success else "Worker 执行失败"
    template = CARD_TEMPLATE_GREEN if run_success else CARD_TEMPLATE_RED
    return title, rows, template


def _send_completion_email(
    *,
    session_user_id: str | None,
    user_info: dict,
    payload: dict,
    session_url: str,
    user_question: str,
    duration_str: str,
    run_success: bool,
    fail_reason: str | None,
    fail_reason_str: str,
) -> None:
    """会话完成/失败时给用户发完成邮件（含会话链接）。无 user_id 或邮箱时跳过。"""
    email = user_info.get("email")
    if not (session_user_id and email and email != "-"):
        return
    submitted_at_raw = payload.get("submitted_at") or ""
    try:
        if submitted_at_raw:
            dt = datetime.fromisoformat(submitted_at_raw.replace("Z", "+00:00"))
            submitted_at_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            submitted_at_str = ""
    except (ValueError, TypeError):
        submitted_at_str = submitted_at_raw or ""
    result_status = (
        "成功" if run_success else ("已取消" if fail_reason == "cancelled" else "失败")
    )
    fail_reason_for_email = (
        fail_reason_str if not run_success and fail_reason_str != "cancelled" else ""
    )
    if len(fail_reason_for_email) > 500:
        fail_reason_for_email = fail_reason_for_email[:500] + "…"
    send_session_complete_email_async(
        session_url,
        session_user_id,
        email,
        user_question=user_question or "",
        submitted_at=submitted_at_str,
        duration=duration_str,
        result_status=result_status,
        fail_reason=fail_reason_for_email,
        completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


class RedisCancellationBridge:
    """Daemon thread that polls Redis stop flag and cancels a controller."""

    def __init__(
        self,
        controller: CancellationController,
        session_id: str,
        task_id: str,
        interval: float = 0.5,
        *,
        _dao_override: Any = None,
    ) -> None:
        self._controller = controller
        self._session_id = session_id
        self._task_id = task_id
        self._interval = interval
        self._dao = _dao_override or get_redis_dao()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _poll(self) -> None:
        while not self._shutdown.is_set() and not self._controller.token.is_cancelled:
            if self._dao.is_stop_requested(self._session_id, self._task_id):
                self._controller.cancel()
                break
            self._shutdown.wait(self._interval)


def _worker_heartbeat_loop(stop_ev: threading.Event) -> None:
    """后台线程：周期刷新本进程 worker_alive 与当前 session 的 run_owner TTL，使 API subscribe 时 is_worker_alive(owner) 为 True，
    且长任务不会因 SESSION_RUN_OWNER_TTL（2h）过期导致 run_owner 丢失、误判 stale 并推送 run_interrupted。"""
    while not stop_ev.wait(timeout=_WORKER_HEARTBEAT_INTERVAL):
        try:
            get_worker_registry_service().set_worker_alive(get_worker_id())
            # 刷新当前 run 的 session_run_owner TTL，避免超过 2h 后 key 过期、API 侧 run_owner=None 触发 run_interrupted
            sid = _current_session_id
            if sid:
                get_worker_registry_service().refresh_session_run_owner(
                    sid, get_worker_id()
                )
        except Exception as e:
            logger.warning(
                "Agent worker heartbeat skipped worker_id=%s: %s", get_worker_id(), e
            )


def _run_worker_loop() -> None:
    global _current_session_id, _active_controller
    redis_dao = get_redis_dao()
    if not redis_dao.get_command_client():
        logger.error(
            "Agent worker: REDIS_URL not configured or Redis unreachable. Exit."
        )
        sys.exit(1)

    sessions_service = get_sessions_service()
    agent_run_service = get_agent_run_service()

    agent_run_service.init_playground_sync()

    while True:
        if _drain_requested:
            logger.info(
                "Agent worker: drain requested before polling queue, exiting loop. worker_id=%s",
                get_worker_id(),
            )
            return

        payload = redis_dao.blpop_agent_run_job(timeout_sec=_BLPOP_TIMEOUT)
        if payload is None:
            if _drain_requested:
                logger.info(
                    "Agent worker: drain requested, no current job, exiting loop. worker_id=%s",
                    get_worker_id(),
                )
                return
            continue

        if _drain_requested:
            logger.info(
                "Agent worker: drain requested after receiving job, requeue and exit. worker_id=%s",
                get_worker_id(),
            )
            if redis_dao.lpush_agent_run_job(payload):
                return
            logger.error(
                "Agent worker: failed to requeue job after drain requested; "
                "will process to avoid dropping it. worker_id=%s session_id=%s task_id=%s",
                get_worker_id(),
                (payload.get("session_id") or "").strip(),
                payload.get("task_id") or "",
            )

        session_id = (payload.get("session_id") or "").strip()
        task_id = payload.get("task_id") or ""
        invocation_id = payload.get("invocation_id")
        user_prompt = payload.get("user_prompt") or ""
        mode = (payload.get("mode") or DEFAULT_MODE).strip().lower() or DEFAULT_MODE
        if mode not in SUPPORTED_MODES:
            logger.warning(
                "Agent worker: unknown mode %r in payload, fallback to %s",
                mode,
                DEFAULT_MODE,
            )
            mode = DEFAULT_MODE
        model_override = (payload.get("model") or "").strip() or None
        byok_credential_id = (payload.get("byok_credential_id") or "").strip() or None
        raw_images = payload.get("images") or []
        images = (
            [url for url in raw_images if isinstance(url, str)]
            if isinstance(raw_images, list)
            else []
        )
        turn_input = TurnInput.from_payload(payload.get("turn_input"))
        bohrium_required = bool(payload.get("bohrium_required"))
        submit_confirmation_enabled = bool(
            payload.get("bohrium_submit_confirmation_required")
        )
        bohrium_job_max_runtime_seconds = None
        raw_max_runtime = payload.get("bohrium_job_max_runtime_seconds")
        if raw_max_runtime not in (None, ""):
            try:
                parsed_max_runtime = int(raw_max_runtime)
                if parsed_max_runtime > 0:
                    bohrium_job_max_runtime_seconds = parsed_max_runtime
            except (TypeError, ValueError):
                logger.warning(
                    "Agent worker: ignore invalid bohrium_job_max_runtime_seconds=%r "
                    "session_id=%s task_id=%s",
                    raw_max_runtime,
                    session_id,
                    task_id,
                )
        raw_workspace = payload.get("workspace")
        workspace = (
            raw_workspace.strip() or None if isinstance(raw_workspace, str) else None
        )
        bohrium_node_sku_id = None
        raw_node_sku_id = payload.get("bohrium_node_sku_id")
        if raw_node_sku_id not in (None, ""):
            try:
                parsed_node_sku_id = int(raw_node_sku_id)
                if parsed_node_sku_id > 0:
                    bohrium_node_sku_id = parsed_node_sku_id
            except (TypeError, ValueError):
                logger.warning(
                    "Agent worker: ignore invalid bohrium_node_sku_id=%r "
                    "session_id=%s task_id=%s",
                    raw_node_sku_id,
                    session_id,
                    task_id,
                )
        from matmaster.bohrium.node_lifecycle import resolve_node_lifecycle

        try:
            node_lifecycle_policy, bohrium_node_idle_timeout_seconds = (
                resolve_node_lifecycle(
                    payload.get("bohrium_node_lifecycle_policy"),
                    payload.get("bohrium_node_idle_timeout_seconds"),
                )
            )
        except ValueError:
            logger.warning(
                "Agent worker: invalid Bohrium Node lifecycle snapshot; "
                "fallback run_end session_id=%s task_id=%s",
                session_id,
                task_id,
            )
            node_lifecycle_policy, bohrium_node_idle_timeout_seconds = (
                resolve_node_lifecycle("run_end", None)
            )
        delivery = payload.get("delivery")
        origin = (payload.get("origin") or "").strip() or None
        job_context_mode = (
            "session_workspace_delivery"
            if origin == "bohrium_completion"
            else "workspace_observation"
        )

        if not session_id:
            logger.warning("Agent worker: skip job with empty session_id")
            continue

        LogContext.bind(session_id, task_id)
        session_user_id = sessions_service.get_session_user_id(session_id)
        user_info = UserService.get_user_info_for_display(session_user_id)
        user_info_display = (
            f"{user_info['user_id']} | {user_info['nickname']} | {user_info['email']}"
        )
        # 清除可能残留的上一轮 stop key（含 session 级），避免上一轮 finally 中 delete 失败导致本轮一启动即被误判为已请求停止
        logger.info(
            "Agent worker: clear stop keys before run session_id=%s task_id=%s",
            session_id,
            task_id,
        )
        redis_dao.delete_stop_requested(session_id, task_id)
        redis_dao.set_interaction_run_context(session_id, task_id, invocation_id or "")

        def send_cb(p: dict, _sid: str = session_id) -> None:
            # 不在此处写 DB：run_agent 内 event_callback 已写，此处再写会导致同一条事件落库两次
            redis_dao.publish_stream_event(_sid, p)

        controller = CancellationController()
        _active_controller = controller
        bridge = RedisCancellationBridge(controller, session_id, task_id)
        bridge.start()
        acquired = False
        delivery_snapshot = None
        run_success = False

        try:
            acquired_ok, fail_reason = sessions_service.try_acquire_session_run(
                session_id
            )
            if not acquired_ok and fail_reason == "db_update_failed":
                logger.info(
                    "Agent worker: db_update_failed, retry once after 2s session_id=%s task_id=%s",
                    session_id,
                    task_id,
                )
                time.sleep(2)
                acquired_ok, fail_reason = sessions_service.try_acquire_session_run(
                    session_id
                )
            if not acquired_ok:
                logger.warning(
                    "Agent worker: skip job session_id=%s task_id=%s reason=%s",
                    session_id,
                    task_id,
                    fail_reason or "unknown",
                )
                redis_dao.delete_interaction_run_context(session_id)
                LogContext.clear()
                continue

            acquired = True
            _current_session_id = session_id
            # run 起点固化本轮交付边界；查询失败返回 None 不阻断 run
            delivery_snapshot = bohrium_delivery_ack.snapshot(
                session_id, workspace=workspace
            )
            run_start_time = time.monotonic()
            queue_len = redis_dao.llen_agent_run_queue()
            active_count = get_worker_registry_service().count_active_runs()
            session_url = _session_url(session_id)
            user_question = (user_prompt or "").strip()
            if len(user_question) > 500:
                user_question = user_question[:500] + "…"
            notify_post_async(
                "Worker 开始执行",
                [
                    ("会话ID", session_id),
                    ("会话地址", session_url),
                    ("用户", user_info_display),
                    ("模型", format_llm_model_for_notify(model_override)),
                    ("模式", mode),
                    ("用户问题", user_question or "-"),
                    ("执行节点", get_worker_id()),
                    ("执行中", str(active_count)),
                    ("排队数", str(queue_len)),
                ],
                template=CARD_TEMPLATE_BLUE,
            )
            run_success = True
            fail_reason: str | None = None
            elapsed_ms: int | None = None
            usage_summary: dict | None = None
            try:
                run_agent_kwargs = {
                    "session_id": session_id,
                    "user_prompt": user_prompt,
                    "send_cb": send_cb,
                    "cancel_token": controller.token,
                    "cancel_controller": controller,
                    "mode": mode,
                    "task_id": task_id,
                    "invocation_id": invocation_id,
                    "model_override": model_override,
                    "byok_credential_id": byok_credential_id,
                    "user_id": session_user_id,
                    "images": images,
                    "turn_input": turn_input,
                    "workspace": workspace,
                    "bohrium_required": bohrium_required,
                    "bohrium_job_max_runtime_seconds": (
                        bohrium_job_max_runtime_seconds
                    ),
                    "bohrium_node_sku_id": bohrium_node_sku_id,
                    "bohrium_node_lifecycle_policy": node_lifecycle_policy.value,
                    "bohrium_node_idle_timeout_seconds": (
                        bohrium_node_idle_timeout_seconds
                    ),
                    "submit_confirmation_enabled": submit_confirmation_enabled,
                    "delivery_snapshot": delivery_snapshot,
                    "job_context_mode": job_context_mode,
                }
                result = asyncio.run(agent_run_service.run_agent(**run_agent_kwargs))
                run_result = result
                if isinstance(result, tuple) and len(result) >= 2:
                    run_result = result[0]
                    elapsed_ms = result[1]
                if (
                    isinstance(result, tuple)
                    and len(result) >= 3
                    and isinstance(result[2], dict)
                ):
                    usage_summary = result[2]
                if (
                    isinstance(run_result, tuple)
                    and len(run_result) >= 2
                    and (run_result[0] is False)
                ):
                    run_success = False
                    fail_reason = run_result[1]
                elif run_result is False:
                    run_success = False
                else:
                    run_success = True
            except Exception as e:
                run_success = False
                fail_reason = str(e)
                logger.exception(
                    "Agent worker: run_agent failed session_id=%s task_id=%s: %s",
                    session_id,
                    task_id,
                    e,
                )
                try:
                    send_cb(
                        {
                            "source": "System",
                            "type": "error",
                            "content": str(e),
                            "session_id": session_id,
                            "task_id": task_id,
                            "invocation_id": invocation_id,
                        }
                    )
                    send_cb(
                        {
                            "source": "System",
                            "type": "stream_closed",
                            "content": "",
                            "session_id": session_id,
                            "task_id": task_id,
                            "invocation_id": invocation_id,
                        }
                    )
                except Exception:
                    pass
        finally:
            bridge.stop()
            _active_controller = None
            if acquired:
                _current_session_id = None
                LogContext.clear()
            redis_dao.delete_interaction_run_context(session_id)
            redis_dao.delete_stop_requested(session_id, task_id)
            if acquired:
                if run_success and delivery_snapshot is not None:
                    try:
                        bohrium_delivery_ack.confirm(delivery_snapshot)
                    except Exception:
                        logger.warning(
                            "Agent worker: bohrium delivery confirm failed "
                            "session_id=%s task_id=%s",
                            session_id,
                            task_id,
                            exc_info=True,
                        )
                sessions_service.release_session_run(
                    session_id, run_success=run_success
                )
                try:
                    queue_len = redis_dao.llen_agent_run_queue()
                    active_count = get_worker_registry_service().count_active_runs()
                    session_url = _session_url(session_id)
                    user_question = (user_prompt or "").strip()
                    if len(user_question) > 500:
                        user_question = user_question[:500] + "…"
                    # 优先使用 run_agent 返回的 elapsed_ms（与 end 事件、前端展示一致），异常路径无返回值时用 Worker 侧计时
                    if elapsed_ms is not None:
                        duration_sec = elapsed_ms / 1000.0
                    else:
                        duration_sec = time.monotonic() - run_start_time
                    duration_str = _format_run_duration(duration_sec)
                    fail_reason_str = (
                        str(fail_reason).strip() if fail_reason is not None else ""
                    )
                    fail_reason_str = _FAIL_REASON_DISPLAY.get(
                        fail_reason_str, fail_reason_str
                    )
                    if _should_notify_completion(delivery):
                        title, rows, template = _build_completion_card(
                            session_id=session_id,
                            session_url=session_url,
                            user_info_display=user_info_display,
                            model=model_override,
                            user_question=user_question,
                            run_success=run_success,
                            fail_reason=fail_reason,
                            fail_reason_str=fail_reason_str,
                            duration_str=duration_str,
                            active_count=active_count,
                            queue_len=queue_len,
                            usage_summary=usage_summary,
                        )
                        notify_post_async(title, rows, template=template)
                        logger.info(
                            "Agent worker: Feishu completion card queued session_id=%s title=%s",
                            session_id,
                            title,
                        )
                        # 会话完成/失败时给用户发邮件（模板：会话已执行完成+链接），与飞书通知并行
                        _send_completion_email(
                            session_user_id=session_user_id,
                            user_info=user_info,
                            payload=payload,
                            session_url=session_url,
                            user_question=user_question,
                            duration_str=duration_str,
                            run_success=run_success,
                            fail_reason=fail_reason,
                            fail_reason_str=fail_reason_str,
                        )
                    else:
                        logger.info(
                            "Agent worker: completion notify suppressed by delivery session_id=%s",
                            session_id,
                        )
                except Exception:
                    logger.exception(
                        "Agent worker: completion notify block failed session_id=%s task_id=%s",
                        session_id,
                        task_id,
                    )
        if _drain_requested:
            logger.info(
                "Agent worker: drain requested, current job finished, exiting loop. session_id=%s worker_id=%s",
                session_id,
                get_worker_id(),
            )
            return


def main() -> None:
    setup_logging(**LoggingConfig.get_worker_config())
    configure_tracing("matmaster-evo-worker")

    def _on_sigterm(_signum: int, _frame: object) -> None:
        global _drain_requested
        _drain_requested = True
        logger.info(
            "Agent worker: received SIGTERM, drain requested; "
            "stop accepting new jobs and exit after current job or when idle. worker_id=%s current_session_id=%s",
            get_worker_id(),
            _current_session_id or "",
        )

    signal.signal(signal.SIGTERM, _on_sigterm)

    # 心跳线程：使 API 能通过 is_worker_alive(owner) 识别本进程仍在跑，刷新页面时不误判 run_interrupted
    _heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_worker_heartbeat_loop,
        args=(_heartbeat_stop,),
        name="agent_worker_heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    logger.info(
        "Agent worker: heartbeat thread started interval=%.0fs worker_id=%s",
        _WORKER_HEARTBEAT_INTERVAL,
        get_worker_id(),
    )

    logger.info(
        "Agent worker: starting BLPOP loop queue_key=%s", "chat:agent_run_queue"
    )
    try:
        _run_worker_loop()
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
