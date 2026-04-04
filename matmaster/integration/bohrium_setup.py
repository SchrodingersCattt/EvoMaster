"""BohriumSetupService -- Bohrium node lifecycle utilities.

The full production node-pool management (setup_bohrium_for_run, cleanup, etc.)
was part of the hosted backend (src/services/agent_run_bohrium.py) which is not
included in the open-source release. Only the skill-sync utility and a stub class
are kept here for API compatibility.

Direct Bohrium job submission via the `bohrium-job` skill and `bohrium-sdk`
is fully supported and does not require this service.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from matmaster.config.exp import ExpConfig
    from matmaster.core.bus import MessageBus


@dataclass(frozen=True)
class SkillSyncSpec:
    """Resolved paths for project skill sync to remote Bohrium node."""

    project_skill_roots: list[str]
    remote_project_root: str


def derive_skill_sync_spec(
    exp_config: ExpConfig,
    *,
    project_root: Path,
) -> SkillSyncSpec | None:
    """Resolve ExpConfig.skills into a SkillSyncSpec for Bohrium upload."""
    skills = exp_config.skills
    if not skills.enabled:
        return None

    raw_value = skills.skills_root
    if isinstance(raw_value, list):
        relative_paths = [
            entry.strip() for entry in raw_value if entry and entry.strip()
        ]
    else:
        stripped = (raw_value or "").strip()
        relative_paths = [stripped] if stripped else []
    if not relative_paths:
        return None

    resolved_roots: list[str] = []
    for rel_path in relative_paths:
        path = Path(rel_path)
        resolved = (
            path.resolve()
            if path.is_absolute()
            else (project_root / rel_path).resolve()
        )
        if resolved.is_dir():
            resolved_roots.append(str(resolved))
    if not resolved_roots:
        return None

    return SkillSyncSpec(
        project_skill_roots=resolved_roots,
        remote_project_root="/share/.matmaster",
    )


class BohriumSetupResult:
    """Placeholder for the production BohriumSetupResult type."""


class BohriumSetupService:
    """Bohrium node lifecycle service.

    In the open-source release the full production node-pool orchestration
    (provision/teardown of remote Bohrium compute nodes via the hosted backend)
    is not included. Direct Bohrium job submission via the `bohrium-job` skill
    and the `bohrium-sdk` package is still fully supported.
    """

    def __init__(
        self,
        sessions_service: Any = None,
        bus: MessageBus | None = None,
    ) -> None:
        self._sessions_service = sessions_service
        self._bus = bus

    def load_credentials(self, session_id: str) -> tuple[dict[str, Any], str | None, str]:
        raise NotImplementedError(
            "Production Bohrium node management is not included in the OSS release. "
            "Use the bohrium-job skill for direct job submission."
        )

    def apply_credentials(self, session: Any, run_creds: dict[str, Any]) -> None:
        raise NotImplementedError("Production Bohrium node management not included.")

    def setup(self, **kwargs: Any) -> BohriumSetupResult:
        raise NotImplementedError("Production Bohrium node management not included.")

    def cleanup(self, **kwargs: Any) -> None:
        raise NotImplementedError("Production Bohrium node management not included.")

    async def run_setup(self, **kwargs: Any) -> BohriumSetupResult:
        raise NotImplementedError("Production Bohrium node management not included.")

    async def run_cleanup(self, **kwargs: Any) -> None:
        raise NotImplementedError("Production Bohrium node management not included.")
