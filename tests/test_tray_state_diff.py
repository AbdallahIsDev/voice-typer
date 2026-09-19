"""DJ-38: ``_publish_tray_state`` suppresses redundant publishes."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_types import AppState  # noqa: E402


class _MockController:
    """Minimal TrayController protocol stub."""

    def toggle_dictation(self) -> None:
        pass

    def change_microphone(self, mic_id: str | None) -> None:
        pass

    def change_model(self, model: str) -> None:
        pass

    def quit_app(self) -> None:
        pass

    def undo_last(self) -> None:
        pass

    def restart_app(self) -> None:
        pass


def _make_tray(monkeypatch, publish_calls: list[dict]) -> TrayIcon:
    """Build a TrayIcon with ``publish_tray_state`` tracked."""
    mock_pystray = MagicMock()
    mock_pystray.Icon = MagicMock
    mock_pystray.Menu = MagicMock
    mock_pystray.Menu.SEPARATOR = "SEP"
    mock_pystray.MenuItem = MagicMock
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    import voice_typer.server.tray as tray_mod

    monkeypatch.setattr(tray_mod, "pystray", mock_pystray)

    import voice_typer.server.tray_menu as tray_menu_mod

    monkeypatch.setattr(tray_menu_mod, "pystray", mock_pystray)

    # Track publish_tray_state calls; return True so the cache is updated
    def _fake_publish(*, icon=None, tooltip=None):
        publish_calls.append({"icon": icon, "tooltip": tooltip})
        return True

    monkeypatch.setattr(tray_menu_mod, "publish_tray_state", _fake_publish)

    tray = TrayIcon(
        controller=_MockController(),
        config=SimpleNamespace(
            hotkey="<f2>",
            model_size="small.en",
            autostart=True,
            show_notifications=True,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
        ),
    )
    # Disable timer side-effects so set_state() doesn't spawn a worker.
    monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
    monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)
    return tray


class TestPublishTrayStateDiff:
    """DJ-38: redundant ``publish_tray_state`` calls are suppressed."""

    def test_first_call_emits(self, monkeypatch):
        """The first ``_publish_tray_state`` call emits (cache miss)."""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls)

        tray._state = AppState.IDLE
        tray._message = ""
        tray._publish_tray_state()

        assert len(publish_calls) == 1, f"First call should emit, got {len(publish_calls)} calls"
        assert publish_calls[0]["icon"] == "idle"

    def test_redundant_call_suppressed(self, monkeypatch):
        """Second call with same state + message is suppressed."""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls)

        tray._state = AppState.IDLE
        tray._message = ""

        tray._publish_tray_state()
        assert len(publish_calls) == 1

        tray._publish_tray_state()
        assert len(publish_calls) == 1, f"Redundant call should be suppressed, got {len(publish_calls)}"

        # A third redundant call, still suppressed.
        tray._publish_tray_state()
        assert len(publish_calls) == 1

    def test_message_change_emits(self, monkeypatch):
        """Changing the message (→ tooltip changes) emits again."""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls)

        tray._state = AppState.IDLE
        tray._message = ""

        tray._publish_tray_state()
        assert len(publish_calls) == 1

        tray._message = "new message"
        tray._publish_tray_state()
        assert len(publish_calls) == 2, f"Tooltip change should emit, got {len(publish_calls)}"
        assert "new message" in publish_calls[1]["tooltip"]

        # Same again, suppressed.
        tray._publish_tray_state()
        assert len(publish_calls) == 2

    def test_state_change_emits(self, monkeypatch):
        """Changing the state (→ icon_name + tooltip both change) emits."""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls)

        tray._state = AppState.IDLE
        tray._message = ""

        tray._publish_tray_state()
        assert publish_calls[0]["icon"] == "idle"

        tray._state = AppState.ERROR
        tray._publish_tray_state()
        assert len(publish_calls) == 2
        assert publish_calls[1]["icon"] == "error", (
            f"icon_name should change to 'error', got {publish_calls[1]['icon']}"
        )

    def test_state_change_with_same_tooltip_emits(self, monkeypatch):
        """Even if the tooltip happens to be identical, a state change"""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls)

        # IDLE with no message → tooltip is "<APP_NAME> [<model>] (<hotkey>)"
        tray._state = AppState.IDLE
        tray._message = ""
        tray._publish_tray_state()
        assert len(publish_calls) == 1

        # Force the same tooltip by hand (normally LOADING has a different
        tray._state = AppState.LOADING  # icon_name is "idle" (per the map)
        tray._publish_tray_state()
        # LOADING maps to "idle" icon_name AND (since state.value != IDLE
        assert len(publish_calls) == 2, "State change with different tooltip should emit"

    def test_stop_clears_cache_so_next_publish_emits(self, monkeypatch):
        """``stop()`` resets ``_last_published`` so a restarted tray"""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls)

        tray._state = AppState.IDLE
        tray._message = ""
        tray._publish_tray_state()
        assert len(publish_calls) == 1

        # Same state, suppressed.
        tray._publish_tray_state()
        assert len(publish_calls) == 1

        tray.stop()

        # After stop(), the cache is cleared, the next publish emits
        tray._publish_tray_state()
        assert len(publish_calls) == 2, "After stop() the cache is cleared so the next publish must emit"

    def test_publish_failure_does_not_cache(self, monkeypatch):
        """A transient ``publish_tray_state`` failure (raises) bypasses"""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls)

        # First call: IDLE + "" → succeeds, caches.
        tray._state = AppState.IDLE
        tray._message = ""
        tray._publish_tray_state()
        assert len(publish_calls) == 1

        # Now make publish_tray_state raise.
        import voice_typer.server.tray_menu as tray_menu_mod

        def _raising_publish(*, icon=None, tooltip=None):
            publish_calls.append({"icon": icon, "tooltip": tooltip, "raised": True})
            raise RuntimeError("transient event-bus failure")

        monkeypatch.setattr(tray_menu_mod, "publish_tray_state", _raising_publish)

        # Change the message so the cache check does NOT suppress this
        tray._message = "trigger-failure"
        tray._publish_tray_state()
        assert len(publish_calls) == 2  # the failing call still recorded
        assert publish_calls[1].get("raised") is True

        # Restore the working publish, the next call should retry
        def _working_publish(*, icon=None, tooltip=None):
            publish_calls.append({"icon": icon, "tooltip": tooltip})
            return True

        monkeypatch.setattr(tray_menu_mod, "publish_tray_state", _working_publish)

        tray._publish_tray_state()
        assert len(publish_calls) == 3, (
            "After a failed publish the cache should NOT be set, the next call must retry, not suppress"
        )
