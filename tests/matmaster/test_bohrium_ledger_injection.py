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


def test_agent_run_ports_carry_bohrium_and_session_jobs_ports() -> None:
    import dataclasses

    from matmaster.types.runtime_ports import AgentRunPorts

    fields = {f.name for f in dataclasses.fields(AgentRunPorts)}
    assert "bohrium_job_ledger" in fields
    assert "session_jobs" in fields
    p = AgentRunPorts()
    assert p.bohrium_job_ledger is None
    assert p.session_jobs is None


def test_bohrium_tool_accepts_job_ledger() -> None:
    from pathlib import Path

    from matmaster.tools.builtin.bohrium_tool.tool import BohriumTool

    sentinel = object()
    bt = BohriumTool(session=None, workdir=Path("."), job_ledger=sentinel)
    assert bt._job_ledger is sentinel


def test_bohrium_tool_defaults_job_ledger_none() -> None:
    from pathlib import Path

    from matmaster.tools.builtin.bohrium_tool.tool import BohriumTool

    bt = BohriumTool(session=None, workdir=Path("."))
    assert bt._job_ledger is None


def test_kernel_does_not_import_bohrium_jobs_dao() -> None:
    import pathlib

    kernel_root = pathlib.Path(__file__).resolve().parents[2] / "matmaster"
    offenders = []
    for path in kernel_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "src.dao" in text or "from src." in text or "import src" in text:
            offenders.append(str(path))
    assert offenders == [], f"kernel must not import src.*: {offenders}"
