"""§8.6: Corporate networks / proxies + SSRF protection."""

from __future__ import annotations

import pytest
from voice_typer.server.service import offline_pack


class TestProxyEnv:
    """§8.6, proxy env vars are respected."""

    def test_reads_http_proxy(self, monkeypatch):
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.example:8080")
        env = offline_pack.proxy_env()
        assert env.get("HTTP_PROXY") == "http://proxy.corp.example:8080"

    def test_reads_https_proxy(self, monkeypatch):
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:8080")
        env = offline_pack.proxy_env()
        assert env.get("HTTPS_PROXY") == "http://proxy.corp.example:8080"

    def test_reads_lowercase_variants(self, monkeypatch):
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.setenv("http_proxy", "http://lower.proxy:3128")
        monkeypatch.setenv("https_proxy", "http://lower.proxy:3129")
        env = offline_pack.proxy_env()
        assert env.get("http_proxy") == "http://lower.proxy:3128"
        assert env.get("https_proxy") == "http://lower.proxy:3129"

    def test_no_proxy_env_returns_empty(self, monkeypatch):
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.delenv(key, raising=False)
        env = offline_pack.proxy_env()
        assert env == {}


class TestAssertPackUrlAllowed:
    """§8.6 + §8.10 (SSRF), pack URL allowlist + IP-literal blocklist."""

    def test_https_github_url_allowed(self):
        # Should not raise.
        offline_pack.assert_offline_pack_url_allowed(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip"
        )

    def test_https_github_objects_url_allowed(self):
        offline_pack.assert_offline_pack_url_allowed(
            "https://objects.githubusercontent.com/github-production-release-asset/foo"
        )

    def test_http_non_loopback_rejected(self):
        # GitHub over HTTP (cleartext) must be rejected.
        with pytest.raises(ValueError, match="must use HTTPS"):
            offline_pack.assert_offline_pack_url_allowed(
                "http://github.com/owner/repo/releases/download/v1/offline_pack.zip"
            )

    def test_private_ip_literal_rejected(self, monkeypatch):
        """Even if a private IP is added to the allowlist, the SSRF"""
        with pytest.raises(ValueError):
            offline_pack.assert_offline_pack_url_allowed("https://10.0.0.5/offline_pack.zip")

    def test_unknown_host_rejected(self):
        with pytest.raises(ValueError, match="not in the trusted allowlist"):
            offline_pack.assert_offline_pack_url_allowed("https://evil.example.com/offline_pack.zip")

    def test_loopback_https_allowed(self):
        # ``localhost`` is in the default allowlist; HTTPS is OK.
        offline_pack.assert_offline_pack_url_allowed("https://localhost:8080/test-pack")

    def test_empty_url_rejected(self):
        with pytest.raises(ValueError):
            offline_pack.assert_offline_pack_url_allowed("")


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
