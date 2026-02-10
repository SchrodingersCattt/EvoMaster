from __future__ import annotations

import asyncio
import base64
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import oss2
from oss2.credentials import EnvironmentVariableCredentialsProvider
from pydantic import BaseModel, HttpUrl


class DownloadUrlToTempFileRequest(BaseModel):
    url: HttpUrl
    temp_dir_path: str = './tmp'
    filename: str | None = None
    overwrite: bool = False


class DownloadUrlToTempFileResponse(BaseModel):
    local_path: str


def get_filename_from_url(file_url: str) -> str:
    """Extract a safe filename from URL path; fallback to a random name."""
    parsed = urlparse(file_url)
    candidate = os.path.basename(parsed.path or '') or ''
    candidate = candidate.strip()
    if not candidate:
        return f"download_{uuid.uuid4().hex}"
    return candidate


def _download_url_to_path(
    url: str, target_path: Path, timeout_seconds: float = 60.0
) -> None:
    """Download a URL to a local path (sync)."""
    request = Request(url, headers={'User-Agent': 'matmaster-tools-server/1.0'})
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        with target_path.open('wb') as f:
            shutil.copyfileobj(response, f, length=8192)


async def download_url_to_temp_file(
    req: DownloadUrlToTempFileRequest,
) -> DownloadUrlToTempFileResponse:
    """Download a URL to a local temp directory and return the saved path."""
    temp_dir = Path(req.temp_dir_path)
    temp_dir.mkdir(parents=True, exist_ok=True)

    filename = (req.filename or get_filename_from_url(str(req.url))).strip()
    if not filename:
        filename = f"download_{uuid.uuid4().hex}"

    target_path = temp_dir / filename
    if target_path.exists() and not req.overwrite:
        suffix = target_path.suffix
        stem = target_path.stem or 'download'
        target_path = temp_dir / f"{stem}_{uuid.uuid4().hex}{suffix}"

    await asyncio.to_thread(_download_url_to_path, str(req.url), target_path, 60.0)

    return DownloadUrlToTempFileResponse(local_path=str(target_path))


@asynccontextmanager
async def temp_dir(path: str = './tmp'):
    """Async context manager for creating/cleaning up temporary directories."""
    temp_path = Path(path)
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_path
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def bytes_to_base64(data: bytes) -> str:
    """Convert bytes to base64 string."""
    return base64.b64encode(data).decode('utf-8')


async def upload_to_oss_wrapper(
    b64_data: str,
    oss_path: str,
    filename: str,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Upload base64 data to OSS and return mapping from filename to URL or error string."""

    def _sync_upload_base64_to_oss(
        data: str, target_path: str, upload_headers: dict[str, str] | None
    ) -> str:
        auth = oss2.ProviderAuth(EnvironmentVariableCredentialsProvider())
        endpoint = os.environ['OSS_ENDPOINT']
        bucket_name = os.environ['OSS_BUCKET_NAME']
        bucket = oss2.Bucket(auth, endpoint, bucket_name)
        bucket.put_object(target_path, base64.b64decode(data), headers=upload_headers)
        return f"https://{bucket_name}.oss-cn-zhangjiakou.aliyuncs.com/{target_path}"

    result = await asyncio.to_thread(
        _sync_upload_base64_to_oss, b64_data, oss_path, headers
    )
    return {filename: result}


@dataclass(frozen=True, slots=True)
class PdfUploadResult:
    """Result for uploading a PDF file to OSS."""

    oss_url: str
    oss_path: str
    filename: str


async def file_to_base64(file_path: Path) -> tuple[Path, str]:
    """Read file and convert to base64 string."""
    content = await asyncio.to_thread(file_path.read_bytes)
    return file_path, bytes_to_base64(content)


async def upload_file_to_oss(
    file_path: Path,
    *,
    session_id: str | None = None,
    oss_prefix: str = 'reports',
    filename: str | None = None,
) -> PdfUploadResult:
    """Upload a file to OSS and return its URL and path."""
    _, b64_data = await file_to_base64(file_path)
    # Use provided filename or fallback to file_path.name
    final_filename = filename or file_path.name
    oss_path = f"{oss_prefix}/{final_filename}"

    # Set HTTP headers to ensure file can be downloaded
    # Content-Disposition: attachment forces download instead of inline display
    headers = {
        'Content-Disposition': f'attachment; filename="{final_filename}"',
        'Content-Type': 'application/pdf',
    }

    oss_result = await upload_to_oss_wrapper(
        b64_data, oss_path, final_filename, headers=headers
    )
    oss_url = list(oss_result.values())[0]
    return PdfUploadResult(oss_url=oss_url, oss_path=oss_path, filename=final_filename)
