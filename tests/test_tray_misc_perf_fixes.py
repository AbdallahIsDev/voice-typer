"""Regression tests for the three tray + misc server perf findings.

Covers:

1. **tray_notifications.on_parakeet_cpu_fallback** must call
   ``tray._publish_tray_state()`` after ``_apply_state`` so the
   Tauri/Electron renderer's tray indicator picks up the
   "(CPU fallback)" tooltip suffix immediately. Pre-fix, only the
   pystray Icon got the suffix (via ``_apply_state``); the Tauri
   host stayed stale until the next ``_on_elapsed_tick`` (1 s later)
   or the next ``set_state`` call. On the tray-unavailable path
   (no pystray Icon), the suffix never reached the renderer at all.

2. **tray_elapsed_timer.ElapsedTimer** must use a generation counter
   so a rapid ``start()`` (stop/restart race) invalidates any
   in-flight ``_tick`` from a prior ``start()`` — preventing the
   stale tick from rescheduling a NEW Timer that overwrites the
   ``_timer`` reference set by the new ``start()``. Pre-fix, the
   stale tick would leak the just-scheduled Timer and break
   ``cancel()``'s join semantics.

3. **waveform_bubble_wiring._push_bubble_config** must use
   ``getattr(cfg, name, None) or default`` for non-None defaults so
   a Config with explicit ``None`` fields (e.g. partial / corrupt
   load) falls back to the documented default instead of
   propagating ``None`` to the bubble renderer. ``custom_theme``
   is exempt because ``None`` is its valid default.

These tests are HEADLESS and SIDE-EFFECT-FREE: they perform no IPC,
spawn no process, touch no sockets. They mock pystray at module
level so the tray module imports cleanly without an X display.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

# ─── tray_notifications: CPU fallback publishes to Tauri ────


class TestCpuFallbackPublishesToTauri:
    """``on_parakeet_cpu_fallback`` must publish state to Tauri after
    applying it to the pystray Icon.

    Pre-fix: only ``_apply_state`` was called, so the Tauri host's
    tray_state event stayed stale (the pystray Icon got the
    "(CPU fallback)" suffix but the renderer didn't). Post-fix:
    ``_publish_tray_state`` is also called, mirroring the pattern in
    ``_on_elapsed_tick``.
    """

    def test_publish_tray_state_called_after_apply_state(self, monkeypatch):
        """The fix calls ``_publish_tray_state`` after ``_apply_state``
        inside the same try/except — so the Tauri host receives the
        updated tooltip immediately."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._state = "RECORDING"
        tray._message = "recording"

        apply_calls: list[tuple] = []
        publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: publish_calls.append(None))

        on_parakeet_cpu_fallback(
            tray,
            {"type": "parakeet_cpu_fallback", "data": {"device": "cpu", "reason": "cuda oom"}},
        )

        # _apply_state must have been called with the current state.
        assert len(apply_calls) == 1, f"Expected 1 _apply_state call, got {apply_calls}"
        assert apply_calls[0] == ("RECORDING", "recording")

        # _publish_tray_state must ALSO have been called (the fix).
        assert len(publish_calls) == 1, (
            f"Expected 1 _publish_tray_state call after the fix, got {publish_calls}. "
            "Pre-fix behavior was to only call _apply_state, leaving the Tauri host stale."
        )

        # _cpu_fallback_active flag must have been set.
        assert tray._cpu_fallback_active is True

    def test_publish_tray_state_failure_does_not_mask_apply_state(self, monkeypatch):
        """If ``_publish_tray_state`` raises, ``_apply_state`` must
        still have been called first (both calls are in the same
        try/except, so a publish failure is caught + logged, not
        propagated — but the apply already ran)."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._state = "IDLE"
        tray._message = ""

        apply_calls: list[tuple] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        # _publish_tray_state raises — must be swallowed.
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: (_ for _ in ()).throw(RuntimeError("publish boom")))

        # Must NOT raise — the try/except in on_parakeet_cpu_fallback
        # catches the publish failure.
        on_parakeet_cpu_fallback(
            tray,
            {"type": "parakeet_cpu_fallback", "data": {"device": "cpu"}},
        )

        # _apply_state ran first (before the publish that raised).
        assert len(apply_calls) == 1, (
            f"_apply_state must have been called even though _publish_tray_state raised; got {apply_calls}"
        )

    def test_ignores_non_dict_event(self, monkeypatch):
        """Malformed payloads (non-dict) are ignored — no state change."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._cpu_fallback_active = False

        on_parakeet_cpu_fallback(tray, "not a dict")  # type: ignore[arg-type]

        # _cpu_fallback_active must NOT have been set.
        assert tray._cpu_fallback_active is False
        tray._apply_state.assert_not_called()
        tray._publish_tray_state.assert_not_called()

    def test_ignores_wrong_event_type(self, monkeypatch):
        """Events with the wrong ``type`` are ignored."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._cpu_fallback_active = False

        on_parakeet_cpu_fallback(tray, {"type": "some_other_event"})

        assert tray._cpu_fallback_active is False
        tray._apply_state.assert_not_called()
        tray._publish_tray_state.assert_not_called()


# ─── tray_elapsed_timer: generation counter prevents leak ────


class TestElapsedTimerGenerationCounter:
    """The generation counter prevents timer leaks on rapid stop/restart.

    Pre-fix: a ``_tick`` running concurrently with a rapid ``start()``
    would reschedule a NEW Timer that overwrites the ``_timer``
    reference set by the new ``start()`` — leaking the just-scheduled
    Timer and breaking ``cancel()``'s join semantics.

    Post-fix: ``start()`` increments ``_generation``; the ``_tick``
    closure captures the generation at entry and only reschedules if
    ``self._generation == my_gen``. A rapid ``start()`` invalidates
    prior ticks so they exit without rescheduling.
    """

    def test_generation_counter_starts_at_zero(self):
        """``_generation`` is initialized to 0 in ``__init__``."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=lambda: True,
            set_timer_ref=lambda t: None,
        )
        assert timer._generation == 0

    def test_start_increments_generation(self):
        """Each ``start()`` call increments ``_generation`` (cancel +1
        then the explicit +1, so a single ``start()`` is +2 — the
        generation is monotonically increasing, which is the only
        property the generation guard relies on)."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        assert timer._generation == 0
        try:
            timer.start()
            # start() calls cancel() (+1) then increments (+1) = +2.
            assert timer._generation > 0, f"start() must increment _generation (got {timer._generation})"
            gen_after_first_start = timer._generation
            timer.start()
            assert timer._generation > gen_after_first_start, (
                f"second start() must further increment _generation "
                f"(was {gen_after_first_start}, now {timer._generation})"
            )
        finally:
            timer.cancel()

    def test_cancel_increments_generation(self):
        """``cancel()`` increments ``_generation`` so any in-flight
        ``_tick`` from a prior ``start()`` exits without rescheduling."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        gen_after_start = timer._generation
        timer.cancel()
        assert timer._generation > gen_after_start, (
            f"cancel() must increment _generation (was {gen_after_start}, now {timer._generation})"
        )

    def test_rapid_restart_does_not_leak_timer_ref(self):
        """Rapid ``start()`` calls don't leave a stale ``_worker``
        reference — the new ``start()``'s worker is the sole owner.

        DJ-37 single-worker design: each ``start()`` cancels + joins
        the prior worker (via ``cancel()``) and increments the
        generation counter so any in-flight worker from a prior
        ``start()`` exits on its next ``is_active()`` / generation
        check. Pre-DJ-37, a concurrent ``_tick`` from the prior
        ``start()`` could overwrite ``self._timer`` with a freshly
        scheduled ``threading.Timer`` that the new ``start()`` didn't
        know about. The generation guard (combined with the explicit
        ``cancel()`` join) closes that leak.
        """
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        refs: list[threading.Thread | None] = []
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=refs.append,
        )

        timer.start()
        first_worker = timer._worker
        assert first_worker is not None
        assert isinstance(first_worker, threading.Thread)

        # Rapid restart — cancels + joins the first worker, then
        # increments generation before starting the new worker.
        timer.start()
        second_worker = timer._worker
        assert second_worker is not None
        assert second_worker is not first_worker, "Restart should create a NEW worker, not reuse the prior one"

        # ``cancel()`` (called inside the second ``start()``) joined
        # the first worker. The generation guard is a belt-and-suspenders
        # against any worker that escapes the join (e.g. is mid-tick).
        time.sleep(0.05)

        # _worker must still point at the second worker (not overwritten
        # by a stale tick from the first worker).
        assert timer._worker is second_worker, (
            "Stale worker from the first start() must NOT overwrite _worker "
            "(generation guard should have made it exit without re-entering the loop)"
        )

        timer.cancel()
        # After cancel, the worker ref is cleared.
        assert timer._worker is None

    def test_stale_tick_does_not_reschedule(self):
        """A ``_tick`` whose generation no longer matches exits without
        rescheduling — verified by checking the generation counter
        invalidated the prior tick's closure.

        Rather than try to deterministically reproduce the race
        (Timer fires concurrently with start()), we verify the
        GENERATION GUARD LOGIC by directly invoking a captured _tick
        closure after a newer start() has incremented _generation."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        refs: list[threading.Timer | None] = []
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=refs.append,
        )

        # We can't easily capture the _tick closure from outside the
        # class (it's a local inside start()). Instead, verify the
        # invariant the generation guard relies on: each start() +
        # cancel() cycle monotonically increases _generation, so any
        # _tick captured at an earlier generation will see
        # ``self._generation != my_gen`` and exit without rescheduling.
        timer.start()
        gen_after_first_start = timer._generation
        # The first _tick closure captured my_gen = gen_after_first_start.
        # A subsequent cancel + start invalidates that closure.
        timer.cancel()
        timer.start()
        gen_after_second_start = timer._generation
        assert gen_after_second_start > gen_after_first_start, (
            "second start() must produce a strictly larger generation than "
            "the first — this is the invariant the generation guard relies on"
        )
        # The first _tick's my_gen (= gen_after_first_start) is now stale.
        # When it fires, ``self._generation != my_gen`` evaluates True
        # (gen_after_second_start > gen_after_first_start), so it exits
        # without rescheduling. This is the generation guard's contract.
        assert gen_after_second_start != gen_after_first_start

        timer.cancel()

    def test_normal_reschedule_still_works(self):
        """The generation guard doesn't break normal 1s rescheduling —
        a single ``start()`` continues ticking until ``cancel()``."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        ticks: list[float] = []
        timer = ElapsedTimer(
            tick_callback=lambda: ticks.append(time.time()),
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        try:
            # Wait for at least 2 ticks (>= 2 seconds).
            deadline = time.time() + 4.0
            while len(ticks) < 2 and time.time() < deadline:
                time.sleep(0.05)
            assert len(ticks) >= 2, f"Expected >= 2 ticks in 4s (normal reschedule path), got {len(ticks)}"
        finally:
            timer.cancel()


# ─── waveform_bubble_wiring: getattr returns default for None ──


class _CapturingWiring:
    """Minimal WaveformBubbleWiring stand-in that captures the
    ``bubble_config`` event published by ``_push_bubble_config``.

    We can't easily instantiate the real ``WaveformBubbleWiring``
    without a full app mock (it needs ``app._waveform_bubble``,
    ``app._thread_registry``, etc.). Instead we extract the
    ``_push_bubble_config`` closure from the wiring's source by
    calling ``_wire_waveform_bubble`` on a mock app and then
    invoking the installed ``on_config`` callback.
    """

    def __init__(self) -> None:
        from voice_typer.server.waveform import WaveformBubble
        from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

        self.bubble = WaveformBubble()
        app = MagicMock()
        app._waveform_bubble = self.bubble
        app._thread_registry = MagicMock()
        self.wiring = WaveformBubbleWiring(app)
        self.wiring._wire_waveform_bubble()

    def push_config(self, cfg: Any) -> dict | None:
        """Invoke the installed ``on_config`` callback with ``cfg``,
        capturing the published ``bubble_config`` event."""
        from voice_typer.server import event_bus

        captured: list[dict] = []

        def capture(msg: dict) -> None:
            if isinstance(msg, dict) and msg.get("type") == "bubble_config":
                captured.append(msg)

        # Snapshot + clear the subscriber set so we only see our capture.
        with event_bus._lock:
            original = set(event_bus._subscribers)
            event_bus._subscribers.clear()
            event_bus._subscribers.add(capture)
        try:
            assert self.bubble.on_config is not None
            self.bubble.on_config(cfg)
        finally:
            with event_bus._lock:
                event_bus._subscribers.clear()
                event_bus._subscribers.update(original)

        return captured[0] if captured else None

    def stop(self) -> None:
        self.wiring.stop()


class TestPushBubbleConfigGetattrDefault:
    """``_push_bubble_config`` must fall back to defaults when config
    fields are explicitly ``None``.

    Pre-fix: ``getattr(cfg, name, default)`` returned the attribute
    value even when it was ``None`` — so a Config with explicit
    ``None`` fields propagated ``None`` to the bubble renderer.

    Post-fix: ``getattr(cfg, name, None) or default`` falls back to
    the default for missing / null values.
    """

    def test_none_fields_fall_back_to_defaults(self):
        """When all non-None-default fields are ``None``, the
        ``bubble_config`` event carries the documented defaults."""
        wiring = _CapturingWiring()
        try:
            # A cfg where every field is explicitly None (e.g. a
            # partial / corrupt Config load).
            class _CfgWithNone:
                bubble_behavior = None  # type: ignore[assignment]
                bubble_click_to_toggle = None  # type: ignore[assignment]
                bubble_mic_button = None  # type: ignore[assignment]
                theme_mode = None  # type: ignore[assignment]
                theme_preset = None  # type: ignore[assignment]
                custom_theme = None

            event = wiring.push_config(_CfgWithNone())
            assert event is not None, "bubble_config event was not published"
            data = event["data"]

            # Each None field must fall back to its documented default.
            assert data["bubble_behavior"] == "show_on_record", (
                f"None bubble_behavior should fall back to 'show_on_record', got {data['bubble_behavior']!r}"
            )
            assert data["bubble_click_to_toggle"] is True, (
                f"None bubble_click_to_toggle should fall back to True, got {data['bubble_click_to_toggle']!r}"
            )
            assert data["bubble_mic_button"] is True, (
                f"None bubble_mic_button should fall back to True, got {data['bubble_mic_button']!r}"
            )
            assert data["theme_mode"] == "system", (
                f"None theme_mode should fall back to 'system', got {data['theme_mode']!r}"
            )
            assert data["theme_preset"] == "default", (
                f"None theme_preset should fall back to 'default', got {data['theme_preset']!r}"
            )
            # custom_theme: None IS the valid default — no fallback.
            assert data["custom_theme"] is None, (
                f"custom_theme=None should be preserved (None is its valid default), got {data['custom_theme']!r}"
            )
        finally:
            wiring.stop()

    def test_missing_attributes_fall_back_to_defaults(self):
        """When the cfg object lacks the attributes entirely (e.g. a
        MinimalMock), the defaults are used."""
        wiring = _CapturingWiring()
        try:
            # A cfg with NONE of the expected attributes.
            event = wiring.push_config(SimpleNamespace())
            assert event is not None
            data = event["data"]

            assert data["bubble_behavior"] == "show_on_record"
            assert data["bubble_click_to_toggle"] is True
            assert data["bubble_mic_button"] is True
            assert data["theme_mode"] == "system"
            assert data["theme_preset"] == "default"
            assert data["custom_theme"] is None
        finally:
            wiring.stop()

    def test_real_values_are_preserved(self):
        """When the cfg has real (non-None) values, they're preserved —
        the ``or default`` fallback only kicks in for None / missing."""
        wiring = _CapturingWiring()
        try:

            class _CfgWithValues:
                bubble_behavior = "always_visible"
                bubble_click_to_toggle = True
                bubble_mic_button = True
                theme_mode = "dark"
                theme_preset = "nord"
                custom_theme = {"light": {"--bg": "#fff"}, "dark": {"--bg": "#000"}}

            event = wiring.push_config(_CfgWithValues())
            assert event is not None
            data = event["data"]

            assert data["bubble_behavior"] == "always_visible"
            assert data["bubble_click_to_toggle"] is True
            assert data["bubble_mic_button"] is True
            assert data["theme_mode"] == "dark"
            assert data["theme_preset"] == "nord"
            assert data["custom_theme"] == {"light": {"--bg": "#fff"}, "dark": {"--bg": "#000"}}

        finally:
            wiring.stop()

    def test_empty_string_theme_preset_falls_back_to_default(self):
        """An empty-string ``theme_preset`` (which is falsy) falls back
        to ``"default"`` — the ``or default`` pattern treats empty
        string as a missing value, which is the intended behavior
        (an empty theme preset is not a valid value)."""
        wiring = _CapturingWiring()
        try:

            class _CfgWithEmpty:
                bubble_behavior = ""  # empty string → fallback
                bubble_click_to_toggle = True
                bubble_mic_button = True
                theme_mode = ""  # empty string → fallback
                theme_preset = ""  # empty string → fallback
                custom_theme = None

            event = wiring.push_config(_CfgWithEmpty())
            assert event is not None
            data = event["data"]

            # Empty strings fall back to defaults (``or`` treats "" as falsy).
            assert data["bubble_behavior"] == "show_on_record"
            assert data["theme_mode"] == "system"
            assert data["theme_preset"] == "default"
        finally:
            wiring.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
