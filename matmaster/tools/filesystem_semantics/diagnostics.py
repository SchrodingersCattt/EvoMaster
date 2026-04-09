"""Shared diagnostics for filesystem semantic probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CandidateEncoding:
    encoding: str
    confidence: float = 0.0

    def to_meta(self) -> dict[str, Any]:
        return {
            "encoding": self.encoding,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class FileSemanticDiagnostic:
    kind: str
    reason: str
    confidence: str
    recovery_applied: bool
    candidates: tuple[CandidateEncoding, ...] = field(default_factory=tuple)
    safe_retry: bool = False
    recommended_action: str = ""

    def to_meta(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "confidence": self.confidence,
            "recovery_applied": self.recovery_applied,
            "candidates": [candidate.to_meta() for candidate in self.candidates],
            "safe_retry": self.safe_retry,
            "recommended_action": self.recommended_action,
        }
