"""Resolve raw bytes into stable text semantics."""

from __future__ import annotations

from dataclasses import dataclass

from .content_probe import probe_content_bytes
from .diagnostics import FileSemanticDiagnostic


@dataclass(frozen=True, slots=True)
class TextResolution:
    status: str
    semantic_kind: str
    text: str | None
    encoding: str | None
    encoding_source: str
    diagnostic: FileSemanticDiagnostic | None = None


def resolve_text_bytes(raw: bytes, explicit_encoding: str | None) -> TextResolution:
    if explicit_encoding:
        return TextResolution(
            status="success",
            semantic_kind="definite_text",
            text=raw.decode(explicit_encoding),
            encoding=explicit_encoding,
            encoding_source="explicit",
        )

    probe = probe_content_bytes(raw)
    if probe.kind in {"definite_text", "recovered_text"}:
        return TextResolution(
            status="success",
            semantic_kind=probe.kind,
            text=raw.decode(probe.encoding or "utf-8"),
            encoding=probe.encoding,
            encoding_source=probe.encoding_source,
            diagnostic=probe.diagnostic,
        )

    return TextResolution(
        status="error",
        semantic_kind=probe.kind,
        text=None,
        encoding=probe.encoding,
        encoding_source=probe.encoding_source,
        diagnostic=probe.diagnostic,
    )
