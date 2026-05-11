from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import matmaster_bohrium_transfer.remote as remote_module
from matmaster_bohrium_transfer.remote import main


def test_remote_cli_version_outputs_json(capsys) -> None:
    exit_code = main(["version", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["protocol_version"] == "1.1"
    assert "multipart_upload" in payload["capabilities"]
    assert "part_content_md5" in payload["capabilities"]
    assert captured.err == ""


def test_upload_submit_uses_transfer_id_isolated_archive_paths(
    monkeypatch,
) -> None:
    archive_paths: list[Path] = []

    def fake_create_zip_store(input_dir, archive_path):
        del input_dir
        path = Path(archive_path)
        archive_paths.append(path)
        return SimpleNamespace(archive_path=path)

    def fake_upload_file_multipart(**kwargs):
        del kwargs
        return {"bytes_total": 3, "parts_total": 1}

    monkeypatch.setattr(remote_module, "create_zip_store", fake_create_zip_store)
    monkeypatch.setattr(
        remote_module, "upload_file_multipart", fake_upload_file_multipart
    )

    for transfer_id in ("t-a", "t-b"):
        remote_module._upload_submit(
            {
                "transfer_id": transfer_id,
                "input_dir": "/share/input",
                "store_host": "https://store.example",
                "store_path": "sandbox/jobs/run-1/",
                "token": "token-1",
                "object_name": "input.zip",
            }
        )

    assert [str(path) for path in archive_paths] == [
        "/share/.matmaster/transfers/t-a/archive/input.zip",
        "/share/.matmaster/transfers/t-b/archive/input.zip",
    ]
