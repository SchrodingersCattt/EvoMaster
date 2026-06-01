"""Type-boundary tests for the kernel runtime trio (spec sections 9.1 / 5.2).

AgentRuntimeSpec is gone; the kernel-facing runtime is now
AgentKernelSpec (config) + AgentKernelResources (live) bundled as
AgentKernelRuntime. The kernel-facing runtime must not expose context
assembly internals.
"""

from __future__ import annotations

import dataclasses

import pytest

from matmaster.types.run_metadata import RunIdentity
from matmaster.types.runtime import (
    AgentKernelResources,
    AgentKernelRuntime,
    AgentKernelSpec,
    AgentKernelTurnRequest,
    CompactionConfig,
)


def _kernel_spec() -> AgentKernelSpec:
    return AgentKernelSpec(
        system_prompt="sys",
        max_turns=10,
        compaction=CompactionConfig(),
        run_identity=RunIdentity(),
    )


def test_kernel_spec_holds_only_config_fields() -> None:
    names = {f.name for f in dataclasses.fields(AgentKernelSpec)}
    assert names == {
        "system_prompt",
        "max_turns",
        "compaction",
        "run_identity",
        "prompt_submit_rewrite_enabled",
        "llm_model",
        "llm_model_profile",
        "llm_model_route",
    }


def test_kernel_turn_request_holds_per_turn_input() -> None:
    names = {f.name for f in dataclasses.fields(AgentKernelTurnRequest)}
    assert names == {"user_message_content", "turn_input"}


def test_kernel_resources_holds_live_resource_fields() -> None:
    names = {f.name for f in dataclasses.fields(AgentKernelResources)}
    assert names == {
        "llm_provider",
        "runtime_ports",
        "tool_runner",
        "tool_catalog",
        "runtime_topology",
        "hook_executor",
        "compactor",
        "capability_policy",
        "structural_validation",
    }


def test_kernel_runtime_pairs_spec_and_resources() -> None:
    names = {f.name for f in dataclasses.fields(AgentKernelRuntime)}
    assert names == {"spec", "resources"}


def test_kernel_spec_is_frozen() -> None:
    spec = _kernel_spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.max_turns = 5  # type: ignore[misc]


def test_kernel_resources_is_frozen() -> None:
    resources = AgentKernelResources(
        llm_provider=object(),
        runtime_ports=object(),
        tool_runner=object(),
        tool_catalog=object(),
        runtime_topology=object(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        resources.tool_runner = object()  # type: ignore[misc]


def test_agent_runtime_spec_type_is_removed() -> None:
    import matmaster.types.runtime as runtime_module

    assert not hasattr(runtime_module, "AgentRuntimeSpec")


def test_kernel_spec_does_not_expose_assembly_internals() -> None:
    """Spec 5.2 / 7.2: kernel-facing config never carries assembly internals."""
    spec = _kernel_spec()
    assert not hasattr(spec, "context_assembler")
    assert not hasattr(spec, "session_events_port")
    assert not hasattr(spec, "session_jobs_port")
    assert not hasattr(spec, "system_prompt_builder")
    assert not hasattr(spec, "user_instructions_port")


def test_kernel_resources_does_not_expose_assembly_internals() -> None:
    resources = AgentKernelResources(
        llm_provider=object(),
        runtime_ports=object(),
        tool_runner=object(),
        tool_catalog=object(),
        runtime_topology=object(),
    )
    assert not hasattr(resources, "context_assembler")
    assert not hasattr(resources, "session_events_port")
    assert not hasattr(resources, "session_jobs_port")
    assert not hasattr(resources, "user_instructions_port")
