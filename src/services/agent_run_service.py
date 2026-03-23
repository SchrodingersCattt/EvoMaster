"""Agent execution service: new matmaster pipeline orchestration.

Rewritten per D-12: run_agent_sync() is a thin orchestration layer using:
  Playground.prepare() -> get_chat_events_table() -> EventRouter bootstrap ->
  Bohrium -> WorkspaceHandler attachment -> Exp.assemble() -> ChatHistory ->
  Kernel.run() -> post-processing

Method signature (12 parameters) unchanged -- zero caller modifications.
"""

import asyncio
import gc
import logging
import os
import threading
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from matmaster.core.bus import MessageBus

from matmaster.hooks import (
    AssistantStateHook,
    ConfirmationHook,
    OutputProcessorHook,
    SkillHitHook,
)
from matmaster.integration import (
    EventRouter,
    PersistenceHandler,
    SSEHandler,
    WorkspaceHandler,
)
from matmaster.integration.bohrium_setup import BohriumSetupService
from matmaster.core.playground import Playground
from matmaster.types.events import CancelledEvent, ErrorEvent
from src.dao.chat_events_table import get_chat_events_table
from src.dao.redis_dao import get_redis_dao
from src.services.chat_history import ChatHistoryConverter
from src.services.quota_service import use_quota
from src.services.sessions_service import get_sessions_service

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Thread pool concurrency: default 2 workers, override with CHAT_AGENT_MAX_WORKERS
_AGENT_MAX_WORKERS = int(os.environ.get('CHAT_AGENT_MAX_WORKERS', '2'))
if _AGENT_MAX_WORKERS < 1:
    _AGENT_MAX_WORKERS = 1

# Multi-turn dialog: max events from DB to avoid context overflow
_DIALOG_HISTORY_MAX_EVENTS = int(
    os.environ.get('CHAT_DIALOG_HISTORY_MAX_EVENTS', '500')
)

_project_root = Path(__file__).resolve().parent.parent.parent
RUN_ID_WEB = 'mat_master_web'


# -- LLM Factory helpers (per D-01 through D-05) --

_MODEL_FAMILY_DEFAULTS: dict[str, dict[str, str]] = {
    'claude-4.6': {
        'reasoning_protocol': 'anthropic_adaptive_thinking',
        'temperature_policy': 'force_one_when_reasoning',
    },
    'gpt-5': {
        'reasoning_protocol': 'openai_reasoning_effort',
        'temperature_policy': 'default',
    },
    'deepseek-reasoner': {
        'reasoning_protocol': 'openai_reasoning_effort',
        'temperature_policy': 'default',
    },
    'gemini-3-flash-preview': {
        'temperature_policy': 'default',
    },
}


def _infer_model_family(model: str) -> str | None:
    """Infer model family from model name string (per D-05, from evomaster/utils/llm.py)."""
    name = (model or '').strip().lower()
    if 'claude-sonnet-4-6' in name or 'claude-opus-4-6' in name:
        return 'claude-4.6'
    if 'claude-haiku-4-5' in name:
        return 'claude-haiku-4.5'
    if 'gpt-5' in name:
        return 'gpt-5'
    if 'deepseek-reasoner' in name:
        return 'deepseek-reasoner'
    if 'gemini-3-flash-preview' in name:
        return 'gemini-3-flash-preview'
    return None


def _build_reasoning_extra_kwargs(
    reasoning_protocol: str | None,
    thinking_effort: str | None,
) -> dict[str, Any]:
    """Build extra_kwargs for OpenAIProvider from reasoning config (per D-04)."""
    if not reasoning_protocol or not thinking_effort:
        return {}
    effort = thinking_effort.strip().lower()
    if not effort:
        return {}
    if reasoning_protocol == 'anthropic_adaptive_thinking':
        return {
            'extra_body': {
                'thinking': {'type': 'adaptive'},
                'output_config': {'effort': effort},
            },
        }
    if reasoning_protocol == 'openai_reasoning_effort':
        return {'reasoning_effort': effort}
    return {}


def _resolve_temperature(
    temperature: float,
    temperature_policy: str | None,
) -> float:
    """Apply temperature policy (per D-04: claude-4.6 forces temperature=1)."""
    if temperature_policy == 'force_one_when_reasoning':
        return 1.0
    return temperature


def _build_workspace_upload_fn(
    archival_config: Any,
) -> Callable[..., Any] | None:
    """Build workspace upload closure when archival is enabled.

    Lazy-imports oss_io to avoid hard oss2 dependency when archival
    is disabled.
    """
    if not archival_config or not archival_config.enabled:
        return None
    oss_prefix = archival_config.oss_prefix

    def _do_upload(session_id: str, task_id: str, workspace_path: Path) -> None:
        from src.dao.oss_io import upload_dir_to_oss

        key_prefix = f"{oss_prefix}/{session_id}/{task_id}"
        upload_dir_to_oss(workspace_path, key_prefix)

    return _do_upload


@runtime_checkable
class ReplyQueueLike(Protocol):
    """Confirmation reply queue abstraction: put content/cancel, blocking get."""

    def put_content(self, content: str) -> None: ...

    def put_cancel(self) -> None: ...

    def get(self, timeout: float | None = None) -> str | None:
        """Blocking get. Returns None for cancel; raises queue.Empty on timeout."""
        ...


class AgentRunService:
    """Agent execution service: pipeline orchestration via matmaster components."""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._executor = ThreadPoolExecutor(max_workers=_AGENT_MAX_WORKERS)
        self._playgrounds: dict[str, Any] = {}
        self._playground_init_done = threading.Event()

    def init_playground_sync(self) -> None:
        """Validate config YAML existence at startup -- no dynamic module import (D-04)."""
        import yaml

        for pg_type in ("mat_master", "minimal"):
            config_path = _project_root / "configs" / pg_type / "config.yaml"
            if not config_path.exists():
                logger.warning("Config not found: %s", config_path)
                continue
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            if not isinstance(cfg, dict) or "agents" not in cfg:
                logger.warning("Config missing 'agents' key: %s", config_path)

        # Deprecation warnings for old modules (per D-02)
        try:
            import evomaster  # noqa: F401

            warnings.warn(
                "evomaster package is deprecated. Use matmaster instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        except ImportError:
            pass

        self._playground_init_done.set()
        logger.info("MatMaster chat: playground config validation complete.")

    def _get_or_create_playground(
        self,
        session_id: str,
        playground_type: str = "mat_master",
    ) -> Playground:
        """Return or create Playground by session_id. Per D-03: x_master raises ValueError."""
        if playground_type == "x_master":
            raise ValueError(
                "x_master playground_type is not supported in the new pipeline"
            )
        if session_id in self._playgrounds:
            return self._playgrounds[session_id]
        config_path = _project_root / "configs" / playground_type / "config.yaml"
        pg = Playground(config_path=config_path)
        self._playgrounds[session_id] = pg
        return pg

    def get_executor(self) -> ThreadPoolExecutor:
        """Return the thread pool for agent execution."""
        return self._executor

    def _build_llm_provider(self, playground, model_override):
        """Build LLMProvider from playground config (per D-01 through D-05).

        Resolution chain:
        1. Get llm config dict from playground config
        2. Resolve profile key from model_override or agent default
        3. Extract parameters from profile
        4. Infer model family, apply reasoning parameters
        5. Apply temperature policy
        6. Instantiate OpenAIProvider with extra_kwargs
        """
        from matmaster.providers.openai_provider import OpenAIProvider

        config = playground.config
        llm_dict = config.llm  # dict of profile_key -> config dict

        # 1. Resolve profile key
        profile_key, llm_cfg = self._resolve_llm_profile(
            llm_dict, config, model_override
        )

        # 2. Extract base parameters
        model = model_override or llm_cfg.get('model', '')
        api_key = llm_cfg.get('api_key', '')
        base_url = llm_cfg.get('base_url')
        temperature = float(llm_cfg.get('temperature', 0.7))
        max_tokens = llm_cfg.get('max_tokens')
        timeout = float(llm_cfg.get('timeout', 300))
        max_retries = int(llm_cfg.get('max_retries', 3))
        retry_delay = float(llm_cfg.get('retry_delay', 1.0))

        # 3. Model family resolution
        family = llm_cfg.get('model_family') or _infer_model_family(model)
        family_defaults = _MODEL_FAMILY_DEFAULTS.get(family or '', {})

        # 4. Reasoning parameters
        reasoning_protocol = (
            llm_cfg.get('reasoning_protocol')
            or family_defaults.get('reasoning_protocol')
        )
        thinking_effort = llm_cfg.get('thinking_effort')
        extra_kwargs = _build_reasoning_extra_kwargs(
            reasoning_protocol, thinking_effort
        )

        # 5. Temperature policy
        temp_policy = (
            llm_cfg.get('temperature_policy')
            or family_defaults.get('temperature_policy')
        )
        temperature = _resolve_temperature(temperature, temp_policy)

        logger.info(
            "_build_llm_provider: profile=%s model=%s family=%s reasoning=%s",
            profile_key, model, family, reasoning_protocol,
        )

        return OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            extra_kwargs=extra_kwargs or None,
        )

    def _resolve_llm_profile(self, llm_dict, config, model_override):
        """Resolve LLM profile key and config dict from model_override.

        Resolution order (per D-02, D-03):
        1. Search config entries for matching model name
        2. Search for matching profile key
        3. Fall back to agents.general.llm default key, override model
        """
        if not isinstance(llm_dict, dict):
            raise ValueError("Config missing 'llm' section")

        # Default profile key from agents.general.llm
        default_key = 'litellm'
        agents = getattr(config, 'agents', None)
        if isinstance(agents, dict):
            general = agents.get('general', {})
            if isinstance(general, dict):
                default_key = general.get('llm', default_key)

        if not model_override:
            # No override: use default profile
            cfg = llm_dict.get(default_key)
            if cfg is None:
                raise ValueError(
                    f"Default LLM profile '{default_key}' not found in config"
                )
            return default_key, cfg

        # 1. Search by model name match
        for key, cfg in llm_dict.items():
            if key == 'default' or not isinstance(cfg, dict):
                continue
            if cfg.get('model') == model_override:
                return key, cfg

        # 2. Search by profile key match
        if model_override in llm_dict and isinstance(
            llm_dict[model_override], dict
        ):
            return model_override, llm_dict[model_override]

        # 3. Fallback: use default profile but override the model
        logger.warning(
            "model_override '%s' not found in config, using default profile '%s' with overridden model",
            model_override,
            default_key,
        )
        cfg = llm_dict.get(default_key)
        if cfg is None:
            raise ValueError(
                f"Default LLM profile '{default_key}' not found in config"
            )
        return default_key, cfg

    def run_agent_sync(
        self,
        session_id: str,
        user_prompt: str,
        send_cb: Callable[[dict], Any],
        loop: Optional[asyncio.AbstractEventLoop],
        stop_event: Any,
        mode: str,
        reply_queue: ReplyQueueLike | None,
        task_id: str,
        invocation_id: str | None = None,
        llm_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        """Execute agent in background thread using new matmaster pipeline.

        Pipeline: Playground.prepare() -> get_chat_events_table() ->
        EventRouter bootstrap -> Bohrium -> WorkspaceHandler attachment ->
        Exp.assemble() -> ChatHistory -> Kernel.run() -> post-processing.

        Method signature unchanged per D-12: all 12 parameters preserved.
        """
        prompt_preview = (
            (user_prompt[:80] + "...") if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            "run_agent_sync start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s",
            session_id,
            task_id,
            mode,
            len(user_prompt),
            prompt_preview,
        )
        run_started_at = time.monotonic()
        bus = MessageBus()
        router = None
        exp = None
        bohrium_svc = None
        ssh_attached = False

        try:
            # -- Stage 1: Playground --
            if not self._playground_init_done.is_set():
                self.init_playground_sync()
            task_id = task_id or ("ws_" + uuid.uuid4().hex[:16])
            playground = self._get_or_create_playground(session_id)
            run_dir = str(_project_root / "runs" / RUN_ID_WEB)
            pg_ctx = playground.prepare(
                {
                    "run_dir": run_dir,
                    "task_id": task_id,
                }
            )
            events_table = get_chat_events_table()

            # -- Stage 2: EventRouter bootstrap --
            router = EventRouter(
                bus=bus,
                handlers=[
                    PersistenceHandler(
                        events_table,
                        session_id,
                        task_id,
                        invocation_id,
                    ),
                    SSEHandler(
                        send_cb,
                        loop,
                        session_id,
                        task_id,
                        invocation_id,
                        mode,
                    ),
                ],
            )
            router.start()

            # -- Stage 3: Bohrium credentials + SSH --
            bohrium_svc = BohriumSetupService(self._sessions_service, bus)
            run_creds, user_id_for_ak, org_id = bohrium_svc.load_credentials(
                session_id
            )

            # Build a lightweight event_callback bridge for bohrium (legacy API)
            def _bohrium_event_cb(source, event_type, content, **extra):
                """Bridge bohrium events into the MessageBus."""
                try:
                    from matmaster.types.events import BohriumNodeEvent

                    bus.emit(
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
                    logger.debug(
                        "bohrium event bridge error type=%s", event_type
                    )

            bohrium_result = bohrium_svc.setup(
                session_id=session_id,
                pg=playground,
                base=getattr(playground, "agent", playground),
                run_creds=run_creds,
                user_id_for_ak=user_id_for_ak,
                org_id=org_id,
                event_callback=_bohrium_event_cb,
                run_started_at=run_started_at,
            )
            ssh_attached = bohrium_result.ssh_attached
            if bohrium_result.abort_result is not None:
                return
            pg_ctx = pg_ctx.with_bohrium(bohrium_result._asdict())
            # Workspace handling depends on the finalized Bohrium/archival context.
            router.add_handler(
                WorkspaceHandler(
                    session_id=session_id,
                    task_id=task_id,
                    ssh_attached=ssh_attached,
                    archival_config=pg_ctx.archival,
                    workspace_path=pg_ctx.workdir,
                    upload_fn=_build_workspace_upload_fn(pg_ctx.archival),
                )
            )

            # -- Stage 4: Exp assembly --
            from matmaster.core.exp import Exp

            exp_config = {
                "name": "direct",
                "tools": {"builtin": ["*"]},
                "guards": [],
                "termination": {"max_turns": 100},
                "prompt": {},
                "context": {},
                "skills": pg_ctx.run_meta.get("skill_config", {}),
                "mcp": pg_ctx.run_meta.get("mcp_config", {}),
            }

            pg_ctx = pg_ctx.model_copy(
                update={
                    "llm_provider": self._build_llm_provider(
                        playground, model_override
                    )
                }
            )

            exp = Exp(exp_config)
            runtime = exp.build_runtime(pg_ctx, bus=bus)

            # Add external hooks to spec
            external_hooks = [
                ConfirmationHook(reply_queue, bus),
                OutputProcessorHook(bus),
                SkillHitHook(bus),
                AssistantStateHook(bus),
            ]
            spec = runtime.spec.model_copy(
                update={"hooks": [*runtime.spec.hooks, *external_hooks]}
            )

            # -- Stage 5: History --
            raw_events = (
                events_table.get_session_events(
                    session_id, limit=_DIALOG_HISTORY_MAX_EVENTS
                )
                if events_table
                else []
            )
            history = ChatHistoryConverter.events_to_messages(raw_events)

            # -- Stage 6: Kernel execution --
            finish_event = runtime.kernel.run(
                spec=spec,
                task=user_prompt,
                history=history,
                stop_event=stop_event,
            )

            # -- Post-processing --
            if finish_event.reason == "cancelled":
                bus.emit(
                    CancelledEvent(
                        source="System", reason="Task cancelled by user."
                    )
                )
            else:
                bus.emit(finish_event)
                # Quota deduction (per QUAL-05: success only)
                if finish_event.status == "completed":
                    user_id = self._sessions_service.get_session_user_id(
                        session_id
                    )
                    if user_id:
                        if loop is not None:
                            future = asyncio.run_coroutine_threadsafe(
                                use_quota(user_id), loop
                            )
                            future.result(timeout=10)
                        else:
                            asyncio.run(use_quota(user_id))

        except Exception as exc:
            logger.exception(
                "run_agent_sync error: session_id=%s", session_id
            )
            try:
                bus.emit(ErrorEvent(source="System", message=str(exc)))
            except Exception:
                pass
        finally:
            elapsed = time.monotonic() - run_started_at
            logger.info(
                "run_agent_sync done: session_id=%s elapsed=%.1fs",
                session_id,
                elapsed,
            )
            if router:
                router.stop()
            if exp:
                try:
                    exp._run_cleanup_callbacks()
                except Exception:
                    logger.warning("Exp cleanup error", exc_info=True)
            if bohrium_svc:
                try:
                    bohrium_svc.cleanup(
                        session_id=session_id,
                        event_callback=_bohrium_event_cb,
                        pg_for_run=playground if "playground" in dir() else None,
                        ssh_attached=ssh_attached,
                    )
                except Exception:
                    logger.warning("Bohrium cleanup error", exc_info=True)
            get_redis_dao().delete_stop_requested(session_id, task_id)
            pg = self._playgrounds.pop(session_id, None)
            if pg:
                pg.cleanup()
            gc.collect()


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """Initialize playground at startup (called in lifespan)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
