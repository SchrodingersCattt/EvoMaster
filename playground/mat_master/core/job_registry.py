"""Tracked async jobs for runtime-level finish gating.

The registry is the source of truth for:
- which async jobs were submitted
- whether they are still pending/running
- whether they reached terminal states
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_RUNNING_STATES = frozenset({"Running", "Pending", "Scheduling", "Wait", "Uploading"})
_SUCCESS_STATES = frozenset({"Finished"})
_FAILURE_STATES = frozenset({"Failed", "Deleted", "Stopped", "Stopping", "Terminating", "Killing"})


@dataclass
class JobRecord:
    """Single async job state tracked by runtime."""

    job_id: str
    software: str
    source_tool: str
    bohr_job_id: str | None = None
    lifecycle_state: str = "submitted"
    raw_status: str | None = None
    unknown_polls: int = 0
    results: dict[str, Any] | None = None
    message: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle_state in {"succeeded", "failed", "unknown_timeout"}


class JobRegistry:
    """In-memory async job registry with refresh and finish checks."""

    def __init__(self, logger, max_unknown_polls: int = 3) -> None:
        self._logger = logger
        self._jobs: dict[str, JobRecord] = {}
        self._max_unknown_polls = max(1, int(max_unknown_polls))

    @property
    def jobs(self) -> dict[str, JobRecord]:
        return dict(self._jobs)

    def record_submit(
        self,
        *,
        job_id: str,
        software: str,
        source_tool: str,
        bohr_job_id: str | None = None,
    ) -> None:
        if not job_id:
            return
        rec = self._jobs.get(job_id)
        if rec is None:
            self._jobs[job_id] = JobRecord(
                job_id=job_id,
                software=software,
                source_tool=source_tool,
                bohr_job_id=bohr_job_id,
            )
            return

        # Update known fields if the same job appears again.
        if bohr_job_id and not rec.bohr_job_id:
            rec.bohr_job_id = bohr_job_id
        if software:
            rec.software = software
        rec.updated_at = _now_iso()

    def pending_jobs(self) -> list[JobRecord]:
        return [j for j in self._jobs.values() if not j.is_terminal]

    def all_terminal(self) -> bool:
        return all(j.is_terminal for j in self._jobs.values())

    def refresh_pending(self) -> None:
        """Refresh non-terminal job statuses via calculation adaptor APIs."""
        pendings = self.pending_jobs()
        if not pendings:
            return
        try:
            from evomaster.adaptors.calculation.job_service import (
                get_job_results,
                query_job_status,
            )
        except Exception as exc:
            self._logger.warning("job_registry: cannot import job service: %s", exc)
            return

        for rec in pendings:
            try:
                status = str(
                    query_job_status(
                        rec.job_id,
                        bohr_job_id=rec.bohr_job_id,
                        software=rec.software,
                    )
                )
            except Exception as exc:
                rec.lifecycle_state = "monitoring"
                rec.message = f"status_query_error: {exc}"
                rec.updated_at = _now_iso()
                continue

            rec.raw_status = status
            rec.updated_at = _now_iso()

            if status in _RUNNING_STATES:
                rec.lifecycle_state = "monitoring"
                rec.unknown_polls = 0
                continue
            if status in _SUCCESS_STATES:
                rec.lifecycle_state = "succeeded"
                rec.unknown_polls = 0
                try:
                    rec.results = get_job_results(
                        rec.job_id,
                        bohr_job_id=rec.bohr_job_id,
                        software=rec.software,
                    )
                except Exception as exc:
                    rec.message = f"get_results_error: {exc}"
                continue
            if status in _FAILURE_STATES or status.startswith("Error:"):
                rec.lifecycle_state = "failed"
                rec.unknown_polls = 0
                rec.message = f"terminal_failure: {status}"
                continue

            # Unknown-like states: retry a few times, then mark terminal unknown timeout.
            rec.unknown_polls += 1
            if rec.unknown_polls >= self._max_unknown_polls:
                rec.lifecycle_state = "unknown_timeout"
                rec.message = f"status remained unknown after {rec.unknown_polls} polls"
            else:
                rec.lifecycle_state = "monitoring"

    def can_finish(self) -> tuple[bool, dict[str, Any]]:
        """Finish-attempt gate result and structured reason."""
        pending = self.pending_jobs()
        if not pending:
            return True, {"can_finish": True, "pending_jobs": 0}
        preview = [
            {
                "job_id": j.job_id,
                "software": j.software,
                "state": j.lifecycle_state,
                "status": j.raw_status,
            }
            for j in pending[:10]
        ]
        return (
            False,
            {
                "can_finish": False,
                "finish_block_reason": "pending_async_jobs",
                "pending_jobs": len(pending),
                "pending_preview": preview,
            },
        )
