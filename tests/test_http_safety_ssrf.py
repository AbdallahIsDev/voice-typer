"""FR-25: regression tests for the SSRF defense in
``_secrets.assert_url_allowed`` and the ``_is_private_ip`` helper.

Pre-fix, the URL allowlist was hostname-based only.  There was no
DNS-resolution-time check that the resolved IP is not a
private/reserved address (RFC 1918 10/8, 172.16/12, 192.168/16;
link-local 169.254/16 including the cloud metadata endpoint
169.254.169.254; loopback 127/8).  If a trusted hostname (e.g.
``api.openai.com``) resolved to a private IP — via ``/etc/hosts``
tampering, compromised DNS, DNS rebinding, or a malicious local DNS
resolver — the request was sent to the private IP, exfiltrating the
API key (in the Authorization header) and the audio body to the
cloud metadata endpoint or any internal service.

The fix adds two helpers and a post-allowlist SSRF check:

  * ``_is_ip_literal(host)`` — True if the host string is already an
    IP literal (e.g. ``"10.0.0.1"``, ``"::1"``).

  * ``_is_private_ip(ip_str)`` — True if the IP is in a
    private/reserved range.  Covers RFC 1918, link-local, loopback,
    unspecified, IPv6 ULA, IPv6 link-local, and the various
    ``ipaddress`` ``is_reserved`` ranges.

  * In ``assert_url_allowed``, after the allowlist + HTTPS checks
    pass, the SSRF check runs:
      * For IP-literal hosts (e.g. ``"10.0.0.5"``): blocklist lookup
        via ``_is_private_ip``.  Reject if private.  Loopback IPs
        (``127.0.0.1``, ``::1``) are exempted (local dev opt-in).
      * For hostname hosts (e.g. ``api.openai.com``): best-effort
        ``socket.getaddrinfo`` resolution; reject if ANY resolved IP
        is private.  ``gaierror`` is swallowed (offline test env).

Test approach:

1. **``_is_private_ip`` direct tests** — verify the helper returns
   True for private/reserved IPs and False for public IPs.

2. **``_is_ip_literal`` direct tests** — verify the helper
   distinguishes IP literals from hostnames.

3. **``assert_url_allowed`` IP-literal blocklist** — extend the
   allowlist with a private IP and verify ``assert_url_allowed``
   rejects it (defense-in-depth: even explicit allowlist extension
   cannot bypass the blocklist).

4. **``assert_url_allowed`` loopback exemption** — verify that
   ``127.0.0.1`` and ``::1`` (in the default allowlist) are NOT
   rejected by the SSRF check (local dev opt-in).

5. **``assert_url_allowed`` DNS-rebinding defense** — mock
   ``socket.getaddrinfo`` to return a private IP for an allowlisted
   hostname; verify ``assert_url_allowed`` rejects it.

6. **``assert_url_allowed`` DNS failure is non-fatal** — mock
   ``socket.getaddrinfo`` to raise ``gaierror``; verify
   ``assert_url_allowed`` does NOT reject (the URL is allowed; the
   HTTP layer will surface the DNS error in the normal way).

7. **``check_dns_rebinding=False`` skips the resolution** — verify
   the kwarg disables the post-resolution check (IP-literal blocklist
   still runs).
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest
from voice_typer.server._secrets import (
    _is_ip_literal,
    _is_private_ip,
    _user_extensions,
    assert_url_allowed,
    extend_url_allowlist,
)

# ---------------------------------------------------------------------------
# _is_private_ip — direct tests
# ---------------------------------------------------------------------------


class TestIsPrivateIp:
    """FR-25: ``_is_private_ip`` must return True for private/reserved
    IPs and False for public IPs."""

    # ── RFC 1918 private ranges ──
    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.1",
            "10.255.255.255",
            "10.1.2.3",
            "172.16.0.1",
            "172.31.255.255",
            "172.16.0.0",
            "172.20.30.40",
            "192.168.1.1",
            "192.168.0.0",
            "192.168.255.255",
        ],
    )
    def test_rfc1918_private_ranges_rejected(self, ip):
        assert _is_private_ip(ip) is True, (
            f"FR-25: RFC 1918 private IP {ip!r} should be rejected by _is_private_ip (SSRF defense)."
        )

    # ── Link-local (169.254/16, including cloud metadata endpoint) ──
    @pytest.mark.parametrize(
        "ip",
        [
            "169.254.169.254",  # AWS / GCP / Azure cloud metadata endpoint
            "169.254.0.1",
            "169.254.255.255",
        ],
    )
    def test_link_local_rejected(self, ip):
        assert _is_private_ip(ip) is True, (
            f"FR-25: link-local IP {ip!r} should be rejected by "
            f"_is_private_ip. The cloud metadata endpoint "
            f"169.254.169.254 is the primary SSRF target — if this "
            f"check fails, an attacker can exfiltrate the API key to "
            f"the cloud metadata endpoint."
        )

    # ── Loopback (127/8) ──
    @pytest.mark.parametrize("ip", ["127.0.0.1", "127.0.0.2", "127.255.255.255"])
    def test_ipv4_loopback_rejected(self, ip):
        assert _is_private_ip(ip) is True, f"FR-25: loopback IP {ip!r} should be rejected by _is_private_ip."

    # ── Unspecified (0.0.0.0) ──
    @pytest.mark.parametrize("ip", ["0.0.0.0"])
    def test_unspecified_rejected(self, ip):
        assert _is_private_ip(ip) is True

    # ── IPv6 private / loopback / link-local / ULA ──
    @pytest.mark.parametrize(
        "ip",
        [
            "::1",  # IPv6 loopback
            "::",  # IPv6 unspecified
            "fe80::1",  # IPv6 link-local
            "fe80::1234",  # IPv6 link-local
            "fc00::1",  # IPv6 ULA (fc00::/7)
            "fd00::1",  # IPv6 ULA (fc00::/7)
            "fd12:3456:789a:1::1",  # IPv6 ULA
        ],
    )
    def test_ipv6_private_ranges_rejected(self, ip):
        assert _is_private_ip(ip) is True, (
            f"FR-25: IPv6 private/reserved IP {ip!r} should be rejected by _is_private_ip."
        )

    # ── Public IPs (must NOT be rejected) ──
    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",  # Google DNS
            "1.1.1.1",  # Cloudflare DNS
            "172.15.0.1",  # Just outside 172.16/12
            "172.32.0.1",  # Just outside 172.16/12
            "192.167.0.1",  # Just outside 192.168/16
            "11.0.0.1",  # Just outside 10/8
            "2606:4700:4700::1111",  # Cloudflare IPv6 DNS
            "2001:4860:4860::8888",  # Google IPv6 DNS
        ],
    )
    def test_public_ips_accepted(self, ip):
        assert _is_private_ip(ip) is False, (
            f"FR-25: public IP {ip!r} should NOT be rejected by "
            f"_is_private_ip (false positive would block legitimate "
            f"cloud providers)."
        )

    # ── Non-IP strings ──
    @pytest.mark.parametrize(
        "ip",
        ["", "not-an-ip", "api.openai.com", "localhost", "256.256.256.256"],
    )
    def test_non_ip_strings_return_false(self, ip):
        """Non-IP strings must return False (callers should check
        ``_is_ip_literal`` first to distinguish "not an IP" from
        "public IP")."""
        assert _is_private_ip(ip) is False


# ---------------------------------------------------------------------------
# _is_ip_literal — direct tests
# ---------------------------------------------------------------------------


class TestIsIpLiteral:
    """FR-25: ``_is_ip_literal`` distinguishes IP literals from
    hostnames."""

    @pytest.mark.parametrize(
        "host",
        [
            "10.0.0.1",
            "127.0.0.1",
            "8.8.8.8",
            "169.254.169.254",
            "::1",
            "fe80::1",
            "2001:4860:4860::8888",
        ],
    )
    def test_ip_literals_detected(self, host):
        assert _is_ip_literal(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "api.openai.com",
            "localhost",
            "api.groq.com",
            "",
            "not-a-host-or-ip",
            "256.256.256.256",  # invalid IPv4
            "gg::1",  # invalid IPv6
        ],
    )
    def test_non_ip_literals_rejected(self, host):
        assert _is_ip_literal(host) is False


# ---------------------------------------------------------------------------
# assert_url_allowed — IP-literal blocklist (allowlisted private IP rejected)
# ---------------------------------------------------------------------------


class TestAssertUrlAllowedIpLiteralBlocklist:
    """FR-25: even if a private IP is explicitly added to the
    allowlist via ``extend_url_allowlist``, ``assert_url_allowed``
    must reject it (defense-in-depth against an attacker who tricks
    the user into adding a private IP)."""

    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.5",
            "172.16.0.1",
            "192.168.1.50",
            "169.254.169.254",  # cloud metadata endpoint
            "0.0.0.0",
        ],
    )
    def test_allowlisted_private_ip_rejected(self, ip):
        """Extend the allowlist with a private IP and verify
        ``assert_url_allowed`` still rejects it."""
        try:
            extend_url_allowlist([ip], caller="test")
            with pytest.raises(ValueError, match="private/reserved IP literal"):
                assert_url_allowed(f"https://{ip}/path", check_dns_rebinding=False)
        finally:
            _user_extensions.discard(ip)

    def test_allowlisted_public_ip_accepted(self):
        """Sanity check: a public IP that's been allowlisted is
        accepted (the blocklist doesn't over-block public IPs)."""
        public_ip = "8.8.8.8"
        try:
            extend_url_allowlist([public_ip], caller="test")
            # Must NOT raise.
            assert_url_allowed(f"https://{public_ip}/path", check_dns_rebinding=False)
        finally:
            _user_extensions.discard(public_ip)

    def test_loopback_ip_not_rejected_by_ssrf_check(self):
        """FR-25: loopback IPs (127.0.0.1, ::1) are in the default
        allowlist for local dev and are EXEMPTED from the SSRF check.
        They must NOT be rejected as "private IP literals"."""
        # 127.0.0.1 is in the default allowlist; the SSRF check
        # exempts loopback.  This call must NOT raise (assuming
        # allow_loopback_http=True for the HTTP scheme, or use HTTPS).
        assert_url_allowed("https://127.0.0.1/path")
        assert_url_allowed("https://[::1]/path")

    def test_loopback_http_with_opt_in_not_rejected_by_ssrf_check(self):
        """Same as above but with HTTP scheme + allow_loopback_http=True
        (the local-dev opt-in path).  The SSRF check exempts loopback."""
        assert_url_allowed(
            "http://127.0.0.1:11434/path",
            allow_loopback_http=True,
        )


# ---------------------------------------------------------------------------
# assert_url_allowed — DNS-rebinding defense (hostname resolves to private IP)
# ---------------------------------------------------------------------------


class TestAssertUrlAllowedDnsRebindingDefense:
    """FR-25: for allowlisted hostnames, ``assert_url_allowed`` resolves
    via ``socket.getaddrinfo`` and rejects if ANY resolved IP is
    private/reserved.  Catches DNS rebinding + ``/etc/hosts`` tampering."""

    def test_hostname_resolving_to_private_ip_rejected(self):
        """If ``api.openai.com`` (in the default allowlist) resolves to
        a private IP (simulated via mocked getaddrinfo), the URL must
        be rejected."""
        # Mock getaddrinfo to return 10.0.0.5 (private) for any host.
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0)),
        ]
        with (
            patch("socket.getaddrinfo", return_value=fake_sockaddrs),
            pytest.raises(ValueError, match="resolves to private/reserved IP"),
        ):
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_hostname_resolving_to_cloud_metadata_rejected(self):
        """If an allowlisted hostname resolves to 169.254.169.254
        (cloud metadata endpoint), the URL must be rejected."""
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0)),
        ]
        with (
            patch("socket.getaddrinfo", return_value=fake_sockaddrs),
            pytest.raises(ValueError, match="169.254.169.254"),
        ):
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_hostname_resolving_to_public_ip_accepted(self):
        """If an allowlisted hostname resolves to a public IP, the URL
        must be accepted (no false positive)."""
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("104.18.6.192", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_sockaddrs):
            # Must NOT raise.
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_hostname_resolving_to_mix_of_public_and_private_rejected(self):
        """If an allowlisted hostname resolves to BOTH a public IP and
        a private IP (e.g. DNS round-robin with a poisoned record),
        the URL must be rejected (ANY private IP triggers rejection)."""
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("104.18.6.192", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0)),
        ]
        with (
            patch("socket.getaddrinfo", return_value=fake_sockaddrs),
            pytest.raises(ValueError, match="resolves to private/reserved IP"),
        ):
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_ipv6_resolution_to_private_ip_rejected(self):
        """If an allowlisted hostname resolves to an IPv6 private IP
        (e.g. ``fc00::1`` ULA), the URL must be rejected."""
        fake_sockaddrs = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fc00::1", 0, 0, 0)),
        ]
        with (
            patch("socket.getaddrinfo", return_value=fake_sockaddrs),
            pytest.raises(ValueError, match="fc00::1"),
        ):
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_dns_failure_is_non_fatal(self):
        """If ``socket.getaddrinfo`` raises ``gaierror`` (offline test
        env, no DNS), the URL must NOT be rejected (best-effort).  The
        HTTP layer will surface the DNS error in the normal way."""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("no DNS")):
            # Must NOT raise.
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_check_dns_rebinding_false_skips_resolution(self):
        """``check_dns_rebinding=False`` disables the post-resolution
        check entirely.  The IP-literal blocklist still runs for IP
        literals, but for hostnames no resolution is attempted."""
        # Patch getaddrinfo to raise if called — verify it's NOT called.
        with patch("socket.getaddrinfo", side_effect=AssertionError("getaddrinfo should not be called")):
            # Must NOT raise (no resolution attempted).
            assert_url_allowed(
                "https://api.openai.com/v1/chat",
                check_dns_rebinding=False,
            )

    def test_check_dns_rebinding_false_still_blocks_ip_literals(self):
        """Even with ``check_dns_rebinding=False``, the IP-literal
        blocklist still runs (the IP-literal check is separate from
        the DNS-rebinding check)."""
        try:
            extend_url_allowlist(["10.0.0.5"], caller="test")
            with pytest.raises(ValueError, match="private/reserved IP literal"):
                assert_url_allowed(
                    "https://10.0.0.5/path",
                    check_dns_rebinding=False,
                )
        finally:
            _user_extensions.discard("10.0.0.5")


# ---------------------------------------------------------------------------
# assert_url_allowed — loopback exemption edge cases
# ---------------------------------------------------------------------------


class TestAssertUrlAllowedLoopbackExemption:
    """FR-25: loopback IPs (127.0.0.1, ::1) are in the default
    allowlist for local dev and are EXEMPTED from the SSRF check
    (the user has already opted in to localhost via the allowlist)."""

    def test_loopback_ipv4_skips_ssrf_check(self):
        """127.0.0.1 is in _LOOPBACK_HOSTS and is exempted from the
        SSRF check (otherwise the SSRF check would reject it as a
        loopback IP).  This must NOT raise."""
        # Use HTTPS to avoid the require_https check.
        assert_url_allowed("https://127.0.0.1/path")

    def test_loopback_ipv6_skips_ssrf_check(self):
        """::1 is in _LOOPBACK_HOSTS and is exempted.  Must NOT raise."""
        assert_url_allowed("https://[::1]/path")

    def test_loopback_named_skips_ssrf_check(self):
        """``localhost`` is in _LOOPBACK_HOSTS and is exempted.  But
        ``localhost`` is NOT an IP literal, so the DNS-rebinding path
        runs — we mock getaddrinfo to return 127.0.0.1 (loopback) and
        verify the URL is accepted (loopback IPs in the resolution
        are NOT rejected because the host is in _LOOPBACK_HOSTS)."""
        # Actually, the loopback exemption is keyed on the HOST being
        # in _LOOPBACK_HOSTS, not on the resolved IPs.  So even if
        # localhost resolves to a non-loopback IP, the exemption
        # applies (the user has opted in to localhost).  But
        # ``localhost`` typically resolves to 127.0.0.1 anyway.
        # Either way, this must NOT raise.
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_sockaddrs):
            assert_url_allowed(
                "http://localhost:11434/v1/chat",
                allow_loopback_http=True,
            )


# ---------------------------------------------------------------------------
# assert_url_allowed — defense-in-depth regression guards
# ---------------------------------------------------------------------------


class TestAssertUrlAllowedSsrfDefenseInDepth:
    """FR-25: defense-in-depth regression guards.  Verify the SSRF
    check runs AFTER the allowlist + HTTPS checks (not before), so
    the existing allowlist + HTTPS error messages are preserved for
    non-SSRF rejections."""

    def test_allowlist_check_runs_before_ssrf_check(self):
        """A non-allowlisted hostname must be rejected with the
        allowlist error (NOT the SSRF error), even if the hostname
        would also fail the SSRF check."""
        # evil.example.com is NOT in the allowlist → allowlist error.
        # We mock getaddrinfo to raise AssertionError if called —
        # proving the SSRF check (which calls getaddrinfo) does NOT
        # run when the allowlist check already fails.
        with (
            patch(
                "socket.getaddrinfo",
                side_effect=AssertionError("getaddrinfo should not be called for non-allowlisted host"),
            ),
            pytest.raises(ValueError, match="not in the trusted allowlist"),
        ):
            assert_url_allowed("https://evil.example.com/steal")

    def test_https_check_runs_before_ssrf_check(self):
        """A non-loopback HTTP URL must be rejected with the HTTPS
        error (NOT the SSRF error), even if the host would also fail
        the SSRF check."""
        # api.openai.com is in the allowlist but uses HTTP → HTTPS error.
        # We mock getaddrinfo to raise AssertionError if called —
        # proving the SSRF check does NOT run when the HTTPS check
        # already fails.
        with (
            patch(
                "socket.getaddrinfo", side_effect=AssertionError("getaddrinfo should not be called for HTTP rejection")
            ),
            pytest.raises(ValueError, match="must use HTTPS"),
        ):
            assert_url_allowed("http://api.openai.com/v1/chat")

    def test_ssrf_check_runs_for_allowlisted_https_hostname(self):
        """For an allowlisted HTTPS hostname, the SSRF check runs
        (calls getaddrinfo).  We mock getaddrinfo to return a public
        IP and verify the URL is accepted (no false positive)."""
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("104.18.6.192", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_sockaddrs) as mock_gai:
            assert_url_allowed("https://api.openai.com/v1/chat")
            assert mock_gai.called, (
                "FR-25: getaddrinfo should be called for an "
                "allowlisted HTTPS hostname (the DNS-rebinding "
                "defense runs after the allowlist + HTTPS checks)."
            )
