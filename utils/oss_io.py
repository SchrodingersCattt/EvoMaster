"""Repository-level synchronous OSS helpers.

Expose the shared upload/download primitives for scripts outside ``src`` while
reusing the single implementation in ``src.dao.oss_io``.
"""

from __future__ import annotations

from src.dao.oss_io import (
    get_signed_url,
    upload_bytes_to_oss,
    upload_dir_to_oss,
    upload_file_to_oss,
    upload_file_to_oss_with_key,
)

__all__ = [
    "get_signed_url",
    "upload_bytes_to_oss",
    "upload_dir_to_oss",
    "upload_file_to_oss",
    "upload_file_to_oss_with_key",
]
