"""Unit tests for the HTTP POST helpers in ``evaluation.eval_ingest_client``."""

from unittest.mock import MagicMock, patch

from evaluation.eval_ingest_client import post_eval_ingest, post_question_catalog_sync


@patch("evaluation.eval_ingest_client.httpx.Client")
@patch(
    "evaluation.eval_ingest_client.utils.env.MATMASTER_TOOLS_EVALUATION_BEARER",
    None,
)
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
@patch(
    "evaluation.eval_ingest_client.utils.env.MATMASTER_TOOLS_EVALUATION_BEARER",
    "svc-token",
)
def test_post_eval_ingest_sends_tools_server_auth_headers(
    mock_client_cls: MagicMock,
) -> None:
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
    call_kw = mock_client.post.call_args
    assert call_kw[1]["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer svc-token",
    }


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
    assert sent["items"][0] == {
        "question_id": "Q1",
        "question_text": "题干一",
        "priority": "",
    }
    assert sent["items"][1] == {
        "question_id": "Q2",
        "question_text": "题干二",
        "priority": "",
    }


@patch("evaluation.eval_ingest_client.httpx.Client")
def test_post_question_catalog_sync_sends_priority_p0(
    mock_client_cls: MagicMock,
) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": 0,
        "msg": "success",
        "data": {"active_count": 1, "inactive_count": 0},
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    ok, msg = post_question_catalog_sync(
        "http://example/qcat/sync",
        [{"question_id": "Q1", "question_text": "题干", "priority": "P0"}],
    )
    assert ok
    assert "active_count=1" in msg
    sent = mock_client.post.call_args[1]["json"]
    assert sent["items"][0]["priority"] == "P0"


@patch("evaluation.eval_ingest_client.httpx.Client")
def test_post_question_catalog_sync_rejects_invalid_priority(
    mock_client_cls: MagicMock,
) -> None:
    ok, err = post_question_catalog_sync(
        "http://x",
        [{"question_id": "Q1", "question_text": "题干", "priority": "high"}],
    )
    assert not ok
    assert "invalid priority" in err
    mock_client_cls.assert_not_called()


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
@patch(
    "evaluation.eval_ingest_client.utils.env.MATMASTER_TOOLS_EVALUATION_BEARER",
    None,
)
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
