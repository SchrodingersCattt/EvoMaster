from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from matmaster_bohrium_transfer.version import (
    CAPABILITIES,
    PACKAGE_NAME,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "packages" / "bohrium-transfer"
DIST_DIR = ROOT / "dist"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle(*, dry_run: bool = False) -> dict[str, object]:
    wheel_path = DIST_DIR / "matmaster_bohrium_transfer-0.1.0-py3-none-any.whl"
    sha_path = wheel_path.with_suffix(wheel_path.suffix + ".sha256")
    if not dry_run:
        subprocess.run(
            ["uv", "build", "--package", PACKAGE_NAME, "--wheel"],
            cwd=ROOT,
            check=True,
        )
        wheels = sorted(DIST_DIR.glob("matmaster_bohrium_transfer-*.whl"))
        if not wheels:
            raise RuntimeError(f"no wheel found in {DIST_DIR}")
        wheel_path = wheels[-1]
        sha_path = wheel_path.with_suffix(wheel_path.suffix + ".sha256")
        sha_path.write_text(f"{_sha256(wheel_path)}  {wheel_path.name}\n")
    return {
        "package": PACKAGE_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "capabilities": list(CAPABILITIES),
        "wheel_path": str(wheel_path),
        "sha256_path": str(sha_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_bundle(dry_run=args.dry_run), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
