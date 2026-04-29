from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "matmaster-bohrium-transfer"
SCHEMA_VERSION = "v1"
PROTOCOL_VERSION = "1.0"
GIT_COMMIT = "unknown"

CAPABILITIES = (
    "multipart_upload",
    "upload_concurrency",
    "manifest_resume",
    "range_resume",
    "range_download_concurrency",
    "sandbox_iterate",
    "zip_stored",
    "secure_payload_file",
    "redacted_errors",
)


def _package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.1.0+local"


PACKAGE_VERSION = _package_version()


def version_payload() -> dict[str, object]:
    return {
        "ok": True,
        "package": PACKAGE_NAME,
        "package_version": PACKAGE_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "git_commit": GIT_COMMIT,
        "capabilities": list(CAPABILITIES),
        "python_version": platform.python_version(),
    }
