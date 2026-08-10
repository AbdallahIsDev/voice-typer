"""Tests for the ``VOICE_TYPER_TRUSTED_HOSTS`` env-var bootstrap in
``voice_typer.server._secrets``.

Covers the partial XZ-SEC-05 fix: a production caller of
``extend_url_allowlist`` that loads user-configured trusted hosts from
the environment at module-load time. This gives users a config path
for self-hosted LLM/ASR endpoints on non-loopback hosts without
requiring the full IPC+config+UI wiring proposed by XZ-SEC-05.
"""

from __future__ import annotations

import pytest
from voice_typer.server._secrets import (
    _ENV_TRUSTED_HOSTS_VAR,
    _load_env_allowlist_extensions,
    _normalize_host,
    _user_extensions,
    assert_url_allowed,
    extend_url_allowlist,
    get_url_allowlist,
    is_url_allowed,
)

ENV_VAR = _ENV_TRUSTED_HOSTS_VAR


@pytest.fixture(autouse=True)
def _clean_user_extensions():
    """Reset ``_user_extensions`` before AND after every test so the
    module-global state never leaks across tests (the bootstrap is
    idempotent, but tests that exercise failure paths may leave partial
    state)."""
    _user_extensions.clear()
    yield
    _user_extensions.clear()


@pytest.fixture(autouse=True)
def _clear_env_var(monkeypatch):
    """Ensure ``VOICE_TYPER_TRUSTED_HOSTS`` is unset for every test
    unless the test explicitly sets it."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    yield


class TestEnvVarName:
    def test_env_var_name_matches_documented_constant(self):
        """The env-var name is a stable public contract."""
        assert _ENV_TRUSTED_HOSTS_VAR == "VOICE_TYPER_TRUSTED_HOSTS"


class TestNormalizeHost:
    def test_lowercases(self):
        assert _normalize_host("My-Host.Example.COM") == "my-host.example.com"

    def test_strips_port(self):
        assert _normalize_host("my-host.example.com:8080") == "my-host.example.com"

    def test_strips_whitespace(self):
        assert _normalize_host("  my-host.example.com  ") == "my-host.example.com"

    def test_empty_string(self):
        assert _normalize_host("") == ""

    def test_whitespace_only(self):
        assert _normalize_host("   ") == ""

    def test_none_safety(self):
        # The function is type-annotated as ``str``; passing None is a
        # type error but we want it to be safe at runtime (no crash).
        # The guard ``if not h`` handles None because ``not None`` is True.
        assert _normalize_host(None) == ""  # type: ignore[arg-type]


class TestLoadEnvAllowlistExtensions:
    def test_unset_env_var_returns_empty_list(self):
        """When the env var is unset, no hosts are added."""
        result = _load_env_allowlist_extensions()
        assert result == []
        assert _user_extensions == set()

    def test_empty_env_var_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "")
        result = _load_env_allowlist_extensions()
        assert result == []
        assert _user_extensions == set()

    def test_whitespace_only_env_var_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "   ")
        result = _load_env_allowlist_extensions()
        assert result == []
        assert _user_extensions == set()

    def test_single_host_added(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan")
        result = _load_env_allowlist_extensions()
        assert result == ["my-vllm.lan"]
        assert "my-vllm.lan" in _user_extensions
        assert "my-vllm.lan" in get_url_allowlist()

    def test_multiple_hosts_comma_separated(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan,my-other.lan,third.example.com")
        result = _load_env_allowlist_extensions()
        assert set(result) == {"my-vllm.lan", "my-other.lan", "third.example.com"}
        assert _user_extensions == {
            "my-vllm.lan",
            "my-other.lan",
            "third.example.com",
        }

    def test_whitespace_around_hosts_tolerated(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "  my-vllm.lan  ,  my-other.lan  ")
        result = _load_env_allowlist_extensions()
        assert set(result) == {"my-vllm.lan", "my-other.lan"}

    def test_empty_entries_in_list_dropped(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan,,  ,my-other.lan")
        result = _load_env_allowlist_extensions()
        assert set(result) == {"my-vllm.lan", "my-other.lan"}

    def test_host_port_stripped(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan:8080")
        result = _load_env_allowlist_extensions()
        assert result == ["my-vllm.lan"]
        assert "my-vllm.lan" in _user_extensions

    def test_host_lowercased(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "My-VLLM.LAN")
        result = _load_env_allowlist_extensions()
        assert result == ["my-vllm.lan"]
        assert "my-vllm.lan" in _user_extensions
        # Original case must NOT be present.
        assert "My-VLLM.LAN" not in _user_extensions

    def test_idempotent_on_repeated_calls(self, monkeypatch):
        """Calling the loader twice with the same env var must not
        duplicate entries or raise."""
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan,my-other.lan")
        first = _load_env_allowlist_extensions()
        second = _load_env_allowlist_extensions()
        assert set(first) == {"my-vllm.lan", "my-other.lan"}
        assert set(second) == {"my-vllm.lan", "my-other.lan"}
        # No duplicates in the underlying set.
        assert _user_extensions == {"my-vllm.lan", "my-other.lan"}


class TestLoadEnvAllowlistExtensionsAuditLog:
    def test_audit_log_emitted_with_env_caller(self, monkeypatch, caplog):
        """When hosts are added, a WARNING is emitted whose message
        includes the env-var caller identifier (so operators can
        trace env-var-driven allowlist extensions in production logs)."""
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan")
        with caplog.at_level("WARNING", logger="voice_typer.server._secrets"):
            _load_env_allowlist_extensions()
        joined = " ".join(r.message for r in caplog.records)
        assert "[URL-Allowlist]" in joined
        assert f"env:{ENV_VAR}" in joined
        assert "my-vllm.lan" in joined

    def test_no_audit_log_when_env_unset(self, monkeypatch, caplog):
        """When the env var is unset, no audit record is emitted at all
        (the loader short-circuits before calling extend_url_allowlist)."""
        monkeypatch.delenv(ENV_VAR, raising=False)
        with caplog.at_level("INFO", logger="voice_typer.server._secrets"):
            _load_env_allowlist_extensions()
        allowlist_records = [r for r in caplog.records if "[URL-Allowlist]" in r.message]
        assert allowlist_records == []


class TestAssertUrlAllowedAcceptsEnvHosts:
    def test_self_hosted_url_passes_after_env_load(self, monkeypatch):
        """End-to-end: a self-hosted URL that would normally be
        rejected by assert_url_allowed passes after the env-var
        bootstrap loads the host."""
        url = "https://my-vllm.lan/v1/chat/completions"
        # Without the env-var extension, this URL must be rejected.
        with pytest.raises(ValueError, match="not in the trusted allowlist"):
            assert_url_allowed(url)

        # Now load the host via the env var.
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan")
        _load_env_allowlist_extensions()

        # The URL must now pass (no exception raised).
        assert_url_allowed(url)
        assert is_url_allowed(url)

    def test_https_required_for_non_loopback_env_host(self, monkeypatch):
        """Even with the host in the allowlist, plain HTTP to a
        non-loopback host is still rejected (require_https=True by
        default)."""
        monkeypatch.setenv(ENV_VAR, "my-vllm.lan")
        _load_env_allowlist_extensions()
        with pytest.raises(ValueError, match="must use HTTPS for non-loopback"):
            assert_url_allowed("http://my-vllm.lan/v1/chat")


class TestSSRFDefenseStillEnforced:
    """The env-var bootstrap adds the host to the textual allowlist,
    but the SSRF IP-literal blocklist must still reject private IPs
    added this way (defense-in-depth)."""

    def test_private_ip_added_via_env_still_rejected(self, monkeypatch):
        """Adding a private IP via the env var must NOT bypass the
        SSRF blocklist — assert_url_allowed must still raise."""
        monkeypatch.setenv(ENV_VAR, "10.0.0.5")
        _load_env_allowlist_extensions()
        # The host IS in the textual allowlist now.
        assert "10.0.0.5" in get_url_allowlist()
        # But assert_url_allowed still rejects it (SSRF blocklist).
        with pytest.raises(ValueError, match="private/reserved IP literal"):
            assert_url_allowed(
                "https://10.0.0.5/path",
                check_dns_rebinding=False,
            )

    def test_loopback_ip_added_via_env_still_allowed(self, monkeypatch):
        """Loopback IPs are already in the default allowlist; adding
        them via env is a no-op but must not break anything."""
        monkeypatch.setenv(ENV_VAR, "127.0.0.1")
        _load_env_allowlist_extensions()
        assert "127.0.0.1" in get_url_allowlist()


class TestModuleLoadBootstrap:
    """The module-load bootstrap call at the bottom of _secrets.py
    invokes _load_env_allowlist_extensions() once on import. Because
    the module is already imported by the time tests run, the
    bootstrap has already fired with whatever env var was present at
    import time (typically unset). These tests verify the bootstrap
    function itself works correctly when called explicitly."""

    def test_bootstrap_function_is_callable(self):
        """The bootstrap function is exposed and callable."""
        # Should not raise; env var is unset (cleared by fixture).
        result = _load_env_allowlist_extensions()
        assert result == []

    def test_bootstrap_uses_correct_env_var_name(self, monkeypatch):
        """Sanity: the bootstrap reads the documented env var name."""
        monkeypatch.setenv(ENV_VAR, "bootstrap-test.example.com")
        result = _load_env_allowlist_extensions()
        assert result == ["bootstrap-test.example.com"]


class TestNoRegressionOnExtendUrlAllowlist:
    """The env-var bootstrap is now a production caller of
    ``extend_url_allowlist``. Make sure the function itself still
    works as before when called directly (existing tests in
    test_secrets.py and the tests/security/ package cover this in
    more depth; these are smoke tests)."""

    def test_extend_url_allowlist_still_adds_host(self):
        extend_url_allowlist(["direct-call.example.com"], caller="test")
        assert "direct-call.example.com" in get_url_allowlist()

    def test_extend_url_allowlist_still_normalizes(self):
        extend_url_allowlist(["Direct-Call.Example.COM:9090"])
        assert "direct-call.example.com" in get_url_allowlist()
