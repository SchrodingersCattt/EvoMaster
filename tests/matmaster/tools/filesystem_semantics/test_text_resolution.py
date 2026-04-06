from matmaster.tools.filesystem_semantics.text_resolution import resolve_text_bytes


def test_resolve_text_bytes_recovers_utf16() -> None:
    raw = b"\xff\xfeh\x00i\x00\n\x00"
    result = resolve_text_bytes(raw, explicit_encoding=None)
    assert result.status == "success"
    assert result.semantic_kind == "definite_text"
    assert result.text == "hi\n"
    assert result.encoding == "utf-16"


def test_resolve_text_bytes_returns_agent_diagnostic_for_candidates() -> None:
    raw = "第一行\n第二行\n".encode("gb18030")
    result = resolve_text_bytes(raw, explicit_encoding=None)
    assert result.status == "error"
    assert result.semantic_kind == "candidate_text"
    assert result.diagnostic is not None
    assert result.diagnostic.kind == "candidate_text"
    assert result.diagnostic.safe_retry is True
