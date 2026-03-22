"""Tests for Exp abstract base class and subclassing."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from matmaster.assembly.exp import Exp
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import FinishEvent
from matmaster.types.runtime import AgentRuntimeSpec


def _make_ctx() -> PlaygroundContext:
    return PlaygroundContext(
        workdir=Path("/tmp/test"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )


class _ConcreteExp(Exp):
    """Minimal Exp subclass for testing."""

    def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
        return MagicMock(spec=AgentRuntimeSpec)


class _NoAssembleExp(Exp):
    """Exp subclass missing assemble -- should fail instantiation."""

    pass


class TestExpAbstraction:
    def test_exp_is_abstract(self) -> None:
        """Cannot instantiate Exp() directly, raises TypeError."""
        with pytest.raises(TypeError):
            Exp()  # type: ignore[abstract]

    def test_subclass_must_implement_assemble(self) -> None:
        """Subclass without assemble() raises TypeError on instantiation."""
        with pytest.raises(TypeError):
            _NoAssembleExp()  # type: ignore[abstract]

    def test_subclass_with_assemble(self) -> None:
        """Subclass implementing assemble() can be instantiated."""
        exp = _ConcreteExp()
        assert isinstance(exp, Exp)


class TestExpNameProperty:
    def test_direct_exp_name(self) -> None:
        """DirectExp-like subclass strips 'Exp' suffix."""

        class DirectExp(Exp):
            def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
                return MagicMock(spec=AgentRuntimeSpec)

        assert DirectExp().exp_name == "Direct"

    def test_foobar_exp_name(self) -> None:
        """FooBarExp strips 'Exp' suffix to 'FooBar'."""

        class FooBarExp(Exp):
            def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
                return MagicMock(spec=AgentRuntimeSpec)

        assert FooBarExp().exp_name == "FooBar"

    def test_no_exp_suffix(self) -> None:
        """Class without 'Exp' suffix returns full name."""

        class MyClass(Exp):
            def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
                return MagicMock(spec=AgentRuntimeSpec)

        assert MyClass().exp_name == "MyClass"


class TestExpRun:
    def test_run_calls_assemble_then_kernel(self) -> None:
        """run() calls assemble first then kernel.run with returned spec."""
        exp = _ConcreteExp()
        ctx = _make_ctx()
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_finish = FinishEvent(
            source="agent", status="completed", reason="natural"
        )

        exp.assemble = MagicMock(return_value=mock_spec)  # type: ignore[method-assign]

        with patch("matmaster.engine.agent.AgentKernel") as MockKernel:
            mock_kernel_inst = MockKernel.return_value
            mock_kernel_inst.run.return_value = mock_finish

            result = exp.run(ctx, "do something")

            exp.assemble.assert_called_once_with(ctx)
            MockKernel.assert_called_once()
            mock_kernel_inst.run.assert_called_once_with(
                mock_spec, "do something", stop_event=None
            )
            assert result is mock_finish

    def test_assemble_kwargs_forwarded(self) -> None:
        """run() forwards **assemble_kwargs to assemble()."""
        exp = _ConcreteExp()
        ctx = _make_ctx()
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_finish = FinishEvent(
            source="agent", status="completed", reason="natural"
        )

        exp.assemble = MagicMock(return_value=mock_spec)  # type: ignore[method-assign]

        with patch("matmaster.engine.agent.AgentKernel") as MockKernel:
            mock_kernel_inst = MockKernel.return_value
            mock_kernel_inst.run.return_value = mock_finish

            exp.run(ctx, "task", extra_param="hello", another=42)

            exp.assemble.assert_called_once_with(ctx, extra_param="hello", another=42)
