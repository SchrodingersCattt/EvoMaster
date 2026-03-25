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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    Contains environment info (workspace, session, cache) and
    externally-determined capability objects (llm_provider) whose
    selection is made outside the Exp layer (e.g. by frontend user).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    workdir: Path
    session_type: str  # "docker" | "local" | "ssh"
    cache_area: Path
    # Resolved directory where tools execute (may differ from workdir for remote sessions).
    # Empty string means "default to str(workdir)" (see model validator).
    execution_workdir: str = Field(default="")
    env_vars: dict[str, str] = Field(default_factory=dict)
    archival: WorkspaceArchivalConfig | None = None
    run_meta: dict[str, Any] = Field(default_factory=dict)
    session: Any = None  # EvoMaster BaseSession instance (per D-09)
    config_dir: Path | None = None  # Playground config directory (per D-10)
    llm_provider: Any = None  # LLMProvider instance (externally determined)
    llm_config: Any = None  # LLMConfig instance (externally loaded)

    @model_validator(mode="before")
    @classmethod
    def _default_execution_workdir(cls, data: Any) -> Any:
        if isinstance(data, dict):
            wd = data.get("workdir")
            ew = data.get("execution_workdir")
            if ew in (None, "") and wd is not None:
                return {**data, "execution_workdir": str(wd)}
        return data

    def with_execution(
        self,
        session: Any,
        session_type: str,
        execution_workdir: str,
    ) -> "PlaygroundContext":
        """Return a new frozen instance with execution binding fields updated."""
        return self.model_copy(
            update={
                "session": session,
                "session_type": session_type,
                "execution_workdir": execution_workdir,
            }
        )

    def with_bohrium(self, result: dict[str, Any]) -> "PlaygroundContext":
        """Return a new frozen instance with Bohrium result in run_meta.

        Since PlaygroundContext is frozen, this creates a copy with
        run_meta updated to include a 'bohrium' key. The original
        instance is not mutated.
        """
        updated_meta = {**self.run_meta, "bohrium": result}
        return self.model_copy(update={"run_meta": updated_meta})
