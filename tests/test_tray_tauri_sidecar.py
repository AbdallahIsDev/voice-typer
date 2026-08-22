"""Tests for the Tauri-sidecar tray behavior.

Covers the ADR-0020 §6.5 single-icon invariant and the host-ready menu
replay:

1. Under ``TAURI_SIDECAR=1`` the pystray icon is NEVER created (the
   native tray is owned by the Rust host) — ``start()`` degrades to the
   unavailable path while still launching background work.
2. When the sidecar publishes ``ready`` (a new host WS connection just
   authenticated), the last tray menu model + state are re-published so
   the Rust host's placeholder menu is replaced even when the original
   one-shot publish raced ahead of the WS subscriber install.
3. Notifications under the Tauri runtime are routed through the
   ``notification`` event bus event instead of a pystray icon that will
   never exist, instead of being queued forever.
"""

from types import SimpleNamespace

import pytest
from voice_typer.server.tray import TrayIcon


def _make_tray(monkeypatch) -> TrayIcon:
    """Build a TrayIcon with minimal fakes — no real pystray, no IPC."""
    config = SimpleNamespace(
        hotkey="<caps_lock>",
        tray_left_click_action="open_app",
        microphone=None,
    )
    controller = SimpleNamespace(
        toggle_dictation=lambda: None,
        restart_app=lambda: None,
        recording=SimpleNamespace(_force_recover_from_stuck_transcription=lambda force=False: None),
        _microphones=[],
        active_microphone_id=None,
        change_microphone=lambda mic_id: None,
        refresh_microphones=lambda: None,
    )
    monkeypatch.setattr(
        "voice_typer.server.tray.TrayIcon.__init__",
        lambda self: _init_minimal(self, config, controller),
    )
    return TrayIcon()


def _init_minimal(self, config, controller) -> None:
    """Minimal attribute set mirroring TrayIcon.__init__'s public surface."""
    self._controller = controller
    self._config = config
    self._hotkey = config.hotkey
    self._microphones = []
    self._state = None
    self._message = ""
    self._icon = None
    self._tray_unavailable = False
    self._notifications_enabled = True
    self._pending_notifications = []
    self._queue_lock = __import__("threading").Lock()
    self._menu_lock = __import__("threading").Lock()
    self._cached_menu = None
    self._menu_cache_valid = False
    self._bg_work_fn = None
    self._bg_thread = None
    self._run_event = __import__("threading").Event()
    self._pending_states = []
    self._last_applied_state = None
    self._last_published = None
    self._cpu_fallback_active = False
    self._recording_started_at = None
    self._autostart_enabled = False
    self._tray_id_map = {}


@pytest.fixture()
def tauri_env(monkeypatch):
    monkeypatch.setenv("TAURI_SIDECAR", "1")


class TestTauriSidecarSkipsPystray:
    def test_start_does_not_create_pystray_icon(self, monkeypatch, tauri_env):
        created = {}
        fake_pystray = SimpleNamespace(
            Menu=lambda *a, **k: (_ for _ in ()).throw(AssertionError("pystray.Menu called")),
            Icon=lambda *a, **k: created.setdefault("icon", object()),
        )
        # The lazy proxy re-reads sys.modules on access.
        import sys

        monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
        launched = []
        tray = _make_tray(monkeypatch)
        monkeypatch.setattr(tray, "_launch_bg_work", lambda: launched.append(True))

        tray.start()

        assert "icon" not in created, "pystray.Icon must not be constructed under TAURI_SIDECAR=1"
        assert tray._icon is None
        assert tray._tray_unavailable is True
        assert launched == [True], "bg_work must still launch on the Tauri path"

    def test_start_still_creates_icon_without_tauri_env(self, monkeypatch):
        """Sanity: without TAURI_SIDECAR the pystray path still runs."""
        import sys

        calls = {"icon": 0}
        fake_icon = SimpleNamespace(run=lambda: None)

        fake_pystray = SimpleNamespace(
            Menu=lambda *a, **k: object(),
            Icon=lambda *a, **k: calls.__setitem__("icon", calls["icon"] + 1) or fake_icon,
        )
        monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        monkeypatch.setattr("voice_typer.server.tray._make_icon", lambda state: object())
        tray = _make_tray(monkeypatch)
        monkeypatch.setattr(tray, "_launch_bg_work", lambda: None)
        # Skip the Wayland probe (host-dependent).
        monkeypatch.setattr(tray, "_is_linux_wayland_without_sni", lambda: False)

        tray.start()

        assert calls["icon"] == 1
        assert tray._icon is not None


class TestHostReadyRepublish:
    def test_ready_event_republishes_menu_and_state(self, monkeypatch, tauri_env):
        tray = _make_tray(monkeypatch)
        published = {"menu": 0, "state": 0}
        monkeypatch.setattr(
            tray,
            "_maybe_publish_tray_menu",
            lambda: published.__setitem__("menu", published["menu"] + 1) or True,
        )
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: published.__setitem__("state", published["state"] + 1))

        tray._on_host_ready({"type": "ready"})

        assert published == {"menu": 1, "state": 1}

    def test_non_ready_events_ignored(self, monkeypatch, tauri_env):
        tray = _make_tray(monkeypatch)
        published = {"menu": 0, "state": 0}
        monkeypatch.setattr(
            tray,
            "_maybe_publish_tray_menu",
            lambda: published.__setitem__("menu", published["menu"] + 1),
        )
        monkeypatch.setattr(
            tray,
            "_publish_tray_state",
            lambda: published.__setitem__("state", published["state"] + 1),
        )

        tray._on_host_ready({"type": "status_change"})
        tray._on_host_ready("not-a-dict")
        tray._on_host_ready(None)

        assert published == {"menu": 0, "state": 0}

    def test_noop_without_tauri_env(self, monkeypatch):
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        tray = _make_tray(monkeypatch)
        published = {"menu": 0}
        monkeypatch.setattr(
            tray,
            "_maybe_publish_tray_menu",
            lambda: published.__setitem__("menu", published["menu"] + 1),
        )

        tray._on_host_ready({"type": "ready"})

        assert published == {"menu": 0}


class TestTauriNotificationRouting:
    def test_do_notify_publishes_notification_event_when_no_icon(self, monkeypatch, tauri_env):
        from voice_typer.server import tray_notifications

        tray = _make_tray(monkeypatch)
        tray._icon = None
        events = []
        import voice_typer.server.event_bus as event_bus

        monkeypatch.setattr(event_bus, "publish", lambda e: events.append(e))

        tray_notifications.do_notify(tray, "Hello", "World")

        assert len(events) == 1
        assert events[0]["type"] == "notification"
        assert events[0]["data"]["title"] == "Hello"
        assert events[0]["data"]["message"] == "World"

    def test_notify_routes_immediately_under_tauri(self, monkeypatch, tauri_env):
        from voice_typer.server import tray_notifications

        tray = _make_tray(monkeypatch)
        tray._icon = None
        routed = []
        monkeypatch.setattr(tray_notifications, "do_notify", lambda t, title, msg: routed.append((title, msg)))

        tray_notifications.notify(tray, "Hello", "World")

        assert routed == [("Hello", "World")]
        assert tray._pending_notifications == []

    def test_notify_safety_routes_immediately_under_tauri(self, monkeypatch, tauri_env):
        from voice_typer.server import tray_notifications

        tray = _make_tray(monkeypatch)
        tray._icon = None
        routed = []
        monkeypatch.setattr(tray_notifications, "do_notify", lambda t, title, msg: routed.append((title, msg)))

        tray_notifications.notify_safety(tray, "Crash", "recovered")

        assert routed == [("Crash", "recovered")]

    def test_publish_failure_is_swallowed(self, monkeypatch, tauri_env):
        from voice_typer.server import tray_notifications

        tray = _make_tray(monkeypatch)
        tray._icon = None
        import voice_typer.server.event_bus as event_bus

        def boom(_event):
            raise RuntimeError("bus down")

        monkeypatch.setattr(event_bus, "publish", boom)

        # Must not raise — notification failures never crash the tray.
        tray_notifications.do_notify(tray, "Hello", "World")
