from __future__ import annotations

import json
import subprocess

from matmaster_bohrium_transfer.version import PROTOCOL_VERSION, SCHEMA_VERSION


def test_build_bohrium_transfer_bundle_dry_run_outputs_metadata() -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/build_bohrium_transfer_bundle.py",
            "--dry-run",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["package"] == "matmaster-bohrium-transfer"
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "part_content_md5" in payload["capabilities"]
    assert payload["wheel_path"].endswith(".whl")
    assert payload["sha256_path"].endswith(".sha256")
