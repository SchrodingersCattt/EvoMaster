from __future__ import annotations

from matmaster.core.agent import ensure_tool_definitions
from matmaster.core.kernel_items import _KernelState
from matmaster.types.messages import SystemMessage
from matmaster.types.runtime import AgentRuntimeSpec


class Catalog:
    def __init__(self) -> None:
        self.version = 1
        self.calls = []

    def build_definitions(self, desc_ctx):
        self.calls.append(desc_ctx)
        return [{"type": "function", "function": {"name": f"tool_v{self.version}"}}]


def _spec(catalog=None, topology=None):
    return AgentRuntimeSpec.model_construct(
        tool_catalog=catalog,
        runtime_topology=topology,
        system_prompt_builder=object(),
    )


def test_ensure_tool_definitions_returns_none_without_catalog() -> None:
    state = _KernelState(messages=[SystemMessage(content="sys")])

    assert ensure_tool_definitions(_spec(), state) is None
    assert state.cached_tool_definitions is None


def test_ensure_tool_definitions_caches_same_list_object() -> None:
    catalog = Catalog()
    state = _KernelState(messages=[SystemMessage(content="sys")])
    spec = _spec(catalog)

    first = ensure_tool_definitions(spec, state)
    second = ensure_tool_definitions(spec, state)

    assert first is second
    assert catalog.calls == [None]


def test_ensure_tool_definitions_rebuilds_on_catalog_version_change() -> None:
    catalog = Catalog()
    state = _KernelState(messages=[SystemMessage(content="sys")])
    spec = _spec(catalog)

    first = ensure_tool_definitions(spec, state)
    catalog.version = 2
    second = ensure_tool_definitions(spec, state)

    assert first is not second
    assert second == [{"type": "function", "function": {"name": "tool_v2"}}]
    assert len(catalog.calls) == 2
