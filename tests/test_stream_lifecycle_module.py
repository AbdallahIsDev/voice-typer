"""Focused unit tests for :mod:`voice_typer.server.recording.stream_lifecycle`.

Phase 4.5 — extracted from the 3772-LOC ``Recorder`` god class.
These tests exercise the public API of
:class:`StreamLifecycle` (the collaborator extracted from ``Recorder``)
without instantiating a real :class:`Recorder` and without touching real
audio hardware / subprocess.

External dependencies are mocked:

- ``sd.InputStream`` — patched on the ``stream_lifecycle`` module's lazy
  ``sd`` proxy with a fake factory (returns a ``MagicMock`` that records
  ``start()`` / ``stop()`` / ``close()`` calls).
- ``recorder`` — a small ``MagicMock``-backed stub exposing only the
  attributes the extracted bodies touch (``_lock``,
  ``_effective_sr``, ``_actual_channels``, ``config``,
  ``_devices._resolve_effective_sample_rate``,
  ``_devices._cached_max_input_channels``,
  ``_devices._all_input_device_candidates``,
  ``_stream_finished_callback``,
  ``_is_in_audio_callback``, ``_audio_callback_dispatch``,
  ``_current_callback``).
- ``time.sleep`` — patched on the ``stream_lifecycle`` module so the
  in-flight callback poll loop in :meth:`teardown_stream_body` does not
  actually sleep during tests.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from voice_typer.server._audio_constants import scaled_audio_blocksize
from voice_typer.server.recording import stream_lifecycle as sl_module
from voice_typer.server.recording.stream_lifecycle import StreamLifecycle

# ── Helpers ───────────────────────────────────────────────────────────


def _make_recorder_stub() -> Any:
    """Build a minimal mock Recorder suitable for StreamLifecycle tests.

    The stub exposes only the shared attributes the extracted bodies
    touch (see the collaborator-pattern list in ``stream_lifecycle.py``'s
    module docstring). ``_lock`` is a real ``threading.Lock`` so the
    ``with recorder._audio_pipeline._lock:`` block actually serializes; everything else
    is a ``MagicMock``.
    """
    recorder = MagicMock()
    # ``_lock`` MUST be a real Lock because the bodies use it as a
    # context manager. ``MagicMock``'s ``__enter__`` would silently
    # succeed but not provide real serialization — fine for these
    # tests, but a real Lock makes the tests representative of the
    # production behaviour.
    recorder._audio_pipeline._lock = threading.Lock()
    # ``_is_in_audio_callback`` is a real ``threading.Event`` for the
    # ``build_audio_callback`` / ``teardown_stream_body`` tests.
    recorder._is_in_audio_callback = threading.Event()
    # STATE-OWNERSHIP: the PortAudio ``_stream`` slot lives on
    # the ``StreamLifecycle`` instance (constructed with ``None`` by
    # its ``__init__``); tests that exercise teardown set
    # ``lifecycle._stream`` explicitly after construction.
    # ``config.recording_channels`` defaults to 1 (mono) in the
    # production Config dataclass.
    recorder.config.recording_channels = 1
    # ``_resolve_effective_sample_rate`` returns ``(rate, dev_info)``
    # by default — tests override per-call returns via ``side_effect``.
    recorder._devices._resolve_effective_sample_rate.return_value = (16000, None)
    # ``_cached_max_input_channels`` returns 1 (mono) by default.
    recorder._devices._cached_max_input_channels.return_value = 1
    # ``_all_input_device_candidates`` returns an empty list by
    # default — tests override.
    recorder._devices._all_input_device_candidates.return_value = []
    # ``_stream_finished_callback`` is just a callable marker.
    recorder._stream_finished_callback = MagicMock(name="_stream_finished_callback")
    # ``_audio_callback_dispatch`` is a callable the closure delegates to.
    recorder._audio_callback_dispatch = MagicMock(name="_audio_callback_dispatch")
    return recorder


def _make_fake_stream_factory(
    monkeypatch, *, fail_indices: set[int] | None = None, actual_samplerate: int | None = None
):
    """Install a fake ``sd.InputStream`` factory on the module's ``sd`` proxy.

    Returns a list that accumulates ``(constructor_kwargs_dict, stream_obj)``
    tuples in invocation order so tests can assert on which candidates were
    attempted and with what parameters.

    ``fail_indices`` (optional) — a set of 0-based attempt-indices that
    should raise ``RuntimeError`` from the factory instead of returning
    a stream (simulates a PortAudio open failure on the Nth candidate).

    ``actual_samplerate`` (optional) — sets ``stream.samplerate`` on the
    returned fake stream so the AUDIO-BT detection branch can be
    exercised. ``None`` means the fake stream has no ``samplerate``
    attribute (matching the ``hasattr`` fallback).
    """
    attempts: list[tuple[dict, Any]] = []
    fail_indices = fail_indices or set()

    def fake_input_stream(**kwargs):
        idx = len(attempts)
        # Record the attempt BEFORE deciding to fail so tests can see
        # what parameters were passed even on the failing call.
        if idx in fail_indices:
            attempts.append((kwargs, None))
            raise RuntimeError(f"fake open failure on attempt {idx}")
        stream = MagicMock(name=f"fake_stream_{idx}")
        stream.start = MagicMock(name="start")
        stream.stop = MagicMock(name="stop")
        stream.close = MagicMock(name="close")
        if actual_samplerate is not None:
            stream.samplerate = actual_samplerate
        # else: leave ``samplerate`` unset so ``hasattr(stream, "samplerate")``
        # returns False (exercises the ``else candidate_sr`` branch).
        attempts.append((kwargs, stream))
        return stream

    # The lazy ``sd`` proxy on ``stream_lifecycle`` re-resolves
    # ``sys.modules`` on every attribute access, so patching the
    # module-level ``sd`` attribute directly is the most robust
    # approach for unit tests.
    fake_sd = MagicMock(name="fake_sd")
    fake_sd.InputStream = MagicMock(side_effect=fake_input_stream)
    monkeypatch.setattr(sl_module, "sd", fake_sd)
    return attempts


# ── Tests: build_audio_callback ───────────────────────────────────────


class TestBuildAudioCallback:
    """Body of ``Recorder._build_audio_callback`` (no source-inspection
    constraints — full extraction)."""

    def test_returns_callable_and_stores_current_callback(self):
        recorder = _make_recorder_stub()
        lifecycle = StreamLifecycle(recorder)

        cb = lifecycle.build_audio_callback(recorder)

        assert callable(cb)
        # AUDIO-HOT: store callback reference for device restart
        assert recorder._current_callback is cb

    def test_callback_sets_flag_dispatches_and_clears_flag(self):
        recorder = _make_recorder_stub()
        lifecycle = StreamLifecycle(recorder)
        cb = lifecycle.build_audio_callback(recorder)

        # Pre-condition: flag is clear.
        assert not recorder._is_in_audio_callback.is_set()

        # Sentinel payload — the closure is supposed to forward all
        # four positional args verbatim to ``_audio_callback_dispatch``.
        indata, frames, time_info, status = object(), 512, object(), 0
        cb(indata, frames, time_info, status)

        # The dispatch was invoked with the same args.
        recorder._audio_callback_dispatch.assert_called_once_with(indata, frames, time_info, status)
        # After the callback returns, the flag must be clear again
        # (the ``finally`` clause clears it even on exception).
        assert not recorder._is_in_audio_callback.is_set()

    def test_callback_clears_flag_even_when_dispatch_raises(self):
        recorder = _make_recorder_stub()
        recorder._audio_callback_dispatch.side_effect = RuntimeError("dispatch boom")
        lifecycle = StreamLifecycle(recorder)
        cb = lifecycle.build_audio_callback(recorder)

        with pytest.raises(RuntimeError, match="dispatch boom"):
            cb(object(), 0, object(), 0)

        # Flag is cleared in the ``finally`` clause even when dispatch
        # raised — without this, ``_teardown_stream`` would hang in its
        # poll loop waiting for a callback that already exited.
        assert not recorder._is_in_audio_callback.is_set()


# ── Tests: open_stream_for_candidates ──────────────────────────────────


class TestOpenStreamForCandidates:
    """Body of ``Recorder._open_stream_for_candidates`` (no source-
    inspection constraints — full extraction)."""

    def test_success_on_first_candidate(self, monkeypatch):
        recorder = _make_recorder_stub()
        attempts = _make_fake_stream_factory(monkeypatch)
        # Provide a dev_info_extra so the info-log branch fires once.
        recorder._devices._resolve_effective_sample_rate.return_value = (
            48000,
            {
                "name": "Mock Mic",
                "host_api_name": "ALSA",
                "native_rate": 48000,
            },
        )
        candidates = [7]
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, last_err = lifecycle.open_stream_for_candidates(
            recorder, candidates, callback, effective_sr=16000, last_error=None
        )

        assert selected == 7
        assert eff_sr == 48000
        assert last_err is None
        # Exactly one InputStream attempt.
        assert len(attempts) == 1
        kwargs, stream = attempts[0]
        # VAD-001 (rate-scaled): ~32 ms blocks at the native rate —
        # 1536 @ 48 kHz resamples to exactly 512 @ 16 kHz (the Silero
        # window). (Fixed 512 at 48 kHz was ~3× the designed callback/VAD
        # cadence.)
        assert kwargs["blocksize"] == scaled_audio_blocksize(48000)
        assert kwargs["blocksize"] == 1536
        assert kwargs["dtype"] == sl_module.np.float32 if hasattr(sl_module, "np") else 1
        assert kwargs["device"] == 7
        assert kwargs["callback"] is callback
        # AUDIO-HOT: finished_callback wired up.
        assert kwargs["finished_callback"] is recorder._stream_finished_callback
        # The stream was started.
        stream.start.assert_called_once_with()
        # _effective_sr updated under the lock.
        assert recorder._effective_sr == 48000
        # AUDIO-CH: actual_channels stored.
        assert recorder._actual_channels == 1
        # _stream is the opened stream.
        assert lifecycle._stream is stream

    def test_first_candidate_fails_second_succeeds(self, monkeypatch):
        recorder = _make_recorder_stub()
        # Make the first candidate's open fail, the second succeed.
        attempts = _make_fake_stream_factory(monkeypatch, fail_indices={0})
        # Give each candidate a distinct effective rate so we can
        # confirm the right one was persisted.
        recorder._devices._resolve_effective_sample_rate.side_effect = [
            (16000, None),
            (48000, None),
        ]
        candidates = [3, 9]
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, last_err = lifecycle.open_stream_for_candidates(
            recorder, candidates, callback, effective_sr=16000, last_error=None
        )

        # Two attempts (first failed, second succeeded).
        assert len(attempts) == 2
        assert selected == 9
        assert eff_sr == 48000
        # last_error is the last failure, NOT cleared on success.
        # (Per the original body: ``last_error`` is the most recent
        # exception encountered during iteration; on success the
        # ``break`` exits the loop without resetting it.)
        assert isinstance(last_err, RuntimeError)
        assert lifecycle._stream is attempts[1][1]
        assert recorder._effective_sr == 48000

    def test_all_candidates_fail_returns_none(self, monkeypatch):
        recorder = _make_recorder_stub()
        attempts = _make_fake_stream_factory(monkeypatch, fail_indices={0, 1})
        recorder._devices._resolve_effective_sample_rate.side_effect = [
            (16000, None),
            (48000, None),
        ]
        candidates = [3, 9]
        callback = MagicMock(name="callback")
        # The initial ``last_error`` passed in from start() is None;
        # the body should replace it with the most recent failure.
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, last_err = lifecycle.open_stream_for_candidates(
            recorder, candidates, callback, effective_sr=16000, last_error=None
        )

        assert selected is None
        # effective_sr is unchanged on failure (the body does NOT
        # advance it on a failed candidate).
        assert eff_sr == 16000
        assert isinstance(last_err, RuntimeError)
        # Both candidates attempted.
        assert len(attempts) == 2
        # On failure, the body sets ``lifecycle._stream = None`` (line
        # 2029 in recorder.py) and continues to the next candidate.
        assert lifecycle._stream is None
        # The failing stream's close() was suppressed (the body wraps
        # the close in ``contextlib.suppress(Exception)`` so a double
        # failure is OK).  The first failed attempt did not produce a
        # stream object (the factory returned None for failed indices).
        _, first_stream = attempts[0]
        assert first_stream is None
        # The second failed attempt also produced None.
        _, second_stream = attempts[1]
        assert second_stream is None

    def test_bluetooth_hfp_profile_detected(self, monkeypatch):
        """when the opened stream reports an 8/16 kHz actual
        sample rate that differs from the requested rate, the body logs
        an INFO message (no exception). The body must not raise."""
        recorder = _make_recorder_stub()
        # The fake stream reports an actual sample rate of 8000, while
        # the candidate requested 16000 — that triggers the BT branch.
        _make_fake_stream_factory(monkeypatch, actual_samplerate=8000)
        recorder._devices._resolve_effective_sample_rate.return_value = (16000, None)
        candidates = [1]
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, last_err = lifecycle.open_stream_for_candidates(
            recorder, candidates, callback, effective_sr=16000, last_error=None
        )

        # The BT branch only logs — it does NOT change selected_device
        # or effective_sr.
        assert selected == 1
        assert eff_sr == 16000
        assert last_err is None


# ── Tests: open_stream_fallback ──────────────────────────────────────


class TestOpenStreamFallback:
    """Body of ``Recorder._open_stream_fallback`` (no source-inspection
    constraints — full extraction)."""

    def test_success_returns_used_fallback_true(self, monkeypatch):
        recorder = _make_recorder_stub()
        attempts = _make_fake_stream_factory(monkeypatch)
        recorder._devices._all_input_device_candidates.return_value = [11, 12]
        recorder._devices._resolve_effective_sample_rate.return_value = (48000, None)
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, used_fb, last_err = lifecycle.open_stream_fallback(
            recorder,
            candidates=[3],
            callback=callback,
            effective_sr=16000,
            last_error=None,
        )

        assert selected == 11
        assert eff_sr == 48000
        assert used_fb is True
        assert last_err is None
        # The tried filter removed candidate 3 from the all-candidates
        # list BEFORE iterating, so only 11 and 12 should have been
        # attempted.  The first succeeded, so only one attempt.
        assert len(attempts) == 1
        assert attempts[0][0]["device"] == 11
        assert lifecycle._stream is attempts[0][1]
        assert recorder._effective_sr == 48000

    def test_tried_filter_excludes_already_tried_devices(self, monkeypatch):
        recorder = _make_recorder_stub()
        attempts = _make_fake_stream_factory(monkeypatch, fail_indices={0})
        # Candidate 5 was already tried in the primary loop; the
        # fallback must NOT re-attempt it.
        recorder._devices._all_input_device_candidates.return_value = [5, 7, 9]
        recorder._devices._resolve_effective_sample_rate.side_effect = [
            (16000, None),  # for 7 (fails)
            (48000, None),  # for 9 (succeeds)
        ]
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, used_fb, _ = lifecycle.open_stream_fallback(
            recorder,
            candidates=[5],
            callback=callback,
            effective_sr=16000,
            last_error=None,
        )

        # Candidate 5 was excluded — only 7 and 9 attempted.
        assert [a[0]["device"] for a in attempts] == [7, 9]
        assert selected == 9
        assert used_fb is True
        assert eff_sr == 48000

    def test_all_fallback_candidates_fail(self, monkeypatch):
        recorder = _make_recorder_stub()
        attempts = _make_fake_stream_factory(monkeypatch, fail_indices={0, 1, 2})
        recorder._devices._all_input_device_candidates.return_value = [11, 12, 13]
        recorder._devices._resolve_effective_sample_rate.return_value = (16000, None)
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, used_fb, last_err = lifecycle.open_stream_fallback(
            recorder,
            candidates=[3],
            callback=callback,
            effective_sr=16000,
            last_error=None,
        )

        assert selected is None
        assert eff_sr == 16000  # unchanged
        assert used_fb is False
        assert isinstance(last_err, RuntimeError)
        # All 3 fallback candidates attempted.
        assert len(attempts) == 3
        assert lifecycle._stream is None

    def test_fallback_logs_dev_info_when_available(self, monkeypatch, caplog):
        """When ``_resolve_effective_sample_rate`` returns a
        ``dev_info`` dict, the body logs an INFO record with the device
        name. The log must propagate via the package-level logger."""
        recorder = _make_recorder_stub()
        _make_fake_stream_factory(monkeypatch)
        recorder._devices._all_input_device_candidates.return_value = [42]
        recorder._devices._resolve_effective_sample_rate.return_value = (
            48000,
            {
                "name": "USB Headset",
                "host_api_name": "ALSA",
                "native_rate": 48000,
            },
        )
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            selected, _, used_fb, _ = lifecycle.open_stream_fallback(
                recorder,
                candidates=[],
                callback=callback,
                effective_sr=16000,
                last_error=None,
            )

        assert selected == 42
        assert used_fb is True
        # The post-success log line includes the device name even when
        # ``dev_info_extra`` was non-None ( guard).
        assert any("USB Headset" in rec.getMessage() for rec in caplog.records)

    def test_fallback_succeeds_with_unknown_dev_info(self, monkeypatch, caplog):
        """when ``dev_info_extra`` is None after a SUCCESSFUL open,
        the post-success log must use the ``(unknown)`` placeholder
        instead of raising ``TypeError`` on ``None["name"]``."""
        recorder = _make_recorder_stub()
        _make_fake_stream_factory(monkeypatch)
        recorder._devices._all_input_device_candidates.return_value = [42]
        recorder._devices._resolve_effective_sample_rate.return_value = (48000, None)
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            selected, _, used_fb, _ = lifecycle.open_stream_fallback(
                recorder,
                candidates=[],
                callback=callback,
                effective_sr=16000,
                last_error=None,
            )

        assert selected == 42
        assert used_fb is True
        # guard: the fallback placeholder must appear.
        assert any("(unknown)" in rec.getMessage() for rec in caplog.records)


# ── Tests: teardown_stream_body ──────────────────────────────────────


class TestTeardownStreamBody:
    """Body of ``Recorder._teardown_stream`` INSIDE the
    ``_stream_lifecycle_lock`` block. The lock acquisition stays on
    ``Recorder`` for source-inspection contracts (see
    ``tests/test_recorder_worker_lifecycle.py:471-472``)."""

    def test_no_stream_returns_immediately(self):
        recorder = _make_recorder_stub()
        # ``_stream`` is None by default in the stub.
        lifecycle = StreamLifecycle(recorder)

        # Must not raise even though there's nothing to tear down.
        lifecycle.teardown_stream_body(recorder)

        # ``lifecycle._stream`` is unchanged (still None) and no
        # ``stop``/``close`` calls were made.
        assert lifecycle._stream is None

    def test_stops_closes_and_clears_stream(self, monkeypatch):
        recorder = _make_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        # In-flight callback flag is NOT set — poll loop exits immediately.
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: the stream slot is set on the OWNING lifecycle.
        lifecycle._stream = fake_stream

        lifecycle.teardown_stream_body(recorder)

        fake_stream.stop.assert_called_once_with()
        fake_stream.close.assert_called_once_with()
        assert lifecycle._stream is None

    def test_polls_in_flight_callback_until_clear(self, monkeypatch):
        """when the in-flight callback flag is set,
        ``teardown_stream_body`` polls ``_is_in_audio_callback`` until it
        clears, then closes the stream. This test simulates the flag
        being cleared after one poll iteration (via a side-effect on
        ``time.sleep``)."""
        recorder = _make_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        # Flag starts SET (callback in-flight).
        recorder._is_in_audio_callback.set()

        # Track sleep calls — the FIRST sleep should clear the flag
        # (simulating the callback finishing).  Subsequent iterations of
        # the poll loop will see the flag clear and break.
        sleep_calls: list[float] = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            # Simulate the in-flight callback finishing on the first poll.
            if recorder._is_in_audio_callback.is_set():
                recorder._is_in_audio_callback.clear()

        monkeypatch.setattr(sl_module.time, "sleep", fake_sleep)
        # Also patch ``perf_counter`` so the deadline math is
        # deterministic — return a fixed time so ``_deadline - perf_counter``
        # is always positive (avoids early break on a busy CI box).
        monkeypatch.setattr(sl_module.time, "perf_counter", lambda: 0.0)
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: the stream slot is set on the OWNING lifecycle
        # (after construction — ``__init__`` initializes it to ``None``).
        lifecycle._stream = fake_stream

        lifecycle.teardown_stream_body(recorder)

        # The poll loop ran at least once — the first iteration saw the
        # flag set, slept, and the side-effect cleared the flag.  The
        # second iteration saw it clear and broke.
        assert len(sleep_calls) >= 1
        # The poll interval is 5ms (0.005).
        assert sleep_calls[0] == pytest.approx(0.005)
        # Stream was stopped, then closed, then ``_stream`` cleared.
        fake_stream.stop.assert_called_once_with()
        fake_stream.close.assert_called_once_with()
        assert lifecycle._stream is None
        # Flag is clear after teardown completes.
        assert not recorder._is_in_audio_callback.is_set()

    def test_poll_budget_exhaustion_closes_anyway(self, monkeypatch):
        """When the 300ms poll budget elapses and the flag is STILL set
        (callback genuinely stuck), the body must still call
        ``close()`` and clear ``_stream`` — the worst-case contract is
        "tear down anyway after the budget".  Without this,
        ``discard()`` could hang indefinitely on a stuck callback."""
        recorder = _make_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        # Flag stays SET for the entire budget.
        recorder._is_in_audio_callback.set()

        sleep_calls: list[float] = []
        # Drive ``perf_counter`` forward so the deadline math crosses
        # the 300ms budget.  Each call returns the next value in the
        # sequence — the budget check uses ``_deadline - perf_counter``
        # and breaks when ``remaining <= 0``.
        perf_counter_values = iter([0.0, 0.005, 0.010, 0.015, 0.350])

        def fake_perf_counter():
            try:
                return next(perf_counter_values)
            except StopIteration:
                return 1.0

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(sl_module.time, "perf_counter", fake_perf_counter)
        monkeypatch.setattr(sl_module.time, "sleep", fake_sleep)
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: the stream slot is set on the OWNING lifecycle.
        lifecycle._stream = fake_stream

        lifecycle.teardown_stream_body(recorder)

        # The body called stop() regardless of the stuck callback.
        fake_stream.stop.assert_called_once_with()
        # After the budget is exhausted, the body still calls close()
        # and clears ``_stream`` (worst-case contract).
        fake_stream.close.assert_called_once_with()
        assert lifecycle._stream is None

    def test_close_exception_propagates(self, monkeypatch):
        """If ``stream.close()`` raises, the exception propagates out of
        ``teardown_stream_body`` — the body does NOT swallow close
        failures.  (The ``_stream = None`` assignment in the body is
        after ``close()``, so a close failure leaves ``_stream`` set;
        the caller — ``Recorder._teardown_stream`` — is responsible for
        handling that case via its ``finally: lock.release()`` block.)"""
        recorder = _make_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        fake_stream.close.side_effect = OSError("close boom")
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: the stream slot is set on the OWNING lifecycle.
        lifecycle._stream = fake_stream

        with pytest.raises(OSError, match="close boom"):
            lifecycle.teardown_stream_body(recorder)

        fake_stream.stop.assert_called_once_with()


# ── Tests: StreamLifecycle.__init__ ──────────────────────────────────


class TestStreamLifecycleInit:
    """Constructor stores the back-reference AND owns the PortAudio
    ``_stream`` slot (STATE-OWNERSHIP: initialized to ``None``,
    the same sentinel the recorder-level declaration used)."""

    def test_init_stores_recorder_reference(self):
        recorder = _make_recorder_stub()
        lifecycle = StreamLifecycle(recorder)
        assert lifecycle._recorder is recorder


# ── Tests: rate-scaled recorder blocksize ─────────────────────────────


class TestScaledAudioBlocksize:
    """``scaled_audio_blocksize`` must size recorder stream blocks by the
    device's native rate so every chunk is ~32 ms of audio and resamples
    to EXACTLY 512 samples at 16 kHz (the Silero VAD window). The fixed
    512 block at native 48 kHz produced ~93.75 callbacks/sec (~3× the
    designed cadence) and made VAD hysteresis frame counts run ~3×
    faster than documented."""

    @pytest.mark.parametrize(
        ("native_rate", "expected_blocksize"),
        [
            (8000, 512),  # floor (256 would be too small)
            (16000, 512),  # 16000 * 0.032 == 512 exactly
            (22050, 705),  # int(22050 * 0.032)
            (44100, 1411),  # int(44100 * 0.032) → ceil(1411 * 160 / 441) == 512 @16k
            (48000, 1536),  # int(48000 * 0.032) → exactly 512 @16k
            (96000, 3072),  # int(96000 * 0.032) → exactly 512 @16k
        ],
    )
    def test_blocksize_scales_with_native_rate(self, native_rate, expected_blocksize):
        assert scaled_audio_blocksize(native_rate) == expected_blocksize

    def test_scaled_blocks_resample_to_silero_window(self):
        """Post-resample chunk length must be exactly 512 @ 16 kHz for
        the mainstream native rates (the whole point of the scaling)."""
        import math

        for native_rate in (44100, 48000, 96000):
            blocksize = scaled_audio_blocksize(native_rate)
            g = math.gcd(16000, native_rate)
            up, down = 16000 // g, native_rate // g
            resampled_len = -(-blocksize * up // down)  # ceil, same as resample_poly
            assert resampled_len == 512, (
                f"blocksize {blocksize} @ {native_rate} Hz resamples to {resampled_len}, expected 512"
            )

    @pytest.mark.parametrize("bad_rate", [0, -48000])
    def test_non_positive_rate_falls_back_to_512(self, bad_rate):
        """An unresolved (<= 0) rate must fall back to the fixed 512
        contract instead of computing a nonsense block."""
        assert scaled_audio_blocksize(bad_rate) == 512

    def test_open_stream_uses_scaled_blocksize_per_rate(self, monkeypatch):
        """The InputStream blocksize must follow the CANDIDATE's rate:
        a 44.1 kHz candidate gets 1411, a 16 kHz candidate keeps 512."""
        recorder = _make_recorder_stub()
        attempts = _make_fake_stream_factory(monkeypatch)
        recorder._devices._resolve_effective_sample_rate.side_effect = [
            (44100, None),
            (16000, None),
        ]
        candidates = [5, 6]
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, _last_err = lifecycle.open_stream_for_candidates(
            recorder, candidates, callback, effective_sr=16000, last_error=None
        )

        assert selected == 5
        assert eff_sr == 44100
        assert len(attempts) == 1
        kwargs, _stream = attempts[0]
        assert kwargs["blocksize"] == scaled_audio_blocksize(44100)
        assert kwargs["blocksize"] == 1411


# ── Tests: restart_stream (mid-session device reconnect) ───────────────


class TestRestartStreamScaledBlocksize:
    """The mid-session device-reconnect restart must open its
    replacement stream with the SAME rate-scaled blocksize the primary
    open paths use (``scaled_audio_blocksize(candidate_sr)``).

    ``DisconnectHandler.restart_stream`` re-opens with
    ``recorder._current_callback`` directly — a fixed 512 there would
    restore the ~94 callbacks/s cadence on 48 kHz devices after every
    hot-swap recovery, and the buffers sized by
    ``SessionState.resize_buffers_for_sample_rate`` (scaled chunk math)
    would no longer match the chunks this stream delivers.
    """

    def test_restart_stream_uses_scaled_blocksize_for_candidate_rate(self, monkeypatch):
        """A reconnect candidate resolved at 48 kHz must be reopened
        with blocksize=1536 (not the fixed 512)."""
        from voice_typer.server.recording import disconnect_handler as dh_module
        from voice_typer.server.recording.disconnect_handler import DisconnectHandler
        from voice_typer.server.recording.recorder import Recorder

        from tests.test_recording_lifecycle_fixes import _install_fake_input_stream

        config = MagicMock(sample_rate=16000, microphone=None, recording_channels=1)
        recorder = Recorder(config)
        # ``restart_stream`` re-opens with the callback captured from
        # the pre-disconnect stream.
        recorder._current_callback = MagicMock(name="_current_callback")
        # Resolve the reconnect candidate at 48 kHz.
        monkeypatch.setattr(recorder._devices, "_resolve_effective_sample_rate", lambda _d: (48000, None))
        attempts = _install_fake_input_stream(dh_module, monkeypatch)

        # Production caller holds ``_stream_lifecycle_lock``.
        with recorder._stream_lifecycle_lock:
            DisconnectHandler(recorder).restart_stream(_captured_generation=0)

        assert len(attempts) == 1, "exactly one restart InputStream attempt expected"
        kwargs = attempts[0]
        assert kwargs["samplerate"] == 48000
        assert kwargs["blocksize"] == scaled_audio_blocksize(48000)
        assert kwargs["blocksize"] == 1536, (
            f"restart_stream must scale the blocksize to the candidate's "
            f"native rate (1536 @ 48 kHz), got {kwargs['blocksize']}. A fixed "
            f"512 here restores ~94 callbacks/s after every hot-swap recovery."
        )

    def test_restart_stream_keeps_512_at_16khz_candidate(self, monkeypatch):
        """At a 16 kHz reconnect candidate the scaled blocksize is
        exactly 512 (the Silero window) — the floor, not a special case."""
        from voice_typer.server.recording import disconnect_handler as dh_module
        from voice_typer.server.recording.disconnect_handler import DisconnectHandler
        from voice_typer.server.recording.recorder import Recorder

        from tests.test_recording_lifecycle_fixes import _install_fake_input_stream

        config = MagicMock(sample_rate=16000, microphone=None, recording_channels=1)
        recorder = Recorder(config)
        recorder._current_callback = MagicMock(name="_current_callback")
        monkeypatch.setattr(recorder._devices, "_resolve_effective_sample_rate", lambda _d: (16000, None))
        attempts = _install_fake_input_stream(dh_module, monkeypatch)

        with recorder._stream_lifecycle_lock:
            DisconnectHandler(recorder).restart_stream(_captured_generation=0)

        assert len(attempts) == 1
        kwargs = attempts[0]
        assert kwargs["samplerate"] == 16000
        assert kwargs["blocksize"] == scaled_audio_blocksize(16000)
        assert kwargs["blocksize"] == 512
