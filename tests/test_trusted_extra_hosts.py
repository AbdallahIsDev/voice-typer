"""XZ-SEC-05 end-to-end tests: user-configured trusted hosts.

XZ-SEC-05: users running self-hosted LLM/ASR endpoints on non-loopback
hosts previously had no in-app path to extend the URL allowlist —
``assert_url_allowed`` raised ``ValueError`` for every request to e.g.
``https://my-vllm.lan/v1``. The wiring that closed the gap:

  1. ``trusted_extra_hosts: list[str]`` on the Config dataclass,
     persisted to config.json and re-applied to the runtime allowlist
     on every ``Config.load()`` (config/__init__.py).
  2. ``add_trusted_endpoint`` IPC command (ConfigHandlersMixin) — adds a
     host to the runtime allowlist AND persists it (config_handlers.py).
  3. ``set_config`` with ``trusted_extra_hosts`` re-applies the allowlist
     immediately (config_handlers.py).
  4. Env-var bootstrap ``VOICE_TYPER_TRUSTED_HOSTS`` (process startup).

All paths remain subject to the SSRF IP-literal blocklist and the
DNS-rebinding check in ``_secrets.assert_url_allowed``.
"""

from __future__ import annotations

import json

import pytest
from voice_typer.server._secrets import (
    _user_extensions,
    assert_url_allowed,
    extend_url_allowlist,
    is_url_allowed,
)
from voice_typer.server.config_validators import validate_config_update

# Reuse the split-IPC-suite fakes (MockApp exposes the config-mutation
# lock + service-adjacent attributes the config handlers touch).
from tests.server.conftest import IPCServer, MockApp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_user_extensions():
    """Reset the module-global allowlist extension set around each test."""
    _user_extensions.clear()
    yield
    _user_extensions.clear()


@pytest.fixture
def server_app():
    """IPCServer backed by MockApp (same shape as the split-suite
    ``server`` + ``mock_app`` fixtures, but local to this module so the
    XZ-SEC-05 tests stay self-contained)."""
    app = MockApp()
    return IPCServer(app), app


class TestConfigLoadReappliesTrustedHosts:
    def test_load_reapplies_persisted_hosts(self, tmp_path, monkeypatch):
        """A config.json carrying ``trusted_extra_hosts`` must extend the
        runtime allowlist on load — the persisted config path (finding's
        'Re-apply on Config.load')."""
        (tmp_path / "config.json").write_text(
            json.dumps({"schema_version": 3, "trusted_extra_hosts": ["my-vllm.lan"]}),
            encoding="utf-8",
        )
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        from voice_typer.server.config import Config

        cfg = Config.load()
        assert cfg.trusted_extra_hosts == ["my-vllm.lan"]
        # End-to-end: the user-configured host now passes assert_url_allowed.
        assert_url_allowed("https://my-vllm.lan/v1/chat/completions")  # must not raise
        assert is_url_allowed("https://my-vllm.lan/v1")

    def test_load_without_hosts_leaves_allowlist_unchanged(self, tmp_path, monkeypatch):
        """No trusted_extra_hosts → nothing is added to the allowlist."""
        (tmp_path / "config.json").write_text(json.dumps({"schema_version": 3}), encoding="utf-8")
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        from voice_typer.server.config import Config

        Config.load()
        assert not is_url_allowed("https://my-vllm.lan/v1")

    def test_ssrf_blocklist_still_applies_after_load(self, tmp_path, monkeypatch):
        """A user cannot bypass SSRF defense by persisting a private IP."""
        (tmp_path / "config.json").write_text(
            json.dumps({"schema_version": 3, "trusted_extra_hosts": ["192.168.1.1"]}),
            encoding="utf-8",
        )
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        from voice_typer.server.config import Config

        Config.load()
        with pytest.raises(ValueError):
            assert_url_allowed("https://192.168.1.1/v1")


class TestAddTrustedEndpointIpc:
    def test_adds_host_and_persists(self, server_app):
        server, mock_app = server_app
        """The add_trusted_endpoint IPC command extends the runtime
        allowlist AND persists the host to config.trusted_extra_hosts."""
        result = server._dispatch(
            {"id": 1, "type": "add_trusted_endpoint", "data": {"host": "my-vllm.lan"}}
        )
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
            server._dispatch(
                {"id": 1, "type": "add_trusted_endpoint", "data": {"host": "my-vllm.lan"}}
            )
        assert mock_app.config.trusted_extra_hosts == ["my-vllm.lan"]

    def test_rejects_invalid_payload(self, server_app):
        server, mock_app = server_app
        for bad in (None, {"host": 42}, {"host": "https://my-vllm.lan"}, {"host": "a b c"}):
            result = server._dispatch(
                {"id": 1, "type": "add_trusted_endpoint", "data": bad}
            )
            assert result["type"] == "error", f"expected error for {bad!r}, got {result!r}"
        # MockConfig.__getattr__ returns None instead of raising, so
        # getattr-with-default would see None; read __dict__ directly.
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
        validated, errors = validate_config_update(
            {"trusted_extra_hosts": ["my-vllm.lan", "bad host"]}
        )
        assert errors, "invalid host list must produce validation errors"
        assert "bad host" not in validated.get("trusted_extra_hosts", [])

        validated, errors = validate_config_update({"trusted_extra_hosts": "not-a-list"})
        assert errors, "non-list value must be rejected"
        assert "trusted_extra_hosts" not in validated


class TestAllowlistHelpers:
    def test_extend_url_allowlist_accepts_host(self):
        extend_url_allowlist(["my-vllm.lan"], caller="test")
        assert_url_allowed("https://my-vllm.lan/v1")
