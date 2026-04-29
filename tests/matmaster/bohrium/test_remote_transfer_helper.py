from __future__ import annotations

import json

from matmaster.bohrium import remote_transfer_helper as helper


def test_legacy_remote_transfer_helper_reports_removal(capsys) -> None:
    exit_code = helper.main([])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["stage"] == "legacy_helper_removed"
    assert "matmaster_bohrium_transfer" in payload["safe_message"]
