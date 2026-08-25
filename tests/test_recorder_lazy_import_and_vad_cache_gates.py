"""Focused regression tests for three Phase 4 cold-start / hot-path fixes.

These tests pin the following invariants:

1. ``Recorder`` is NOT a module-top attribute of ``voice_typer.server.app``
   — it is imported lazily inside ``VoiceTyperApp.__init__`` (inside the
   STARTUP-9 background ``_build_recorder_subsystem`` closure) before the
   construction assignment ``self._recorder_backing = recorder``. This
   matches the deferred-import pattern already used for
   ``RecordingController``, ``ModelManager``, etc., and keeps the
   ``voice_typer.server.recording`` package (which eagerly loads 7+
   numpy-importing submodules) out of the module-import critical path.
   STARTUP-9 moved the eager ``self.recorder = Recorder(...)`` off the
   main thread entirely — the old assignment must NOT appear in
   ``__init__``'s source. Verified by ``hasattr`` check and by
   static-source inspection of ``VoiceTyperApp.__init__``.

2. The raw-RMS computation in
   ``AudioPipeline.process_audio_chunk`` (the ``np.dot`` reduction on
   ``indata``) is GATED on ``recorder._cached_vad_enabled`` so it is
   skipped entirely in raw mode (VAD off). When VAD is disabled the
   transient ``_pending_raw_chunk_rms`` attribute is set to ``0.0`` (no
   BLAS call); when VAD is enabled the raw RMS is computed as before.
   ``vad_auto_calibrate`` short-circuits with the same gate so the
   computed value would be discarded anyway.

3. ``vad_helpers.vad_auto_calibrate`` reads ``recorder._cached_vad_enabled``
   (the cached scalar set by ``refresh_vad_caches``) instead of the
   dynamic ``_vad_enabled`` property (which does a 5 s TTL cache lookup
   involving ``time.perf_counter()``). The cached scalar is always
   initialized to ``False`` in ``Recorder.__init__`` and refreshed before
   the first chunk arrives.

The tests use ``MagicMock`` stubs — no real audio I/O, no real VAD model,
no real PortAudio is touched. Designed to run on the Linux sandbox
without a working scipy installation.
"""

from __future__ import annotations

import collections
import inspect
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording.audio_pipeline import AudioPipeline
from voice_typer.server.recording.vad_helpers import vad_auto_calibrate

# ── (1) Recorder lazy-import in VoiceTyperApp.__init__ ────────────────


class TestRecorderLazyImport:
    """``Recorder`` is imported lazily inside
    ``VoiceTyperApp.__init__``, NOT at module top of
    ``voice_typer.server.app``.

    Importing ``Recorder`` at module top triggers
    ``voice_typer/server/recording/__init__.py`` which eagerly loads 7+
    submodules that each do ``import numpy as np`` at module top — adding
    ~250–335 ms to every cold start. Deferring the import to
    ``__init__`` keeps the recording package out of the module-import
    critical path.
    """

    def test_recorder_not_at_module_top(self) -> None:
        """``Recorder`` is NOT a module-top attribute of
        ``voice_typer.server.app`` — it's imported inside
        ``VoiceTyperApp.__init__``.
        """
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "Recorder"), (
            "Recorder should NOT be a module-top attribute of "
            "voice_typer.server.app — it should be imported inside "
            "VoiceTyperApp.__init__ to defer the recording package "
            "(and its eager numpy import chain) to first construction."
        )

    def test_recorder_import_lives_inside_init(self) -> None:
        """The ``from voice_typer.server.recording import Recorder``
        statement appears inside the recording-init builder's source
        (inside the background ``_build_recorder_subsystem`` closure),
        before the construction assignment
        ``self._recorder_backing = recorder``. The eager
        ``self.recorder = Recorder(...)`` assignment must NOT appear
        anywhere in the builder — it moved to the background
        recorder-init thread. (The body lived inline in
        ``VoiceTyperApp.__init__`` before the ``__init__`` decomposition;
        ``_init_recording`` is its new home.)
        """
        from voice_typer.server.app import VoiceTyperApp

        init_src = inspect.getsource(VoiceTyperApp._init_recording)
        # The lazy import statement must appear in the builder's body.
        assert "from voice_typer.server.recording import Recorder" in init_src, (
            "VoiceTyperApp._init_recording must contain the lazy import "
            "'from voice_typer.server.recording import Recorder' so the "
            "recording package is not imported at module top."
        )
        # the eager construction was moved to the background
        # recorder-init thread — the old assignment is gone from the builder.
        assert "self.recorder = Recorder(" not in init_src, (
            "the eager 'self.recorder = Recorder(...)' was removed "
            "from the recording-init builder — the recorder is built on "
            "the background recorder-init thread and assigned to "
            "_recorder_backing."
        )
        # And the import must appear BEFORE the construction assignment.
        import_idx = init_src.index("from voice_typer.server.recording import Recorder")
        construct_idx = init_src.index("self._recorder_backing = recorder")
        assert import_idx < construct_idx, (
            "The lazy 'from voice_typer.server.recording import Recorder' "
            "statement must appear BEFORE 'self._recorder_backing = "
            "recorder' inside VoiceTyperApp._init_recording."
        )

    def test_recorder_import_absent_from_module_top(self) -> None:
        """The module-top source of ``voice_typer.server.app`` must NOT
        contain ``from voice_typer.server.recording import Recorder``.
        """
        from voice_typer.server import app as _app_mod

        # Strip the body of VoiceTyperApp to avoid false positives from
        # the lazy import inside __init__. We only inspect the leading
        # section of the module (imports + class-level constants) up to
        # the first ``class`` / ``def`` definition.
        #
        # The lazy import inside __init__ is OK; the test rejects a
        # module-TOP import. We approximate by checking that the
        # recording import does not appear in the module's globals dict.
        assert "Recorder" not in _app_mod.__dict__, (
            "Recorder must not be bound in voice_typer.server.app's "
            "module __dict__ — the lazy import inside __init__ binds it "
            "as a local, not as a module global."
        )


# ── (2) Raw-RMS computation gated on _cached_vad_enabled ──────────────


def _make_process_chunk_recorder_stub(
    *,
    cached_vad_enabled: bool = True,
) -> MagicMock:
    """Build a MagicMock ``Recorder`` for ``process_audio_chunk`` tests.

    Sets ``_cached_vad_enabled`` explicitly so the test does NOT depend
    on MagicMock's default truthy attribute behavior — the gate reads
    this exact scalar.

    The six named helpers are MagicMock objects so the test can assert
    call counts. A real ``threading.Lock`` and ``deque`` are installed
    so the orchestration body's ``with self._recorder._lock:`` and
    ``self._recorder._recent_rms_values.append(chunk_rms)`` lines work
    with real semantics.
    """
    recorder = MagicMock(name="RecorderStub")
    recorder._cached_vad_enabled = cached_vad_enabled
    recorder._detect_device_disconnect.return_value = False
    recorder._handle_xrun_status.return_value = False
    # _apply_filter_chain returns a filtered array with a DIFFERENT RMS
    # than the raw indata, so the test can distinguish raw vs filtered.
    recorder._apply_filter_chain.return_value = np.array([0.5, -0.5, 0.5, -0.5], dtype=np.float32)
    recorder._append_to_buffer_locked.return_value = (1, 1)
    # _compute_rms_and_peak returns the FILTERED RMS (0.5) — distinct
    # from the raw RMS of the test's indata.
    recorder._compute_rms_and_peak.return_value = (0.5, 0.9, 0.032)
    recorder._detect_and_emit_clipping.return_value = None
    recorder._run_vad_state_machine.return_value = None
    recorder._lock = threading.Lock()
    recorder._recent_rms_values = collections.deque(maxlen=10)
    recorder._last_rms = None
    recorder._rms_callback_error_count = 0
    recorder.on_rms_level = None
    recorder.on_silence_warning = None
    recorder.on_silence_auto_stop = None
    recorder.on_max_duration_auto_stop = None
    recorder._recording_start_time = 100.0
    recorder._effective_sr = 16000
    return recorder


class TestRawRmsGatedOnCachedVadEnabled:
    """The raw-RMS computation in ``process_audio_chunk`` is gated on
    ``recorder._cached_vad_enabled`` so it is skipped entirely in raw
    mode (VAD off).

    When VAD is disabled, ``vad_auto_calibrate`` short-circuits with the
    same gate, so the computed raw RMS would be discarded. Skipping the
    computation saves one BLAS ``np.dot`` reduction per chunk (~16 Hz)
    in raw mode — pure waste otherwise.
    """

    def test_raw_rms_skipped_when_vad_disabled(self) -> None:
        """When ``_cached_vad_enabled`` is False, the raw-RMS ``np.dot``
        is NOT executed. The transient attribute is set to ``0.0`` (the
        cheap else-branch value) so ``run_vad_state_machine`` still
        finds a defined value if it reads the attribute.
        """
        recorder = _make_process_chunk_recorder_stub(cached_vad_enabled=False)
        pipeline = AudioPipeline(recorder)
        indata = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        # The transient attribute must be the cheap default (0.0), NOT
        # the raw RMS of indata (which is ~0.255).
        assert pipeline._pending_raw_chunk_rms == 0.0, (
            "When VAD is disabled (_cached_vad_enabled=False), the raw "
            "RMS computation must be SKIPPED — _pending_raw_chunk_rms "
            "should be the cheap default 0.0, not "
            f"{pipeline._pending_raw_chunk_rms}."
        )

    def test_raw_rms_computed_when_vad_enabled(self) -> None:
        """When ``_cached_vad_enabled`` is True, the raw-RMS ``np.dot``
        IS executed on the raw ``indata`` and stored on the transient
        attribute — preserves the existing VAD-auto-calibration feed
        path.
        """
        recorder = _make_process_chunk_recorder_stub(cached_vad_enabled=True)
        pipeline = AudioPipeline(recorder)
        indata = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)
        expected_raw_rms = float(np.sqrt(np.mean(indata**2)))

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        assert pipeline._pending_raw_chunk_rms == pytest.approx(expected_raw_rms), (
            "When VAD is enabled (_cached_vad_enabled=True), the raw RMS "
            "of indata must be computed and stored on _pending_raw_chunk_rms "
            f"(expected {expected_raw_rms}, got "
            f"{pipeline._pending_raw_chunk_rms})."
        )
        # And it must NOT be 0.0 (sanity check that the computation ran).
        assert pipeline._pending_raw_chunk_rms != 0.0, "Raw RMS must not be the cheap default 0.0 when VAD is enabled."

    def test_raw_rms_skipped_when_vad_disabled_and_indata_empty(self) -> None:
        """When VAD is disabled AND ``indata`` is empty, the gate
        short-circuits to the else branch (0.0) without computing
        anything. This is a defensive case — the gate must not raise.
        """
        recorder = _make_process_chunk_recorder_stub(cached_vad_enabled=False)
        pipeline = AudioPipeline(recorder)
        indata = np.zeros((0,), dtype=np.float32)

        pipeline.process_audio_chunk(indata, 0, None, 0, 12345.0)

        assert pipeline._pending_raw_chunk_rms == 0.0

    def test_raw_rms_gate_uses_cached_scalar_not_property(self) -> None:
        """The gate reads ``recorder._cached_vad_enabled`` (the cached
        scalar) — NOT the dynamic ``_vad_enabled`` property. Verified
        by setting the two to mismatched values and checking the
        computation follows the cached scalar.
        """
        recorder = _make_process_chunk_recorder_stub(cached_vad_enabled=False)
        # ``_vad_enabled`` (the dynamic property) returns True here as
        # a MagicMock attribute (truthy). If the gate read this instead
        # of the cached scalar, the raw RMS would be computed (not 0.0).
        # The cached scalar is False → raw RMS must be 0.0.
        recorder._vad_enabled = True  # mismatched — would force computation
        pipeline = AudioPipeline(recorder)
        indata = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)

        pipeline.process_audio_chunk(indata, 4, None, 0, 12345.0)

        assert pipeline._pending_raw_chunk_rms == 0.0, (
            "The raw-RMS gate must read 'recorder._cached_vad_enabled' "
            "(the cached scalar = False), NOT 'recorder._vad_enabled' "
            "(the dynamic property = True here). If the gate read the "
            "property, the raw RMS would be ~0.255, not 0.0."
        )


# ── (3) vad_auto_calibrate reads _cached_vad_enabled ──────────────────


def _make_vad_auto_calibrate_recorder_stub(
    *,
    cached_vad_enabled: bool,
    vad_enabled: bool,
) -> MagicMock:
    """Build a MagicMock ``Recorder`` for ``vad_auto_calibrate`` tests.

    Sets BOTH ``_cached_vad_enabled`` (the cached scalar) and
    ``_vad_enabled`` (the dynamic property) so tests can mismatch them
    and prove which one the gate reads.
    """
    recorder = MagicMock(name="RecorderStub")
    recorder._cached_vad_enabled = cached_vad_enabled
    recorder._vad_enabled = vad_enabled  # mismatched by design
    recorder._recording_start_time = 100.0
    # ``_vad.auto_calibrate`` is the downstream call — mock counts it.
    recorder._vad.auto_calibrate.return_value = None
    return recorder


class TestVadAutoCalibrateReadsCachedScalar:
    """``vad_auto_calibrate`` reads ``recorder._cached_vad_enabled`` (the
    cached scalar set by ``refresh_vad_caches``) — NOT the dynamic
    ``_vad_enabled`` property (which does a 5 s TTL cache lookup
    involving ``time.perf_counter()``). The cached scalar is always
    initialized to ``False`` in ``Recorder.__init__`` and refreshed
    before the first chunk arrives.
    """

    def test_short_circuits_when_cached_scalar_false_even_if_property_true(
        self,
    ) -> None:
        """When ``_cached_vad_enabled`` is False but ``_vad_enabled``
        is True (mismatched), the gate MUST short-circuit and NOT call
        ``recorder._vad.auto_calibrate``. This proves the gate reads
        the cached scalar.
        """
        recorder = _make_vad_auto_calibrate_recorder_stub(
            cached_vad_enabled=False,
            vad_enabled=True,
        )

        vad_auto_calibrate(recorder, chunk_rms=0.5, chunk_duration=0.032)

        recorder._vad.auto_calibrate.assert_not_called()

    def test_proceeds_when_cached_scalar_true_even_if_property_false(
        self,
    ) -> None:
        """When ``_cached_vad_enabled`` is True but ``_vad_enabled``
        is False (mismatched), the gate MUST proceed and call
        ``recorder._vad.auto_calibrate``. This proves the gate reads
        the cached scalar (not the property).
        """
        recorder = _make_vad_auto_calibrate_recorder_stub(
            cached_vad_enabled=True,
            vad_enabled=False,
        )

        vad_auto_calibrate(recorder, chunk_rms=0.5, chunk_duration=0.032)

        recorder._vad.auto_calibrate.assert_called_once()

    def test_does_not_call_perf_counter_when_cached_scalar_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the gate short-circuits (cached scalar False), the
        function must NOT call ``time.perf_counter()`` — that's the
        whole point of the cached-scalar optimization (the property
        lookup path does a ``perf_counter`` for its 5 s TTL cache).

        Verified by patching ``time.perf_counter`` in the vad_helpers
        module namespace and asserting call count. Uses pytest's
        ``monkeypatch`` so the patch is auto-undone after the test
        (no global state leakage across tests when running under
        pytest-xdist in the same worker process).
        """
        import voice_typer.server.recording.vad_helpers as vad_helpers_mod

        recorder = _make_vad_auto_calibrate_recorder_stub(
            cached_vad_enabled=False,
            vad_enabled=True,
        )

        perf_calls: list[float] = []

        def spy_perf_counter() -> float:
            perf_calls.append(0.0)
            return 0.0

        # Patch the time module's perf_counter via monkeypatch so the
        # spy is auto-removed at test teardown (the time module is
        # shared globally; a manual setattr would leak to other tests
        # running in the same xdist worker process).
        monkeypatch.setattr(vad_helpers_mod.time, "perf_counter", spy_perf_counter)

        vad_auto_calibrate(recorder, chunk_rms=0.5, chunk_duration=0.032)

        assert perf_calls == [], (
            "time.perf_counter must NOT be called when the cached "
            "scalar gate short-circuits — that's the whole point of "
            f"the cached-scalar optimization (got {len(perf_calls)} calls)."
        )

        # And downstream auto_calibrate was not called either.
        recorder._vad.auto_calibrate.assert_not_called()
