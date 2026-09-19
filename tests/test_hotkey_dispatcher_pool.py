"""Pool tracking tests for ``HotkeyDispatcher._shared_backend_pool``."""

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
    """Build a minimal mock app satisfying the HotkeyDispatcher contract."""
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
    """Build a HotkeyDispatcher backed by a minimal mock app."""
    app = _make_mock_app()
    return HotkeyDispatcher(app)


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


def test_register_twice_same_hotkey_reuses_pooled_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """Calling ``register()`` twice with the SAME hotkey must NOT call"""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    # First register, pool is empty, factory called once.
    result1 = dispatcher.register()
    assert result1 is True
    assert factory.call_count == 1
    assert dispatcher._hotkey_backend is new_backend
    assert dispatcher.get_active_backend_count() == 1

    # Second register with the SAME hotkey, pool has "<f2>" already,
    result2 = dispatcher.register()
    assert result2 is True
    assert factory.call_count == 1, f"Expected factory called once (pool fast-path); got {factory.call_count}"
    assert dispatcher._hotkey_backend is new_backend
    assert dispatcher.get_active_backend_count() == 1, "Pool size must stay at 1 when the same spec is re-registered"

    new_backend.start.assert_called_once()


def test_register_with_different_hotkeys_creates_distinct_backends(dispatcher: HotkeyDispatcher, monkeypatch):
    """``restart()`` to a DIFFERENT hotkey spec creates a NEW backend"""
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
    assert "<f2>" not in dispatcher._shared_backend_pool


def test_register_failure_does_not_pollute_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``_shared_backend_pool``, otherwise a subsequent ``register()``"""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    new_backend.start.side_effect = OSError("hotkey already claimed")
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    result = dispatcher.register()
    assert result is False
    # Pool stays empty, the failed start did not add an entry.
    assert dispatcher.get_active_backend_count() == 0
    assert dispatcher._shared_backend_pool == {}


def test_restart_failure_then_restore_tracks_restored_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """The restored backend IS tracked in the pool. The failed-spec"""
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

    assert dispatcher._hotkey_backend is restored_backend
    assert dispatcher.get_active_backend_count() == 1
    assert dispatcher._shared_backend_pool.get("<f2>") is restored_backend
    # Broken backend (for "<bad>") is NOT in the pool, its start() raised.
    assert "<bad>" not in dispatcher._shared_backend_pool


def test_get_active_backend_count_purges_dead_entries(dispatcher: HotkeyDispatcher):
    """native subprocess crashed), ``get_active_backend_count()`` must"""
    dead_backend = MagicMock()
    dead_backend.is_alive.return_value = False
    dispatcher._shared_backend_pool["<f9>"] = dead_backend
    assert dispatcher.get_active_backend_count() == 0
    assert "<f9>" not in dispatcher._shared_backend_pool


def test_create_main_backend_purges_stale_entry_then_recreates(dispatcher: HotkeyDispatcher, monkeypatch):
    """If the pool has a DEAD entry for the requested spec,"""
    dead_backend = MagicMock()
    dead_backend.is_alive.return_value = False
    dispatcher._shared_backend_pool["<f2>"] = dead_backend

    fresh_backend = MagicMock()
    fresh_backend.is_alive.return_value = True
    factory = MagicMock(return_value=fresh_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.register()

    # Factory WAS called, the dead entry was purged and a fresh
    factory.assert_called_once_with("<f2>", role="dictation")
    assert dispatcher._hotkey_backend is fresh_backend
    assert dispatcher._shared_backend_pool["<f2>"] is fresh_backend


def test_stop_all_clears_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``stop_all`` clears ``_shared_backend_pool`` so a post-shutdown"""
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


def test_register_esc_tracks_esc_backend_in_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``register_esc`` adds the ESC backend to ``_shared_backend_pool``"""
    esc_backend = MagicMock()
    esc_backend.is_alive.return_value = True
    factory = MagicMock(return_value=esc_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.register_esc()

    assert dispatcher._esc_backend is esc_backend
    assert dispatcher._shared_backend_pool.get("<esc>") is esc_backend
    assert dispatcher.get_active_backend_count() == 1


def test_unregister_esc_untracks_from_pool(dispatcher: HotkeyDispatcher, monkeypatch):
    """``unregister_esc`` removes the ESC backend from the pool so the"""
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
    """BROKEN-2 REGRESSION: ``unregister_esc`` must remove the pooled"""
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
    shared_native.remove_extra_matcher.assert_called_once_with("esc")


def test_shared_native_returns_none_when_adapter_in_fallback(dispatcher):
    """BROKEN-3: ``_shared_native()`` reports ``None`` when the shared"""
    shared_backend = MagicMock()
    shared_backend._state = "FALLBACK"
    shared_backend._native = MagicMock()  # duck-types add_extra_matcher
    dispatcher._shared_backend = shared_backend

    assert dispatcher._shared_native() is None
    # Pooling is unavailable -> the per-role subprocess fallback runs.
    assert dispatcher._pool_aux_into_shared("esc", "<esc>", lambda: None, None) is False


def test_shared_native_swap_to_legacy_resyncs_aux_roles(dispatcher, monkeypatch):
    """BROKEN-3: when the shared backend's adapter swaps to legacy"""
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
    """NATIVE, the dispatcher re-registers the aux roles so they re-pool"""
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
    """BROKEN-2 REGRESSION (register path): re-running ``register()``"""
    shared_native = MagicMock()

    dictation_backend = MagicMock(name="dictation")
    dictation_backend.is_alive.return_value = True
    # The dispatcher installs the dictation backend as ``_shared_backend``;
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
    """``register_repaste`` adds the repaste backend to the pool under"""
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


def test_full_registration_tracks_all_three_specs(dispatcher: HotkeyDispatcher, monkeypatch):
    """``register()`` with ESC and repaste enabled tracks all THREE"""
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

    result = dispatcher.register()
    assert result is True

    # All three specs are tracked in the pool.
    assert dispatcher._shared_backend_pool.get("<f2>") is dictation_backend
    assert dispatcher._shared_backend_pool.get("<esc>") is esc_backend
    assert dispatcher._shared_backend_pool.get("<ctrl>+<shift>+<v>") is repaste_backend
    assert dispatcher.get_active_backend_count() == 3


def test_stop_all_clears_pool_after_full_registration(dispatcher: HotkeyDispatcher, monkeypatch):
    """``stop_all`` clears ALL three pool entries after a full"""
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


def test_restart_untracks_old_backend_before_stopping(dispatcher: HotkeyDispatcher, monkeypatch):
    """the backend is logically dead, even if ``stop()`` hangs."""
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


# ─── Role-based extra-matcher teardown (remove_extra_matcher stepping stone) ─


def _install_shared_native(dispatcher: HotkeyDispatcher) -> MagicMock:
    """Install a mock shared dictation backend whose native supports"""
    shared_native = MagicMock(name="shared_native")
    dictation_backend = MagicMock(name="dictation")
    dictation_backend.is_alive.return_value = True
    dictation_backend._native = shared_native
    dispatcher._shared_backend = dictation_backend
    dispatcher._hotkey_backend = dictation_backend
    return shared_native


def test_register_repaste_empty_config_removes_pooled_extra_matcher(dispatcher: HotkeyDispatcher, monkeypatch):
    """Clearing ``repaste_hotkey`` (config_applier calls"""
    shared_native = _install_shared_native(dispatcher)
    dispatcher._app.config.repaste_hotkey = "<ctrl>+<shift>+<v>"
    repaste_backend = MagicMock(name="repaste")
    repaste_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=repaste_backend),
    )

    dispatcher.register_repaste()
    shared_native.add_extra_matcher.assert_called_once_with("repaste", "<ctrl>+<shift>+<v>")
    assert dispatcher._repaste_callback is not None

    # Clear the setting and re-register (the config_applier path).
    dispatcher._app.config.repaste_hotkey = ""
    dispatcher.register_repaste()

    assert dispatcher._repaste_backend is None
    assert dispatcher._repaste_spec is None
    assert dispatcher._repaste_callback is None
    shared_native.remove_extra_matcher.assert_called_once_with("repaste")


def test_register_repaste_rejected_spec_removes_pooled_extra_matcher(dispatcher: HotkeyDispatcher, monkeypatch):
    """When the configured repaste hotkey is rejected by the denylist,"""
    shared_native = _install_shared_native(dispatcher)
    # First, a valid registration so a matcher is pooled.
    dispatcher._app.config.repaste_hotkey = "<ctrl>+<shift>+<v>"
    repaste_backend = MagicMock(name="repaste")
    repaste_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=repaste_backend),
    )
    dispatcher.register_repaste()
    assert shared_native.add_extra_matcher.call_count == 1

    # Re-register with a reserved combo → validation rejects.
    dispatcher._app.config.repaste_hotkey = "<alt>+<f4>"
    dispatcher.register_repaste()

    assert dispatcher._app.config.repaste_hotkey == ""
    assert dispatcher._repaste_backend is None
    assert dispatcher._repaste_callback is None
    shared_native.remove_extra_matcher.assert_called_once_with("repaste")


def test_register_esc_pool_then_start_failure_removes_matcher(dispatcher: HotkeyDispatcher, monkeypatch):
    """If pooling succeeds but ``start()`` raises, the extra matcher"""
    shared_native = _install_shared_native(dispatcher)
    esc_backend = MagicMock(name="esc")
    esc_backend.is_alive.return_value = True
    esc_backend.start.side_effect = RuntimeError("start failed")
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=esc_backend),
    )

    dispatcher.register_esc()  # must not raise (outer except swallows)

    assert dispatcher._esc_backend is None
    assert dispatcher._esc_spec is None
    assert dispatcher._esc_callback is None
    shared_native.add_extra_matcher.assert_called_once_with("esc", "<esc>")
    shared_native.remove_extra_matcher.assert_called_once_with("esc")


def test_register_repaste_pool_then_start_failure_removes_matcher(dispatcher: HotkeyDispatcher, monkeypatch):
    """Same pool-then-start failure contract for the repaste role."""
    shared_native = _install_shared_native(dispatcher)
    dispatcher._app.config.repaste_hotkey = "<ctrl>+<shift>+<v>"
    repaste_backend = MagicMock(name="repaste")
    repaste_backend.is_alive.return_value = True
    repaste_backend.start.side_effect = RuntimeError("start failed")
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=repaste_backend),
    )

    dispatcher.register_repaste()  # must not raise

    assert dispatcher._repaste_backend is None
    assert dispatcher._repaste_spec is None
    assert dispatcher._repaste_callback is None
    shared_native.add_extra_matcher.assert_called_once_with("repaste", "<ctrl>+<shift>+<v>")
    shared_native.remove_extra_matcher.assert_called_once_with("repaste")


def test_esc_reregister_after_unregister_re_adds_single_matcher(dispatcher: HotkeyDispatcher, monkeypatch):
    """Unregister then re-register ESC must re-pool exactly ONE"""
    shared_native = _install_shared_native(dispatcher)
    esc_backend = MagicMock(name="esc")
    esc_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=esc_backend),
    )

    dispatcher.register_esc()
    assert shared_native.add_extra_matcher.call_count == 1

    dispatcher.unregister_esc()
    assert shared_native.remove_extra_matcher.call_count == 1
    assert dispatcher._esc_callback is None

    dispatcher.register_esc()
    # Re-add after remove: still exactly one role registration, not two.
    assert shared_native.add_extra_matcher.call_count == 2
    shared_native.add_extra_matcher.assert_called_with("esc", "<esc>")
    assert shared_native.remove_extra_matcher.call_count == 1
    assert dispatcher._esc_spec == "<esc>"
    assert dispatcher._esc_callback is not None
    # Shared dictation backend was never stopped by the aux cycle.
    assert dispatcher._shared_backend is not None
    dispatcher._shared_backend.stop.assert_not_called()


def test_remove_shared_extra_matcher_noop_without_shared_native(dispatcher):
    """``_remove_shared_extra_matcher`` is a no-op when pooling is"""
    dispatcher._shared_backend = None
    dispatcher._remove_shared_extra_matcher("esc")  # must not raise


def test_remove_shared_extra_matcher_noop_for_unknown_role(dispatcher):
    """Removing a role that was never pooled is safe (mirrors the"""
    shared_native = _install_shared_native(dispatcher)
    dispatcher._remove_shared_extra_matcher("never-registered")
    shared_native.remove_extra_matcher.assert_called_once_with("never-registered")
