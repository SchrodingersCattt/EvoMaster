from __future__ import annotations

from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.artifacts import SessionArtifactsSource
from matmaster.context.sources.workspace import SessionWorkspaceSource


def test_workspace_source_empty_returns_no_sections() -> None:
    assert SessionWorkspaceSource(text="").to_sections() == ()


def test_workspace_source_renders_checkpoint_visible_section() -> None:
    section = SessionWorkspaceSource(text="/share/result.xyz").to_sections()[0]

    assert section.key == "session_workspace"
    assert section.tag == "session_workspace"
    assert section.order == SectionOrder.SESSION_WORKSPACE
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


def test_artifacts_source_empty_returns_no_sections() -> None:
    assert SessionArtifactsSource(text="").to_sections() == ()


def test_artifacts_source_renders_checkpoint_visible_section() -> None:
    section = SessionArtifactsSource(text="figure: /share/a.png").to_sections()[0]

    assert section.key == "session_artifacts"
    assert section.tag == "session_artifacts"
    assert section.order == SectionOrder.SESSION_ARTIFACTS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
