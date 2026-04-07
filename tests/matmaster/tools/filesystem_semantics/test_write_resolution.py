from matmaster.tools.filesystem_semantics.content_probe import ProbeResult
from matmaster.tools.filesystem_semantics.snapshots import (
    FileSemanticSnapshot,
    SnapshotFingerprint,
)
from matmaster.tools.filesystem_semantics.write_resolution import resolve_write_request


def test_write_resolution_inherits_definite_snapshot_encoding() -> None:
    snapshot = FileSemanticSnapshot(
        path="/workspace/f.txt",
        fingerprint=SnapshotFingerprint(size=5, mtime=1.0, prefix_hash="aaa"),
        kind="definite_text",
        encoding="utf-16",
        encoding_source="bom",
    )
    result = resolve_write_request(
        existing_snapshot=snapshot,
        explicit_encoding=None,
        file_exists=True,
    )
    assert result.status == "allow"
    assert result.encoding == "utf-16"


def test_write_resolution_allows_fresh_definite_probe_without_snapshot() -> None:
    result = resolve_write_request(
        existing_snapshot=None,
        current_fingerprint=SnapshotFingerprint(size=8, mtime=3.0, prefix_hash="zzz"),
        current_probe=ProbeResult("definite_text", "utf-16", "bom"),
        explicit_encoding=None,
        file_exists=True,
    )
    assert result.status == "allow"
    assert result.encoding == "utf-16"


def test_write_resolution_rejects_recovered_snapshot_without_explicit_encoding() -> (
    None
):
    snapshot = FileSemanticSnapshot(
        path="/workspace/f.txt",
        fingerprint=SnapshotFingerprint(size=5, mtime=1.0, prefix_hash="aaa"),
        kind="recovered_text",
        encoding="utf-16",
        encoding_source="nul_pattern",
    )
    result = resolve_write_request(
        existing_snapshot=snapshot,
        current_fingerprint=snapshot.fingerprint,
        current_probe=None,
        explicit_encoding=None,
        file_exists=True,
    )
    assert result.status == "deny"


def test_write_resolution_rejects_candidate_probe_without_explicit_encoding() -> None:
    result = resolve_write_request(
        existing_snapshot=None,
        current_fingerprint=SnapshotFingerprint(size=8, mtime=3.0, prefix_hash="zzz"),
        current_probe=ProbeResult("candidate_text", None, "candidate_probe"),
        explicit_encoding=None,
        file_exists=True,
    )
    assert result.status == "deny"
