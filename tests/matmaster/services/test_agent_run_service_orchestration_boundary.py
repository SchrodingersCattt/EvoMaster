from __future__ import annotations

import inspect
from pathlib import Path

from matmaster.core.exp import Exp
from matmaster.types.runtime import AgentKernelResources, AgentKernelSpec
from matmaster.types.runtime_ports import AgentRunPorts
from src.services.agent_run_service import AgentRunService


def _source_text(obj) -> str:
    path = Path(inspect.getsourcefile(obj) or "")
    assert path.exists()
    return path.read_text(encoding="utf-8")


def test_agent_run_service_no_longer_imports_context_assembly_adapters() -> None:
    text = _source_text(AgentRunService)

    forbidden = (
        "ContextAssembler",
        "build_context_assembler",
        "resolve_turn_context_intent",
        "write_user_turn_context_event(",
        "_active_skills",
        "_resolve_active_skill_names",
        "SkillRegistryResolver",
    )
    for value in forbidden:
        assert value not in text


def test_core_exp_does_not_import_src_services() -> None:
    text = _source_text(Exp)

    assert "src.services" not in text


def test_kernel_runtime_surface_has_no_context_assembly_fields() -> None:
    forbidden = {
        "context_runtime",
        "context_assembler",
        "assembly_ports",
        "user_turn_context_writer",
    }

    assert forbidden.isdisjoint(AgentKernelSpec.__dataclass_fields__)
    assert forbidden.isdisjoint(AgentKernelResources.__dataclass_fields__)


def test_agent_run_ports_has_writer_but_no_bag_fields() -> None:
    fields = set(AgentRunPorts.__dataclass_fields__)

    assert "user_turn_context_writer" in fields
    assert "extra" not in fields
    assert "metadata" not in fields
    assert "state" not in fields
    assert "context" not in fields
    assert "services" not in fields
    assert "payload" not in fields
