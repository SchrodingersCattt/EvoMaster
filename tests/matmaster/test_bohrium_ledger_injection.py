from __future__ import annotations

from typing import get_type_hints


def test_bohrium_job_ledger_port_has_sync_record_methods() -> None:
    from matmaster.context.ports import BohriumJobLedgerPort

    for name in ("record_submit", "record_poll", "record_kill", "mark_handled"):
        assert hasattr(BohriumJobLedgerPort, name)
    assert not hasattr(BohriumJobLedgerPort, "record_download")


def test_session_jobs_has_pending_terminal_jobs_field() -> None:
    from matmaster.context.ports import SessionJobs

    sj = SessionJobs.empty()
    assert sj.active_jobs == ()
    assert sj.pending_terminal_jobs == ()
    hints = get_type_hints(SessionJobs)
    assert "pending_terminal_jobs" in hints
