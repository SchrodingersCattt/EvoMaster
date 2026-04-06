"""Byte-level content probing for filesystem semantics."""

from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import CandidateEncoding, FileSemanticDiagnostic


@dataclass(frozen=True, slots=True)
class ProbeResult:
    kind: str
    encoding: str | None
    encoding_source: str
    diagnostic: FileSemanticDiagnostic | None = None


def _looks_like_utf16_without_bom(raw: bytes) -> bool:
    if len(raw) < 4:
        return False
    even_nuls = sum(1 for byte in raw[0::2][:16] if byte == 0)
    odd_nuls = sum(1 for byte in raw[1::2][:16] if byte == 0)
    return even_nuls >= 3 or odd_nuls >= 3


def _has_binary_control_profile(raw: bytes) -> bool:
    nontext_controls = sum(1 for byte in raw if byte < 32 and byte not in (9, 10, 13))
    return nontext_controls >= max(2, len(raw) // 8)


def probe_content_bytes(raw: bytes) -> ProbeResult:
    if raw.startswith(b"\xef\xbb\xbf"):
        return ProbeResult("definite_text", "utf-8-sig", "bom")
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return ProbeResult("definite_text", "utf-32", "bom")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ProbeResult("definite_text", "utf-16", "bom")

    if _looks_like_utf16_without_bom(raw):
        return ProbeResult("recovered_text", "utf-16", "nul_pattern")

    try:
        raw.decode("utf-8")
        if _has_binary_control_profile(raw):
            return ProbeResult("binary_suspect", None, "binary_heuristic")
        return ProbeResult("definite_text", "utf-8", "strict_utf8")
    except UnicodeDecodeError:
        pass

    if raw.count(b"\x00") >= max(1, len(raw) // 4):
        return ProbeResult("binary_suspect", None, "binary_heuristic")

    candidates = (
        CandidateEncoding("gb18030", 0.60),
        CandidateEncoding("utf-16", 0.35),
    )
    return ProbeResult(
        "candidate_text",
        None,
        "candidate_probe",
        diagnostic=FileSemanticDiagnostic(
            kind="candidate_text",
            reason="utf8_decode_failed_with_viable_alternatives",
            confidence="medium",
            recovery_applied=False,
            candidates=candidates,
            safe_retry=True,
            recommended_action="retry_read_with_encoding",
        ),
    )
