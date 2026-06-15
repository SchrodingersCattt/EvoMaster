from __future__ import annotations

import logging
from types import SimpleNamespace

import matmaster.core.runtime_context_assembly as rca


def _make_ctx(workspace_jobs_port) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(
            ports=SimpleNamespace(
                compaction=SimpleNamespace(history=None),
                workspace_jobs=workspace_jobs_port,
            ),
            user_instructions=None,
        ),
        environment=SimpleNamespace(
            session_id="sess-1",
            metadata=SimpleNamespace(task_id="ws_t"),
        ),
    )


def _patch_heavy(monkeypatch):
    captured: dict = {}

    class _FakeAssembler:
        def __init__(self, *, ports, session_context_factory, render_options) -> None:
            captured["ports"] = ports

    class _FakeCompactor:
        def __init__(self, **kwargs) -> None:
            captured["compactor_kwargs"] = kwargs

    monkeypatch.setattr(rca, "ContextAssembler", _FakeAssembler)
    monkeypatch.setattr(rca, "ContextCompactor", _FakeCompactor)
    return captured


def test_uses_injected_workspace_jobs_port(monkeypatch) -> None:
    captured = _patch_heavy(monkeypatch)
    fake_port = object()
    rca.build_runtime_context_assembly(
        llm_provider=object(),
        compaction=object(),
        ctx=_make_ctx(fake_port),
        skill_resolver=rca.empty_skill_resolver,
        spawn_id=None,
        logger=logging.getLogger("test"),
    )
    assert captured["ports"].workspace_jobs is fake_port


def test_falls_back_to_empty_port_when_none(monkeypatch) -> None:
    captured = _patch_heavy(monkeypatch)
    rca.build_runtime_context_assembly(
        llm_provider=object(),
        compaction=object(),
        ctx=_make_ctx(None),
        skill_resolver=rca.empty_skill_resolver,
        spawn_id=None,
        logger=logging.getLogger("test"),
    )
    port = captured["ports"].workspace_jobs
    assert isinstance(port, rca._EmptyWorkspaceJobsPort)
