from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matmaster.bohrium.types import BohriumCredentials

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
    def from_credentials(
        cls,
        cred: BohriumCredentials,
        *,
        sandbox: bool,
        source: str = "runtime",
    ) -> BohriumContext:
        if not cred.access_key:
            raise BohriumCredentialError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )
        return cls(
            access_key=cred.access_key,
            project_id=cred.project_id,
            base_url=cred.base_url,
            credential_source=source,
            sandbox=sandbox,
            user_id=cred.user_id,
            user_no=cred.user_no,
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
