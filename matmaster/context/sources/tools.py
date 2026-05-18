from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import ActiveSkill
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


def resolve_declared_servers(skills: Iterable[ActiveSkill]) -> set[str]:
    return {skill.mcp_server for skill in skills if skill.mcp_server}


def resolve_runnable_servers(
    skills: Iterable[ActiveSkill],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> set[str]:
    declared = resolve_declared_servers(skills)
    runnable = set(declared)
    if legal_servers is not None:
        runnable &= set(legal_servers)
    if schemas_by_server is not None:
        runnable = {
            server
            for server in runnable
            if isinstance((schemas := schemas_by_server.get(server)), list) and schemas
        }
    return runnable


def format_active_mcp(
    skills: Iterable[ActiveSkill],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> str:
    declared = sorted(resolve_declared_servers(skills))
    if not declared:
        return ""
    runnable = resolve_runnable_servers(
        skills,
        legal_servers=legal_servers,
        schemas_by_server=schemas_by_server,
    )
    lines = ["[Active MCP servers]"]
    for server in declared:
        if server not in runnable:
            lines.append(f"- {server}: unavailable")
            continue
        schemas = (schemas_by_server or {}).get(server) or []
        lines.append(f"- {server}: available")
        for schema in schemas:
            name = schema.get("name") if isinstance(schema, Mapping) else None
            if isinstance(name, str) and name:
                lines.append(f"  - {server}_{name}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionToolsSource:
    skills: tuple[ActiveSkill, ...] = ()
    legal_servers: frozenset[str] | None = None
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None

    @classmethod
    def from_skills(
        cls,
        skills: Iterable[ActiveSkill],
        *,
        legal_servers: set[str] | None,
        schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
    ) -> SessionToolsSource:
        return cls(
            skills=tuple(skills),
            legal_servers=(
                frozenset(legal_servers) if legal_servers is not None else None
            ),
            schemas_by_server=schemas_by_server,
        )

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_active_mcp(
            self.skills,
            legal_servers=(
                set(self.legal_servers) if self.legal_servers is not None else None
            ),
            schemas_by_server=self.schemas_by_server,
        )
        if not text:
            return ()
        return (
            ContextSection(
                key="session_tools",
                tag="active_tools",
                content=text,
                order=SectionOrder.SESSION_TOOLS,
                views=ALL_VIEWS,
            ),
        )
