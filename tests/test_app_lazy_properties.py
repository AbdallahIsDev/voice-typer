"""regression tests: lazy ``history_db`` and ``_audio_processor``
properties on ``VoiceTyperApp``.

Pins the lazy-construction contract for the two heaviest cold-start
subsystems deferred by 1. ``history_db`` ``HistoryDB()`` construction was
   eagerly called in ``__init__``, blocking up to 30s on the writer
   thread's schema-init. The lazy ``@property`` defers construction to
   first access. A ``_shutting_down_event`` guard prevents the shutdown
   teardown path (``shutdown/teardowns/history_db.py``) from triggering
   lazy construction via its ``if app.history_db is not None:`` check.

2. ``_audio_processor`` ``AudioProcessor(...)`` construction
   was eagerly called in ``__init__``, pulling in the full
   ``audio_filters`` package + ``scipy.signal.butter`` (via
   ``build_chain``) on every cold start. The lazy ``@property`` returns
   a ``_LazyAudioProcessorProxy`` that defers the real construction
   (and the transitive ``audio_filters`` import chain) to first
   attribute access.

Both properties expose a setter so existing tests that inject mocks
via ``app.history_db = MagicMock()`` / ``app._audio_processor =
MagicMock()`` use the setter, which bypasses lazy construction.

These tests run on the Linux sandbox. ``scipy`` is mocked at module
level (via ``sys.modules``) because the test environment has a
numpy/scipy version mismatch (numpy 1.26.4 vs scipy 1.18.0) that
breaks the real ``scipy.signal`` import — mocking scipy lets the
test construct a real ``VoiceTyperApp`` without paying the
``audio_filters`` import cost (which is the whole point of the fix).
"""

from __future__ import annotations

import sys
import threading
import types
from unittest.mock import MagicMock

import pytest

# ─── Shared fixtures ────────────────────────────────────────────────────


def _patch_app_platform_helpers(monkeypatch):
    """Patch the platform helpers that ``VoiceTyperApp.__init__`` touches.

    Mirrors the helper in ``tests/test_lazy_subsystem_construction.py``.
    The helpers are resolved at call time from their canonical home
    ``voice_typer.server.server_platform`` (deferred imports inside
    ``startup_tasks.sync_autostart`` / ``load_microphones``), so that is
    the module to patch.
    """
    from voice_typer.server import server_platform

    monkeypatch.setattr(server_platform, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(server_platform, "enable_autostart", lambda: True)
    monkeypatch.setattr(server_platform, "disable_autostart", lambda: True)
    monkeypatch.setattr(server_platform, "list_microphones", lambda: [])


@pytest.fixture(autouse=True)
def _mock_scipy(monkeypatch):
    """Mock scipy so ``audio_filters.highpass`` / ``audio_pipeline``
    don't crash on the numpy/scipy version mismatch in this env.

    The mock is a per-test ``sys.modules`` injection (auto-undone by
    ``monkeypatch``) so it doesn't leak across tests.
    """
    mock_scipy = MagicMock(name="mock_scipy")
    mock_scipy_signal = MagicMock(name="mock_scipy.signal")
    mock_scipy.signal = mock_scipy_signal
    monkeypatch.setitem(sys.modules, "scipy", mock_scipy)
    monkeypatch.setitem(sys.modules, "scipy.signal", mock_scipy_signal)


# ─── lazy history_db ─────────────────────────────────────────────


class TestHistoryDbLazyConstruction:
    """``HistoryDB()`` must NOT be constructed in ``__init__``.

    The lazy ``@property`` defers construction to first access. A
    ``_shutting_down_event`` guard prevents the shutdown teardown path
    from triggering construction.
    """

    def test_history_db_backing_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """``_history_db_backing`` must be ``None`` after ``__init__`` —
        ``HistoryDB()`` is NOT eagerly constructed.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._history_db_backing is None, (
            "HistoryDB must NOT be eagerly constructed in __init__; _history_db_backing should start as None."
        )

    def test_history_db_not_constructed_at_init_via_mock(self, tmp_config_dir, monkeypatch):
        """(a): ``HistoryDB()`` is NOT called during
        ``VoiceTyperApp.__init__``. Verified by spying on the
        ``HistoryDB`` class constructor.
        """
        _patch_app_platform_helpers(monkeypatch)
        # Patch HistoryDB on the app module BEFORE constructing
        # VoiceTyperApp. The lazy property's getter does
        # ``HistoryDB()`` (an unqualified lookup that resolves via the
        # module's globals), so monkeypatching
        # ``voice_typer.server.app.HistoryDB`` is the correct patch
        # target.
        from voice_typer.server import app as _app_mod

        mock_history_db_cls = MagicMock(name="MockHistoryDB")
        monkeypatch.setattr(_app_mod, "HistoryDB", mock_history_db_cls)

        instance = _app_mod.VoiceTyperApp()

        # HistoryDB() must NOT have been called during __init__.
        assert mock_history_db_cls.call_count == 0, (
            "HistoryDB() was called during __init__ — the lazy property should defer construction to first access."
        )
        assert instance._history_db_backing is None

    def test_history_db_constructed_on_first_access(self, tmp_config_dir, monkeypatch):
        """(b): first access of ``app.history_db`` constructs a
        ``HistoryDB`` and caches it in ``_history_db_backing``.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod
        from voice_typer.server.history_db import HistoryDB

        instance = _app_mod.VoiceTyperApp()
        assert instance._history_db_backing is None

        # First access triggers construction.
        db = instance.history_db
        assert isinstance(db, HistoryDB), "First access to app.history_db must construct a HistoryDB."
        # Cached: a second access returns the same instance.
        assert instance.history_db is db
        assert instance._history_db_backing is db, (
            "First access must cache the constructed instance in _history_db_backing."
        )

    def test_history_db_setter_bypasses_construction(self, tmp_config_dir, monkeypatch):
        """(c): assigning via the setter stores directly into the
        backing — a subsequent getter call returns the assigned value
        without invoking the lazy constructor. This is the contract
        tests rely on when they inject mocks via
        ``app.history_db = MagicMock()``.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod

        mock_history_db_cls = MagicMock(name="MockHistoryDB")
        monkeypatch.setattr(_app_mod, "HistoryDB", mock_history_db_cls)

        instance = _app_mod.VoiceTyperApp()

        sentinel = MagicMock(name="fake_history_db")
        instance.history_db = sentinel

        # Setter must store into backing; getter must return the
        # sentinel (no construction).
        assert instance.history_db is sentinel, (
            "Setter for history_db must store into the backing; getter "
            "must return the assigned sentinel (no construction)."
        )
        assert instance._history_db_backing is sentinel
        # HistoryDB() must NOT have been called (the setter bypasses
        # lazy construction).
        assert mock_history_db_cls.call_count == 0, (
            "Setter must bypass lazy construction — HistoryDB() was "
            "called even though a sentinel was assigned via the setter."
        )

    def test_history_db_returns_none_during_shutdown(self, tmp_config_dir, monkeypatch):
        """when ``_shutting_down_event`` is set, the lazy getter
        returns ``None`` instead of constructing a ``HistoryDB``. This
        prevents the shutdown teardown path
        (``shutdown/teardowns/history_db.py``) from triggering lazy
        construction via its ``if app.history_db is not None:`` check
        on a never-dictated session.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod

        mock_history_db_cls = MagicMock(name="MockHistoryDB")
        monkeypatch.setattr(_app_mod, "HistoryDB", mock_history_db_cls)

        instance = _app_mod.VoiceTyperApp()
        # Simulate shutdown — quit() / restart_app() sets this before
        # the teardown path runs.
        instance._shutting_down_event.set()

        # Accessing history_db during shutdown must return None
        # (no construction).
        assert instance.history_db is None, (
            "history_db getter must return None when _shutting_down_event "
            "is set — prevents lazy construction during shutdown teardown."
        )
        assert mock_history_db_cls.call_count == 0, "HistoryDB() must NOT be called when _shutting_down_event is set."


# ─── lazy _audio_processor ───────────────────────────────────────


class TestAudioProcessorLazyConstruction:
    """``AudioProcessor(...)`` must NOT be constructed in
    ``__init__``. The lazy ``@property`` returns a
    ``_LazyAudioProcessorProxy`` that defers the real construction to
    first attribute access.
    """

    def test_audio_processor_backing_is_proxy_after_init(self, tmp_config_dir, monkeypatch):
        """After ``__init__``, ``_audio_processor_backing`` is a
        ``_LazyAudioProcessorProxy`` (NOT a real ``AudioProcessor``).
        The proxy defers construction to first attribute access.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp, _LazyAudioProcessorProxy

        instance = VoiceTyperApp()
        # STARTUP-9: ``Recorder(...)`` (whose ``audio_processor=`` argument
        # access materializes the proxy) is constructed on a background
        # thread. Wait for that build to finish before asserting —
        # otherwise the assertion races the thread and intermittently
        # sees ``None`` (observed under full-suite parallel load).
        assert instance._recorder_build_ready.wait(10.0), "recorder background build did not finish within 10s"
        if instance._recorder_build_error is not None:
            raise instance._recorder_build_error
        # The backing is the proxy (created lazily by the getter when
        # __init__ passed audio_processor=self._audio_processor to
        # Recorder).
        backing = instance._audio_processor_backing
        assert backing is not None, (
            "_audio_processor_backing should be a _LazyAudioProcessorProxy "
            "after __init__ (the Recorder constructor accesses the property)."
        )
        assert isinstance(backing, _LazyAudioProcessorProxy), (
            f"_audio_processor_backing should be a _LazyAudioProcessorProxy, got {type(backing).__name__}."
        )
        # The proxy's _real must be None (no construction yet).
        assert object.__getattribute__(backing, "_real") is None, (
            "The _LazyAudioProcessorProxy must NOT have constructed the "
            "real AudioProcessor during __init__ — _real should be None."
        )

    def test_audio_processor_not_constructed_at_init_via_mock(self, tmp_config_dir, monkeypatch):
        """(a): ``AudioProcessor(...)`` is NOT called during
        ``VoiceTyperApp.__init__``. Verified by spying on the
        ``AudioProcessor`` class constructor inside the proxy's
        ``_resolve`` method (which is the only call site that should
        construct a real ``AudioProcessor``).
        """
        _patch_app_platform_helpers(monkeypatch)
        # Patch AudioProcessor inside the proxy's _resolve method.
        # The proxy does ``from voice_typer.server.audio_processor
        # import AudioProcessor`` inside _resolve, so patching
        # ``voice_typer.server.audio_processor.AudioProcessor`` is the
        # correct target.
        from voice_typer.server import app as _app_mod, audio_processor as _ap_mod

        mock_ap_cls = MagicMock(name="MockAudioProcessor")
        monkeypatch.setattr(_ap_mod, "AudioProcessor", mock_ap_cls)

        _app_mod.VoiceTyperApp()

        # AudioProcessor(...) must NOT have been called during __init__.
        assert mock_ap_cls.call_count == 0, (
            "AudioProcessor() was called during __init__ — "
            "the lazy proxy should defer construction to first attribute "
            "access."
        )

    def test_audio_processor_constructed_on_first_attribute_access(self, tmp_config_dir, monkeypatch):
        """(b): first attribute access on the proxy triggers
        construction of the real ``AudioProcessor`` and wires
        ``set_quality_callback``.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod, audio_processor as _ap_mod

        mock_ap_instance = MagicMock(name="MockAudioProcessorInstance")
        mock_ap_cls = MagicMock(name="MockAudioProcessor", return_value=mock_ap_instance)
        monkeypatch.setattr(_ap_mod, "AudioProcessor", mock_ap_cls)

        instance = _app_mod.VoiceTyperApp()

        # AudioProcessor NOT constructed yet.
        assert mock_ap_cls.call_count == 0

        # First attribute access triggers construction.
        _ = instance._audio_processor.filter_names

        # AudioProcessor(config, sample_rate=...) was called once.
        assert mock_ap_cls.call_count == 1, (
            "First attribute access on the _LazyAudioProcessorProxy must construct a real AudioProcessor."
        )
        # set_quality_callback was wired on the constructed instance.
        mock_ap_instance.set_quality_callback.assert_called_once_with(instance._on_audio_quality_chunk)

    def test_audio_processor_setter_bypasses_proxy(self, tmp_config_dir, monkeypatch):
        """(c): assigning via the setter stores directly into the
        backing — a subsequent getter call returns the assigned value
        without invoking the proxy. This is the contract tests rely on
        when they inject mocks via ``app._audio_processor =
        MagicMock()``.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod, audio_processor as _ap_mod

        mock_ap_cls = MagicMock(name="MockAudioProcessor")
        monkeypatch.setattr(_ap_mod, "AudioProcessor", mock_ap_cls)

        instance = _app_mod.VoiceTyperApp()

        sentinel = MagicMock(name="fake_audio_processor")
        instance._audio_processor = sentinel

        # Setter must store into backing; getter must return the
        # sentinel (no proxy, no construction).
        assert instance._audio_processor is sentinel, (
            "Setter for _audio_processor must store into the backing; "
            "getter must return the assigned sentinel (no proxy)."
        )
        assert instance._audio_processor_backing is sentinel
        # AudioProcessor() must NOT have been called (the setter
        # bypasses the proxy entirely).
        assert mock_ap_cls.call_count == 0, (
            "Setter must bypass the proxy — AudioProcessor() was called "
            "even though a sentinel was assigned via the setter."
        )

    def test_audio_processor_proxy_caches_real_instance(self, tmp_config_dir, monkeypatch):
        """The proxy caches the real ``AudioProcessor`` after first
        construction — subsequent attribute accesses reuse the cached
        instance (no re-construction).
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod, audio_processor as _ap_mod

        mock_ap_instance = MagicMock(name="MockAudioProcessorInstance")
        mock_ap_cls = MagicMock(name="MockAudioProcessor", return_value=mock_ap_instance)
        monkeypatch.setattr(_ap_mod, "AudioProcessor", mock_ap_cls)

        instance = _app_mod.VoiceTyperApp()

        # First attribute access constructs.
        _ = instance._audio_processor.filter_names
        assert mock_ap_cls.call_count == 1

        # Second attribute access reuses the cached instance.
        _ = instance._audio_processor.sample_rate
        assert mock_ap_cls.call_count == 1, (
            "Subsequent attribute accesses must reuse the cached AudioProcessor — no re-construction."
        )

    def test_audio_processor_proxy_forwards_attribute_access(self, tmp_config_dir, monkeypatch):
        """Attribute access on the proxy is forwarded to the real
        ``AudioProcessor``.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod, audio_processor as _ap_mod

        mock_ap_instance = MagicMock(name="MockAudioProcessorInstance")
        mock_ap_instance.filter_names = ["highpass", "gate"]
        mock_ap_instance.sample_rate = 16000
        mock_ap_cls = MagicMock(name="MockAudioProcessor", return_value=mock_ap_instance)
        monkeypatch.setattr(_ap_mod, "AudioProcessor", mock_ap_cls)

        instance = _app_mod.VoiceTyperApp()

        # Forwarded attribute access.
        assert instance._audio_processor.filter_names == ["highpass", "gate"]
        assert instance._audio_processor.sample_rate == 16000

        # Method calls are forwarded too.
        instance._audio_processor.reset()
        mock_ap_instance.reset.assert_called_once_with()


# ─── deferred imports inside lazy getters ──────────────────────


class TestDeferredImportsInLazyGetters:
    """the module-top imports for ``AudioProcessor``,
    ``DuckCrashRecovery``, ``VolumeDucker``, and ``WaveformBubble``
    were moved INTO the lazy getters (or the proxy's ``_resolve``
    method). Verified by checking that the symbols are NOT attributes
    of the ``voice_typer.server.app`` module (they were removed from
    the module-top imports).
    """

    def test_audio_processor_not_at_module_top(self):
        """``AudioProcessor`` is NOT a module-top attribute of
        ``voice_typer.server.app`` — it's imported inside the
        ``_LazyAudioProcessorProxy._resolve`` method.
        """
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "AudioProcessor"), (
            "AudioProcessor should NOT be a module-top attribute — "
            "it should be imported inside the _LazyAudioProcessorProxy._resolve "
            "method to defer the audio_filters → scipy import chain."
        )

    def test_duck_crash_recovery_not_at_module_top(self):
        """``DuckCrashRecovery`` is NOT a module-top attribute of
        ``voice_typer.server.app`` — it's imported inside the
        ``_duck_crash_recovery`` getter.
        """
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "DuckCrashRecovery"), (
            "DuckCrashRecovery should NOT be a module-top attribute — "
            "it should be imported inside the _duck_crash_recovery getter."
        )

    def test_volume_ducker_not_at_module_top(self):
        """``VolumeDucker`` is NOT a module-top attribute of
        ``voice_typer.server.app`` — it's imported inside the
        ``_volume_ducker`` getter.
        """
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "VolumeDucker"), (
            "VolumeDucker should NOT be a module-top attribute — "
            "it should be imported inside the _volume_ducker getter."
        )

    def test_waveform_bubble_not_at_module_top(self):
        """``WaveformBubble`` is NOT a module-top attribute of
        ``voice_typer.server.app`` — it's imported inside the
        ``_waveform_bubble`` getter.
        """
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "WaveformBubble"), (
            "WaveformBubble should NOT be a module-top attribute — "
            "it should be imported inside the _waveform_bubble getter."
        )


# ─── deferred recorder / recording construction (STARTUP-9) ──────


class TestRecorderDeferredConstruction:
    """``Recorder`` + ``RecordingController`` must NOT be constructed
    synchronously in ``VoiceTyperApp.__init__``.

    STARTUP-9: the ``voice_typer.server.recording`` import + ``Recorder()``
    build eagerly loads numpy/scipy/sounddevice (PortAudio) and can take
    1-8s on the main thread (measured ~5x slower under the system Python
    the packaged app runs on). Construction is deferred to a background
    thread registered with the ThreadRegistry; ``app.recorder`` /
    ``app.recording`` are lazy properties that block only briefly on
    first access.

    These tests pin the contract deterministically by installing FAKE
    ``voice_typer.server.recording`` / ``recording_controller`` modules
    into ``sys.modules`` (hermetic — no numpy/audio imports at all). The
    fake ``Recorder.__init__`` is gated on a ``threading.Event``, so the
    test can observe the sentinel state while the build is provably
    still in flight on a background thread.
    """

    @staticmethod
    def _install_fake_recording_modules(monkeypatch, recorder_cls, controller_cls):
        """Install fake ``voice_typer.server.recording`` /
        ``recording_controller`` modules into ``sys.modules`` so the
        background build thread's deferred imports (``from
        voice_typer.server.recording import Recorder`` / ``from
        voice_typer.server.recording_controller import
        RecordingController``) resolve to the fakes — the test never
        imports numpy/audio and never constructs real subsystems.
        """
        fake_recording = types.ModuleType("voice_typer.server.recording")
        fake_controller = types.ModuleType("voice_typer.server.recording_controller")
        # ``setattr`` instead of attribute assignment so pyrefly (which
        # rejects unknown attributes on ``ModuleType``) accepts the fakes.
        fake_recording.Recorder = recorder_cls
        fake_controller.RecordingController = controller_cls
        monkeypatch.setitem(sys.modules, "voice_typer.server.recording", fake_recording)
        monkeypatch.setitem(
            sys.modules,
            "voice_typer.server.recording_controller",
            fake_controller,
        )

    def test_recorder_backing_is_sentinel_after_init_and_accessible_after_build(self, tmp_config_dir, monkeypatch):
        """Right after ``__init__``, ``_recorder_backing`` is still the
        ``_RECORDER_MISSING`` sentinel and ``_recorder_build_ready`` is
        NOT set — the recorder was NOT built synchronously. The fake
        ``Recorder.__init__`` blocks on an event, so the test can prove
        the construction is proceeding on a background thread while the
        sentinel state is observable; once released, ``app.recorder`` /
        ``app.recording`` return the built instances.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod

        entered = threading.Event()  # set once the background build enters Recorder.__init__
        release = threading.Event()  # the test sets this to let the build finish
        built: list = []

        class _BlockingRecorder:
            def __init__(self, config, audio_processor=None, thread_registry=None):
                built.append(self)
                entered.set()
                release.wait(10)

        controller_instance = MagicMock(name="controller_instance")
        controller_cls = MagicMock(
            name="MockRecordingController",
            return_value=controller_instance,
        )
        self._install_fake_recording_modules(monkeypatch, _BlockingRecorder, controller_cls)

        try:
            instance = _app_mod.VoiceTyperApp()

            # The recorder must NOT have been built synchronously in __init__.
            assert instance._recorder_backing is _app_mod._RECORDER_MISSING, (
                "Recorder must NOT be constructed in __init__; _recorder_backing "
                "should still be the _RECORDER_MISSING sentinel."
            )
            assert not instance._recorder_build_ready.is_set(), (
                "The background recorder build must not have completed during __init__."
            )

            # Prove the build IS proceeding — on a background thread, not the main one.
            assert entered.wait(5), "background recorder build thread never started"

            # While the build is in flight the sentinel must hold (no eager construction).
            assert instance._recorder_backing is _app_mod._RECORDER_MISSING

            # Release the gate: the build completes and the properties work.
            release.set()
            assert instance._recorder_build_ready.wait(5), "background recorder build did not complete after release"
            assert instance.recorder is built[0], "app.recorder must return the recorder built by the background thread"
            assert instance.recorder is built[0], "app.recorder must cache (same instance)"
            assert instance.recording is controller_instance, (
                "app.recording must return the controller built by the background thread"
            )
            assert instance.recording is controller_instance, "app.recording must cache"
        finally:
            release.set()

    def test_recorder_setter_short_circuits_background_build(self, tmp_config_dir, monkeypatch):
        """If a test (or caller) injects ``app.recorder = MagicMock()``
        via the setter while the background build is in flight, the build
        must NOT clobber the injected value, and ``app.recording`` still
        works (falling back to an on-demand ``RecordingController``).
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod

        entered = threading.Event()
        release = threading.Event()

        class _BlockingRecorder:
            def __init__(self, config, audio_processor=None, thread_registry=None):
                entered.set()
                release.wait(10)

        controller_instance = MagicMock(name="controller_instance")
        controller_cls = MagicMock(
            name="MockRecordingController",
            return_value=controller_instance,
        )
        self._install_fake_recording_modules(monkeypatch, _BlockingRecorder, controller_cls)

        try:
            instance = _app_mod.VoiceTyperApp()
            assert entered.wait(5), "background recorder build thread never started"

            injected = MagicMock(name="injected_recorder")
            instance.recorder = injected  # setter while the build is in flight
            release.set()
            assert instance._recorder_build_ready.wait(5), "background recorder build did not complete after release"

            # The background build must not clobber the injected mock.
            assert instance.recorder is injected, (
                "the background build must not clobber a recorder injected via the setter"
            )
            # app.recording still works (on-demand controller fallback).
            assert instance.recording is controller_instance, (
                "app.recording must fall back to an on-demand RecordingController "
                "when the background build was short-circuited"
            )
        finally:
            release.set()
