"""Snapshot helpers for filesystem semantic probes."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns

from matmaster.types.tool_runner_state import ToolRunnerState

from .diagnostics import FileSemanticDiagnostic

DEFAULT_SNAPSHOT_LIMIT = 128


@dataclass(frozen=True, slots=True)
class SnapshotFingerprint:
    size: int
    mtime: float
    prefix_hash: str


@dataclass(frozen=True, slots=True)
class FileSemanticSnapshot:
    path: str
    kind: str
    encoding: str | None
    encoding_source: str
    fingerprint: SnapshotFingerprint
    diagnostic: FileSemanticDiagnostic | None = None
    last_access_ns: int = 0


def merge_snapshot(
    old: FileSemanticSnapshot | None,
    new: FileSemanticSnapshot,
) -> FileSemanticSnapshot | None:
    if old is None:
        return new
    if old.fingerprint == new.fingerprint:
        return new
    return None


def put_snapshot(
    runner_state: ToolRunnerState,
    snapshot: FileSemanticSnapshot,
    *,
    max_entries: int = DEFAULT_SNAPSHOT_LIMIT,
) -> None:
    stored = dict(runner_state.get("file_semantics", {}))
    stored[snapshot.path] = snapshot

    while max_entries >= 0 and len(stored) > max_entries:
        effective_access = {
            path: item.last_access_ns or monotonic_ns()
            for path, item in stored.items()
        }
        oldest_path = min(effective_access, key=effective_access.get)
        stored.pop(oldest_path, None)

    runner_state.set("file_semantics", stored)
