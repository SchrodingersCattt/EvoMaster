from matmaster.tools.filesystem_semantics import (
    CandidateEncoding,
    FileSemanticDiagnostic,
)


def test_file_semantic_diagnostic_to_meta_minimum_contract() -> None:
    diagnostic = FileSemanticDiagnostic(
        kind="encoding",
        reason="decoded with fallback",
        confidence="medium",
        recovery_applied=True,
        candidates=(CandidateEncoding(encoding="latin-1", confidence=0.5),),
        safe_retry=False,
        recommended_action="inspect file",
    )

    payload = diagnostic.to_meta()

    assert payload["kind"] == "encoding"
    assert payload["safe_retry"] is False
    assert payload["candidates"][0]["encoding"] == "latin-1"
    assert payload["confidence"] == "medium"
    assert payload["recovery_applied"] is True
    assert payload["recommended_action"] == "inspect file"
