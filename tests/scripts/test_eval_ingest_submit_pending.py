"""Tests for ``scripts/eval_ingest_submit_pending.py``."""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_submit_module():
    script = REPO_ROOT / "scripts" / "eval_ingest_submit_pending.py"
    spec = importlib.util.spec_from_file_location("_eval_ingest_submit_pending", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_submit_pending_main_posts_score_from_cli_when_missing_in_file(
    tmp_path: Path,
) -> None:
    envelope = {
        "schema": "matmaster_eval_pending_ingest_v1",
        "ingest_url": "http://example/api/v1/evaluation/ingest",
        "run_id": "run-uuid",
        "git_commit": "deadbeef",
        "item": {
            "question_id": "Q1",
            "question_sha256": "ab" * 32,
            "extra": {},
        },
    }
    p = tmp_path / "SC_x_direct_r0.json"
    p.write_text(json.dumps(envelope), encoding="utf-8")

    mod = _load_submit_module()
    with patch("matmaster.eval_ingest_client.post_eval_ingest") as post:
        post.return_value = (True, "ok")
        old_argv = sys.argv
        try:
            sys.argv = [
                "eval_ingest_submit_pending.py",
                "--pending",
                str(p),
                "--score",
                "71.5",
            ]
            with redirect_stderr(StringIO()):
                rc = mod.main()
        finally:
            sys.argv = old_argv

    assert rc == 0
    post.assert_called_once()
    url, body = post.call_args[0][0], post.call_args[0][1]
    assert url == envelope["ingest_url"]
    assert body["run_id"] == "run-uuid"
    assert body["git_commit"] == "deadbeef"
    assert len(body["items"]) == 1
    assert body["items"][0]["question_id"] == "Q1"
    assert body["items"][0]["score"] == 71.5


def test_submit_pending_cli_reason_and_suggestion_used_for_post(tmp_path: Path) -> None:
    envelope = {
        "schema": "matmaster_eval_pending_ingest_v1",
        "ingest_url": "http://example/api/v1/evaluation/ingest",
        "run_id": "run-uuid",
        "item": {
            "question_id": "Q1",
            "question_sha256": "ab" * 32,
            "extra": {},
            "score_reason": "旧原因",
            "suggestion": "旧建议",
        },
    }
    p = tmp_path / "task.json"
    p.write_text(json.dumps(envelope), encoding="utf-8")

    mod = _load_submit_module()
    with patch("matmaster.eval_ingest_client.post_eval_ingest") as post:
        post.return_value = (True, "ok")
        old_argv = sys.argv
        try:
            sys.argv = [
                "eval_ingest_submit_pending.py",
                "--pending",
                str(p),
                "--score",
                "80",
                "--score-reason",
                "依据 YAML checklist",
                "--suggestion",
                "加强结构验证",
            ]
            with redirect_stderr(StringIO()):
                rc = mod.main()
        finally:
            sys.argv = old_argv

    assert rc == 0
    body = post.call_args[0][1]
    assert body["items"][0]["score"] == 80.0
    assert body["items"][0]["score_reason"] == "依据 YAML checklist"
    assert body["items"][0]["suggestion"] == "加强结构验证"


def test_submit_pending_ignores_score_fields_already_in_file(tmp_path: Path) -> None:
    envelope = {
        "schema": "matmaster_eval_pending_ingest_v1",
        "ingest_url": "http://example/api/v1/evaluation/ingest",
        "run_id": "run-uuid",
        "item": {
            "question_id": "Q1",
            "question_sha256": "ab" * 32,
            "extra": {},
            "score": 11,
            "score_reason": "旧原因",
            "suggestion": "旧建议",
        },
    }
    p = tmp_path / "task.json"
    p.write_text(json.dumps(envelope), encoding="utf-8")

    mod = _load_submit_module()
    with patch("matmaster.eval_ingest_client.post_eval_ingest") as post:
        post.return_value = (True, "ok")
        old_argv = sys.argv
        try:
            sys.argv = [
                "eval_ingest_submit_pending.py",
                "--pending",
                str(p),
                "--score",
                "80",
            ]
            with redirect_stderr(StringIO()):
                rc = mod.main()
        finally:
            sys.argv = old_argv

    assert rc == 0
    body = post.call_args[0][1]
    assert body["items"][0]["score"] == 80.0
    assert "score_reason" not in body["items"][0]
    assert "suggestion" not in body["items"][0]


def test_submit_pending_requires_score_cli_arg(tmp_path: Path) -> None:
    envelope = {
        "ingest_url": "http://example/api/v1/evaluation/ingest",
        "run_id": "r",
        "item": {"question_id": "Q1", "question_sha256": "ab" * 32, "extra": {}},
    }
    p = tmp_path / "task.json"
    p.write_text(json.dumps(envelope), encoding="utf-8")

    mod = _load_submit_module()
    old_argv = sys.argv
    try:
        sys.argv = [
            "eval_ingest_submit_pending.py",
            "--pending",
            str(p),
        ]
        buf = StringIO()
        try:
            with redirect_stderr(buf):
                mod.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse SystemExit")
    finally:
        sys.argv = old_argv

    assert "--score" in buf.getvalue()
