"""OSS I/O helpers for Bohrium-backed calculation flows."""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_oss2: object | None = None


def _get_oss2():
    global _oss2
    if _oss2 is None:
        try:
            import oss2
            from oss2.credentials import EnvironmentVariableCredentialsProvider

            _oss2 = (oss2, EnvironmentVariableCredentialsProvider)
        except ImportError as exc:
            raise ImportError(
                "Calculation OSS upload requires oss2. Install with: pip install oss2"
            ) from exc
    return _oss2


def _object_key_last_segment(name: str) -> str:
    seg = Path(name).name.strip()
    if not seg or seg in (".", ".."):
        return "uploaded_file"
    return seg.replace("\\", "_").replace("/", "_")


def upload_file_to_oss(
    local_path: Path,
    workspace_root: Path,
    *,
    oss_prefix: str = "evomaster/calculation",
    object_basename: str | None = None,
) -> str:
    path = Path(local_path)
    if not path.is_absolute():
        path = (workspace_root / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    oss2_module, cred_provider = _get_oss2()
    endpoint = os.environ.get("OSS_ENDPOINT")
    bucket_name = os.environ.get("OSS_BUCKET_NAME")
    if not endpoint or not bucket_name:
        raise RuntimeError(
            "Calculation OSS upload requires OSS_ENDPOINT and OSS_BUCKET_NAME in environment. "
            "Set them in .env at project root (run.py loads .env when starting). "
            "Also set OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET for upload."
        )

    auth = oss2_module.ProviderAuth(cred_provider())
    bucket = oss2_module.Bucket(auth, endpoint, bucket_name)
    raw_name = object_basename if object_basename is not None else path.name
    filename = _object_key_last_segment(raw_name)
    prefix = oss_prefix.strip().strip("/")
    oss_key = f"{prefix}/{uuid.uuid4().hex}/{filename}"
    with open(path, "rb") as f:
        bucket.put_object(oss_key, f.read())
    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    url = f"https://{bucket_name}.{host}/{oss_key}"
    logger.debug("Uploaded %s -> %s", path, url)
    return url


def _is_oss_or_http_url(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return lowered.startswith("https://") or lowered.startswith("http://")


def download_oss_to_local(
    oss_url: str,
    workspace_root: Path,
    dest_relative_path: str | None = None,
) -> Path:
    if not _is_oss_or_http_url(oss_url):
        raise ValueError(f"Not an OSS/HTTP URL: {oss_url}")
    workspace_root = Path(workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    if dest_relative_path:
        dest = (workspace_root / dest_relative_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(oss_url)
        path = unquote(parsed.path or "")
        name = re.sub(r"[^\w.\-]", "_", path.split("/")[-1] or "downloaded_file")
        if not name:
            name = "downloaded_file"
        dest = workspace_root / name

    req = Request(oss_url, headers={"User-Agent": "MatMaster-Calculation/1.0"})
    with urlopen(req, timeout=120) as resp:
        data = resp.read()
    dest.write_bytes(data)
    logger.debug("Downloaded %s -> %s", oss_url, dest)
    return dest
