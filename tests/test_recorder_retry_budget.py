"""Sliding-window retry-budget tests for the recorder disconnect path.

Background
----------
``Recorder._handle_device_disconnect`` increments
``_device_disconnect_retries`` and fires ``on_device_lost`` only when
the counter exceeds ``_max_disconnect_retries``. But
``DisconnectHandler.restart_stream`` resets the counter to 0 on every
SUCCESSFUL restart. So a Bluetooth mic that disconnects + reconnects
every ~30s never reaches the threshold and the user never sees
"Microphone disconnected" — the recorder silently recovers forever.

Fix
---
A sliding-window retry budget (``collections.deque`` of
``time.monotonic()`` timestamps) tracks recent successful restarts.
When N restarts (default 3) accumulate within T seconds (default 60s),
``restart_stream`` fires ``on_device_lost`` (mirroring the max-retries
callback-resolution + fallback chain in ``_handle_device_disconnect``)
and clears the deque. A single disconnect+restart leaves the deque
with 1 entry — well below the threshold — so the normal
retry-then-recover flow is unaffected.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Test helpers (mirrored from test_recorder_worker_lifecycle.py) ──


class _OkStream:
    """No-op InputStream mock for tests that don't touch real audio."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _patch_ok_stream(monkeypatch, recording_mod):
    """Patch sounddevice with a no-op InputStream + permissive device query.

    Mirrors the helper in ``tests/test_recorder_worker_lifecycle.py`` so
    the recorder can be ``start()``-ed headless without real audio hardware
    and ``restart_stream`` succeeds on every call.
    """
    monkeypatch.setattr(recording_mod.sd, "InputStream", _OkStream)

    def _query_devices(*args, **kwargs):
        device_dict = {
            "max_input_channels": 1,
            "default_samplerate": 16000,
            "hostapi": 0,
            "index": 0,
            "name": "Mock Input",
        }
        if not args and not kwargs:
            return [device_dict]
        return device_dict

    monkeypatch.setattr(recording_mod.sd, "query_devices", _query_devices)
    monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})


# ── Tests ──────────────────────────────────────────────────────────


class TestRetryBudgetSlidingWindow:
    """The deque-based sliding-window retry budget gates
    ``on_device_lost`` for flapping devices while leaving the normal
    retry-then-recover flow intact."""

    def test_single_disconnect_restart_does_not_fire_on_device_lost(self, monkeypatch):
        """A single disconnect+restart cycle must NOT fire
        ``on_device_lost`` — the deque has 1 entry, well below the
        threshold (default 3). This is the regression guard: the fix
        must not break the normal recovery flow."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            device_lost_calls: list[bool] = []
            r.on_device_lost = lambda: device_lost_calls.append(True)

            captured_gen = r._stop_generation
            r._handle_device_disconnect(_captured_generation=captured_gen)

            assert device_lost_calls == [], (
                "Single disconnect+restart must NOT fire on_device_lost. "
                f"Got {len(device_lost_calls)} calls. The sliding-window "
                "budget should leave the deque with 1 entry (well below "
                "the default threshold of 3)."
            )
            assert len(r._restart_timestamps) == 1, (
                "After one successful restart, the deque should hold "
                f"exactly 1 timestamp (got {len(r._restart_timestamps)})."
            )
            assert r._devices._device_disconnect_retries == 0, (
                "Per-attempt counter should be reset to 0 by the successful restart (existing behavior preserved)."
            )
        finally:
            r._devices._device_disconnected = False
            r.stop()

    def test_flapping_device_3_restarts_in_60s_fires_on_device_lost(self, monkeypatch):
        """A BT mic that disconnects + reconnects 3 times within 60s
        (a flap) MUST fire ``on_device_lost`` on the 3rd successful
        restart. Pre-fix, the per-attempt counter was reset on every
        successful restart, so the threshold was never reached and the
        user never saw "Microphone disconnected"."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            device_lost_calls: list[bool] = []
            r.on_device_lost = lambda: device_lost_calls.append(True)

            captured_gen = r._stop_generation

            # Cycle 1: disconnect + successful restart.
            r._handle_device_disconnect(_captured_generation=captured_gen)
            assert device_lost_calls == [], (
                "Cycle 1: on_device_lost must NOT fire yet (deque has 1 entry, below the threshold of 3)."
            )
            assert len(r._restart_timestamps) == 1

            # Cycle 2: another disconnect + successful restart (still
            # within the 60s window).
            r._handle_device_disconnect(_captured_generation=captured_gen)
            assert device_lost_calls == [], (
                "Cycle 2: on_device_lost must NOT fire yet (deque has 2 entries, below the threshold of 3)."
            )
            assert len(r._restart_timestamps) == 2

            # Cycle 3: third disconnect + successful restart within 60s
            # → flap detected → on_device_lost fires. The deque is then
            # cleared so the next session gets a fresh budget.
            r._handle_device_disconnect(_captured_generation=captured_gen)
            assert len(device_lost_calls) == 1, (
                "Cycle 3: on_device_lost MUST fire on the 3rd successful "
                "restart within the 60s window (flap detected). Got "
                f"{len(device_lost_calls)} calls. Pre-fix the per-attempt "
                "counter was reset on every successful restart so the "
                "threshold was never reached."
            )
            assert len(r._restart_timestamps) == 0, (
                "After firing on_device_lost, the deque must be cleared so "
                "a subsequent restart within the window doesn't immediately "
                "re-trigger (got "
                f"{len(r._restart_timestamps)} entries)."
            )
        finally:
            r._devices._device_disconnected = False
            r.stop()

    def test_old_restarts_pruned_outside_window(self, monkeypatch):
        """Restarts older than ``_flapping_window_seconds`` are pruned
        from the deque, so a slow-flapping device (e.g. one disconnect
        per 5 minutes) never reaches the threshold. This guards against
        a regression where the deque grows unbounded."""
        import time as _time

        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # Deterministic clock: ``time.monotonic()`` on Windows is
        # quantized to the ~15.6ms system timer tick, so a real 0.06s
        # ``time.sleep`` can advance the clock by only 3 ticks (46.8ms)
        # — LESS than the 0.05s window — making the prune step miss and
        # the flap detector spuriously fire (~7% flake rate observed).
        # Patch ``time.monotonic`` with a fake clock the test advances
        # explicitly. Both ``recorder.py`` and ``disconnect_handler.py``
        # do ``import time`` (same stdlib module), so this patch covers
        # the flap-detection timestamps; ``monkeypatch`` restores it
        # after the test. A frozen clock also keeps the mic-watcher /
        # health-checker TTL logic from firing mid-test (more
        # deterministic, no spurious disconnects).
        fake_clock = {"t": 1000.0}

        def _fake_monotonic() -> float:
            return fake_clock["t"]

        monkeypatch.setattr(_time, "monotonic", _fake_monotonic)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        # Shrink the window to 0.05s so we don't have to wait for a
        # real 60s.
        r._flapping_window_seconds = 0.05
        r.start()
        try:
            device_lost_calls: list[bool] = []
            r.on_device_lost = lambda: device_lost_calls.append(True)

            captured_gen = r._stop_generation

            # Two rapid restarts (below threshold).
            r._handle_device_disconnect(_captured_generation=captured_gen)
            r._handle_device_disconnect(_captured_generation=captured_gen)
            assert len(r._restart_timestamps) == 2
            assert device_lost_calls == []

            # Advance the clock past the window so the next restart's
            # prune step evicts the two old timestamps (with a margin
            # well above the 15.6ms tick quantization).
            fake_clock["t"] += 0.06

            # Third restart — the prune step evicts the 2 old entries
            # BEFORE the threshold check, so the deque has only 1 entry
            # (the new one) and on_device_lost must NOT fire.
            r._handle_device_disconnect(_captured_generation=captured_gen)
            assert device_lost_calls == [], (
                "Old restarts outside the window must be pruned — the "
                "deque should have 1 entry (the new restart), not 3. "
                "Got on_device_lost fired, which means pruning is broken."
            )
            assert len(r._restart_timestamps) == 1, (
                "After pruning, the deque should hold only the new "
                f"restart's timestamp (got {len(r._restart_timestamps)})."
            )
        finally:
            r._flapping_window_seconds = 60.0
            r._devices._device_disconnected = False
            r.stop()

    def test_start_clears_restart_timestamps(self, monkeypatch):
        """``Recorder.start()`` must clear ``_restart_timestamps`` so a
        fresh session doesn't inherit a stale flap-detection window
        from the prior session."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Simulate two prior restarts (deque has 2 entries).
            captured_gen = r._stop_generation
            r._handle_device_disconnect(_captured_generation=captured_gen)
            r._handle_device_disconnect(_captured_generation=captured_gen)
            assert len(r._restart_timestamps) == 2

            # Stop and restart — start() must clear the deque.
            r.stop()
            assert len(r._restart_timestamps) == 2, "stop() should NOT clear the deque (only start() does)."
            r.start()
            assert len(r._restart_timestamps) == 0, (
                "start() must clear _restart_timestamps so a fresh session "
                "doesn't inherit a stale flap-detection window. Got "
                f"{len(r._restart_timestamps)} entries."
            )
        finally:
            r._devices._device_disconnected = False
            r.stop()

    def test_threshold_constant_default(self):
        """The default flap-detection threshold is 3 restarts in 60s.
        This pins the default so a future change doesn't silently
        widen or narrow the window."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r._flapping_max_restarts == 3, "Default flap-detection threshold must be 3 restarts."
        assert r._flapping_window_seconds == 60.0, "Default flap-detection window must be 60 seconds."


# ── BT-aware retry policy wiring (DJ-70) ──────────────────────────


class TestBTAwareRetryPolicyWiring:
    """DJ-70: ``_handle_device_disconnect`` must consult the BT-aware
    retry-policy helpers (``_build_device_info_for_retry_policy`` /
    ``_get_max_retries_for_device`` / ``_get_retry_sleep_for_device``)
    on every invocation so Bluetooth headsets get 6 retries + 0.75s
    inter-retry sleep (covering the 1-3s HFP mode-switch window),
    while non-BT devices keep the immediate 3-retry budget.

    Pre-fix, the helpers existed on ``DeviceManager`` but were never
    called from the production disconnect path — ``_handle_device_disconnect``
    used the fixed ``_max_disconnect_retries = 3`` and never slept
    between retries, so a BT headset mode-switch (1-3s) exceeded the
    3-retry budget (~100ms at 32ms cadence) and the recording was
    terminated."""

    def test_handle_device_disconnect_calls_bt_aware_helpers(self):
        """Source inspection: ``_handle_device_disconnect`` must call
        ``_build_device_info_for_retry_policy`` +
        ``_get_max_retries_for_device`` + ``_get_retry_sleep_for_device``
        so the BT-aware budget + sleep is wired into the production
        retry path."""
        import inspect

        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._handle_device_disconnect)
        assert "_build_device_info_for_retry_policy" in src, (
            "DJ-70: _handle_device_disconnect must call _build_device_info_for_retry_policy to detect BT devices."
        )
        assert "_get_max_retries_for_device" in src, (
            "DJ-70: _handle_device_disconnect must call _get_max_retries_for_device to use the BT-aware retry budget."
        )
        assert "_get_retry_sleep_for_device" in src, (
            "DJ-70: _handle_device_disconnect must call _get_retry_sleep_for_device to sleep between BT retries."
        )
        assert "time.sleep(_retry_sleep)" in src, (
            "DJ-70: _handle_device_disconnect must sleep for the BT-aware retry interval between retries."
        )

    def test_non_bt_device_uses_immediate_retry(self, monkeypatch):
        """Behavioral: a non-BT device (no BT keyword, 48 kHz native
        rate) gets the immediate 3-retry budget — no sleep between
        retries. This preserves the pre-fix behavior for non-BT
        devices so the fix doesn't regress the common case."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Mock the BT-aware helpers to return the non-BT policy.
            r._devices._build_device_info_for_retry_policy = lambda: {
                "name": "USB Mic",
                "default_samplerate": 48000,
            }
            r._devices._get_max_retries_for_device = lambda info: 3
            r._devices._get_retry_sleep_for_device = lambda info: 0.0

            sleep_calls: list[float] = []
            import voice_typer.server.recording.recorder as rec_mod

            monkeypatch.setattr(rec_mod.time, "sleep", lambda s: sleep_calls.append(s))

            captured_gen = r._stop_generation
            r._handle_device_disconnect(_captured_generation=captured_gen)

            # Non-BT device → no sleep (immediate retry).
            assert sleep_calls == [], (
                "Non-BT device must NOT sleep between retries (preserves "
                f"pre-fix behavior). Got sleep calls: {sleep_calls}"
            )
        finally:
            r._devices._device_disconnected = False
            r.stop()

    def test_bt_device_sleeps_between_retries(self, monkeypatch):
        """Behavioral: a BT device (named 'Bluetooth Headset', 8 kHz
        HFP native rate) gets the 6-retry budget AND a 0.75s sleep
        before each retry attempt past the first. This is the core
        DJ-70 fix — without the sleep, the 6-retry budget fires within
        ~200ms (6 × 32ms cadence), still too fast for the 1-3s HFP
        mode-switch window."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Mock the BT-aware helpers to return the BT policy.
            r._devices._build_device_info_for_retry_policy = lambda: {
                "name": "Bluetooth Headset",
                "default_samplerate": 8000,
            }
            r._devices._get_max_retries_for_device = lambda info: 6
            r._devices._get_retry_sleep_for_device = lambda info: (
                0.75 if r._devices._get_max_retries_for_device(info) >= 6 else 0.0
            )

            sleep_calls: list[float] = []
            import voice_typer.server.recording.recorder as rec_mod

            monkeypatch.setattr(rec_mod.time, "sleep", lambda s: sleep_calls.append(s))

            # Simulate the 2nd retry attempt (retry counter starts at 0,
            # _handle_device_disconnect increments to 1 on first call,
            # 2 on second call). The sleep should fire on the 2nd+ call.
            r._devices._device_disconnect_retries = 1  # will be incremented to 2
            captured_gen = r._stop_generation
            r._handle_device_disconnect(_captured_generation=captured_gen)

            assert len(sleep_calls) == 1, (
                "BT device must sleep exactly once between retries (on "
                f"the 2nd+ attempt). Got {len(sleep_calls)} sleep calls."
            )
            assert sleep_calls[0] == 0.75, (
                f"BT retry sleep must be 0.75s (default _bt_retry_sleep_seconds). Got {sleep_calls[0]}"
            )
        finally:
            r._devices._device_disconnected = False
            r.stop()

    def test_bt_device_first_retry_no_sleep(self, monkeypatch):
        """Behavioral: the FIRST retry attempt for a BT device must
        NOT sleep (no prior failure to recover from). The sleep only
        fires on the 2nd+ attempt so the initial recovery is
        immediate."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            r._devices._build_device_info_for_retry_policy = lambda: {
                "name": "Bluetooth Headset",
                "default_samplerate": 8000,
            }
            r._devices._get_max_retries_for_device = lambda info: 6
            r._devices._get_retry_sleep_for_device = lambda info: 0.75

            sleep_calls: list[float] = []
            import voice_typer.server.recording.recorder as rec_mod

            monkeypatch.setattr(rec_mod.time, "sleep", lambda s: sleep_calls.append(s))

            # First attempt: retry counter starts at 0, incremented to 1.
            r._devices._device_disconnect_retries = 0
            captured_gen = r._stop_generation
            r._handle_device_disconnect(_captured_generation=captured_gen)

            assert sleep_calls == [], (
                "First BT retry attempt must NOT sleep (no prior failure "
                f"to recover from). Got sleep calls: {sleep_calls}"
            )
        finally:
            r._devices._device_disconnected = False
            r.stop()
