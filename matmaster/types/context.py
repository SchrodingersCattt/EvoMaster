"""PlaygroundContext frozen model -- Layer 1 boundary contract.

Playground layer output: environment context built by Playground.setup()
and passed to Exp.assemble(). frozen=True guarantees immutability during
inter-layer transfer.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlaygroundContext(BaseModel):
    """Playground 层输出的环境上下文契约。

    由 Playground.setup() 构建，传递给 Exp.assemble()。
    frozen=True 保证层间传递时不被意外修改。
    """

    model_config = ConfigDict(frozen=True)

    workdir: Path
    session_type: str  # "docker" | "local" | "ssh"
    cache_area: Path
    env_vars: dict[str, str] = Field(default_factory=dict)
    mcp_manager: Any = None  # Phase 4 defines Protocol
    skill_registry: Any = None  # Phase 3 defines Protocol
    run_meta: dict[str, Any] = Field(default_factory=dict)
