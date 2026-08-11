"""URL allowlist tests split out of ``tests/test_security_fixes.py``.

Domain: G4-M-55 (``extend_url_allowlist`` emits a WARNING-level audit
log) + G4-M-56 (``assert_url_allowed`` gains an opt-in
``allow_loopback_http`` kwarg so HTTP loopback is no longer accepted
by default).

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
"""

from __future__ import annotations

import inspect
import socket

import pytest
from voice_typer.server import _secrets
from voice_typer.server._secrets import (
    assert_url_allowed,
    extend_url_allowlist,
)
from voice_typer.server.security.url_allowlist import (
    _is_ip_literal,
    _is_private_ip,
    _user_extensions,
    get_url_allowlist,
)


@pytest.fixture(autouse=True)
def _reset_url_allowlist_extensions():
    """Reset the process-global runtime allowlist between tests.

    ``extend_url_allowlist`` mutates the module-global ``_user_extensions``
    set; several tests here call it (the audit-log tests and the SSRF
    tests, e.g. ``extend_url_allowlist(["10.0.0.1"])``). Without a reset,
    those hosts leak into every later test in the session — benign today
    (the SSRF blocklist rejects the private IPs added), but a latent
    state leak for any future test asserting ``get_url_allowlist()``.
    """
    _user_extensions.clear()
    yield
    _user_extensions.clear()


class TestExtendUrlAllowlistAuditLog:
    """G4-M-55: ``extend_url_allowlist`` emits a WARNING-level audit
    log on every call so operators can trace every runtime expansion
    of the trusted-host set back to its origin."""

    def test_warning_emitted_with_hosts(self, caplog):
        """G4-M-55: a WARNING is emitted with the hosts being added."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server.security.url_allowlist"):
                extend_url_allowlist(
                    ["audit-test.example.com"],
                    caller="test_g4_m_55_warning_emitted",
                )
            # The WARNING must mention the URL-Allowlist extension.
            assert any("[URL-Allowlist]" in r.message and r.levelname == "WARNING" for r in caplog.records), (
                f"expected WARNING-level URL-Allowlist log; got: {caplog.records!r}"
            )
            # The host must be in the log message.
            assert any("audit-test.example.com" in r.message for r in caplog.records), (
                f"host must be in the log message; got: {caplog.records!r}"
            )
        finally:
            _secrets._user_extensions.discard("audit-test.example.com")

    def test_warning_includes_caller(self, caplog):
        """G4-M-55: the WARNING includes the caller identifier."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server.security.url_allowlist"):
                extend_url_allowlist(
                    ["caller-test.example.com"],
                    caller="explicit-caller-id",
                )
            joined = " ".join(r.message for r in caplog.records)
            assert "explicit-caller-id" in joined, f"caller identifier must appear in the log message; got: {joined!r}"
        finally:
            _secrets._user_extensions.discard("caller-test.example.com")

    def test_warning_auto_detects_caller_when_not_passed(self, caplog):
        """G4-M-55: when ``caller=None``, the caller is auto-detected
        via ``inspect.stack()`` and included in the WARNING."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server.security.url_allowlist"):
                # Don't pass caller — auto-detection should kick in.
                extend_url_allowlist(["auto-caller.example.com"])
            joined = " ".join(r.message for r in caplog.records)
            # The auto-detected caller should include this test function
            # name (or the test module name).
            assert (
                "test_warning_auto_detects_caller_when_not_passed" in joined
                or "TestG4M55" in joined
                or "test_url_allowlist" in joined
            ), f"auto-detected caller must reference this test; got: {joined!r}"
        finally:
            _secrets._user_extensions.discard("auto-caller.example.com")

    def test_info_emitted_for_empty_input(self, caplog):
        """YJ-44: a no-op call (empty hosts iterable) emits an INFO
        audit record — not a WARNING. WARNING is reserved for the
        security-relevant case (actual hosts being added). The no-op
        case is still audited (so operators can trace every attempt to
        extend the allowlist) but demoted to INFO to avoid WARNING
        spam when callers pass an empty iterable defensively."""
        with caplog.at_level("INFO", logger="voice_typer.server.security.url_allowlist"):
            extend_url_allowlist([], caller="test-empty-input")
        # An INFO record with the URL-Allowlist tag must be emitted.
        assert any("[URL-Allowlist]" in r.message and r.levelname == "INFO" for r in caplog.records), (
            "a no-op extend_url_allowlist call must emit an INFO audit record"
        )
        # And NO WARNING record should be emitted for the no-op case.
        assert not any(r.levelname == "WARNING" and "[URL-Allowlist]" in r.message for r in caplog.records), (
            "a no-op extend_url_allowlist call must NOT emit a WARNING "
            "(YJ-44: WARNING reserved for actual host additions)"
        )


class TestAssertUrlAllowedLoopbackOptIn:
    """G4-M-56: ``assert_url_allowed`` gains an ``allow_loopback_http``
    kwarg (default ``False``). Pre-fix, loopback hosts (localhost,
    127.0.0.1, ::1) were ALWAYS exempt from the HTTPS requirement.
    Post-fix, callers must opt in via the kwarg."""

    def test_http_loopback_rejected_by_default(self):
        """G4-M-56: ``http://localhost:11434`` is REJECTED by default
        (``allow_loopback_http=False``). Pre-fix, it was accepted."""
        with pytest.raises(ValueError, match="HTTPS for loopback"):
            assert_url_allowed(
                "http://localhost:11434/v1/chat/completions",
                field_name="llm_api_url",
                client_name="test",
            )

    def test_http_loopback_allowed_when_opted_in(self):
        """G4-M-56: ``http://localhost:11434`` is ACCEPTED when the
        caller passes ``allow_loopback_http=True``."""
        # Should NOT raise.
        assert_url_allowed(
            "http://localhost:11434/v1/chat/completions",
            field_name="llm_api_url",
            client_name="test",
            allow_loopback_http=True,
        )

    def test_http_127_local_allowed_when_opted_in(self):
        """G4-M-56: ``http://127.0.0.1:8000`` is ACCEPTED when opted in."""
        assert_url_allowed(
            "http://127.0.0.1:8000/v1",
            allow_loopback_http=True,
        )

    def test_http_ipv6_loopback_allowed_when_opted_in(self):
        """G4-M-56: ``http://[::1]:8000`` is ACCEPTED when opted in."""
        assert_url_allowed(
            "http://[::1]:8000/v1",
            allow_loopback_http=True,
        )

    def test_https_loopback_allowed_without_opt_in(self):
        """G4-M-56: HTTPS to loopback is still accepted without opt-in
        (the kwarg only gates HTTP, not HTTPS)."""
        assert_url_allowed("https://localhost:8443/v1")

    def test_https_non_loopback_allowed(self):
        """G4-M-56: regression — HTTPS to a normal allowlisted host
        still works without opt-in."""
        assert_url_allowed("https://api.openai.com/v1/chat/completions")

    def test_http_non_loopback_rejected_even_with_opt_in(self):
        """G4-M-56: ``allow_loopback_http=True`` does NOT open the
        door to HTTP for non-loopback hosts — only loopback is exempted."""
        with pytest.raises(ValueError, match="HTTPS for non-loopback"):
            assert_url_allowed(
                "http://api.openai.com/v1/chat/completions",
                allow_loopback_http=True,
            )

    def test_loopback_http_error_message_mentions_kwarg(self):
        """G4-M-56: the error message for a rejected HTTP loopback URL
        mentions ``allow_loopback_http=True`` so the operator knows
        how to fix the call site."""
        with pytest.raises(ValueError, match="allow_loopback_http=True"):
            assert_url_allowed("http://localhost:11434/v1")

    def test_default_kwarg_value_is_false(self):
        """G4-M-56: the default value of ``allow_loopback_http`` is
        ``False`` — callers must explicitly opt in."""
        sig = inspect.signature(assert_url_allowed)
        param = sig.parameters["allow_loopback_http"]
        assert param.default is False, f"allow_loopback_http default must be False; got {param.default!r}"


class TestAssertUrlAllowedSsrfDefense:
    """HU-35: the SSRF defense layers of ``assert_url_allowed`` MUST
    have direct test coverage.

    After the allowlist + HTTPS checks pass, three defenses run:
      1. IP-literal blocklist via ``_is_private_ip`` (rejects
         ``10.0.0.1``, ``169.254.169.254`` cloud metadata, etc.).
      2. DNS-rebinding check via ``socket.getaddrinfo`` (rejects
         hostnames that resolve to private IPs).
      3. ``check_dns_rebinding=False`` opt-out for no-network test envs
         (the IP-literal blocklist STILL runs).

    The cloud-metadata endpoint ``169.254.169.254`` is the primary SSRF
    target — a regression that drops these checks would let a crafted
    ``cloud_api_url`` exfiltrate the API key via the Authorization
    header.
    """

    def test_rejects_private_ip_literal(self):
        """RFC 1918 private IP literals are rejected even when
        explicitly allowlisted — the IP-literal blocklist runs AFTER
        the hostname allowlist check."""
        extend_url_allowlist(["10.0.0.1"])
        with pytest.raises(ValueError, match="private/reserved IP literal"):
            assert_url_allowed(
                "https://10.0.0.1/v1",
                field_name="cloud_api_url",
                client_name="cloud/test",
            )

    def test_rejects_cloud_metadata_endpoint(self):
        """The AWS/cloud metadata endpoint 169.254.169.254 is the
        primary SSRF target — must be rejected even if allowlisted."""
        extend_url_allowlist(["169.254.169.254"])
        with pytest.raises(ValueError, match="private/reserved IP literal"):
            assert_url_allowed(
                "https://169.254.169.254/latest/meta-data/",
                field_name="cloud_api_url",
                client_name="cloud/test",
            )

    def test_ipv6_private_literal_rejected_by_ssrf(self):
        """HU-35 follow-up: an IPv6 unique-local / link-local literal
        that IS allowlisted is rejected by the SSRF IP-literal blocklist
        (fc00::/7 is private, fe80::/10 is link-local) — the blocklist
        runs after the hostname allowlist check, so the private address
        is refused even though the user explicitly allowed it.
        """
        extend_url_allowlist(["fc00::1", "fe80::1"])
        for host in ("fc00::1", "fe80::1"):
            with pytest.raises(ValueError, match="private/reserved IP literal"):
                assert_url_allowed(
                    f"https://[{host}]/v1",
                    field_name="cloud_api_url",
                    client_name="cloud/test",
                )

    def test_public_ipv6_literal_allowlisted_and_allowed(self):
        """HU-35 follow-up: a PUBLIC (non-private) IPv6 literal can now
        be allowlisted (the port-stripping no longer mangles it) and
        passes ``assert_url_allowed`` — the SSRF blocklist only rejects
        private/reserved ranges.
        """
        # 2606:4700:4700::1111 is Cloudflare's public DNS IPv6 — not
        # private/loopback/link-local/unspecified/reserved.
        extend_url_allowlist(["2606:4700:4700::1111"])
        assert_url_allowed(
            "https://[2606:4700:4700::1111]/v1",
            field_name="cloud_api_url",
            client_name="cloud/test",
        )

    def test_bracketed_ipv6_with_port_survives_allowlist(self):
        """HU-35 follow-up: the bracketed-with-port form
        (``[fc00::1]:8080``) normalizes to the bare literal so the
        allowlist entry matches the bracket-stripped hostname that
        ``urlparse`` produces."""
        extend_url_allowlist(["[fc00::1]:8080"])
        allowlist = get_url_allowlist()
        assert "fc00::1" in allowlist, f"bracketed IPv6 must normalize to the bare literal; got {allowlist!r}"

    def test_unparseable_multi_colon_host_is_dropped(self):
        """HU-35 follow-up: a multi-colon string that is NOT a valid
        IPv6 literal (e.g. ``bad:host:name``) must be DROPPED by
        ``extend_url_allowlist`` — not silently truncated to its first
        hextet (the old ``split(":")[0]`` behavior would have added the
        mangled host ``bad``). Mirrors the reject semantics of the
        ``trusted_extra_hosts`` config validator and the
        ``add_trusted_endpoint`` handler.
        """
        extend_url_allowlist(["bad:host:name"], caller="test")
        assert "bad" not in get_url_allowlist()
        assert "bad:host:name" not in get_url_allowlist()

    def test_bare_ipv6_with_trailing_hextet_is_valid_ipv6(self):
        """``fc00::1:8080`` IS valid IPv6 (8 hextets, the last being
        ``8080``) — it survives normalization intact and is later
        rejected as a private literal by the SSRF blocklist. This
        documents the un-bracketed-port ambiguity: the bracketed form
        ``[fc00::1]:8080`` is the way to express a port."""
        extend_url_allowlist(["fc00::1:8080"], caller="test")
        assert "fc00::1:8080" in get_url_allowlist()
        # But the URL can never be used — SSRF rejects the private
        # IPv6 literal.
        with pytest.raises(ValueError, match="private/reserved IP literal"):
            assert_url_allowed(
                "https://[fc00::1:8080]/v1",
                field_name="cloud_api_url",
                client_name="cloud/test",
            )

    def test_ipv6_loopback_literal_is_allowed(self):
        """Loopback literals (127.0.0.1, ::1) are explicitly exempted
        from the SSRF blocklist because they are already allowlisted
        for local development — document the intent so a future
        hardening pass doesn't accidentally break localhost flows."""
        # No raise — loopback returns before the SSRF blocklist.
        assert_url_allowed(
            "https://[::1]/v1",
            field_name="cloud_api_url",
            client_name="cloud/test",
        )

    def test_check_dns_rebinding_false_skips_resolution(self, monkeypatch):
        """``check_dns_rebinding=False`` (the no-network test-env
        opt-out) skips the ``socket.getaddrinfo`` resolution entirely.
        Here the hostname would resolve to a private IP if queried —
        with the opt-out the URL is allowed; with the default (True) it
        is rejected. This pins both the default AND the opt-out.
        """

        def _fake_getaddrinfo(_host, _port):
            # api.openai.com resolves to a private IP (DNS-rebinding
            # attack / /etc/hosts tampering simulation).
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
            ]

        monkeypatch.setattr(
            "voice_typer.server.security.url_allowlist.socket.getaddrinfo",
            _fake_getaddrinfo,
        )

        # Default check_dns_rebinding=True: the private resolution is
        # rejected.
        with pytest.raises(ValueError, match="resolves to private/reserved IP"):
            assert_url_allowed(
                "https://api.openai.com/v1",
                field_name="cloud_api_url",
                client_name="cloud/test",
            )

        # check_dns_rebinding=False: resolution skipped, URL allowed.
        assert_url_allowed(
            "https://api.openai.com/v1",
            field_name="cloud_api_url",
            client_name="cloud/test",
            check_dns_rebinding=False,
        )

    def test_dns_resolution_failure_is_nonfatal(self, monkeypatch):
        """A ``socket.gaierror`` (no DNS, offline sandbox) is swallowed
        and the allowlisted hostname is allowed — the IP-literal
        blocklist still runs for IP hosts."""
        monkeypatch.setattr(
            "voice_typer.server.security.url_allowlist.socket.getaddrinfo",
            lambda _h, _p: (_ for _ in ()).throw(OSError("name or service not known")),
        )
        assert_url_allowed(
            "https://api.openai.com/v1",
            field_name="cloud_api_url",
            client_name="cloud/test",
        )

    def test_ssrf_defense_helpers(self):
        """Unit-check the two SSRF helper predicates directly."""
        assert _is_ip_literal("10.0.0.1") is True
        assert _is_ip_literal("::1") is True
        assert _is_ip_literal("api.openai.com") is False
        assert _is_ip_literal("") is False
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("169.254.169.254") is True
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("127.0.0.1") is True
        assert _is_private_ip("::1") is True
        assert _is_private_ip("fc00::1") is True
        assert _is_private_ip("fe80::1") is True
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("1.1.1.1") is False
