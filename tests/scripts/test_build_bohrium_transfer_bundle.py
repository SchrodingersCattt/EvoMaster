from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    from matmaster_bohrium_transfer.version import PROTOCOL_VERSION, SCHEMA_VERSION
except ModuleNotFoundError:
    _ROOT = Path(__file__).resolve().parents[2]
    _TRANSFER_SRC = _ROOT / "packages" / "bohrium-transfer" / "src"
    sys.path.insert(0, str(_TRANSFER_SRC))
    from matmaster_bohrium_transfer.version import PROTOCOL_VERSION, SCHEMA_VERSION


def test_build_bohrium_transfer_bundle_dry_run_outputs_metadata() -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            "python",
            "scripts/build_bohrium_transfer_bundle.py",
            "--dry-run",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1])
    assert payload["package"] == "matmaster-bohrium-transfer"
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "part_content_md5" in payload["capabilities"]
    assert "storehost_tiefblue_part_contract" in payload["capabilities"]
    assert payload["wheel_path"].endswith(".whl")
    assert payload["sha256_path"].endswith(".sha256")
