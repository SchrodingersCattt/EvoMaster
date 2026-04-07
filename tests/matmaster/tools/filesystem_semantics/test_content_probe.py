from matmaster.tools.filesystem_semantics.content_probe import probe_content_bytes


def test_probe_detects_utf16_bom() -> None:
    result = probe_content_bytes(b"\xff\xfeh\x00i\x00\n\x00")
    assert result.kind == "definite_text"
    assert result.encoding == "utf-16"
    assert result.encoding_source == "bom"


def test_probe_detects_utf32_bom_before_utf16() -> None:
    result = probe_content_bytes(b"\xff\xfe\x00\x00h\x00\x00\x00")
    assert result.kind == "definite_text"
    assert result.encoding == "utf-32"


def test_probe_recovers_utf16_without_bom_from_nul_pattern() -> None:
    result = probe_content_bytes(b"h\x00i\x00\n\x00")
    assert result.kind == "recovered_text"
    assert result.encoding == "utf-16"


def test_probe_marks_binary_when_dense_control_bytes() -> None:
    result = probe_content_bytes(b"\x00\x01\x02\x03\x04\x05\x06\x07")
    assert result.kind == "binary_suspect"
    assert result.encoding is None


def test_probe_returns_candidate_text_for_utf8_failure() -> None:
    raw = "第一行\n第二行\n".encode("gb18030")
    result = probe_content_bytes(raw)
    assert result.kind == "candidate_text"
    assert result.diagnostic is not None
    assert result.diagnostic.candidates[0].encoding in {"gb18030", "utf-16"}
