from __future__ import annotations


def test_core_context_builder_shim_reexports_context_implementation() -> None:
    from matmaster.context.system_prompt import ContextBuilder as NewContextBuilder
    from matmaster.core.context_builder import ContextBuilder as ShimContextBuilder

    assert ShimContextBuilder is NewContextBuilder
