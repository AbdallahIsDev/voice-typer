"""DJ-36: ``_apply_state`` skips the ``_make_icon`` redraw when state
is unchanged.

Context: the 1s elapsed-recording tick (UX-11) calls
``_on_elapsed_tick`` → ``_apply_state(state, message)``. Pre-DJ-36 this
re-malloc'd a fresh PIL image + pystray icon handle every second via
``_make_icon(state)`` even though the icon depends only on ``state``
(not on ``message`` / elapsed time). On Windows the rapid
``DestroyIcon`` / ``CreateIcon`` cycle also tickled a stale-handle bug
(CR-16 / GT-E1-8 — pystray WinError 1402).

DJ-36 fix: cache the last-applied STATE on ``self._last_applied_state``
and skip the ``self._icon.icon = _make_icon(state)`` assignment when
``state == self._last_applied_state``. The tooltip
(``self._icon.title``) is still updated unconditionally so the elapsed
``mm:ss`` stays live.

These tests pin the contract:

1. First ``_apply_state`` for a given state invokes ``_make_icon``.
2. Subsequent ``_apply_state`` calls with the SAME state do NOT invoke
   ``_make_icon`` (cache hit) — even when the message changes.
3. ``_apply_state`` with a DIFFERENT state invokes ``_make_icon`` again.
4. The tooltip (``self._icon.title``) is updated even when
   ``_make_icon`` is skipped (so elapsed ``mm:ss`` stays live).
5. ``stop()`` clears the cache so a restarted tray redraws.
"""

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


class _FakeIcon:
    """Minimal pystray.Icon stub that records icon + title assignments."""

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
    """Build a TrayIcon with mocked pystray + tracked ``_make_icon``."""
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

    # Stub _make_icon so we can count calls; returns a fresh MagicMock
    # each call so we can also assert the icon attribute changes.
    make_icon_calls: list[AppState] = []
    monkeypatch.setattr(
        tray_mod,
        "_make_icon",
        lambda state, size=0: make_icon_calls.append(state) or MagicMock(),
    )

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
    # Stash the call log on the tray for the test to read.
    tray._test_make_icon_calls = make_icon_calls  # type: ignore[attr-defined]
    # Disable the elapsed-timer side-effects — we're testing _apply_state
    # in isolation, not the recording-tick path.
    monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
    monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)
    return tray


class TestApplyStateIconDiff:
    """DJ-36: ``_make_icon`` is NOT called when state is unchanged."""

    def test_first_apply_invokes_make_icon(self, monkeypatch):
        """The first ``_apply_state`` for a given state invokes
        ``_make_icon`` (cache miss — ``_last_applied_state`` is None)."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()
        calls = tray._test_make_icon_calls
        calls.clear()  # ignore the start() initial icon draw

        tray._apply_state(AppState.IDLE, "msg1")
        assert len(calls) == 1, f"Expected 1 _make_icon call, got {calls}"
        assert calls[0] == AppState.IDLE

    def test_same_state_skips_make_icon(self, monkeypatch):
        """Subsequent ``_apply_state`` with the SAME state does NOT
        invoke ``_make_icon`` — the icon PNG depends only on state."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()
        calls = tray._test_make_icon_calls
        calls.clear()

        tray._apply_state(AppState.IDLE, "first")
        assert len(calls) == 1

        # Same state, different message — _make_icon NOT called.
        tray._apply_state(AppState.IDLE, "second")
        assert len(calls) == 1, f"Expected _make_icon to be skipped for same state, got {calls}"

        # Same state, yet another message — still skipped.
        tray._apply_state(AppState.IDLE, "third")
        assert len(calls) == 1

    def test_state_change_invokes_make_icon(self, monkeypatch):
        """``_apply_state`` with a DIFFERENT state invokes ``_make_icon``."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()
        calls = tray._test_make_icon_calls
        calls.clear()

        tray._apply_state(AppState.IDLE, "idle")
        assert len(calls) == 1

        tray._apply_state(AppState.RECORDING, "recording")
        assert len(calls) == 2, f"Expected 2 _make_icon calls, got {calls}"
        assert calls[1] == AppState.RECORDING

        # Back to IDLE — state changed, _make_icon called again.
        tray._apply_state(AppState.IDLE, "back")
        assert len(calls) == 3
        assert calls[2] == AppState.IDLE

    def test_tooltip_updated_even_when_icon_skipped(self, monkeypatch):
        """The tooltip (``self._icon.title``) is updated even when
        ``_make_icon`` is skipped — so the elapsed ``mm:ss`` stays live
        during the 1s recording tick."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()
        calls = tray._test_make_icon_calls
        calls.clear()

        tray._apply_state(AppState.IDLE, "first message")
        first_title = tray._icon.title
        assert "first message" in first_title

        # Same state, different message — icon skipped, title updated.
        tray._apply_state(AppState.IDLE, "second message")
        assert len(calls) == 1, "Icon should NOT be redrawn for same state"
        second_title = tray._icon.title
        assert "second message" in second_title, f"Tooltip should be updated to 'second message', got {second_title!r}"
        assert second_title != first_title, "Tooltip should differ across calls"

    def test_stop_clears_cache_so_next_apply_redraws(self, monkeypatch):
        """``stop()`` resets ``_last_applied_state`` so a restarted tray
        redraws the icon on the first ``_apply_state`` (no stale cache)."""
        tray = _make_tray(monkeypatch)
        tray.start(bg_work=None)
        tray.run()
        calls = tray._test_make_icon_calls
        calls.clear()

        tray._apply_state(AppState.IDLE, "msg")
        assert len(calls) == 1

        tray.stop()

        # Simulate a restart: new icon, cleared cache.
        tray._icon = _FakeIcon()
        tray._last_applied_state = None  # what stop() does
        calls.clear()

        tray._apply_state(AppState.IDLE, "msg")
        assert len(calls) == 1, "After stop(), the cache is cleared so the first apply must redraw"
