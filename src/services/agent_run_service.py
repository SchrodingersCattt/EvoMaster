"""Agent execution service: matmaster pipeline orchestration.

Pipeline: Playground.prepare() -> get_chat_events_table() -> RunEventFanout ->
Bohrium -> WorkspaceHandler -> Exp.run_stream() -> fanout.dispatch() ->
post-processing.
"""

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable
from contextlib import aclosing
from functools import lru_cache
from pathlib import Path
from typing import Any

from matmaster.config.loader import load_agents_general_llm
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.playground import PlaygroundManager
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.integration.event_payloads import _normalize_public_source
from matmaster.integration.fanout import RunEventFanout
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.types.cancellation import CancellationToken
from matmaster.types.events import (
    BusEvent,
    CancelledEvent,
    ErrorEvent,
    RunResultEvent,
    StreamClosedEvent,
    ToolResultEvent,
)
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.runtime_ports import AgentRunPorts, FigureUploadPort
from src.dao.chat_events_table import get_chat_events_table
from src.dao.redis_dao import get_redis_dao
from src.services.agent_run_bohrium_stage import run_bohrium_stage
from src.services.agent_run_history_wiring import build_history_wiring
from src.services.billing_llm_provider import BillingLLMProvider
from src.services.billing_service import BillingRunContext, get_billing_service
from src.services.figure_coordinator import FigureCoordinator
from src.services.history_checkpoint_service import HistoryCheckpointService
from src.services.image_input_service import get_image_input_service
from src.services.sessions_service import get_sessions_service
from src.services.stream_reply_queue import RedisReplyQueue
from src.services.user_turn_context_service import (
    write_user_turn_context_event as _persist_utc_event,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Multi-turn dialog: max events from DB to avoid context overflow
_DIALOG_HISTORY_MAX_EVENTS = int(
    os.environ.get("CHAT_DIALOG_HISTORY_MAX_EVENTS", "500")
)

_project_root = Path(__file__).resolve().parent.parent.parent
RUN_ID_WEB = "mat_master_web"

_MATMASTER_CONFIG_DIR = _project_root / "config"


@lru_cache(maxsize=1)
def _get_agent_default_llm() -> str | None:
    """Cached read of ``agents.general.llm`` from ``config/config.yaml``."""
    return load_agents_general_llm(_MATMASTER_CONFIG_DIR / "config.yaml")


_INVALID_FINISH_MESSAGES: dict[str, str] = {
    "output_length_exceeded": (
        "模型输出被 provider 的输出 token 上限截断，"
        "未形成可提交的最终回答，请稍后重试。"
    ),
    "content_filtered": "模型输出被 provider 内容策略截断或拦截，未形成可提交的最终回答。",
    "reasoning_only": "模型只返回了思考内容，没有生成可见最终回答。请重试。",
    "empty_response": "模型本轮没有返回可见最终回答。请重试。",
    "missing_llm_response": "模型流结束但没有返回可验证的响应对象。请重试。",
    "missing_tool_call_payload": (
        "模型声明要调用工具，但 provider 流式响应未返回有效工具调用参数。"
        "系统已重试仍未恢复，请重试或切换模型。"
    ),
}
_INVALID_FINISH_DEFAULT = "模型没有返回有效最终回答。请重试。"


def _invalid_finish_error_message(finish_detail: Any) -> str:
    kind = getattr(finish_detail, "kind", None)
    if kind is None and isinstance(finish_detail, dict):
        kind = finish_detail.get("kind")
    return _INVALID_FINISH_MESSAGES.get(kind, _INVALID_FINISH_DEFAULT)


def _build_run_usage_summary(event: RunResultEvent) -> dict[str, Any] | None:
    """从 ``RunResultEvent`` 提取 token 消耗摘要，供飞书通知等审计展示。

    ``event.usage`` 是 run-level aggregate scalar usage，包含 root accepted
    LLM turns、Agent subagent usage 和 compaction summary usage。``usage_vendor_by_turn``
    仍只表示 root accepted turns 的 provider-native 快照，不参与 aggregate cache /
    reasoning 补账。无任何 usage 信息时返回 ``None``。
    """
    usage = dict(event.usage or {})
    last_turn_usage: dict[str, int] = {}
    if event.finish_detail is not None:
        last_turn_usage = dict(event.finish_detail.last_turn_usage or {})

    if not usage and not last_turn_usage:
        return None

    prompt = int(usage.get('prompt_tokens') or 0)
    completion = int(usage.get('completion_tokens') or 0)
    total = int(usage.get('total_tokens') or 0) or (prompt + completion)
    cache_read = int(usage.get('cache_read_tokens') or 0)
    cache_write = int(usage.get('cache_write_tokens') or 0)
    reasoning = int(usage.get('reasoning_tokens') or 0)

    summary: dict[str, Any] = {
        'num_turns': int(event.num_turns or 0),
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'total_tokens': total,
    }
    if cache_read:
        summary['cache_read_tokens'] = cache_read
    if cache_write:
        summary['cache_write_tokens'] = cache_write
    if reasoning:
        summary['reasoning_tokens'] = reasoning
    if last_turn_usage:
        summary['last_turn_usage'] = dict(last_turn_usage)
    return summary


async def _attach_run_cost(
    usage_summary: dict[str, Any] | None,
    invocation_id: str | None,
) -> dict[str, Any] | None:
    """best-effort 查本轮 run 全链路费用并并入 usage_summary，供飞书卡片展示。

    费用口径是 invocation 维度的全链路（含子 agent / 压缩），与 token 摘要的
    root-kernel 口径不同；查询失败/无账单时原样返回，不影响完成卡片。
    """
    if not invocation_id:
        return usage_summary
    try:
        cost = await get_billing_service().get_run_cost(invocation_id)
    except Exception:
        cost = None
    if not cost:
        return usage_summary
    enriched = dict(usage_summary or {})
    enriched['cost'] = cost
    return enriched


async def _emit_error_and_close_fanout(
    fanout: RunEventFanout, message: str, source: str = "System"
) -> None:
    """Dispatch ErrorEvent + StreamClosedEvent(treat_as_failure) pair via fanout."""
    await fanout.dispatch(ErrorEvent(source=source, message=message))
    await fanout.dispatch(
        StreamClosedEvent(
            source=source,
            end_reason="error",
            task_completed=False,
            treat_as_failure=True,
        )
    )


def _build_user_turn_context_writer(
    *,
    events_table: Any,
    session_id: str,
):
    async def _writer(request) -> None:
        payload = {
            "schema_version": request.schema_version,
            "kind": request.kind,
            "message": request.message.model_dump(mode="json"),
            "user_instructions_hash": request.user_instructions_hash,
            "transform": request.transform,
            "render_version": request.render_version,
        }
        await _persist_utc_event(
            events_table=events_table,
            session_id=session_id,
            task_id=request.task_id,
            invocation_id=request.invocation_id,
            spawn_id=request.spawn_id,
            payload=payload,
        )

    return _writer


class AgentRunService:
    """Agent execution service: pipeline orchestration via matmaster components."""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._pg_manager = PlaygroundManager(_project_root)

    def init_playground_sync(self) -> None:
        """Validate configs at startup -- delegates to PlaygroundManager."""
        self._pg_manager.validate_startup()

    async def run_agent(
        self,
        session_id: str,
        user_prompt: str,
        send_cb: Callable[[dict], Any],
        cancel_token: CancellationToken,
        mode: str,
        task_id: str,
        invocation_id: str | None = None,
        llm_override: str | None = None,
        model_override: str | None = None,
        byok_credential_id: str | None = None,
        user_id: str | None = None,
        images: list[str] | None = None,
        turn_input: TurnInput | None = None,
        bohrium_required: bool = False,
        remote_workdir: str | None = None,
    ) -> tuple[bool | tuple[bool, str], int, dict[str, Any] | None]:
        """Execute agent pipeline using generator event stream with fanout dispatch.

        Events flow through RunEventFanout directly to handlers:
        kernel._run_items() -> kernel.run_stream() -> exp.run_stream()
        -> source normalization -> fanout.dispatch()

        SSE handler is awaited first (low latency), persistence runs as
        background tasks, WorkspaceHandler receives events inline.

        Returns ``(run_result, elapsed_ms, usage_summary)`` where ``run_result``
        is ``True`` on success or ``(False, reason)`` on failure/cancel, and
        ``usage_summary`` is the token-usage breakdown (or ``None`` when the run
        ended before any LLM turn / usage was available).
        """

        def _elapsed_ms() -> int:
            return int((time.monotonic() - run_started_at) * 1000)

        prompt_preview = (
            (user_prompt[:80] + "...") if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            "run_agent start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s",
            session_id,
            task_id,
            mode,
            len(user_prompt),
            prompt_preview,
        )
        run_started_at = time.monotonic()
        fanout: RunEventFanout | None = None
        exp = None
        bohrium_svc = None
        ssh_attached = False
        playground = None
        try:
            # -- Stage 1: Playground --
            self.init_playground_sync()
            task_id = task_id or ("ws_" + uuid.uuid4().hex[:16])
            playground = self._pg_manager.get_or_create(session_id)
            run_dir = str(_project_root / "runs" / RUN_ID_WEB)
            # Physical substrate from Playground; the per-run AgentRunRequest
            # (turn input, llm, ports, ...) is assembled once near Stage 6.
            environment = playground.prepare(
                RunMetadata(run_dir=run_dir, task_id=task_id),
                session_id=session_id,
            )
            try:
                events_table = get_chat_events_table()
            except Exception:
                logger.exception(
                    "run_agent pre-handler setup failed: session_id=%s",
                    session_id,
                )
                return ((False, "pre_router_setup_failed"), 0, None)

            # -- Stage 2: RunEventFanout bootstrap --
            # SSE handler first for lower frontend latency,
            # persistence as background tasks.
            fanout = RunEventFanout(
                sse_handler=SSEHandler(
                    send_cb,
                    session_id,
                    task_id,
                    invocation_id,
                    mode,
                ),
                persistence_handler=PersistenceHandler(
                    events_table,
                    session_id,
                    task_id,
                    invocation_id,
                ),
            )

            exp_name = mode or "direct"
            from matmaster.config.loader import load_exp_config

            exp_config = load_exp_config(exp_name)

            # -- Stage 3: Bohrium credentials + SSH --
            # Thread-safe event sink: Bohrium worker-thread callbacks
            # schedule fanout.dispatch() onto the event loop.
            loop = asyncio.get_running_loop()

            def _dispatch_from_thread(event: BusEvent) -> None:
                fanout.dispatch_from_thread(loop, event)

            stage_result = await run_bohrium_stage(
                sessions_service=self._sessions_service,
                fanout=fanout,
                dispatch_from_thread=_dispatch_from_thread,
                session_id=session_id,
                task_id=task_id,
                playground=playground,
                environment=environment,
                run_started_at=run_started_at,
                bohrium_required=bohrium_required,
                remote_workdir=remote_workdir,
            )
            bohrium_svc = stage_result.bohrium_svc
            if stage_result.abort_result is not None:
                abort = stage_result.abort_result
                return (abort[0], abort[1], None)
            environment = stage_result.environment
            ssh_attached = stage_result.ssh_attached
            user_instructions = stage_result.user_instructions

            # -- Stage 4: Exp assembly --
            from matmaster.config.loader import load_llm_config
            from matmaster.core.exp import Exp
            from matmaster.providers.llm_factory import build_provider_bundle

            llm_config = load_llm_config(_project_root / "config" / "llm_config.yaml")

            agent_default_llm = _get_agent_default_llm()
            image_service = get_image_input_service()
            top_level_images = tuple(images or ())
            current_images = image_service.select_current_images(
                turn_input, top_level_images
            )

            byok_id = (byok_credential_id or "").strip() or None
            if byok_id:
                # BYOK：凭证由 tools-server 下发，绕开 llm_config / routes，用户自付不扣额度。
                from matmaster.providers.llm_factory import build_byok_provider_bundle
                from src.services.llm_credential_client import (
                    ByokCredentialError,
                    fetch_byok_credential,
                )

                try:
                    cred = await fetch_byok_credential(
                        user_id=user_id or "", credential_id=byok_id
                    )
                except ByokCredentialError as exc:
                    logger.warning(
                        "byok credential fetch failed session_id=%s cred=%s: %s",
                        session_id,
                        byok_id,
                        exc,
                    )
                    return ((False, "byok_credential_unavailable"), _elapsed_ms(), None)

                llm_bundle = build_byok_provider_bundle(
                    model=cred.model,
                    api_key=cred.api_key,
                    base_url=cred.base_url,
                    credential_id=byok_id,
                    extra_body=cred.extra_body,
                )
                # BYOK 第一期不接入族级 vision 校验：有图片用默认 detail，无图为 None。
                image_detail = "high" if current_images else None
                billing_mode = "byok"
            else:
                image_detail = image_service.resolve_image_detail(
                    llm_config=llm_config,
                    images=current_images,
                    llm_override=llm_override,
                    model_override=model_override,
                    default_profile_key=agent_default_llm,
                )
                llm_bundle = build_provider_bundle(
                    llm_config,
                    model_override=model_override,
                    llm_override=llm_override,
                    default_profile_key=agent_default_llm,
                )
                billing_mode = "platform"
            llm_provider = llm_bundle.provider
            try:
                llm_provider = BillingLLMProvider(
                    llm_provider,
                    run_context=BillingRunContext(
                        session_id=session_id,
                        task_id=task_id,
                        invocation_id=invocation_id,
                    ),
                    model=llm_bundle.model,
                    billing_service=get_billing_service(),
                    billing_mode=billing_mode,
                )
            except Exception:
                logger.warning(
                    "billing wrapper init failed session_id=%s",
                    session_id,
                    exc_info=True,
                )

            exp = Exp(exp_config)

            if environment.session is not None:
                environment.session._cancel_token = cancel_token

            checkpoint_service = (
                HistoryCheckpointService(events_table)
                if events_table is not None
                else None
            )
            figure_coordinator = FigureCoordinator(
                fanout=fanout,
                session_id=session_id,
                task_id=task_id,
            )
            figure_upload_config = figure_coordinator.upload_config

            def _checkpoint_sink_factory(*, spawn_id: str | None = None):
                if checkpoint_service is None:

                    async def _noop_checkpoint_sink(
                        *,
                        payload: dict[str, Any],
                        base_messages: list[dict[str, Any]],
                    ) -> None:
                        return None

                    return _noop_checkpoint_sink
                return checkpoint_service.build_checkpoint_sink(
                    fanout=fanout,
                    session_id=session_id,
                    task_id=task_id,
                    invocation_id=invocation_id,
                    spawn_id=spawn_id,
                )

            # -- Stage 4b: AskQuestion bridge --
            from matmaster.integration.interaction_bridge import AskQuestionBridge

            async def _interaction_event_sink(event: BusEvent) -> None:
                await fanout.dispatch(event)

            bridge = AskQuestionBridge(
                session_id=session_id,
                event_sink=_interaction_event_sink,
                reply_queue=RedisReplyQueue(session_id),
                timeout_seconds=1800,
            )
            # -- Stage 5: History --
            wiring = build_history_wiring(
                events_table=events_table,
                session_id=session_id,
                task_id=task_id,
                raw_history_limit=_DIALOG_HISTORY_MAX_EVENTS,
                checkpoint_sink_factory=_checkpoint_sink_factory,
                pre_compaction_barrier=fanout.flush_persistence_barrier,
            )
            history = wiring.history
            bohrium_rebuild_events = (
                tuple(wiring.bohrium_rebuild_events)
                if wiring.bohrium_rebuild_events
                else ()
            )
            from src.services.interrupt_service import RedisInterruptChecker

            # -- Stage 5b: Turn input enrichment --
            turn_input = image_service.enrich_turn_input_images(
                turn_input=turn_input,
                user_prompt=user_prompt,
                top_level_images=top_level_images,
                image_detail=image_detail,
            )

            # -- Compose the Exp input from the prepared environment and
            # service-owned runtime request.
            agent_run_ctx = AgentRunContext(
                environment=environment,
                request=AgentRunRequest(
                    llm_provider=llm_provider,
                    llm_config=llm_config,
                    llm_model=llm_bundle.model,
                    llm_model_profile=llm_bundle.model_profile,
                    llm_model_route=llm_bundle.model_route,
                    invocation_id=invocation_id,
                    interaction_bridge=bridge,
                    turn_input=turn_input,
                    user_instructions=user_instructions,
                    active_skills=frozenset(),
                    bohrium_rebuild_events=bohrium_rebuild_events,
                    ports=AgentRunPorts(
                        child_event_forward_sink=figure_coordinator.child_event_sink,
                        compaction=wiring.compaction,
                        figure_upload=FigureUploadPort(config=figure_upload_config),
                        interrupt_checker=RedisInterruptChecker(session_id),
                        user_turn_context_writer=_build_user_turn_context_writer(
                            events_table=events_table,
                            session_id=session_id,
                        ),
                    ),
                ),
            )

            # -- Stage 6: Generator event stream --
            run_result_event = None
            async with aclosing(
                exp.run_stream(
                    agent_run_ctx,
                    history=history,
                    cancel_token=cancel_token,
                )
            ) as stream:
                async for event in stream:
                    if hasattr(event, "source"):
                        normalized = _normalize_public_source(event.source)
                        if event.source != normalized:
                            event = event.model_copy(update={"source": normalized})

                    if isinstance(event, RunResultEvent) and event.spawn_id is None:
                        await figure_coordinator.flush_if_dirty("final_flush")

                    await fanout.dispatch(event)

                    if isinstance(event, ToolResultEvent):
                        await figure_coordinator.record_tool_result(
                            event,
                            include_spawned=False,
                            reason="tool_result",
                        )

                    # Detect terminal event
                    if isinstance(event, RunResultEvent):
                        run_result_event = event

            # -- Post-processing --
            if run_result_event is None:
                await _emit_error_and_close_fanout(
                    fanout, "Generator terminated without result"
                )
                return ((False, "no_result"), _elapsed_ms(), None)

            usage_summary = _build_run_usage_summary(run_result_event)
            usage_summary = await _attach_run_cost(usage_summary, invocation_id)
            if run_result_event.reason == "cancelled":
                await fanout.dispatch(
                    CancelledEvent(source="System", reason="Task cancelled by user.")
                )
                await fanout.dispatch(
                    StreamClosedEvent(
                        source="System",
                        end_reason="cancelled",
                        task_completed=False,
                    )
                )
                return ((False, "cancelled"), _elapsed_ms(), usage_summary)
            else:
                if run_result_event.reason == "invalid_finish":
                    await fanout.dispatch(
                        ErrorEvent(
                            source="System",
                            message=_invalid_finish_error_message(
                                run_result_event.finish_detail
                            ),
                        )
                    )
                await fanout.dispatch(
                    StreamClosedEvent(
                        source="System",
                        task_completed=run_result_event.reason == "natural",
                        end_reason=run_result_event.reason,
                        treat_as_failure=run_result_event.status == "failed" or None,
                    )
                )
                if run_result_event.status == "completed":
                    # 扣费由 tools-server 侧按金额实时完成（billing usage 上报），
                    # evo 不再做按次扣减。
                    return (True, _elapsed_ms(), usage_summary)
                fail_reason = (
                    run_result_event.reason or run_result_event.status or "failed"
                )
                return ((False, fail_reason), _elapsed_ms(), usage_summary)

        except Exception as exc:
            logger.exception("run_agent error: session_id=%s", session_id)
            if fanout is not None:
                try:
                    await _emit_error_and_close_fanout(fanout, str(exc))
                except Exception:
                    pass
            return ((False, str(exc)), _elapsed_ms(), None)
        finally:
            elapsed = time.monotonic() - run_started_at
            logger.info(
                "run_agent done: session_id=%s elapsed=%.1fs",
                session_id,
                elapsed,
            )
            # Cleanup order matters:
            # 1. Bohrium cleanup -- can still emit events via event bridge
            if bohrium_svc:
                try:
                    await bohrium_svc.run_cleanup(
                        session_id=session_id,
                        pg_for_run=playground,
                        ssh_attached=ssh_attached,
                    )
                except Exception:
                    logger.warning("Bohrium cleanup error", exc_info=True)
            # 2. Exp cleanup is owned by Exp.run_stream() via its finally block.
            #    aclosing() guarantees the generator's finally runs before we
            #    reach this point, so the callback list is already cleared.
            #    Do NOT re-invoke exp._run_cleanup_callbacks() here.
            # 3. Fanout LAST -- drains pending persistence and calls handler close()
            if fanout is not None:
                try:
                    await asyncio.wait_for(fanout.drain_and_close(), timeout=10)
                except Exception:
                    logger.warning(
                        "fanout.drain_and_close() failed during cleanup",
                        exc_info=True,
                    )
            get_redis_dao().delete_stop_requested(session_id, task_id)
            self._pg_manager.release(session_id)


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """Initialize playground at startup (called in lifespan)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
