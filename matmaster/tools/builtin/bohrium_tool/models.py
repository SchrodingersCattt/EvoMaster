from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
