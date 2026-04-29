from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .security import secure_write_json


class ManifestStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def transfer_dir(self, transfer_id: str) -> Path:
        return self.root / transfer_id

    def read(self, transfer_id: str) -> dict[str, Any]:
        path = self.transfer_dir(transfer_id) / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, transfer_id: str, manifest: dict[str, Any]) -> None:
        directory = self.transfer_dir(transfer_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "manifest.json"
        tmp = directory / f"manifest.{time.time_ns()}.tmp"
        secure_write_json(tmp, manifest)
        tmp.replace(path)
        path.chmod(0o600)

    def gc(self, *, older_than_seconds: int = 7 * 24 * 3600) -> list[Path]:
        now = time.time()
        removed: list[Path] = []
        if not self.root.exists():
            return removed
        for child in self.root.iterdir():
            if not child.is_dir() or (child / "lock").exists():
                continue
            if now - child.stat().st_mtime > older_than_seconds:
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child)
        return removed
