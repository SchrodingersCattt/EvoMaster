from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matmaster.integration.runtime_bridge.models import ResolvedCredential

from .errors import BohriumCredentialError


@dataclass(frozen=True)
class BohriumContext:
    access_key: str
    project_id: int
    base_url: str
    credential_source: str
    sandbox: bool
    user_id: int | None = None
    user_no: str = ""

    @classmethod
    def from_resolved_credential(
        cls, cred: ResolvedCredential, *, sandbox: bool
    ) -> "BohriumContext":
        values = cred.values
        access_key = str(values.get("access_key") or "").strip()
        if not access_key:
            raise BohriumCredentialError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )

        raw_project_id = values.get("project_id", -1)
        project_id = raw_project_id if isinstance(raw_project_id, int) else -1
        return cls(
            access_key=access_key,
            project_id=project_id,
            base_url=str(values.get("base_url") or "").strip(),
            credential_source=cred.source,
            sandbox=sandbox,
            user_id=values.get("user_id"),
            user_no=str(values.get("user_no") or "").strip(),
        )


@dataclass(frozen=True)
class BohriumInputSource:
    kind: str
    raw_path: str
    resolved_path: str


@dataclass(frozen=True)
class BohriumDownloadTarget:
    kind: str
    raw_path: str
    resolved_path: str
    staging_dir: Path
    publish_mode: str
