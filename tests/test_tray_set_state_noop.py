"""DJ-41: ``set_state`` short-circuits when ``state`` + ``message`` are
both unchanged.

Context: ``TrayIcon.set_state`` is the main entry point for tray state
updates. Pre-DJ-41, every call ran the full pipeline:
  1. update ``_state`` / ``_message``
  2. invalidate menu cache (sometimes)
  3. start/stop the elapsed timer
  4. call ``_apply_state`` (re-malloc an icon via ``_make_icon``)
  5. call ``_publish_tray_state`` (emit ``tray_state`` event)
  6. push tray menu (sometimes)

Several callers re-issue the same state:
  - The IPC ``set_state`` handler replays on reconnect.
  - ``refresh_config`` calls ``_publish_tray_state`` after a config
    change with no actual state change.
  - ``_on_parakeet_cpu_fallback`` may re-fire with the same state.

Pre-DJ-41 each redundant call did ~3 useless work units (icon redraw +
event emit + lock acquire). DJ-41 adds a short-circuit at the top:
``if state == self._state and message == self._message: return``.

These tests pin the contract:

1. Same state + same message → no-op (no ``_apply_state`` /
   ``_publish_tray_state`` calls).
2. Same state, different message → NOT a no-op (publish fires so the
   tooltip updates).
3. Different state, same message → NOT a no-op.
4. The no-op short-circuit happens BEFORE the elapsed-timer
   side-effects (no spurious start/cancel).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# Mock pystray at module level so the tray module imports cleanly
# without an X display (headless CI).
_mock_pystray = MagicMock()
_mock_pystray.Menu = MagicMock
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

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


class _FakeIcon:
    """Minimal pystray.Icon stub."""

    def __init__(self, **kwargs):
        self.icon = kwargs.get("icon")
        self.title = kwargs.get("title", "")

    def run(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def notify(self, *a, **kw) -> None:
        pass


def _make_tray(monkeypatch) -> TrayIcon:
    """Build a TrayIcon with mocked pystray."""
    mock_pystray = MagicMock()
    mock_pystray.Icon = _FakeIcon
    mock_pystray.Menu = MagicMock
    mock_pystray.Menu.SEPARATOR = "SEP"
    mock_pystray.MenuItem = MagicMock
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    import voice_typer.server.tray as tray_mod

    monkeypatch.setattr(tray_mod, "pystray", mock_pystray)

    import voice_typer.server.tray_menu as tray_menu_mod

    monkeypatch.setattr(tray_menu_mod, "pystray", mock_pystray)

    # Stub _make_icon so the icon redraw path doesn't touch PIL.
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=0: MagicMock())

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
    # Disable elapsed-timer side-effects — we're testing set_state's
    # short-circuit, not the timer machinery.
    monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
    monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)
    return tray


class TestSetStateNoop:
    """DJ-41: ``set_state`` short-circuits when state + message unchanged."""

    def test_noop_skips_apply_and_publish(self, monkeypatch):
        """Same state + same message → ``_apply_state`` /
        ``_publish_tray_state`` are NOT called."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()

        # Track _apply_state + _publish_tray_state calls.
        apply_calls: list[tuple] = []
        publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: publish_calls.append(None))

        # First call — applies + publishes.
        tray.set_state(AppState.IDLE, "msg")
        assert len(apply_calls) == 1, f"First call should apply, got {apply_calls}"
        assert len(publish_calls) == 1, f"First call should publish, got {publish_calls}"

        # Same state + same message — short-circuited (DJ-41).
        tray.set_state(AppState.IDLE, "msg")
        assert len(apply_calls) == 1, (
            f"No-op call should NOT _apply_state, got {apply_calls}"
        )
        assert len(publish_calls) == 1, (
            f"No-op call should NOT _publish_tray_state, got {publish_calls}"
        )

        # A third identical call — still short-circuited.
        tray.set_state(AppState.IDLE, "msg")
        assert len(apply_calls) == 1
        assert len(publish_calls) == 1

    def test_different_message_not_noop(self, monkeypatch):
        """Same state, different message → NOT a no-op (publish fires so
        the tooltip updates)."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()

        apply_calls: list[tuple] = []
        publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: publish_calls.append(None))

        tray.set_state(AppState.IDLE, "first")
        assert len(apply_calls) == 1
        assert len(publish_calls) == 1

        # Different message — NOT a no-op.
        tray.set_state(AppState.IDLE, "second")
        assert len(apply_calls) == 2, "Different message should NOT be a no-op"
        assert len(publish_calls) == 2

    def test_different_state_not_noop(self, monkeypatch):
        """Different state, same message → NOT a no-op."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()

        apply_calls: list[tuple] = []
        publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: publish_calls.append(None))

        tray.set_state(AppState.IDLE, "msg")
        assert len(apply_calls) == 1
        assert len(publish_calls) == 1

        # Different state — NOT a no-op.
        tray.set_state(AppState.RECORDING, "msg")
        assert len(apply_calls) == 2, "Different state should NOT be a no-op"
        assert len(publish_calls) == 2

    def test_noop_skips_elapsed_timer_side_effects(self, monkeypatch):
        """The no-op short-circuit happens BEFORE the elapsed-timer
        start/stop logic — a redundant ``set_state(RECORDING, msg)``
        doesn't re-call ``_start_elapsed_timer`` (which would cancel +
        restart the worker thread)."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()

        # Track _start_elapsed_timer + _cancel_elapsed_timer calls
        # (override the no-op patches set by _make_tray).
        start_calls: list[None] = []
        cancel_calls: list[None] = []
        monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: start_calls.append(None))
        monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: cancel_calls.append(None))

        # IDLE → RECORDING: starts the timer.
        tray.set_state(AppState.RECORDING, "recording")
        assert len(start_calls) == 1, f"Expected 1 start call, got {start_calls}"
        assert len(cancel_calls) == 0

        # RECORDING → RECORDING (same args): no-op, no spurious start/cancel.
        tray.set_state(AppState.RECORDING, "recording")
        assert len(start_calls) == 1, (
            f"No-op RECORDING call should NOT re-start the timer, got {start_calls}"
        )
        assert len(cancel_calls) == 0, (
            f"No-op RECORDING call should NOT cancel the timer, got {cancel_calls}"
        )

        # RECORDING → RECORDING (different message): NOT a no-op, but
        # the start/cancel logic only fires on state CHANGE (RECORDING ⇄
        # non-RECORDING), so start_calls + cancel_calls stay at 1 + 0.
        tray.set_state(AppState.RECORDING, "still recording")
        assert len(start_calls) == 1, (
            "Same-state transition should not re-start the timer "
            "(prev_state == state == RECORDING)"
        )
        assert len(cancel_calls) == 0

        # RECORDING → IDLE: cancels the timer.
        tray.set_state(AppState.IDLE, "done")
        assert len(cancel_calls) == 1, "IDLE transition should cancel the timer"

        # IDLE → IDLE (same args): no-op, no spurious cancel.
        tray.set_state(AppState.IDLE, "done")
        assert len(cancel_calls) == 1, (
            f"No-op IDLE call should NOT re-cancel, got {cancel_calls}"
        )

    def test_noop_returns_none_quickly(self, monkeypatch):
        """The no-op short-circuit is the FIRST statement in set_state,
        so callers that re-issue the same state pay only the cost of a
        tuple-equality check (no menu-cache invalidation, no
        transcribing-change check, no timer side-effects, no
        _apply_state, no _publish_tray_state, no _maybe_publish_tray_menu)."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()

        # Track every side-effecting call set_state can make.
        apply_calls: list[tuple] = []
        publish_calls: list[None] = []
        menu_publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: publish_calls.append(None))
        monkeypatch.setattr(tray, "_maybe_publish_tray_menu", lambda: menu_publish_calls.append(None))

        # Initial state is IDLE — set_state(IDLE, "") should be a no-op
        # because __init__ set _state=IDLE + _message="".
        tray.set_state(AppState.IDLE, "")
        assert apply_calls == [], f"Initial no-op should NOT apply, got {apply_calls}"
        assert publish_calls == [], f"Initial no-op should NOT publish, got {publish_calls}"
        assert menu_publish_calls == [], f"Initial no-op should NOT push menu, got {menu_publish_calls}"
