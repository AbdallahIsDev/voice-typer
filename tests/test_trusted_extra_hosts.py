"""
XZ-SEC-05 end-to-end tests: user-configured trusted hosts.
XZ-SEC-05: users running self-hosted LLM/ASR endpoints on non-loopback
"""

from __future__ import annotations

import json

import pytest
from voice_typer.server._secrets import (
    _user_extensions,
    assert_url_allowed,
    extend_url_allowlist,
    get_url_allowlist,
    is_url_allowed,
)
from voice_typer.server.config_validators import validate_config_update

# Reuse the split-IPC-suite fakes (MockApp exposes the config-mutation
from tests.server.conftest import IPCServer, MockApp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_user_extensions():
    """Reset the module-global allowlist extension set around each test."""
    _user_extensions.clear()
    yield
    _user_extensions.clear()


@pytest.fixture
def server_app():
    """
    IPCServer backed by MockApp (same shape as the split-suite
    XZ-SEC-05 tests stay self-contained).
    """
    app = MockApp()
    return IPCServer(app), app


class TestConfigLoadReappliesTrustedHosts:
    def test_load_reapplies_persisted_hosts(self, tmp_config_dir):
        """runtime allowlist on load, the persisted config path (finding's"""
        (tmp_config_dir / "config.json").write_text(
            json.dumps({"schema_version": 3, "trusted_extra_hosts": ["my-vllm.lan"]}),
            encoding="utf-8",
        )

        from voice_typer.server.config import Config

        cfg = Config.load()
        assert cfg.trusted_extra_hosts == ["my-vllm.lan"]
        # End-to-end: the user-configured host now passes assert_url_allowed.
        assert_url_allowed("https://my-vllm.lan/v1/chat/completions")  # must not raise
        assert is_url_allowed("https://my-vllm.lan/v1")

    def test_load_without_hosts_leaves_allowlist_unchanged(self, tmp_config_dir):
        """No trusted_extra_hosts → nothing is added to the allowlist."""
        (tmp_config_dir / "config.json").write_text(json.dumps({"schema_version": 3}), encoding="utf-8")

        from voice_typer.server.config import Config

        Config.load()
        assert not is_url_allowed("https://my-vllm.lan/v1")

    def test_ssrf_blocklist_still_applies_after_load(self, tmp_config_dir):
        """A user cannot bypass SSRF defense by persisting a private IP."""
        (tmp_config_dir / "config.json").write_text(
            json.dumps({"schema_version": 3, "trusted_extra_hosts": ["192.168.1.1"]}),
            encoding="utf-8",
        )

        from voice_typer.server.config import Config

        Config.load()
        with pytest.raises(ValueError):
            assert_url_allowed("https://192.168.1.1/v1")


class TestAddTrustedEndpointIpc:
    def test_adds_host_and_persists(self, server_app):
        server, mock_app = server_app
        """The add_trusted_endpoint IPC command extends the runtime
        allowlist AND persists the host to config.trusted_extra_hosts."""
        result = server._dispatch({"id": 1, "type": "add_trusted_endpoint", "data": {"host": "my-vllm.lan"}})
        assert result["type"] == "ack"
        assert result["data"]["host"] == "my-vllm.lan"
        assert mock_app.config.trusted_extra_hosts == ["my-vllm.lan"]
        assert_url_allowed("https://my-vllm.lan/v1")  # must not raise

    def test_normalizes_host_with_port_and_case(self, server_app):
        server, mock_app = server_app
        result = server._dispatch(
            {
                "id": 1,
                "type": "add_trusted_endpoint",
                "data": {"host": "My-Vllm.Lan:8443"},
            }
        )
        assert result["type"] == "ack"
        assert result["data"]["host"] == "my-vllm.lan"
        assert mock_app.config.trusted_extra_hosts == ["my-vllm.lan"]

    def test_idempotent(self, server_app):
        server, mock_app = server_app
        for _ in range(2):
            server._dispatch({"id": 1, "type": "add_trusted_endpoint", "data": {"host": "my-vllm.lan"}})
        assert mock_app.config.trusted_extra_hosts == ["my-vllm.lan"]

    def test_rejects_invalid_payload(self, server_app):
        server, mock_app = server_app
        for bad in (None, {"host": 42}, {"host": "https://my-vllm.lan"}, {"host": "a b c"}):
            result = server._dispatch({"id": 1, "type": "add_trusted_endpoint", "data": bad})
            assert result["type"] == "error", f"expected error for {bad!r}, got {result!r}"
        assert mock_app.config.__dict__.get("trusted_extra_hosts", []) == []
        assert not is_url_allowed("https://my-vllm.lan/v1")


class TestSetConfigReappliesTrustedHosts:
    def test_set_config_with_trusted_hosts_extends_allowlist(self, server_app):
        server, mock_app = server_app
        """set_config carrying trusted_extra_hosts must re-apply the
        allowlist immediately (not only on next launch)."""
        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {"trusted_extra_hosts": ["my-vllm.lan"]},
            }
        )
        assert result["type"] == "ack"
        assert mock_app.config.trusted_extra_hosts == ["my-vllm.lan"]
        assert_url_allowed("https://my-vllm.lan/v1")

    def test_set_config_validator_rejects_invalid_hosts(self):
        validated, errors = validate_config_update({"trusted_extra_hosts": ["my-vllm.lan", "bad host"]})
        assert errors, "invalid host list must produce validation errors"
        assert "bad host" not in validated.get("trusted_extra_hosts", [])

        validated, errors = validate_config_update({"trusted_extra_hosts": "not-a-list"})
        assert errors, "non-list value must be rejected"
        assert "trusted_extra_hosts" not in validated


class TestAllowlistHelpers:
    def test_extend_url_allowlist_accepts_host(self):
        extend_url_allowlist(["my-vllm.lan"], caller="test")
        assert_url_allowed("https://my-vllm.lan/v1")


class TestIpv6Allowlisting:
    """HU-35 follow-up: IPv6 literals survive port-stripping and can be"""

    def test_config_validator_accepts_public_ipv6(self):
        """A public IPv6 literal is accepted by the config validator"""
        validated, errors = validate_config_update({"trusted_extra_hosts": ["2606:4700:4700::1111"]})
        assert not errors, f"public IPv6 must validate; got errors: {errors!r}"
        assert validated["trusted_extra_hosts"] == ["2606:4700:4700::1111"]

    def test_config_validator_accepts_bracketed_ipv6_with_port(self):
        """The bracketed-with-port form passes validation (its inner"""
        validated, errors = validate_config_update({"trusted_extra_hosts": ["[2606:4700:4700::1111]:8443"]})
        assert not errors, f"bracketed IPv6 must validate; got errors: {errors!r}"
        extend_url_allowlist(validated["trusted_extra_hosts"], caller="test")
        assert "2606:4700:4700::1111" in get_url_allowlist()

    def test_config_validator_accepts_private_ipv6_but_ssrf_blocks_it(self):
        """valid bare host) but ``assert_url_allowed`` still rejects it via"""
        validated, errors = validate_config_update({"trusted_extra_hosts": ["fc00::1"]})
        assert not errors, f"private IPv6 must validate as a host; got errors: {errors!r}"
        extend_url_allowlist(validated["trusted_extra_hosts"], caller="test")
        with pytest.raises(ValueError, match="private/reserved IP literal"):
            assert_url_allowed(
                "https://[fc00::1]/v1",
                field_name="cloud_api_url",
                client_name="cloud/test",
            )

    def test_config_validator_rejects_colon_hostname(self):
        """A NON-IPv6 host containing a colon is still rejected, IPv6 is"""
        validated, errors = validate_config_update({"trusted_extra_hosts": ["bad:host:name"]})
        assert errors, "colon-bearing non-IPv6 hostname must be rejected"
        assert "trusted_extra_hosts" not in validated

    def test_add_trusted_endpoint_accepts_public_ipv6(self, server_app):
        """The add_trusted_endpoint IPC handler accepts a public IPv6"""
        server, mock_app = server_app
        result = server._dispatch(
            {
                "id": 1,
                "type": "add_trusted_endpoint",
                "data": {"host": "2606:4700:4700::1111"},
            }
        )
        assert result["type"] == "ack"
        assert result["data"]["host"] == "2606:4700:4700::1111"
        assert mock_app.config.trusted_extra_hosts == ["2606:4700:4700::1111"]
        # End-to-end: the public IPv6 URL now passes assert_url_allowed.
        assert_url_allowed(
            "https://[2606:4700:4700::1111]/v1",
            field_name="cloud_api_url",
            client_name="cloud/test",
        )

    def test_add_trusted_endpoint_bracketed_ipv6_with_port(self, server_app):
        """``[2606:4700:4700::1111]:8443`` normalizes to the bare literal."""
        server, mock_app = server_app
        result = server._dispatch(
            {
                "id": 1,
                "type": "add_trusted_endpoint",
                "data": {"host": "[2606:4700:4700::1111]:8443"},
            }
        )
        assert result["type"] == "ack"
        assert result["data"]["host"] == "2606:4700:4700::1111"
        assert mock_app.config.trusted_extra_hosts == ["2606:4700:4700::1111"]

    def test_add_trusted_endpoint_rejects_colon_hostname(self, server_app):
        """A non-IPv6 host containing colons is still rejected."""
        server, mock_app = server_app
        result = server._dispatch(
            {
                "id": 1,
                "type": "add_trusted_endpoint",
                "data": {"host": "bad:host:name"},
            }
        )
        assert result["type"] == "error", f"colon hostname must be rejected; got {result!r}"
        assert mock_app.config.__dict__.get("trusted_extra_hosts", []) == []
