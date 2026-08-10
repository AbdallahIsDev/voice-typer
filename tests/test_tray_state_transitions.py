"""Fix-6 : tray state transition tests.

These tests pin the three fixes applied to ``voice_typer/server/tray.py``:

  * **** (High): ``set_state`` now extends the
    ``transcribing_changed`` predicate to ALSO cover RECORDING ⇄
    non-RECORDING transitions. The menu cache is invalidated on every
    RECORDING ⇄ non-RECORDING / TRANSCRIBING ⇄ non-TRANSCRIBING
    transition, and ``_maybe_publish_tray_menu`` is called so the
    Tauri host receives an updated menu (the "Stop Dictation" label
    flips on RECORDING enter/exit, "Force Cancel" appears on
    TRANSCRIBING enter/exit). Pre- the RECORDING transition
    only invalidated the icon — the menu stayed stale until the next
    state change (e.g. a microphone list refresh).

  * **** (Medium): ``_publish_tray_state`` wraps the
    check-then-publish-then-cache sequence in a dedicated
    ``self._publish_lock`` so two concurrent callers (the 1s
    elapsed-recording tick vs a state-change IPC) cannot both pass the
    cache check and both emit. The lock is held ONLY across the tuple
    comparison + the event-bus publish (NOT across ``_compute_tooltip``
    or the icon-name lookup).

  * **** (Low): ``_compute_tooltip`` truncates the return value
    to 127 chars (with a trailing ``…`` if truncated) so the Win32
    ``NOTIFYICONDATAW.szTip`` 128-char limit (127 + NUL) does not
    silently truncate the tooltip at the OS layer.
"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
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


def _make_tray(monkeypatch, publish_calls: list[dict] | None = None) -> TrayIcon:
    """Build a TrayIcon with ``publish_tray_state`` + ``_maybe_publish_tray_menu``
    tracked.

    Mirrors the helper in tests/test_tray_state_diff.py so
    tests share the same setup shape.
    """
    mock_pystray = MagicMock()
    mock_pystray.Icon = MagicMock
    mock_pystray.Menu = MagicMock
    mock_pystray.Menu.SEPARATOR = "SEP"
    mock_pystray.MenuItem = MagicMock
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    import voice_typer.server.tray as tray_mod
    import voice_typer.server.tray_menu as tray_menu_mod

    monkeypatch.setattr(tray_mod, "pystray", mock_pystray)
    monkeypatch.setattr(tray_menu_mod, "pystray", mock_pystray)

    # Stub _make_icon so the icon redraw path doesn't touch PIL.
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=0: MagicMock())

    # Track publish_tray_state calls; return True so the cache is updated
    # (matches the Tauri-sidecar success path).
    def _fake_publish(*, icon=None, tooltip=None):
        if publish_calls is not None:
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
    # Disable elapsed-timer side-effects so set_state() doesn't spawn a worker.
    monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
    monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)
    return tray


# ─── RECORDING transition invalidates menu cache ─────────────────


class TestRecordingTransitionInvalidatesMenuCache:
    """``set_state`` must invalidate the menu cache AND call
    ``_maybe_publish_tray_menu`` on RECORDING ⇄ non-RECORDING transitions,
    not just TRANSCRIBING ⇄ non-TRANSCRIBING."""

    def test_idle_to_recording_invalidates_menu_cache(self, monkeypatch):
        """IDLE → RECORDING must clear ``_menu_cache_valid`` and push the
        menu so the "Stop Dictation" label flips."""
        tray = _make_tray(monkeypatch)
        # Start with a valid cache (simulate a prior build).
        tray._menu_cache_valid = True
        tray._cached_menu = ("placeholder",)

        menu_publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_maybe_publish_tray_menu", lambda: menu_publish_calls.append(None))
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: None)
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: None)

        tray.set_state(AppState.RECORDING, "recording")

        assert tray._menu_cache_valid is False, (
            "IDLE → RECORDING must invalidate the menu cache  — "
            "the 'Stop Dictation' label must flip on the next right-click."
        )
        assert len(menu_publish_calls) == 1, (
            "IDLE → RECORDING must push the tray menu  — got "
            f"{len(menu_publish_calls)} calls"
        )

    def test_recording_to_idle_invalidates_menu_cache(self, monkeypatch):
        """RECORDING → IDLE must clear ``_menu_cache_valid`` and push the
        menu so the "Stop Dictation" label flips back to "Start Dictation"."""
        tray = _make_tray(monkeypatch)
        tray._state = AppState.RECORDING
        tray._message = "recording"
        tray._menu_cache_valid = True
        tray._cached_menu = ("placeholder",)

        menu_publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_maybe_publish_tray_menu", lambda: menu_publish_calls.append(None))
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: None)
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: None)

        tray.set_state(AppState.IDLE, "")

        assert tray._menu_cache_valid is False, (
            "RECORDING → IDLE must invalidate the menu cache  — "
            "the 'Start Dictation' label must flip on the next right-click."
        )
        assert len(menu_publish_calls) == 1, (
            "RECORDING → IDLE must push the tray menu  — got "
            f"{len(menu_publish_calls)} calls"
        )

    def test_recording_to_transcribing_no_menu_publish(self, monkeypatch):
        """RECORDING → TRANSCRIBING stays inside the {RECORDING, TRANSCRIBING}
        set, so ``record_or_transcribe_changed`` is False and the menu is
        NOT re-pushed (no label flip — both states have the same
        ``is_recording`` / ``is_transcribing`` flags)."""
        tray = _make_tray(monkeypatch)
        tray._state = AppState.RECORDING
        tray._message = "recording"
        tray._menu_cache_valid = True

        menu_publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_maybe_publish_tray_menu", lambda: menu_publish_calls.append(None))
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: None)
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: None)

        tray.set_state(AppState.TRANSCRIBING, "transcribing")

        # RECORDING and TRANSCRIBING are BOTH in the membership set, so
        # the predicate is False — the menu cache stays valid and no
        # menu publish fires.
        assert tray._menu_cache_valid is True, (
            "RECORDING → TRANSCRIBING stays inside the membership set — "
            "no cache invalidation ."
        )
        assert menu_publish_calls == [], (
            "RECORDING → TRANSCRIBING must NOT push the menu (both states "
            "have the same 'Stop Dictation' + 'Force Cancel' visibility)."
        )

    def test_idle_to_transcribing_invalidates_menu_cache(self, monkeypatch):
        """IDLE → TRANSCRIBING is the original  coverage and must
        still invalidate the cache (regression guard for the predicate
        refactor from ``transcribing_changed`` to
        ``record_or_transcribe_changed``)."""
        tray = _make_tray(monkeypatch)
        tray._menu_cache_valid = True

        menu_publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_maybe_publish_tray_menu", lambda: menu_publish_calls.append(None))
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: None)
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: None)

        tray.set_state(AppState.TRANSCRIBING, "transcribing")

        assert tray._menu_cache_valid is False, (
            "IDLE → TRANSCRIBING must invalidate the menu cache — the "
            "Force Cancel item must appear on the next right-click."
        )
        assert len(menu_publish_calls) == 1

    def test_idle_to_error_no_menu_publish(self, monkeypatch):
        """IDLE → ERROR is NOT in the membership set, so no menu publish
        fires (no label flip). Guards against over-publishing."""
        tray = _make_tray(monkeypatch)
        tray._menu_cache_valid = True

        menu_publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_maybe_publish_tray_menu", lambda: menu_publish_calls.append(None))
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: None)
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: None)

        tray.set_state(AppState.ERROR, "boom")

        assert tray._menu_cache_valid is True, "IDLE → ERROR must NOT invalidate the menu cache."
        assert menu_publish_calls == [], "IDLE → ERROR must NOT push the menu."


# ─── _publish_tray_state thread safety ───────────────────────────


class TestPublishTrayStateThreadSafe:
    """the check-then-publish-then-cache sequence in
    ``_publish_tray_state`` must be serialized by ``_publish_lock`` so two
    concurrent callers cannot both pass the cache check and both emit."""

    def test_publish_lock_declared(self):
        """TrayIcon.__init__ must declare ``_publish_lock`` as a Lock."""
        tray = _make_tray(MagicMock())
        assert hasattr(tray, "_publish_lock"), (
            "TrayIcon must declare ``_publish_lock``  — got no attribute."
        )
        # threading.Lock instances are not the Lock class directly (factory
        # returns a C object); verify it can be acquired + released.
        with tray._publish_lock:
            pass
        assert tray._publish_lock is not tray._icon_lock, (
            "_publish_lock must be a separate Lock instance from _icon_lock "
            " — sharing would over-serialize the publish path against "
            "the icon teardown path."
        )
        assert tray._publish_lock is not tray._menu_lock, (
            "_publish_lock must be a separate Lock instance from _menu_lock "
            " — sharing would serialize the publish path against the "
            "menu rebuild path."
        )

    def test_concurrent_publishes_no_duplicate_emit(self, monkeypatch):
        """N threads call ``_publish_tray_state`` concurrently with the
        SAME state + message. Without the lock, every thread passes the
        cache check (cache is initially None) and emits — the publish
        counter would be N. With the lock, the first thread emits + sets
        the cache; subsequent threads see the cache hit and skip."""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls=publish_calls)
        # Force a stable state + message so every thread computes the
        # same (icon_name, tooltip) tuple.
        tray._state = AppState.IDLE
        tray._message = ""
        tray._last_published = None  # ensure cache miss

        # Insert a tiny delay INSIDE the publish callback so concurrent
        # threads race against each other (without the lock, the delay
        # window is wide enough for multiple threads to pass the cache
        # check before any thread writes ``_last_published``).
        import voice_typer.server.tray_menu as tray_menu_mod

        def _slow_publish(*, icon=None, tooltip=None):
            time.sleep(0.02)  # widen the race window
            publish_calls.append({"icon": icon, "tooltip": tooltip})
            return True

        monkeypatch.setattr(tray_menu_mod, "publish_tray_state", _slow_publish)

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        errors: list[Exception] = []

        def worker():
            try:
                barrier.wait(timeout=5.0)
                tray._publish_tray_state()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, name=f"pub-{i}") for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"Thread {t.name!r} deadlocked on _publish_lock."

        assert not errors, f"concurrent _publish_tray_state raised: {errors}"
        # With the lock, exactly ONE publish fires (the first thread sets
        # the cache; subsequent threads see the hit and return early).
        # Tolerate at most 1 publish (the lock guarantees exactly one).
        assert len(publish_calls) == 1, (
            "Concurrent publishes with the same state must emit exactly ONCE "
            " — the first caller sets ``_last_published`` and "
            f"subsequent callers skip. Got {len(publish_calls)} publishes."
        )

    def test_concurrent_publishes_with_changing_message_no_crash(self, monkeypatch):
        """Concurrent calls with DIFFERENT messages (every thread sets a
        unique message before publishing) must not crash and must not
        deadlock. The cache may emit multiple times (each unique message
        is a cache miss), but the lock serializes the emit + cache write
        so no torn read of ``_last_published`` is possible."""
        publish_calls: list[dict] = []
        tray = _make_tray(monkeypatch, publish_calls=publish_calls)

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        errors: list[Exception] = []

        def worker(i: int):
            try:
                barrier.wait(timeout=5.0)
                # Each thread sets a unique message → unique tooltip →
                # cache miss → publish fires. The lock serializes the
                # cache writes so no torn read.
                tray._message = f"msg-{i}"
                tray._publish_tray_state()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,), name=f"pub-chg-{i}") for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"Thread {t.name!r} deadlocked on _publish_lock."

        assert not errors, f"concurrent _publish_tray_state raised: {errors}"
        # Each unique message is a cache miss → emits. The lock guarantees
        # the cache is consistent, but the publishes themselves may dedupe
        # in any order. We only assert no crash + at least one publish fired.
        assert len(publish_calls) >= 1, "Expected at least one publish to fire."


# ─── _compute_tooltip truncation ─────────────────────────────────


class TestComputeTooltipTruncation:
    """``_compute_tooltip`` must truncate the return value to 127
    chars (with a trailing ``…`` if truncated) so the Win32
    ``NOTIFYICONDATAW.szTip`` 128-char limit (127 + NUL) does not
    silently truncate at the OS layer."""

    def test_short_tooltip_not_truncated(self, monkeypatch):
        """A short tooltip (under 127 chars) is returned unchanged — no
        spurious ``…`` appended."""
        tray = _make_tray(monkeypatch)
        tray._state = AppState.IDLE
        tray._message = ""

        tooltip = tray._compute_tooltip(AppState.IDLE, "")

        assert len(tooltip) <= 127, "Short tooltip must fit within 127 chars."
        assert not tooltip.endswith("…"), "Short tooltip must NOT be truncated."

    def test_long_tooltip_truncated_to_127_chars(self, monkeypatch):
        """A tooltip longer than 127 chars is truncated to exactly 127
        chars, ending in ``…``."""
        tray = _make_tray(monkeypatch)
        # Force a very long message so the tooltip exceeds 127 chars.
        long_message = "x" * 200
        tray._state = AppState.IDLE
        tray._message = long_message

        tooltip = tray._compute_tooltip(AppState.IDLE, long_message)

        assert len(tooltip) == 127, (
            f"Long tooltip must be truncated to exactly 127 chars — got {len(tooltip)}."
        )
        assert tooltip.endswith("…"), (
            "Truncated tooltip must end with ``…`` (U+2026) so the user "
            "sees the truncation."
        )

    def test_tooltip_exactly_127_chars_not_truncated(self, monkeypatch):
        """A tooltip that is exactly 127 chars long is NOT truncated —
        the boundary is `> 127`, not `>= 127`."""
        tray = _make_tray(monkeypatch)
        tray._state = AppState.IDLE

        # Build a message that produces a tooltip of EXACTLY 127 chars.
        # The tooltip format is ``<APP_NAME> — <message> [<model>] (<hotkey>)``
        # — we tune the message length to land at the boundary.
        base = tray._compute_tooltip(AppState.IDLE, "")
        base_len = len(base)
        # We need the message to add (127 - base_len - 3) chars (the " — "
        # separator is 3 chars: space, em-dash, space).
        delta = 127 - base_len - 3
        if delta < 0:
            # base is already > 127 (e.g. very long model name); skip the
            # boundary test in that case — the long-tooltip test above
            # already covers the truncation path.
            pytest.skip(
                f"Base tooltip is already {base_len} chars — cannot construct "
                "a 127-char boundary case with this config."
            )
        message = "y" * delta

        tooltip = tray._compute_tooltip(AppState.IDLE, message)
        assert len(tooltip) == 127, (
            f"Boundary tooltip must be exactly 127 chars — got {len(tooltip)}."
        )
        assert not tooltip.endswith("…"), (
            "A tooltip of exactly 127 chars must NOT be truncated — the "
            "boundary is `> 127`, not `>= 127`."
        )

    def test_tooltip_128_chars_truncated(self, monkeypatch):
        """A tooltip of 128 chars (one over the limit) is truncated to
        127 chars with a trailing ``…``."""
        tray = _make_tray(monkeypatch)
        tray._state = AppState.IDLE

        base = tray._compute_tooltip(AppState.IDLE, "")
        base_len = len(base)
        delta = 128 - base_len - 3
        if delta < 0:
            pytest.skip(
                f"Base tooltip is already {base_len} chars — cannot construct "
                "a 128-char boundary case with this config."
            )
        message = "z" * delta

        tooltip = tray._compute_tooltip(AppState.IDLE, message)
        assert len(tooltip) == 127, (
            f"128-char tooltip must be truncated to 127 chars — got {len(tooltip)}."
        )
        assert tooltip.endswith("…"), "128-char tooltip must end with ``…``."

    def test_truncated_tooltip_uses_single_codepoint_ellipsis(self, monkeypatch):
        """The truncation suffix must be the single Unicode codepoint
        ``…`` (U+2026), not three ASCII dots ``...`` — the single
        codepoint occupies ONE char in the 127-char budget (vs three for
        ``...``)."""
        tray = _make_tray(monkeypatch)
        tray._state = AppState.IDLE
        long_message = "x" * 200

        tooltip = tray._compute_tooltip(AppState.IDLE, long_message)

        assert tooltip[-1] == "…", (
            "Truncation suffix must be U+2026 (single codepoint), not ASCII dots."
        )
        # U+2026 is a single codepoint — len("…") == 1.
        assert len("…") == 1, "U+2026 must be a single Python char."

    def test_truncated_tooltip_is_deterministic_cache_key(self, monkeypatch):
        """Two calls with the same long message must produce the SAME
        truncated tooltip — the truncation is deterministic so the
        ``_last_published`` tuple comparison in ``_publish_tray_state``
        deduplicates correctly."""
        tray = _make_tray(monkeypatch)
        tray._state = AppState.IDLE
        long_message = "x" * 200

        t1 = tray._compute_tooltip(AppState.IDLE, long_message)
        t2 = tray._compute_tooltip(AppState.IDLE, long_message)

        assert t1 == t2, "Truncation must be deterministic for the same input."
        assert len(t1) == 127
        assert t1.endswith("…")
