from __future__ import annotations

import socket

import pytest

from src.services.byok_endpoint_policy import (
    BYOKEndpointPolicy,
    BYOKEndpointPolicyError,
)


def _patch_dns(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    def fake_getaddrinfo(host: str, port: int, *_args: object) -> list[tuple]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_allows_https_public_domain_and_normalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dns(monkeypatch, "93.184.216.34")

    policy = BYOKEndpointPolicy()

    assert (
        policy.validate_base_url("  https://api.example.com/v1//  ")
        == "https://api.example.com/v1"
    )


def test_rejects_unsafe_url_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dns(monkeypatch, "93.184.216.34")
    policy = BYOKEndpointPolicy()

    unsafe_urls = [
        "http://api.example.com",
        "ftp://api.example.com",
        "https://user:pass@api.example.com",
        "https://api.example.com/v1?api_key=secret",
        "https://api.example.com/v1#fragment",
        "https://api.example.com:8443/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://10.1.2.3/v1",
        "https://169.254.169.254/latest",
    ]

    for url in unsafe_urls:
        with pytest.raises(BYOKEndpointPolicyError):
            policy.validate_base_url(url)


def test_rejects_dns_records_to_private_or_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = BYOKEndpointPolicy()

    for address in ("10.1.2.3", "127.0.0.1", "169.254.169.254", "fe80::1"):
        _patch_dns(monkeypatch, address)
        with pytest.raises(BYOKEndpointPolicyError):
            policy.validate_base_url("https://api.example.com/v1")


def test_redirect_target_uses_same_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dns(monkeypatch, "93.184.216.34")
    policy = BYOKEndpointPolicy()

    assert (
        policy.validate_redirect_target("https://redirect.example.com/v1/")
        == "https://redirect.example.com/v1"
    )

    _patch_dns(monkeypatch, "127.0.0.1")
    with pytest.raises(BYOKEndpointPolicyError):
        policy.validate_redirect_target("https://redirect.example.com/v1")
