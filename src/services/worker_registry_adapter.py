"""WorkerRegistryServiceAdapter -- bridges WorkerRegistryService to matmaster Protocol.

WorkerRegistryService (src/services/) returns None from delete_session_run_owner,
but the WorkerRegistry Protocol (matmaster/assembly/) requires bool. This adapter
bridges the difference.

Per D-16: existing worker_registry_service.py adapted via dependency injection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.worker_registry_service import WorkerRegistryService

logger = logging.getLogger(__name__)


class WorkerRegistryServiceAdapter:
    """Adapts WorkerRegistryService to matmaster WorkerRegistry Protocol.

    Key difference bridged:
    - Protocol: delete_session_run_owner(session_id) -> bool
    - Service: delete_session_run_owner(session_id) -> None
    """

    def __init__(self, service: "WorkerRegistryService") -> None:
        self._service = service

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        return self._service.set_session_run_owner(session_id, worker_id)

    def refresh_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        return self._service.refresh_session_run_owner(session_id, worker_id)

    def delete_session_run_owner(self, session_id: str) -> bool:
        self._service.delete_session_run_owner(session_id)
        return True

    def get_session_run_owner(self, session_id: str) -> str | None:
        return self._service.get_session_run_owner(session_id)
