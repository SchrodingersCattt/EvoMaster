"""child exp 的 per-subagent LLM profile 解析。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matmaster.config.exp import ExpConfig
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.types.runtime_ports import AgentRunPorts


@dataclass
class _FakeBundle:
    provider: object
    model: str
    model_profile: str
    model_route: str | None
    context_limit: int
    supports_vision: bool
    vision_detail: str | None


def _make_ctx() -> AgentRunContext:
    return AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=Path("/tmp/test"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        ),
        request=AgentRunRequest(),
    )


def _ctx_with_factory(factory):
    base = _make_ctx()
    return base.model_copy(
        update={
            "request": base.request.model_copy(
                update={
                    "llm_provider": "PARENT",
                    "llm_model": "parent-model",
                    "llm_model_profile": "parent/profile",
                    "ports": AgentRunPorts(subagent_provider_factory=factory),
                }
            )
        }
    )


class TestResolveChildRunCtx:
    def test_no_llm_inherits_parent(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        ctx = _ctx_with_factory(lambda *, profile_key: None)
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm=None))
        assert out is ctx

    def test_factory_none_inherits_parent(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        ctx = _make_ctx()
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm="some/profile"))
        assert out is ctx

    def test_configured_llm_overrides_provider(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        bundle = _FakeBundle(
            provider="CHILD",
            model="child-model",
            model_profile="child/profile",
            model_route="child/profile",
            context_limit=123,
            supports_vision=True,
            vision_detail="high",
        )
        ctx = _ctx_with_factory(lambda *, profile_key: bundle)
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm="child/profile"))
        assert out is not ctx
        assert out.request.llm_provider == "CHILD"
        assert out.request.llm_model == "child-model"
        assert out.request.llm_model_profile == "child/profile"
        assert out.request.context_limit == 123
        assert out.request.supports_vision is True

    def test_keyerror_falls_back_to_parent(self):
        from matmaster.core.exp import _resolve_child_run_ctx

        def boom(*, profile_key):
            raise KeyError(profile_key)

        ctx = _ctx_with_factory(boom)
        out = _resolve_child_run_ctx(ctx, ExpConfig(llm="bad/key"))
        assert out is ctx
        assert out.request.llm_provider == "PARENT"
