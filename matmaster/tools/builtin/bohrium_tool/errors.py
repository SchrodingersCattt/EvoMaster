class BohriumError(RuntimeError):
    """Base class for Bohrium runtime failures."""


class BohriumCredentialError(BohriumError):
    """Raised when required Bohrium credentials are unavailable."""


class BohriumPathError(BohriumError):
    """Raised when Bohrium path resolution fails."""


class BohriumTransferError(BohriumError):
    """Raised when archive upload, download, or publish fails."""


class BohriumAPIError(BohriumError):
    """Raised when Bohrium OpenAPI returns an error."""


class BohriumJobStateError(BohriumError):
    """Raised when a Bohrium job is not in the expected state."""
