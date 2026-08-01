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

    def test_idle_state(self, server):
        result = server._dispatch({"id": 2, "type": "get_status"})
        assert result["type"] == "status"
        assert result["data"]["status"] == "idle"

    def test_omits_id_if_not_provided(self, server):
        result = server._dispatch({"type": "get_status"})
        assert "id" not in result


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

        assert data["hotkey"] == _default_hotkey_for_platform()
        assert data["model_size"] == "small.en"
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
