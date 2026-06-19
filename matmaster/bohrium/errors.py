from __future__ import annotations


class BohriumError(RuntimeError):
    """Base class for all Bohrium errors."""


class BohriumCredentialError(BohriumError):
    """Raised when Bohrium credentials cannot be resolved."""


class BohriumAPIError(BohriumError):
    """Raised when Bohrium OpenAPI returns an error."""


class BohriumTransferError(BohriumError):
    """Raised when archive upload, download, or publish fails."""

    created_job_ref: object | None = None

    @classmethod
    def with_created_job_ref(
        cls, message: str, created_job_ref: object | None
    ) -> BohriumTransferError:
        error = cls(message)
        error.created_job_ref = created_job_ref
        return error


class BohriumRuntimeNotInitialized(BohriumError):
    """Raised when the current session has no attached Bohrium runtime."""


class BohriumSubmissionBuildError(BohriumError):
    """Raised when a submission spec cannot be built from runtime state."""


class BohriumPathMaterializationError(BohriumError):
    """Raised when a calculation input path cannot be converted to a remote URL."""
