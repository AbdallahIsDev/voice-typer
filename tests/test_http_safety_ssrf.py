"""``_secrets.assert_url_allowed`` and the ``_is_private_ip`` helper."""

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


class TestIsPrivateIp:
    """FR-25: ``_is_private_ip`` must return True for private/reserved"""

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
            f"169.254.169.254 is the primary SSRF target, if this "
            f"check fails, an attacker can exfiltrate the API key to "
            f"the cloud metadata endpoint."
        )

    @pytest.mark.parametrize("ip", ["127.0.0.1", "127.0.0.2", "127.255.255.255"])
    def test_ipv4_loopback_rejected(self, ip):
        assert _is_private_ip(ip) is True, f"FR-25: loopback IP {ip!r} should be rejected by _is_private_ip."

    @pytest.mark.parametrize("ip", ["0.0.0.0"])
    def test_unspecified_rejected(self, ip):
        assert _is_private_ip(ip) is True

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

    @pytest.mark.parametrize(
        "ip",
        ["", "not-an-ip", "api.openai.com", "localhost", "256.256.256.256"],
    )
    def test_non_ip_strings_return_false(self, ip):
        """Non-IP strings must return False (callers should check"""
        assert _is_private_ip(ip) is False


class TestIsIpLiteral:
    """FR-25: ``_is_ip_literal`` distinguishes IP literals from"""

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


class TestAssertUrlAllowedIpLiteralBlocklist:
    """allowlist via ``extend_url_allowlist``, ``assert_url_allowed``"""

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
        """Extend the allowlist with a private IP and verify"""
        try:
            extend_url_allowlist([ip], caller="test")
            with pytest.raises(ValueError, match="private/reserved IP literal"):
                assert_url_allowed(f"https://{ip}/path", check_dns_rebinding=False)
        finally:
            _user_extensions.discard(ip)

    def test_allowlisted_public_ip_accepted(self):
        """Sanity check: a public IP that's been allowlisted is"""
        public_ip = "8.8.8.8"
        try:
            extend_url_allowlist([public_ip], caller="test")
            # Must NOT raise.
            assert_url_allowed(f"https://{public_ip}/path", check_dns_rebinding=False)
        finally:
            _user_extensions.discard(public_ip)

    def test_loopback_ip_not_rejected_by_ssrf_check(self):
        """
        FR-25: loopback IPs (127.0.0.1, ::1) are in the default
        They must NOT be rejected as "private IP literals".
        """
        # exempts loopback.  This call must NOT raise (assuming
        assert_url_allowed("https://127.0.0.1/path")
        assert_url_allowed("https://[::1]/path")

    def test_loopback_http_with_opt_in_not_rejected_by_ssrf_check(self):
        """Same as above but with HTTP scheme + allow_loopback_http=True"""
        assert_url_allowed(
            "http://127.0.0.1:11434/path",
            allow_loopback_http=True,
        )


class TestAssertUrlAllowedDnsRebindingDefense:
    """FR-25: for allowlisted hostnames, ``assert_url_allowed`` resolves"""

    def test_hostname_resolving_to_private_ip_rejected(self):
        """a private IP (simulated via mocked getaddrinfo), the URL must"""
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
        """If an allowlisted hostname resolves to 169.254.169.254"""
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0)),
        ]
        with (
            patch("socket.getaddrinfo", return_value=fake_sockaddrs),
            pytest.raises(ValueError, match="169.254.169.254"),
        ):
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_hostname_resolving_to_public_ip_accepted(self):
        """If an allowlisted hostname resolves to a public IP, the URL"""
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("104.18.6.192", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_sockaddrs):
            # Must NOT raise.
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_hostname_resolving_to_mix_of_public_and_private_rejected(self):
        """a private IP (e.g. DNS round-robin with a poisoned record),"""
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
        """If an allowlisted hostname resolves to an IPv6 private IP"""
        fake_sockaddrs = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fc00::1", 0, 0, 0)),
        ]
        with (
            patch("socket.getaddrinfo", return_value=fake_sockaddrs),
            pytest.raises(ValueError, match="fc00::1"),
        ):
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_dns_failure_is_non_fatal(self):
        """If ``socket.getaddrinfo`` raises ``gaierror`` (offline test"""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("no DNS")):
            # Must NOT raise.
            assert_url_allowed("https://api.openai.com/v1/chat")

    def test_check_dns_rebinding_false_skips_resolution(self):
        """``check_dns_rebinding=False`` disables the post-resolution"""
        # Patch getaddrinfo to raise if called, verify it's NOT called.
        with patch("socket.getaddrinfo", side_effect=AssertionError("getaddrinfo should not be called")):
            # Must NOT raise (no resolution attempted).
            assert_url_allowed(
                "https://api.openai.com/v1/chat",
                check_dns_rebinding=False,
            )

    def test_check_dns_rebinding_false_still_blocks_ip_literals(self):
        """Even with ``check_dns_rebinding=False``, the IP-literal"""
        try:
            extend_url_allowlist(["10.0.0.5"], caller="test")
            with pytest.raises(ValueError, match="private/reserved IP literal"):
                assert_url_allowed(
                    "https://10.0.0.5/path",
                    check_dns_rebinding=False,
                )
        finally:
            _user_extensions.discard("10.0.0.5")


class TestAssertUrlAllowedLoopbackExemption:
    """FR-25: loopback IPs (127.0.0.1, ::1) are in the default"""

    def test_loopback_ipv4_skips_ssrf_check(self):
        """loopback IP).  This must NOT raise."""
        # Use HTTPS to avoid the require_https check.
        assert_url_allowed("https://127.0.0.1/path")

    def test_loopback_ipv6_skips_ssrf_check(self):
        """::1 is in _LOOPBACK_HOSTS and is exempted.  Must NOT raise."""
        assert_url_allowed("https://[::1]/path")

    def test_loopback_named_skips_ssrf_check(self):
        """``localhost`` is in _LOOPBACK_HOSTS and is exempted.  But"""
        # Either way, this must NOT raise.
        fake_sockaddrs = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_sockaddrs):
            assert_url_allowed(
                "http://localhost:11434/v1/chat",
                allow_loopback_http=True,
            )


class TestAssertUrlAllowedSsrfDefenseInDepth:
    """FR-25: defense-in-depth regression guards.  Verify the SSRF"""

    def test_allowlist_check_runs_before_ssrf_check(self):
        """allowlist error (NOT the SSRF error), even if the hostname"""
        with (
            patch(
                "socket.getaddrinfo",
                side_effect=AssertionError("getaddrinfo should not be called for non-allowlisted host"),
            ),
            pytest.raises(ValueError, match="not in the trusted allowlist"),
        ):
            assert_url_allowed("https://evil.example.com/steal")

    def test_https_check_runs_before_ssrf_check(self):
        """A non-loopback HTTP URL must be rejected with the HTTPS"""
        with (
            patch(
                "socket.getaddrinfo", side_effect=AssertionError("getaddrinfo should not be called for HTTP rejection")
            ),
            pytest.raises(ValueError, match="must use HTTPS"),
        ):
            assert_url_allowed("http://api.openai.com/v1/chat")

    def test_ssrf_check_runs_for_allowlisted_https_hostname(self):
        """For an allowlisted HTTPS hostname, the SSRF check runs"""
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
