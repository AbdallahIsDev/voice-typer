"""UE-11 + UE-48 regression tests for ModelManager.

UE-11: ``set_active_backend`` must mirror ``change_model``'s deferral
pattern — when the user is recording OR the transcribe thread holds
the busy event (mid-transcription), the request is captured in
``_pending_backend_change`` (sibling to ``_pending_model_change``)
and applied on the next ``apply_pending_model_change`` call (invoked
from ``recording_controller.start`` before the new recording begins).
Without this guard, ``set_active_backend`` unconditionally ran the
unload phase mid-transcription, unloading the ctranslate2 model from
underneath the in-flight transcribe thread (crash / heap corruption /
stuck thread — see UE-11 in review.md).

``ensure_active_engine_loaded`` must reject the request when
the active backend is busy (inside
``transcribe_with_fallback`` on another thread — e.g. a stuck
ctranslate2 call). The rejection causes ``recording_controller.start``
to fall through to the ``fallback_to_whisper`` path, which loads a
SEPARATE Whisper backend rather than piling up on the stuck backend's
ctranslate2 internal lock. ``force_unload_active`` is the watchdog's
escalation path — it tears down the stuck model's GPU resources AND
clears the busy flag so the next dictation isn't rejected.

These tests pin the contract:

UE-11 (set_active_backend deferral):
1. ``set_active_backend`` defers when ``recorder.recording`` is True.
2. ``set_active_backend`` defers when ``_busy_event`` is not set
   (busy).
3. Deferred request captures ``_pending_backend_change`` + persists
   config + notifies + returns WITHOUT unloading the old backend.
4. ``set_active_backend`` does NOT defer when not recording + not
   busy (the existing immediate-apply path is preserved).
5. ``apply_pending_model_change`` applies a deferred backend change
   (clears the field + re-invokes ``set_active_backend``).
6. ``apply_pending_model_change`` applies BOTH a deferred model change
   AND a deferred backend change if both are set.
7. ``apply_pending_model_change`` returns False when neither is set
   (no-op).
8. ``apply_pending_model_change`` returns True when at least one is
   set.
9. Both pending fields are cleared BEFORE either apply runs (crash
   safety — no re-fire on the next recording).

UE-48 (ensure_active_engine_loaded busy rejection):
10. ``ensure_active_engine_loaded`` returns None when the active
    backend is busy.
11. The rejection sets ``_pending_dictation = True`` so the user's
    F2 press is queued.
12. ``ensure_active_engine_loaded`` does NOT reject when the active
    backend is not busy (the existing path is preserved).
13. The busy-check is defensive — a registry that raises during
    ``is_busy`` does NOT crash ``ensure_active_engine_loaded``.

UE-48 (force_unload_active):
14. ``force_unload_active`` drops the active backend's registry slot
    (``registry.unregister``) so the next load constructs a FRESH
    engine instance.
15. ``force_unload_active`` does NOT call ``backend.unload()`` — the
    stuck thread may still be inside the engine's C-level call and
    destroying it there would use-after-free.
16. ``force_unload_active`` calls ``release_gpu_memory()``.
17. ``force_unload_active`` calls ``registry.force_clear_busy`` so
    the next dictation isn't rejected.
18. ``force_unload_active`` is best-effort — never raises even if
    every layer fails.
19. ``force_unload_active`` is idempotent — calling it twice doesn't
    raise.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.model_manager import ModelManager

# ── Test fixtures ─────────────────────────────────────────────────────


class _Config:
    """Minimal config stub matching ``ConfigProtocol``'s fields.

    Tracks ``save()`` calls so tests can assert that the deferred
    path persists the config (matching ``change_model``'s
    setattr-before-deferral pattern).
    """

    def __init__(self, asr_backend: str = "whisper", model_size: str = "tiny.en") -> None:
        self.asr_backend = asr_backend
        self.model_size = model_size
        self.device = "cpu"
        self.language = "en"
        self.beam_size = 1
        self.best_of = 1
        self.condition_on_previous_text = False
        self.save_calls: list[bool] = []

    def save(self) -> bool:
        self.save_calls.append(True)
        return True


def _make_mm(
    *,
    asr_backend: str = "whisper",
    recording: bool = False,
    busy: bool = False,
) -> tuple[ModelManager, MagicMock, _Config, AsrBackendRegistry]:
    """Build a ModelManager with a mock app + real registry.

    Returns ``(mm, app, config, registry)``. The mock app has:

    * ``recorder.recording`` set to ``recording``
    * ``_busy_event`` set to a threading.Event — ``set()`` if NOT
      busy, ``clear()`` if busy (mirrors the
      ``not _busy_event.is_set()`` check in ``change_model``)
    * ``config`` = the real ``_Config`` stub
    * ``tray.notify`` = a MagicMock so we can assert on the
      "will change after current recording" notification

    The registry is a REAL :class:`AsrBackendRegistry` so the
    busy-flag API works end-to-end (no mocking of
    ``is_busy`` / ``set_busy`` / etc.).
    """
    config = _Config(asr_backend=asr_backend)
    registry = AsrBackendRegistry(config)

    app = MagicMock(name="app")
    app.config = config
    app.recorder.recording = recording
    busy_event = threading.Event()
    if not busy:
        busy_event.set()  # is_set() == True means NOT busy
    # else: leave cleared → is_set() == False means busy
    app._busy_event = busy_event
    app._config_mutation_lock = threading.RLock()
    app._shutting_down = False
    app._pending_dictation = False

    mm = ModelManager.__new__(ModelManager)
    mm._app = app
    mm._registry = registry
    mm._model_change_lock = threading.RLock()
    mm._lazy_init_lock = threading.Lock()
    mm._model_lru_lock = threading.Lock()
    mm._model_access_times = {}
    mm._pending_model_change = None
    mm._pending_backend_change = None
    mm._pending_dictation = False
    mm._model_load_attempted = False
    mm._model_load_thread = None
    mm._idle_unload_lock = threading.Lock()
    mm._idle_unload_timer = None

    return mm, app, config, registry


# set_active_backend deferral ──────────────────────────────


class TestSetActiveBackendDefersWhenBusy:
    """UE-11: ``set_active_backend`` must defer when the user is
    recording OR the transcribe thread holds the busy event."""

    def test_set_active_backend_defers_when_recording(self):
        """When ``recorder.recording`` is True, ``set_active_backend``
        MUST NOT run the unload phase — unloading the ctranslate2 model
        mid-inference crashes / corrupts / hangs the transcribe
        thread."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=True, busy=False)
        # Pre-register a whisper engine so we can assert it was NOT
        # unloaded by the deferred path.
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine

        # Stub _ensure_engine so it doesn't try to actually import
        # qwen_engine.
        mm._ensure_engine = MagicMock()

        mm.set_active_backend("qwen")

        # contract: the request was deferred.
        assert mm._pending_backend_change == "qwen", (
            "UE-11: set_active_backend must capture the requested backend "
            "in _pending_backend_change when recording, so the next "
            "apply_pending_model_change call can re-apply it. Pre-fix, "
            "set_active_backend unconditionally ran the unload phase "
            "mid-transcription."
        )
        # Config was persisted (matching change_model's setattr-before-
        # deferral pattern).
        assert config.save_calls, (
            "UE-11: set_active_backend must persist the new backend via "
            "config.save() even when deferring — a crash mid-recording "
            "shouldn't lose the user's intent."
        )
        assert config.asr_backend == "qwen"
        # The OLD backend was NOT unloaded (the whole point of the
        # deferral).
        (
            whisper_engine.unload.assert_not_called(),
            (
                "UE-11: set_active_backend must NOT call _change_model_unload_phase "
                "when recording — unloading the ctranslate2 model mid-inference "
                "crashes the transcribe thread."
            ),
        )
        # The user was notified.
        app.tray.notify.assert_called()
        notify_args = app.tray.notify.call_args
        assert "qwen" in str(notify_args), (
            "UE-11: set_active_backend must notify the user that the backend will change after the current recording."
        )

    def test_set_active_backend_defers_when_busy_event_not_set(self):
        """When ``_busy_event.is_set()`` is False (busy — transcribe
        thread is running), ``set_active_backend`` MUST defer."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=True)
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine

        mm._ensure_engine = MagicMock()

        mm.set_active_backend("parakeet")

        assert mm._pending_backend_change == "parakeet", (
            "UE-11: set_active_backend must defer when _busy_event is not set (transcribe thread is running mid-call)."
        )
        whisper_engine.unload.assert_not_called()

    def test_set_active_backend_does_not_defer_when_not_busy(self):
        """When NOT recording AND not busy, ``set_active_backend`` MUST
        apply immediately (the existing immediate-apply path is
        preserved — UE-11 only adds the deferral for the busy case)."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine

        # Stub _ensure_engine so it doesn't try to actually import
        # qwen_engine — instead, register a mock qwen engine.
        def fake_ensure(backend_name):
            qwen_engine = MagicMock()
            qwen_engine.is_loaded = True
            registry.register(backend_name, qwen_engine)

        mm._ensure_engine = fake_ensure
        # Stub load_active to return a truthy backend without loading.
        registry.load_active = lambda progress_callback=None: registry.get("qwen")
        # Stub touch_model + _evict_lru_model (they touch LRU state).
        mm.touch_model = lambda name: None
        mm._evict_lru_model = lambda: None

        mm.set_active_backend("qwen")
        # Join the background BackendChange thread so its
        # ``asr_backend_ready`` publish completes inside this test
        # (doesn't leak into a later test's event_bus subscription
        # window).
        bg_thread = getattr(mm, "_backend_change_thread", None)
        if bg_thread is not None:
            bg_thread.join(timeout=5.0)

        # NOT deferred.
        assert mm._pending_backend_change is None, (
            "UE-11: set_active_backend must NOT defer when not recording "
            "and not busy — the existing immediate-apply path is preserved."
        )
        # Config was set + saved.
        assert config.asr_backend == "qwen"
        # Whisper engine WAS unloaded (immediate-apply path).
        whisper_engine.unload.assert_called()

    def test_set_active_backend_noop_when_already_active_is_unaffected(self):
        """UE-11 must NOT break the existing no-op short-circuit when
        the requested backend equals the current backend."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        # No save_calls spy needed — the no-op must NOT call save().
        config.save_calls.clear()

        mm.set_active_backend("whisper")

        assert mm._pending_backend_change is None
        assert config.save_calls == [], (
            "UE-11: set_active_backend must NOT call config.save() when "
            "the backend is already active (no-op short-circuit)."
        )


class TestApplyPendingBackendChange:
    """UE-11: ``apply_pending_model_change`` must apply a deferred
    backend change captured by ``set_active_backend``."""

    def test_apply_pending_backend_change_invokes_set_active_backend(self):
        """When ``_pending_backend_change`` is set,
        ``apply_pending_model_change`` must re-invoke the blocking
        backend-switch path to run the unload/load cycle (now that
        the app is no longer busy).

        AB-10 design note: ``apply_pending_model_change`` calls
        ``_set_active_backend_blocking`` (NOT the public non-blocking
        ``set_active_backend``) because the caller —
        ``recording_controller._start_dictation`` — needs the model
        fully loaded BEFORE the recorder starts capturing audio.
        The public non-blocking variant would return immediately
        and the recorder would start with the OLD (unloaded) engine.
        See ``model_manager.py:1341`` for the design rationale.
        """
        mm, app, config, registry = _make_mm(asr_backend="qwen", recording=False, busy=False)
        # Simulate a deferred backend change captured during a previous
        # recording. config.asr_backend was already set to "qwen" by
        # the deferred path (matching set_active_backend's
        # setattr-before-deferral pattern); we now want to apply the
        # full unload/load cycle.
        mm._pending_backend_change = "whisper"

        # Spy: track _set_active_backend_blocking calls (the
        # BLOCKING variant — see design note in the docstring above).
        set_backend_calls: list[str] = []
        original_set_active_backend_blocking = mm._set_active_backend_blocking

        def spy_set_active_backend(backend):
            set_backend_calls.append(backend)
            # Call the real method to actually apply the change.
            original_set_active_backend_blocking(backend)

        mm._set_active_backend_blocking = spy_set_active_backend

        # Stub _ensure_engine so it doesn't try to actually import
        # whisper. Pre-register a whisper engine so the immediate-apply
        # path can proceed.
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine
        mm._ensure_engine = MagicMock()
        registry.load_active = lambda progress_callback=None: registry.get("whisper")
        mm.touch_model = lambda name: None
        mm._evict_lru_model = lambda: None

        result = mm.apply_pending_model_change()

        assert result is True, (
            "UE-11: apply_pending_model_change must return True when a deferred backend change was applied."
        )
        assert set_backend_calls == ["whisper"], (
            "UE-11: apply_pending_model_change must re-invoke "
            "_set_active_backend_blocking (the AB-10 blocking variant) "
            "with the deferred backend name."
        )
        # The pending field was cleared (no re-fire on the next recording).
        assert mm._pending_backend_change is None

    def test_apply_pending_model_change_applies_both_model_and_backend(self):
        """When BOTH ``_pending_model_change`` AND
        ``_pending_backend_change`` are set, both must be applied
        (model FIRST, then backend — so an explicit
        ``set_active_backend("whisper")`` overrides the model-change-
        implied backend).

        AB-10 design note: ``apply_pending_model_change`` calls the
        BLOCKING variants (``_change_model_blocking`` and
        ``_set_active_backend_blocking``) — NOT the public non-blocking
        ``change_model`` / ``set_active_backend`` — because the caller
        (``recording_controller._start_dictation``) needs the model
        fully loaded BEFORE the recorder starts capturing audio.
        See ``model_manager.py:1341`` for the design rationale.
        """
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        mm._pending_model_change = "medium.en"
        mm._pending_backend_change = "whisper"

        # Spy: track apply order on the BLOCKING variants (see
        # design note in the docstring above).
        apply_calls: list[str] = []
        mm._change_model_blocking = lambda model_size: apply_calls.append(("_change_model_blocking", model_size))
        mm._set_active_backend_blocking = lambda backend: apply_calls.append(("_set_active_backend_blocking", backend))

        result = mm.apply_pending_model_change()

        assert result is True
        assert apply_calls == [
            ("_change_model_blocking", "medium.en"),
            ("_set_active_backend_blocking", "whisper"),
        ], (
            "UE-11: apply_pending_model_change must apply the model "
            "change FIRST (because the blocking model-change variant "
            "re-evaluates the backend from model_size) and the backend "
            "change SECOND (so an explicit set_active_backend overrides "
            "the model-change-implied backend). Both apply via the AB-10 "
            "BLOCKING variants, not the public non-blocking wrappers."
        )
        # Both pending fields cleared.
        assert mm._pending_model_change is None
        assert mm._pending_backend_change is None

    def test_apply_pending_model_change_clears_both_before_apply(self):
        """Both pending fields must be cleared BEFORE either apply runs
        — a crash mid-apply must not leave a stale request that
        re-fires on the next recording.

        AB-10 design note: ``apply_pending_model_change`` invokes the
        BLOCKING variants (``_change_model_blocking`` and
        ``_set_active_backend_blocking``) — NOT the public non-blocking
        wrappers — because the caller needs the model fully loaded
        before recording starts. The spies below target the blocking
        variants accordingly. See ``model_manager.py:1341`` for the
        design rationale.
        """
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        mm._pending_model_change = "medium.en"
        mm._pending_backend_change = "whisper"

        # Make _change_model_blocking raise to simulate a crash mid-apply.
        def crashing_change_model_blocking(model_size):
            raise RuntimeError("simulated crash mid-apply")

        mm._change_model_blocking = crashing_change_model_blocking
        mm._set_active_backend_blocking = MagicMock()

        with pytest.raises(RuntimeError, match="simulated crash"):
            mm.apply_pending_model_change()

        # Both fields were cleared BEFORE the apply ran, so the crash
        # doesn't leave a stale request.
        assert mm._pending_model_change is None, (
            "UE-11: apply_pending_model_change must clear _pending_model_change "
            "BEFORE invoking _change_model_blocking — a crash mid-apply must "
            "not leave a stale request that re-fires on the next recording."
        )
        assert mm._pending_backend_change is None, (
            "UE-11: apply_pending_model_change must clear _pending_backend_change "
            "BEFORE invoking _change_model_blocking — even if _change_model_blocking "
            "crashes, the backend change must not re-fire on the next recording."
        )
        # _set_active_backend_blocking was NOT called (the model change
        # crashed first).
        mm._set_active_backend_blocking.assert_not_called()

    def test_apply_pending_model_change_noop_when_neither_set(self):
        """When neither ``_pending_model_change`` nor
        ``_pending_backend_change`` is set, return False (no-op)."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        mm._pending_model_change = None
        mm._pending_backend_change = None

        result = mm.apply_pending_model_change()

        assert result is False, (
            "UE-11: apply_pending_model_change must return False when neither pending field is set (no-op)."
        )

    def test_apply_pending_model_change_handles_missing_backend_field(self):
        """Defensive: legacy test fixtures that construct ModelManager
        via ``__new__`` and don't set ``_pending_backend_change`` must
        NOT crash. The ``getattr(..., None)`` defensive read preserves
        their behaviour (no AttributeError)."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        # Simulate a legacy fixture that skipped setting the new field.
        del mm._pending_backend_change
        mm._pending_model_change = None

        # Must NOT raise AttributeError.
        result = mm.apply_pending_model_change()
        assert result is False

    def test_apply_pending_model_change_applies_only_backend_when_model_none(self):
        """When only ``_pending_backend_change`` is set (no
        ``_pending_model_change``), only the blocking backend-switch
        is called (the blocking model-change is NOT called).

        AB-10 design note: ``apply_pending_model_change`` invokes the
        BLOCKING variants — NOT the public non-blocking wrappers —
        because the caller needs the model fully loaded before
        recording starts. The spies below target the blocking variants
        accordingly. See ``model_manager.py:1341`` for the design
        rationale.
        """
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        mm._pending_model_change = None
        mm._pending_backend_change = "whisper"

        change_model_calls: list[str] = []
        set_backend_calls: list[str] = []
        mm._change_model_blocking = lambda model_size: change_model_calls.append(model_size)
        mm._set_active_backend_blocking = lambda backend: set_backend_calls.append(backend)

        result = mm.apply_pending_model_change()

        assert result is True
        assert change_model_calls == [], (
            "UE-11: apply_pending_model_change must NOT call _change_model_blocking when _pending_model_change is None."
        )
        assert set_backend_calls == ["whisper"]


# ensure_active_engine_loaded busy rejection ───────────────


class TestEnsureActiveEngineLoadedBusyRejection:
    """``ensure_active_engine_loaded`` must reject when the
    active backend is busy."""

    def test_rejects_when_active_backend_is_busy(self):
        """When the active backend's busy flag is set (inside
        ``transcribe_with_fallback`` on another thread),
        ``ensure_active_engine_loaded`` must return None and queue the
        dictation via ``_pending_dictation = True``."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        # Mark the active backend as busy (simulating a stuck
        # transcription on another thread).
        registry.set_busy("parakeet")

        result = mm.ensure_active_engine_loaded()

        assert result is None, (
            "ensure_active_engine_loaded must return None when "
            "the active backend is busy (stuck transcription). Returning "
            "None causes recording_controller.start to fall through to "
            "fallback_to_whisper, which loads a SEPARATE backend rather "
            "than piling up on the stuck backend's ctranslate2 lock."
        )
        assert mm._pending_dictation is True, (
            "ensure_active_engine_loaded must set "
            "_pending_dictation = True when rejecting so the user's F2 "
            "press is queued and re-tried after the watchdog recovers."
        )

    def test_does_not_reject_when_active_backend_not_busy(self):
        """When the active backend is NOT busy,
        ``ensure_active_engine_loaded`` must proceed with the existing
        lazy-init path (no rejection)."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        # Stub _ensure_engine so it doesn't actually construct an
        # engine — we just want to verify the busy-check didn't reject.
        mm._ensure_engine = MagicMock()
        mm.touch_model = MagicMock()
        # Make the registry return a non-None engine so the method
        # completes the lazy-init path successfully.
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)

        result = mm.ensure_active_engine_loaded()

        # Not rejected (returns the active transcriber, not None).
        assert result is not None, (
            "ensure_active_engine_loaded must NOT reject when the "
            "active backend is not busy — the existing lazy-init path is "
            "preserved."
        )
        assert mm._pending_dictation is False

    def test_busy_check_is_defensive_against_registry_errors(self):
        """If ``registry.is_busy`` raises (e.g. a mock registry that
        doesn't implement the method),
        ``ensure_active_engine_loaded`` must NOT crash — it must log a
        debug message and continue with the existing path."""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        # Make is_busy raise.
        registry.is_busy = MagicMock(side_effect=RuntimeError("simulated registry error"))
        # Stub the rest so the method can complete successfully.
        mm._ensure_engine = MagicMock()
        mm.touch_model = MagicMock()
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)

        # Must NOT raise.
        result = mm.ensure_active_engine_loaded()

        # The busy-check failed open (defensive) — the method proceeded
        # with the existing lazy-init path.
        assert result is not None, (
            "ensure_active_engine_loaded must be defensive "
            "against registry.is_busy errors — the busy-check must "
            "fail OPEN (continue with the existing path), not crash."
        )


# force_unload_active ──────────────────────────────────────


class TestForceUnloadActive:
    """``force_unload_active`` is the watchdog's escalation
    path — ejects the stuck backend from the registry (so the next
    dictation constructs a FRESH engine instance) AND clears the busy
    flag so the next dictation isn't rejected. It must NOT destroy the
    engine object itself: the stuck thread may still be inside its
    C-level call."""

    def test_drops_registry_slot(self):
        """``force_unload_active`` must drop the active backend's
        registry slot (via ``registry.unregister``) so a subsequent
        ``_ensure_engine`` constructs a fresh instance instead of
        reusing the stuck one."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        assert registry.get("parakeet") is None, (
            "force_unload_active must drop the _backends[name] slot "
            "so the next dictation cannot enter the same engine instance "
            "the stuck thread still occupies."
        )

    def test_does_not_destroy_live_engine(self):
        """``force_unload_active`` must NOT call ``backend.unload()`` —
        a forced recovery fires while the worker thread may still be
        inside the engine's C-level call, and freeing CUDA tensors /
        ctranslate2 handles under it crashes with use-after-free. The
        engine is orphaned instead (the stuck thread keeps its
        reference)."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        (
            parakeet_engine.unload.assert_not_called(),
            (
                "force_unload_active must NOT destroy the engine object "
                "— the stuck thread may still be inside its C-level call; the "
                "backend is ejected from the registry instead."
            ),
        )

    def test_calls_force_clear_busy(self):
        """``force_unload_active`` must call
        ``registry.force_clear_busy`` so the next dictation isn't
        rejected by the UE-48 busy-check."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)
        # Mark the backend as busy (simulating a stuck transcription).
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        mm.force_unload_active()

        assert registry.is_busy("parakeet") is False, (
            "force_unload_active must clear the busy flag so the "
            "next ensure_active_engine_loaded isn't rejected. Without "
            "this, the busy flag would remain set forever (the stuck "
            "transcription never returned to clear it) and every "
            "subsequent dictation would be rejected + queued indefinitely."
        )

    def test_best_effort_never_raises(self):
        """``force_unload_active`` must NEVER raise — even if every
        layer (registry.unregister, release_gpu_memory,
        force_clear_busy) raises. The watchdog calls this from its own
        force-recover path; a raise here would mask the recovery state
        reset it has already performed."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        # Make every layer raise.
        registry.unregister = MagicMock(side_effect=RuntimeError("registry unregister failed"))
        registry.force_clear_busy = MagicMock(side_effect=RuntimeError("force_clear_busy failed"))
        # Patch release_gpu_memory to raise.
        import sys
        from types import ModuleType

        fake_asr_utils = ModuleType("voice_typer.server.asr_utils")
        fake_asr_utils.release_gpu_memory = MagicMock(side_effect=RuntimeError("gpu release failed"))
        original_module = sys.modules.get("voice_typer.server.asr_utils")
        sys.modules["voice_typer.server.asr_utils"] = fake_asr_utils
        try:
            # Must NOT raise.
            mm.force_unload_active()
        finally:
            if original_module is not None:
                sys.modules["voice_typer.server.asr_utils"] = original_module
            else:
                sys.modules.pop("voice_typer.server.asr_utils", None)

    def test_idempotent_calling_twice_does_not_raise(self):
        """``force_unload_active`` must be idempotent — calling it
        twice must NOT raise (the second call is a no-op on an already-
        unloaded backend)."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        # First call.
        mm.force_unload_active()
        # Second call (backend is already ejected).
        mm.force_unload_active()

        # No exception raised — the test passing is the assertion.
        # Verify the slot stayed dropped (sanity) and the engine object
        # was never destroyed.
        assert registry.get("parakeet") is None
        assert not parakeet_engine.unload.called

    def test_does_not_touch_config_asr_backend(self):
        """``force_unload_active`` must NOT touch ``config.asr_backend``
        — the next ``ensure_active_engine_loaded`` call will re-create
        + re-load the SAME backend (the watchdog's contract is "tear
        down the stuck model so the next dictation can load a fresh
        one", not "switch to a different backend")."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        assert config.asr_backend == "parakeet", (
            "force_unload_active must NOT touch config.asr_backend "
            "— the watchdog's contract is to tear down the stuck model, "
            "not to switch backends."
        )

    def test_does_not_call_tray_set_state(self):
        """``force_unload_active`` must NOT call ``tray.set_state`` —
        the watchdog has already set the tray to IDLE with the
        "recovered" message, and overwriting that with the TY-11
        "Idle — model unloaded" message would confuse the user."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        (
            app.tray.set_state.assert_not_called(),
            (
                "force_unload_active must NOT call tray.set_state — "
                "the watchdog has already set the tray state, and overwriting "
                "it would confuse the user (the recovery message is more "
                "specific than the TY-11 'Idle — model unloaded' message)."
            ),
        )


# +  integration: end-to-end stuck recovery ────────────


class TestStuckRecoveryIntegration:
    """UE-48 + UE-11 integration: simulate the watchdog's full stuck-
    recovery flow and verify the next dictation succeeds."""

    def test_stuck_recovery_flow_clears_busy_and_allows_next_dictation(self):
        """End-to-end: a backend gets stuck (busy flag set),
        ``ensure_active_engine_loaded`` rejects the next dictation,
        the watchdog calls ``force_unload_active``, and the NEXT
        ``ensure_active_engine_loaded`` call succeeds against a FRESH
        engine instance (slot dropped + busy flag cleared)."""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)
        fresh_engine = MagicMock()
        fresh_engine.is_loaded = True

        def fake_ensure_engine(backend_name):
            # Mirrors the real ``_ensure_engine`` short-circuit: only
            # constructs when the slot is empty.
            if registry.get(backend_name) is None:
                registry.register(backend_name, fresh_engine)

        mm._ensure_engine = fake_ensure_engine
        mm.touch_model = MagicMock()

        # Step 1: the transcribe thread enters transcribe_with_fallback
        # (via the registry wrapper), setting the busy flag.
        with registry.busy_context("parakeet"):
            assert registry.is_busy("parakeet") is True

            # Step 2: while the transcription is running, the user
            # presses F2. ensure_active_engine_loaded rejects.
            result = mm.ensure_active_engine_loaded()
            assert result is None
            assert mm._pending_dictation is True

        # Step 3: the transcribe thread is stuck (the busy_context
        # cleared the flag on exit, but in the real world a stuck
        # C-level ctranslate2 call never returns — so simulate that
        # by re-setting the flag without the context manager).
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        # Step 4: the watchdog's force-recover path fires and calls
        # force_unload_active.
        mm.force_unload_active()

        # Step 5: the busy flag is cleared — the next dictation can
        # proceed.
        assert registry.is_busy("parakeet") is False, (
            "UE-48 integration: after force_unload_active, the busy flag "
            "must be cleared so the next ensure_active_engine_loaded call "
            "isn't rejected."
        )

        # Step 6: the next ensure_active_engine_loaded call succeeds —
        # against a FRESH engine instance, not the ejected stuck one.
        mm._pending_dictation = False  # reset for the new attempt
        result = mm.ensure_active_engine_loaded()
        assert result is not None, (
            "UE-48 integration: after force_unload_active clears the busy "
            "flag, the next ensure_active_engine_loaded call must succeed "
            "(not be rejected by the busy-check)."
        )
        assert result is fresh_engine, (
            "UE-48 integration: after force_unload_active drops the "
            "registry slot, the next dictation must be served by a FRESH "
            "engine instance — never the ejected stuck one."
        )
        assert result is not parakeet_engine
        assert mm._pending_dictation is False
