from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import requests

from .errors import (
    StorageCompleteError,
    StorageInitError,
    StoragePartUploadError,
    TransferError,
)
from .transport import request_storehost_json


def encode_storage_param(parameter: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(parameter).encode()).decode()


def decode_storage_param(encoded: str) -> dict[str, Any]:
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


@dataclass(frozen=True)
class StoreHostPartResult:
    part_string: str
    server_hash_checked: bool
    server_hash_value: str | None


def _wrap_storage_error(error_cls, exc: TransferError):
    return error_cls(
        exc.stage,
        str(exc),
        retryable=exc.retryable,
        redacted_detail=exc.redacted_detail,
    )


def _normalize_hash_value(
    data: dict[str, Any],
    headers: dict[str, str],
) -> tuple[str | None, str | None]:
    for field in ("contentMd5", "md5", "etag"):
        value = str(data.get(field) or "").strip()
        if value:
            return field, value
    for field in ("Content-MD5", "ETag"):
        value = str(headers.get(field) or "").strip()
        if value:
            return field, value
    return None, None


def _hash_matches(value: str, *, md5_base64: str, md5_hex: str) -> bool | None:
    normalized = value.strip().strip('"')
    if not normalized:
        return None
    if normalized == md5_base64:
        return True
    if len(normalized) == 32 and all(
        char in "0123456789abcdefABCDEF" for char in normalized
    ):
        return normalized.lower() == md5_hex.lower()
    return None


class StoreHostClient:
    def __init__(self, store_host: str, token: str, *, session=None) -> None:
        self.store_host = store_host.rstrip("/")
        self.token = token
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def init_multipart(self, object_key: str) -> str:
        try:
            response = request_storehost_json(
                self.session,
                "POST",
                f"{self.store_host}/api/upload/multipart/init",
                stage="multipart_init",
                headers=self._headers(),
                json_body={"path": object_key},
                timeout=30,
                retryable_business_codes={50001, 50002, 50003},
            )
        except TransferError as exc:
            raise _wrap_storage_error(StorageInitError, exc) from exc
        data = response.data
        initial_key = str(data.get("initialKey") or "")
        if not initial_key:
            raise StorageInitError(
                "multipart_init",
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
        data: Iterable[bytes] | Callable[[], Iterable[bytes]],
        md5_base64: str,
        md5_hex: str,
    ) -> StoreHostPartResult:
        param = {
            "initialKey": initial_key,
            "number": number,
            "partSize": part_size,
        }
        headers = self._headers()
        headers["X-Storage-Param"] = encode_storage_param(param)
        headers["Content-Length"] = str(part_size)
        try:
            response = request_storehost_json(
                self.session,
                "POST",
                f"{self.store_host}/api/upload/multipart/upload",
                stage="multipart_part",
                headers=headers,
                data=data,
                timeout=300,
                retryable_business_codes={50001, 50002, 50003},
            )
        except TransferError as exc:
            raise _wrap_storage_error(StoragePartUploadError, exc) from exc
        data_block = response.data
        part_string = str(data_block.get("partString") or "")
        if not part_string:
            raise StoragePartUploadError(
                "multipart_part",
                "multipart part upload response missing partString",
                retryable=True,
            )
        _field, server_hash_value = _normalize_hash_value(data_block, response.headers)
        server_hash_checked = False
        if server_hash_value:
            matched = _hash_matches(
                server_hash_value,
                md5_base64=md5_base64,
                md5_hex=md5_hex,
            )
            if matched is False:
                raise StoragePartUploadError(
                    "part_upload",
                    "multipart part upload hash mismatch",
                    retryable=True,
                )
            server_hash_checked = matched is True
        return StoreHostPartResult(
            part_string=part_string,
            server_hash_checked=server_hash_checked,
            server_hash_value=server_hash_value,
        )

    def complete_multipart(
        self,
        *,
        object_key: str,
        initial_key: str,
        part_strings: list[str],
    ) -> None:
        try:
            request_storehost_json(
                self.session,
                "POST",
                f"{self.store_host}/api/upload/multipart/complete",
                stage="multipart_complete",
                headers=self._headers(),
                json_body={
                    "initialKey": initial_key,
                    "partString": part_strings,
                },
                timeout=300,
                retryable_business_codes={50001, 50002, 50003},
                allow_missing_data=True,
            )
        except TransferError as exc:
            raise _wrap_storage_error(StorageCompleteError, exc) from exc

    def iterate(self, prefix: str, *, next_token: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"prefix": prefix}
        if next_token:
            payload["nextToken"] = next_token
        try:
            response = request_storehost_json(
                self.session,
                "POST",
                f"{self.store_host}/api/iterate",
                stage="sandbox_iterate",
                headers={**self._headers(), "Content-Type": "application/json"},
                json_body=payload,
                timeout=30,
                retryable_business_codes={50001, 50002, 50003},
            )
        except TransferError as exc:
            raise _wrap_storage_error(StorageInitError, exc) from exc
        return response.data
