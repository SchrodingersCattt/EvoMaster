from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit


class BYOKEndpointPolicyError(ValueError):
    pass


class BYOKEndpointPolicy:
    def __init__(self, *, allowed_ports: set[int] | None = None) -> None:
        self.allowed_ports = allowed_ports or {443}

    def validate_base_url(self, raw_url: str) -> str:
        return self._validate_url(raw_url)

    def validate_redirect_target(self, target_url: str) -> str:
        return self._validate_url(target_url)

    def _validate_url(self, raw_url: str) -> str:
        value = (raw_url or "").strip()
        if not value:
            raise BYOKEndpointPolicyError("base_url is required.")

        parts = urlsplit(value)
        if parts.scheme.lower() != "https":
            raise BYOKEndpointPolicyError("base_url must use https.")
        if not parts.netloc or not parts.hostname:
            raise BYOKEndpointPolicyError("base_url host is required.")
        if parts.username is not None or parts.password is not None:
            raise BYOKEndpointPolicyError("base_url must not contain userinfo.")
        if parts.query or parts.fragment:
            raise BYOKEndpointPolicyError("base_url must not contain query or fragment.")

        try:
            port = parts.port or 443
        except ValueError as exc:
            raise BYOKEndpointPolicyError("base_url port is invalid.") from exc
        if port not in self.allowed_ports:
            raise BYOKEndpointPolicyError("base_url port is not allowed.")

        host = parts.hostname.strip().lower()
        self._validate_host(host, port)

        path = re.sub(r"/{2,}", "/", parts.path or "").rstrip("/")
        netloc = host if port == 443 else f"{host}:{port}"
        return f"https://{netloc}{path}"

    def _validate_host(self, host: str, port: int) -> None:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            ".localhost"
        ):
            raise BYOKEndpointPolicyError("base_url host is not allowed.")

        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            ip = None
        if ip is not None:
            self._validate_ip(ip)
            return

        try:
            records = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            raise BYOKEndpointPolicyError("base_url host cannot be resolved.") from exc
        if not records:
            raise BYOKEndpointPolicyError("base_url host cannot be resolved.")

        for record in records:
            sockaddr = record[4]
            resolved_host = sockaddr[0]
            try:
                self._validate_ip(ipaddress.ip_address(resolved_host))
            except ValueError as exc:
                raise BYOKEndpointPolicyError(
                    "base_url resolved address is invalid."
                ) from exc

    @staticmethod
    def _validate_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if not ip.is_global:
            raise BYOKEndpointPolicyError("base_url resolved address is not public.")
