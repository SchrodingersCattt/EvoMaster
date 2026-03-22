"""BohriumSetupService -- thin wrapper around agent_run_bohrium functions.

Wraps the 4 top-level functions in src/services/agent_run_bohrium.py
into a two-phase setup/cleanup API for use by the new service pipeline.

This is a thin delegation layer -- all logic remains in agent_run_bohrium.py.
The wrapper provides:
1. A class-based API instead of module-level functions
2. Optional MessageBus for progress event emission
3. Clean constructor injection of sessions_service
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from typing import Callable

    from matmaster.core.bus import MessageBus

logger = logging.getLogger(__name__)


class BohriumSetupService:
    """Thin wrapper around agent_run_bohrium.py functions.

    Provides setup/cleanup two-phase API for Bohrium node lifecycle.
    Delegates all logic to existing module-level functions.
    """

    def __init__(
        self,
        sessions_service: Any,
        bus: MessageBus | None = None,
    ) -> None:
        self._sessions_service = sessions_service
        self._bus = bus

    def load_credentials(self, session_id: str) -> tuple[dict[str, Any], str | None, str]:
        """Load run credentials from session store.

        Delegates to agent_run_bohrium.load_run_credentials().
        """
        from src.services.agent_run_bohrium import load_run_credentials

        return load_run_credentials(self._sessions_service, session_id)

    def apply_credentials(self, session: Any, run_creds: dict[str, Any]) -> None:
        """Attach transient Bohrium credentials to active session.

        Delegates to agent_run_bohrium.apply_run_credentials_to_session().
        """
        from src.services.agent_run_bohrium import apply_run_credentials_to_session

        apply_run_credentials_to_session(session, run_creds)

    def setup(
        self,
        *,
        session_id: str,
        pg: Any,
        base: Any,
        run_creds: dict[str, Any],
        user_id_for_ak: str | None,
        org_id: str,
        event_callback: Callable[..., None],
        run_started_at: float,
    ) -> NamedTuple:
        """Prepare Bohrium node and SSH session for the run.

        Delegates to agent_run_bohrium.setup_bohrium_for_run().
        Returns BohriumSetupResult(ssh_attached, abort_result).
        """
        from src.services.agent_run_bohrium import setup_bohrium_for_run

        return setup_bohrium_for_run(
            session_id=session_id,
            pg=pg,
            base=base,
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

        Delegates to agent_run_bohrium.cleanup_bohrium_after_run().
        """
        from src.services.agent_run_bohrium import cleanup_bohrium_after_run

        cleanup_bohrium_after_run(
            session_id=session_id,
            sessions_service=self._sessions_service,
            event_callback=event_callback,
            pg_for_run=pg_for_run,
            ssh_attached=ssh_attached,
        )
