"""BohriumSetupService -- callback-injected wrapper for Bohrium lifecycle.

Provides a two-phase setup/cleanup API for Bohrium node lifecycle.
All external logic is injected via 4 callables at construction time,
eliminating the reverse dependency on src.services.agent_run_bohrium.

The wrapper provides:
1. A class-based API instead of module-level functions
2. Optional MessageBus for progress event emission
3. Dependency-inverted constructor (4 callables instead of sessions_service)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matmaster.integration.bohrium_env import BohriumSetupResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from matmaster.config.exp import ExpConfig
    from matmaster.core.bus import MessageBus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillSyncSpec:
    """Resolved paths for project skill sync to remote Bohrium node."""

    project_skill_roots: list[str]
    remote_project_root: str


def derive_skill_sync_spec(
    exp_config: ExpConfig,
    *,
    project_root: Path,
) -> SkillSyncSpec | None:
    """Resolve ExpConfig.skills into a SkillSyncSpec for Bohrium upload."""
    skills = exp_config.skills
    if not skills.enabled:
        return None

    raw_value = skills.skills_root
    if isinstance(raw_value, list):
        relative_paths = [
            entry.strip() for entry in raw_value if entry and entry.strip()
        ]
    else:
        stripped = (raw_value or "").strip()
        relative_paths = [stripped] if stripped else []
    if not relative_paths:
        return None

    resolved_roots: list[str] = []
    for rel_path in relative_paths:
        path = Path(rel_path)
        resolved = (
            path.resolve()
            if path.is_absolute()
            else (project_root / rel_path).resolve()
        )
        if resolved.is_dir():
            resolved_roots.append(str(resolved))
    if not resolved_roots:
        return None

    return SkillSyncSpec(
        project_skill_roots=resolved_roots,
        remote_project_root="/share/.matmaster",
    )


class BohriumSetupService:
    """Callback-injected wrapper for Bohrium node lifecycle.

    Provides setup/cleanup two-phase API for Bohrium node lifecycle.
    All external logic is injected via 4 callables -- no direct import
    from the upstream agent_run_bohrium module.
    """

    def __init__(
        self,
        *,
        load_credentials_fn: Callable[[str], tuple[dict[str, Any], str | None, str]],
        apply_credentials_fn: Callable[[Any, dict[str, Any]], None],
        setup_fn: Callable[..., BohriumSetupResult],
        cleanup_fn: Callable[..., None],
        bus: MessageBus | None = None,
    ) -> None:
        self._load_credentials = load_credentials_fn
        self._apply_credentials = apply_credentials_fn
        self._setup = setup_fn
        self._cleanup = cleanup_fn
        self._bus = bus

    def load_credentials(
        self, session_id: str
    ) -> tuple[dict[str, Any], str | None, str]:
        """Load run credentials from session store.

        Delegates to the injected load_credentials_fn callable.
        """
        return self._load_credentials(session_id)

    def apply_credentials(self, session: Any, run_creds: dict[str, Any]) -> None:
        """Attach transient Bohrium credentials to active session.

        Delegates to the injected apply_credentials_fn callable.
        """
        self._apply_credentials(session, run_creds)

    def setup(
        self,
        *,
        session_id: str,
        pg: Any,
        skill_sync_spec: SkillSyncSpec | None,
        run_creds: dict[str, Any],
        user_id_for_ak: str | None,
        org_id: str,
        event_callback: Callable[..., None],
        run_started_at: float,
    ) -> BohriumSetupResult:
        """Prepare Bohrium node and SSH session for the run.

        Delegates to the injected setup_fn callable.
        Returns BohriumSetupResult including execution binding fields.
        """
        return self._setup(
            session_id=session_id,
            pg=pg,
            skill_sync_spec=skill_sync_spec,
            run_creds=run_creds,
            user_id_for_ak=user_id_for_ak,
            org_id=org_id,
            event_callback=event_callback,
            run_started_at=run_started_at,
        )

    def cleanup(
        self,
        *,
        session_id: str,
        event_callback: Callable[..., None],
        pg_for_run: Any,
        ssh_attached: bool,
    ) -> None:
        """Restore session state and cleanup Bohrium node.

        Delegates to the injected cleanup_fn callable.
        """
        self._cleanup(
            session_id=session_id,
            event_callback=event_callback,
            pg_for_run=pg_for_run,
            ssh_attached=ssh_attached,
        )

    # ── High-level async entry point ─────────────────────

    def _make_event_bridge(
        self,
        loop: asyncio.AbstractEventLoop,
    ) -> Callable[..., None]:
        """Create a thread-safe callback that bridges bohrium events into the bus.

        Runs from executor thread, uses call_soon_threadsafe to emit safely.
        Requires self._bus to be set.
        """
        from matmaster.types.events import (
            BohriumNodeEvent,
            ErrorEvent,
            StreamClosedEvent,
        )

        bus = self._bus
        assert bus is not None  # noqa: S101 -- caller guarantees

        def _cb(source: Any, event_type: str, content: Any, **extra: Any) -> None:
            def _do_emit() -> None:
                try:
                    if event_type == 'error':
                        msg = content if isinstance(content, str) else str(content)
                        bus.emit_nowait(ErrorEvent(source=str(source), message=msg))
                        bus.emit_nowait(
                            StreamClosedEvent(
                                source=str(source),
                                end_reason='error',
                                task_completed=False,
                                treat_as_failure=True,
                            )
                        )
                        return
                    if event_type == 'stream_closed':
                        body = '' if content is None else str(content)
                        bus.emit_nowait(
                            StreamClosedEvent(
                                source=str(source),
                                content=body,
                                task_completed=False,
                                end_reason='error',
                                treat_as_failure=True,
                            )
                        )
                        return
                    bus.emit_nowait(
                        BohriumNodeEvent(
                            source=str(source),
                            payload={
                                'type': event_type,
                                'content': content,
                                **extra,
                            },
                        )
                    )
                except Exception:
                    logger.debug('bohrium event bridge error type=%s', event_type)

            loop.call_soon_threadsafe(_do_emit)

        return _cb

    async def run_setup(
        self,
        *,
        session_id: str,
        playground: Any,
        skill_sync_spec: SkillSyncSpec | None,
        run_started_at: float,
    ) -> BohriumSetupResult:
        """High-level async entry: credentials + event bridge + setup in executor.

        Encapsulates the full bohrium setup orchestration that was previously
        scattered in agent_run_service.
        """
        run_creds, user_id_for_ak, org_id = self.load_credentials(session_id)

        loop = asyncio.get_running_loop()
        event_cb = self._make_event_bridge(loop)

        return await loop.run_in_executor(
            None,
            lambda: self.setup(
                session_id=session_id,
                pg=playground,
                skill_sync_spec=skill_sync_spec,
                run_creds=run_creds,
                user_id_for_ak=user_id_for_ak,
                org_id=org_id,
                event_callback=event_cb,
                run_started_at=run_started_at,
            ),
        )

    async def run_cleanup(
        self,
        *,
        session_id: str,
        pg_for_run: Any,
        ssh_attached: bool,
    ) -> None:
        """High-level async cleanup: event bridge + cleanup in executor."""
        loop = asyncio.get_running_loop()
        event_cb = self._make_event_bridge(loop)

        await loop.run_in_executor(
            None,
            lambda: self.cleanup(
                session_id=session_id,
                event_callback=event_cb,
                pg_for_run=pg_for_run,
                ssh_attached=ssh_attached,
            ),
        )
