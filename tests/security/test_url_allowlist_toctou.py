"""Post-connect SSRF peer-IP checks for security-sensitive HTTP paths."""

import socket

import pytest
from voice_typer.server.security.url_allowlist import (
    resolve_allowed_url_ips,
    verify_peer_ip_allowed,
)


def test_resolve_rejects_private_dns_result(monkeypatch):
    from voice_typer.server.security import url_allowlist as allowlist

    monkeypatch.setattr(allowlist, "get_url_allowlist", lambda: frozenset({"example.com"}))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.1", 0))],
    )
    with pytest.raises(ValueError):
        resolve_allowed_url_ips("https://example.com/x", field_name="url", client_name="t")


def test_resolve_returns_public_ips(monkeypatch):
    from voice_typer.server.security import url_allowlist as allowlist

    monkeypatch.setattr(allowlist, "get_url_allowlist", lambda: frozenset({"example.com"}))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    assert resolve_allowed_url_ips("https://example.com/x", field_name="url", client_name="t") == ("93.184.216.34",)


def test_verify_peer_rejects_outside_set():
    with pytest.raises(ValueError):
        verify_peer_ip_allowed("10.0.0.1", ("93.184.216.34",), host="example.com")
    verify_peer_ip_allowed("93.184.216.34", ("93.184.216.34",), host="example.com")
