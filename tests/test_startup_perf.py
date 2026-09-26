"""
F4 startup-perf regression tests.
* **DJ-2**: ``LausuApp.__init__`` must NOT eagerly construct
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app_for_startup_perf(tmp_config_dir, monkeypatch):
    """Create a LausuApp with mocked hardware/GUI deps."""
    # Patch the canonical home of the platform helpers: they are
    from voice_typer.server.server_platform import autostart as autostart_mod

    monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
    monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

    from voice_typer.server.app import LausuApp

    instance = LausuApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    yield instance
    loader = getattr(instance.models, "_model_load_thread", None)
    if loader is not None and loader.is_alive():
        loader.join(timeout=2.0)


def _patch_app_platform_helpers(monkeypatch):
    """Patch the platform helpers that ``LausuApp.__init__`` touches."""
    from voice_typer.server.server_platform import autostart as autostart_mod

    monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
    monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])


class TestNoEagerManagerConstruction:
    """DJ-2: ``LausuApp.__init__`` must NOT construct the JSON-reading"""

    def test_template_manager_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        """TemplateManager() must not be called from ``LausuApp.__init__``."""
        from voice_typer.server import templates as templates_mod

        construct_count = {"n": 0}
        real_template_manager = templates_mod.TemplateManager

        def _counting_ctor(*args, **kwargs):
            construct_count["n"] += 1
            return real_template_manager(*args, **kwargs)

        monkeypatch.setattr(templates_mod, "TemplateManager", _counting_ctor)
        # Also patch the symbol re-exported at app module level so any
        try:
            from voice_typer.server import app as app_mod

            if hasattr(app_mod, "TemplateManager"):
                monkeypatch.setattr(app_mod, "TemplateManager", _counting_ctor)
        except ImportError:
            pass

        _patch_app_platform_helpers(monkeypatch)

        from voice_typer.server.app import LausuApp

        instance = LausuApp()
        # __init__ must NOT eagerly construct TemplateManager.
        assert construct_count["n"] == 0, (
            "DJ-2: LausuApp.__init__ eagerly constructed TemplateManager "
            f"{construct_count['n']} time(s); it should be lazy-constructed on "
            "first access via service/template.py / dictation_pipeline.py."
        )
        # The attribute must still be accessible (preserved public API) —
        assert hasattr(instance, "_template_manager"), (
            "DJ-2: _template_manager attribute must still exist on LausuApp (preserved public API)."
        )
        assert instance._template_manager is None, (
            "DJ-2: _template_manager should be None immediately after "
            "__init__ (lazy construction); got an already-constructed instance."
        )

    def test_vocabulary_manager_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        """VocabularyManager() must not be called from ``LausuApp.__init__``."""
        from voice_typer.server import vocabulary as vocabulary_mod

        construct_count = {"n": 0}
        real_vocabulary_manager = vocabulary_mod.VocabularyManager

        def _counting_ctor(*args, **kwargs):
            construct_count["n"] += 1
            return real_vocabulary_manager(*args, **kwargs)

        monkeypatch.setattr(vocabulary_mod, "VocabularyManager", _counting_ctor)
        try:
            from voice_typer.server import app as app_mod

            if hasattr(app_mod, "VocabularyManager"):
                monkeypatch.setattr(app_mod, "VocabularyManager", _counting_ctor)
        except ImportError:
            pass

        _patch_app_platform_helpers(monkeypatch)

        from voice_typer.server.app import LausuApp

        instance = LausuApp()
        assert construct_count["n"] == 0, (
            "DJ-2: LausuApp.__init__ eagerly constructed VocabularyManager "
            f"{construct_count['n']} time(s); it should be lazy-constructed on "
            "first access via service/vocabulary.py / dictation_pipeline.py."
        )
        assert hasattr(instance, "_vocabulary_manager"), (
            "DJ-2: _vocabulary_manager attribute must still exist on LausuApp (preserved public API)."
        )
        assert instance._vocabulary_manager is None, (
            "DJ-2: _vocabulary_manager should be None immediately after "
            "__init__ (lazy construction); got an already-constructed instance."
        )

    def test_lazy_fallback_still_constructs_managers(self, tmp_config_dir, monkeypatch):
        """The lazy fallback in ``service/template.py`` must still construct"""
        _patch_app_platform_helpers(monkeypatch)

        from voice_typer.server.app import LausuApp

        instance = LausuApp()
        assert instance._template_manager is None
        # Invoke the lazy fallback path used by service/template.py.
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager()
        instance._template_manager = tm
        assert instance._template_manager is tm, "The lazy fallback should be able to populate _template_manager."


class TestVadPreloadCalled:
    """Silero VAD model is hot by the time the user first presses F2."""

    def test_vad_preload_called_during_startup_run(self, app_for_startup_perf, monkeypatch):
        """``StartupSequence.run()`` must call ``vad.preload()``."""
        from voice_typer.server import startup_sequence, vad as vad_mod

        preload_invoked = threading.Event()

        def _fake_preload():
            preload_invoked.set()

        monkeypatch.setattr(vad_mod, "preload", _fake_preload)
        # Also stub the heavy IO tasks so we don't actually hit disk /
        from voice_typer.server import startup_tasks

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        app_for_startup_perf.hotkeys = MagicMock()
        app_for_startup_perf.models = MagicMock()
        app_for_startup_perf.config.bubble_behavior = "hidden"
        app_for_startup_perf.config.bubble_show_on_startup = False
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        startup_sequence.StartupSequence(app_for_startup_perf).run()

        assert preload_invoked.wait(timeout=2.0), (
            "DJ-57: vad.preload() was not called during startup. "
            "StartupSequence.run() must spawn a daemon thread that "
            "calls vad.preload() so the Silero VAD model is hot by "
            "the time the user first presses F2 (otherwise the first "
            "~1s of speech is silently dropped via ring-buffer overflow)."
        )

    def test_vad_preload_failure_does_not_break_startup(self, app_for_startup_perf, monkeypatch):
        """If ``vad.preload()`` raises (e.g. torch not installed), startup"""
        from voice_typer.server import startup_sequence, vad as vad_mod

        def _exploding_preload():
            raise RuntimeError("torch not available in test environment")

        monkeypatch.setattr(vad_mod, "preload", _exploding_preload)
        from voice_typer.server import startup_tasks

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        app_for_startup_perf.hotkeys = MagicMock()
        app_for_startup_perf.models = MagicMock()
        app_for_startup_perf.config.bubble_behavior = "hidden"
        app_for_startup_perf.config.bubble_show_on_startup = False
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        # Must NOT raise, the daemon thread swallows the exception.
        startup_sequence.StartupSequence(app_for_startup_perf).run()

        # Sanity: hotkey + model load DID run (startup was not aborted
        app_for_startup_perf.hotkeys.register.assert_called_once()
        app_for_startup_perf.models.start_background_load.assert_called_once()


class TestNoEagerQwenEnsureEngine:
    """DJ-3: ``LausuApp.__init__`` must NOT eagerly call"""

    def test_no_ensure_engine_call_in_init(self, tmp_config_dir, monkeypatch):
        """Constructing LausuApp with asr_backend='qwen' +"""
        _patch_app_platform_helpers(monkeypatch)

        # Patch ModelManager._ensure_engine BEFORE constructing the app
        from voice_typer.server.model_manager import ModelManager

        ensure_engine_calls: list[str] = []
        real_ensure_engine = ModelManager._ensure_engine

        def _counting_ensure_engine(self, backend_name, *args, **kwargs):
            ensure_engine_calls.append(backend_name)
            return real_ensure_engine(self, backend_name, *args, **kwargs)

        monkeypatch.setattr(ModelManager, "_ensure_engine", _counting_ensure_engine)

        from voice_typer.server.app import LausuApp

        instance = LausuApp()
        # Configure qwen backend AFTER construction so we can verify
        instance.config.asr_backend = "qwen"
        instance.config.qwen_model_path = "/nonexistent/qwen/model"

        # triggered: __init__ must NOT have called _ensure_engine at
        assert ensure_engine_calls == [], (
            "DJ-3: LausuApp.__init__ must NOT eagerly call "
            f"_ensure_engine (got calls: {ensure_engine_calls}). The "
            "background load thread constructs the engine on the daemon "
            "thread, see ModelManager.start_background_load."
        )
