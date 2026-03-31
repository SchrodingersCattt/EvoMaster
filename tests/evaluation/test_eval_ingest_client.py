"""Unit tests for ``evaluation.eval_ingest_client``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from evaluation.eval_ingest_client import (
    EVAL_INGEST_API_PATH,
    EVAL_INGEST_URL,
    build_ingest_item,
    clip_ingest_text_field,
    eval_run_zip_should_skip_arcname,
    extract_total_tokens,
    load_devshell_events_timeline,
    normalize_baseline_channel,
    normalize_pending_item_for_submission,
    post_eval_ingest,
    post_question_catalog_sync,
    prompt_sha256,
    score_for_eval_ingest,
)


def test_normalize_baseline_channel() -> None:
    assert normalize_baseline_channel(None) == "claude_code"
    assert normalize_baseline_channel("cursor") == "cursor"
    assert normalize_baseline_channel("  claude_code  ") == "claude_code"


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


def test_score_for_eval_ingest_explicit() -> None:
    assert score_for_eval_ingest({"score": 0.73}, 1) == 0.73
    assert score_for_eval_ingest({"eval_score": 88}, 0) == 88.0


def test_score_for_eval_ingest_proxy() -> None:
    assert score_for_eval_ingest({"status": "done"}, 0) == 100.0
    assert score_for_eval_ingest({"status": "done"}, 1) == 0.0


def test_score_for_eval_ingest_parse_error() -> None:
    assert score_for_eval_ingest({"parse_error": True}, 0) == 0.0


def test_eval_run_zip_should_skip_arcname() -> None:
    assert eval_run_zip_should_skip_arcname("workspaces/t/__pycache__/x.pyc")
    assert eval_run_zip_should_skip_arcname("foo.pyc")
    assert eval_run_zip_should_skip_arcname(".DS_Store")
    assert not eval_run_zip_should_skip_arcname("workspaces/t/_devshell_summary.json")
    assert not eval_run_zip_should_skip_arcname("logs/t/events_1.jsonl")


def test_eval_ingest_url_matches_tools_server() -> None:
    from utils.env import MATMASTER_TOOLS_SERVER

    expected = f"{MATMASTER_TOOLS_SERVER.rstrip('/')}{EVAL_INGEST_API_PATH}"
    assert EVAL_INGEST_URL == expected


def test_build_ingest_item_minimal() -> None:
    item = build_ingest_item(
        question_id="Q1",
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
    assert "question_text" not in item
    assert item["duration_ms"] == 5000
    assert item["tokens"] == 100
    assert item["score"] == 100.0
    assert item["num_turns"] == 3
    assert item["extra"]["task_id"] == "Q1_direct_r0"
    assert item["extra"]["reason"] == "natural"
    assert "model" not in item["extra"]
    assert "num_turns" not in item["extra"]


def test_build_ingest_item_model_top_level() -> None:
    item = build_ingest_item(
        question_id="Q1",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=0,
        summary={"model": "claude-sonnet-4", "num_turns": 1},
        duration_ms=None,
    )
    assert item["model"] == "claude-sonnet-4"
    assert item["num_turns"] == 1
    assert item["score"] == 100.0
    assert "model" not in item["extra"]
    assert "num_turns" not in item["extra"]


def test_build_ingest_item_explicit_score_overrides_exit() -> None:
    item = build_ingest_item(
        question_id="Q1",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=1,
        summary={"score": 65.0},
        duration_ms=None,
    )
    assert item["score"] == 65.0


def test_build_ingest_item_result_oss_url() -> None:
    item = build_ingest_item(
        question_id="Q1",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=0,
        summary={},
        duration_ms=None,
        result_oss_url="https://bucket.oss.example.com/prefix/u/f.zip",
    )
    assert item["result_oss_url"].startswith("https://")


def test_build_ingest_item_eval_tooling_in_extra() -> None:
    tooling = {"schema": "matmaster_eval_tooling_v1", "skill_names": ["x"]}
    item = build_ingest_item(
        question_id="Q1",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=0,
        summary={"status": "done"},
        duration_ms=1,
        eval_tooling=tooling,
    )
    assert item["extra"]["eval_tooling"] == tooling


def test_build_ingest_item_events_timeline_in_extra() -> None:
    tl = ["read_file", "execute_bash", "run_result"]
    item = build_ingest_item(
        question_id="Q1",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=0,
        summary={"status": "done"},
        duration_ms=1,
        events_timeline=tl,
    )
    assert item["extra"]["events_timeline"] == tl


def test_load_devshell_events_timeline_skips_tool_result(tmp_path: Path) -> None:
    log_d = tmp_path / "logs" / "SC_struct_006_direct_r0"
    log_d.mkdir(parents=True)
    lines = [
        '{"type": "tool_call", "tool": "read_file", "call_id": "a", "args": {}}',
        '{"type": "tool_result", "tool": "read_file", "call_id": "a", "content": "x"}',
        '{"type": "tool_call", "tool": "execute_bash", "call_id": "b", "args": {}}',
        '{"type": "tool_result", "tool": "execute_bash", "call_id": "b", "content": "y"}',
        '{"type": "tool_call", "tool": "write_file", "call_id": "c", "args": {}}',
        '{"type": "tool_result", "tool": "write_file", "call_id": "c", "content": "z"}',
        '{"type": "tool_call", "tool": "execute_bash", "call_id": "d", "args": {}}',
        '{"type": "tool_result", "tool": "execute_bash", "call_id": "d", "content": "w"}',
        '{"type": "run_result", "status": "completed", "reason": "natural"}',
    ]
    (log_d / "events_20260330_203343.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    assert load_devshell_events_timeline(log_d) == [
        "read_file",
        "execute_bash",
        "write_file",
        "execute_bash",
        "run_result",
    ]


def test_load_devshell_events_timeline_with_response(tmp_path: Path) -> None:
    log_d = tmp_path / "logs" / "t1"
    log_d.mkdir(parents=True)
    (log_d / "events_1.jsonl").write_text(
        '{"type": "response", "content": "hi"}\n'
        '{"type": "tool_call", "tool": "read_file", "call_id": "a", "args": {}}\n'
        '{"type": "run_result", "status": "completed", "reason": "natural"}\n',
        encoding="utf-8",
    )
    assert load_devshell_events_timeline(log_d) == [
        "response",
        "read_file",
        "run_result",
    ]


def test_load_devshell_events_timeline_missing_returns_none(tmp_path: Path) -> None:
    assert load_devshell_events_timeline(tmp_path / "nope") is None
    empty = tmp_path / "e"
    empty.mkdir()
    assert load_devshell_events_timeline(empty) is None


def test_clip_ingest_text_field() -> None:
    assert clip_ingest_text_field(None) is None
    assert clip_ingest_text_field("  \n") is None
    assert clip_ingest_text_field(" ok ") == "ok"
    assert clip_ingest_text_field("x" * 5, max_len=3) == "xxx"


def test_normalize_pending_item_for_submission() -> None:
    out, err = normalize_pending_item_for_submission(
        {
            "question_id": "Q1",
            "score": 80,
            "score_reason": "  依据 checklist ",
            "suggestion": "",
        }
    )
    assert err is None
    assert out is not None
    assert out["score"] == 80.0
    assert out["score_reason"] == "依据 checklist"
    assert "suggestion" not in out


def test_normalize_pending_item_requires_score() -> None:
    out, err = normalize_pending_item_for_submission({"question_id": "Q1"})
    assert out is None
    assert err is not None
    assert "score" in err


def test_normalize_pending_item_rejects_bad_score_reason_type() -> None:
    out, err = normalize_pending_item_for_submission(
        {"question_id": "Q1", "score": 1, "score_reason": 123}
    )
    assert out is None
    assert err is not None


def test_build_ingest_item_parse_error_summary() -> None:
    item = build_ingest_item(
        question_id="Q1",
        task_id="Q1_direct_r0",
        mode="direct",
        repeat_idx=0,
        devshell_exit_code=1,
        summary={"parse_error": True, "error": "bad json"},
        duration_ms=None,
    )
    assert item["extra"]["parse_error"] is True
    assert item["extra"]["error"] == "bad json"
    assert item["score"] == 0.0
    assert "duration_ms" not in item


@patch("evaluation.eval_ingest_client.httpx.Client")
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


@patch("evaluation.eval_ingest_client.httpx.Client")
def test_post_question_catalog_sync_success(mock_client_cls: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": 0,
        "msg": "success",
        "data": {"active_count": 2, "inactive_count": 0},
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    ok, msg = post_question_catalog_sync(
        "http://example/qcat/sync",
        [
            {"question_id": "Q1", "question_text": "题干一"},
            {"question_id": "Q2", "question_text": "题干二"},
        ],
    )
    assert ok
    assert "active_count=2" in msg
    call_kw = mock_client.post.call_args
    assert call_kw[0][0] == "http://example/qcat/sync"
    sent = call_kw[1]["json"]
    assert sent["items"][0] == {"question_id": "Q1", "question_text": "题干一"}


@patch("evaluation.eval_ingest_client.httpx.Client")
def test_post_question_catalog_sync_rejects_missing_text(
    mock_client_cls: MagicMock,
) -> None:
    ok, err = post_question_catalog_sync(
        "http://x",
        [{"question_id": "Q1"}],
    )
    assert not ok
    assert "question_text" in err
    mock_client_cls.assert_not_called()


@patch("evaluation.eval_ingest_client.httpx.Client")
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
