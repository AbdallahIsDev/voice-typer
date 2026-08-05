"""regression tests for ``voice_typer/server/model_manager.py``.

Pins the contract for five fixes shipped in the WM-FIX-P4 batch:

 (HIGH) — ``ensure_active_engine_loaded`` race    The ``backend = self._app.config.asr_backend`` read MUST happen
    INSIDE ``_lazy_init_lock`` and MUST re-validate ``config.asr_backend``
    after ``_ensure_engine`` so a concurrent ``_change_model_blocking``
    that rewrote config between the read and the lock acquisition does
    NOT produce a phantom VRAM engine for the stale backend name.

 (Medium) — ``start_background_load`` check-then-spawn race
 The liveness check + ``threading.Thread`` construction + assignment
    to ``_model_load_thread`` MUST be atomic (guarded by
    ``_model_load_spawn_lock``) so two concurrent callers don't both
    spawn a ModelLoad thread (leaking the first).

 (Medium) — unconditional ``asr_backend_ready`` on failure
  ``_change_model_blocking`` and ``_set_active_backend_blocking`` MUST
    publish ``asr_backend_ready`` ONLY on success. On failure, they
    MUST publish a separate ``asr_backend_load_failed`` event with
    ``{"backend": ..., "model_size": ..., "failure_reason": ...}``.
    The deferred branch (recording in progress) publishes NEITHER
    event (the load didn't happen).

 (Low) — exception log lacks backend/model context
 ``load_background``'s outer ``except Exception`` log MUST include
    ``backend`` + ``model`` so the crash trace is actionable.

 (Low) — ``load_background`` finally auto-starts on failure
 ``load_background``'s ``finally`` block MUST NOT auto-start a
    dictation (via ``_schedule_timer(0, _start_dictation)``) when the
    load FAILED or CRASHED — the auto-start would loop on
    ``fallback_to_whisper`` and fail the same way, spamming the tray
    with ERROR state. ``_pending_dictation`` MUST be cleared on the
    failure / crash paths so the finally's ``if self._pending_dictation``
    check skips the auto-start.

These tests mock the heavy torch / transformers / huggingface_hub
dependencies (mirroring ``tests/test_model_manager.py``) so they run
headless on the Linux sandbox.
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

from voice_typer.server import event_bus
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.model_manager import ModelManager

# ── Shared fixtures ────────────────────────────────────────────────────


def _make_mm_with_mock_registry(
    *,
    backend_name: str = "whisper",
    model_size: str = "tiny.en",
) -> tuple[ModelManager, MagicMock, MagicMock]:
    """Build a ModelManager via the real ``__init__`` with a mock app,
    then swap the registry for a MagicMock (mirrors the helper in
    ``tests/test_model_manager.py``).

    Returns ``(mm, app, mock_registry)``. The real ``__init__`` runs so
    its wiring (locks, LRU state, ``_model_load_thread = None``,
    ``_model_load_spawn_lock``, etc.) is exercised. The registry is
    swapped because every method we assert on (``load_active``,
    ``create``, ``unload``...) is easier to verify via mocks.
    """
    app = MagicMock(name="app")
    app.config.asr_backend = backend_name
    app.config.model_size = model_size
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.model_idle_unload_minutes = 0
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    app._config_mutation_lock = threading.RLock()
    # Ensure change_model is NOT deferred —
    # ``_change_model_setattr_phase`` checks ``recorder.recording``
    # (must be False) and ``_busy_event.is_set()`` (must return True
    # = not busy).
    app.recorder.recording = False
    app._busy_event = MagicMock()
    app._busy_event.is_set.return_value = True
    app.config.save.return_value = True

    mm = ModelManager(app)

    engine = MagicMock(name="engine")
    engine.is_loaded = True
    engine.device_info = f"{backend_name}/cpu"

    mock_registry = MagicMock(name="registry")
    mock_registry.active_name = backend_name
    mock_registry.get_active.return_value = engine
    mock_registry.get.return_value = engine
    mock_registry.load_active.return_value = engine  # truthy → success
    mock_registry.load_with_fallback.return_value = engine
    mock_registry.available_backends = [backend_name]
    mm._registry = mock_registry

    mm._ensure_engine = MagicMock()
    mm._evict_lru_model = MagicMock()
    mm.cancel_idle_unload_timer = MagicMock()

    return mm, app, mock_registry


def _make_mm_with_real_registry(
    *,
    asr_backend: str = "whisper",
    model_size: str = "tiny.en",
    recording: bool = False,
    busy: bool = False,
) -> tuple[ModelManager, MagicMock, object, AsrBackendRegistry]:
    """Build a ModelManager with a mock app + REAL ``AsrBackendRegistry``.

    Used by / tests that need the real registry's busy-flag
    API and ``get`` / ``register`` semantics (no mocking of those).
    Mirrors the helper in ``tests/test_model_manager_busy_guard.py``.
    """
    config = _Config(asr_backend=asr_backend, model_size=model_size)
    registry = AsrBackendRegistry(config)

    app = MagicMock(name="app")
    app.config = config
    app.recorder.recording = recording
    busy_event = threading.Event()
    if not busy:
        busy_event.set()  # is_set() == True means NOT busy
    app._busy_event = busy_event
    app._config_mutation_lock = threading.RLock()
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()

    mm = ModelManager.__new__(ModelManager)
    mm._app = app
    mm._registry = registry
    mm._model_change_lock = threading.RLock()
    mm._lazy_init_lock = threading.Lock()
    mm._model_load_spawn_lock = threading.Lock()
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


class _Config:
    """Minimal config stub matching ``ConfigProtocol``'s fields."""

    def __init__(self, asr_backend: str = "whisper", model_size: str = "tiny.en") -> None:
        self.asr_backend = asr_backend
        self.model_size = model_size
        self.device = "cpu"
        self.language = "en"
        self.beam_size = 1
        self.best_of = 1
        self.condition_on_previous_text = False
        self.model_idle_unload_minutes = 0
        self.save_calls: list[bool] = []

    def save(self) -> bool:
        self.save_calls.append(True)
        return True


# ── : ensure_active_engine_loaded race ───────────────────────────


class TestEnsureActiveEngineLoadedRace:
    """(HIGH): the ``backend = config.asr_backend`` read MUST
    happen INSIDE ``_lazy_init_lock`` and MUST re-validate after
    ``_ensure_engine`` so a concurrent ``_change_model_blocking`` that
    rewrote config between the read and the lock acquisition does NOT
    produce a phantom VRAM engine for the stale backend name."""

    def test_backend_read_happens_inside_lazy_init_lock(self):
        """Source guard: the ``backend = self._app.config.asr_backend``
        assignment MUST appear INSIDE the ``with self._lazy_init_lock:``
        block in ``ensure_active_engine_loaded`` — NOT before it.

        Pre-fix, the read was OUTSIDE the lock, which let a concurrent
        ``_change_model_blocking`` rewrite ``config.asr_backend``
        between the read and the lock acquisition.
        """
        import inspect
        import re

        src = inspect.getsource(ModelManager.ensure_active_engine_loaded)
        # Find the lock acquisition line.
        lock_match = re.search(r"with self\._lazy_init_lock:", src)
        assert lock_match is not None, ": ensure_active_engine_loaded must acquire _lazy_init_lock."
        lock_line_idx = lock_match.start()
        # Find the CODE-form of the backend read (indented inside the
        # ``with`` block, NOT the comment-form which uses backticks).
        # The code form is ``backend = self._app.config.asr_backend``
        # followed by a newline + ``engine = ...`` (the next
        # statement). The comment form uses backticks around the
        # attribute name.
        code_read_match = re.search(
            r"backend = self\._app\.config\.asr_backend\n\s+engine = ",
            src,
        )
        assert code_read_match is not None, (
            "ensure_active_engine_loaded must read "
            "``backend = self._app.config.asr_backend`` followed by "
            "``engine = self._registry.get(backend)`` (the code form)."
        )
        backend_read_idx = code_read_match.start()
        assert backend_read_idx > lock_line_idx, (
            "``backend = self._app.config.asr_backend`` MUST be "
            "INSIDE the ``with self._lazy_init_lock:`` block in "
            "ensure_active_engine_loaded. Pre-fix, the read was "
            "OUTSIDE the lock, which let a concurrent "
            "_change_model_blocking rewrite config.asr_backend between "
            "the read and the lock acquisition — producing a phantom "
            "VRAM engine for the stale backend name."
        )

    def test_revalidates_backend_after_ensure_engine(self):
        """Source guard: after ``_ensure_engine(backend)``, the method
        MUST re-read ``config.asr_backend`` and reconcile if it has
        changed. The re-validation is the actual mitigation (the lock
        narrows the window, the re-validation closes it)."""
        import inspect

        src = inspect.getsource(ModelManager.ensure_active_engine_loaded)
        assert "current_backend = self._app.config.asr_backend" in src, (
            "ensure_active_engine_loaded MUST re-validate "
            "``config.asr_backend`` after _ensure_engine by reading "
            "``current_backend`` and reconciling if it differs from "
            "the captured ``backend``. Without this re-validation, a "
            "concurrent _change_model_blocking (which does NOT take "
            "_lazy_init_lock) could still rewrite config between the "
            "read and _ensure_engine, producing a phantom engine."
        )
        assert "if current_backend != backend:" in src, (
            "ensure_active_engine_loaded MUST branch on "
            "``current_backend != backend`` and re-route to the "
            "current backend when a concurrent changer rewrote config."
        )

    def test_re_routes_when_config_changes_during_lock_acquisition(self):
        """End-to-end: simulate a concurrent ``_change_model_blocking``
        that rewrites ``config.asr_backend`` between the (now
        inside-lock) read and ``_ensure_engine``. The fix MUST re-route
        to the current backend so the phantom engine for the stale
        backend name is NOT returned to the caller (it's constructed
        but abandoned — the next change cycle unloads it).

        Scenario:
        1. config.asr_backend starts as "whisper".
        2. ensure_active_engine_loaded reads backend = "whisper" (inside lock).
        3. registry.get("whisper") returns None.
        4. _ensure_engine("whisper") is called — constructs a phantom
           whisper engine (unavoidable: the fix can't look into the
           future to know config is about to change).
        5. Simulate the concurrent changer: config.asr_backend flips
           to "parakeet" and registry now has a parakeet engine.
        6. The fix re-validates config.asr_backend, sees "parakeet",
           re-routes, and returns the parakeet engine (NOT the phantom
           whisper engine).

        Pre-fix (read OUTSIDE the lock), the method would have returned
        the phantom whisper engine while config said "parakeet" — the
        caller would transcribe against a backend the rest of the app
        had abandoned.
        """
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        # The phantom whisper engine constructed by _ensure_engine is
        # never observed — the fix aborts the swap before transcription
        # can run against the abandoned backend.
        # The real parakeet engine loaded by the concurrent changer.
        parakeet_engine = MagicMock(name="parakeet-engine")
        parakeet_engine.is_loaded = True

        # Simulate the concurrent changer: after the first
        # _ensure_engine("whisper") call, config flips to "parakeet"
        # and the parakeet engine appears in the registry.
        config_state = {"asr_backend": "whisper"}

        class _FlippingConfig:
            def __init__(self):
                self.model_size = "tiny.en"
                self.device = "cpu"
                self.language = "en"
                self.beam_size = 1
                self.best_of = 1
                self.condition_on_previous_text = False

            @property
            def asr_backend(self):
                return config_state["asr_backend"]

            @property
            def model_idle_unload_minutes(self):
                return 0

            def save(self):
                return True

        app.config = _FlippingConfig()

        # Track _ensure_engine calls.
        ensure_calls: list[str] = []

        def _track_ensure(backend_name):
            ensure_calls.append(backend_name)
            # Simulate the concurrent _change_model_blocking firing
            # DURING _ensure_engine("whisper"): config flips to
            # "parakeet" and the parakeet engine appears in the
            # registry. This is the race window the fix must handle.
            if backend_name == "whisper":
                config_state["asr_backend"] = "parakeet"
                # The phantom whisper engine is constructed.
                # (In real code, _ensure_engine would register it.)
                # We don't register it on the mock — we want
                # registry.get("whisper") to STILL return None so the
                # re-validation path is exercised. The phantom is
                # "leaked" but not returned.

        mm._ensure_engine = _track_ensure

        # registry.get side_effect:
        # - "whisper" → None (not yet loaded; _ensure_engine doesn't
        #   register on the mock)
        # - "parakeet" → parakeet_engine (loaded by the concurrent
        #   changer)
        def _get(name):
            if name == "whisper":
                return None
            return parakeet_engine

        registry.get.side_effect = _get
        # active_transcriber reads registry.get(active_name).
        mm.active_transcriber = MagicMock(return_value=parakeet_engine)
        mm.touch_model = MagicMock()

        result = mm.ensure_active_engine_loaded()

        # The returned engine is the parakeet engine (re-routed to the
        # current backend), NOT a phantom whisper engine. Pre-fix, the
        # method would have returned the phantom whisper engine while
        # config said "parakeet".
        assert result is parakeet_engine, (
            "ensure_active_engine_loaded must return the "
            "re-routed (current) backend's engine when config "
            "changes during the lock acquisition. Pre-fix, it would "
            "have returned a phantom engine for the stale 'whisper' "
            "backend while config said 'parakeet'."
        )
        # _ensure_engine was called for "whisper" (the phantom
        # construction — unavoidable: the fix can't look into the
        # future to know config is about to change). The phantom is
        # abandoned; the next change cycle unloads it.
        assert "whisper" in ensure_calls, (
            "_ensure_engine must be called for the initial "
            "backend lookup (whisper) when registry.get returns None. "
            "The phantom construction is unavoidable — the fix "
            "prevents the phantom from being RETURNED, not from being "
            "constructed."
        )
        # _ensure_engine was NOT called for "parakeet" because the
        # parakeet engine already exists (loaded by the concurrent
        # changer). This proves the re-route path was taken.
        assert "parakeet" not in ensure_calls, (
            "_ensure_engine must NOT be called for the "
            "re-routed backend (parakeet) when its engine already "
            "exists in the registry (loaded by the concurrent "
            "_change_model_blocking). The re-route path looks up the "
            "existing engine instead of constructing a duplicate."
        )

    def test_no_revalidation_when_config_unchanged(self):
        """When ``config.asr_backend`` does NOT change during the lock
        acquisition, the re-validation is a no-op and the method
        proceeds with the original backend (the existing path is
        preserved — only adds the re-validation, it doesn't
        change the happy path)."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        whisper_engine = MagicMock(name="whisper-engine")
        whisper_engine.is_loaded = True
        # ``active_transcriber`` reads ``registry.get_active()`` — make
        # it return the whisper engine so the method's return value is
        # the whisper engine.
        registry.get_active.return_value = whisper_engine
        registry.get.return_value = whisper_engine
        mm._ensure_engine = MagicMock()
        mm.touch_model = MagicMock()

        result = mm.ensure_active_engine_loaded()

        # No re-route — _ensure_engine NOT called (engine was already
        # registered).
        mm._ensure_engine.assert_not_called()
        assert result is whisper_engine


# ── : start_background_load check-then-spawn race ───────────────


class TestStartBackgroundLoadAtomicSpawn:
    """(Medium): the liveness check + Thread construction +
    assignment to ``_model_load_thread`` MUST be atomic so two
    concurrent callers don't both spawn a ModelLoad thread."""

    def test_spawn_lock_exists_in_init(self):
        """``__init__`` MUST create ``_model_load_spawn_lock`` so the
        lock exists before any thread can call ``start_background_load``."""
        mm, _app, _registry = _make_mm_with_mock_registry()
        assert hasattr(mm, "_model_load_spawn_lock"), (
            "__init__ must create _model_load_spawn_lock so "
            "start_background_load's check-then-spawn can be guarded. "
            "Pre-fix, the check-then-spawn was NOT atomic and two "
            "concurrent callers could both spawn a ModelLoad thread."
        )
        # Must be a plain Lock (no re-entrancy needed).
        assert not hasattr(mm._model_load_spawn_lock, "_is_owned"), (
            "_model_load_spawn_lock must be a plain Lock "
            "(not an RLock) — the spawn critical section has no "
            "re-entrancy requirements."
        )

    def test_concurrent_start_background_load_spawns_single_thread(self):
        """End-to-end: spawn N threads that all call
        ``start_background_load`` concurrently. Assert that at most ONE
        ``ModelLoad`` thread is actually started (the spawn lock
        serializes the check-then-spawn).

        We slow down ``load_background`` with a barrier so the spawned
        thread stays alive long enough for the concurrent
        ``start_background_load`` calls to observe it via the
        ``is_alive()`` check.
        """
        mm, app, registry = _make_mm_with_mock_registry()
        # Slow down load_background so the spawned thread stays alive.
        barrier = threading.Event()

        def _slow_load_background():
            barrier.wait(timeout=5.0)

        mm.load_background = _slow_load_background
        # Track Thread.start() invocations — we patch threading.Thread
        # to count starts, but we need a REAL Thread for the
        # ``is_alive()`` check inside ``start_background_load`` to work.
        # Instead, we count the threads spawned by inspecting
        # ``mm._model_load_thread`` after each call.
        spawned_threads: list[threading.Thread] = []
        spawn_lock = threading.Lock()

        # Wrap start_background_load to capture the spawned thread.
        original_start = mm.start_background_load

        def _capturing_start():
            original_start()
            with spawn_lock:
                if mm._model_load_thread is not None:
                    spawned_threads.append(mm._model_load_thread)

        mm.start_background_load = _capturing_start

        # Spawn n concurrent callers.
        n = 8
        callers = []
        for _ in range(n):
            t = threading.Thread(target=mm.start_background_load)
            callers.append(t)
            t.start()
        for t in callers:
            t.join(timeout=5.0)

        # Release the barrier so the spawned ModelLoad thread can exit.
        barrier.set()

        # Wait for the ModelLoad thread to finish.
        if mm._model_load_thread is not None:
            mm._model_load_thread.join(timeout=5.0)

        # Count DISTINCT Thread identities that were assigned to
        # ``_model_load_thread``. Pre-fix, the check-then-spawn race
        # could produce multiple threads (the second assignment
        # overwrote the first, leaking it). Post-fix, the spawn lock
        # serializes so at most ONE thread is ever assigned.
        distinct_identities = {id(t) for t in spawned_threads}
        assert len(distinct_identities) <= 1, (
            f": start_background_load spawned {len(distinct_identities)} "
            f"distinct ModelLoad threads under {n} concurrent callers. "
            "Pre-fix, the check-then-spawn was NOT atomic — two "
            "callers could both observe _model_load_thread is None / "
            "not alive, both construct a Thread, and both start it; "
            "the second assignment overwrote the first and the first "
            "thread was leaked (still running, untracked, no shutdown "
            "join). Post-fix, _model_load_spawn_lock serializes the "
            "check-then-spawn so at most one ModelLoad thread is ever "
            "live."
        )

    def test_idempotent_when_thread_already_alive(self):
        """When ``_model_load_thread`` is already alive,
        ``start_background_load`` MUST be a no-op (return without
        spawning a new thread). This is the existing fast-path;         must not break it."""
        mm, app, registry = _make_mm_with_mock_registry()
        barrier = threading.Event()

        def _slow_load():
            barrier.wait(timeout=5.0)

        mm.load_background = _slow_load
        # First call spawns the thread.
        mm.start_background_load()
        first_thread = mm._model_load_thread
        assert first_thread is not None
        assert first_thread.is_alive()
        # Second call while the first is alive — MUST be a no-op.
        mm.start_background_load()
        assert mm._model_load_thread is first_thread, (
            "start_background_load must NOT spawn a new thread "
            "when _model_load_thread is already alive. The spawn lock "
            "preserves the existing idempotency contract."
        )
        # Release the barrier so the test can clean up.
        barrier.set()
        first_thread.join(timeout=5.0)


# ── : asr_backend_ready / asr_backend_load_failed event contract ──


class TestBackendLoadFailedEvent:
    """(Medium): ``_change_model_blocking`` and
    ``_set_active_backend_blocking`` MUST publish
    ``asr_backend_load_failed`` (NOT ``asr_backend_ready``) on failure."""

    def test_change_model_blocking_publishes_ready_on_success(self):
        """Regression: on success, ``asr_backend_ready`` is still
        published ( only adds the failure event — it does not
        change the success path)."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._change_model_blocking("parakeet")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(ready_events) == 1, (
            f" regression: on success, exactly one asr_backend_ready "
            f"event must be published. Got {len(ready_events)} ready + "
            f"{len(failed_events)} failed."
        )
        assert len(failed_events) == 0, " regression: asr_backend_load_failed must NOT be published on success."
        evt = ready_events[0]
        assert evt["data"]["backend"] == "parakeet"
        assert evt["data"]["model_size"] == "parakeet"

    def test_change_model_blocking_publishes_load_failed_on_failure(self):
        """When ``load_active`` returns falsy, ``_change_model_blocking``
        MUST publish ``asr_backend_load_failed`` (NOT
        ``asr_backend_ready``) with the backend name + a short
        ``failure_reason``."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        # Make load_active return falsy (failure).
        registry.load_active.return_value = None
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._change_model_blocking("parakeet")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(ready_events) == 0, (
            "asr_backend_ready must NOT be published on failure. "
            "Pre-fix, the event was published UNCONDITIONALLY so the "
            "renderer dismissed the 'loading' spinner and showed 'ready' "
            "UI even when the load had failed (no engine, ERROR tray "
            "state)."
        )
        assert len(failed_events) == 1, (
            f": exactly one asr_backend_load_failed event must be published on failure. Got {len(failed_events)}."
        )
        evt = failed_events[0]
        assert evt["data"]["backend"] == "parakeet"
        assert evt["data"]["model_size"] == "parakeet"
        assert "failure_reason" in evt["data"], (
            "asr_backend_load_failed event data must include a "
            "'failure_reason' field with a short human-readable string "
            "suitable for direct display to the user."
        )
        assert evt["data"]["failure_reason"], ": failure_reason must be a non-empty string."

    def test_change_model_blocking_publishes_load_failed_on_exception(self):
        """When ``load_active`` raises, ``_change_model_blocking`` MUST
        publish ``asr_backend_load_failed`` (NOT ``asr_backend_ready``)
        with the exception message in ``failure_reason``."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        # Make load_active raise.
        registry.load_active.side_effect = RuntimeError("simulated CUDA OOM")
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._change_model_blocking("parakeet")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(ready_events) == 0, ": asr_backend_ready must NOT be published when load_active raises."
        assert len(failed_events) == 1
        evt = failed_events[0]
        assert evt["data"]["backend"] == "parakeet"
        assert "simulated CUDA OOM" in evt["data"]["failure_reason"], (
            "failure_reason must include the exception message so the renderer can surface the root cause to the user."
        )

    def test_change_model_blocking_publishes_neither_event_when_deferred(self):
        """When the change is deferred (recording in progress),
        ``_change_model_blocking`` MUST publish NEITHER event — the
        load didn't happen, so neither 'ready' nor 'failed' applies.
        The next ``apply_pending_model_change`` will re-run the cycle
        and publish at that point."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        # Force the deferred branch — recording in progress.
        app.recorder.recording = True
        # _busy_event.is_set() returns True (not busy), but the
        # recording check forces deferral.
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._change_model_blocking("qwen")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(ready_events) == 0, (
            "asr_backend_ready must NOT be published when the "
            "change is deferred (recording in progress) — the load "
            "didn't happen."
        )
        assert len(failed_events) == 0, (
            "asr_backend_load_failed must NOT be published when "
            "the change is deferred — the load didn't fail, it was "
            "postponed. The next apply_pending_model_change will re-run "
            "the cycle and publish at that point."
        )

    def test_set_active_backend_blocking_publishes_ready_on_success(self):
        """Regression: on success, ``asr_backend_ready`` is still
        published by ``_set_active_backend_blocking``."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._set_active_backend_blocking("qwen")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(ready_events) == 1, (
            f" regression: _set_active_backend_blocking must publish "
            f"exactly one asr_backend_ready on success. Got "
            f"{len(ready_events)} ready + {len(failed_events)} failed."
        )
        assert len(failed_events) == 0

    def test_set_active_backend_blocking_publishes_load_failed_on_failure(self):
        """When ``load_active`` returns falsy,
        ``_set_active_backend_blocking`` MUST publish
        ``asr_backend_load_failed`` (NOT ``asr_backend_ready``)."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        registry.load_active.return_value = None  # failure
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._set_active_backend_blocking("qwen")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(ready_events) == 0, (
            "_set_active_backend_blocking must NOT publish "
            "asr_backend_ready on failure. Pre-fix, the event was "
            "published UNCONDITIONALLY."
        )
        assert len(failed_events) == 1
        evt = failed_events[0]
        assert evt["data"]["backend"] == "qwen"
        assert "failure_reason" in evt["data"]
        assert evt["data"]["failure_reason"]

    def test_set_active_backend_blocking_publishes_neither_when_deferred(self):
        """When the backend change is deferred (recording in progress),
        ``_set_active_backend_blocking`` MUST publish NEITHER event."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        # Force the deferred branch — recording in progress.
        app.recorder.recording = True
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._set_active_backend_blocking("qwen")
        finally:
            event_bus.unsubscribe(subscriber)

        ready_events = [e for e in received if e.get("type") == "asr_backend_ready"]
        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(ready_events) == 0, (
            "_set_active_backend_blocking must NOT publish asr_backend_ready when the change is deferred."
        )
        assert len(failed_events) == 0, (
            "_set_active_backend_blocking must NOT publish "
            "asr_backend_load_failed when the change is deferred — the "
            "load didn't fail, it was postponed."
        )

    def test_load_failed_event_data_shape(self):
        """The ``asr_backend_load_failed`` event data MUST include
        ``backend``, ``model_size``, and ``failure_reason`` fields
        (so the renderer can route the event and display the reason)."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        registry.load_active.return_value = None
        received: list[dict] = []

        def subscriber(event: dict) -> None:
            received.append(event)

        event_bus.subscribe(subscriber)
        try:
            mm._change_model_blocking("parakeet")
        finally:
            event_bus.unsubscribe(subscriber)

        failed_events = [e for e in received if e.get("type") == "asr_backend_load_failed"]
        assert len(failed_events) == 1
        evt = failed_events[0]
        assert evt["type"] == "asr_backend_load_failed"
        assert set(evt["data"].keys()) >= {"backend", "model_size", "failure_reason"}, (
            f"asr_backend_load_failed event data must include "
            f"backend, model_size, and failure_reason fields. Got: "
            f"{set(evt['data'].keys())}"
        )


# ── : exception log includes backend + model context ─────────


class TestExceptionLogContext:
    """(Low): ``load_background``'s outer ``except Exception``
    log MUST include ``backend`` + ``model`` so the crash trace is
    actionable (the same crash in ``parakeet`` vs ``whisper`` has very
    different remediation paths)."""

    def test_exception_log_includes_backend_and_model(self, caplog):
        """Source guard: the ``log.exception`` call in the outer
        ``except Exception`` block MUST include ``backend=%s`` and
        ``model=%s`` format specifiers."""
        import inspect

        src = inspect.getsource(ModelManager.load_background)
        # The exception log must include backend + model context.
        # We look for the format string in the source.
        assert "backend=%s" in src and "model=%s" in src, (
            "load_background's outer ``except Exception`` "
            "log.exception MUST include ``backend=%s`` and ``model=%s`` "
            "format specifiers. Pre-fix, the bare "
            "``log.exception('[STARTUP] Background model load crashed')`` "
            "left the operator guessing which backend / model size "
            "crashed — the same crash in parakeet vs whisper has very "
            "different remediation paths."
        )

    def test_exception_log_emitted_with_context_on_crash(self, caplog):
        """End-to-end: when ``load_background``'s body raises, the
        outer ``except Exception`` log MUST include the backend and
        model_size in the formatted message."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="parakeet", model_size="parakeet")

        # Make _ensure_engine raise so the body's outer except fires.
        def _crashing_ensure(_name):
            raise RuntimeError("simulated crash in _ensure_engine")

        mm._ensure_engine = _crashing_ensure

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.model_manager"):
            mm.load_background()

        crashed_logs = [r for r in caplog.records if "Background model load crashed" in r.getMessage()]
        assert len(crashed_logs) == 1, (
            "load_background's outer except must log exactly one 'Background model load crashed' message."
        )
        msg = crashed_logs[0].getMessage()
        assert "backend=parakeet" in msg, f": the crash log must include 'backend=parakeet'. Got: {msg!r}"
        assert "model=parakeet" in msg, f": the crash log must include 'model=parakeet'. Got: {msg!r}"


# ── : load_background finally does NOT auto-start on failure ──


class TestNoAutoStartOnFailure:
    """``load_background``'s ``finally`` block MUST NOT
    auto-start a dictation when the load FAILED or CRASHED. The
    auto-start would loop on ``fallback_to_whisper`` and fail the same
    way, spamming the tray with ERROR state. ``_pending_dictation``
    MUST be cleared on the failure / crash paths so the finally's
    ``if self._pending_dictation`` check skips the auto-start."""

    def test_no_auto_start_when_load_with_fallback_fails(self):
        """When ``load_with_fallback`` returns falsy (all backends
        failed), the finally block MUST NOT call
        ``_schedule_timer(0, _start_dictation)`` even if
        ``_pending_dictation`` was set (the F2 press). The flag is
        cleared on the failure path."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        # Simulate all backends failed.
        registry.load_with_fallback.return_value = None
        registry.available_backends = ["whisper", "parakeet"]
        # User pressed F2 during load — _pending_dictation is set.
        mm._pending_dictation = True
        # Track _schedule_timer calls.
        schedule_calls: list = []
        app._schedule_timer = lambda delay, cb: schedule_calls.append((delay, cb))

        mm.load_background()

        # _pending_dictation was cleared on the failure path.
        assert mm._pending_dictation is False, (
            "_pending_dictation MUST be cleared on the "
            "failure path (load_with_fallback returned falsy) so the "
            "finally block does NOT auto-start a dictation that would "
            "immediately fail (no model is loaded). Pre-fix, the flag "
            "was NOT cleared and the finally block unconditionally "
            "scheduled _start_dictation, which then called "
            "ensure_active_engine_loaded (still no model), fell "
            "through to fallback_to_whisper (also failed — same root "
            "cause), and entered a tight retry loop that spammed the "
            "tray with ERROR state."
        )
        # _schedule_timer was NOT called (no auto-start on failure).
        assert schedule_calls == [], f": _schedule_timer must NOT be called on the failure path. Got: {schedule_calls}"

    def test_no_auto_start_when_body_raises(self):
        """When ``load_background``'s body raises (crash), the finally
        block MUST NOT call ``_schedule_timer(0, _start_dictation)``
        even if ``_pending_dictation`` was set. The flag is cleared on
        the crash path."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")

        # Make _ensure_engine raise so the body's outer except fires.
        def _crashing_ensure(_name):
            raise RuntimeError("simulated crash")

        mm._ensure_engine = _crashing_ensure
        # User pressed F2 during load.
        mm._pending_dictation = True
        schedule_calls: list = []
        app._schedule_timer = lambda delay, cb: schedule_calls.append((delay, cb))

        mm.load_background()

        # _pending_dictation was cleared on the crash path.
        assert mm._pending_dictation is False, (
            "_pending_dictation MUST be cleared on the crash "
            "path (body raised) so the finally block does NOT auto-start."
        )
        assert schedule_calls == [], f": _schedule_timer must NOT be called on the crash path. Got: {schedule_calls}"

    def test_auto_start_still_fires_on_success(self):
        """Regression: on SUCCESS, the finally block MUST auto-start a
        pending dictation (the original intent — auto-start only when
        the model is actually ready). only clears the flag on
        failure / crash; the success path is preserved."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        # Load succeeds (default mock returns truthy engine).
        # User pressed F2 during load.
        mm._pending_dictation = True
        schedule_calls: list = []
        app._schedule_timer = lambda delay, cb: schedule_calls.append((delay, cb))

        mm.load_background()

        # _pending_dictation was consumed by the auto-start.
        assert mm._pending_dictation is False, (
            "regression: on success, _pending_dictation must "
            "be cleared by the auto-start (the finally block consumes it)."
        )
        # _schedule_timer WAS called on success.
        assert len(schedule_calls) == 1, (
            f" regression: on success, _schedule_timer must be "
            f"called exactly once to auto-start the pending dictation. "
            f"Got: {len(schedule_calls)} calls. Pre-fix, this was the "
            f"unconditional behaviour; only suppresses the "
            f"auto-start on FAILURE / CRASH."
        )
        delay, cb = schedule_calls[0]
        assert delay == 0
        assert cb == app._start_dictation

    def test_no_auto_start_when_shutting_down(self):
        """Regression: the existing ``not self._app._shutting_down``
        guard is preserved — even on success, the auto-start is
        suppressed during shutdown."""
        mm, app, registry = _make_mm_with_mock_registry(backend_name="whisper")
        app._shutting_down = True
        mm._pending_dictation = True
        schedule_calls: list = []
        app._schedule_timer = lambda delay, cb: schedule_calls.append((delay, cb))

        mm.load_background()

        # _schedule_timer NOT called (shutting down).
        # Note: load_background returns early at the top when
        # _shutting_down is True, so the body never runs.
        assert schedule_calls == [], (
            "regression: _schedule_timer must NOT be called "
            "when _shutting_down is True (the existing early-return "
            "guard at the top of load_background)."
        )
