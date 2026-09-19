"""Tests for ``_recorder_split.start_recording``."""

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
    """Build a MagicMock recorder with the minimum stubs"""
    recorder = MagicMock(name="recorder")
    recorder.config = MagicMock(name="config")
    recorder.config.sample_rate = sample_rate
    recorder.config.microphone = None
    recorder.config.save.return_value = True

    # `_cache_session_config` returns the per-session max_rec seconds.
    recorder._session_state.cache_session_config.return_value = 30

    recorder._devices._resolve_device.return_value = device
    recorder._devices._same_physical_microphone_candidates.return_value = [device]

    callback_sentinel = object()
    recorder._stream_lifecycle.build_audio_callback.return_value = callback_sentinel

    if effective_sr is None:
        effective_sr = sample_rate

    if open_success:
        # `_open_stream_for_candidates` returns
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (
            device,
            effective_sr,
            None,
        )
        recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")
    else:
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (
            None,
            sample_rate,
            RuntimeError("no mic"),
        )
        recorder._stream_lifecycle._stream = None

    recorder._recording_event = threading.Event()
    recorder._audio_processor = audio_processor

    recorder._preroll_active = False
    recorder._preroll_seconds = 0.0
    recorder._preroll_buffer = collections.deque(maxlen=0)
    # Real scalar sample rates: ``refresh_vad_caches`` (invoked directly
    recorder._audio_pipeline._buffer_sr = None
    recorder._effective_sr = sample_rate

    return recorder


def _source_without_docstring(func) -> str:
    """Return ``func``'s source with the leading docstring statement removed."""
    import ast
    import textwrap

    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    fn = tree.body[0]
    # Narrow to node types that actually carry a ``body`` (pyrefly types
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return src
    first = fn.body[0] if fn.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        lines = src.splitlines(keepends=True)
        del lines[first.lineno - 1 : first.end_lineno]
        src = "".join(lines)
    return src


class TestStartRecordingHappyPath:
    """Verify the body of ``Recorder.start`` runs end-to-end when the"""

    def test_runs_all_steps_in_order(self, monkeypatch):
        """When the stream opens on the first candidate,"""
        recorder = _build_mock_recorder()
        import voice_typer.server.recording._recorder_split as split_mod

        refresh_vad_mock = MagicMock()
        monkeypatch.setattr(split_mod, "refresh_vad_caches", refresh_vad_mock)
        start_recording(recorder)

        recorder._secure_clear_session_caches.assert_called_once()
        recorder._session_state.reset_session_state.assert_called_once()
        recorder._session_state.cache_session_config.assert_called_once()
        recorder._devices._resolve_device.assert_called_once()
        recorder._devices._same_physical_microphone_candidates.assert_called_once_with(
            recorder._devices._resolve_device.return_value
        )
        recorder._stream_lifecycle.build_audio_callback.assert_called_once()
        recorder._stream_lifecycle.open_stream_for_candidates.assert_called_once()
        # No fallback because the stream opened on the first candidate.
        recorder._stream_lifecycle.open_stream_fallback.assert_not_called()
        recorder._session_state.resize_buffers_for_sample_rate.assert_called_once()
        assert recorder._recording_event.is_set()
        recorder._session_state.prepend_preroll_to_buffer.assert_not_called()
        refresh_vad_mock.assert_called_once_with(recorder)
        recorder._start_audio_worker.assert_called_once()
        recorder._capture.start_event_worker_body.assert_called_once()
        recorder._devices._start_device_health_checker.assert_called_once()

    def test_step_order_matches_contract(self, monkeypatch):
        """Pin the source-order contract: cache-clear → state reset →"""
        recorder = _build_mock_recorder()
        call_log: list[str] = []

        def log_call(name, ret=None):
            def _hook(*a, **k):
                call_log.append(name)
                return ret

            return _hook

        # Stub every method that the function calls in source order.
        recorder._secure_clear_session_caches.side_effect = log_call("secure_clear")
        recorder._session_state.reset_session_state.side_effect = log_call("reset_session")
        recorder._session_state.cache_session_config.side_effect = log_call("cache_config", ret=30)
        recorder._devices._resolve_device.side_effect = log_call("resolve_device", ret=5)
        recorder._devices._same_physical_microphone_candidates.side_effect = log_call("candidates", ret=[5])
        recorder._stream_lifecycle.build_audio_callback.side_effect = log_call("build_callback", ret=object())
        recorder._stream_lifecycle.open_stream_for_candidates.side_effect = log_call(
            "open_stream", ret=(5, 16000, None)
        )
        recorder._session_state.resize_buffers_for_sample_rate.side_effect = log_call("resize_buffers")
        recorder._recording_event = MagicMock(wraps=threading.Event())
        recorder._recording_event.set.side_effect = log_call("event.set")
        import voice_typer.server.recording._recorder_split as split_mod

        monkeypatch.setattr(split_mod, "refresh_vad_caches", log_call("refresh_vad"))
        recorder._start_audio_worker.side_effect = log_call("start_audio_worker")
        recorder._capture.start_event_worker_body.side_effect = log_call("start_event_worker")
        recorder._devices._start_device_health_checker.side_effect = log_call("start_device_health_checker")

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
        """Pin the call-arg contract for ``_open_stream_for_candidates``:"""
        recorder = _build_mock_recorder()
        start_recording(recorder)

        args, _ = recorder._stream_lifecycle.open_stream_for_candidates.call_args
        rec_arg, candidates, callback, eff_sr, last_err = args
        assert rec_arg is recorder
        assert candidates == recorder._devices._same_physical_microphone_candidates.return_value
        assert callback is recorder._stream_lifecycle.build_audio_callback.return_value
        assert eff_sr == recorder.config.sample_rate
        assert last_err is None


class TestDeviceEnumerationAtFunctionScope:
    """device-enumeration block (``last_error``, ``selected_device``,"""

    def test_locals_are_at_function_scope(self):
        """``last_error``, ``selected_device``, ``effective_sr``,"""
        code = start_recording.__code__
        for name in ("last_error", "selected_device", "effective_sr", "used_fallback"):
            assert name in code.co_varnames, (
                f"CRITICAL contract: `{name}` must be a local of "
                f"start_recording (in co_varnames), not of the "
                f"callback closure. A previous merge regressed this and "
                f"crashed every recording start with UnboundLocalError."
            )

    def test_locals_not_in_co_cellvars(self):
        """The device-enumeration locals are NOT captured by any"""
        code = start_recording.__code__
        for name in ("last_error", "selected_device", "effective_sr"):
            assert name not in code.co_cellvars, (
                f"CRITICAL contract: `{name}` must NOT be captured by a "
                f"nested closure. If it were, it would be a cellvar, not "
                f"a local of start_recording, and the structural "
                f"device-enumeration contract would be violated."
            )

    def test_no_persistence_closure_in_body(self):
        """fallback device is session-local only, so ``start_recording``"""
        code = start_recording.__code__
        for name in ("_persist_mic", "mic-fallback-save"):
            assert name not in code.co_varnames
            assert name not in code.co_cellvars
            assert name not in code.co_freevars
            assert all(name not in str(c) for c in code.co_consts), (
                f"CRITICAL contract: `{name}` must not appear anywhere in "
                "start_recording, the microphone-fallback persistence "
                "block was removed because auto-writing config.microphone "
                "silently replaced the user's saved selection."
            )
        assert "self" not in code.co_varnames
        assert "self" not in code.co_cellvars
        assert "self" not in code.co_freevars


class TestStartRecordingFallbackPath:
    """When the same-name candidates all fail to open a stream,"""

    def test_fallback_called_when_first_candidate_returns_none_stream(self):
        """When ``open_stream_for_candidates`` returns"""
        recorder = _build_mock_recorder(open_success=False)
        # First attempt fails (``_stream`` is None);
        assert recorder._stream_lifecycle._stream is None
        original_err = recorder._stream_lifecycle.open_stream_for_candidates.return_value[2]

        def _fallback_side_effect(rec, candidates, callback, eff_sr, last_err):
            recorder._stream_lifecycle._stream = MagicMock(name="fallback-stream")
            return (7, 16000, True, None)

        recorder._stream_lifecycle.open_stream_fallback.side_effect = _fallback_side_effect

        start_recording(recorder)

        recorder._stream_lifecycle.open_stream_fallback.assert_called_once()
        args, _ = recorder._stream_lifecycle.open_stream_fallback.call_args
        rec_arg, candidates, callback, eff_sr, last_err = args
        assert rec_arg is recorder
        assert candidates == recorder._devices._same_physical_microphone_candidates.return_value
        assert callback is recorder._stream_lifecycle.build_audio_callback.return_value
        assert last_err is original_err, (
            "The ``last_error`` captured from the failed candidate "
            "attempt must be propagated to ``open_stream_fallback`` "
            "as the 5th positional arg (after the recorder)."
        )

    def test_raises_last_error_when_all_paths_fail(self):
        """When both the candidate path AND the fallback path fail"""
        recorder = _build_mock_recorder(open_success=False)
        original_err = OSError("[Errno -9998] PortAudio invalid channel count")
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (
            None,
            16000,
            original_err,
        )
        recorder._stream_lifecycle.open_stream_fallback.return_value = (
            None,
            16000,
            True,
            original_err,  # same error propagated from the candidate path
        )
        recorder._stream_lifecycle._stream = None

        with pytest.raises(OSError) as exc_info:
            start_recording(recorder)
        assert exc_info.value is original_err, (
            "start_recording must re-raise the captured last_error "
            "verbatim (not wrap it), so the caller sees the underlying "
            "PortAudio/OSError failure mode."
        )

    def test_raises_runtime_error_when_no_error_recorded(self):
        """Defensive contract: if the device enumeration loop"""
        recorder = _build_mock_recorder(open_success=False)
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (None, 16000, None)
        recorder._stream_lifecycle.open_stream_fallback.return_value = (None, 16000, True, None)
        recorder._stream_lifecycle._stream = None

        with pytest.raises(RuntimeError, match="No input device could be opened"):
            start_recording(recorder)


class TestMicrophoneFallbackSessionLocal:
    """When the opened device differs from the configured ``device``"""

    def test_config_microphone_not_overwritten_on_fallback(self):
        """``selected_device != device`` → the stream runs on the"""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._devices._resolve_device.return_value = 5
        recorder.config.microphone = "Windows WASAPI|USB Mic"

        start_recording(recorder)

        assert recorder.config.microphone == "Windows WASAPI|USB Mic", (
            "config.microphone must NOT be auto-rewritten when a "
            "fallback device is used, the saved selection belongs to "
            "the user."
        )
        recorder.config.save.assert_not_called()

    def test_no_persistence_thread_spawned_when_selected_device_differs(self):
        """No persistence thread (``mic-fallback-save``) may be spawned"""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._devices._resolve_device.return_value = 5

        start_recording(recorder)

        for call in recorder._spawn_device_thread.call_args_list:
            assert call.kwargs.get("name") != "mic-fallback-save", (
                "the mic-fallback persistence thread was removed; spawning "
                "it again would silently overwrite the user's selection"
            )

    def test_fallback_logs_session_local_notice(self, caplog):
        """The fallback path logs that the saved selection is unchanged"""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._devices._resolve_device.return_value = 5

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            start_recording(recorder)

        assert any(
            "saved selection unchanged" in rec.message and "[RECORDING]" in rec.message for rec in caplog.records
        ), "fallback usage must log an INFO line noting the saved selection is unchanged."

    def test_nothing_logged_when_selected_device_matches(self, caplog):
        """No fallback notice when the opened device is the configured"""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._devices._resolve_device.return_value = 5
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (5, 16000, None)

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            start_recording(recorder)

        assert not any("saved selection unchanged" in rec.message for rec in caplog.records)

    def test_non_int_selected_device_skips_fallback_block(self, caplog):
        """A non-int device (e.g. None or a string) skips the"""
        recorder = _build_mock_recorder(device=5, open_success=True)
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (
            "not-an-int",
            16000,
            None,
        )
        recorder._devices._resolve_device.return_value = 5

        with caplog.at_level("INFO", logger="voice_typer.server.recording"):
            start_recording(recorder)
        assert not any("saved selection unchanged" in rec.message for rec in caplog.records)
        recorder.config.save.assert_not_called()


class TestResamplerWarmUp:
    """When the effective sample rate differs from the configured"""

    def test_warm_up_called_when_sr_differs_and_poly_not_loaded(self, monkeypatch):
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,  # differs from target_sr
            open_success=True,
        )
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

        # Pretend scipy is already loaded, skip the synchronous warm-up.
        monkeypatch.setattr(rec_pkg, "_resample_poly", object(), raising=False)
        monkeypatch.setattr(rec_pkg, "_resample_poly_error", None, raising=False)

        start_recording(recorder)

        recorder.warm_up_resampler.assert_not_called()

    def test_warm_up_skipped_when_poly_failed_before(self, monkeypatch):
        """If a previous warm-up attempt failed"""
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


class TestAudioProcessorRetune:
    """rebuild the AudioProcessor's filter chain at the device's native"""

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
        # No ``set_sample_rate`` attribute on the mock, the retune
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

        audio_processor.set_sample_rate.assert_not_called()
        audio_processor.rebuild_from_config.assert_not_called()

    def test_no_retune_when_no_audio_processor(self, caplog):
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=None,
            open_success=True,
        )

        # Must not raise, with no audio processor, no retune is needed
        start_recording(recorder)

    def test_set_sample_rate_failure_does_not_break_start(self, caplog):
        """: a buggy ``AudioProcessor.set_sample_rate`` is invoked"""
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = 16000
        audio_processor.set_sample_rate.side_effect = RuntimeError("simulated bug")
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )

        # Must not raise, the try/except logs a WARNING and continues.
        with caplog.at_level("WARNING", logger="voice_typer.server.recording"):
            start_recording(recorder)
        audio_processor.set_sample_rate.assert_called_once_with(48000)
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

        # Must not raise, the try/except logs a WARNING and continues.
        with caplog.at_level("WARNING", logger="voice_typer.server.recording"):
            start_recording(recorder)
        audio_processor.rebuild_from_config.assert_called_once_with(recorder.config)
        assert any("retune_audio_processor failed on start" in rec.message for rec in caplog.records), (
            ": a failed retune (rebuild_from_config path) must be "
            "logged as a WARNING so the fallback per-chunk resample "
            "path is observable."
        )

    def test_no_retune_when_processor_sr_is_none(self, caplog):
        """Defensive: when ``_audio_processor._sample_rate`` is None"""
        audio_processor = MagicMock(name="AudioProcessor")
        audio_processor._sample_rate = None
        recorder = _build_mock_recorder(
            sample_rate=16000,
            effective_sr=48000,
            audio_processor=audio_processor,
            open_success=True,
        )

        start_recording(recorder)

        audio_processor.set_sample_rate.assert_not_called()
        audio_processor.rebuild_from_config.assert_not_called()


class TestRecordingEventContract:
    """``_recording_event.set()`` must be called BEFORE the audio"""

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
        recorder._capture.start_event_worker_body.side_effect = log_call("start_event_worker")
        recorder._devices._start_device_health_checker.side_effect = log_call("start_device_health_checker")

        start_recording(recorder)

        assert "event.set" in call_log
        event_idx = call_log.index("event.set")
        assert "start_audio_worker" in call_log
        assert "start_event_worker" in call_log
        assert "start_device_health_checker" in call_log
        assert event_idx < call_log.index("start_audio_worker")
        assert event_idx < call_log.index("start_event_worker")
        assert event_idx < call_log.index("start_device_health_checker")


class TestNoRealExternalDeps:
    """Sanity-check that ``start_recording`` does NOT touch:"""

    def test_permissions_module_not_imported_in_function_body(self):
        """``Recorder.start``)."""
        body = _source_without_docstring(start_recording)
        assert "verify_microphone_accessible" not in body, (
            "start_recording must NOT call the permissions module, that's "
            "the responsibility of Recorder.start's _start_lock block."
        )
        assert "import permissions" not in body, (
            "start_recording must not import the permissions module, the "
            "lock-gate permission check stays on Recorder.start."
        )

    def test_no_sd_or_sounddevice_references_in_function_body(self):
        """``sounddevice`` directly, all PortAudio interaction happens"""
        body = _source_without_docstring(start_recording)
        sd_pattern = re.compile(r"\bsd\b")
        sounddevice_pattern = re.compile(r"sounddevice")
        assert not sd_pattern.search(body), "start_recording must not reference the `sd` proxy directly."
        assert not sounddevice_pattern.search(body), "start_recording must not reference `sounddevice` directly."

    def test_no_direct_subprocess_or_os_calls(self):
        """No ``os.system`` / ``subprocess.*`` calls, those would be"""
        body = _source_without_docstring(start_recording)
        assert "subprocess" not in body
        assert "os.system" not in body


class TestSourceRewritingContract:
    """future merge that re-introduces ``self.`` in the body would"""

    def test_no_self_references_in_body(self):
        """No ``self.`` references in the function body. All instance"""
        body = _source_without_docstring(start_recording)
        # ``self.X`` followed by an identifier is a real reference.
        self_pattern = re.compile(r"\bself\.\w")
        matches = self_pattern.findall(body)
        assert not matches, f"start_recording body must not reference `self.X` (only `recorder.X`). Found: {matches}"

    def test_recording_event_access_via_recorder(self):
        """``_recording_event`` access must be via"""
        body = _source_without_docstring(start_recording)
        assert "recorder._recording_event.set()" in body
        assert "self._recording_event" not in body

    def test_audio_processor_access_via_recorder(self):
        """``start_recording`` again, the retune call that was previously"""
        body = _source_without_docstring(start_recording)
        assert "recorder._audio_processor" in body, (
            ": start_recording must access recorder._audio_processor "
            "to pass it to retune_audio_processor (the retune call was "
            "re-added with a try/except wrapper for failure tolerance)."
        )
        assert "self._audio_processor" not in body

    def test_resolver_methods_called_via_recorder(self):
        """The device-enumeration helpers (``_resolve_device``,"""
        body = _source_without_docstring(start_recording)
        for owner, method in (
            ("recorder._devices", "_resolve_device"),
            ("recorder._devices", "_same_physical_microphone_candidates"),
            ("recorder._stream_lifecycle", "open_stream_for_candidates"),
            ("recorder._stream_lifecycle", "open_stream_fallback"),
        ):
            assert f"{owner}.{method}" in body, (
                f"start_recording must call `{method}` via {owner}.{method}, not via self.{method}."
            )
            assert f"self.{method}" not in body, (
                f"start_recording must NOT call `self.{method}`, the body was rewritten to use {owner}.{method}."
            )


class TestLazyPackageImport:
    """``start_recording`` performs its patchable collaborator imports"""

    def test_lazy_import_in_function_body(self):
        """The function body contains a call-time import of the mutable"""
        body = _source_without_docstring(start_recording)
        assert "from voice_typer.server.recording import resampling as _recording_resampling" in body, (
            "start_recording must do the lazy resampling import inside its "
            "body, moving it to module top would re-introduce the "
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
