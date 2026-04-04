"""Repository-level OSS helpers.

Re-exports OSS upload/download primitives from the calculation adaptor.
"""

from __future__ import annotations

from evomaster.adaptors.calculation.oss_io import (
    download_oss_to_local,
    upload_file_to_oss,
)

__all__ = [
    "download_oss_to_local",
    "upload_file_to_oss",
]
