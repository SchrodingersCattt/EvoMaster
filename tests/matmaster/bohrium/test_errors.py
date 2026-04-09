from __future__ import annotations

from matmaster.bohrium.errors import (
    BohriumAPIError,
    BohriumCredentialError,
    BohriumError,
    BohriumPathMaterializationError,
    BohriumRuntimeNotInitialized,
    BohriumSubmissionBuildError,
    BohriumTransferError,
)
from matmaster.tools.builtin.bohrium_tool.errors import (
    BohriumJobStateError,
    BohriumPathError,
)


def test_core_errors_inherit_from_bohrium_error() -> None:
    assert issubclass(BohriumCredentialError, BohriumError)
    assert issubclass(BohriumAPIError, BohriumError)
    assert issubclass(BohriumTransferError, BohriumError)
    assert issubclass(BohriumRuntimeNotInitialized, BohriumError)
    assert issubclass(BohriumSubmissionBuildError, BohriumError)
    assert issubclass(BohriumPathMaterializationError, BohriumError)


def test_tool_specific_errors_inherit_from_bohrium_error() -> None:
    assert issubclass(BohriumPathError, BohriumError)
    assert issubclass(BohriumJobStateError, BohriumError)
