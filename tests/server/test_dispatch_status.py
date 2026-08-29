"""IPC dispatch tests for status / defaults / today-stats commands.

Classes:
- TestDispatchGetStatus   — get_status dispatcher
- TestDispatchGetTodayStats — get_today_stats dispatcher
- TestGetDefaultsIpc       — UX-018 get_defaults IPC command

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

from tests.server.conftest import (  # noqa: F401
    IPCServer,
    MockApp,
    mock_app,
    server,
)


class TestDispatchGetStatus:
    def test_returns_current_state(self, server, mock_app):
        from voice_typer.server.tray import AppState

        mock_app.tray.state = AppState.RECORDING
        result = server._dispatch({"id": 1, "type": "get_status"})
        # payload now includes xruns_since_start.
        assert result["id"] == 1
        assert result["type"] == "status"
        assert result["data"]["status"] == "recording"
        assert "xruns_since_start" in result["data"]
        # the About page's "Config Directory" diagnostic reads this —
        # the key must always be present (may be "" if unavailable).
        assert "config_dir" in result["data"]

    def test_idle_state(self, server):
        result = server._dispatch({"id": 2, "type": "get_status"})
        assert result["type"] == "status"
        assert result["data"]["status"] == "idle"

    def test_omits_id_if_not_provided(self, server):
        result = server._dispatch({"type": "get_status"})
        assert "id" not in result

    def test_includes_offline_pack_degradation_state(self, server, monkeypatch):
        """Phase 2d (§8.10): get_status carries the offline-pack state."""
        from voice_typer.server.service import update_check

        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        result = server._dispatch({"id": 9, "type": "get_status"})
        assert result["type"] == "status"
        op = result["data"]["offline_pack"]
        assert set(op) == {"installed_version", "available", "consent_granted"}
        assert op == {"installed_version": None, "available": False, "consent_granted": False}

    def test_offline_pack_present_is_reported_available(self, server, monkeypatch):
        """Pack installed → available True + version surfaced."""
        from voice_typer.server.service import update_check

        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: "v9")
        result = server._dispatch({"id": 10, "type": "get_status"})
        op = result["data"]["offline_pack"]
        assert op["installed_version"] == "v9"
        assert op["available"] is True

    def test_consent_reflected_in_status(self, server, monkeypatch):
        """Consent flag surfaced for the renderer's silent-download UX."""
        from voice_typer.server.service import update_check

        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        server.app.config.offline_pack_consent = True
        result = server._dispatch({"id": 11, "type": "get_status"})
        assert result["data"]["offline_pack"]["consent_granted"] is True


class TestDispatchGetTodayStats:
    def test_returns_stats(self, server):
        result = server._dispatch({"id": 1, "type": "get_today_stats"})
        assert result == {
            "id": 1,
            "type": "today_stats",
            "data": {"count": 5, "chars": 240},
        }


# get_defaults IPC ─────────────────────────────────────────────


class TestGetDefaultsIpc:
    """UX-018: the ``get_defaults`` IPC command returns the default
    Config() values so the renderer's "Reset to Defaults" button
    doesn't hardcode 22+ field defaults (which silently drift)."""

    def test_get_defaults_returns_config_defaults(self, server, mock_app):
        """get_defaults should return a dict with default Config values."""
        result = server._dispatch({"id": 1, "type": "get_defaults"})
        assert result["type"] == "defaults"
        assert result["id"] == 1
        data = result["data"]
        # Verify a few representative defaults match Config()
        # NATIVE-001: default hotkey is platform-aware
        from voice_typer.server.config import _default_hotkey_for_platform
        from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

        assert data["hotkey"] == _default_hotkey_for_platform()
        # Compare against the canonical constant: since the "no default
        # model" change, DEFAULT_MODEL_SIZE is the empty string — the app
        # loads nothing until the user picks a model in onboarding.
        assert data["model_size"] == DEFAULT_MODEL_SIZE
        assert data["language"] == "en"
        assert data["autostart"] is True
        assert data["paste_on_stop"] is True

    def test_get_defaults_redacts_api_keys(self, server, mock_app):
        """get_defaults must also redact API keys (even though defaults
        are empty strings, the sanitizer should still be applied for
        defense-in-depth)."""
        result = server._dispatch({"id": 1, "type": "get_defaults"})
        data = result["data"]
        # Default API keys are empty strings, not "<redacted>"
        assert data["cloud_api_key"] == ""
        assert data["openai_api_key"] == ""
        assert data["llm_api_key"] == ""

    def test_get_defaults_does_not_modify_app_config(self, server, mock_app):
        """get_defaults must not mutate the app's actual config."""
        mock_app.config.hotkey = "<f9>"  # non-default value
        result = server._dispatch({"id": 1, "type": "get_defaults"})
        # The defaults should show the platform-aware default hotkey,
        # but the app config should still be <f9>.
        from voice_typer.server.config import _default_hotkey_for_platform

        assert result["data"]["hotkey"] == _default_hotkey_for_platform()
        assert mock_app.config.hotkey == "<f9>"
