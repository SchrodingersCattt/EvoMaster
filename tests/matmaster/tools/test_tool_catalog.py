"""Tests for ToolCatalog after dynamic description/prompt migration."""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationController
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology, ToolPlane


class _MinimalTool:
    resource_claims = ()
    capabilities = frozenset()
    effect_level = "local_mutation"
    fast_path_eligible = True
    max_result_chars = 0
    plane = ToolPlane.CONTROL_PLANE
    state_mode = "stateless"
    stop_mode = "cancellable"
    exposed_to_model = True

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"minimal {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content=f"executed {self._name}")


class _DynamicTool(_MinimalTool):
    capabilities = frozenset({"dynamic.use"})
    effect_level = "none"
    fast_path_eligible = True

    def describe(self, ctx: ToolDescriptionContext) -> str:
        return f"{self.name} on {ctx.session_kind}"

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        if ctx is None:
            return "dynamic prompt"
        return f"prompt:{self.name}:{ctx.workspace_root}"


class _PromptlessTool(_MinimalTool):
    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return None


class _HiddenTool(_MinimalTool):
    exposed_to_model = False


def _make_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
    )


def _make_ctx() -> ToolDescriptionContext:
    topology = _make_topology()
    return ToolDescriptionContext(
        session_kind=topology.session_kind,
        workspace_root=topology.workspace_root,
        topology=topology,
    )


def _make_catalog(*tools) -> ToolCatalog:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool, source="builtin")
    return ToolCatalog(registry, topology=_make_topology())


class TestCatalogVersion:
    def test_initial_version_is_zero(self) -> None:
        assert _make_catalog().version == 0

    def test_register_overlay_increments_version(self) -> None:
        catalog = _make_catalog()
        catalog.register_overlay(_MinimalTool("overlay"), source="mcp")

        assert catalog.version == 1


class TestCatalogGetTool:
    def test_returns_tool_instance(self) -> None:
        catalog = _make_catalog(_MinimalTool("my_tool"))

        instance = catalog.get_tool("my_tool")

        assert instance is not None
        assert isinstance(instance, ToolInstance)
        assert instance.tool_spec.tool_name == "my_tool"

    def test_returns_none_for_missing(self) -> None:
        assert _make_catalog().get_tool("missing") is None


class TestCatalogBuildDefinitions:
    def test_build_definitions_without_ctx_uses_compiled_static_description(
        self,
    ) -> None:
        catalog = _make_catalog(_DynamicTool("alpha"))

        defs = catalog.build_definitions()

        assert defs[0]["function"]["description"] == "minimal alpha"

    def test_build_definitions_with_ctx_uses_describe_when_available(self) -> None:
        catalog = _make_catalog(_DynamicTool("alpha"))

        defs = catalog.build_definitions(_make_ctx())

        assert defs[0]["function"]["description"] == "alpha on local"

    def test_build_definitions_with_ctx_falls_back_for_minimal_tool(self) -> None:
        catalog = _make_catalog(_MinimalTool("alpha"))

        defs = catalog.build_definitions(_make_ctx())

        assert defs[0]["function"]["description"] == "minimal alpha"

    def test_hidden_tool_is_excluded_from_definitions(self) -> None:
        catalog = _make_catalog(_MinimalTool("visible"), _HiddenTool("hidden"))

        defs = catalog.build_definitions(_make_ctx())
        names = {d["function"]["name"] for d in defs}

        assert names == {"visible"}


class TestCatalogPrompts:
    def test_collect_prompts_gathers_non_none_and_skips_missing_prompt(self) -> None:
        catalog = _make_catalog(
            _DynamicTool("alpha"),
            _PromptlessTool("beta"),
            _MinimalTool("gamma"),
        )

        prompts = catalog.collect_prompts(_make_ctx())

        assert prompts == "prompt:alpha:/tmp/workspace"

    def test_collect_prompts_defaults_to_legacy_behavior_without_metadata(self) -> None:
        catalog = _make_catalog(_DynamicTool("alpha"))

        prompts = catalog.collect_prompts(_make_ctx())

        assert prompts == "prompt:alpha:/tmp/workspace"


class TestCatalogCancelInjection:
    def test_inject_cancel_token_sets_tool_attribute(self) -> None:
        tool = _MinimalTool("alpha")
        catalog = _make_catalog(tool)
        ctrl = CancellationController()

        catalog.inject_cancel_token(ctrl.token)

        assert tool._cancel_token is ctrl.token


class TestCatalogContainer:
    def test_contains_and_len_delegate_to_registry(self) -> None:
        catalog = _make_catalog(_MinimalTool("a"), _MinimalTool("b"))

        assert "a" in catalog
        assert "missing" not in catalog
        assert len(catalog) == 2
