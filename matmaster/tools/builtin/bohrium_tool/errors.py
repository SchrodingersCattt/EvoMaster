from matmaster.bohrium.errors import (  # noqa: F401
    BohriumAPIError,
    BohriumCredentialError,
    BohriumError,
    BohriumTransferError,
)


class BohriumPathError(BohriumError):
    """Raised when Bohrium path resolution fails."""


class BohriumJobStateError(BohriumError):
    """Raised when a Bohrium job is not in the expected state."""
