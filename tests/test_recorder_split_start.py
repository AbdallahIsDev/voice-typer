"""Tests for ``_recorder_split.start_recording``.

Phase 4.5 — pin the extraction contract for the body of
``Recorder.start`` that was moved (verbatim, with ``self.X`` rewritten
to ``recorder.X``) into a free function in
``voice_typer/server/recording/_recorder_split.py``. The
``with self._start_lock:`` permission-gate block stays on
``Recorder.start`` so the source-inspection test
(``tests/test_recording.py::TestRec5StartLock``) keeps pinning the lock
contract; this function is NOT subject to source-inspection.

These tests use a MagicMock recorder with explicit stubs for the
device-enumeration tuple-returning helpers, so they never touch
PortAudio, the OS permissions module, real worker threads, or the
real ``AudioProcessor`` chain. This keeps each test deterministic and
sub-second.

The tests pin four contracts:

  1. **Happy-path ordering**: every per-session step (cache-clear →
     state reset → config cache → device resolve → callback build →
     device enumerate → buffer resize → event set → preroll prepend
     → VAD cache refresh → audio worker → event worker → device
     health checker) is invoked in source order.

  2. **Device-enumeration at function scope** (the ``CRITICAL — DO
     NOT RESTRUCTURE`` warning): the ``last_error`` /
     ``selected_device`` / ``effective_sr`` / ``used_fallback`` locals
     are local variables of ``start_recording`` (in ``co_varnames``),
     NOT captured by the ``callback`` closure (in ``co_cellvars``).
     A previous merge nested these inside the closure and crashed
     every recording start with ``UnboundLocalError``.

  3. **Fallback path**: when the same-name candidates fail to open a
     stream, ``_open_stream_fallback`` is invoked; when both paths
     fail, the captured ``last_error`` is re-raised (or a generic
     ``RuntimeError`` if no error was recorded).

  4. **Self → recorder rewriting**: the function body must not
     reference ``self.X`` (only ``recorder.X``). A future merge that
     re-introduces ``self.`` in the body would raise ``NameError: self``
     at call time.
"""

from __future__ import annotations

import inspect
import re
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.recording._recorder_split import start_recording


def _build_mock_recorder(
    *,
    sample_rate: int = 16000,
    device: int = 5,
    effective_sr: int | None = None,
    audio_processor: object | None = None,
    open_success: bool = True,
) -> MagicMock:
    """Build a MagicMock recorder with the minimum stubs
    ``start_recording`` needs to run without touching PortAudio, the
    OS permissions module, or real worker threads."""
    recorder = MagicMock(name="recorder")
    recorder.config = MagicMock(name="config")
    recorder.config.sample_rate = sample_rate
    recorder.config.microphone = None
    recorder.config.save.return_value = True

    # `_cache_session_config` returns the per-session max_rec seconds.
    recorder._cache_session_config.return_value = 30

    recorder._resolve_device.return_value = device
    recorder._same_physical_microphone_candidates.return_value = [device]

    callback_sentinel = object()
    recorder._build_audio_callback.return_value = callback_sentinel

    if effective_sr is None:
        effective_sr = sample_rate

    if open_success:
        # `_open_stream_for_candidates` returns
        # ``(selected_device, effective_sr, last_error)``.
        recorder._open_stream_for_candidates.return_value = (
            device,
            effective_sr,
            None,
        )
        # `_stream` is non-None so the fallback path is skipped and
        # the ``if recorder._stream is None:`` early-return doesn't fire.
        recorder._stream = MagicMock(name="opened-stream")
    else:
        recorder._open_stream_for_candidates.return_value = (
            None,
            sample_rate,
            RuntimeError("no mic"),
        )
        recorder._stream = None

    # `_recording_event` must be a real threading.Event so the
    # `event.set()` ordering check can inspect its state.
    recorder._recording_event = threading.Event()
    recorder._audio_processor = audio_processor

    return recorder


# ── Happy-path ordering ────────────────────────────────────────────


class TestStartRecordingHappyPath:
    """Verify the body of ``Recorder.start`` runs end-to-end when the
    stream opens successfully on the first candidate."""

    def test_runs_all_steps_in_order(self):
        """When the stream opens on the first candidate,
        ``start_recording`` must invoke every per-session step. The
        step ordering is asserted by the ``in_order`` helper below.
        """
        recorder = _build_mock_recorder()
        start_recording(recorder)

        recorder._secure_clear_session_caches.assert_called_once()
        recorder._reset_session_state.assert_called_once()
        recorder._cache_session_config.assert_called_once()
        recorder._resolve_device.assert_called_once()
        recorder._same_physical_microphone_candidates.assert_called_once_with(recorder._resolve_device.return_value)
        recorder._build_audio_callback.assert_called_once()
        recorder._open_stream_for_candidates.assert_called_once()
        # No fallback because the stream opened on the first candidate.
        recorder._open_stream_fallback.assert_not_called()
        recorder._resize_buffers_for_sample_rate.assert_called_once()
        assert recorder._recording_event.is_set()
        recorder._prepend_preroll_to_buffer.assert_called_once()
        recorder._refresh_vad_caches.assert_called_once()
        recorder._start_audio_worker.assert_called_once()
        recorder._start_event_worker.assert_called_once()
        recorder._start_device_health_checker.assert_called_once()

    def test_step_order_matches_contract(self):
        """Pin the source-order contract: cache-clear → state reset →
        config cache → resolve device → candidates → build callback →
        open stream → resize buffers → event set → preroll prepend →
        VAD cache refresh → audio worker → event worker → device
        health checker.

        A future refactor that swaps the order of these calls (e.g.
        starts the audio worker before ``_recording_event.set()``)
        would silently re-introduce the race the comment
        block above the audio worker spawn guards against.
        """
        recorder = _build_mock_recorder()
        call_log: list[str] = []

        def log_call(name, ret=None):
            # ``side_effect`` overrides ``return_value`` when set, so
            # the hook's return value is the mock's return value. We
            # pass the right tuple/string for tuple-returning helpers
            # (``_open_stream_for_candidates`` etc.); for the no-return
            # helpers, ``ret`` stays ``None`` and the function ignores
            # the return.
            def _hook(*a, **k):
                call_log.append(name)
                return ret

            return _hook

        # Stub every method that the function calls in source order.
        recorder._secure_clear_session_caches.side_effect = log_call("secure_clear")
        recorder._reset_session_state.side_effect = log_call("reset_session")
        recorder._cache_session_config.side_effect = log_call("cache_config", ret=30)
        recorder._resolve_device.side_effect = log_call("resolve_device", ret=5)
        recorder._same_physical_microphone_candidates.side_effect = log_call("candidates", ret=[5])
        recorder._build_audio_callback.side_effect = log_call("build_callback", ret=object())
        # ``_open_stream_for_candidates`` returns a 3-tuple — the
        # function unpacks it, so the side_effect must return one.
        recorder._open_stream_for_candidates.side_effect = log_call("open_stream", ret=(5, 16000, None))
        recorder._resize_buffers_for_sample_rate.side_effect = log_call("resize_buffers")
        recorder._recording_event = MagicMock(wraps=threading.Event())
        recorder._recording_event.set.side_effect = log_call("event.set")
        recorder._prepend_preroll_to_buffer.side_effect = log_call("prepend_preroll")
        recorder._refresh_vad_caches.side_effect = log_call("refresh_vad")
        recorder._start_audio_worker.side_effect = log_call("start_audio_worker")
        recorder._start_event_worker.side_effect = log_call("start_event_worker")
        recorder._start_device_health_checker.side_effect = log_call("start_device_health_checker")

        start_recording(recorder)

        expected_order = [
            "secure_clear",
            "reset_session",
            "cache_config",
            "resolve_device",
            "candidates",
            "build_callback",
            "open_stream",
            "resize_buffers",
            "event.set",
            "prepend_preroll",
            "refresh_vad",
            "start_audio_worker",
            "start_event_worker",
            "start_device_health_checker",
        ]
        assert call_log == expected_order, (
            f"start_recording step order regressed. Expected {expected_order}, got {call_log}."
        )

    def test_open_stream_for_candidates_receives_callback_and_effective_sr(self):
        """Pin the call-arg contract for ``_open_stream_for_candidates``:
        ``(candidates, callback, effective_sr, last_error)`` where
        ``callback`` is the closure returned by
        ``_build_audio_callback`` (not the recorder itself). A future
        refactor that swaps arg order would silently break the
        closure-dispatch contract.
        """
        recorder = _build_mock_recorder()
        start_recording(recorder)

        args, _ = recorder._open_stream_for_candidates.call_args
        candidates, callback, eff_sr, last_err = args
        assert candidates == recorder._same_physical_microphone_candidates.return_value
        assert callback is recorder._build_audio_callback.return_value
        assert eff_sr == recorder.config.sample_rate
        assert last_err is None


# ── Device-enumeration at function scope (CRITICAL contract) ──────


class TestDeviceEnumerationAtFunctionScope:
    """Pin the ``CRITICAL — DO NOT RESTRUCTURE`` contract: the
    device-enumeration block (``last_error``, ``selected_device``,
    ``effective_sr``, ``_open_stream_for_candidates``,
    ``_open_stream_fallback``, and the ``if recorder._stream is
    None:`` check) MUST stay at ``start_recording`` function-body
    scope, NOT inside the ``callback`` closure built by
    ``_build_audio_callback``.

    A previous merge nested these inside the closure, which made
    ``last_error`` a local of ``callback`` and crashed every recording
    start with ``UnboundLocalError``. These tests pin the
    locals-as-function-locals contract so a future refactor can't
    regress it."""

    def test_locals_are_at_function_scope(self):
        """``last_error``, ``selected_device``, ``effective_sr``,
        ``used_fallback`` are local variables of ``start_recording``.
        They appear in the function's ``co_varnames`` (NOT in any
        nested closure's ``co_freevars``).
        """
        code = start_recording.__code__
        # ``co_varnames`` is the tuple of local variable names (in
        # source order, plus temporaries) for the function body.
        for name in ("last_error", "selected_device", "effective_sr", "used_fallback"):
            assert name in code.co_varnames, (
                f"CRITICAL contract: `{name}` must be a local of "
                f"start_recording (in co_varnames), not of the "
                f"callback closure. A previous merge regressed this and "
                f"crashed every recording start with UnboundLocalError."
            )

    def test_locals_not_in_co_cellvars(self):
        """The device-enumeration locals are NOT captured by any
        nested closure. They appear in ``co_cellvars`` only if some
        nested ``def`` references them — they shouldn't.
        """
        code = start_recording.__code__
        for name in ("last_error", "selected_device", "effective_sr"):
            assert name not in code.co_cellvars, (
                f"CRITICAL contract: `{name}` must NOT be captured by a "
                f"nested closure. If it were, it would be a cellvar, not "
                f"a local of start_recording, and the structural "
                f"device-enumeration contract would be violated."
            )

    def test_persist_mic_closure_captures_recorder_not_self(self):
        """The ``_persist_mic`` closure nested inside the
        microphone-fallback persistence block captures ``recorder``
        (and ``log``), NOT ``self``. The captured name must be
        ``recorder`` — that's what makes the extracted function's
        reference to the recorder work after the closure defers.
        """
        code = start_recording.__code__
        # ``recorder`` is captured by the nested ``_persist_mic``
        # closure so it appears in ``co_cellvars``.
        assert "recorder" in code.co_cellvars, (
            "CRITICAL contract: _persist_mic must capture "
            "`recorder` via closure (so the fallback persistence runs "
            "against the right object)."
        )
        # Ensure no stale ``self`` reference snuck in (would mean the
        # body wasn't fully self-rewritten).
        assert "self" not in code.co_varnames
        assert "self" not in code.co_cellvars
        assert "self" not in code.co_freevars


# ── Fallback path ────────────────────────────────────────────────


class TestStartRecordingFallbackPath:
    """When the same-name candidates all fail to open a stream,
    ``start_recording`` must invoke ``_open_stream_fallback`` to
    try every available input device. This is the 'hot-plug'
    fallback that lets recording start even when the user's
    configured mic has been unplugged."""

    def test_fallback_called_when_first_candidate_returns_none_stream(self):
        """When ``_open_stream_for_candidates`` returns
        ``selected_device=None`` AND ``recorder._stream`` is None,
        the fallback path must run.
        """
        recorder = _build_mock_recorder(open_success=False)
        # First attempt fails (``_stream`` is None);
        # ``_open_stream_fallback`` is the recorder method that opens
        # the stream — it sets ``self._stream`` itself on success. We
        # simulate that by using a ``side_effect`` that assigns
        # ``recorder._stream`` to a non-None MagicMock before returning
        # the 4-tuple.
        assert recorder._stream is None
        original_err = recorder._open_stream_for_candidates.return_value[2]

        def _fallback_side_effect(candidates, callback, eff_sr, last_err):
            recorder._stream = MagicMock(name="fallback-stream")
            return (7, 16000, True, None)

        recorder._open_stream_fallback.side_effect = _fallback_side_effect

        start_recording(recorder)

        recorder._open_stream_fallback.assert_called_once()
        args, _ = recorder._open_stream_fallback.call_args
        candidates, callback, eff_sr, last_err = args
        assert candidates == recorder._same_physical_microphone_candidates.return_value
        assert callback is recorder._build_audio_callback.return_value
        assert last_err is original_err, (
            "The ``last_error`` captured from the failed candidate "
            "attempt must be propagated to ``_open_stream_fallback`` "
            "as the 4th positional arg."
        )

    def test_raises_last_error_when_all_paths_fail(self):
        """When both the candidate path AND the fallback path fail
        to open a stream, ``start_recording`` must re-raise the
        last error captured during enumeration (NOT a generic
        ``RuntimeError``) — so callers see the underlying
        OS/PortAudio failure (e.g. ``OSError: [Errno -9998]
        Invalid number of channels``).
        """
        recorder = _build_mock_recorder(open_success=False)
        original_err = OSError("[Errno -9998] PortAudio invalid channel count")
        recorder._open_stream_for_candidates.return_value = (
            None,
            16000,
            original_err,
        )
        recorder._open_stream_fallback.return_value = (
            None,
            16000,
            True,
            original_err,  # same error propagated from the candidate path
        )
        recorder._stream = None

        with pytest.raises(OSError) as exc_info:
            start_recording(recorder)
        assert exc_info.value is original_err, (
            "start_recording must re-raise the captured last_error "
            "verbatim (not wrap it), so the caller sees the underlying "
            "PortAudio/OSError failure mode."
        )

    def test_raises_runtime_error_when_no_error_recorded(self):
        """Defensive contract: if the device enumeration loop
        somehow returned None stream AND no last_error (e.g. an
        empty device list with no PortAudio exceptions), raise a
        generic ``RuntimeError`` rather than silently returning
        with no audio.
        """
        recorder = _build_mock_recorder(open_success=False)
        recorder._open_stream_for_candidates.return_value = (None, 16000, None)
        recorder._open_stream_fallback.return_value = (None, 16000, True, None)
        recorder._stream = None

        with pytest.raises(RuntimeError, match="No input device could be opened"):
            start_recording(recorder)


# ── Microphone fallback persistence ──────────────────────────────


class TestMicrophoneFallbackPersistence:
    """When the opened device differs from the configured ``device``
    returned by ``_resolve_device``, the fallback must be persisted
    on a background daemon thread (``_spawn_device_thread``) so the
    recording-start critical path isn't blocked by the 50-500 ms
    blocking config-write (the fix)."""

    def test_persist_thread_spawned_when_selected_device_differs(self):
        """When ``selected_device != device`` (and is an int), the
        fallback persistence thread is spawned and
        ``config.microphone`` is updated to the new device.
        """
        recorder = _build_mock_recorder(device=5, open_success=True)
        # Override: configured device=5 but the stream opened on device=7.
        recorder._open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._resolve_device.return_value = 5

        start_recording(recorder)

        recorder._spawn_device_thread.assert_called_once()
        kwargs = recorder._spawn_device_thread.call_args.kwargs
        assert kwargs["name"] == "mic-fallback-save"
        assert callable(kwargs["target"])

        # The config.microphone must be updated to the new device.
        assert recorder.config.microphone == "7"

    def test_persist_thread_not_spawned_when_selected_device_matches(self):
        """No persistence thread when the opened device is the
        configured one.
        """
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._resolve_device.return_value = 5
        recorder._open_stream_for_candidates.return_value = (5, 16000, None)

        start_recording(recorder)

        recorder._spawn_device_thread.assert_not_called()

    def test_persist_thread_runs_persist_mic_closure(self):
        """The ``target`` callable passed to ``_spawn_device_thread``
        is the ``_persist_mic`` closure. When called, it must invoke
        ``recorder.config.save()``.
        """
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._resolve_device.return_value = 5
        recorder._open_stream_for_candidates.return_value = (7, 16000, None)
        recorder.config.save.return_value = True

        start_recording(recorder)

        target = recorder._spawn_device_thread.call_args.kwargs["target"]
        target()  # invoke the closure
        recorder.config.save.assert_called_once()

    def test_persist_mic_logs_when_save_fails(self, caplog):
        """When ``config.save()`` returns False, the closure logs a
        debug message — best-effort persistence.
        """
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._resolve_device.return_value = 5
        recorder._open_stream_for_candidates.return_value = (7, 16000, None)
        recorder.config.save.return_value = False

        start_recording(recorder)
        target = recorder._spawn_device_thread.call_args.kwargs["target"]

        with caplog.at_level("DEBUG", logger="voice_typer.server.recording"):
            target()
        assert any("Could not persist microphone fallback" in rec.message for rec in caplog.records), (
            "when config.save() returns False, the "
            "persistence closure must log a debug message so the "
            "best-effort failure is observable."
        )

    def test_persist_thread_not_spawned_when_selected_device_is_not_int(self):
        """The persistence block guards with
        ``isinstance(selected_device, int)`` — a non-int device
        (e.g. None or a string) skips the persistence path.
        """
        recorder = _build_mock_recorder(device=5, open_success=True)
        # Return a string ``selected_device`` — not int, so the
        # persistence block must be skipped.
        recorder._open_stream_for_candidates.return_value = (
            "not-an-int",
            16000,
            None,
        )
        recorder._resolve_device.return_value = 5

        start_recording(recorder)
        recorder._spawn_device_thread.assert_not_called()


# ── Resampler warm-up ────────────────────────────────────────────


class TestResamplerWarmUp:
    """When the effective sample rate differs from the configured
    target rate AND scipy's ``resample_poly`` is not yet loaded
    (and not previously failed), ``start_recording`` must call
    ``recorder.warm_up_resampler()`` synchronously so the first
    chunk doesn't race with the async scipy preloader.
    """

    def test_warm_up_called_when_sr_differs_and_poly_not_loaded(self, monkeypatch):
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,  # differs from target_sr
            open_success=True,
        )
        # Patch the package-level mutable globals so the warm-up
        # branch fires. The lazy import inside ``start_recording``
        # reads these via ``_recording_pkg._resample_poly`` — the
        # custom module class routes through to ``resampling._*``,
        # so monkeypatching the package name propagates to the
        # function call site.
        import voice_typer.server.recording as rec_pkg

        monkeypatch.setattr(rec_pkg, "_resample_poly", None, raising=False)
        monkeypatch.setattr(rec_pkg, "_resample_poly_error", None, raising=False)

        start_recording(recorder)

        recorder.warm_up_resampler.assert_called_once()

    def test_warm_up_skipped_when_sr_matches(self, monkeypatch):
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=16000,  # matches target_sr → skip warm-up
            open_success=True,
        )

        start_recording(recorder)

        recorder.warm_up_resampler.assert_not_called()

    def test_warm_up_skipped_when_poly_already_loaded(self, monkeypatch):
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            open_success=True,
        )
        import voice_typer.server.recording as rec_pkg

        # Pretend scipy is already loaded — skip the synchronous warm-up.
        monkeypatch.setattr(rec_pkg, "_resample_poly", object(), raising=False)
        monkeypatch.setattr(rec_pkg, "_resample_poly_error", None, raising=False)

        start_recording(recorder)

        recorder.warm_up_resampler.assert_not_called()

    def test_warm_up_skipped_when_poly_failed_before(self, monkeypatch):
        """If a previous warm-up attempt failed
        (``_resample_poly_error`` is set), don't retry — the
        per-chunk resample fallback will run on the RT thread.
        """
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            open_success=True,
        )
        import voice_typer.server.recording as rec_pkg

        monkeypatch.setattr(rec_pkg, "_resample_poly", None, raising=False)
        monkeypatch.setattr(rec_pkg, "_resample_poly_error", RuntimeError("scipy missing"), raising=False)

        start_recording(recorder)

        recorder.warm_up_resampler.assert_not_called()


# ── AudioProcessor retune (call removed) ────────────────────


class TestAudioProcessorRetune:
    """(CALL REMOVAL): ``start_recording`` no longer calls
    ``retune_audio_processor``. The AudioProcessor chain stays at its
    construction rate (typically WHISPER_SAMPLE_RATE = 16 kHz) and the
    per-chunk resample inside ``AudioProcessor.process_chunk`` (invoked
    from ``audio_pipeline.process_audio_chunk`` with
    ``input_sample_rate=recorder._effective_sr``) handles the 48 kHz →
    16 kHz downsample on the worker thread.

    These tests pin the after the refactor contract: ``set_sample_rate`` and
    ``rebuild_from_config`` MUST NOT be called from ``start_recording``,
    regardless of the device's effective sample rate. Filter-chain
    correctness at 16 kHz is preserved by ``process_chunk``'s internal
    resample (verified via the audio-pipeline regression suite), not by
    an up-front retune.
    """

    def test_set_sample_rate_not_called_when_available(self, caplog):
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000  # chain rate
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,  # device rate differs from chain rate
            audio_processor=audio_processor,
            open_success=True,
        )
        audio_processor.set_sample_rate = MagicMock()

        start_recording(recorder)

        # retune was removed — the chain stays at 16 kHz and
        # process_chunk resamples 48 kHz → 16 kHz on the worker thread.
        audio_processor.set_sample_rate.assert_not_called()
        audio_processor.rebuild_from_config.assert_not_called()

    def test_rebuild_from_config_not_called_when_set_sample_rate_unavailable(self, caplog):
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )
        # No ``set_sample_rate`` attribute on the mock — previously the
        # retune helper would have fallen through to
        # ``rebuild_from_config``. After the refactor neither path runs.
        del audio_processor.set_sample_rate

        start_recording(recorder)

        audio_processor.rebuild_from_config.assert_not_called()

    def test_no_retune_when_processor_sr_matches(self, caplog):
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=16000,  # matches → skip retune
            audio_processor=audio_processor,
            open_success=True,
        )

        start_recording(recorder)

        audio_processor.set_sample_rate.assert_not_called()
        audio_processor.rebuild_from_config.assert_not_called()

    def test_no_retune_when_no_audio_processor(self, caplog):
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=None,
            open_success=True,
        )

        # Must not raise — with no audio processor, no retune is needed
        # (no filter chain to tune). The 48 kHz audio is stored raw and
        # resampled by stop()/snapshot() on the way out.
        start_recording(recorder)

    def test_set_sample_rate_failure_does_not_break_start(self, caplog):
        """with the retune call removed, a buggy
        ``AudioProcessor.set_sample_rate`` is never invoked from
        ``start_recording``, so it can't break the start critical path.
        The per-chunk resample in ``process_chunk`` will run instead
        (and any failure there is caught by the audio worker's
        per-chunk try/except).
        """
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000
        audio_processor.set_sample_rate.side_effect = RuntimeError("simulated bug")
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )

        # Must not raise and must NOT log a set_sample_rate failure
        # (the buggy method is never called).
        with caplog.at_level("WARNING", logger="voice_typer.server.recording"):
            start_recording(recorder)
        audio_processor.set_sample_rate.assert_not_called()
        assert not any(
            "set_sample_rate" in rec.message and "failed" in rec.message
            for rec in caplog.records
        ), "set_sample_rate must not be invoked (and thus not fail) after the refactor."

    def test_rebuild_from_config_failure_does_not_break_start(self, caplog):
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000
        del audio_processor.set_sample_rate
        audio_processor.rebuild_from_config.side_effect = RuntimeError("simulated bug")
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )

        # rebuild_from_config is never called from start_recording.
        with caplog.at_level("WARNING", logger="voice_typer.server.recording"):
            start_recording(recorder)
        audio_processor.rebuild_from_config.assert_not_called()
        assert not any(
            "rebuild_from_config" in rec.message and "failed" in rec.message
            for rec in caplog.records
        ), "rebuild_from_config must not be invoked (and thus not fail) after the refactor."

    def test_no_retune_when_processor_sr_is_none(self, caplog):
        """Defensive: when ``_audio_processor._sample_rate`` is None
        (e.g. an AudioProcessor test double that didn't set the
        attribute), ``start_recording`` skips the retune block rather
        than crashing on ``int(None)``. After the refactor the entire retune
        block is gone, so this is trivially satisfied — but the test
        pins the contract so a future regression (re-adding the call)
        doesn't reintroduce the ``int(None)`` crash.
        """
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = None
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )

        # Must not raise — the retune call is gone entirely (the fix).
        start_recording(recorder)

        audio_processor.set_sample_rate.assert_not_called()
        audio_processor.rebuild_from_config.assert_not_called()


# ── Recording event contract ─────────────────────────────────────


class TestRecordingEventContract:
    """``_recording_event.set()`` must be called BEFORE the audio
    worker / event worker / device health checker threads are
    spawned — so the callback will actually push to the ring buffer
    once the workers start draining it (the fix).
    """

    def test_recording_event_set_before_workers_started(self):
        recorder = _build_mock_recorder()
        call_log: list[str] = []

        def log_call(name):
            def _hook(*a, **k):
                call_log.append(name)

            return _hook

        recorder._recording_event = MagicMock(wraps=threading.Event())
        recorder._recording_event.set.side_effect = log_call("event.set")
        recorder._start_audio_worker.side_effect = log_call("start_audio_worker")
        recorder._start_event_worker.side_effect = log_call("start_event_worker")
        recorder._start_device_health_checker.side_effect = log_call("start_device_health_checker")

        start_recording(recorder)

        assert "event.set" in call_log
        event_idx = call_log.index("event.set")
        assert "start_audio_worker" in call_log
        assert "start_event_worker" in call_log
        assert "start_device_health_checker" in call_log
        assert event_idx < call_log.index("start_audio_worker")
        assert event_idx < call_log.index("start_event_worker")
        assert event_idx < call_log.index("start_device_health_checker")


# ── No real audio / permissions / subprocess ──────────────────────


class TestNoRealExternalDeps:
    """Sanity-check that ``start_recording`` does NOT touch:
    - the OS permissions module (only ``Recorder.start``'s lock
      block does that),
    - PortAudio / sounddevice (mocked via the recorder),
    - real worker threads (the recorder methods are MagicMock
      stubs).
    """

    def test_permissions_module_not_imported_in_function_body(self):
        """``start_recording``'s source must not import the
        permissions module — that's the responsibility of
        ``Recorder.start``'s lock block (which stays on
        ``Recorder.start``).
        """
        src = inspect.getsource(start_recording)
        body = src.replace(start_recording.__doc__ or "", "")
        assert "verify_microphone_accessible" not in body, (
            "start_recording must NOT call the permissions module — that's "
            "the responsibility of Recorder.start's _start_lock block."
        )
        assert "import permissions" not in body, (
            "start_recording must not import the permissions module — the "
            "lock-gate permission check stays on Recorder.start."
        )

    def test_no_sd_or_sounddevice_references_in_function_body(self):
        """The function body must not reference ``sd`` or
        ``sounddevice`` directly — all PortAudio interaction happens
        inside the recorder's delegate methods (which tests stub via
        MagicMock).
        """
        src = inspect.getsource(start_recording)
        body = src.replace(start_recording.__doc__ or "", "")
        sd_pattern = re.compile(r"\bsd\b")
        sounddevice_pattern = re.compile(r"sounddevice")
        assert not sd_pattern.search(body), "start_recording must not reference the `sd` proxy directly."
        assert not sounddevice_pattern.search(body), "start_recording must not reference `sounddevice` directly."

    def test_no_direct_subprocess_or_os_calls(self):
        """No ``os.system`` / ``subprocess.*`` calls — those would be
        a major layering violation.
        """
        src = inspect.getsource(start_recording)
        body = src.replace(start_recording.__doc__ or "", "")
        assert "subprocess" not in body
        assert "os.system" not in body


# ── Self → recorder rewriting contract ────────────────────────────


class TestSourceRewritingContract:
    """Pin the ``self.X`` → ``recorder.X`` rewriting contract. A
    future merge that re-introduces ``self.`` in the body would
    silently break the extracted function (it would raise
    ``NameError: self`` at call time).
    """

    def test_no_self_references_in_body(self):
        """No ``self.`` references in the function body. All instance
        access must go through ``recorder.``.
        """
        src = inspect.getsource(start_recording)
        # Strip the docstring (which legitimately mentions
        # ``self._start_lock`` when describing what ``Recorder.start``
        # does — that's a docstring fragment, not a code reference).
        body = src.replace(start_recording.__doc__ or "", "")
        # ``self.X`` followed by an identifier is a real reference.
        # Avoid false-positives from comment strings by matching the
        # Python token shape ``self.\w``.
        self_pattern = re.compile(r"\bself\.\w")
        matches = self_pattern.findall(body)
        assert not matches, f"start_recording body must not reference `self.X` (only `recorder.X`). Found: {matches}"

    def test_recording_event_access_via_recorder(self):
        """``_recording_event`` access must be via
        ``recorder._recording_event``, not ``self._recording_event``
        or a bare-name lookup.
        """
        src = inspect.getsource(start_recording)
        body = src.replace(start_recording.__doc__ or "", "")
        assert "recorder._recording_event.set()" in body
        assert "self._recording_event" not in body

    def test_audio_processor_access_via_recorder(self):
        """(CALL REMOVAL): ``_audio_processor`` is no longer
        accessed from ``start_recording`` at all — the retune call that
        used to read it has been removed (the per-chunk resample in
        ``AudioProcessor.process_chunk`` handles the native-rate →
        16 kHz downsample on the worker thread instead).

        Previously this test asserted ``recorder._audio_processor``
        appeared in the source (the retune call). After the refactor the
        access is gone, so the contract is now: ``_audio_processor``
        is NOT accessed from ``start_recording`` at all (neither via
        ``recorder.`` nor ``self.``). If a future change re-adds an
        ``_audio_processor`` access, it MUST use ``recorder.`` (not
        ``self.``) — the second assertion pins that.
        """
        src = inspect.getsource(start_recording)
        body = src.replace(start_recording.__doc__ or "", "")
        # After the refactor: no _audio_processor access at all from start_recording.
        assert "recorder._audio_processor" not in body, (
            "start_recording should NOT access recorder._audio_processor "
            "— the retune call was removed (per-chunk resample handles it)."
        )
        assert "self._audio_processor" not in body

    def test_resolver_methods_called_via_recorder(self):
        """The device-enumeration helpers (``_resolve_device``,
        ``_same_physical_microphone_candidates``,
        ``_open_stream_for_candidates``, ``_open_stream_fallback``)
        must be called via ``recorder.X``.
        """
        src = inspect.getsource(start_recording)
        body = src.replace(start_recording.__doc__ or "", "")
        for method in (
            "_resolve_device",
            "_same_physical_microphone_candidates",
            "_open_stream_for_candidates",
            "_open_stream_fallback",
        ):
            assert f"recorder.{method}" in body, (
                f"start_recording must call `{method}` via recorder.{method}, not via self.{method}."
            )
            assert f"self.{method}" not in body, (
                f"start_recording must NOT call `self.{method}` — the body was rewritten to use `recorder.{method}`."
            )


# ── Lazy import of the package namespace ─────────────────────────


class TestLazyPackageImport:
    """The function does ``from voice_typer.server import recording as
    _recording_pkg`` lazily (mirroring ``discard_recording``) to avoid
    a circular import. The ``_recording_pkg._resample_poly`` /
    ``_resample_poly_error`` lookups go through the package's custom
    module class, so monkeypatching the package names propagates to
    the function call site.
    """

    def test_lazy_import_in_function_body(self):
        """The function body contains a lazy import of the package
        namespace — pin this so a future refactor doesn't move it
        to module top (which would create a circular import).
        """
        src = inspect.getsource(start_recording)
        body = src.replace(start_recording.__doc__ or "", "")
        assert "from voice_typer.server import recording as _recording_pkg" in body, (
            "start_recording must do the lazy package import inside its "
            "body — moving it to module top would re-introduce the "
            "circular import that recorder.py's top-level import of this "
            "module creates."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
