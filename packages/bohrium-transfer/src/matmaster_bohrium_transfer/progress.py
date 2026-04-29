from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferProgressEvent:
    event_type: str
    transfer_id: str
    phase: str
    direction: str
    bytes_done: int
    bytes_total: int | None
    parts_done: int | None = None
    parts_total: int | None = None
    rate_mbps: float | None = None
    resume_supported: bool | None = None
    location: str | None = None
    package_version: str | None = None
    protocol_version: str | None = None


class ProgressSink:
    def emit(self, event: TransferProgressEvent) -> None:
        raise NotImplementedError


class NoopProgressSink(ProgressSink):
    def emit(self, event: TransferProgressEvent) -> None:
        del event


class LoggingProgressSink(ProgressSink):
    def __init__(
        self,
        *,
        min_bytes: int = 32 * 1024 * 1024,
        min_seconds: float = 1.0,
    ) -> None:
        self.min_bytes = min_bytes
        self.min_seconds = min_seconds
        self._last_bytes: dict[str, int] = {}
        self._last_time: dict[str, float] = {}

    def emit(self, event: TransferProgressEvent) -> None:
        now = time.monotonic()
        last_bytes = self._last_bytes.get(event.transfer_id, 0)
        last_time = self._last_time.get(event.transfer_id, 0.0)
        byte_delta = event.bytes_done - last_bytes
        time_delta = now - last_time
        if event.event_type.endswith("_chunk_completed"):
            if byte_delta < self.min_bytes and time_delta < self.min_seconds:
                return
        self._last_bytes[event.transfer_id] = event.bytes_done
        self._last_time[event.transfer_id] = now
        logger.info(
            "transfer_progress type=%s id=%s bytes=%s/%s",
            event.event_type,
            event.transfer_id,
            event.bytes_done,
            event.bytes_total,
        )
