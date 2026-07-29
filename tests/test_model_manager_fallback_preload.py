"""DJ-14: pre-warmed CPU whisper-tiny.en fallback backend.

When ``config.preload_cpu_fallback`` is True (default) AND
``config.asr_backend != "whisper"``, ``ModelManager.start_background_load``
spawns a daemon thread that pre-warms a CPU-resident
``whisper-tiny.en`` backend and registers it in the
``AsrBackendRegistry`` under the name ``"whisper_cpu_fallback"``.

The fallback path in ``transcription.py:_transcribe_with_fallback_unlocked``
(F6's file) looks up this name and transcribes on the pre-loaded model
instead of doing a cold ~5-15 s load on the dictation hot path.

These tests pin the contract:

1. The preload happens when the config gates are open.
2. The preload is SKIPPED when ``asr_backend == "whisper"`` (don't
   double-load — the existing GPU→CPU fallback inside
   ``TranscriptionEngine`` already covers that case).
3. The preload is SKIPPED when ``preload_cpu_fallback`` is False.
4. The backend is registered under the documented name
   ``"whisper_cpu_fallback"``.
5. The constructed engine has ``model_size="tiny.en"``,
   ``device="cpu"``.
6. ``engine.load()`` is called to actually pull the model into RSS.
7. The preload is idempotent — calling it twice doesn't double-register.
8. The preload is best-effort — a construction failure doesn't crash.
9. The preload is skipped when shutdown is in progress.

The heavy ``torch`` / ``ctranslate2`` / ``transformers`` dependencies
are mocked so the tests run headless on the Linux sandbox. The actual
VRAM/RSS impact of the preload can ONLY be verified on a real host
with the whisper package installed — see VALIDATE ON WHISPER-HOST in
the fix report.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest


def _make_mm(
    *,
    asr_backend: str = "parakeet",
    preload_cpu_fallback: bool = True,
    shutting_down: bool = False,
) -> tuple:
    """Build a ModelManager with a mock app + mock registry.

    Returns ``(mm, app, mock_registry)``. The mock registry records
    ``register`` / ``unregister`` / ``get`` calls so tests can assert
    on the DJ-14 preload contract.
    """
    from voice_typer.server.model_manager import ModelManager

    app = MagicMock(name="app")
    app.config.asr_backend = asr_backend
    app.config.model_size = "small.en"
    app.config.device = "cuda"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.preload_cpu_fallback = preload_cpu_fallback
    app._shutting_down = shutting_down
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    app._config_mutation_lock = threading.RLock()

    mm = ModelManager(app)

    mock_registry = MagicMock(name="registry")
    mock_registry.active_name = asr_backend
    mock_registry.get.return_value = None  # no existing fallback
    mm._registry = mock_registry

    return mm, app, mock_registry


@pytest.fixture
def patch_transcription_engine(monkeypatch):
    """Patch ``TranscriptionEngine`` so the preload doesn't actually
    import torch / ctranslate2.

    Yields a ``(engine_mock, construct_calls)`` tuple where
    ``construct_calls`` is the list of kwargs dicts passed to the
    ``TranscriptionEngine`` constructor.
    """
    construct_calls: list[dict] = []

    # Build a fresh MagicMock for each construction so each call
    # returns a distinct engine instance (matching real behaviour).
    def _fake_ctor(**kwargs):
        construct_calls.append(kwargs)
        engine = MagicMock(name="TranscriptionEngine")
        # Simulate a successful load.
        engine.is_loaded = True
        engine.model_size = kwargs.get("model_size", "tiny.en")
        engine._device = kwargs.get("device", "cpu")
        engine._compute_type = "int8"
        return engine

    # Insert a fake module in sys.modules so the ``from
    # voice_typer.server.transcription import TranscriptionEngine``
    # import inside ``_preload_cpu_fallback_backend`` resolves to our
    # mock. We patch the existing module's TranscriptionEngine attribute
    # rather than replacing the whole module so other tests that
    # import transcription (e.g. via model_manager) keep working.
    import voice_typer.server.transcription as trans_mod

    monkeypatch.setattr(trans_mod, "TranscriptionEngine", _fake_ctor)
    return _fake_ctor, construct_calls


class TestDJ14PreloadContract:
    """DJ-14: ``ModelManager._preload_cpu_fallback_backend`` MUST
    construct a CPU whisper-tiny.en backend, register it under the
    documented name, and call ``.load()`` to pull the model into RSS."""

    def test_cpu_fallback_backend_name_constant(self):
        """The registry name contract for F6's lookup. This MUST be
        ``"whisper_cpu_fallback"`` so the fallback path in
        ``transcription.py`` (F6's file) can look it up via
        ``registry.get("whisper_cpu_fallback")``."""
        from voice_typer.server.model_manager import ModelManager

        assert ModelManager._CPU_FALLBACK_BACKEND_NAME == "whisper_cpu_fallback", (
            "DJ-14 contract: the CPU fallback backend MUST be registered "
            "under the name 'whisper_cpu_fallback' so the fallback path "
            "in transcription.py (F6's file) can look it up by that name. "
            "Changing this constant breaks the F6 contract."
        )

    def test_preload_constructs_engine_with_tiny_en_and_cpu(self, patch_transcription_engine):
        """When the config gates are open (preload_cpu_fallback=True,
        asr_backend != "whisper"), ``_preload_cpu_fallback_backend``
        MUST construct a TranscriptionEngine with
        ``model_size="tiny.en"`` and ``device="cpu"``."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")
        _fake_ctor, construct_calls = patch_transcription_engine

        mm._preload_cpu_fallback_backend()

        assert len(construct_calls) == 1, (
            "DJ-14: _preload_cpu_fallback_backend must construct exactly "
            "one TranscriptionEngine when the config gates are open."
        )
        kwargs = construct_calls[0]
        assert kwargs["model_size"] == "tiny.en", (
            "DJ-14: the preloaded CPU fallback engine must use "
            "model_size='tiny.en' (the smallest whisper model — chosen "
            "for the fallback path because it loads fast and uses the "
            "least CPU/RAM)."
        )
        assert kwargs["device"] == "cpu", (
            "DJ-14: the preloaded CPU fallback engine must use device='cpu' "
            "so it doesn't compete with the active backend for VRAM."
        )

    def test_preload_registers_under_documented_name(self, patch_transcription_engine):
        """The constructed engine MUST be registered under the
        ``_CPU_FALLBACK_BACKEND_NAME`` (``"whisper_cpu_fallback"``) so
        F6's fallback path can look it up."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")
        _fake_ctor, _ = patch_transcription_engine

        mm._preload_cpu_fallback_backend()

        mock_registry.register.assert_called_once()
        call_args = mock_registry.register.call_args
        assert call_args.args[0] == "whisper_cpu_fallback", (
            "DJ-14: the CPU fallback engine must be registered under the "
            "name 'whisper_cpu_fallback' (the documented contract for F6's "
            "fallback path lookup)."
        )

    def test_preload_calls_engine_load(self, patch_transcription_engine):
        """``engine.load()`` MUST be called to actually pull the model
        into RSS. Without this call the engine is registered but
        unloaded — F6's fallback path would still hit a cold load."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")
        _fake_ctor, _ = patch_transcription_engine

        mm._preload_cpu_fallback_backend()

        mock_registry.register.assert_called_once()
        registered_engine = mock_registry.register.call_args.args[1]
        registered_engine.load.assert_called_once()
        # The load call must include a progress_callback kwarg (even
        # if it's a no-op lambda) — TranscriptionEngine.load's signature
        # requires it.
        assert "progress_callback" in registered_engine.load.call_args.kwargs

    def test_preload_skipped_when_asr_backend_is_whisper(self, patch_transcription_engine):
        """DJ-14 CAUTION: when ``asr_backend == "whisper"``, the
        preload MUST be skipped — the active backend IS whisper, so a
        separate CPU fallback would double-load the same model family.
        The existing GPU→CPU fallback inside ``TranscriptionEngine``
        already covers that case."""
        mm, app, mock_registry = _make_mm(asr_backend="whisper")
        _fake_ctor, construct_calls = patch_transcription_engine

        mm._preload_cpu_fallback_backend()

        assert construct_calls == [], (
            "DJ-14: _preload_cpu_fallback_backend must NOT construct a "
            "TranscriptionEngine when asr_backend == 'whisper' (the "
            "active backend IS whisper — preloading a separate CPU "
            "fallback would double-load the same model family)."
        )
        mock_registry.register.assert_not_called()

    def test_preload_skipped_when_preload_cpu_fallback_false(self, patch_transcription_engine):
        """DJ-14: when ``preload_cpu_fallback`` is False (user opt-out),
        the preload MUST be skipped."""
        mm, app, mock_registry = _make_mm(
            asr_backend="parakeet", preload_cpu_fallback=False
        )
        _fake_ctor, construct_calls = patch_transcription_engine

        mm._preload_cpu_fallback_backend()

        assert construct_calls == [], (
            "DJ-14: _preload_cpu_fallback_backend must NOT construct a "
            "TranscriptionEngine when config.preload_cpu_fallback is False "
            "(user opt-out from Settings)."
        )
        mock_registry.register.assert_not_called()

    def test_preload_skipped_when_shutting_down(self, patch_transcription_engine):
        """DJ-14: when ``app._shutting_down`` is True, the preload MUST
        be skipped — don't load a model during shutdown."""
        mm, app, mock_registry = _make_mm(
            asr_backend="parakeet", shutting_down=True
        )
        _fake_ctor, construct_calls = patch_transcription_engine

        mm._preload_cpu_fallback_backend()

        assert construct_calls == [], (
            "DJ-14: _preload_cpu_fallback_backend must NOT construct a "
            "TranscriptionEngine when app._shutting_down is True (the "
            "app is tearing down — loading a model now would race "
            "with cleanup)."
        )
        mock_registry.register.assert_not_called()

    def test_preload_idempotent_when_already_loaded(self, patch_transcription_engine):
        """DJ-14: if the backend is already registered AND loaded, the
        preload MUST be a no-op (idempotent). Calling it twice
        shouldn't double-register or double-load."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")
        _fake_ctor, construct_calls = patch_transcription_engine

        # Simulate an already-loaded backend in the registry.
        existing_engine = MagicMock(name="existing_engine")
        existing_engine.is_loaded = True
        mock_registry.get.return_value = existing_engine

        mm._preload_cpu_fallback_backend()

        assert construct_calls == [], (
            "DJ-14: _preload_cpu_fallback_backend must NOT construct a "
            "new TranscriptionEngine when an already-loaded backend is "
            "registered under 'whisper_cpu_fallback' (idempotent)."
        )
        mock_registry.register.assert_not_called()

    def test_preload_unregisters_on_load_failure(self, patch_transcription_engine):
        """DJ-14: if ``engine.load()`` raises, the partially-registered
        engine MUST be unregistered so the next preload attempt can
        try again (and so F6's lookup doesn't find a half-loaded
        engine)."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")
        _fake_ctor, _ = patch_transcription_engine

        # Override the fake constructor to return an engine whose
        # .load() raises.
        def _failing_ctor(**kwargs):
            engine = MagicMock(name="failing_engine")
            engine.is_loaded = False
            engine.load.side_effect = RuntimeError("torch not installed")
            return engine

        import voice_typer.server.transcription as trans_mod

        trans_mod.TranscriptionEngine = _failing_ctor  # noqa: SLF001

        mm._preload_cpu_fallback_backend()

        # Registered, then unregistered on load failure.
        mock_registry.register.assert_called_once()
        mock_registry.unregister.assert_called_once_with("whisper_cpu_fallback")

    def test_preload_swallows_construction_failure(self, patch_transcription_engine):
        """DJ-14: if ``TranscriptionEngine(...)`` construction raises,
        the preload MUST swallow the exception (non-fatal — the
        existing cold-load fallback path remains available)."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")

        # Override the fake constructor to raise.
        def _failing_ctor(**kwargs):
            raise RuntimeError("ctranslate2 import failed")

        import voice_typer.server.transcription as trans_mod

        trans_mod.TranscriptionEngine = _failing_ctor  # noqa: SLF001

        # Must NOT raise.
        mm._preload_cpu_fallback_backend()

        mock_registry.register.assert_not_called()

    def test_preload_swallows_unexpected_exception(self, patch_transcription_engine):
        """DJ-14: the preload MUST NEVER break the active load path.
        Any unexpected exception is logged and swallowed (defensive
        try/except at the outermost level)."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")
        _fake_ctor, _ = patch_transcription_engine

        # Make config.asr_backend access raise to trigger the
        # outermost try/except.
        type(app.config).asr_backend = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        # Must NOT raise.
        mm._preload_cpu_fallback_backend()

        mock_registry.register.assert_not_called()


class TestDJ14SpawnFromStartBackgroundLoad:
    """DJ-14: ``start_background_load`` MUST spawn a daemon thread that
    pre-warms the CPU fallback backend after the active load finishes."""

    def test_spawn_skipped_when_preload_cpu_fallback_false(self):
        """When ``preload_cpu_fallback`` is False, ``start_background_load``
        MUST NOT spawn the CPU fallback preload thread."""
        mm, app, mock_registry = _make_mm(
            asr_backend="parakeet", preload_cpu_fallback=False
        )
        # Stub load_background so the ModelLoad thread exits instantly.
        mm.load_background = MagicMock()

        registered_names: list[str] = []

        def _capture_register(name, **kwargs):
            registered_names.append(name)

        app._thread_registry.register.side_effect = _capture_register

        mm.start_background_load()

        # Give the ModelLoad thread a moment to exit.
        time.sleep(0.05)

        assert "ModelLoadCpuFallback" not in registered_names, (
            "DJ-14: start_background_load must NOT spawn the "
            "ModelLoadCpuFallback thread when preload_cpu_fallback is False."
        )

    def test_spawn_skipped_when_asr_backend_is_whisper(self):
        """When ``asr_backend == 'whisper'``, ``start_background_load``
        MUST NOT spawn the CPU fallback preload thread (don't
        double-load)."""
        mm, app, mock_registry = _make_mm(asr_backend="whisper")
        mm.load_background = MagicMock()

        registered_names: list[str] = []

        def _capture_register(name, **kwargs):
            registered_names.append(name)

        app._thread_registry.register.side_effect = _capture_register

        mm.start_background_load()
        time.sleep(0.05)

        assert "ModelLoadCpuFallback" not in registered_names, (
            "DJ-14: start_background_load must NOT spawn the "
            "ModelLoadCpuFallback thread when asr_backend == 'whisper' "
            "(don't double-load — the existing GPU→CPU fallback inside "
            "TranscriptionEngine covers that case)."
        )

    def test_spawned_when_gates_open(self):
        """When the config gates are open (preload_cpu_fallback=True,
        asr_backend != 'whisper'), ``start_background_load`` MUST
        spawn the ModelLoadCpuFallback daemon thread and register it
        with ``app._thread_registry`` for shutdown join."""
        mm, app, mock_registry = _make_mm(asr_backend="parakeet")
        # Stub load_background so the ModelLoad thread exits instantly
        # — the preload thread joins it (timeout=60s) and then runs.
        mm.load_background = MagicMock()

        registered_names: list[str] = []

        def _capture_register(name, **kwargs):
            registered_names.append(name)

        app._thread_registry.register.side_effect = _capture_register

        mm.start_background_load()
        # Give both threads a moment to register themselves.
        time.sleep(0.1)

        assert "ModelLoad" in registered_names, (
            "DJ-14 sanity: start_background_load must spawn the ModelLoad "
            "thread (this is the existing CR-18 path, not the DJ-14 path)."
        )
        assert "ModelLoadCpuFallback" in registered_names, (
            "DJ-14: start_background_load must spawn the "
            "ModelLoadCpuFallback daemon thread when the config gates "
            "are open, AND register it with app._thread_registry so "
            "shutdown_all() can join it during quit()."
        )


class TestDJ14ConfigField:
    """DJ-14: ``preload_cpu_fallback`` is a new Config field with a
    default of True. Exposed via IPC so users can opt out from
    Settings."""

    def test_field_exists_with_default_true(self):
        """``Config()`` must have ``preload_cpu_fallback == True``
        (the DJ-14 default)."""
        from voice_typer.server.config import Config

        cfg = Config()
        assert hasattr(cfg, "preload_cpu_fallback")
        assert cfg.preload_cpu_fallback is True, (
            "DJ-14: preload_cpu_fallback must default to True so the "
            "CPU fallback backend is pre-warmed by default (the "
            "intermittent-dictation power-saving case is the common one)."
        )

    def test_field_in_ipc_allowlist(self):
        """The field must be in ``IPC_CONFIG_ALLOWLIST`` so the
        renderer can set it via IPC ``set_config`` (users on
        memory-constrained systems can opt out from Settings)."""
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        assert "preload_cpu_fallback" in IPC_CONFIG_ALLOWLIST, (
            "DJ-14: preload_cpu_fallback must be in IPC_CONFIG_ALLOWLIST "
            "so users can opt out from Settings (memory-constrained "
            "systems may not want the ~1-3 GB RSS overhead)."
        )

    def test_ipc_validator_accepts_bool(self):
        """The IPC validator must accept True/False for
        ``preload_cpu_fallback``."""
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"preload_cpu_fallback": True})
        assert validated.get("preload_cpu_fallback") is True
        assert not errors

        validated, errors = validate_config_update({"preload_cpu_fallback": False})
        assert validated.get("preload_cpu_fallback") is False
        assert not errors
