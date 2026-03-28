"""Unit tests for ``matmaster.eval_ingest_client``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from matmaster.eval_ingest_client import (
    EVAL_INGEST_API_PATH,
    EVAL_INGEST_URL,
    build_ingest_item,
    extract_total_tokens,
    post_eval_ingest,
    prompt_sha256,
)


def test_prompt_sha256_stable() -> None:
    h = prompt_sha256("hello")
    assert len(h) == 64
    assert h == prompt_sha256("hello")
    assert h != prompt_sha256("hello ")


def test_extract_total_tokens() -> None:
    assert extract_total_tokens(None) is None
    assert extract_total_tokens({}) is None
    assert extract_total_tokens({"total_tokens": 42}) == 42
    assert extract_total_tokens({"prompt_tokens": 10, "completion_tokens": 5}) == 15


def test_eval_ingest_url_matches_tools_server() -> None:
    from utils.env import MATMASTER_TOOLS_SERVER

    expected = f"{MATMASTER_TOOLS_SERVER.rstrip('/')}{EVAL_INGEST_API_PATH}"
    assert EVAL_INGEST_URL == expected


def test_build_ingest_item_minimal() -> None:
    item = build_ingest_item(
        question_id="Q1",
        prompt="p",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=0,
        summary={
            "status": "done",
            "reason": "natural",
            "num_turns": 3,
            "usage": {"total_tokens": 100},
        },
        duration_ms=5000,
    )
    assert item["question_id"] == "Q1"
    assert item["question_sha256"] == prompt_sha256("p")
    assert item["duration_ms"] == 5000
    assert item["tokens"] == 100
    assert item["num_turns"] == 3
    assert item["extra"]["task_id"] == "Q1_direct_r0"
    assert item["extra"]["reason"] == "natural"
    assert "model" not in item["extra"]
    assert "num_turns" not in item["extra"]


def test_build_ingest_item_model_top_level() -> None:
    item = build_ingest_item(
        question_id="Q1",
        prompt="p",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=0,
        summary={"model": "claude-sonnet-4", "num_turns": 1},
        duration_ms=None,
    )
    assert item["model"] == "claude-sonnet-4"
    assert item["num_turns"] == 1
    assert "model" not in item["extra"]
    assert "num_turns" not in item["extra"]


def test_build_ingest_item_parse_error_summary() -> None:
    item = build_ingest_item(
        question_id="Q1",
        prompt="x",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=1,
        summary={"parse_error": True, "error": "bad json"},
        duration_ms=None,
    )
    assert item["extra"]["parse_error"] is True
    assert item["extra"]["error"] == "bad json"
    assert "duration_ms" not in item


@patch("matmaster.eval_ingest_client.httpx.Client")
def test_post_eval_ingest_success(mock_client_cls: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": 0, "msg": "success", "data": {}}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    ok, msg = post_eval_ingest(
        "http://example/ingest",
        {"run_id": "r1", "items": [{"question_id": "q"}]},
    )
    assert ok
    assert "success" in msg
    call_kw = mock_client.post.call_args
    assert call_kw[0][0] == "http://example/ingest"
    assert call_kw[1]["headers"] == {"Content-Type": "application/json"}


@patch("matmaster.eval_ingest_client.httpx.Client")
def test_post_eval_ingest_business_error(mock_client_cls: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": -1, "msg": "db fail"}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    ok, msg = post_eval_ingest(
        "http://x", {"run_id": "r", "items": [{"question_id": "q"}]}
    )
    assert not ok
    assert "db fail" in msg
