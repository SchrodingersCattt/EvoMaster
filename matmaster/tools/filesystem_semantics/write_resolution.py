"""Write-time encoding decisions for filesystem semantics."""

from __future__ import annotations

from dataclasses import dataclass

from .content_probe import ProbeResult
from .diagnostics import FileSemanticDiagnostic
from .snapshots import FileSemanticSnapshot, SnapshotFingerprint


@dataclass(frozen=True, slots=True)
class WriteDecision:
    status: str
    encoding: str | None
    source: str
    diagnostic: FileSemanticDiagnostic | None = None


def resolve_write_request(
    *,
    existing_snapshot: FileSemanticSnapshot | None,
    current_fingerprint: SnapshotFingerprint | None = None,
    current_probe: ProbeResult | None = None,
    explicit_encoding: str | None,
    file_exists: bool,
) -> WriteDecision:
    if explicit_encoding:
        return WriteDecision("allow", explicit_encoding, "explicit")
    if not file_exists:
        return WriteDecision("allow", "utf-8", "new_file")

    if existing_snapshot is not None and current_fingerprint is None:
        if existing_snapshot.kind == "definite_text":
            return WriteDecision("allow", existing_snapshot.encoding, "snapshot")
        return WriteDecision("deny", None, "snapshot")

    if (
        existing_snapshot is not None
        and current_fingerprint is not None
        and existing_snapshot.fingerprint == current_fingerprint
    ):
        if existing_snapshot.kind == "definite_text":
            return WriteDecision("allow", existing_snapshot.encoding, "snapshot")
        return WriteDecision("deny", None, "snapshot")

    if current_probe is not None and current_probe.kind == "definite_text":
        return WriteDecision("allow", current_probe.encoding, "fresh_probe")

    return WriteDecision("deny", None, "probe")
