"""regression tests: lazy ``history_db`` and ``_audio_processor``"""

from __future__ import annotations

import sys
import threading
import types
from unittest.mock import MagicMock

import pytest


def _patch_app_platform_helpers(monkeypatch):
    """Patch the platform helpers that ``VoiceTyperApp.__init__`` touches."""
    from voice_typer.server.server_platform import autostart as autostart_mod

    monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
    monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])


@pytest.fixture(autouse=True)
def _mock_scipy(monkeypatch):
    """Mock scipy so ``audio_filters.highpass`` / ``audio_pipeline``"""
    mock_scipy = MagicMock(name="mock_scipy")
    mock_scipy_signal = MagicMock(name="mock_scipy.signal")
    mock_scipy.signal = mock_scipy_signal
    monkeypatch.setitem(sys.modules, "scipy", mock_scipy)
    monkeypatch.setitem(sys.modules, "scipy.signal", mock_scipy_signal)


class TestHistoryDbLazyConstruction:
    """``HistoryDB()`` must NOT be constructed in ``__init__``."""

    def test_history_db_backing_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """``_history_db_backing`` must be ``None`` after ``__init__`` —"""
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._history_db_backing is None, (
            "HistoryDB must NOT be eagerly constructed in __init__; _history_db_backing should start as None."
        )

    def test_history_db_not_constructed_at_init_via_mock(self, tmp_config_dir, monkeypatch):
        """(a): ``HistoryDB()`` is NOT called during"""
        _patch_app_platform_helpers(monkeypatch)
        # Patch HistoryDB on the app module BEFORE constructing
        from voice_typer.server import app as _app_mod

        mock_history_db_cls = MagicMock(name="MockHistoryDB")
        monkeypatch.setattr(_app_mod, "HistoryDB", mock_history_db_cls)

        instance = _app_mod.VoiceTyperApp()

        # HistoryDB() must NOT have been called during __init__.
        assert mock_history_db_cls.call_count == 0, (
            "HistoryDB() was called during __init__, the lazy property should defer construction to first access."
        )
        assert instance._history_db_backing is None

    def test_history_db_constructed_on_first_access(self, tmp_config_dir, monkeypatch):
        """``HistoryDB`` and caches it in ``_history_db_backing``."""
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
        """backing, a subsequent getter call returns the assigned value"""
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod

        mock_history_db_cls = MagicMock(name="MockHistoryDB")
        monkeypatch.setattr(_app_mod, "HistoryDB", mock_history_db_cls)

        instance = _app_mod.VoiceTyperApp()

        sentinel = MagicMock(name="fake_history_db")
        instance.history_db = sentinel

        assert instance.history_db is sentinel, (
            "Setter for history_db must store into the backing; getter "
            "must return the assigned sentinel (no construction)."
        )
        assert instance._history_db_backing is sentinel
        # HistoryDB() must NOT have been called (the setter bypasses
        assert mock_history_db_cls.call_count == 0, (
            "Setter must bypass lazy construction, HistoryDB() was "
            "called even though a sentinel was assigned via the setter."
        )

    def test_history_db_returns_none_during_shutdown(self, tmp_config_dir, monkeypatch):
        """when ``_shutting_down_event`` is set, the lazy getter"""
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod

        mock_history_db_cls = MagicMock(name="MockHistoryDB")
        monkeypatch.setattr(_app_mod, "HistoryDB", mock_history_db_cls)

        instance = _app_mod.VoiceTyperApp()
        # Simulate shutdown, quit() / restart_app() sets this before
        instance._shutting_down_event.set()

        # Accessing history_db during shutdown must return None
        assert instance.history_db is None, (
            "history_db getter must return None when _shutting_down_event "
            "is set, prevents lazy construction during shutdown teardown."
        )
        assert mock_history_db_cls.call_count == 0, "HistoryDB() must NOT be called when _shutting_down_event is set."


class TestAudioProcessorLazyConstruction:
    """first attribute access."""

    def test_audio_processor_backing_is_proxy_after_init(self, tmp_config_dir, monkeypatch):
        """``_LazyAudioProcessorProxy`` (NOT a real ``AudioProcessor``)."""
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp, _LazyAudioProcessorProxy

        instance = VoiceTyperApp()
        # STARTUP-9: ``Recorder(...)`` (whose ``audio_processor=`` argument
        assert instance._recorder_build_ready.wait(10.0), "recorder background build did not finish within 10s"
        if instance._recorder_build_error is not None:
            raise instance._recorder_build_error
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
            "real AudioProcessor during __init__, _real should be None."
        )

    def test_audio_processor_not_constructed_at_init_via_mock(self, tmp_config_dir, monkeypatch):
        """(a): ``AudioProcessor(...)`` is NOT called during"""
        _patch_app_platform_helpers(monkeypatch)
        # Patch AudioProcessor inside the proxy's _resolve method.
        from voice_typer.server import app as _app_mod, audio_processor as _ap_mod

        mock_ap_cls = MagicMock(name="MockAudioProcessor")
        monkeypatch.setattr(_ap_mod, "AudioProcessor", mock_ap_cls)

        _app_mod.VoiceTyperApp()

        # AudioProcessor(...) must NOT have been called during __init__.
        assert mock_ap_cls.call_count == 0, (
            "AudioProcessor() was called during __init__, "
            "the lazy proxy should defer construction to first attribute "
            "access."
        )

    def test_audio_processor_constructed_on_first_attribute_access(self, tmp_config_dir, monkeypatch):
        """(b): first attribute access on the proxy triggers"""
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
        mock_ap_instance.set_quality_callback.assert_called_once_with(instance._on_audio_quality_chunk)

    def test_audio_processor_setter_bypasses_proxy(self, tmp_config_dir, monkeypatch):
        """backing, a subsequent getter call returns the assigned value"""
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app as _app_mod, audio_processor as _ap_mod

        mock_ap_cls = MagicMock(name="MockAudioProcessor")
        monkeypatch.setattr(_ap_mod, "AudioProcessor", mock_ap_cls)

        instance = _app_mod.VoiceTyperApp()

        sentinel = MagicMock(name="fake_audio_processor")
        instance._audio_processor = sentinel

        assert instance._audio_processor is sentinel, (
            "Setter for _audio_processor must store into the backing; "
            "getter must return the assigned sentinel (no proxy)."
        )
        assert instance._audio_processor_backing is sentinel
        # AudioProcessor() must NOT have been called (the setter
        assert mock_ap_cls.call_count == 0, (
            "Setter must bypass the proxy, AudioProcessor() was called "
            "even though a sentinel was assigned via the setter."
        )

    def test_audio_processor_proxy_caches_real_instance(self, tmp_config_dir, monkeypatch):
        """The proxy caches the real ``AudioProcessor`` after first"""
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
            "Subsequent attribute accesses must reuse the cached AudioProcessor, no re-construction."
        )

    def test_audio_processor_proxy_forwards_attribute_access(self, tmp_config_dir, monkeypatch):
        """Attribute access on the proxy is forwarded to the real"""
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


class TestDeferredImportsInLazyGetters:
    """the module-top imports for ``AudioProcessor``,"""

    def test_audio_processor_not_at_module_top(self):
        """``_LazyAudioProcessorProxy._resolve`` method."""
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "AudioProcessor"), (
            "AudioProcessor should NOT be a module-top attribute, "
            "it should be imported inside the _LazyAudioProcessorProxy._resolve "
            "method to defer the audio_filters → scipy import chain."
        )

    def test_duck_crash_recovery_not_at_module_top(self):
        """``_duck_crash_recovery`` getter."""
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "DuckCrashRecovery"), (
            "DuckCrashRecovery should NOT be a module-top attribute, "
            "it should be imported inside the _duck_crash_recovery getter."
        )

    def test_volume_ducker_not_at_module_top(self):
        """``_volume_ducker`` getter."""
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "VolumeDucker"), (
            "VolumeDucker should NOT be a module-top attribute, it should be imported inside the _volume_ducker getter."
        )

    def test_waveform_bubble_not_at_module_top(self):
        """``_waveform_bubble`` getter."""
        from voice_typer.server import app as _app_mod

        assert not hasattr(_app_mod, "WaveformBubble"), (
            "WaveformBubble should NOT be a module-top attribute, "
            "it should be imported inside the _waveform_bubble getter."
        )


class TestRecorderDeferredConstruction:
    """``Recorder`` + ``RecordingController`` must NOT be constructed"""

    @staticmethod
    def _install_fake_recording_modules(monkeypatch, recorder_cls, controller_cls):
        """Install fake ``voice_typer.server.recording`` /"""
        fake_recording = types.ModuleType("voice_typer.server.recording")
        fake_controller = types.ModuleType("voice_typer.server.recording_controller")
        fake_recording.Recorder = recorder_cls
        fake_controller.RecordingController = controller_cls
        monkeypatch.setitem(sys.modules, "voice_typer.server.recording", fake_recording)
        monkeypatch.setitem(
            sys.modules,
            "voice_typer.server.recording_controller",
            fake_controller,
        )

    def test_recorder_backing_is_sentinel_after_init_and_accessible_after_build(self, tmp_config_dir, monkeypatch):
        """``_RECORDER_MISSING`` sentinel and ``_recorder_build_ready`` is"""
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

            # Prove the build IS proceeding, on a background thread, not the main one.
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
        """If a test (or caller) injects ``app.recorder = MagicMock()``"""
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
            assert instance.recording is controller_instance, (
                "app.recording must fall back to an on-demand RecordingController "
                "when the background build was short-circuited"
            )
        finally:
            release.set()
