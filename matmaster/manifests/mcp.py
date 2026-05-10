from __future__ import annotations

from typing import Any


def _skill_mcp_server(skill: Any) -> str | None:
    raw = getattr(getattr(skill, "meta_info", None), "mcp_server", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def resolve_declared_servers(skills: list[Any]) -> set[str]:
    return {
        server for skill in skills if (server := _skill_mcp_server(skill)) is not None
    }


def resolve_runnable_servers(
    skills: list[Any],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: dict[str, list[dict[str, Any]]] | None = None,
) -> set[str]:
    declared = resolve_declared_servers(skills)
    runnable = set(declared)
    if legal_servers is not None:
        runnable &= set(legal_servers)
    if schemas_by_server is not None:
        runnable = {
            server
            for server in runnable
            if isinstance(schemas_by_server.get(server), list)
            and len(schemas_by_server.get(server) or []) > 0
        }
    return runnable


def format_active_mcp(
    skills: list[Any],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: dict[str, list[dict[str, Any]]] | None = None,
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
            name = schema.get("name")
            if isinstance(name, str) and name:
                lines.append(f"  - {server}_{name}")
    return "\n".join(lines)
