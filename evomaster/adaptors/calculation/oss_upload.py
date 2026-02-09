"""Upload local files to Aliyun OSS for calculation MCP tools; download OSS results to workspace.

Upload uses oss2 when available; env: OSS_ENDPOINT, OSS_BUCKET_NAME, credentials
(via EnvironmentVariableCredentialsProvider).
Download uses stdlib urllib so no extra deps for result fetching.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request

logger = logging.getLogger(__name__)

_oss2: Optional[object] = None


def _get_oss2():
    global _oss2
    if _oss2 is None:
        try:
            import oss2
            from oss2.credentials import EnvironmentVariableCredentialsProvider
            _oss2 = (oss2, EnvironmentVariableCredentialsProvider)
        except ImportError:
            raise ImportError(
                "Calculation OSS upload requires oss2. Install with: pip install oss2"
            )
    return _oss2


def upload_file_to_oss(
    local_path: Path,
    workspace_root: Path,
    *,
    oss_prefix: str = "evomaster/calculation",
) -> str:
    """Upload a local file to OSS and return its public URL."""
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
    filename = path.name
    oss_key = f"{oss_prefix}/{int(time.time())}_{filename}"
    with open(path, "rb") as f:
        bucket.put_object(oss_key, f.read())
    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    url = f"https://{bucket_name}.{host}/{oss_key}"
    logger.debug("Uploaded %s -> %s", path, url)
    return url


def _is_oss_or_http_url(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    v = value.strip().lower()
    return v.startswith("https://") or v.startswith("http://")


def download_oss_to_local(
    oss_url: str,
    workspace_root: Path,
    dest_relative_path: Optional[str] = None,
) -> Path:
    """Download a file from OSS (or any HTTP(S) URL) to workspace. Returns path to saved file."""
    if not _is_oss_or_http_url(oss_url):
        raise ValueError(f"Not an OSS/HTTP URL: {oss_url}")
    workspace_root = Path(workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    if dest_relative_path:
        dest = (workspace_root / dest_relative_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        # Infer filename from URL path (last segment)
        from urllib.parse import urlparse, unquote
        parsed = urlparse(oss_url)
        path = unquote(parsed.path or "")
        name = path.split("/")[-1] or "downloaded_file"
        # Sanitize
        name = re.sub(r"[^\w.\-]", "_", name)
        if not name:
            name = "downloaded_file"
        dest = workspace_root / name

    req = Request(oss_url, headers={"User-Agent": "EvoMaster-Calculation/1.0"})
    with urlopen(req, timeout=120) as resp:
        data = resp.read()
    dest.write_bytes(data)
    logger.debug("Downloaded %s -> %s", oss_url, dest)
    return dest
