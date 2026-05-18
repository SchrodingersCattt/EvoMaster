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
from matmaster.context.assembly import (
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import SkillResolver, UserInstructions
from matmaster.context.scanner import scan_skill_hits
from matmaster.context.sections import ContextView
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.playground import PlaygroundManager
from matmaster.core.runtime_context_assembly import empty_skill_resolver
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
    SkillHitEvent,
    StreamClosedEvent,
    ToolResultEvent,
)
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.runtime_ports import FigureUploadPort
from src.dao.chat_events_table import get_chat_events_table
from src.dao.redis_dao import get_redis_dao
from src.services.agent_run_bohrium_stage import (
    _build_figure_upload_config,
    run_bohrium_stage,
)
from src.services.agent_run_history_wiring import build_history_wiring
from src.services.context_assembly_factory import build_context_assembler
from src.services.context_turn_intent import resolve_turn_context_intent
from src.services.history_checkpoint_service import HistoryCheckpointService
from src.services.image_input_service import get_image_input_service
from src.services.quota_service import use_quota
from src.services.response_figures_service import ResponseFiguresAccumulator
from src.services.session_event_codec import decode_session_events
from src.services.sessions_service import get_sessions_service
from src.services.skill_registry_factory import build_skill_registry
from src.services.skill_resolver import SkillRegistryResolver
from src.services.stream_reply_queue import RedisReplyQueue
from src.services.user_turn_context_service import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
    write_user_turn_context_event,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Multi-turn dialog: max events from DB to avoid context overflow
_DIALOG_HISTORY_MAX_EVENTS = int(
    os.environ.get('CHAT_DIALOG_HISTORY_MAX_EVENTS', '500')
)

_project_root = Path(__file__).resolve().parent.parent.parent
RUN_ID_WEB = 'mat_master_web'

_MATMASTER_CONFIG_DIR = _project_root / 'config'


@lru_cache(maxsize=1)
def _get_agent_default_llm() -> str | None:
    """Cached read of ``agents.general.llm`` from ``config/config.yaml``."""
    return load_agents_general_llm(_MATMASTER_CONFIG_DIR / 'config.yaml')


_INVALID_FINISH_MESSAGES: dict[str, str] = {
    'output_length_exceeded': (
        '模型输出被 provider 的输出 token 上限截断，'
        '未形成可提交的最终回答，请稍后重试。'
    ),
    'content_filtered': '模型输出被 provider 内容策略截断或拦截，未形成可提交的最终回答。',
    'reasoning_only': '模型只返回了思考内容，没有生成可见最终回答。请重试。',
    'empty_response': '模型本轮没有返回可见最终回答。请重试。',
    'missing_llm_response': '模型流结束但没有返回可验证的响应对象。请重试。',
    'missing_tool_call_payload': (
        '模型声明要调用工具，但 provider 流式响应未返回有效工具调用参数。'
        '系统已重试仍未恢复，请重试或切换模型。'
    ),
}
_INVALID_FINISH_DEFAULT = '模型没有返回有效最终回答。请重试。'


def _invalid_finish_error_message(finish_detail: Any) -> str:
    kind = getattr(finish_detail, 'kind', None)
    if kind is None and isinstance(finish_detail, dict):
        kind = finish_detail.get('kind')
    return _INVALID_FINISH_MESSAGES.get(kind, _INVALID_FINISH_DEFAULT)


async def _emit_error_and_close_fanout(
    fanout: RunEventFanout, message: str, source: str = 'System'
) -> None:
    """Dispatch ErrorEvent + StreamClosedEvent(treat_as_failure) pair via fanout."""
    await fanout.dispatch(ErrorEvent(source=source, message=message))
    await fanout.dispatch(
        StreamClosedEvent(
            source=source,
            end_reason='error',
            task_completed=False,
            treat_as_failure=True,
        )
    )


class AgentRunService:
    """Agent execution service: pipeline orchestration via matmaster components."""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._pg_manager = PlaygroundManager(_project_root)
        # Hot cache: session_id -> frozenset of skill names already activated.
        # The authoritative source is DB skill_hit events; this dict only
        # avoids re-scanning the DB on every turn. Populated lazily on cache miss.
        self._active_skills: dict[str, frozenset[str]] = {}

    def init_playground_sync(self) -> None:
        """Validate configs at startup -- delegates to PlaygroundManager."""
        self._pg_manager.validate_startup()

    def _build_skill_resolver(
        self,
        exp_config: Any,
        session: Any | None,
    ) -> SkillResolver:
        skills_cfg = getattr(exp_config, "skills", None)
        if skills_cfg is None or not getattr(skills_cfg, "enabled", False):
            return empty_skill_resolver

        roots_raw = getattr(skills_cfg, "skills_root", None)
        if isinstance(roots_raw, (list, tuple)):
            config_roots: tuple[str | Path, ...] = tuple(
                root for root in roots_raw if isinstance(root, (str, Path)) and root
            )
        elif isinstance(roots_raw, (str, Path)) and roots_raw:
            config_roots = (roots_raw,)
        else:
            config_roots = ()

        disabled_raw = getattr(skills_cfg, "disabled_skill_names", ())
        config_disabled = (
            tuple(disabled_raw)
            if isinstance(disabled_raw, (list, tuple, set, frozenset))
            else ()
        )
        try:
            registry = build_skill_registry(
                config_roots=config_roots,
                session=session,
                config_disabled=config_disabled,
            )
        except Exception:
            logger.warning(
                "active skill resolver: building SkillRegistry failed",
                exc_info=True,
            )
            registry = None
        return SkillRegistryResolver(registry)

    def _resolve_active_skill_names(
        self,
        session_id: str,
        events_table: Any,
        *,
        until_event_id: int | None = None,
    ) -> frozenset[str]:
        cached = self._active_skills.get(session_id)
        if cached is not None:
            return cached

        raw_events: list[dict] = []
        if events_table is not None:
            try:
                raw_events = events_table.get_session_events(
                    session_id,
                    limit=_DIALOG_HISTORY_MAX_EVENTS,
                )
            except Exception:
                logger.warning(
                    "active skill rehydrate: get_session_events failed for session_id=%s",
                    session_id,
                    exc_info=True,
                )

        events = decode_session_events(raw_events)
        if until_event_id is not None:
            events = tuple(event for event in events if event.id <= until_event_id)
        names = frozenset(
            record.skill_name for record in scan_skill_hits(events) if record.skill_name
        )
        self._active_skills[session_id] = names
        return names

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
        images: list[str] | None = None,
        turn_input: TurnInput | None = None,
        bohrium_required: bool = False,
        remote_workdir: str | None = None,
    ) -> tuple[bool | tuple[bool, str], int]:
        """Execute agent pipeline using generator event stream with fanout dispatch.

        Events flow through RunEventFanout directly to handlers:
        kernel._run_items() -> kernel.run_stream() -> exp.run_stream()
        -> source normalization -> fanout.dispatch()

        SSE handler is awaited first (low latency), persistence runs as
        background tasks, WorkspaceHandler receives events inline.

        Returns ``(run_result, elapsed_ms)`` where ``run_result`` is ``True``
        on success or ``(False, reason)`` on failure/cancel.
        """

        def _elapsed_ms() -> int:
            return int((time.monotonic() - run_started_at) * 1000)

        prompt_preview = (
            (user_prompt[:80] + '...') if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            'run_agent start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s',
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
            task_id = task_id or ('ws_' + uuid.uuid4().hex[:16])
            playground = self._pg_manager.get_or_create(session_id)
            run_dir = str(_project_root / 'runs' / RUN_ID_WEB)
            pg_ctx = playground.prepare(
                RunMetadata(run_dir=run_dir, task_id=task_id),
                session_id=session_id,
            )
            if turn_input is not None:
                pg_ctx = pg_ctx.with_metadata(
                    turn_input=turn_input,
                )
            try:
                events_table = get_chat_events_table()
            except Exception:
                logger.exception(
                    'run_agent pre-handler setup failed: session_id=%s',
                    session_id,
                )
                return ((False, 'pre_router_setup_failed'), 0)

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

            exp_name = mode or 'direct'
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
                pg_ctx=pg_ctx,
                run_started_at=run_started_at,
                bohrium_required=bohrium_required,
                remote_workdir=remote_workdir,
            )
            bohrium_svc = stage_result.bohrium_svc
            if stage_result.abort_result is not None:
                return stage_result.abort_result
            pg_ctx = stage_result.pg_ctx
            ssh_attached = stage_result.ssh_attached
            user_instructions = stage_result.user_instructions

            # -- Stage 4: Exp assembly --
            from matmaster.config.loader import load_llm_config
            from matmaster.core.exp import Exp
            from matmaster.providers.llm_factory import build_provider

            llm_config = load_llm_config(_project_root / 'config' / 'llm_config.yaml')

            agent_default_llm = _get_agent_default_llm()
            resolved_llm = llm_config.resolve_route(
                model_override=model_override,
                llm_override=llm_override,
                default_key=agent_default_llm,
            )
            selected_profile = llm_config.get_profile(resolved_llm.profile_key)
            top_level_images = tuple(images or ())
            turn_input_images = turn_input.images if turn_input is not None else ()
            current_images = turn_input_images or top_level_images
            if (
                turn_input_images
                and top_level_images
                and turn_input_images != top_level_images
            ):
                logger.warning(
                    "run_agent image inputs differ; using TurnInput images "
                    "session_id=%s task_id=%s",
                    session_id,
                    task_id,
                )
            image_detail = None
            if current_images:
                selected_profile = get_image_input_service().ensure_vision_supported(
                    llm_config=llm_config,
                    llm_override=llm_override,
                    model_override=model_override,
                    default_profile_key=agent_default_llm,
                )
                image_detail = selected_profile.vision_detail

            pg_ctx = pg_ctx.model_copy(
                update={
                    'llm_provider': build_provider(
                        llm_config,
                        model_override=model_override,
                        llm_override=llm_override,
                        default_profile_key=agent_default_llm,
                    ),
                    'llm_config': llm_config,
                }
            )

            exp = Exp(exp_config)

            if pg_ctx.session is not None:
                pg_ctx.session._cancel_token = cancel_token

            checkpoint_service = (
                HistoryCheckpointService(events_table)
                if events_table is not None
                else None
            )
            figure_accumulator = ResponseFiguresAccumulator()
            figure_dispatch_lock = asyncio.Lock()
            figure_upload_config = _build_figure_upload_config(
                session_id=session_id,
                task_id=task_id,
            )

            async def _dispatch_response_figures_if_dirty_unlocked(
                reason: str,
            ) -> None:
                response_figures_event = (
                    figure_accumulator.build_snapshot_event_if_dirty()
                )
                if response_figures_event is None:
                    return
                try:
                    await fanout.flush_persistence_barrier()
                    dispatched = await fanout.dispatch_and_wait_persistence(
                        response_figures_event
                    )
                except Exception:
                    logger.warning(
                        'response_figures dispatch failed reason=%s',
                        reason,
                        exc_info=True,
                    )
                    return

                if dispatched:
                    figure_accumulator.mark_snapshot_emitted()
                else:
                    logger.warning(
                        'response_figures dispatch reported handler failure '
                        'reason=%s',
                        reason,
                    )

            async def _dispatch_response_figures_if_dirty(reason: str) -> None:
                async with figure_dispatch_lock:
                    await _dispatch_response_figures_if_dirty_unlocked(reason)

            async def _record_tool_result_figures_and_dispatch_if_dirty(
                event: ToolResultEvent,
                *,
                include_spawned: bool,
                reason: str,
            ) -> None:
                async with figure_dispatch_lock:
                    figure_accumulator.add_tool_result(
                        event,
                        include_spawned=include_spawned,
                    )
                    await _dispatch_response_figures_if_dirty_unlocked(reason)

            async def _child_event_sink(event: BusEvent) -> None:
                try:
                    await fanout.dispatch(event)
                    if isinstance(event, ToolResultEvent):
                        await _record_tool_result_figures_and_dispatch_if_dirty(
                            event,
                            include_spawned=True,
                            reason='child_tool_result',
                        )
                except Exception:
                    logger.warning(
                        'child event sink failed for event type=%s',
                        getattr(event, 'type', '?'),
                        exc_info=True,
                    )

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

            pg_ctx = pg_ctx.with_runtime_port(
                figure_upload=FigureUploadPort(config=figure_upload_config),
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
            pg_ctx = pg_ctx.model_copy(update={'interaction_bridge': bridge})
            # -- Stage 5: History --
            wiring = build_history_wiring(
                base_runtime_ports=pg_ctx.runtime_ports,
                events_table=events_table,
                session_id=session_id,
                task_id=task_id,
                raw_history_limit=_DIALOG_HISTORY_MAX_EVENTS,
                child_event_sink=_child_event_sink,
                checkpoint_sink_factory=_checkpoint_sink_factory,
                pre_compaction_barrier=fanout.flush_persistence_barrier,
            )
            history = wiring.history
            pg_ctx = pg_ctx.with_runtime_ports(wiring.runtime_ports)
            if wiring.bohrium_rebuild_events:
                pg_ctx = pg_ctx.with_metadata(
                    bohrium_rebuild_events=tuple(wiring.bohrium_rebuild_events),
                )

            # -- Stage 5b: Phase 2C user_turn_context cutover via ContextAssembler --
            instructions_bundle = UserInstructions(
                text=user_instructions.text,
                hash=user_instructions.hash,
                truncated=user_instructions.truncated,
            )
            pg_ctx = pg_ctx.with_metadata(user_instructions=instructions_bundle)

            skill_resolver = self._build_skill_resolver(
                exp_config,
                session=pg_ctx.session,
            )
            context_assembler, assembly_ports = build_context_assembler(
                events_table=events_table,
                skill_resolver=skill_resolver,
            )
            session_events_port = assembly_ports.session_events

            try:
                intent = await resolve_turn_context_intent(
                    instructions_hash=instructions_bundle.hash,
                    session_id=session_id,
                    spawn_id=None,
                    events_port=session_events_port,
                )
            except Exception:
                logger.warning(
                    "resolve_turn_context_intent failed; treating current turn as anchor",
                    exc_info=True,
                )
                intent = ContextAssemblyIntent.ANCHOR_TURN

            pre_turn_history_event_id = (
                turn_input.pre_turn_history_event_id if turn_input is not None else 0
            )
            if turn_input is None:
                turn_input = TurnInput.from_values(
                    user_text=user_prompt,
                    files=(),
                    images=current_images,
                    image_detail=image_detail if current_images else None,
                    workspace_paths=(),
                    pre_turn_history_event_id=pre_turn_history_event_id,
                )
            elif current_images:
                turn_input = TurnInput.from_values(
                    user_text=turn_input.user_text,
                    files=turn_input.files,
                    images=current_images,
                    image_detail=(
                        image_detail
                        if image_detail is not None
                        else turn_input.attachments.image_detail
                    ),
                    workspace_paths=turn_input.workspace_paths,
                    pre_turn_history_event_id=turn_input.pre_turn_history_event_id,
                )
            pg_ctx = pg_ctx.with_metadata(turn_input=turn_input)

            assembly = await context_assembler.assemble_turn(
                intent=intent,
                request=TurnAssemblyRequest(
                    session_id=session_id,
                    spawn_id=None,
                    turn_input=turn_input,
                    user_instructions=instructions_bundle,
                ),
            )
            rendered_message = assembly.user_turn_context.to_message(
                ContextView.RUNTIME
            )

            user_turn_payload = {
                "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
                "kind": "anchor" if intent.is_anchor_turn else "continuation",
                "message": rendered_message.model_dump(mode="json"),
                "user_instructions_hash": (
                    instructions_bundle.hash if intent.is_anchor_turn else None
                ),
                "transform": DEFAULT_TURN_TRANSFORM,
                "render_version": USER_CONTEXT_RENDER_VERSION,
            }
            try:
                await write_user_turn_context_event(
                    events_table=events_table,
                    session_id=session_id,
                    task_id=task_id,
                    invocation_id=invocation_id,
                    spawn_id=None,
                    payload=user_turn_payload,
                )
            except Exception as exc:
                logger.exception(
                    "user_turn_context write failed; aborting turn "
                    "session_id=%s invocation_id=%s",
                    session_id,
                    invocation_id,
                )
                return ((False, str(exc)), _elapsed_ms())

            user_prompt = rendered_message.content

            # Resolve active skills (hot cache + DB rehydrate). Must run
            # AFTER history is available so the snapshot frozen below reflects
            # any skills recovered from past turns.
            active_skills = self._resolve_active_skill_names(
                session_id,
                events_table,
            )

            def _remember_skill_hit(skill_name: str) -> None:
                if skill_name:
                    current = self._active_skills.get(session_id, frozenset())
                    self._active_skills[session_id] = frozenset((*current, skill_name))

            pg_ctx = pg_ctx.with_metadata(active_skills=frozenset(active_skills))

            # -- Stage 6: Generator event stream --
            run_result_event = None
            async with aclosing(
                exp.run_stream(
                    pg_ctx,
                    user_prompt,
                    history=history,
                    cancel_token=cancel_token,
                    skill_resolver=skill_resolver,
                )
            ) as stream:
                async for event in stream:
                    # Source normalization (ESIN-06)
                    if hasattr(event, 'source'):
                        normalized = _normalize_public_source(event.source)
                        if event.source != normalized:
                            event = event.model_copy(update={'source': normalized})

                    if isinstance(event, SkillHitEvent):
                        _remember_skill_hit(event.skill_name)

                    if isinstance(event, RunResultEvent) and event.spawn_id is None:
                        await _dispatch_response_figures_if_dirty('final_flush')

                    await fanout.dispatch(event)

                    if isinstance(event, ToolResultEvent):
                        await _record_tool_result_figures_and_dispatch_if_dirty(
                            event,
                            include_spawned=False,
                            reason='tool_result',
                        )

                    # Detect terminal event
                    if isinstance(event, RunResultEvent):
                        run_result_event = event

            # -- Post-processing --
            if run_result_event is None:
                await _emit_error_and_close_fanout(
                    fanout, 'Generator terminated without result'
                )
                return ((False, 'no_result'), _elapsed_ms())

            if run_result_event.reason == 'cancelled':
                await fanout.dispatch(
                    CancelledEvent(source='System', reason='Task cancelled by user.')
                )
                await fanout.dispatch(
                    StreamClosedEvent(
                        source='System',
                        end_reason='cancelled',
                        task_completed=False,
                    )
                )
                return ((False, 'cancelled'), _elapsed_ms())
            else:
                if run_result_event.reason == 'invalid_finish':
                    await fanout.dispatch(
                        ErrorEvent(
                            source='System',
                            message=_invalid_finish_error_message(
                                run_result_event.finish_detail
                            ),
                        )
                    )
                await fanout.dispatch(
                    StreamClosedEvent(
                        source='System',
                        task_completed=run_result_event.reason == 'natural',
                        end_reason=run_result_event.reason,
                        treat_as_failure=run_result_event.status == 'failed' or None,
                    )
                )
                if run_result_event.status == 'completed':
                    user_id = self._sessions_service.get_session_user_id(session_id)
                    if user_id:
                        await use_quota(user_id, model_key=model_override)
                    return (True, _elapsed_ms())
                fail_reason = (
                    run_result_event.reason or run_result_event.status or 'failed'
                )
                return ((False, fail_reason), _elapsed_ms())

        except Exception as exc:
            logger.exception('run_agent error: session_id=%s', session_id)
            if fanout is not None:
                try:
                    await _emit_error_and_close_fanout(fanout, str(exc))
                except Exception:
                    pass
            return ((False, str(exc)), _elapsed_ms())
        finally:
            elapsed = time.monotonic() - run_started_at
            logger.info(
                'run_agent done: session_id=%s elapsed=%.1fs',
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
                    logger.warning('Bohrium cleanup error', exc_info=True)
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
                        'fanout.drain_and_close() failed during cleanup',
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
