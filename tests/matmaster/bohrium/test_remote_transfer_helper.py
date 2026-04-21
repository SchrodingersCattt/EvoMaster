from __future__ import annotations

import json
from pathlib import Path

import pytest

from matmaster.bohrium import remote_transfer_helper as helper


def test_load_payload_unlinks_file_and_rejects_schema_mismatch(tmp_path: Path) -> None:
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps({"schema_version": "v0"}), encoding="utf-8")

    with pytest.raises(helper.HelperFailure, match="schema_version"):
        helper.load_payload(payload_file)

    assert not payload_file.exists()


def test_zip_and_extract_preserve_non_ascii_file_names(tmp_path: Path) -> None:
    input_dir = tmp_path / "输入"
    input_dir.mkdir()
    (input_dir / "结构.log").write_text("完成\n", encoding="utf-8")
    archive = tmp_path / "input.zip"
    extract_dir = tmp_path / "extract"

    helper.zip_directory(input_dir, archive)
    files = helper.extract_zip(archive, extract_dir)

    assert files == ["结构.log"]
    assert (extract_dir / "结构.log").read_text(encoding="utf-8") == "完成\n"


def test_run_upload_submit_returns_oss_key_without_download_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "INPUT").write_text("data", encoding="utf-8")
    upload_calls: list[tuple[str, str, str]] = []

    class FakeTiefblue:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def upload_From_file_multi_part(
            self,
            *,
            object_key: str,
            file_path: str,
            token: str,
            progress_bar: bool,
        ) -> None:
            upload_calls.append((object_key, file_path, token))
            assert progress_bar is False

    monkeypatch.setattr(helper, "load_tiefblue_client", lambda: FakeTiefblue)

    result = helper.run_upload_submit(
        {
            "schema_version": helper.SCHEMA_VERSION,
            "input_dir": str(input_dir),
            "store_host": "https://store.example.com",
            "store_path": "sandbox/jobs/run-1",
            "token": "upload-token",
            "object_name": "input.zip",
        }
    )

    assert result["ok"] is True
    assert result["oss_key"] == "sandbox/jobs/run-1/input.zip"
    assert "download_url" not in result
    assert upload_calls[0][0] == "sandbox/jobs/run-1/input.zip"
    assert upload_calls[0][2] == "upload-token"


def test_publish_result_dir_rejects_existing_lock(tmp_path: Path) -> None:
    staging = tmp_path / "results.tmp.1"
    staging.mkdir()
    (staging / "log").write_text("done\n", encoding="utf-8")
    result_dir = tmp_path / "results"
    (tmp_path / "results.lock").mkdir()

    with pytest.raises(helper.HelperFailure, match="concurrent"):
        helper.publish_result_dir(staging, result_dir)

    assert staging.exists()


def test_publish_result_dir_replaces_existing_directory(tmp_path: Path) -> None:
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "old.log").write_text("old\n", encoding="utf-8")
    staging = tmp_path / "results.tmp.1"
    staging.mkdir()
    (staging / "log").write_text("new\n", encoding="utf-8")

    helper.publish_result_dir(staging, result_dir)

    assert not staging.exists()
    assert not (result_dir / "old.log").exists()
    assert (result_dir / "log").read_text(encoding="utf-8") == "new\n"
    assert not list(tmp_path.glob("results.bak.*"))
    assert not (tmp_path / "results.lock").exists()


def test_redact_secrets_masks_token_like_values() -> None:
    text = "failed https://store/api/download/x?token=secret-token&access_key=ak"

    redacted = helper.redact_secrets(text)

    assert "secret-token" not in redacted
    assert "access_key=ak" not in redacted
    assert "token=<redacted>" in redacted
    assert "access_key=<redacted>" in redacted
