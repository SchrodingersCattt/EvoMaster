from __future__ import annotations


class BohriumCredentialError(RuntimeError):
    """Raised when Bohrium credentials cannot be resolved."""


class BohriumRuntimeNotInitialized(RuntimeError):
    """Raised when the current session has no attached Bohrium runtime."""


class BohriumSubmissionBuildError(RuntimeError):
    """Raised when a submission spec cannot be built from runtime state."""


class BohriumPathMaterializationError(RuntimeError):
    """Raised when a calculation input path cannot be converted to a remote URL."""
