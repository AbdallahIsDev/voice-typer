"""ARCH-007/008: tests for AsrBackendRegistry.create() and the unified
construction path.

Verifies that:
- AsrBackendRegistry.create() constructs and registers each backend type
- The three previously-triplicated construction sites in app.py now all
  delegate to the registry (one chokepoint)
- The registry is initialized in __init__ (not lazily) so _start_dictation
  can rely on it existing
"""

import inspect
from unittest.mock import MagicMock

from voice_typer.server.asr_registry import AsrBackendRegistry


class TestAsrRegistryCreatesEngines:
    """ARCH-007: AsrBackendRegistry.create() owns engine construction."""

    def test_create_whisper_constructs_and_registers(self, monkeypatch):
        """create('whisper', ...) constructs TranscriptionEngine and registers it."""
        fake_engine = MagicMock()
        fake_cls = MagicMock(return_value=fake_engine)
        fake_mod = MagicMock(TranscriptionEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.transcription" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create(
            "whisper",
            whisper_kwargs=dict(
                model_size="tiny.en",
                device="cpu",
                language="en",
                beam_size=1,
                best_of=1,
                condition_on_previous_text=False,
            ),
        )
        assert result is fake_engine
        fake_cls.assert_called_once()
        _, kwargs = fake_cls.call_args
        assert kwargs["model_size"] == "tiny.en"
        assert kwargs["device"] == "cpu"
        # Registry should now have the whisper backend
        assert registry.get("whisper") is fake_engine

    def test_create_qwen_constructs_with_kwargs(self, monkeypatch):
        """create('qwen', ...) constructs QwenEngine with the right kwargs."""
        fake_engine = MagicMock()
        fake_cls = MagicMock(return_value=fake_engine)
        fake_mod = MagicMock(QwenEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.qwen_engine" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create(
            "qwen",
            qwen_kwargs=dict(model_path="/fake/path", device="cpu", language="en"),
        )
        assert result is fake_engine
        fake_cls.assert_called_once_with(model_path="/fake/path", device="cpu", language="en")
        assert registry.get("qwen") is fake_engine

    def test_create_parakeet_constructs_with_kwargs(self, monkeypatch):
        """create('parakeet', ...) constructs ParakeetEngine with the right kwargs."""
        fake_engine = MagicMock()
        fake_cls = MagicMock(return_value=fake_engine)
        fake_mod = MagicMock(ParakeetEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.parakeet_engine" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create(
            "parakeet",
            parakeet_kwargs=dict(device="cuda", language="en"),
        )
        assert result is fake_engine
        fake_cls.assert_called_once_with(device="cuda", language="en")
        assert registry.get("parakeet") is fake_engine

    def test_create_unknown_backend_returns_none(self):
        """create('nonexistent', ...) returns None and logs an error."""
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create("nonexistent")
        assert result is None

    def test_create_handles_import_error_gracefully(self, monkeypatch):
        """If the backend module can't be imported, return None (not raise)."""

        def raise_import(name, *a, **kw):
            raise ImportError(f"no module {name}")

        monkeypatch.setattr("importlib.import_module", raise_import)
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create("whisper", whisper_kwargs={})
        assert result is None

    def test_create_handles_construction_error_gracefully(self, monkeypatch):
        """If the engine constructor raises, return None (not propagate)."""
        fake_cls = MagicMock(side_effect=RuntimeError("bad config"))
        fake_mod = MagicMock(TranscriptionEngine=fake_cls)
        monkeypatch.setattr(
            "importlib.import_module",
            lambda name: fake_mod if name == "voice_typer.server.transcription" else __import__(name),
        )
        registry = AsrBackendRegistry(MagicMock())
        result = registry.create("whisper", whisper_kwargs={})
        assert result is None


class TestAppConstructionDelegatesToRegistry:
    """ARCH-007: All 3 construction sites in app.py delegate to registry.create()."""

    def test_no_direct_transcription_engine_construction_in_app(self):
        """app.py source must NOT contain 'X = TranscriptionEngine(...)' assignments.

        Previously the code had three sites like:
            self.transcriber = TranscriptionEngine(model_size=..., device=..., ...)
        All three now go through AsrBackendRegistry.create() instead.

        We look for the assignment pattern ('= TranscriptionEngine(')
        which only matches real constructor calls, not text mentions
        in comments/docstrings.
        """
        import re

        from voice_typer.server import app

        src = inspect.getsource(app)
        # Match lines like "self.transcriber = TranscriptionEngine(...)"
        # but NOT comments (# ...) or docstring text mentioning the pattern.
        construction_pattern = re.compile(r"^[^#]*=\s*TranscriptionEngine\(", re.MULTILINE)
        matches = construction_pattern.findall(src)
        assert not matches, (
            "ARCH-007 regression: app.py still has direct "
            "'X = TranscriptionEngine(...)' construction. All construction "
            "must go through AsrBackendRegistry.create(). "
            f"Found {len(matches)} match(es)."
        )


class TestAsrRegistryInitializedInAppInit:
    """ARCH-008: registry is initialized in __init__ (not lazily)."""

    def test_asr_registry_exists_after_init(self, tmp_path, monkeypatch):
        """VoiceTyperApp.__init__ must initialize ModelManager (and thus
        the AsrBackendRegistry) before any engine field is accessed.

        ARCH-REFAC-003: the registry used to be exposed via
        ``app._asr_registry`` (@property delegate); it now lives on
        ``app.models._registry`` / ``app.models.registry``.
        """
        # Point config to a temp directory
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        # Mock heavy deps so __init__ doesn't fail
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])
        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # The registry must exist on ModelManager from __init__ time so
        # _start_dictation and other code paths can rely on it existing.
        assert hasattr(app.models, "_registry"), (
            "ModelManager._registry must be set in __init__ so _start_dictation "
            "and other code paths can rely on it existing"
        )
        assert app.models._registry is not None
        assert app.models.registry is not None


class TestLegacyFieldPropertyDelegatesToRegistry:
    """ARCH-047: the three legacy engine attributes
    (``transcriber`` / ``_qwen_engine`` / ``_parakeet_engine``) are now
    ``@property`` accessors that delegate directly to
    ``self._registry.get(...)`` — no mirrored state, no sync step.

    Their ``@property.setter`` counterparts delegate to
    ``self._registry.register(...)`` / ``unregister(...)`` so test code
    that assigns to these attributes continues to work transparently.
    These tests verify the setter's no-churn / unregister-on-None /
    replace-on-change semantics that were previously implemented by the
    (now-deleted) ``_sync_registry_from_fields`` method.
    """

    def _make_mm(self):
        from voice_typer.server.asr_registry import AsrBackendRegistry
        from voice_typer.server.model_manager import ModelManager

        # Minimal config stub — only `asr_backend` is read.
        class _Config:
            asr_backend = "whisper"

        registry = AsrBackendRegistry(_Config())
        # Bypass __init__ — we only need the registry. The three legacy
        # engine attributes are now @property accessors that delegate to
        # ``registry.get(...)``, so they don't need to be initialized.
        mm = ModelManager.__new__(ModelManager)
        mm._registry = registry
        mm._app = None
        return mm, registry

    def test_setter_no_churn_when_same_instance(self, monkeypatch):
        """Re-assigning the SAME instance must NOT log
        'unregistered backend' / 'registered backend'."""
        mm, registry = self._make_mm()

        # Set a transcriber instance — should register.
        transcriber = object()
        mm.transcriber = transcriber
        assert registry.get("whisper") is transcriber

        # Re-assign the SAME instance — registry must NOT churn. The
        # setter short-circuits when ``current is value``, so no
        # unregister/register pair fires. We approximate "no churn" by
        # checking the registered object identity is unchanged.
        mm.transcriber = transcriber
        assert registry.get("whisper") is transcriber

    def test_setter_unregisters_when_value_becomes_none(self, monkeypatch):
        """Assigning None must unregister the backend."""
        mm, registry = self._make_mm()

        mm.transcriber = object()
        assert registry.get("whisper") is mm.transcriber

        # Value → None: must unregister.
        mm.transcriber = None
        assert registry.get("whisper") is None

    def test_setter_replaces_when_value_changes_instance(self, monkeypatch):
        """Changing the instance must unregister the old + register the new."""
        mm, registry = self._make_mm()

        old = object()
        mm.transcriber = old
        assert registry.get("whisper") is old

        new = object()
        mm.transcriber = new
        assert registry.get("whisper") is new

    def test_getter_delegates_to_registry(self, monkeypatch):
        """The @property getter must read directly from the registry."""
        mm, registry = self._make_mm()

        # Initially the registry has no whisper backend.
        assert mm.transcriber is None

        # Register a backend directly via the registry and confirm the
        # property reads it back.
        obj = object()
        registry.register("whisper", obj)
        assert mm.transcriber is obj

        # And the same delegation works for qwen / parakeet.
        qwen_obj = object()
        registry.register("qwen", qwen_obj)
        assert mm._qwen_engine is qwen_obj
        parakeet_obj = object()
        registry.register("parakeet", parakeet_obj)
        assert mm._parakeet_engine is parakeet_obj

    def test_setter_handles_qwen_and_parakeet(self, monkeypatch):
        """The @property setters for _qwen_engine / _parakeet_engine
        delegate to ``register("qwen", ...)`` / ``register("parakeet", ...)``
        just like the whisper setter."""
        mm, registry = self._make_mm()

        mm._qwen_engine = "qwen_value"
        assert registry.get("qwen") == "qwen_value"
        assert mm._qwen_engine == "qwen_value"

        mm._parakeet_engine = "parakeet_value"
        assert registry.get("parakeet") == "parakeet_value"
        assert mm._parakeet_engine == "parakeet_value"

        # None unregisters.
        mm._qwen_engine = None
        assert registry.get("qwen") is None
        mm._parakeet_engine = None
        assert registry.get("parakeet") is None


# ModelManager.set_active_backend regression tests ──────


class TestSetActiveBackend:
    """G4-CR-08: ``ModelManager.set_active_backend`` must exist and switch
    the active ASR backend WITHOUT changing ``model_size``.

    Previously the IPC handler caught the ``AttributeError`` raised by
    the missing method, logged a warning, and returned ``ack`` — the
    actual backend swap never happened. These tests use the REAL
    :class:`ModelManager` (not a MagicMock) so a missing method shows
    up as a real test failure instead of being masked by mock auto-stub.
    """

    def test_set_active_backend_method_exists_on_real_model_manager(self):
        """``ModelManager`` must define ``set_active_backend`` (not rely on
        ``MagicMock`` auto-stub)."""
        from voice_typer.server.model_manager import ModelManager

        assert hasattr(ModelManager, "set_active_backend"), (
            "G4-CR-08: ModelManager must define set_active_backend(). "
            "Previously this method was missing and the IPC handler "
            "silently swallowed the AttributeError."
        )
        # The method must be callable on the class itself (not just an
        # instance attribute) — this is what distinguishes a real method
        # from a MagicMock auto-stub.
        assert callable(ModelManager.set_active_backend)

    def test_set_active_backend_rejects_unknown_backend(self):
        """An unknown backend name raises ValueError (not silent AttributeError)."""
        from voice_typer.server.model_manager import ModelManager

        # Use __new__ to skip the heavy __init__ (which needs a real app).
        mm = ModelManager.__new__(ModelManager)
        mm._app = MagicMock()
        mm._model_change_lock = __import__("threading").RLock()
        # The ValueError must be raised BEFORE any lock acquisition or
        # config mutation — the validation happens at the top of the method.
        try:
            mm.set_active_backend("nonexistent-backend")
        except ValueError as e:
            assert "unknown backend" in str(e).lower()
        except Exception as e:
            raise AssertionError(
                f"set_active_backend with unknown backend should raise ValueError, got {type(e).__name__}: {e}"
            ) from e
        else:
            raise AssertionError("set_active_backend with unknown backend should raise ValueError")

    def test_set_active_backend_noop_when_already_active(self):
        """If the new backend equals the current one, the method returns
        early without unloading/reloading."""
        import threading as _threading
        from unittest.mock import MagicMock

        from voice_typer.server.asr_registry import AsrBackendRegistry
        from voice_typer.server.model_manager import ModelManager

        class _Config:
            asr_backend = "whisper"
            model_size = "tiny.en"

            def save(self):
                return True

        config = _Config()
        registry = AsrBackendRegistry(config)
        mm = ModelManager.__new__(ModelManager)
        mm._app = MagicMock()
        mm._app.config = config
        mm._app.recorder.recording = False
        mm._app._busy_event = _threading.Event()
        mm._app._busy_event.set()  # not busy
        mm._registry = registry
        mm._model_change_lock = _threading.RLock()
        mm.transcriber = None
        mm._qwen_engine = None
        mm._parakeet_engine = None
        mm._model_access_times = {}
        mm._model_lru_lock = _threading.Lock()
        mm._lazy_init_lock = _threading.Lock()
        mm._pending_model_change = None
        mm._model_load_attempted = False

        # Spy: ensure save() is NOT called when backend is unchanged.
        save_calls = []
        original_save = config.save

        def spy_save():
            save_calls.append(True)
            return original_save()

        config.save = spy_save

        # Already on whisper — calling set_active_backend("whisper") is a no-op.
        mm.set_active_backend("whisper")

        assert save_calls == [], (
            "set_active_backend should NOT call config.save() when the backend is already active (no-op short-circuit)."
        )

    def test_set_active_backend_switches_backend_and_preserves_model_size(self):
        """Switching whisper → qwen unloads whisper, sets config.asr_backend=qwen,
        and leaves model_size untouched."""
        import threading as _threading
        from unittest.mock import MagicMock

        from voice_typer.server.asr_registry import AsrBackendRegistry
        from voice_typer.server.model_manager import ModelManager

        class _Config:
            asr_backend = "whisper"
            model_size = "small.en"

            def save(self):
                return True

        config = _Config()
        registry = AsrBackendRegistry(config)
        # Pre-register a whisper engine so _change_model_unload_phase has
        # something to unload.
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)

        mm = ModelManager.__new__(ModelManager)
        mm._app = MagicMock()
        mm._app.config = config
        mm._app.recorder.recording = False
        mm._app._busy_event = _threading.Event()
        mm._app._busy_event.set()  # not busy
        mm._registry = registry
        mm._model_change_lock = _threading.RLock()
        mm.transcriber = whisper_engine
        mm._qwen_engine = None
        mm._parakeet_engine = None
        mm._model_access_times = {}
        mm._model_lru_lock = _threading.Lock()
        mm._lazy_init_lock = _threading.Lock()
        mm._pending_model_change = None
        mm._model_load_attempted = False

        # Stub _ensure_engine so it doesn't try to actually import qwen_engine.
        ensure_calls = []

        def fake_ensure(backend_name):
            ensure_calls.append(backend_name)
            # Register a stub qwen engine.
            qwen_engine = MagicMock()
            qwen_engine.is_loaded = True
            registry.register(backend_name, qwen_engine)

        mm._ensure_engine = fake_ensure

        # Stub load_active so it returns a truthy backend without loading.
        def fake_load_active(progress_callback=None):
            return registry.get("qwen")

        registry.load_active = fake_load_active

        # Stub touch_model + _evict_lru_model (they touch state we don't care about).
        mm.touch_model = lambda name: None
        mm._evict_lru_model = lambda: None

        # Switch to qwen.
        mm.set_active_backend("qwen")

        # config.asr_backend must be updated to qwen.
        assert config.asr_backend == "qwen", (
            f"set_active_backend('qwen') should set config.asr_backend='qwen', got {config.asr_backend!r}"
        )
        # model_size must be PRESERVED (: "WITHOUT changing model_size").
        assert config.model_size == "small.en", (
            f"set_active_backend should NOT change model_size; expected 'small.en', got {config.model_size!r}"
        )
        # _ensure_engine was called for qwen (new backend constructed).
        assert ensure_calls == ["qwen"], f"_ensure_engine should have been called once for 'qwen', got {ensure_calls}"
        # Whisper engine was unloaded (unload() called on it).
        whisper_engine.unload.assert_called()


# whisper fallback constructs whisper on cold boot ───────


class TestWhisperFallbackConstructsOnColdBoot:
    """G4-H-19: when the primary backend fails AND no whisper engine was
    pre-registered, ``load_with_fallback`` must construct one on-the-fly
    via ``create("whisper", ...)`` instead of silently no-op'ing."""

    def test_load_with_fallback_constructs_whisper_when_missing(self, monkeypatch):
        """Primary backend fails → whisper engine not registered → registry
        constructs whisper via ``create()`` and loads it as fallback."""
        # Construct a fake whisper engine that the create() path will return.
        fake_whisper_engine = MagicMock()
        fake_whisper_engine.is_loaded = False  # not yet loaded
        fake_whisper_cls = MagicMock(return_value=fake_whisper_engine)
        fake_whisper_mod = MagicMock(TranscriptionEngine=fake_whisper_cls)
        # Primary backend module — its engine raises on load().
        failing_engine = MagicMock()
        failing_engine.is_loaded = False
        failing_engine.load.side_effect = RuntimeError("parakeet CUDA OOM")
        failing_parakeet_cls = MagicMock(return_value=failing_engine)
        failing_parakeet_mod = MagicMock(ParakeetEngine=failing_parakeet_cls)

        def fake_import(name, *a, **kw):
            if name == "voice_typer.server.transcription":
                return fake_whisper_mod
            if name == "voice_typer.server.parakeet_engine":
                return failing_parakeet_mod
            return __import__(name)

        monkeypatch.setattr("importlib.import_module", fake_import)

        # Config with parakeet as the active backend.
        class _Config:
            asr_backend = "parakeet"
            model_size = "parakeet"
            device = "cpu"
            language = "en"
            beam_size = 1
            best_of = 1
            condition_on_previous_text = False

        registry = AsrBackendRegistry(_Config())
        # Pre-register the parakeet engine (simulating _ensure_engine having run).
        registry.register("parakeet", failing_engine)
        # IMPORTANT: do NOT register a whisper engine — this is the
        # cold-boot scenario  addresses.

        result = registry.load_with_fallback(progress_callback=lambda msg: None)

        # The whisper engine should have been constructed via create().
        fake_whisper_cls.assert_called_once()
        # And then loaded.
        fake_whisper_engine.load.assert_called_once()
        # The result is the whisper engine (fallback succeeded).
        assert result is fake_whisper_engine, (
            "load_with_fallback should return the whisper fallback engine "
            "when the primary backend fails and whisper was not pre-registered."
        )


# circuit breaker tests ──────────────────────────────────


class TestCircuitBreaker:
    """G4-M-45: per-backend failure counter + disable after N failures."""

    def test_failure_count_increments_on_load_failure(self, monkeypatch):
        """Each load failure increments the per-backend failure counter."""
        failing_engine = MagicMock()
        failing_engine.is_loaded = False
        failing_engine.load.side_effect = RuntimeError("transient failure")

        class _Config:
            asr_backend = "whisper"
            model_size = "tiny.en"
            device = "cpu"
            language = "en"
            beam_size = 1
            best_of = 1
            condition_on_previous_text = False

        registry = AsrBackendRegistry(_Config())
        registry.register("whisper", failing_engine)

        assert registry.failure_count("whisper") == 0
        registry.load_with_fallback(progress_callback=lambda msg: None)
        assert registry.failure_count("whisper") == 1, "Failure counter should increment to 1 after one load failure."
        registry.load_with_fallback(progress_callback=lambda msg: None)
        assert registry.failure_count("whisper") == 2

    def test_failure_count_resets_on_success(self, monkeypatch):
        """A successful load resets the failure counter to 0."""
        # First call fails, second succeeds.
        call_count = {"n": 0}
        engine = MagicMock()
        engine.is_loaded = False

        def fake_load(progress_callback=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first call fails")
            # second call succeeds (no raise)

        engine.load.side_effect = fake_load

        class _Config:
            asr_backend = "whisper"
            model_size = "tiny.en"
            device = "cpu"
            language = "en"
            beam_size = 1
            best_of = 1
            condition_on_previous_text = False

        registry = AsrBackendRegistry(_Config())
        registry.register("whisper", engine)

        registry.load_with_fallback(progress_callback=lambda msg: None)
        assert registry.failure_count("whisper") == 1
        registry.load_with_fallback(progress_callback=lambda msg: None)
        assert registry.failure_count("whisper") == 0, "Failure counter should reset to 0 after a successful load."

    def test_backend_disabled_after_max_consecutive_failures(self, monkeypatch):
        """After ``_MAX_CONSECUTIVE_FAILURES`` failures, the backend is
        added to ``_disabled_backends`` and the one-shot callback fires."""
        failing_engine = MagicMock()
        failing_engine.is_loaded = False
        failing_engine.load.side_effect = RuntimeError("persistent failure")

        # Stub create("whisper", ...) so the fallback doesn't try to
        # import the real TranscriptionEngine module.
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = False
        whisper_engine.load.side_effect = RuntimeError("whisper also fails")

        class _Config:
            asr_backend = "parakeet"
            model_size = "parakeet"
            device = "cpu"
            language = "en"
            beam_size = 1
            best_of = 1
            condition_on_previous_text = False

        registry = AsrBackendRegistry(_Config())
        registry.register("parakeet", failing_engine)

        # Override create() so the whisper fallback doesn't import.
        original_create = registry.create

        def stub_create(name, **kwargs):
            if name == "whisper":
                registry.register("whisper", whisper_engine)
                return whisper_engine
            return original_create(name, **kwargs)

        registry.create = stub_create

        # Track the disable callback.
        disable_calls: list[tuple[str, int]] = []
        registry.on_backend_disabled = lambda name, count: disable_calls.append((name, count))

        # Drive parakeet to MAX failures.
        for _ in range(registry._MAX_CONSECUTIVE_FAILURES):
            registry.load_with_fallback(progress_callback=lambda msg: None)

        assert registry._is_disabled("parakeet"), "parakeet should be disabled after reaching MAX_CONSECUTIVE_FAILURES."
        assert len(disable_calls) == 1, "on_backend_disabled should fire exactly once when the threshold is reached."
        assert disable_calls[0][0] == "parakeet"
        assert disable_calls[0][1] == registry._MAX_CONSECUTIVE_FAILURES

        # Subsequent load_with_fallback calls skip parakeet entirely.
        failing_engine.load.reset_mock()
        registry.load_with_fallback(progress_callback=lambda msg: None)
        failing_engine.load.assert_not_called()
        # Disabled backends should be skipped — load_with_fallback should
        # go straight to the whisper fallback.

    def test_reset_failures_clears_disabled_state(self):
        """``reset_failures(name)`` clears both the counter and disabled state."""

        class _Config:
            asr_backend = "whisper"
            disabled_backends = ["parakeet"]

        registry = AsrBackendRegistry(_Config())
        assert registry._is_disabled("parakeet")
        registry._failure_counts["parakeet"] = 5
        registry.reset_failures("parakeet")
        assert not registry._is_disabled("parakeet")
        assert registry.failure_count("parakeet") == 0

    def test_persisted_disabled_backends_restored_on_init(self):
        """If ``config.disabled_backends`` is set, the registry restores
        the disabled state on construction."""

        class _Config:
            asr_backend = "whisper"
            disabled_backends = ["parakeet", "qwen"]

        registry = AsrBackendRegistry(_Config())
        assert registry._is_disabled("parakeet")
        assert registry._is_disabled("qwen")
        assert not registry._is_disabled("whisper")
