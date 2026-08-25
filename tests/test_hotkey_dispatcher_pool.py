"""Pool tracking tests for ``HotkeyDispatcher._shared_backend_pool``.

Verifies the MINIMAL per-spec backend pool introduced to lay the
groundwork for the full pooling refactor (single native binary serving
multiple ``(role, spec)`` pairs — see the module docstring TODO in
``voice_typer/server/hotkey_dispatcher.py``).

Scope of the minimal pool:
  - ``_shared_backend_pool: dict[str, HotkeyBackend]`` tracks every
    live backend by its hotkey spec.
  - ``get_active_backend_count()`` returns the number of DISTINCT
    native backends currently tracked (i.e. the number of distinct
    native subprocesses owned by this dispatcher).
  - ``_create_and_start_main_backend`` checks the pool BEFORE calling
    the factory: if a backend for the same spec is already alive, it
    is reused (no factory call, no second ``start()``).
  - ``register_esc`` / ``register_repaste`` TRACK their backends in
    the pool (for count accuracy) but do NOT fast-path the factory
    call (the ESC / repaste callbacks differ from the dictation
    callback, so reusing a dictation backend would cause both
    callbacks to fire on the same keypress — that conflict is
    resolved by the full refactor's role-tagged wire events).
  - ``stop_all`` clears the pool.

The tests use a minimal mock app (no real ``VoiceTyperApp``) and mock
``create_hotkey_backend`` so no real native subprocess is spawned.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher


def _make_mock_app(
    *,
    hotkey: str = "<f2>",
    recording_mode: str = "toggle",
    esc_cancel_enabled: bool = False,
    repaste_hotkey: str | None = None,
) -> SimpleNamespace:
    """Build a minimal mock app satisfying the HotkeyDispatcher contract.

    Mirrors the mock-app factory in ``test_hotkey_dispatcher_restart.py``
    so the two test files are interchangeable in terms of the dispatcher
    surface they exercise.
    """
    app = SimpleNamespace()
    app.config = SimpleNamespace(
        hotkey=hotkey,
        recording_mode=recording_mode,
        esc_cancel_enabled=esc_cancel_enabled,
        repaste_hotkey=repaste_hotkey,
        save=MagicMock(return_value=True),
    )
    app.tray = MagicMock()
    app._stop_dictation = MagicMock()
    app.toggle_dictation = MagicMock()
    app._cancel_dictation = MagicMock()
    app.repaste_last = MagicMock()
    return app


@pytest.fixture
def dispatcher() -> HotkeyDispatcher:
    """Build a HotkeyDispatcher backed by a minimal mock app.

    Default config: hotkey=<f2>, toggle mode, ESC disabled, no repaste.
    """
    app = _make_mock_app()
    return HotkeyDispatcher(app)


# ─── _shared_backend_pool exists & is a dict ───────────────────────────────


def test_shared_backend_pool_is_a_dict_initialized_empty():
    """``_shared_backend_pool`` is a ``dict`` and starts empty."""
    app = _make_mock_app()
    dispatcher = HotkeyDispatcher(app)
    assert isinstance(dispatcher._shared_backend_pool, dict)
    assert len(dispatcher._shared_backend_pool) == 0


def test_get_active_backend_count_returns_zero_initially():
    """``get_active_backend_count()`` returns 0 for a fresh dispatcher."""
    app = _make_mock_app()
    dispatcher = HotkeyDispatcher(app)
    assert dispatcher.get_active_backend_count() == 0


# ─── register() twice with same hotkey reuses the pooled backend ───────────


def test_register_twice_same_hotkey_reuses_pooled_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """Calling ``register()`` twice with the SAME hotkey must NOT call
    ``create_hotkey_backend`` twice. The second call hits the per-spec
    pool fast path in ``_create_and_start_main_backend`` and returns
    the existing backend (no new subprocess spawned)."""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    # First register — pool is empty, factory called once.
    result1 = dispatcher.register()
    assert result1 is True
    assert factory.call_count == 1
    assert dispatcher._hotkey_backend is new_backend
    assert dispatcher.get_active_backend_count() == 1

    # Second register with the SAME hotkey — pool has "<f2>" already,
    # so the factory is NOT called again and the same backend is reused.
    result2 = dispatcher.register()
    assert result2 is True
    assert factory.call_count == 1, f"Expected factory called once (pool fast-path); got {factory.call_count}"
    assert dispatcher._hotkey_backend is new_backend
    assert dispatcher.get_active_backend_count() == 1, "Pool size must stay at 1 when the same spec is re-registered"

    # start() was called once (first register). The pool fast-path
    # returns BEFORE the start() call, so the second register does NOT
    # re-start the already-running backend.
    new_backend.start.assert_called_once()


def test_register_with_different_hotkeys_creates_distinct_backends(dispatcher: HotkeyDispatcher, monkeypatch):
    """``restart()`` to a DIFFERENT hotkey spec creates a NEW backend
    (different spec = different pool entry). The OLD backend is
    untracked and stopped; the NEW backend is tracked. Final pool
    size is 1 (the new spec), not 2."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<f3>")

    # Old backend was stopped and untracked.
    old_backend.stop.assert_called_once()
    # New backend installed and tracked.
    assert dispatcher._hotkey_backend is new_backend
    assert dispatcher.get_active_backend_count() == 1
    # Pool key is the NEW spec.
    assert "<f3>" in dispatcher._shared_backend_pool
    assert dispatcher._shared_backend_pool["<f3>"] is new_backend
    # OLD spec ("<f2>") is NOT in the pool (old_backend was never
    # tracked because it was assigned directly, not via register()).
    assert "<f2>" not in dispatcher._shared_backend_pool


# ─── failed start does not leave a stale pool entry ────────────────────────


def test_register_failure_does_not_pollute_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """If ``start()`` raises, the failed backend must NOT be added to
    ``_shared_backend_pool`` — otherwise a subsequent ``register()``
    with the same spec would return a dead backend from the pool.
    The pool insertion happens AFTER ``start()`` succeeds."""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    new_backend.start.side_effect = OSError("hotkey already claimed")
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    result = dispatcher.register()
    assert result is False
    # Pool stays empty — the failed start did not add an entry.
    assert dispatcher.get_active_backend_count() == 0
    assert dispatcher._shared_backend_pool == {}


def test_restart_failure_then_restore_tracks_restored_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """``restart()`` to a bad spec fails, then restores the OLD spec.
    The restored backend IS tracked in the pool. The failed-spec
    backend (whose start() raised) is NOT tracked."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    broken_backend = MagicMock()
    broken_backend.is_alive.return_value = True
    broken_backend.start.side_effect = OSError("rejected")
    restored_backend = MagicMock()
    restored_backend.is_alive.return_value = True
    factory = MagicMock(side_effect=[broken_backend, restored_backend])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<bad>")

    # Restored backend installed and tracked under the OLD spec "<f2>".
    assert dispatcher._hotkey_backend is restored_backend
    assert dispatcher.get_active_backend_count() == 1
    assert dispatcher._shared_backend_pool.get("<f2>") is restored_backend
    # Broken backend (for "<bad>") is NOT in the pool — its start() raised.
    assert "<bad>" not in dispatcher._shared_backend_pool


# ─── dead pooled entries are purged ────────────────────────────────────────


def test_get_active_backend_count_purges_dead_entries(dispatcher: HotkeyDispatcher):
    """If a pooled backend's ``is_alive()`` returns False (e.g. the
    native subprocess crashed), ``get_active_backend_count()`` must
    purge it from the pool before reporting the count."""
    dead_backend = MagicMock()
    dead_backend.is_alive.return_value = False
    dispatcher._shared_backend_pool["<f9>"] = dead_backend
    assert dispatcher.get_active_backend_count() == 0
    assert "<f9>" not in dispatcher._shared_backend_pool


def test_create_main_backend_purges_stale_entry_then_recreates(dispatcher: HotkeyDispatcher, monkeypatch):
    """If the pool has a DEAD entry for the requested spec,
    ``_create_and_start_main_backend`` must purge it and call the
    factory to create a fresh backend (NOT return the dead one)."""
    dead_backend = MagicMock()
    dead_backend.is_alive.return_value = False
    dispatcher._shared_backend_pool["<f2>"] = dead_backend

    fresh_backend = MagicMock()
    fresh_backend.is_alive.return_value = True
    factory = MagicMock(return_value=fresh_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.register()

    # Factory WAS called — the dead entry was purged and a fresh
    # backend created (the pool fast-path did NOT return the dead one).
    factory.assert_called_once_with("<f2>", role="dictation")
    assert dispatcher._hotkey_backend is fresh_backend
    assert dispatcher._shared_backend_pool["<f2>"] is fresh_backend


# ─── stop_all clears the pool ─────────────────────────────────────────────


def test_stop_all_clears_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``stop_all`` clears ``_shared_backend_pool`` so a post-shutdown
    ``register()`` starts from a clean slate. Mirrors the existing
    contract for ``_shared_backend`` (cleared by ``stop_all``)."""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )
    dispatcher.register()
    assert dispatcher.get_active_backend_count() == 1

    dispatcher.stop_all()

    assert dispatcher._shared_backend_pool == {}
    assert dispatcher.get_active_backend_count() == 0
    assert dispatcher._shared_backend is None


# ─── ESC / repaste tracking (no fast-path; track only) ─────────────────────


def test_register_esc_tracks_esc_backend_in_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``register_esc`` adds the ESC backend to ``_shared_backend_pool``
    under ``"<esc>"`` after ``start()`` succeeds. The pool count
    reflects the ESC backend as a distinct subprocess (the
    extra-matcher delegation to the shared dictation backend is a
    SEPARATE mechanism — see ``_shared_backend`` — and does not affect
    the per-spec pool tracking)."""
    esc_backend = MagicMock()
    esc_backend.is_alive.return_value = True
    factory = MagicMock(return_value=esc_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.register_esc()

    assert dispatcher._esc_backend is esc_backend
    assert dispatcher._shared_backend_pool.get("<esc>") is esc_backend
    assert dispatcher.get_active_backend_count() == 1


def test_unregister_esc_untracks_from_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``unregister_esc`` removes the ESC backend from the pool so the
    count drops to 0."""
    esc_backend = MagicMock()
    esc_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=esc_backend),
    )
    dispatcher.register_esc()
    assert dispatcher.get_active_backend_count() == 1

    dispatcher.unregister_esc()

    assert dispatcher._esc_backend is None
    assert "<esc>" not in dispatcher._shared_backend_pool
    assert dispatcher.get_active_backend_count() == 0


def test_unregister_esc_removes_pooled_extra_matcher(dispatcher: HotkeyDispatcher, monkeypatch):
    """BROKEN-2 REGRESSION: ``unregister_esc`` must remove the pooled
    ``"esc"`` extra matcher from the STILL-ALIVE shared backend.

    The shared dictation backend survives ``unregister_esc`` (only the
    delegated ESC backend is stopped), so without the removal the
    ``"esc"`` extra matcher keeps firing the cancel callback — ESC
    keeps cancelling dictation after ``esc_cancel_enabled`` is turned
    off in settings."""
    shared_native = MagicMock()
    shared_backend = MagicMock()
    shared_backend._native = shared_native
    dispatcher._shared_backend = shared_backend

    esc_backend = MagicMock()
    esc_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=esc_backend),
    )
    dispatcher.register_esc()
    # ESC was pooled onto the shared backend (delegated model).
    shared_native.add_extra_matcher.assert_called_once_with("esc", "<esc>")

    dispatcher.unregister_esc()

    assert dispatcher._esc_backend is None
    assert dispatcher._esc_spec is None
    # The pooled extra matcher was removed from the shared backend.
    shared_native.remove_extra_matcher.assert_called_once_with("esc")


def test_shared_native_returns_none_when_adapter_in_fallback(dispatcher):
    """BROKEN-3: ``_shared_native()`` reports ``None`` when the shared
    backend is a ``_NativeBackendAdapter`` in FALLBACK/FAILED state —
    its native subprocess is dead, so aux roles must NOT pool onto it
    (they would silently stop firing)."""
    shared_backend = MagicMock()
    shared_backend._state = "FALLBACK"
    shared_backend._native = MagicMock()  # duck-types add_extra_matcher
    dispatcher._shared_backend = shared_backend

    assert dispatcher._shared_native() is None
    # Pooling is unavailable -> the per-role subprocess fallback runs.
    assert dispatcher._pool_aux_into_shared("esc", "<esc>", lambda: None, None) is False


def test_shared_native_swap_to_legacy_resyncs_aux_roles(dispatcher, monkeypatch):
    """BROKEN-3: when the shared backend's adapter swaps to legacy
    (native subprocess permanently failed -> FALLBACK), the dispatcher
    re-registers the aux roles so ESC / repaste keep working via their
    own per-role subprocesses instead of staying delegated onto the
    dead native."""
    shared_native = MagicMock()
    dictation_backend = MagicMock(name="dictation")
    dictation_backend.is_alive.return_value = True
    dictation_backend._native = shared_native
    dictation_backend._state = "NATIVE"
    esc1 = MagicMock(name="esc1")
    esc1.is_alive.return_value = True
    repaste1 = MagicMock(name="repaste1")
    repaste1.is_alive.return_value = True
    esc2 = MagicMock(name="esc2")
    esc2.is_alive.return_value = True
    repaste2 = MagicMock(name="repaste2")
    repaste2.is_alive.return_value = True
    factory = MagicMock(side_effect=[dictation_backend, esc1, repaste1, esc2, repaste2])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)
    dispatcher._app.config.esc_cancel_enabled = True
    dispatcher._app.config.repaste_hotkey = "<ctrl>+<shift>+<v>"

    dispatcher.register()
    # Both roles were pooled onto the shared native (delegated model).
    assert dispatcher._esc_backend is esc1
    assert dispatcher._repaste_backend is repaste1
    assert shared_native.add_extra_matcher.call_count == 2

    # Simulate the adapter's native permanently failing -> legacy swap.
    dictation_backend._state = "FALLBACK"
    dispatcher._handle_shared_native_state_changed("FALLBACK")

    # Both roles re-registered as per-role subprocesses (NOT pooled).
    assert dispatcher._esc_backend is esc2
    assert dispatcher._repaste_backend is repaste2
    assert shared_native.add_extra_matcher.call_count == 2  # no re-pooling


def test_shared_native_recovery_resyncs_aux_roles(dispatcher, monkeypatch):
    """BROKEN-3 recovery direction: when the adapter swaps back to
    NATIVE, the dispatcher re-registers the aux roles so they re-pool
    onto the recovered native (single subprocess again) — preventing a
    double-fire (per-role subprocess + extra matcher both matching)."""
    shared_native = MagicMock()
    dictation_backend = MagicMock(name="dictation")
    dictation_backend.is_alive.return_value = True
    dictation_backend._native = shared_native
    dictation_backend._state = "FALLBACK"  # start swapped-to-legacy
    esc1 = MagicMock(name="esc1")
    esc1.is_alive.return_value = True
    esc2 = MagicMock(name="esc2")
    esc2.is_alive.return_value = True
    factory = MagicMock(side_effect=[dictation_backend, esc1, esc2])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)
    dispatcher._app.config.esc_cancel_enabled = True

    dispatcher.register()
    # FALLBACK from the start: ESC runs as its own per-role subprocess.
    assert dispatcher._esc_backend is esc1
    assert shared_native.add_extra_matcher.call_count == 0

    # Simulate native recovery -> adapter swaps back to NATIVE.
    dictation_backend._state = "NATIVE"
    dispatcher._handle_shared_native_state_changed("NATIVE")

    # ESC re-registered and pooled onto the recovered native.
    assert dispatcher._esc_backend is esc2
    assert shared_native.add_extra_matcher.call_count == 1
    assert esc2._native._delegated is True


def test_register_with_esc_disabled_removes_pooled_extra_matcher(dispatcher: HotkeyDispatcher, monkeypatch):
    """BROKEN-2 REGRESSION (register path): re-running ``register()``
    with ``esc_cancel_enabled`` turned OFF must remove the pooled
    ``"esc"`` extra matcher from the shared backend — otherwise the
    ESC key keeps cancelling dictation after the setting is disabled
    (only the delegated ESC backend is stopped, and the shared
    dictation backend stays alive)."""
    shared_native = MagicMock()

    dictation_backend = MagicMock(name="dictation")
    dictation_backend.is_alive.return_value = True
    # The dispatcher installs the dictation backend as ``_shared_backend``;
    # its native must support the pooling API for ESC to be delegated.
    dictation_backend._native = shared_native
    esc_backend = MagicMock(name="esc")
    esc_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(side_effect=[dictation_backend, esc_backend]),
    )
    dispatcher._app.config.esc_cancel_enabled = True

    dispatcher.register()
    shared_native.add_extra_matcher.assert_called_once_with("esc", "<esc>")

    # Disable ESC and re-register (config toggle round-trip).
    dispatcher._app.config.esc_cancel_enabled = False
    dispatcher.register()

    assert dispatcher._esc_backend is None
    assert dispatcher._esc_spec is None
    shared_native.remove_extra_matcher.assert_called_once_with("esc")


def test_register_repaste_tracks_repaste_backend_in_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``register_repaste`` adds the repaste backend to the pool under
    the configured repaste hotkey spec."""
    dispatcher._app.config.repaste_hotkey = "<ctrl>+<shift>+<v>"
    repaste_backend = MagicMock()
    repaste_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=repaste_backend),
    )

    dispatcher.register_repaste()

    assert dispatcher._repaste_backend is repaste_backend
    assert dispatcher._shared_backend_pool.get("<ctrl>+<shift>+<v>") is repaste_backend
    assert dispatcher.get_active_backend_count() == 1


# ─── combined: dictation + ESC + repaste all tracked distinctly ────────────


def test_full_registration_tracks_all_three_specs(dispatcher: HotkeyDispatcher, monkeypatch):
    """``register()`` with ESC and repaste enabled tracks all THREE
    distinct specs in the pool. The pool count is 3 (one entry per
    distinct spec).

    NOTE: This is the count of DISTINCT backend INSTANCES tracked by
    the pool. The ``_shared_backend`` extra-matcher mechanism may
    delegate ESC / repaste to the dictation subprocess on platforms
    that select the native ``SubprocessHotkeyBackend`` — in that case
    the ACTUAL native subprocess count is 1, but the pool still
    tracks 3 backend objects (the delegated ones have no subprocess
    of their own). ``get_active_backend_count()`` reports the pool
    size, which is the upper bound on the subprocess count."""
    dispatcher._app.config.esc_cancel_enabled = True
    dispatcher._app.config.repaste_hotkey = "<ctrl>+<shift>+<v>"

    # Distinct backends for each role. ``side_effect`` returns them in
    # the order the factory is called: dictation, ESC, repaste.
    dictation_backend = MagicMock(name="dictation")
    dictation_backend.is_alive.return_value = True
    esc_backend = MagicMock(name="esc")
    esc_backend.is_alive.return_value = True
    repaste_backend = MagicMock(name="repaste")
    repaste_backend.is_alive.return_value = True
    factory = MagicMock(side_effect=[dictation_backend, esc_backend, repaste_backend])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    result = dispatcher.register()
    assert result is True

    # All three specs are tracked in the pool.
    assert dispatcher._shared_backend_pool.get("<f2>") is dictation_backend
    assert dispatcher._shared_backend_pool.get("<esc>") is esc_backend
    assert dispatcher._shared_backend_pool.get("<ctrl>+<shift>+<v>") is repaste_backend
    assert dispatcher.get_active_backend_count() == 3


def test_stop_all_clears_pool_after_full_registration(dispatcher: HotkeyDispatcher, monkeypatch):
    """``stop_all`` clears ALL three pool entries after a full
    registration (dictation + ESC + repaste)."""
    dispatcher._app.config.esc_cancel_enabled = True
    dispatcher._app.config.repaste_hotkey = "<ctrl>+<shift>+<v>"

    dictation_backend = MagicMock(name="dictation")
    dictation_backend.is_alive.return_value = True
    esc_backend = MagicMock(name="esc")
    esc_backend.is_alive.return_value = True
    repaste_backend = MagicMock(name="repaste")
    repaste_backend.is_alive.return_value = True
    factory = MagicMock(side_effect=[dictation_backend, esc_backend, repaste_backend])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.register()
    assert dispatcher.get_active_backend_count() == 3

    dispatcher.stop_all()

    assert dispatcher._shared_backend_pool == {}
    assert dispatcher.get_active_backend_count() == 0


# ─── restart untracks old backend before stopping ─────────────────────────


def test_restart_untracks_old_backend_before_stopping(dispatcher: HotkeyDispatcher, monkeypatch):
    """``restart()`` untracks the OLD backend from the pool BEFORE
    calling ``stop()``. This ensures the pool count drops as soon as
    the backend is logically dead, even if ``stop()`` hangs."""
    # First, register to get a tracked backend.
    old_backend = MagicMock(name="old")
    old_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=old_backend),
    )
    dispatcher.register()
    assert dispatcher._shared_backend_pool.get("<f2>") is old_backend

    # Now restart to a new spec. The factory must be called for "<f3>"
    # (the OLD backend is for "<f2>", so no fast-path reuse for "<f3>").
    new_backend = MagicMock(name="new")
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<f3>")

    # OLD backend untracked and stopped.
    assert "<f2>" not in dispatcher._shared_backend_pool
    old_backend.stop.assert_called_once()
    # NEW backend tracked.
    assert dispatcher._shared_backend_pool.get("<f3>") is new_backend
    assert dispatcher.get_active_backend_count() == 1
