from __future__ import annotations

import json

from matmaster_bohrium_transfer.remote import main


def test_remote_cli_version_outputs_json(capsys) -> None:
    exit_code = main(["version", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["protocol_version"] == "1.0"
    assert "multipart_upload" in payload["capabilities"]
    assert captured.err == ""
