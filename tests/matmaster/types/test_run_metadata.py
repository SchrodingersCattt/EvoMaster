from __future__ import annotations

import pytest
from pydantic import ValidationError

from matmaster.context.ports import UserInstructions
from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.run_metadata import RunIdentity, RunMetadata


def test_run_metadata_defaults_are_typed_and_frozen() -> None:
    metadata = RunMetadata()

    assert metadata.run_dir == ""
    assert metadata.task_id == ""
    assert metadata.source == ""
    assert metadata.turn_input is None
    assert metadata.user_instructions is None
    assert metadata.active_skills == frozenset()
    assert metadata.bohrium_rebuild_events == ()

    with pytest.raises(ValidationError):
        metadata.task_id = "task-1"


def test_run_metadata_model_copy_returns_updated_instance() -> None:
    turn_input = TurnInput.from_values(user_text="hello")
    metadata = RunMetadata(
        run_dir="/tmp/run",
        task_id="task-1",
        turn_input=turn_input,
        active_skills=frozenset({"plot"}),
    )

    updated = metadata.model_copy(update={"task_id": "task-2"})

    assert metadata.task_id == "task-1"
    assert updated.task_id == "task-2"
    assert updated.run_dir == "/tmp/run"
    assert updated.turn_input is turn_input
    assert updated.active_skills == frozenset({"plot"})


def test_run_metadata_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RunMetadata(ghost_field="x")


def test_run_metadata_keeps_user_instructions_identity_on_copy() -> None:
    bundle = UserInstructions(
        text="project rules",
        hash="sha256:already-computed",
        truncated=True,
    )
    metadata = RunMetadata(user_instructions=bundle)

    updated = metadata.model_copy(update={"source": "web"})

    assert updated.user_instructions is bundle
    assert updated.user_instructions.hash == "sha256:already-computed"
    assert updated.user_instructions.truncated is True


def test_run_identity_defaults_are_typed_and_frozen() -> None:
    identity = RunIdentity()

    assert identity.task_id == ""
    assert identity.session_id == ""
    assert identity.spawn_id is None

    with pytest.raises(ValidationError):
        identity.session_id = "sess-1"


def test_run_identity_model_copy_returns_updated_instance() -> None:
    identity = RunIdentity(task_id="task-1", session_id="sess-1")

    updated = identity.model_copy(update={"spawn_id": "child-1"})

    assert identity.spawn_id is None
    assert updated.task_id == "task-1"
    assert updated.session_id == "sess-1"
    assert updated.spawn_id == "child-1"
