from unittest.mock import MagicMock

import pytest

from src.services.session_directory_service import (
    SessionDirectoryError,
    SessionDirectoryResolver,
    normalize_remote_workspace_path,
    normalize_session_directory_for_storage,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/share", "/share"),
        ("/share/foo", "/share/foo"),
        ("/share/foo/./bar/", "/share/foo/bar"),
        ("/share/foo/../bar", "/share/bar"),
        ("  /share/run-1  ", "/share/run-1"),
        ("/personal", "/personal"),
        ("/personal/sub", "/personal/sub"),
        ("/personal/foo/../bar/", "/personal/bar"),
        ("  /personal/run-1  ", "/personal/run-1"),
    ],
)
def test_normalize_remote_workspace_path_accepts_dual_root_descendants(raw, expected):
    assert normalize_remote_workspace_path(raw) == expected


@pytest.mark.parametrize(
    ("raw", "error_code"),
    [
        (123, "directory_invalid_type"),
        ("relative/path", "directory_must_be_absolute"),
        ("/tmp/foo", "directory_outside_roots"),
        ("/share2/foo", "directory_outside_roots"),
        ("/personalx", "directory_outside_roots"),
        ("/personalx/foo", "directory_outside_roots"),
        ("/", "directory_outside_roots"),
        ("/share/../root", "directory_outside_roots"),
        ("/share/foo/../../root", "directory_outside_roots"),
        ("/personal/../root", "directory_outside_roots"),
        pytest.param("/share/bad\0path", "directory_invalid_chars", id="null-byte"),
    ],
)
def test_normalize_remote_workspace_path_rejects_invalid_inputs(raw, error_code):
    with pytest.raises(SessionDirectoryError) as exc:
        normalize_remote_workspace_path(raw)

    assert exc.value.error_code == error_code
    assert exc.value.http_status == 400


def test_normalize_session_directory_for_storage_returns_none_for_blank():
    assert normalize_session_directory_for_storage(None) is None
    assert normalize_session_directory_for_storage("") is None
    assert normalize_session_directory_for_storage("   ") is None


def test_normalize_session_directory_for_storage_normalizes_and_rejects():
    assert (
        normalize_session_directory_for_storage(" /share/foo/../bar/ ") == "/share/bar"
    )
    assert (
        normalize_session_directory_for_storage(" /personal/foo/../bar/ ")
        == "/personal/bar"
    )

    with pytest.raises(SessionDirectoryError) as exc:
        normalize_session_directory_for_storage("/tmp/bad")
    assert exc.value.error_code == "directory_outside_roots"


def _sessions_service(session_directory):
    svc = MagicMock()
    svc.get_session.return_value = {"session_directory": session_directory}
    return svc


def test_resolver_uses_request_directory_before_session_default():
    resolver = SessionDirectoryResolver(_sessions_service("/share/default"))

    result = resolver.resolve(
        session_id="sess-1",
        request_directory="/share/request/../run",
        request_directory_provided=True,
    )

    assert result.remote_workdir == "/share/run"
    assert result.source == "request"
    assert result.bohrium_required is True


def test_resolver_accepts_personal_request_directory():
    resolver = SessionDirectoryResolver(_sessions_service("/share/default"))

    result = resolver.resolve(
        session_id="sess-1",
        request_directory="/personal/run/../keep",
        request_directory_provided=True,
    )

    assert result.remote_workdir == "/personal/keep"
    assert result.source == "request"
    assert result.bohrium_required is True


def test_resolver_blank_request_falls_through_to_session_default():
    svc = _sessions_service("/share/default")
    resolver = SessionDirectoryResolver(svc)

    result = resolver.resolve(
        session_id="sess-1",
        request_directory="   ",
        request_directory_provided=True,
    )

    assert result.remote_workdir == "/share/default"
    assert result.source == "session"
    assert result.bohrium_required is True
    svc.get_session.assert_called_once_with("sess-1")


def test_resolver_without_request_uses_session_default():
    resolver = SessionDirectoryResolver(_sessions_service("/share/default"))

    result = resolver.resolve(
        session_id="sess-1",
        request_directory=None,
        request_directory_provided=False,
    )

    assert result.remote_workdir == "/share/default"
    assert result.source == "session"
    assert result.bohrium_required is True


def test_resolver_without_any_directory_returns_none_source():
    resolver = SessionDirectoryResolver(_sessions_service(None))

    result = resolver.resolve(
        session_id="sess-1",
        request_directory=None,
        request_directory_provided=False,
    )

    assert result.remote_workdir is None
    assert result.source == "none"
    assert result.bohrium_required is False


def test_resolver_relabels_invalid_session_default():
    resolver = SessionDirectoryResolver(_sessions_service("/tmp/bad"))

    with pytest.raises(SessionDirectoryError) as exc:
        resolver.resolve(
            session_id="sess-1",
            request_directory=None,
            request_directory_provided=False,
        )

    assert exc.value.error_code == "session_directory_invalid"
