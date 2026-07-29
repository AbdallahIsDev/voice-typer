"""Extracted configuration-related startup methods from ``VoiceTyperApp``.

These functions were originally methods on ``VoiceTyperApp`` (in
``voice_typer/server/app.py``) and have been extracted into standalone
functions for testability. Each function takes ``app`` (the
``VoiceTyperApp`` instance) as its first parameter and accesses state via
``app.config``, ``app.tray``, ``app._microphones``, ``app._shutting_down``,
etc. — exactly the same attributes the original ``self.*`` references
resolved to.

The original delegate methods on ``VoiceTyperApp`` were removed during the
RW-9 god-class decomposition; callers (and tests) now invoke these
functions directly (e.g.
``monkeypatch.setattr(startup_tasks, "sync_autostart", ...)``).

A note on monkeypatching: tests like ``test_autostart_syncs_with_platform``
replace ``voice_typer.server.app.is_autostart_enabled`` /
``enable_autostart`` / ``disable_autostart`` / ``list_microphones`` at
call time. To keep those patches effective, the platform-helper names are
looked up dynamically from the ``voice_typer.server.app`` module inside
the relevant functions rather than being captured at import time.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from voice_typer.server import task_scheduler
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_windows
from voice_typer.server.providers import AppProtocol
from voice_typer.server.server_platform import create_launcher_shortcut

log = logging.getLogger(__name__)


# PERF-FIX-2: cache the macOS ApplicationServices framework handle at
# module level instead of re-loading it every 60s in
# ``_check_accessibility``. ``ctypes.cdll.LoadLibrary`` is not free
# (it calls dlopen + resolves symbols), and the handle is safe to
# share across threads (the underlying C function ``AXIsProcessTrusted``
# is thread-safe). ``None`` means "not yet loaded" (or "load failed
# permanently" — in which case the pulse re-attempts on the first call
# of each process). The cache is best-effort: a permanent load failure
# (non-macOS, missing framework) leaves this as ``None`` and the pulse
# falls through to the "fail safe (assume not granted)" branch.
_APP_SERVICES_LIB: Any | None = None
_APP_SERVICES_LIB_LOADED: bool = False


def sync_autostart(app: AppProtocol) -> dict:
    """Ensure ``config.autostart`` matches the actual platform autostart state.

    PVT-060: returns a result dict ``{"registered": bool, "error": str | None}``
    so the caller (``ConfigApplier.apply_config_side_effects``) can
    propagate the autostart status to the ``set_config`` IPC response.
    The renderer reads ``autostart_status.registered`` /
    ``autostart_status.error`` to surface "Autostart registration
    failed: <reason>" instead of silently failing.

    The dict shape matches :func:`voice_typer.server.server_platform
    .enable_autostart_ex` so the renderer can use the same field names
    whether the status came from a config-change sync or a direct
    ``enable_autostart`` IPC call.

    Note: this function still calls the bool-returning
    ``app.enable_autostart`` / ``app.disable_autostart`` (not the rich
    ``enable_autostart_ex``) so existing tests that monkeypatch
    ``voice_typer.server.app.enable_autostart`` continue to take
    effect. The error string is therefore only populated when the
    bool function raises (defensive — the production ``enable_autostart``
    catches exceptions internally and returns False, so ``error`` will
    typically be ``None`` even on failure). A future refactor that
    routes through ``enable_autostart_ex`` directly will populate
    ``error`` with the real failure reason.
    """
    # Look up the platform helpers from the app module at call time so
    # tests that monkeypatch voice_typer.server.app.{is_autostart_enabled,
    # enable_autostart, disable_autostart} still take effect.
    from voice_typer.server import app as _app_module

    # ER-73(a): track the post-sync ACTUAL OS-level autostart state so the
    # caller can pass it straight to ``tray.set_autostart_enabled(...)`` without
    # re-invoking ``is_autostart_enabled()``. The pre-ER-73 startup path
    # called ``is_autostart_enabled()`` twice back-to-back on the startup hot
    # path (once inside sync_autostart, once immediately after in
    # startup_sequence) — both calls hit the same platform helper (Win32
    # registry / launchctl plist / XDG autostart file) and return the same
    # value, so the second call was pure waste. The ``actual_post_sync``
    # field is the post-sync OS state derived from the read + the
    # enable/disable success flag, so callers no longer need to re-query.
    result: dict = {"registered": False, "error": None, "actual_post_sync": False}
    try:
        actual = _app_module.is_autostart_enabled()
        if app.config.autostart and not actual:
            log.info("[CONFIG] Config says autostart=true but it is disabled -- enabling")
            registered = _app_module.enable_autostart()
            # PVT-060: capture the post-enable state. enable_autostart()
            # returns True on success; on failure (exception caught
            # internally) it returns False — we surface that as
            # registered=False, error=None (the error is logged inside
            # enable_autostart_ex).
            # ER-73(a): ``actual_post_sync`` is True iff the enable succeeded
            # (registered is True); on failure the OS state is unchanged
            # (still False, the value we read at the top of this branch).
            result = {
                "registered": bool(registered),
                "error": None,
                "actual_post_sync": bool(registered),
            }
        elif not app.config.autostart and actual:
            log.info("[CONFIG] Config says autostart=false but it is enabled -- disabling")
            removed = _app_module.disable_autostart()
            # ``registered`` in the result dict reflects "is the
            # autostart entry now in the desired state?". After a
            # successful disable, the entry is NO LONGER registered,
            # so ``registered = removed`` (True if disable succeeded).
            # ER-73(a): ``actual_post_sync`` is the post-disable OS state —
            # False iff the disable succeeded (removed is True); on failure
            # the OS state is unchanged (still True, the value we read at
            # the top of this branch).
            result = {
                "registered": bool(removed),
                "error": None,
                "actual_post_sync": not bool(removed),
            }
        else:
            # Already in sync — report the current state.
            # ER-73(a): ``actual_post_sync`` mirrors the unchanged OS state.
            result = {
                "registered": bool(actual),
                "error": None,
                "actual_post_sync": bool(actual),
            }
    except Exception as e:
        log.warning("[CONFIG] Autostart sync failed: %s", e)
        # ER-73(a): on failure we don't know the post-sync OS state — leave
        # ``actual_post_sync`` as False (the conservative default). Callers
        # that need a definitive read can still call ``is_autostart_enabled()``
        # explicitly, but the startup path treats this as "autostart is off"
        # (the safer default for tray-menu display — avoids showing a
        # stale "enabled" checkmark next to a disabled entry).
        result = {"registered": False, "error": str(e), "actual_post_sync": False}
    return result


def sync_prewarm_task(app: AppProtocol, shutdown_event: threading.Event | None = None) -> dict:
    """Ensure the OS prewarm scheduled task matches the user's fast_startup setting.

    PW-3: when ``app.config.fast_startup`` is False, the prewarm task is
    UNREGISTERED so the OS scheduler doesn't fire a process that exits
    immediately with EXIT_DISABLED. When True (the default), the task is
    registered as before.

    Falls back gracefully if the platform doesn't support scheduled
    tasks (non-Windows).

    RACE-020: accepts an optional shutdown_event so the task can
    abort early if the app is quitting during startup.

    PVT-060/PW-3: returns a result dict
    ``{"registered": bool, "error": str | None}`` so the caller
    (``ConfigApplier.apply_config_side_effects``) can propagate the
    prewarm status to the ``set_config`` IPC response. The renderer
    reads ``prewarm_status.registered`` / ``prewarm_status.error`` to
    surface "Prewarm task registration failed: <reason>".
    """
    if not task_scheduler.is_supported():
        # Non-Windows platforms don't have a prewarm scheduled task —
        # report a no-op success so the renderer doesn't show a
        # spurious error on Linux/macOS.
        return {"registered": False, "error": None}
    if shutdown_event is not None and shutdown_event.is_set():
        return {"registered": False, "error": None}
    try:
        fast_startup = bool(getattr(app.config, "fast_startup", True))
        registered = task_scheduler.is_prewarm_registered()
        if shutdown_event is not None and shutdown_event.is_set():
            return {"registered": bool(registered), "error": None}
        if fast_startup:
            if not registered:
                log.info("[CONFIG] Registering prewarm scheduled task")
                task_scheduler.register_prewarm_task()
                registered = True
        else:
            # PW-3: user disabled prewarm — unregister the OS task so
            # it stops firing silently. Idempotent: if not registered,
            # unregister is a no-op.
            if registered:
                log.info("[CONFIG] fast_startup disabled — unregistering prewarm scheduled task")
                task_scheduler.unregister_prewarm_task()
                registered = False
        return {"registered": bool(registered), "error": None}
    except Exception as e:
        log.warning("[CONFIG] Prewarm task sync failed: %s", e)
        return {"registered": False, "error": str(e)}


def ensure_desktop_shortcut(app: AppProtocol) -> None:
    """Create the Desktop + Start Menu shortcuts on first run.

    Also migrates away the legacy backend-only ``Voice Typer.bat`` that
    pointed at ``pythonw -m voice_typer`` (which started the backend
    with no Electron, so the bubble overlay never worked).  That .bat
    is removed so the user is left with only the correct universal
    launcher shortcut.
    """
    if not is_windows():
        return
    desktop = Path.home() / "Desktop"
    legacy_bat = desktop / "Voice Typer.bat"

    # 1. Migrate: remove the legacy backend-only .bat so the broken
    #    "no bubble" shortcut stops shadowing the correct one.
    try:
        if legacy_bat.exists() and "-m voice_typer" in legacy_bat.read_text(encoding="utf-8", errors="replace"):
            legacy_bat.unlink()
            log.info("[STARTUP] Removed legacy backend-only shortcut: %s", legacy_bat)
    except OSError:
        pass

    # 2. Ensure the universal-launcher shortcut exists.
    #    create_launcher_shortcut() skips .lnk files that already exist,
    #    so this is a no-op on subsequent startups.
    try:
        create_launcher_shortcut()
    except Exception as e:
        log.debug("[STARTUP] Desktop shortcut creation skipped: %s", e)


def load_microphones(app: AppProtocol, shutdown_event: threading.Event | None = None) -> None:
    """Enumerate microphones and update the tray menu.

    RACE-020: accepts an optional shutdown_event so the task can
    abort early if the app is quitting during startup.

    AUDIO-MIC: detects device changes by comparing the new list against
    the cached one. When the set of device IDs changes (USB mic
    plugged/unplugged), pushes a ``microphones_changed`` IPC event so
    the Electron renderer can refresh its microphone dropdown without
    a manual "Refresh" click. The comparison is done via ``old_ids``
    and ``new_ids`` sets.
    """
    # Look up list_microphones from the app module at call time so tests
    # that monkeypatch voice_typer.server.app.list_microphones still
    # take effect.
    from voice_typer.server import app as _app_module

    # RACE-020: abort early if shutting down
    if shutdown_event is not None and shutdown_event.is_set():
        return
    try:
        mics = _app_module.list_microphones()
        # AUDIO-MIC: detect device changes by comparing the new
        # list against the cached one. If the set of device IDs
        # changed (USB mic plugged/unplugged), notify the UI via
        # IPC push event so the Electron renderer can refresh its
        # microphone dropdown without a manual "Refresh" click.
        old_ids = {m["id"] for m in app._microphones} if app._microphones else set()
        new_ids = {m["id"] for m in mics}
        app._microphones = mics
        app.tray.set_microphones(mics)
        # Log INFO on first load or when device count changes.
        # Routine polls where nothing changed log nothing — the
        # microphones_changed IPC event handles UI updates.
        if not old_ids:
            log.info("[RECORDING] Found %d microphone(s)", len(mics))
        elif len(mics) != len(old_ids):
            log.info("[RECORDING] Microphone count changed: %d -> %d", len(old_ids), len(mics))
        # AUDIO-MIC: push a device-change IPC event if the device
        # set changed since the last enumeration.
        if old_ids and old_ids != new_ids:
            added = new_ids - old_ids
            removed = old_ids - new_ids
            log.info(
                "[AUDIO-MIC] Device set changed: +%d added, -%d removed",
                len(added),
                len(removed),
            )
            try:
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "microphones_changed",
                        "data": {"count": len(mics)},
                    }
                )
            except Exception:
                pass
    except Exception as e:
        log.warning("[RECORDING] Could not enumerate microphones: %s", e)


def start_accessibility_pulse(app: AppProtocol, initial_state: bool) -> None:
    """PLAT-009: Periodically re-check macOS Accessibility permission.

    Runs on macOS only. Every 60 seconds, re-invokes
    ``AXIsProcessTrusted()`` and fires ``tray.notify_safety`` only
    on state transitions (granted→revoked or revoked→granted) so
    the user isn't spammed with repeated notifications.

    Pre-fix: accessibility was checked once at startup. If the user
    granted permission after startup, the app never recovered until
    restart. With this pulse, the app detects the change within 60s.

    PERF-FIX-2: two allocation patterns were cleaned up:

    - ``threading.Event().wait(1.0)`` in the 60-iteration sleep loop
      previously allocated a fresh ``Event`` object every second. We
      now create a single ``Event`` once and reuse it for the lifetime
      of the pulse thread.
    - ``ctypes.cdll.LoadLibrary(".../ApplicationServices")`` was called
      every 60s in ``_check_accessibility``. The handle is now cached
      at module level (``_APP_SERVICES_LIB``) and reused for the
      lifetime of the process — ``dlopen`` is idempotent on an already-
      loaded framework but still does a symbol-table lookup, which is
      wasted work on a 60s heartbeat.
    """

    def _check_accessibility() -> bool:
        """Return True if Accessibility permission is granted.

        PERF-FIX-2: uses the module-level cached ApplicationServices
        handle. The first call loads the framework; subsequent calls
        reuse the cached handle. A permanent load failure leaves the
        cache as ``None`` and this function returns False (fail safe).
        """
        global _APP_SERVICES_LIB, _APP_SERVICES_LIB_LOADED
        if not _APP_SERVICES_LIB_LOADED:
            try:
                import ctypes

                _APP_SERVICES_LIB = ctypes.cdll.LoadLibrary(
                    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                )
            except Exception:
                _APP_SERVICES_LIB = None
            finally:
                _APP_SERVICES_LIB_LOADED = True
        if _APP_SERVICES_LIB is None:
            return False  # fail safe (assume not granted)
        try:
            return bool(_APP_SERVICES_LIB.AXIsProcessTrusted())
        except Exception:
            return False  # fail safe (assume not granted)

    def _pulse_loop(stop_event: threading.Event) -> None:
        # PERF-FIX-2: allocate ONE Event for the lifetime of the pulse
        # thread and reuse it. The previous code called
        # ``threading.Event().wait(1.0)`` in a 60-iteration loop, which
        # allocated a fresh Event object (and its underlying condition
        # variable + lock) every second — ~3.6k allocations/hour per
        # pulse thread.
        #
        # PERF-25 / TY-16: the loop now also watches ``stop_event``
        # (registered with ``app._thread_registry``) so ``shutdown_all()``
        # can signal an early exit instead of waiting up to 60s for the
        # next ``app._shutting_down`` poll. ``stop_event.set()`` from
        # ``shutdown_all()`` wakes the thread IMMEDIATELY on shutdown —
        # the 60s timeout only governs the AXIsProcessTrusted() recheck
        # interval, which is the actual purpose of the loop. The previous
        # 60-iteration 1s loop (PERF-25) was added so shutdown signals
        # would be picked up within ~1s, but ``stop_event.wait()`` already
        # wakes immediately on ``stop_event.set()`` — the 1s slicing was
        # redundant and caused 60 kernel thread wakeups per minute for
        # the lifetime of the app (~2.4-12 Wh/day wasted on battery per
        # the TY-16 finding). The defensive ``app._shutting_down`` check
        # is kept for callers that don't go through the registry.
        last_state = initial_state
        while not app._shutting_down:
            # TY-16: single 60s wait — ``stop_event.set()`` from
            # ``shutdown_all()`` wakes the thread immediately on
            # shutdown; the 60s timeout only governs the
            # AXIsProcessTrusted() recheck interval. ``stop_event.wait``
            # returns True when set (shutdown signalled), False on
            # timeout (60s elapsed — recheck AXIsProcessTrusted()).
            if stop_event.wait(timeout=60.0):
                return
            if app._shutting_down or stop_event.is_set():
                return
            current = _check_accessibility()
            if current != last_state:
                if current:
                    log.info("[PLAT-009] macOS Accessibility permission granted")
                    with contextlib.suppress(Exception):
                        app.tray.notify(
                            APP_NAME,
                            "Accessibility permission granted. Hotkeys are now active.",
                        )
                else:
                    log.warning("[PLAT-009] macOS Accessibility permission revoked")
                    with contextlib.suppress(Exception):
                        app.tray.notify_safety(
                            f"{APP_NAME} — Accessibility Revoked",
                            "Global hotkeys have been disabled. Open System Settings "
                            "\u2192 Privacy & Security \u2192 Accessibility to re-grant.",
                        )
                last_state = current

    # PERF-25: dedicated stop_event so ``app._thread_registry`` can
    # signal the pulse thread to exit during ``shutdown_all()``. The
    # thread is also still gated on ``app._shutting_down`` for
    # backward compat with any code path that doesn't go through the
    # registry.
    stop_event = threading.Event()
    t = threading.Thread(target=_pulse_loop, args=(stop_event,), daemon=True, name="A11yPulse")
    # RACE-008: daemon=True is acceptable — the pulse only reads
    # permission state, no critical cleanup. With the stop_event +
    # registry, shutdown is signalled within ~1s.
    t.start()
    # PERF-25: register with the central ThreadRegistry so
    # ``shutdown_all()`` signals + joins this thread. join_timeout=2.0
    # matches the original "exits within 1 second" contract with a
    # safety margin.
    registry = getattr(app, "_thread_registry", None)
    if registry is not None:
        try:
            registry.register(
                name="A11yPulse",
                thread=t,
                stop_event=stop_event,
                join_timeout=2.0,
            )
        except Exception:
            log.debug("[STARTUP] could not register A11yPulse with ThreadRegistry", exc_info=True)


# ─── Onboarding reset (PVT-012 / re-run setup wizard) ─────────────────────


def reset_onboarding_complete(
    config_dir: Path | None = None,
    *,
    app: object | None = None,
) -> dict:
    """PVT-012 — delete the ``.onboarding_complete`` marker so the wizard
    re-runs on next launch.

    This is the backend primitive for the "Re-run setup wizard"
    affordance in Settings → Advanced. The renderer calls a future
    ``onboarding_reset`` IPC handler which delegates to this function;
    on next app launch, :meth:`OnboardingController.is_first_run`
    returns True (because the marker is gone) and the wizard re-appears.

    NOTE: this function is defined here (in ``startup_tasks.py``) rather
    than as a method on :class:`OnboardingController` because the file
    scope of this fix sub-agent does not include ``onboarding.py``.
    When ``onboarding.py`` is updated to add the
    :meth:`OnboardingController.reset` method, that method should
    delegate to this function so the marker-deletion logic has a single
    source of truth.

    Parameters
    ----------
    config_dir:
        Optional override for the config directory (defaults to the
        canonical :func:`voice_typer.server.config._config_dir`).
        Used by tests to point at a tmp_path.
    app:
        Optional :class:`voice_typer.server.app.VoiceTyperApp` instance.
        When provided, the ``onboarding_completed`` flag is mutated on
        the live ``app.config`` object and persisted via
        ``app.config.save_strict()`` — which acquires the config-mutation
        lock so the write cannot race a concurrent
        ``set_config`` IPC handler. When ``None`` (e.g. tests), falls
        back to a fresh ``Config.load()`` snapshot + ``cfg.save()``;
        this bypasses the lock and is acceptable for the test-only path
        but callers should pass ``app`` whenever one is in scope.

    Returns
    -------
    dict
        ``{"reset": bool, "error": str | None}`` where ``reset`` is
        True if the marker was deleted (or already absent — idempotent).
        The renderer surfaces ``error`` if the deletion failed (e.g.
        permission denied on the marker file).
    """
    try:
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        marker = Path(config_dir) / ".onboarding_complete"
        if marker.exists():
            marker.unlink()
            log.info("[ONBOARDING] Reset onboarding marker: %s", marker)
        else:
            log.info("[ONBOARDING] Reset onboarding marker (already absent): %s", marker)
        # Also clear the ``onboarding_completed`` flag in config.json so
        # ``OnboardingController.is_first_run`` returns True even if the
        # marker file is recreated by a stale save.
        # Prefer the live ``app.config`` over a fresh ``Config.load()``
        # snapshot so the mutation goes through the config-mutation lock
        # (acquired inside ``Config.save`` / ``Config.save_strict``). A
        # ``Config.load()`` + ``cfg.save()`` sequence reads WITHOUT the
        # lock and could overwrite a concurrent ``set_config`` IPC
        # handler's write. When the caller has the app instance in
        # scope, they should pass it.
        if app is not None:
            try:
                cfg = getattr(app, "config", None)
                if cfg is not None and getattr(cfg, "onboarding_completed", False):
                    # Acquire the app's config-mutation lock around the
                    # read-modify-save cycle to prevent racing a concurrent
                    # ``set_config`` IPC handler. ``Config.save_strict()`` does
                    # NOT acquire ``app._config_mutation_lock`` on its own
                    # (``Config._mutation_lock`` is only wired up via an
                    # explicit ``set_mutation_lock()`` call, which is never
                    # invoked — see ADR-0008-§3.1 for the locking contract).
                    # ``RLock`` reentrancy makes this safe even if an IPC
                    # handler already holding the lock delegates here.
                    lock = getattr(app, "_config_mutation_lock", None)
                    if lock is not None:
                        with lock:
                            cfg.onboarding_completed = False
                            cfg.save_strict()
                    else:
                        cfg.onboarding_completed = False
                        cfg.save_strict()
                    log.info("[ONBOARDING] Cleared onboarding_completed flag in config.json (via app.config)")
            except Exception:
                log.debug("[ONBOARDING] could not clear onboarding_completed via app.config", exc_info=True)
        else:
            # Fall back to ``Config.load()`` + ``cfg.save()`` for the
            # test-only path (no app instance available). The save
            # still acquires the config-mutation lock, but the load
            # bypasses it — a known race window that callers can close
            # by passing ``app``.
            try:
                from voice_typer.server.config import Config

                cfg = Config.load()
                if getattr(cfg, "onboarding_completed", False):
                    cfg.onboarding_completed = False
                    cfg.save()
                    log.info("[ONBOARDING] Cleared onboarding_completed flag in config.json")
            except Exception:
                log.debug("[ONBOARDING] could not clear onboarding_completed in config.json", exc_info=True)
        return {"reset": True, "error": None}
    except Exception as exc:
        log.exception("[ONBOARDING] Failed to reset onboarding marker")
        return {"reset": False, "error": str(exc)}
