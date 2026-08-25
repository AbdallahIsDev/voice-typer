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

import collections
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

    # ``start_recording`` sizes the roll deque using a
    # generous sample rate (max of config.sample_rate and 48 kHz)
    # BEFORE the stream opens. The check reads
    # ``recorder._preroll_active`` (bool) and
    # ``recorder._preroll_seconds`` (float) — both must be real Python
    # scalars (MagicMock would raise ``TypeError`` on the
    # ``> 0`` / ``int(...)`` ops). Default to "preroll inactive" so
    # the size block is a no-op (matches the behavior
    # for these tests that don't exercise the preroll path).
    recorder._preroll_active = False
    recorder._preroll_seconds = 0.0
    recorder._preroll_buffer = collections.deque(maxlen=0)

    return recorder


def _source_without_docstring(func) -> str:
    """Return ``func``'s source with the leading docstring statement removed.

    Python 3.13 dedents function docstrings at compile time (gh-103180), so
    ``src.replace(func.__doc__ or "", "")`` only strips the docstring on
    <=3.12 — on 3.13 the dedented ``__doc__`` is no longer a substring of the
    raw source and the replace silently no-ops, leaking the docstring (which
    legitimately mentions ``self._start_lock``) into the inspected body.
    Excising the docstring statement via the AST is robust on every version.
    """
    import ast
    import textwrap

    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    fn = tree.body[0]
    # Narrow to node types that actually carry a ``body`` (pyrefly types
    # ``tree.body[0]`` as the abstract ``ast.stmt``, which has none).
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return src
    first = fn.body[0] if fn.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        lines = src.splitlines(keepends=True)
        del lines[first.lineno - 1 : first.end_lineno]
        src = "".join(lines)
    return src


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
        # preroll prepend was MOVED off the start thread — it
        # now runs as a "phase 0" inside the audio worker thread's
        # ``audio_worker_loop`` (capture.py). The start() path no
        # longer calls ``_prepend_preroll_to_buffer`` synchronously.
        recorder._prepend_preroll_to_buffer.assert_not_called()
        recorder._refresh_vad_caches.assert_called_once()
        recorder._start_audio_worker.assert_called_once()
        recorder._start_event_worker.assert_called_once()
        recorder._start_device_health_checker.assert_called_once()

    def test_step_order_matches_contract(self):
        """Pin the source-order contract: cache-clear → state reset →
               config cache → resolve device → candidates → build callback →
               open stream → resize buffers → event set → VAD cache refresh →
               audio worker → event worker → device health checker.

        : the preroll prepend step was REMOVED from this ordering
               — it now runs as a "phase 0" inside the audio worker thread's
               ``audio_worker_loop`` (capture.py), so start() no longer
               synchronously invokes ``_prepend_preroll_to_buffer`` between
               ``event.set`` and ``refresh_vad``.

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
        # prepend_preroll is no longer called from start_recording
        # leaving the side_effect unset so a regression call would NOT
        # appear in call_log (the assertion below would catch the
        # unexpected call via the ordering mismatch).
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

    def test_no_persistence_closure_in_body(self):
        """The microphone-fallback persistence block is GONE: the
        fallback device is session-local only, so ``start_recording``
        must not define a nested persistence closure nor reference the
        ``"mic-fallback-save"`` spawn name. Auto-persisting the
        fallback silently overwrote the user's saved selection.
        """
        code = start_recording.__code__
        for name in ("_persist_mic", "mic-fallback-save"):
            assert name not in code.co_varnames
            assert name not in code.co_cellvars
            assert name not in code.co_freevars
            assert all(name not in str(c) for c in code.co_consts), (
                f"CRITICAL contract: `{name}` must not appear anywhere in "
                "start_recording — the microphone-fallback persistence "
                "block was removed because auto-writing config.microphone "
                "silently replaced the user's saved selection."
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


# ── Microphone fallback is session-local ─────────────────────────


class TestMicrophoneFallbackSessionLocal:
    """When the opened device differs from the configured ``device``
    returned by ``_resolve_device``, the fallback applies to THIS
    session only: ``config.microphone`` must NOT be rewritten and no
    persistence thread may be spawned. Auto-writing the fallback
    silently replaced the user's saved selection (or a ``None``
    System Default) with an arbitrary concrete device id."""

    def test_config_microphone_not_overwritten_on_fallback(self):
        """``selected_device != device`` → the stream runs on the
        fallback, but the persisted selection stays untouched."""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._resolve_device.return_value = 5
        recorder.config.microphone = "Windows WASAPI|USB Mic"

        start_recording(recorder)

        assert recorder.config.microphone == "Windows WASAPI|USB Mic", (
            "config.microphone must NOT be auto-rewritten when a "
            "fallback device is used — the saved selection belongs to "
            "the user."
        )
        recorder.config.save.assert_not_called()

    def test_no_persistence_thread_spawned_when_selected_device_differs(self):
        """No persistence thread (``mic-fallback-save``) may be spawned
        for a session-local fallback."""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._resolve_device.return_value = 5

        start_recording(recorder)

        for call in recorder._spawn_device_thread.call_args_list:
            assert call.kwargs.get("name") != "mic-fallback-save", (
                "the mic-fallback persistence thread was removed; spawning "
                "it again would silently overwrite the user's selection"
            )

    def test_fallback_logs_session_local_notice(self, caplog):
        """The fallback path logs that the saved selection is unchanged
        so the session-local behavior is observable in the log file."""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._resolve_device.return_value = 5

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            start_recording(recorder)

        assert any(
            "saved selection unchanged" in rec.message and "[RECORDING]" in rec.message for rec in caplog.records
        ), "fallback usage must log an INFO line noting the saved selection is unchanged."

    def test_nothing_logged_when_selected_device_matches(self, caplog):
        """No fallback notice when the opened device is the configured
        one."""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._resolve_device.return_value = 5
        recorder._open_stream_for_candidates.return_value = (5, 16000, None)

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            start_recording(recorder)

        assert not any("saved selection unchanged" in rec.message for rec in caplog.records)

    def test_non_int_selected_device_skips_fallback_block(self, caplog):
        """A non-int device (e.g. None or a string) skips the
        fallback-notice block entirely."""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._open_stream_for_candidates.return_value = (
            "not-an-int",
            16000,
            None,
        )
        recorder._resolve_device.return_value = 5

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            start_recording(recorder)
        assert not any("saved selection unchanged" in rec.message for rec in caplog.records)
        recorder.config.save.assert_not_called()


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
        # Patch the resampling submodule's mutable globals so the
        # warm-up branch fires. ``start_recording`` reads them via a
        # deferred ``from voice_typer.server.recording import resampling``
        # at call time, so monkeypatching the submodule propagates to
        # the function call site.
        from voice_typer.server.recording import resampling as rec_resampling

        monkeypatch.setattr(rec_resampling, "_resample_poly", None, raising=False)
        monkeypatch.setattr(rec_resampling, "_resample_poly_error", None, raising=False)

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
        from voice_typer.server.recording import resampling as rec_pkg

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
        from voice_typer.server.recording import resampling as rec_pkg

        monkeypatch.setattr(rec_pkg, "_resample_poly", None, raising=False)
        monkeypatch.setattr(rec_pkg, "_resample_poly_error", RuntimeError("scipy missing"), raising=False)

        start_recording(recorder)

        recorder.warm_up_resampler.assert_not_called()


# ── AudioProcessor retune (: call re-added) ────────────────


class TestAudioProcessorRetune:
    """: ``start_recording`` calls ``retune_audio_processor`` to
       rebuild the AudioProcessor's filter chain at the device's native
       sample rate (unifying the start() and hot-plug paths). The call is
       wrapped in a ``try/except`` that logs-but-continues on failure —
       the per-chunk resample in ``AudioProcessor.process_chunk`` (invoked
       from ``audio_pipeline.process_audio_chunk`` with
       ``input_sample_rate=recorder._effective_sr``) remains as the
       robust fallback if ``set_sample_rate`` raises.

    These tests pin the contract: ``set_sample_rate`` (or
       ``rebuild_from_config`` when ``set_sample_rate`` is unavailable)
       MUST be called from ``start_recording`` when the chain rate differs
       from the device's effective sample rate. Filter-chain correctness
       is preserved either way (retune succeeds → chain at native rate;
       retune fails → per-chunk resample handles it).
    """

    def test_set_sample_rate_called_when_available(self, caplog):
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

        # retune IS now called — the chain is rebuilt at 48 kHz
        # so ``process_chunk`` doesn't need to resample 48 kHz → 16 kHz
        # per chunk on the worker thread.
        audio_processor.set_sample_rate.assert_called_once_with(48000)
        audio_processor.rebuild_from_config.assert_not_called()

    def test_rebuild_from_config_called_when_set_sample_rate_unavailable(self, caplog):
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )
        # No ``set_sample_rate`` attribute on the mock — the retune
        # helper falls through to ``rebuild_from_config`` (the spec-
        # limited fallback path).
        del audio_processor.set_sample_rate

        start_recording(recorder)

        audio_processor.rebuild_from_config.assert_called_once_with(recorder.config)

    def test_no_retune_when_processor_sr_matches(self, caplog):
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=16000,  # matches → skip retune (no-op)
            audio_processor=audio_processor,
            open_success=True,
        )

        start_recording(recorder)

        # When the chain rate already matches the device rate, the
        # retune helper short-circuits (no set_sample_rate call needed).
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
        """: a buggy ``AudioProcessor.set_sample_rate`` is invoked
        from ``start_recording`` but the ``try/except`` around the
        retune call catches the failure and logs a WARNING. ``start()``
        still completes successfully — the per-chunk resample in
        ``process_chunk`` runs as the robust fallback on the worker
        thread (and any failure there is caught by the audio worker's
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

        # Must not raise — the try/except logs a WARNING and continues.
        with caplog.at_level("WARNING", logger="voice_typer.server.recording"):
            start_recording(recorder)
        audio_processor.set_sample_rate.assert_called_once_with(48000)
        # the wrapper around retune_audio_processor logs a
        # WARNING with "retune_audio_processor failed on start".
        assert any("retune_audio_processor failed on start" in rec.message for rec in caplog.records), (
            ": a failed retune must be logged as a WARNING so the fallback per-chunk resample path is observable."
        )

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

        # Must not raise — the try/except logs a WARNING and continues.
        with caplog.at_level("WARNING", logger="voice_typer.server.recording"):
            start_recording(recorder)
        audio_processor.rebuild_from_config.assert_called_once_with(recorder.config)
        assert any("retune_audio_processor failed on start" in rec.message for rec in caplog.records), (
            ": a failed retune (rebuild_from_config path) must be "
            "logged as a WARNING so the fallback per-chunk resample "
            "path is observable."
        )

    def test_no_retune_when_processor_sr_is_none(self, caplog):
        """Defensive: when ``_audio_processor._sample_rate`` is None
        (e.g. an AudioProcessor test double that didn't set the
        attribute), the retune helper's ``int(None)`` check would
        raise. The helper itself guards against this (``_proc_sr is
        None`` short-circuits) — but if a future regression removes
        that guard, the ``try/except`` around the retune call in
        ``start_recording`` catches the ``TypeError`` and continues
        with the per-chunk resample fallback.
        """
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = None
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )

        # Must not raise — retune helper short-circuits on
        # ``_proc_sr is None`` (no set_sample_rate call); even if it
        # didn't, the try/except would catch the failure.
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
        body = _source_without_docstring(start_recording)
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
        body = _source_without_docstring(start_recording)
        sd_pattern = re.compile(r"\bsd\b")
        sounddevice_pattern = re.compile(r"sounddevice")
        assert not sd_pattern.search(body), "start_recording must not reference the `sd` proxy directly."
        assert not sounddevice_pattern.search(body), "start_recording must not reference `sounddevice` directly."

    def test_no_direct_subprocess_or_os_calls(self):
        """No ``os.system`` / ``subprocess.*`` calls — those would be
        a major layering violation.
        """
        body = _source_without_docstring(start_recording)
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
        body = _source_without_docstring(start_recording)
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
        body = _source_without_docstring(start_recording)
        assert "recorder._recording_event.set()" in body
        assert "self._recording_event" not in body

    def test_audio_processor_access_via_recorder(self):
        """: ``_audio_processor`` IS accessed from
        ``start_recording`` again — the retune call that was previously
        removed has been re-added (wrapped in a try/except that
        logs-but-continues on failure). The access MUST be via
        ``recorder._audio_processor`` (not ``self._audio_processor``)
        — the second assertion pins that.
        """
        body = _source_without_docstring(start_recording)
        # retune call re-added — recorder._audio_processor is
        # accessed (passed to retune_audio_processor).
        assert "recorder._audio_processor" in body, (
            ": start_recording must access recorder._audio_processor "
            "to pass it to retune_audio_processor (the retune call was "
            "re-added with a try/except wrapper for failure tolerance)."
        )
        assert "self._audio_processor" not in body

    def test_resolver_methods_called_via_recorder(self):
        """The device-enumeration helpers (``_resolve_device``,
        ``_same_physical_microphone_candidates``,
        ``_open_stream_for_candidates``, ``_open_stream_fallback``)
        must be called via ``recorder.X``.
        """
        body = _source_without_docstring(start_recording)
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
    """``start_recording`` performs its patchable collaborator imports
    lazily, INSIDE the function body, to avoid a circular import
    (recorder.py imports this module at module top). The mutable
    resampler state moved to the ``recording.resampling`` submodule, so
    the call-time import contract now targets ``...recording.resampling``
    (tests monkeypatch ``voice_typer.server.recording.resampling._resample_poly``
    and the function must re-read it at call time). The former
    ``from voice_typer.server import recording as _recording_pkg`` lazy
    import was REMOVED from ``start_recording`` by the contiguous-storage
    change's lint pass: after the resampling migration nothing in the
    function read through the package namespace anymore (the lazy
    ``_recording_pkg`` imports remain only in ``discard_recording`` /
    ``stop_recording``, which still route secure-clears through it).
    """

    def test_lazy_import_in_function_body(self):
        """The function body contains a call-time import of the mutable
        resampling namespace — pin this so a future refactor doesn't move
        it to module top (which would create a circular import).
        """
        body = _source_without_docstring(start_recording)
        assert "from voice_typer.server.recording import resampling as _recording_resampling" in body, (
            "start_recording must do the lazy resampling import inside its "
            "body — moving it to module top would re-introduce the "
            "circular import that recorder.py's top-level import of this "
            "module creates."
        )
        # And the dead package-namespace import must stay gone.
        assert "from voice_typer.server import recording as _recording_pkg" not in body, (
            "start_recording no longer reads through the package "
            "namespace; an unused lazy import here would be dead code."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
