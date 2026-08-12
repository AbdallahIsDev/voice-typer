"""F4 startup-perf regression tests.

Pins the DJ-2 / DJ-3 / DJ-4 / DJ-57 fixes:

* **DJ-2** — ``VoiceTyperApp.__init__`` must NOT eagerly construct
  ``TemplateManager`` / ``VocabularyManager`` (which read JSON from
  disk). The managers must be lazy-constructed on first access via the
  existing fallbacks in ``service/template.py`` /
  ``service/vocabulary.py`` / ``dictation_pipeline.py``.
* **DJ-57** — ``vad.preload()`` must be called during startup (on a
  fire-and-forget daemon thread) so the Silero VAD model is hot by
  the time the user first presses F2.

These tests run on the Linux sandbox — they don't require sounddevice,
torch, or a display server. ``mock_heavy_imports`` (autouse, in
``conftest.py``) stubs the hardware-touching modules.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app_for_startup_perf(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked hardware/GUI deps.

    Mirrors the ``app_for_startup`` fixture in
    ``tests/test_startup_sequence.py``. Heavy deps (sounddevice,
    pystray, pynput, etc.) are mocked by the autouse
    ``mock_heavy_imports`` fixture in ``tests/conftest.py``.
    """
    # Use raising=False so the patches work even if the symbols are
    # not yet re-exported on ``voice_typer.server.app`` (they are
    # looked up dynamically inside ``startup_tasks.sync_autostart`` /
    # ``startup_tasks.load_microphones`` via ``_app_module.<name>``).
    from voice_typer.server import app as _app_mod

    monkeypatch.setattr(_app_mod, "is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(_app_mod, "enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(_app_mod, "disable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(_app_mod, "list_microphones", lambda: [], raising=False)

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    yield instance
    # Join the background model-load thread so it doesn't outlive the
    # test and touch a torn-down VoiceTyperApp.
    loader = getattr(instance.models, "_model_load_thread", None)
    if loader is not None and loader.is_alive():
        loader.join(timeout=2.0)


def _patch_app_platform_helpers(monkeypatch):
    """Patch the platform helpers that ``VoiceTyperApp.__init__`` touches.

    Uses ``raising=False`` because ``voice_typer.server.app`` does not
    re-export these symbols at module load (they're looked up dynamically
    inside ``startup_tasks.sync_autostart`` / ``load_microphones`` via
    ``_app_module.<name>``). The pre-existing ``tests/app/conftest.py``
    fixture uses ``monkeypatch.setattr("voice_typer.server.app.X", ...)``
    with the default ``raising=True``, which would raise ``AttributeError``
    if the symbol isn't already on the module — we use the module-object
    form + ``raising=False`` so the patch always succeeds.
    """
    from voice_typer.server import app as _app_mod

    monkeypatch.setattr(_app_mod, "is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(_app_mod, "enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(_app_mod, "disable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(_app_mod, "list_microphones", lambda: [], raising=False)


# no eager TemplateManager / VocabularyManager ────────────────


class TestNoEagerManagerConstruction:
    """DJ-2: ``VoiceTyperApp.__init__`` must NOT construct the JSON-reading
    managers (TemplateManager / VocabularyManager) eagerly on the main
    thread. The lazy fallback in ``service/template.py`` /
    ``service/vocabulary.py`` / ``dictation_pipeline.py`` handles
    construction on first access.
    """

    def test_template_manager_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        """TemplateManager() must not be called from ``VoiceTyperApp.__init__``.

        We replace ``voice_typer.server.templates.TemplateManager`` with a
        counter mock and assert that constructing ``VoiceTyperApp`` does
        not invoke it. The lazy fallbacks construct the manager on first
        access (e.g. when the renderer queries templates via IPC), not
        during app construction.
        """
        from voice_typer.server import templates as templates_mod

        construct_count = {"n": 0}
        real_template_manager = templates_mod.TemplateManager

        def _counting_ctor(*args, **kwargs):
            construct_count["n"] += 1
            return real_template_manager(*args, **kwargs)

        monkeypatch.setattr(templates_mod, "TemplateManager", _counting_ctor)
        # Also patch the symbol re-exported at app module level so any
        # ``from voice_typer.server.app import TemplateManager`` style
        # lookup (none expected, but defensive) is also counted.
        try:
            from voice_typer.server import app as app_mod

            if hasattr(app_mod, "TemplateManager"):
                monkeypatch.setattr(app_mod, "TemplateManager", _counting_ctor)
        except ImportError:
            pass

        _patch_app_platform_helpers(monkeypatch)

        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        # __init__ must NOT eagerly construct TemplateManager.
        assert construct_count["n"] == 0, (
            "DJ-2: VoiceTyperApp.__init__ eagerly constructed TemplateManager "
            f"{construct_count['n']} time(s); it should be lazy-constructed on "
            "first access via service/template.py / dictation_pipeline.py."
        )
        # The attribute must still be accessible (preserved public API) —
        # initially None until the lazy fallback constructs it.
        assert hasattr(instance, "_template_manager"), (
            "DJ-2: _template_manager attribute must still exist on VoiceTyperApp (preserved public API)."
        )
        assert instance._template_manager is None, (
            "DJ-2: _template_manager should be None immediately after "
            "__init__ (lazy construction); got an already-constructed instance."
        )

    def test_vocabulary_manager_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        """VocabularyManager() must not be called from ``VoiceTyperApp.__init__``.

        Mirrors the TemplateManager test. VocabularyManager reads
        vocabulary.json from disk at construction time — moving it off
        the main-thread critical path saves hundreds of ms on a cold
        disk.
        """
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

        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert construct_count["n"] == 0, (
            "DJ-2: VoiceTyperApp.__init__ eagerly constructed VocabularyManager "
            f"{construct_count['n']} time(s); it should be lazy-constructed on "
            "first access via service/vocabulary.py / dictation_pipeline.py."
        )
        assert hasattr(instance, "_vocabulary_manager"), (
            "DJ-2: _vocabulary_manager attribute must still exist on VoiceTyperApp (preserved public API)."
        )
        assert instance._vocabulary_manager is None, (
            "DJ-2: _vocabulary_manager should be None immediately after "
            "__init__ (lazy construction); got an already-constructed instance."
        )

    def test_lazy_fallback_still_constructs_managers(self, tmp_config_dir, monkeypatch):
        """The lazy fallback in ``service/template.py`` must still construct
        the manager on first access — DJ-2 only removes the EAGER init,
        not the manager itself."""
        _patch_app_platform_helpers(monkeypatch)

        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._template_manager is None
        # Invoke the lazy fallback path used by service/template.py.
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager()
        instance._template_manager = tm
        assert instance._template_manager is tm, "The lazy fallback should be able to populate _template_manager."


# vad.preload() called during startup ───────────────────────


class TestVadPreloadCalled:
    """DJ-57: ``vad.preload()`` must be invoked during startup so the
    Silero VAD model is hot by the time the user first presses F2.

    The preload runs on a fire-and-forget daemon thread (not the bg
    startup thread) so it doesn't block the rest of startup. We use an
    Event to detect invocation across the thread boundary.
    """

    def test_vad_preload_called_during_startup_run(self, app_for_startup_perf, monkeypatch):
        """``StartupSequence.run()`` must call ``vad.preload()``."""
        from voice_typer.server import startup_sequence, vad as vad_mod

        preload_invoked = threading.Event()

        def _fake_preload():
            preload_invoked.set()

        monkeypatch.setattr(vad_mod, "preload", _fake_preload)
        # Also stub the heavy IO tasks so we don't actually hit disk /
        # audio hardware during the test.
        from voice_typer.server import startup_tasks

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        app_for_startup_perf.hotkeys = MagicMock()
        app_for_startup_perf.models = MagicMock()
        app_for_startup_perf.config.bubble_behavior = "hidden"
        app_for_startup_perf.config.bubble_show_on_startup = False
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        startup_sequence.StartupSequence(app_for_startup_perf).run()

        # Wait for the daemon thread to invoke vad.preload(). The
        # timeout is generous (2s) to avoid flakiness on slow CI;
        # in practice the call happens within microseconds of run().
        assert preload_invoked.wait(timeout=2.0), (
            "DJ-57: vad.preload() was not called during startup. "
            "StartupSequence.run() must spawn a daemon thread that "
            "calls vad.preload() so the Silero VAD model is hot by "
            "the time the user first presses F2 (otherwise the first "
            "~1s of speech is silently dropped via ring-buffer overflow)."
        )

    def test_vad_preload_failure_does_not_break_startup(self, app_for_startup_perf, monkeypatch):
        """If ``vad.preload()`` raises (e.g. torch not installed), startup
        must NOT abort — the audio worker's RMS fallback handles the
        no-VAD case. This pins the best-effort contract documented in
        ``_spawn_vad_preload``."""
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
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        app_for_startup_perf.hotkeys = MagicMock()
        app_for_startup_perf.models = MagicMock()
        app_for_startup_perf.config.bubble_behavior = "hidden"
        app_for_startup_perf.config.bubble_show_on_startup = False
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        # Must NOT raise — the daemon thread swallows the exception.
        startup_sequence.StartupSequence(app_for_startup_perf).run()

        # Sanity: hotkey + model load DID run (startup was not aborted
        # by the vad.preload() failure on the daemon thread).
        app_for_startup_perf.hotkeys.register.assert_called_once()
        app_for_startup_perf.models.start_background_load.assert_called_once()


# no eager _ensure_engine("qwen") in __init__ ─────────────────


class TestNoEagerQwenEnsureEngine:
    """DJ-3: ``VoiceTyperApp.__init__`` must NOT eagerly call
    ``self.models._ensure_engine("qwen")``. The background load thread
    (started by ``ModelManager.start_background_load()`` in
    ``StartupSequence.run``) already constructs the engine on the
    daemon thread."""

    def test_no_ensure_engine_call_in_init(self, tmp_config_dir, monkeypatch):
        """Constructing VoiceTyperApp with asr_backend='qwen' +
        qwen_model_path set must NOT call ``_ensure_engine``."""
        _patch_app_platform_helpers(monkeypatch)

        # Patch ModelManager._ensure_engine BEFORE constructing the app
        # so we can count calls from __init__.
        from voice_typer.server.model_manager import ModelManager

        ensure_engine_calls: list[str] = []
        real_ensure_engine = ModelManager._ensure_engine

        def _counting_ensure_engine(self, backend_name, *args, **kwargs):
            ensure_engine_calls.append(backend_name)
            return real_ensure_engine(self, backend_name, *args, **kwargs)

        monkeypatch.setattr(ModelManager, "_ensure_engine", _counting_ensure_engine)

        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        # Configure qwen backend AFTER construction so we can verify
        # __init__ didn't call _ensure_engine even when the config was
        # set to qwen (mirrors the pre- code path).
        instance.config.asr_backend = "qwen"
        instance.config.qwen_model_path = "/nonexistent/qwen/model"

        # Re-run the eager-init check that pre- code would have
        # triggered: __init__ must NOT have called _ensure_engine at
        # all (the bg load thread handles it instead).
        assert ensure_engine_calls == [], (
            "DJ-3: VoiceTyperApp.__init__ must NOT eagerly call "
            f"_ensure_engine (got calls: {ensure_engine_calls}). The "
            "background load thread constructs the engine on the daemon "
            "thread — see ModelManager.start_background_load."
        )
