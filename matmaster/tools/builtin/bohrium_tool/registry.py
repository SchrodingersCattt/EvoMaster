"""Bohrium job registry with exponential backoff throttle.

Tracks submitted jobs in-memory and enforces a polling schedule to prevent
API spam. First poll after submit is always allowed; subsequent polls are
gated by ``next_interval(poll_count - 1)`` seconds.

Thread safety: registry MUST only be accessed from the asyncio event loop
thread (same contract as ToolRunnerState).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def next_interval(poll_count: int) -> int:
    """Seconds until next fresh poll is allowed after ``poll_count`` polls."""
    return min(3600, 30 * 2**poll_count)


@dataclass
class JobRecord:
    """Single Bohrium job tracked by the registry."""

    job_id: str
    status: str = "submitted"
    job_name: str = ""
    submitted_at: float = field(default_factory=time.monotonic)
    last_polled_at: float = 0.0
    poll_count: int = 0
    last_result: str = ""


class JobRegistry:
    """In-memory job state tracker with exponential backoff throttle."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    def register(self, job_id: str, *, job_name: str = "") -> None:
        """Register a newly submitted job."""
        self._jobs[str(job_id)] = JobRecord(
            job_id=str(job_id),
            job_name=job_name,
        )

    def update_poll(self, job_id: str, *, status: str, result: str) -> None:
        """Update job after a fresh non-cached poll."""
        key = str(job_id)
        rec = self._jobs.get(key)
        if rec is None:
            rec = JobRecord(job_id=key)
            self._jobs[key] = rec
        rec.status = status
        rec.last_polled_at = time.monotonic()
        rec.poll_count += 1
        rec.last_result = result

    def update_download(self, job_id: str) -> None:
        """Mark job as downloaded."""
        rec = self._jobs.get(str(job_id))
        if rec is not None:
            rec.status = "downloaded"

    def get(self, job_id: str) -> JobRecord | None:
        """Get job record or None."""
        return self._jobs.get(str(job_id))

    def should_throttle(self, job_id: str) -> tuple[bool, int]:
        """Check if a poll should be throttled."""
        rec = self._jobs.get(str(job_id))
        if rec is None or rec.status not in ("submitted", "running"):
            return False, 0
        if rec.poll_count == 0:
            return False, 0
        interval = next_interval(rec.poll_count - 1)
        elapsed = time.monotonic() - rec.last_polled_at
        if elapsed < interval:
            return True, int(interval - elapsed)
        return False, 0

    def pending_jobs(self) -> list[JobRecord]:
        """Return jobs in submitted or running state."""
        return [
            rec
            for rec in self._jobs.values()
            if rec.status in ("submitted", "running")
        ]

    def all_jobs(self) -> list[JobRecord]:
        """Return all tracked jobs."""
        return list(self._jobs.values())
