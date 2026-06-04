from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from matmaster.tools.builtin.bohrium_tool import tool as tmod


class _FakeLedger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def record_submit(self, **kw):
        self.calls.append(("submit", kw))

    def record_poll(self, **kw):
        self.calls.append(("poll", kw))

    def record_kill(self, **kw):
        self.calls.append(("kill", kw))

    def mark_handled(self, **kw):
        self.calls.append(("handled", kw))


def _ctx(sandbox: bool = True):
    return SimpleNamespace(
        sandbox=sandbox,
        credentials=SimpleNamespace(project_id=42, base_url="https://x"),
    )


def test_submit_records_ledger_after_job_add(monkeypatch) -> None:
    fake = _FakeLedger()
    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=fake)
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx(sandbox=True))
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(
        tmod,
        "submit_job_via_runtime",
        lambda **kw: SimpleNamespace(
            job_id="12345", raw_add_response={"jobId": "12345"}
        ),
    )
    res = bt._submit(
        {"input_dir": "in", "image": "img", "cmd": "run", "job_name": "jn"}
    )
    assert res.status == "success"
    assert fake.calls[0][0] == "submit"
    kw = fake.calls[0][1]
    assert kw["job_id"] == "12345"
    assert kw["job_name"] == "jn"
    assert kw["project_id"] == 42
    assert kw["sandbox"] is True
    assert kw["input_dir"] == "in"


def test_submit_ledger_failure_does_not_break_tool(monkeypatch) -> None:
    class _BoomLedger(_FakeLedger):
        def record_submit(self, **kw):
            raise RuntimeError("db down")

    bt = tmod.BohriumTool(session=None, workdir=Path("."), job_ledger=_BoomLedger())
    monkeypatch.setattr(bt, "_build_context", lambda **kw: _ctx())
    monkeypatch.setattr(bt, "_log_request_context", lambda **kw: None)
    monkeypatch.setattr(
        tmod,
        "submit_job_via_runtime",
        lambda **kw: SimpleNamespace(job_id="1", raw_add_response={}),
    )
    res = bt._submit({"input_dir": "in", "image": "img", "cmd": "run"})
    assert res.status == "success"
