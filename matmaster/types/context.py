"""PlaygroundContext frozen model -- Layer 1 boundary contract.

Playground layer output: environment context built by Playground.prepare()
and passed to Exp.assemble(). frozen=True guarantees immutability during
inter-layer transfer.

WorkspaceArchivalConfig: nested frozen contract for workspace archival
metadata (OSS bucket, prefix, credential ref). Populated by Playground
from config YAML; consumed by Service layer after run completes.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceArchivalConfig(BaseModel):
    """Workspace archival configuration.

    Frozen nested contract describing where and how to archive the
    workspace after a run completes.  The actual upload is performed
    by the Service layer in Phase 5; Playground only populates the
    metadata from config YAML.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    oss_bucket: str = ""
    oss_prefix: str = ""
    credential_ref: str = ""


class PlaygroundContext(BaseModel):
    """Playground layer environment context contract.

    Built by Playground.prepare(), passed to Exp.assemble().
    frozen=True guarantees immutability during inter-layer transfer.

    This contract is strictly environment-only: workspace path,
    session type, cache area, environment variables, archival config,
    and run metadata.  No capability objects (MCP, Skill, Tool, LLM)
    belong here.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    workdir: Path
    session_type: str  # "docker" | "local" | "ssh"
    cache_area: Path
    env_vars: dict[str, str] = Field(default_factory=dict)
    archival: WorkspaceArchivalConfig | None = None
    run_meta: dict[str, Any] = Field(default_factory=dict)
    session: Any = None  # EvoMaster BaseSession instance (per D-09)
    config_dir: Path | None = None  # Playground config directory (per D-10)

    def with_bohrium(self, result: dict[str, Any]) -> "PlaygroundContext":
        """Return a new frozen instance with Bohrium result in run_meta.

        Since PlaygroundContext is frozen, this creates a copy with
        run_meta updated to include a 'bohrium' key. The original
        instance is not mutated.
        """
        updated_meta = {**self.run_meta, "bohrium": result}
        return self.model_copy(update={"run_meta": updated_meta})
