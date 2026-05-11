from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransferError(Exception):
    stage: str
    safe_message: str
    retryable: bool = False
    transfer_id: str = ""
    bytes_done: int | None = None
    bytes_total: int | None = None
    resume_available: bool = False
    redacted_detail: str = ""
    diagnostics: dict[str, object] | None = None

    def __str__(self) -> str:
        return self.safe_message

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": False,
            "stage": self.stage,
            "retryable": self.retryable,
            "safe_message": self.safe_message,
            "transfer_id": self.transfer_id,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "resume_available": self.resume_available,
            "redacted_detail": self.redacted_detail,
        }
        if self.diagnostics:
            payload["diagnostics"] = self.diagnostics
        return payload


class ArchiveError(TransferError):
    pass


class RetryableTransferError(TransferError):
    pass


class NonRetryableTransferError(TransferError):
    pass


class StorageInitError(TransferError):
    pass


class StoragePartUploadError(TransferError):
    pass


class StorageCompleteError(TransferError):
    pass


class ManifestError(TransferError):
    pass


class ResumeValidationError(TransferError):
    pass


class RangeProbeError(TransferError):
    pass


class DownloadError(TransferError):
    pass


class ExtractError(TransferError):
    pass


class PublishError(TransferError):
    pass


class RemoteVersionError(TransferError):
    pass


class RemoteExecutionError(TransferError):
    pass
