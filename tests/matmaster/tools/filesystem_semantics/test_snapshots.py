from matmaster.tools.filesystem_semantics import (
    FileSemanticDiagnostic,
    FileSemanticSnapshot,
    SnapshotFingerprint,
    merge_snapshot,
    put_snapshot,
)
from matmaster.types.tool_runner_state import ToolRunnerState


def make_snapshot(
    path: str,
    *,
    size: int,
    mtime: float,
    prefix_hash: str,
    last_access_ns: int,
    kind: str = "text",
    encoding: str | None = "utf-8",
    encoding_source: str = "detected",
    diagnostic: FileSemanticDiagnostic | None = None,
) -> FileSemanticSnapshot:
    return FileSemanticSnapshot(
        path=path,
        kind=kind,
        encoding=encoding,
        encoding_source=encoding_source,
        diagnostic=diagnostic,
        fingerprint=SnapshotFingerprint(
            size=size,
            mtime=mtime,
            prefix_hash=prefix_hash,
        ),
        last_access_ns=last_access_ns,
    )


def test_merge_snapshot_returns_new_for_same_fingerprint() -> None:
    old = make_snapshot(
        "/tmp/a.txt", size=10, mtime=1.0, prefix_hash="abc", last_access_ns=1
    )
    new = make_snapshot(
        "/tmp/a.txt", size=10, mtime=1.0, prefix_hash="abc", last_access_ns=2
    )

    assert merge_snapshot(old, new) == new


def test_merge_snapshot_returns_none_for_mismatch() -> None:
    old = make_snapshot(
        "/tmp/a.txt", size=10, mtime=1.0, prefix_hash="abc", last_access_ns=1
    )
    new = make_snapshot(
        "/tmp/a.txt", size=11, mtime=1.0, prefix_hash="abc", last_access_ns=2
    )

    assert merge_snapshot(old, new) is None


def test_put_snapshot_evicts_oldest_by_last_access_ns() -> None:
    state = ToolRunnerState()
    older = make_snapshot(
        "/tmp/a.txt", size=10, mtime=1.0, prefix_hash="a", last_access_ns=1
    )
    middle = make_snapshot(
        "/tmp/b.txt", size=10, mtime=2.0, prefix_hash="b", last_access_ns=2
    )
    newest = make_snapshot(
        "/tmp/c.txt", size=10, mtime=3.0, prefix_hash="c", last_access_ns=3
    )

    nullable = make_snapshot(
        "/tmp/d.txt",
        size=10,
        mtime=4.0,
        prefix_hash="d",
        last_access_ns=4,
        encoding=None,
        diagnostic=None,
    )
    assert nullable.encoding is None
    assert nullable.diagnostic is None

    put_snapshot(state, older, max_entries=2)
    put_snapshot(state, middle, max_entries=2)
    put_snapshot(state, newest, max_entries=2)

    stored = state.get("file_semantics")

    assert set(stored) == {"/tmp/b.txt", "/tmp/c.txt"}
    assert stored["/tmp/b.txt"] is middle
    assert stored["/tmp/c.txt"] is newest


def test_put_snapshot_treats_zero_last_access_as_current(monkeypatch) -> None:
    state = ToolRunnerState()
    monkeypatch.setattr(
        "matmaster.tools.filesystem_semantics.snapshots.monotonic_ns",
        lambda: 100,
    )

    zero_access = make_snapshot(
        "/tmp/zero.txt", size=10, mtime=1.0, prefix_hash="z", last_access_ns=0
    )
    older = make_snapshot(
        "/tmp/older.txt", size=10, mtime=2.0, prefix_hash="o", last_access_ns=1
    )
    newest = make_snapshot(
        "/tmp/newest.txt", size=10, mtime=3.0, prefix_hash="n", last_access_ns=3
    )

    put_snapshot(state, zero_access, max_entries=2)
    put_snapshot(state, older, max_entries=2)
    put_snapshot(state, newest, max_entries=2)

    stored = state.get("file_semantics")

    assert set(stored) == {"/tmp/zero.txt", "/tmp/newest.txt"}
    assert stored["/tmp/zero.txt"] is zero_access
    assert stored["/tmp/newest.txt"] is newest
