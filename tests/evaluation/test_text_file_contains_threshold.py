from evaluation.validators.text_file import check_text_file_contains_all


def test_text_file_contains_all_defaults_to_all_tokens(tmp_path):
    out = tmp_path / "answer.json"
    out.write_text('{"name": "A", "smiles": "C=C"}', encoding="utf-8")

    ok, msg = check_text_file_contains_all(
        tmp_path,
        filename="answer.json",
        tokens=["name", "smiles", "missing"],
    )

    assert not ok
    assert "2/3 tokens found" in msg


def test_text_file_contains_all_supports_min_ratio(tmp_path):
    out = tmp_path / "answer.json"
    out.write_text('{"name": "A", "smiles": "C=C"}', encoding="utf-8")

    ok, msg = check_text_file_contains_all(
        tmp_path,
        filename="answer.json",
        tokens=["name", "smiles", "missing"],
        min_ratio=0.6,
    )

    assert ok
    assert "2/3 tokens found" in msg


def test_text_file_contains_all_supports_minimum_threshold(tmp_path):
    out = tmp_path / "answer.json"
    out.write_text('{"name": "A", "smiles": "C=C"}', encoding="utf-8")

    ok, msg = check_text_file_contains_all(
        tmp_path,
        filename="answer.json",
        tokens=["name", "smiles", "missing"],
        minimum_threshold=2,
    )

    assert ok
    assert "2/3 tokens found" in msg
