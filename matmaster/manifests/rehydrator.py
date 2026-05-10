from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from matmaster.manifests import attachment as attachment_manifest
from matmaster.manifests import mcp as mcp_manifest
from matmaster.manifests import skill as skill_manifest

logger = logging.getLogger(__name__)


class CompactionRehydrator:
    def __init__(
        self,
        *,
        get_query_events: Callable[[], list[dict[str, Any]]],
        get_all_events: Callable[[], list[dict[str, Any]]],
        get_latest_checkpoint_covered_until_event_id: Callable[[], int | None]
        | None = None,
        skill_registry: Any,
        playground_ctx: Any,
        legal_mcp_servers: set[str] | None = None,
        schemas_by_server: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._get_query_events = get_query_events
        self._get_all_events = get_all_events
        self._get_latest_checkpoint_covered_until_event_id = (
            get_latest_checkpoint_covered_until_event_id
        )
        self._skill_registry = skill_registry
        self._playground_ctx = playground_ctx
        self._legal_mcp_servers = legal_mcp_servers
        self._schemas_by_server = schemas_by_server

    async def build(self) -> str:
        query_events = self._safe_call("query_events", self._get_query_events, [])
        all_events = self._safe_call("all_events", self._get_all_events, [])
        latest_covered_until = None
        if self._get_latest_checkpoint_covered_until_event_id is not None:
            latest_covered_until = self._safe_call(
                "latest_checkpoint",
                self._get_latest_checkpoint_covered_until_event_id,
                None,
            )

        skills = self._safe_call(
            "loaded_skills",
            lambda: skill_manifest.resolve_active_skills(
                all_events,
                self._skill_registry,
            ),
            [],
        )
        attachments_text = self._safe_call(
            "attachments",
            lambda: attachment_manifest.format_available_attachments(
                attachment_manifest.filter_entries_after_event_id(
                    attachment_manifest.build_available_attachments(query_events),
                    latest_covered_until,
                )
            ),
            "",
        )
        loaded_skills_text = self._safe_call(
            "loaded_skills_text",
            lambda: skill_manifest.format_loaded_skills(skills),
            "",
        )
        active_mcp_text = self._safe_call(
            "active_mcp",
            lambda: mcp_manifest.format_active_mcp(
                skills,
                legal_servers=self._legal_mcp_servers,
                schemas_by_server=self._schemas_by_server,
            ),
            "",
        )

        return self._compose(
            attachments=attachments_text,
            loaded_skills=loaded_skills_text,
            active_mcp=active_mcp_text,
            runtime_context="",
            external_artifacts="",
        )

    @staticmethod
    def _safe_call(name: str, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:
            logger.warning("compaction rehydrator manifest failed: %s", name, exc_info=True)
            return default

    @staticmethod
    def _wrap(tag: str, content: str) -> str:
        text = (content or "").strip()
        if not text:
            return ""
        return f"<{tag}>\n{text}\n</{tag}>"

    def _compose(
        self,
        *,
        attachments: str,
        loaded_skills: str,
        active_mcp: str,
        runtime_context: str,
        external_artifacts: str,
    ) -> str:
        sections = [
            self._wrap("attachments", attachments),
            self._wrap("loaded_skills", loaded_skills),
            self._wrap("active_tools", active_mcp),
            self._wrap("runtime_context", runtime_context),
            self._wrap("external_artifacts", external_artifacts),
        ]
        return "\n\n".join(section for section in sections if section)

