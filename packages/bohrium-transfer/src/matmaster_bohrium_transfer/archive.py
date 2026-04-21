from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchiveResult:
    archive_path: Path
    archive_size: int
    archive_mtime_ns: int
    source_fingerprint: str
    archive_format: str = "zip"
    archive_compression: str = "stored"


def directory_fingerprint(input_dir: str | Path) -> str:
    root = Path(input_dir)
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        st = path.lstat()
        kind = "symlink" if path.is_symlink() else "file"
        entries.append(
            {
                "rel_path": path.relative_to(root).as_posix(),
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "mode": stat.S_IMODE(st.st_mode),
                "kind": kind,
            }
        )
    raw = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def create_zip_store(input_dir: str | Path, archive_path: str | Path) -> ArchiveResult:
    root = Path(input_dir)
    target = Path(archive_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_fingerprint = directory_fingerprint(root)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                zf.write(path, path.relative_to(root).as_posix())
    st = target.stat()
    return ArchiveResult(
        archive_path=target,
        archive_size=st.st_size,
        archive_mtime_ns=st.st_mtime_ns,
        source_fingerprint=source_fingerprint,
    )
