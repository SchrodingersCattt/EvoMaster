from __future__ import annotations

import json
import subprocess


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
    assert payload["protocol_version"] == "1.0"
    assert payload["wheel_path"].endswith(".whl")
    assert payload["sha256_path"].endswith(".sha256")
