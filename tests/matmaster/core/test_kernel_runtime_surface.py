"""Helper parameter-surface + Exp surface tests (spec sections 9.5 / 9.6 / 5.4).

These guard the dependency-minimization outcome of the kernel runtime boundary
refactor: kernel helpers take only what they need, never the whole
``kernel_runtime``; Exp's public method surface is exactly build_runtime /
runtime_scope / run_stream; ``Exp.assemble()`` is gone.
"""

from __future__ import annotations

import inspect

from matmaster.core.agent import ensure_tool_definitions
from matmaster.core.agent_compaction import (
    run_compaction_plan,
    run_preflight_compaction_if_needed,
    run_runtime_compaction_if_needed,
)
from matmaster.core.agent_llm_stream import call_llm_streaming, stream_llm_items
from matmaster.core.agent_tool_dispatch import dispatch_tool_calls
from matmaster.core.exp import Exp


def _params(fn) -> set[str]:
    return set(inspect.signature(fn).parameters)


def test_dispatch_tool_calls_param_surface() -> None:
    params = _params(dispatch_tool_calls)
    assert params == {"tool_calls", "tool_runner", "max_turns", "state", "cancel_token"}
    assert "kernel_runtime" not in params
    assert "spec" not in params


def test_run_compaction_plan_splits_spec_and_resources() -> None:
    params = _params(run_compaction_plan)
    assert "kernel_spec" in params
    assert "kernel_resources" in params
    assert "kernel_runtime" not in params
    assert "spec" not in params


def test_run_preflight_compaction_splits_spec_and_resources() -> None:
    params = _params(run_preflight_compaction_if_needed)
    assert "kernel_spec" in params
    assert "kernel_resources" in params
    assert "kernel_runtime" not in params
    assert "spec" not in params


def test_run_runtime_compaction_splits_spec_and_resources() -> None:
    params = _params(run_runtime_compaction_if_needed)
    assert "kernel_spec" in params
    assert "kernel_resources" in params
    assert "turn_input" in params
    assert "kernel_runtime" not in params
    assert "spec" not in params


def test_call_llm_streaming_takes_kernel_resources_only() -> None:
    params = _params(call_llm_streaming)
    assert "kernel_resources" in params
    assert "kernel_runtime" not in params
    assert "spec" not in params


def test_stream_llm_items_takes_kernel_resources_only() -> None:
    params = _params(stream_llm_items)
    assert "kernel_resources" in params
    assert "kernel_runtime" not in params
    assert "spec" not in params


def test_ensure_tool_definitions_takes_kernel_resources() -> None:
    params = list(inspect.signature(ensure_tool_definitions).parameters)
    assert params[0] == "kernel_resources"
    assert "spec" not in params


def test_exp_public_method_surface_after_v2() -> None:
    public_methods = {
        name
        for name, value in vars(Exp).items()
        if not name.startswith("_") and callable(value)
    }
    assert public_methods == {"build_runtime", "runtime_scope", "run_stream"}


def test_exp_assemble_is_removed() -> None:
    assert not hasattr(Exp, "assemble")
