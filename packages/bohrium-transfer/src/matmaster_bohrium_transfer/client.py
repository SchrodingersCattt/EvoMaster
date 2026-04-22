from __future__ import annotations

import base64
import json
from typing import Any

import requests

from .errors import StorageCompleteError, StorageInitError, StoragePartUploadError


def encode_storage_param(parameter: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(parameter).encode()).decode()


def decode_storage_param(encoded: str) -> dict[str, Any]:
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


class StoreHostClient:
    def __init__(self, store_host: str, token: str, *, session=None) -> None:
        self.store_host = store_host.rstrip("/")
        self.token = token
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def init_multipart(self, object_key: str) -> str:
        response = self.session.post(
            f"{self.store_host}/api/upload/multipart/init",
            headers=self._headers(),
            json={"path": object_key},
            timeout=30,
        )
        if not getattr(response, "ok", False):
            raise StorageInitError(
                "init",
                "multipart init failed",
                redacted_detail=getattr(response, "text", ""),
            )
        data = response.json().get("data") or {}
        initial_key = str(data.get("initialKey") or "")
        if not initial_key:
            raise StorageInitError(
                "init",
                "multipart init response missing initialKey",
            )
        return initial_key

    def upload_part(
        self,
        *,
        object_key: str,
        initial_key: str,
        number: int,
        part_size: int,
        data: bytes,
    ) -> str:
        param = {
            "initialKey": initial_key,
            "number": number,
            "partSize": part_size,
            "objectKey": object_key,
        }
        headers = self._headers()
        headers["X-Storage-Param"] = encode_storage_param(param)
        response = self.session.post(
            f"{self.store_host}/api/upload/multipart/upload",
            headers=headers,
            data=data,
            timeout=300,
        )
        if not getattr(response, "ok", False):
            raise StoragePartUploadError(
                "part_upload",
                "multipart part upload failed",
                retryable=True,
            )
        data_block = response.json().get("data") or {}
        part_string = str(data_block.get("partString") or "")
        if not part_string:
            raise StoragePartUploadError(
                "part_upload",
                "multipart part upload response missing partString",
                retryable=True,
            )
        return part_string

    def complete_multipart(
        self,
        *,
        object_key: str,
        initial_key: str,
        part_strings: list[str],
    ) -> None:
        response = self.session.post(
            f"{self.store_host}/api/upload/multipart/complete",
            headers=self._headers(),
            json={
                "path": object_key,
                "initialKey": initial_key,
                "partString": part_strings,
            },
            timeout=300,
        )
        if not getattr(response, "ok", False):
            raise StorageCompleteError("complete", "multipart complete failed")
