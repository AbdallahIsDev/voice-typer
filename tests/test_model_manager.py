"""regression test: ``available_backends`` @property must NOT be
called with parens.

Pre-fix bug: ``model_manager.py:363`` called
``self._registry.available_backends()`` — but ``available_backends`` is
a ``@property`` returning ``list[str]``. Calling ``()`` on the
returned list raised ``TypeError: 'list' object is not callable`` on
the all-backends-fail path, masking the diagnostic ``log.warning``
that lists attempted backends + primary backend (actionable
diagnostic info for the user / support).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from voice_typer.server.model_manager import ModelManager


def _make_mm_with_failing_registry() -> tuple[ModelManager, MagicMock]:
    """Construct a ModelManager whose registry's
    ``load_with_fallback`` returns falsy (simulating all backends
    failed). Returns the ModelManager and the mock app for
    inspection.
    """
    # Build a minimal mock app with the attributes ModelManager.__init__
    # and load_background read.
    app = MagicMock(name="app")
    app.config.asr_backend = "whisper"
    app.config.model_size = "tiny"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.hotkey = "<f2>"
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()

    mm = ModelManager(app)

    # Replace the registry with a mock whose ``load_with_fallback``
    # returns falsy (the all-backends-fail path).
    mock_registry = MagicMock(name="registry")
    mock_registry.load_with_fallback.return_value = None  # falsy → fail path
    # ``available_backends`` is a @property — mock it as a list, NOT a
    # callable. The OLD buggy code would call this with parens and
    # raise TypeError. The  fix accesses it as a property.
    mock_registry.available_backends = ["whisper", "parakeet"]
    mock_registry.active_name = "whisper"
    mock_registry.get_active.return_value = None
    mm._registry = mock_registry

    # Stub _ensure_engine so we don't actually try to construct a real
    # TranscriptionEngine. (_sync_registry_from_fields was removed — the
    # @property setters on transcriber / _qwen_engine / _parakeet_engine
    # now keep the registry in sync automatically.)
    mm._ensure_engine = MagicMock()
    # Stub touch_model + _evict_lru_model so they don't touch LRU state.
    mm.touch_model = MagicMock()
    mm._evict_lru_model = MagicMock()

    return mm, app


class TestAvailableBackendsPropertyNoParens:
    """``available_backends`` is a @property — must be accessed
    WITHOUT parens."""

    def test_source_does_not_call_available_backends_with_parens(self):
        """Source guard: ``load_background`` must NOT call
        ``available_backends()`` (with parens). It must access it as a
        property: ``available_backends`` (no parens)."""
        import inspect

        src = inspect.getsource(ModelManager.load_background)
        # The buggy form: ``self._registry.available_backends()`` with
        # parens. We strip whitespace inside the parens to be robust
        # against formatting.
        assert "available_backends()" not in src, (
            "regression: load_background calls "
            "self._registry.available_backends() with parens — but "
            "available_backends is a @property. Calling it with parens "
            "raises TypeError: 'list' object is not callable, masking "
            "the diagnostic log.warning on the all-backends-fail path."
        )
        # The fixed form: property access without parens.
        assert "available_backends" in src, (
            "load_background must access "
            "self._registry.available_backends (no parens) to list "
            "attempted backends in the diagnostic log.warning."
        )

    def test_all_backends_fail_emits_warning_with_backend_names(self, caplog):
        """End-to-end: when ``load_with_fallback`` returns falsy (all
        backends failed), the diagnostic ``log.warning`` MUST be
        emitted with the backend names — NOT a ``TypeError`` from
        calling the ``available_backends`` property with parens.

        Pre-fix: this test would fail with ``TypeError: 'list' object
        is not callable`` raised from inside ``load_background`` (the
        ``except Exception`` block would catch it and log
        ``[STARTUP] Background model load crashed`` — masking the
        diagnostic ``log.warning`` that lists the attempted backends).
        """
        mm, app = _make_mm_with_failing_registry()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.model_manager"):
            mm.load_background()

        # The diagnostic log.warning MUST be present (it lists the
        # attempted backends + primary). Pre-fix, the TypeError raised
        # by ``available_backends()`` masked this warning.
        diagnostic_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "All backends failed to load" in r.getMessage()
        ]
        assert diagnostic_warnings, (
            "when all backends fail to load, load_background must "
            "emit a log.warning listing the attempted backends + primary. "
            "Pre-fix, this warning was masked by a TypeError raised when "
            "calling available_backends() (a @property) with parens."
        )
        # The warning message must include the backend names from the
        # (mocked) ``available_backends`` property.
        warning_msg = diagnostic_warnings[0].getMessage()
        assert "whisper" in warning_msg and "parakeet" in warning_msg, (
            f"the diagnostic log.warning must list the attempted backends (whisper, parakeet). Got: {warning_msg!r}"
        )
        # The primary backend name must also be present.
        assert "primary=whisper" in warning_msg, (
            f"the diagnostic log.warning must include the primary backend name. Got: {warning_msg!r}"
        )

    def test_all_backends_fail_does_not_raise_typeerror(self, caplog):
        """the all-backends-fail path must NOT raise
        ``TypeError: 'list' object is not callable`` (the pre-fix bug
        from calling ``available_backends()`` with parens)."""
        mm, app = _make_mm_with_failing_registry()

        # Capture all log records at any level — we want to assert the
        # ``Background model load crashed`` exception log is NOT present
        # (that would indicate load_background hit the outer except).
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.model_manager"):
            mm.load_background()

        crashed_logs = [r for r in caplog.records if "Background model load crashed" in r.getMessage()]
        assert not crashed_logs, (
            "load_background's outer ``except Exception`` caught "
            "an exception (logged as 'Background model load crashed'). "
            "Pre-fix, this was a TypeError from calling available_backends() "
            "with parens. The fixed code must reach the diagnostic "
            "log.warning without raising."
        )


# ─── Dedicated coverage for ModelManager lifecycle paths ───────────────
#
# These tests exercise the constructor wiring and the high-level
# methods (``fallback_to_whisper``, ``change_model``, ``_evict_lru_model``)
# that previously had NO direct test file — every other test site used
# ``ModelManager.__new__(ModelManager)`` to bypass ``__init__``, so the
# constructor wiring and these specific code paths were never
# exercised end-to-end.


def _make_mm_with_mock_registry():
    """Build a ModelManager via the real ``__init__`` with a MagicMock
    app, then swap the registry for a MagicMock.

    The real ``__init__`` runs so its wiring (locks, LRU state,
    ``_model_load_thread = None``, etc.) is exercised. The registry
    is then replaced because constructing a real ``AsrBackendRegistry``
    is fine (it doesn't load any engines) but every method we want to
    assert on (``load_with_fallback``, ``create``, ``unload``...) is
    easier to verify via mocks.
    """
    import threading

    app = MagicMock(name="app")
    app.config.asr_backend = "whisper"
    app.config.model_size = "tiny"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.hotkey = "<f2>"
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    # ``_config_mutation_lock`` is acquired as a context manager by
    # ``_change_model_blocking``. A MagicMock context manager works
    # but a real RLock is more faithful (and exposes ``_is_owned``
    # for the re-entrancy assertion).
    app._config_mutation_lock = threading.RLock()

    mm = ModelManager(app)

    # Swap the real registry for a mock — but keep its ``available_backends``
    # as a list (it's a @property on the real registry, so MagicMock's
    # auto-attribute would be a MagicMock, which the ``callable()`` branch
    # in ``load_background`` would try to invoke).
    mock_registry = MagicMock(name="registry")
    mock_registry.available_backends = ["whisper", "parakeet"]
    mock_registry.active_name = "whisper"
    mock_registry.get_active.return_value = None
    mm._registry = mock_registry

    # Stub the LRU-touch + eviction helpers so the load paths don't
    # actually try to track / evict (those are tested directly below).
    mm.touch_model = MagicMock()
    mm._evict_lru_model = MagicMock()

    return mm, app


class TestInitWiring:
    """``ModelManager.__init__`` must wire the registry, all locks, and
    the LRU / pending-state fields. Previously every test site used
    ``__new__`` so this wiring was never asserted.
    """

    def test_init_creates_registry_and_locks(self):
        """``__init__`` must construct an ``AsrBackendRegistry`` and the
        four locks (``_model_change_lock`` is an RLock so
        ``apply_pending_model_change`` can re-enter ``change_model``)."""
        from unittest.mock import MagicMock

        from voice_typer.server.asr_registry import AsrBackendRegistry

        app = MagicMock()
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny"
        app.config.device = "cpu"
        app.config.language = "en"
        app.config.beam_size = 1
        app.config.best_of = 1
        app.config.condition_on_previous_text = False

        mm = ModelManager(app)

        # Registry is constructed eagerly (not lazy).
        assert isinstance(mm._registry, AsrBackendRegistry), "__init__ must construct an AsrBackendRegistry eagerly"
        # _model_change_lock must be an RLock (re-entrant for
        # apply_pending_model_change -> change_model).
        assert hasattr(mm._model_change_lock, "_is_owned"), "_model_change_lock must be an RLock (re-entrant)"
        # _model_lru_lock is a plain Lock (no re-entrancy needed).
        assert not hasattr(mm._model_lru_lock, "_is_owned"), "_model_lru_lock must be a plain Lock"
        # _lazy_init_lock is a plain Lock (was a hasattr-based lazy
        # init before the LAZY-INIT-LOCK-FIX — must now exist on
        # __init__).
        assert hasattr(mm, "_lazy_init_lock"), "__init__ must set _lazy_init_lock (LAZY-INIT-LOCK-FIX)"

    def test_init_initializes_lru_and_pending_state(self):
        """``__init__`` must zero out the LRU tracking dict and the
        pending-state fields so the first ``change_model`` /
        ``toggle_dictation`` doesn't read stale state from a previous
        instance."""
        from unittest.mock import MagicMock

        app = MagicMock()
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny"
        app.config.device = "cpu"
        app.config.language = "en"
        app.config.beam_size = 1
        app.config.best_of = 1
        app.config.condition_on_previous_text = False

        mm = ModelManager(app)

        assert mm._model_access_times == {}, "__init__ must start with an empty LRU tracking dict"
        assert mm._model_load_thread is None
        assert mm._model_load_attempted is False
        assert mm._pending_dictation is False
        assert mm._pending_model_change is None
        assert mm._pending_backend_change is None
        assert mm._idle_unload_timer is None


class TestFallbackToWhisper:
    """``fallback_to_whisper`` must mutate config + persist it, construct
    the whisper backend if missing, load via the registry, and update
    tray state for both success and failure paths.
    """

    def test_success_path_creates_whisper_and_sets_idle_tray(self):
        from voice_typer.server.tray_types import AppState

        mm, app = _make_mm_with_mock_registry()
        # Whisper not yet registered -> registry.create must be called.
        mm._registry.get.return_value = None
        mm._registry.load_with_fallback.return_value = MagicMock(name="active")

        mm.fallback_to_whisper(notify_on_failure=False)

        # Config mutated to whisper/tiny and persisted.
        assert app.config.asr_backend == "whisper"
        assert app.config.model_size == "tiny"
        app.config.save.assert_called_once()
        # Whisper backend was missing -> registry.create invoked.
        mm._registry.create.assert_called_once()
        create_kwargs = mm._registry.create.call_args
        assert create_kwargs.args[0] == "whisper"
        # load_with_fallback invoked with a progress_callback.
        assert mm._registry.load_with_fallback.called
        # LRU touched + eviction considered on success.
        mm.touch_model.assert_called_once()
        mm._evict_lru_model.assert_called_once()
        # Tray transitioned to IDLE on success.
        tray_states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert AppState.IDLE in tray_states, f"fallback_to_whisper success must set tray to IDLE; got {tray_states}"

    def test_failure_path_sets_error_tray_and_notifies(self):
        from voice_typer.server.tray_types import AppState

        mm, app = _make_mm_with_mock_registry()
        # Whisper already registered -> create NOT called.
        existing = MagicMock(name="existing-whisper")
        mm._registry.get.return_value = existing
        # Load fails.
        mm._registry.load_with_fallback.return_value = None

        mm.fallback_to_whisper(notify_on_failure=True)

        # No create when backend already exists — just model_size backfill.
        mm._registry.create.assert_not_called()
        assert existing.model_size == "tiny"
        # Tray transitioned to ERROR on failure.
        tray_states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert AppState.ERROR in tray_states, f"fallback_to_whisper failure must set tray to ERROR; got {tray_states}"
        # notify_on_failure=True -> tray.notify_safety fired.
        app.tray.notify_safety.assert_called_once()


class TestChangeModelBlocking:
    """``_change_model_blocking`` is the synchronous body of
    ``change_model``. Must run the setattr + unload + load cycle in
    order, holding ``_model_change_lock`` throughout, and publish the
    ``asr_backend_ready`` event on completion.
    """

    def test_blocking_change_runs_unload_then_load_and_publishes(self):
        mm, app = _make_mm_with_mock_registry()
        # Not recording, not busy -> change executes immediately (not deferred).
        app.recorder.recording = False
        app._busy_event.is_set.return_value = True  # not busy
        app.config.save.return_value = True
        # Load succeeds.
        mm._registry.load_active.return_value = True
        active = MagicMock(name="active-engine")
        active.device_info = "cpu"
        mm._registry.get_active.return_value = active
        # Stub _ensure_engine so it doesn't actually construct a backend.
        mm._ensure_engine = MagicMock()
        # Stub the event publish so we can assert it ran.
        mm._publish_backend_ready_event = MagicMock()
        # Stub cancel_idle_unload_timer (called at the top).
        mm.cancel_idle_unload_timer = MagicMock()

        mm._change_model_blocking("parakeet")

        # setattr phase: config was mutated to parakeet.
        assert app.config.asr_backend == "parakeet"
        assert app.config.model_size == "parakeet"
        app.config.save.assert_called_once()
        # unload phase: registry.unload + unregister for the OLD backend ("whisper").
        # ``unregister`` is called once — directly in
        # ``_change_model_unload_phase``. The legacy
        # ``self.transcriber = None`` setter call that previously ran
        # here was dead code: by the time the elif was reached,
        # ``self._registry.unregister("whisper")`` above had already
        # cleared the registry entry, so ``self.transcriber`` (which
        # delegates to ``registry.get("whisper")``) was always ``None``
        # in production and the elif branch was never taken. The dead
        # elif was removed; this assertion was tightened from
        # ``["whisper", "whisper"]`` to ``["whisper"]`` to reflect
        # the real production call count.
        mm._registry.unload.assert_called_once_with("whisper")
        unregister_calls = [c.args[0] for c in mm._registry.unregister.call_args_list]
        assert unregister_calls == ["whisper"], (
            f"Expected unregister('whisper') once (direct only — the legacy "
            f"setter-call branch was dead code, removed); got {unregister_calls}"
        )
        # load phase: _ensure_engine called with the NEW backend ("parakeet").
        mm._ensure_engine.assert_called_once_with("parakeet")
        mm._registry.load_active.assert_called_once()
        # LRU touched for the new backend, eviction considered.
        mm.touch_model.assert_called_once_with("parakeet")
        mm._evict_lru_model.assert_called_once()
        # Event published with the new backend + model_size.
        mm._publish_backend_ready_event.assert_called_once_with("parakeet", "parakeet")

    def test_blocking_change_defers_when_recording(self):
        """When a recording is in progress, ``_change_model_setattr_phase``
        returns ``deferred=True`` and the load phase is skipped — the
        change is captured in ``_pending_model_change`` for
        ``apply_pending_model_change`` to apply later."""
        mm, app = _make_mm_with_mock_registry()
        app.recorder.recording = True  # recording in progress
        app._busy_event.is_set.return_value = True
        app.config.save.return_value = True

        mm._ensure_engine = MagicMock()
        mm._registry.load_active = MagicMock()
        mm._publish_backend_ready_event = MagicMock()
        mm.cancel_idle_unload_timer = MagicMock()

        mm._change_model_blocking("qwen")

        # Config still mutated + saved (so the next boot reflects the request).
        assert app.config.asr_backend == "qwen"
        app.config.save.assert_called_once()
        # But load phase skipped — _ensure_engine NOT called.
        mm._ensure_engine.assert_not_called()
        mm._registry.load_active.assert_not_called()
        # Pending change captured.
        assert mm._pending_model_change == "qwen"
        # Event NOT published (load didn't happen).
        mm._publish_backend_ready_event.assert_not_called()


class TestChangeModelAckShape:
    """``change_model`` (the IPC entry point) must return an ack dict
    shaped ``{"status": "loading", "previous": {...}, "pending": {...}}``
    and spawn the background thread — it must NOT block on the load.
    """

    def test_returns_loading_ack_with_previous_and_pending(self):
        mm, app = _make_mm_with_mock_registry()
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny"
        mm.cancel_idle_unload_timer = MagicMock()

        # Swap _change_model_background for a no-op so we don't spawn a
        # real thread (we only care about the ack shape here).
        mm._change_model_background = MagicMock()

        ack = mm.change_model("parakeet")

        assert ack["status"] == "loading"
        assert ack["previous"] == {"backend": "whisper", "model_size": "tiny"}
        assert ack["pending"] == {"backend": "parakeet", "model_size": "parakeet"}
        # Background spawn invoked exactly once.
        mm._change_model_background.assert_called_once_with("parakeet")

    def test_change_model_size_routing(self):
        """``change_model`` routes ``model_size`` to a backend name:
        ``"parakeet"`` -> parakeet, ``"qwen"`` -> qwen, anything else
        -> whisper. Verifies the routing for all three branches."""
        mm, app = _make_mm_with_mock_registry()
        mm.cancel_idle_unload_timer = MagicMock()
        mm._change_model_background = MagicMock()

        # parakeet
        ack = mm.change_model("parakeet")
        assert ack["pending"]["backend"] == "parakeet"
        # qwen
        ack = mm.change_model("qwen")
        assert ack["pending"]["backend"] == "qwen"
        # anything else -> whisper
        ack = mm.change_model("base.en")
        assert ack["pending"]["backend"] == "whisper"
        assert ack["pending"]["model_size"] == "base.en"


class TestLRUEviction:
    """``_evict_lru_model`` must unload the oldest backend when more than
    ``_MAX_LOADED_MODELS`` are loaded. Previously this path was only
    exercised incidentally via ``load_background`` — no test asserted
    the eviction trigger directly.
    """

    def test_no_eviction_when_at_or_below_max(self):
        """When ``len(_model_access_times) <= _MAX_LOADED_MODELS``,
        ``_evict_lru_model`` is a no-op — no engine is unloaded."""
        import time
        from unittest.mock import MagicMock

        mm, _app = _make_mm_with_mock_registry()
        # Restore the real ``_evict_lru_model`` (the helper stubs it so
        # the load/change tests don't actually evict). We're testing
        # the real method here.
        del mm._evict_lru_model
        # Exactly _MAX_LOADED_MODELS entries -> no eviction.
        mm._model_access_times = {
            "whisper": time.monotonic(),
            "parakeet": time.monotonic(),
        }
        mm._registry.get = MagicMock(return_value=MagicMock())

        mm._evict_lru_model()

        mm._registry.get.assert_not_called()

    def test_evicts_oldest_backend_when_over_max(self):
        """When ``len(_model_access_times) > _MAX_LOADED_MODELS``, the
        entry with the OLDEST timestamp is unloaded + unregistered +
        removed from tracking.

        Previously this path called ``engine.unload()`` directly and
        left the backend in the registry (so a subsequent
        ``_ensure_engine`` returned a stale, unloaded handle). Now it
        goes through ``self._registry.unload(name)`` (busy-check
        honoured) + ``self._registry.unregister(name)`` (so the stale
        slot is cleared), mirroring ``_change_model_unload_phase``.
        """
        import time
        from unittest.mock import MagicMock

        mm, _app = _make_mm_with_mock_registry()
        del mm._evict_lru_model  # restore real method (helper stubs it)
        # Three entries — whisper is the oldest (timestamp in the past).
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100.0,  # oldest
            "parakeet": now - 10.0,
            "qwen": now,
        }
        # registry is a MagicMock — registry.unload / unregister both
        # succeed by default. We assert on the call args below.
        mm._registry.get = MagicMock(return_value=MagicMock(name="oldest-engine"))

        mm._evict_lru_model()

        # Oldest backend was unloaded + unregistered via the registry.
        mm._registry.unload.assert_called_once_with("whisper")
        mm._registry.unregister.assert_called_once_with("whisper")
        # Oldest backend removed from tracking.
        assert "whisper" not in mm._model_access_times
        assert "parakeet" in mm._model_access_times
        assert "qwen" in mm._model_access_times

    def test_eviction_respects_busy_check(self):
        """If the registry reports the oldest backend as busy
        (``registry.unload`` raises ``RuntimeError``), eviction MUST
        skip the backend rather than tearing it down mid-transcription.

        Pre-fix: ``_evict_lru_model`` called ``engine.unload()``
        directly, bypassing ``registry.unload``'s busy-check — a
        concurrent transcription would have its model freed
        mid-flight, crashing the C-level ctranslate2 / torch call.
        """
        import time

        mm, _app = _make_mm_with_mock_registry()
        del mm._evict_lru_model  # restore real method
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100.0,  # oldest, but busy
            "parakeet": now - 10.0,
            "qwen": now,
        }
        # registry.unload raises RuntimeError (busy-check refused).
        mm._registry.unload.side_effect = RuntimeError("cannot unload busy backend: whisper")

        mm._evict_lru_model()

        # The busy backend was NOT torn down — unload raised, so
        # ``unregister`` MUST NOT be called (otherwise we'd leave a
        # half-torn-down backend in the registry).
        mm._registry.unload.assert_called_once_with("whisper")
        mm._registry.unregister.assert_not_called()
        # Tracking dict is unchanged — eviction was skipped, NOT
        # partially applied. (The del happens AFTER the unload block
        # in the implementation, so a RuntimeError return leaves the
        # entry in place.)
        assert "whisper" in mm._model_access_times
        assert "parakeet" in mm._model_access_times
        assert "qwen" in mm._model_access_times

    def test_eviction_survives_engine_without_unload_method(self):
        """Eviction must not raise even if ``registry.unload`` /
        ``registry.unregister`` / ``release_gpu_memory`` encounter
        non-busy errors. The implementation wraps each step in
        try/except so a partial failure (e.g. a backend whose
        ``unload()`` raised a non-RuntimeError) doesn't leak an
        exception to the caller (``load_background`` /
        ``change_model``) which would mask the success of the load
        itself, AND still removes the entry from the LRU tracking so
        a subsequent ``_ensure_engine`` constructs a fresh engine.

        Previously this test set up an engine without ``unload()`` to
        exercise the ``hasattr`` guard in the direct-call path. With
        the registry-mediated unload, ``registry.unload`` (a MagicMock
        by default) swallows backend errors internally, so we
        explicitly make it raise a non-RuntimeError to exercise the
        new ``except Exception`` branch (RuntimeError is reserved for
        the busy-check skip path — see ``test_eviction_respects_busy_check``).
        """
        import time

        mm, _app = _make_mm_with_mock_registry()
        del mm._evict_lru_model  # restore real method (helper stubs it)
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100.0,
            "parakeet": now - 10.0,
            "qwen": now,
        }
        # Make registry.unload raise a NON-RuntimeError (e.g. backend's
        # unload() raised ValueError). Eviction must NOT raise — it
        # logs + continues to unregister + remove from tracking.
        # (RuntimeError is the busy-check skip path — not exercised
        # here.)
        mm._registry.unload.side_effect = ValueError("backend unload crashed")

        # Must NOT raise.
        mm._evict_lru_model()

        # Tracking still cleared — eviction is best-effort, a
        # backend-unload crash doesn't leave the entry pinned forever
        # (the registry unregister + tracking removal still happen so
        # a subsequent _ensure_engine constructs a fresh engine).
        assert "whisper" not in mm._model_access_times


# ── LRU model eviction (split from the former review-round catch-all
# tests/test_remaining_fixes.py) ──────────────────────────────────────


class TestLRUModelEviction:
    """PERF-015: Verify LRU model eviction in ModelManager."""

    def test_evict_method_exists(self):
        """ModelManager should have _evict_lru_model and touch_model methods."""
        from voice_typer.server.model_manager import ModelManager

        assert hasattr(ModelManager, "_evict_lru_model")
        assert hasattr(ModelManager, "touch_model")

    def test_no_eviction_below_limit(self):
        """Eviction should not happen when models <= _MAX_LOADED_MODELS."""
        from voice_typer.server.model_manager import ModelManager

        mm = ModelManager.__new__(ModelManager)
        mm._model_access_times = {"whisper": 1.0, "qwen": 2.0}
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)
        mm._MAX_LOADED_MODELS = 2
        mm._registry = MagicMock()
        # Should not try to unload anything
        mm._evict_lru_model()
        mm._registry.get.assert_not_called()

    def test_eviction_unloads_oldest(self):
        """Eviction should unload the least recently used model."""
        import time

        from voice_typer.server.model_manager import ModelManager

        mm = ModelManager.__new__(ModelManager)
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100,  # oldest
            "qwen": now - 10,
            "parakeet": now,
        }
        mm._MAX_LOADED_MODELS = 2
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)

        # Eviction unloads via the registry (so the busy-check in
        # ``AsrBackendRegistry.unload`` is honoured) and then unregisters
        # the backend + drops it from the LRU tracking.
        mm._registry = MagicMock()

        mm._evict_lru_model()
        # Should have unloaded the oldest (whisper) through the registry.
        mm._registry.unload.assert_called_once_with("whisper")
        # Should have removed the oldest from access times
        assert "whisper" not in mm._model_access_times

    def test_touch_updates_timestamp(self):
        """touch_model should update the access timestamp."""
        import time

        from voice_typer.server.model_manager import ModelManager

        mm = ModelManager.__new__(ModelManager)
        mm._model_access_times = {}
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)

        before = time.monotonic()
        mm.touch_model("whisper")
        after = time.monotonic()

        assert "whisper" in mm._model_access_times
        assert before <= mm._model_access_times["whisper"] <= after
