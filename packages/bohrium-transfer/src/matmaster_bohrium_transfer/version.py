from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "matmaster-bohrium-transfer"
SCHEMA_VERSION = "v2"
PROTOCOL_VERSION = "1.1"
GIT_COMMIT = "unknown"

CAPABILITIES = (
    "multipart_upload",
    "upload_concurrency",
    "manifest_resume_v2",
    "transfer_id_path_isolation",
    "strict_business_code",
    "single_retry_budget",
    "streaming_part_upload",
    "part_content_md5",
    "range_resume",
    "range_download_concurrency",
    "download_sha256",
    "download_zip_verify",
    "download_hash_validation",
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
        "build_id": f"{PACKAGE_VERSION}+{GIT_COMMIT}",
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "git_commit": GIT_COMMIT,
        "capabilities": list(CAPABILITIES),
        "python_version": platform.python_version(),
    }
