"""Extracted configuration-related startup methods from ``VoiceTyperApp``.

These functions were originally methods on ``VoiceTyperApp`` (in
``voice_typer/server/app.py``) and have been extracted into standalone
functions for testability. Each function takes ``app`` (the
``VoiceTyperApp`` instance) as its first parameter and accesses state via
``app.config``, ``app.tray``, ``app._microphones``, ``app._shutting_down``,
etc. — exactly the same attributes the original ``self.*`` references
resolved to.

The original delegate methods on ``VoiceTyperApp`` were removed during the
god-class decomposition; callers (and tests) now invoke these
functions directly (e.g.
``monkeypatch.setattr(startup_tasks, "sync_autostart", ...)``).

A note on monkeypatching: tests like ``test_autostart_syncs_with_platform``
replace ``voice_typer.server.server_platform.is_autostart_enabled`` /
``enable_autostart`` / ``disable_autostart`` / ``list_microphones`` at
call time. To keep those patches effective, the platform-helper names are
imported inside the relevant functions (deferred import from the
canonical ``server_platform`` module) rather than being captured at
import time.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from voice_typer.server import onboarding_status
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_windows
from voice_typer.server.providers import AppProtocol
from voice_typer.server.server_platform import create_launcher_shortcut
from voice_typer.server.server_platform.macos_bundle_id import resolve_host_bundle_id

log = logging.getLogger(__name__)


# cache the macOS ApplicationServices framework handle at
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


def _a11y_regrant_message(bundle_id: str | None) -> str:
    """Build the macOS Accessibility re-grant notification body.

    When the HOST app's bundle ID can be resolved at runtime (see
    ``resolve_host_bundle_id``), the message includes the exact
    ``tccutil reset Accessibility <bundle-id>`` command for the
    currently-running runtime (Electron or Tauri). When it cannot be
    resolved (dev-mode run without an ``.app`` in the process chain),
    fall back to the generic System Settings walkthrough — a wrong
    bundle ID in a ``tccutil`` command is worse than no command.
    """
    # TCC-002: the command string comes from the single construction
    # point in macos_bundle_id (tccutil_reset_command_str), so a future
    # change to tccutil invocation lands in one place.
    from voice_typer.server.server_platform.macos_bundle_id import tccutil_reset_command_str

    if bundle_id:
        return (
            "Voice Typer was updated — Accessibility permission may "
            f"need to be re-granted. Run: {tccutil_reset_command_str('Accessibility', bundle_id)}"
        )
    return (
        "Voice Typer was updated — Accessibility permission may "
        "need to be re-granted. Open System Settings "
        "-> Privacy & Security -> Accessibility to re-grant."
    )


def sync_autostart(app: AppProtocol) -> dict:
    """Ensure ``config.autostart`` matches the actual platform autostart state.

    returns a result dict ``{"registered": bool, "error": str | None}``
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
        ``autostart.enable_autostart`` / ``autostart.disable_autostart``
        (not the rich ``enable_autostart_ex``) so existing tests that
        monkeypatch ``voice_typer.server.server_platform.autostart.enable_autostart``
        continue to take
        effect. The error string is therefore only populated when the
        bool function raises (defensive — the production ``enable_autostart``
        catches exceptions internally and returns False, so ``error`` will
        typically be ``None`` even on failure). A future refactor that
        routes through ``enable_autostart_ex`` directly will populate
        ``error`` with the real failure reason.
    """
    # Import the autostart facade module at call time so tests that
    # monkeypatch voice_typer.server.server_platform.autostart.{
    # is_autostart_enabled, enable_autostart, disable_autostart} still
    # take effect (the attribute is resolved on that module at each call).
    from voice_typer.server.server_platform import autostart as _autostart

    # One-time per-install cleanup of legacy autostart entries
    # (AUTOSTART-LEGACY): pre-PLAT-RUN fixed names + the buggy
    # ``sys.executable``-derived hashes could leave multiple live
    # ``VoiceTyper*`` Run keys / ``VoiceTyperAutostart*`` tasks /
    # ``VoiceTyper*.bat`` files that ALL point at this install and ALL
    # fire at logon. The sweep is marker-gated (per install hash), so
    # after the first run the only cost here is a single
    # ``Path.exists()`` check. Best-effort: a sweep failure must never
    # break autostart sync.
    try:
        from voice_typer.server.config import _config_dir as _cfg_dir
        from voice_typer.server.server_platform import sweep_legacy_autostart_entries

        _sweep = sweep_legacy_autostart_entries(_cfg_dir())
        if _sweep.get("swept"):
            _removed = _sweep.get("removed", {})
            _total = sum(len(v) for v in _removed.values())
            if _total:
                log.info(
                    "[AUTOSTART] Legacy autostart sweep removed %s duplicate entry(s): %s",
                    _total,
                    _removed,
                )
    except Exception:
        log.debug("[AUTOSTART] Legacy autostart sweep failed", exc_info=True)

    # (a): track the post-sync ACTUAL OS-level autostart state so the
    # caller can pass it straight to ``tray.set_autostart_enabled(...)`` without
    # re-invoking ``is_autostart_enabled()``. The pre- startup path
    # called ``is_autostart_enabled()`` twice back-to-back on the startup hot
    # path (once inside sync_autostart, once immediately after in
    # startup_sequence) — both calls hit the same platform helper (Win32
    # registry / launchctl plist / XDG autostart file) and return the same
    # value, so the second call was pure waste. The ``actual_post_sync``
    # field is the post-sync OS state derived from the read + the
    # enable/disable success flag, so callers no longer need to re-query.
    result: dict = {"registered": False, "error": None, "actual_post_sync": False}
    try:
        actual = _autostart.is_autostart_enabled()
        if app.config.autostart and not actual:
            log.info("[CONFIG] Config says autostart=true but it is disabled -- enabling")
            registered = _autostart.enable_autostart()
            # capture the post-enable state. enable_autostart()
            # returns True on success; on failure (exception caught
            # internally) it returns False — we surface that as
            # registered=False, error=None (the error is logged inside
            # enable_autostart_ex).
            # (a): ``actual_post_sync`` is True iff the enable succeeded
            # (registered is True); on failure the OS state is unchanged
            # (still False, the value we read at the top of this branch).
            result = {
                "registered": bool(registered),
                "error": None,
                "actual_post_sync": bool(registered),
            }
            log.info(
                "[CONFIG] Autostart sync: enable attempted, registered=%s, post_sync_state=%s",
                result["registered"],
                result["actual_post_sync"],
            )
        elif not app.config.autostart and actual:
            log.info("[CONFIG] Config says autostart=false but it is enabled -- disabling")
            removed = _autostart.disable_autostart()
            # ``registered`` in the result dict reflects "is the
            # autostart entry now in the desired state?". After a
            # successful disable, the entry is NO LONGER registered,
            # so ``registered = removed`` (True if disable succeeded).
            # (a): ``actual_post_sync`` is the post-disable OS state —
            # False iff the disable succeeded (removed is True); on failure
            # the OS state is unchanged (still True, the value we read at
            # the top of this branch).
            result = {
                "registered": bool(removed),
                "error": None,
                "actual_post_sync": not bool(removed),
            }
            log.info(
                "[CONFIG] Autostart sync: disable attempted, removed=%s, post_sync_state=%s",
                result["registered"],
                result["actual_post_sync"],
            )
        else:
            # Already in sync — report the current state.
            # (a): ``actual_post_sync`` mirrors the unchanged OS state.
            result = {
                "registered": bool(actual),
                "error": None,
                "actual_post_sync": bool(actual),
            }
            log.info(
                "[CONFIG] Autostart already in sync (config=%s, os=%s)",
                bool(app.config.autostart),
                bool(actual),
            )
    except Exception as e:
        log.warning("[CONFIG] Autostart sync failed: %s", e)
        # (a): on failure we don't know the post-sync OS state — leave
        # ``actual_post_sync`` as False (the conservative default). Callers
        # that need a definitive read can still call ``is_autostart_enabled()``
        # explicitly, but the startup path treats this as "autostart is off"
        # (the safer default for tray-menu display — avoids showing a
        # stale "enabled" checkmark next to a disabled entry).
        result = {"registered": False, "error": str(e), "actual_post_sync": False}
    return result


def sync_prewarm_task(app: AppProtocol, shutdown_event: threading.Event | None = None) -> dict:
    """No-op stub retained for caller compatibility.

    Prewarm became a worker startup phase (master plan §6.2 P-1): the
    OS-level scheduled-task registration (Windows Task Scheduler /
    macOS LaunchAgent / Linux systemd user timer) was deleted along
    with the prewarm binary it launched. There is no longer anything
    to register or unregister here.

    The renderer's Settings page still surfaces a "Fast Startup"
    toggle (and the ``set_config`` IPC response still carries a
    ``prewarm_status`` field) — this stub returns a no-op success so
    the renderer doesn't show a spurious error when the user toggles
    the (now-cosmetic) setting. The actual cache-warming happens
    inside the worker process on each spawn.
    """
    _ = app  # unused — kept for signature backward-compat
    _ = shutdown_event  # unused — kept for signature backward-compat
    return {"registered": False, "error": None}


def check_offline_pack_on_launch(app: AppProtocol, shutdown_event: threading.Event | None = None) -> dict:
    """Phase 2d launch-time offline-pack existence check (§8.10, §8.16).

    Runs on a fire-and-forget daemon thread at startup (see
    ``StartupSequence._startup_parallel_work``). Never blocks the window:

    1. **Cheap existence check** (``update_check._local_offline_pack_version``
       — ``iterdir`` + ``pack-manifest.json`` presence, NO SHA-256 hashing)
       on the hot startup path.
    2. **Pack present** → spawn :class:`offline_pack.BackgroundChecksum`
       on its own daemon thread (§8.16: the full checksum runs in the
       background and publishes ``offline_pack_verified`` /
       ``offline_pack_corrupt``). Launch is never slowed.
    3. **Pack missing** → publish the ``offline_pack_missing`` event
       (§8.10 — the renderer's ``useOfflinePackDownload`` hook flips to
       the "missing" state) and run ``check_offline_pack_update`` with
       ``trigger_download=True`` — the silent re-download. The download
       is consent-gated (``offline_pack_consent``); when consent is off
       the check returns ``consent_required=True`` and nothing is
       downloaded (C-DATA-1).

    Best-effort: never raises. All failures are caught and logged so a
    broken pack-root scan can never abort startup.

    Returns a small outcome dict (testable):
    ``{"checked": True, "installed_version": <str|None>, ...}``.
    """
    try:
        from voice_typer.server import event_bus as _event_bus_module
        from voice_typer.server.service import offline_pack, update_check

        config = getattr(app, "config", None)
        event_bus = _event_bus_module

        # 1. Cheap existence check — no hashing (§8.10).
        local_version: str | None = None
        try:
            local_version = update_check._local_offline_pack_version()
        except Exception:  # noqa: BLE001 — a corrupt pack root must not abort startup
            log.debug("[PACK] launch-time local pack scan failed", exc_info=True)

        if shutdown_event is not None and shutdown_event.is_set():
            return {"checked": False, "reason": "shutdown"}

        if local_version is not None:
            # 2. Present → background checksum (§8.16). Never blocks launch.
            try:
                background = offline_pack.BackgroundChecksum(local_version, event_bus=event_bus)
                background.start()
            except Exception:  # noqa: BLE001 — checksum spawn is best-effort
                log.exception("[PACK] background checksum spawn failed for %s", local_version)
            log.info(
                "[PACK] offline pack %s present at launch — background checksum started",
                local_version,
            )
            return {"checked": True, "installed_version": local_version, "checksum": "background"}

        # 3. Missing → publish offline_pack_missing (§8.10) + consent-gated
        #    silent re-download. The event is published even when consent
        #    is off so the renderer can show the "Preparing offline
        #    engine…" banner instead of silently failing later.
        try:
            offline_pack._publish_event(
                event_bus,
                "offline_pack_missing",
                {
                    "version": None,
                    "path": str(offline_pack._default_offline_pack_root()),
                },
            )
        except Exception:  # noqa: BLE001 — event publish is best-effort
            log.debug("[PACK] offline_pack_missing publish failed", exc_info=True)
        log.info("[PACK] offline pack missing at launch — consent-gated re-download check")

        try:
            result = update_check.check_offline_pack_update(config, event_bus, trigger_download=True)
            return {
                "checked": True,
                "installed_version": None,
                "update_check": dict(result),
            }
        except Exception:  # noqa: BLE001 — never raise from the launch task
            log.exception("[PACK] consent-gated re-download check failed (best-effort)")
            return {"checked": True, "installed_version": None, "update_check": None}
    except Exception:  # noqa: BLE001 — outermost guard: this task must never raise
        log.exception("[PACK] launch-time pack check failed (best-effort)")
        return {"checked": False, "reason": "error"}


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


def _reconcile_configured_microphone(app: AppProtocol, mics: list[dict]) -> None:
    """Validate ``app.config.microphone`` against the live device list.

    config.json is the CANONICAL settings store; the persisted value is
    either ``None`` (System Default — the documented canonical meaning) or
    a stable device id emitted by :func:`list_microphones`
    (``"<host api>|<name>[#N]"``, plus legacy shapes resolvable by
    :func:`find_microphone_by_id`).

    Runs during startup (and any tray-driven re-enumeration) so a stale,
    renamed, or unplugged persisted selection is reconciled BEFORE the
    renderer ever loads the Microphone page — the page renders the
    already-corrected state instead of discovering it as a side effect of
    being opened. The recovery is silent to the user (an internal
    configuration inconsistency, not a user-facing error); operators get
    a WARNING diagnostic naming the stale id and the recovery action.

    Never raises: enumeration/reconciliation failures must not break mic
    loading (mirrors the surrounding best-effort contract).
    """
    from voice_typer.server.server_platform.microphone_list import find_microphone_by_id

    try:
        mic_id = app.config.microphone
    except AttributeError:
        return
    # Only str/None are meaningful persisted values. Anything else is an
    # in-memory test fake / corrupt runtime state that IPC validators own —
    # never silently rewritten here.
    if mic_id is not None and not isinstance(mic_id, str):
        return
    if mic_id is None:
        log.debug("[MIC] Startup microphone check: System Default (no persisted selection)")
        return
    if not mics:
        # An EMPTY enumeration is NOT evidence that the configured device
        # is gone (failed PortAudio query / headless environment). Never
        # fall back on it — wait for a successful enumeration.
        log.debug("[MIC] Skipping microphone reconciliation: no devices enumerated")
        return

    resolved: dict | None = None
    try:
        resolved = find_microphone_by_id(mic_id)
    except Exception:
        log.debug("[MIC] Microphone resolution failed for %r", mic_id, exc_info=True)

    if resolved is not None:
        canonical = str(resolved.get("id", ""))
        if canonical and canonical != mic_id:
            # Legacy id shape (bare index / compound form) resolved to a
            # live device via find_microphone_by_id's fallback strategies
            # — persist its NEW stable id so every consumer agrees on one
            # representation going forward.
            lock = getattr(app, "_config_mutation_lock", None)
            with contextlib.ExitStack() as stack:
                if lock is not None:
                    stack.enter_context(lock)
                app.config.microphone = canonical
                saved = app.config.save()
            if saved:
                log.info(
                    "[MIC] Migrated legacy microphone id %r -> stable id %r (%s)",
                    mic_id,
                    canonical,
                    resolved.get("name", "?"),
                )
                _publish_mic_reconciled(app, {"microphone": canonical})
            else:
                log.warning(
                    "[MIC] Failed to persist legacy-id migration %r -> %r",
                    mic_id,
                    canonical,
                )
        else:
            log.info(
                "[MIC] Startup microphone check: configured device available: %s (%s)",
                resolved.get("name", "?"),
                mic_id,
            )
        return

    # Stale selection → SILENT user-facing recovery + diagnostic log.
    # No tray notification, no renderer snack: this is an internal config
    # inconsistency fixed at startup, not something the user did.
    lock = getattr(app, "_config_mutation_lock", None)
    with contextlib.ExitStack() as stack:
        if lock is not None:
            stack.enter_context(lock)
        app.config.microphone = None
        saved = app.config.save()
    if saved:
        log.warning(
            "[MIC] Configured microphone %r is not available on this machine "
            "(%d input device(s) found) — recovered to System Default and "
            "persisted null.",
            mic_id,
            len(mics),
        )
        _publish_mic_reconciled(app, {"microphone": None})
    else:
        log.error(
            "[MIC] Configured microphone %r is unavailable and persisting the "
            "System Default fallback FAILED — stale id left on disk; will "
            "retry at next startup.",
            mic_id,
        )


def _publish_mic_reconciled(app: AppProtocol, updates: dict) -> None:
    """Push a ``config_changed`` event after startup reconciliation.

    Lets connected renderers refresh their cached config without opening
    the Microphone page (same envelope shape as the IPC set_config push).
    Best-effort — no subscribers is fine.
    """
    try:
        from voice_typer.server import event_bus

        event_bus.publish({"type": "config_changed", "data": updates})
    except Exception:
        log.debug("[MIC] config_changed publish failed", exc_info=True)


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
    # Import list_microphones at call time so tests that monkeypatch
    # voice_typer.server.server_platform.microphone_list.list_microphones
    # still take effect.
    from voice_typer.server.server_platform.microphone_list import list_microphones

    # RACE-020: abort early if shutting down
    if shutdown_event is not None and shutdown_event.is_set():
        return
    try:
        mics = list_microphones()
        # Startup reconciliation: validate the PERSISTED selection against
        # the freshly enumerated devices so a stale/unavailable id falls
        # back to System Default (silently, with a diagnostic log) before
        # any consumer — tray, recorder, renderer — reads it. Must run on
        # EVERY enumeration path (startup AND tray refresh), not only once,
        # so the persisted value always matches reality.
        try:
            _reconcile_configured_microphone(app, mics)
        except Exception:
            # Belt-and-braces: a reconciler bug must never downgrade the
            # enumeration into "Could not enumerate microphones" (which
            # would leave tray + renderer without any device list).
            log.warning("[MIC] Microphone reconciliation failed", exc_info=True)
        # AUDIO-MIC: detect device changes by comparing the new
        # list against the cached one. If the set of device IDs
        # changed (USB mic plugged/unplugged), notify the UI via
        # IPC push event so the Electron renderer can refresh its
        # microphone dropdown without a manual "Refresh" click.
        app_mics = getattr(app, "_microphones", [])
        old_ids = {m["id"] for m in app_mics} if app_mics else set()
        new_ids = {m["id"] for m in mics}
        setattr(app, "_microphones", mics)  # noqa: B010 — attr not on AppProtocol; direct assignment fails pyrefly
        app.tray.set_microphones(mics)
        # Log INFO on first load or when device count changes.
        # Routine polls where nothing changed log nothing — the
        # microphones_changed IPC event handles UI updates.
        if not old_ids:
            log.info("[RECORDING] Found %d microphones", len(mics))
        elif len(mics) != len(old_ids):
            log.info("[RECORDING] Microphone count changed: %d -> %d", len(old_ids), len(mics))
        # AUDIO-MIC: push a device-change IPC event if the device
        # set changed since the last enumeration. ALSO publish on the
        # FIRST population (empty → non-empty): the renderer connects
        # and the restored Microphone page fetches ``get_microphones``
        # during the startup window BEFORE this task runs (verified from
        # voice-typer.log: TCP client connected 18:24:55, ``[RECORDING]
        # Found 3 microphones`` 18:24:58) — so its initial snapshot is an
        # empty list. Without this publish the page stays stale ("No
        # microphones found", Start Test disabled) until a manual page
        # change or a genuine hot-plug event.
        if (old_ids and old_ids != new_ids) or (not old_ids and new_ids):
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
    """Periodically re-check macOS Accessibility permission.

        Runs on macOS only. Every 60 seconds, re-invokes
        ``AXIsProcessTrusted()`` and fires ``tray.notify_safety`` only
        on state transitions (granted→revoked or revoked→granted) so
        the user isn't spammed with repeated notifications.

        Pre-fix: accessibility was checked once at startup. If the user
        granted permission after startup, the app never recovered until
        restart. With this pulse, the app detects the change within 60s.

    PERF-: two allocation patterns were cleaned up:

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

        PERF-: uses the module-level cached ApplicationServices
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
        # PERF-: allocate ONE Event for the lifetime of the pulse
        # thread and reuse it. The previous code called
        # ``threading.Event().wait(1.0)`` in a 60-iteration loop, which
        # allocated a fresh Event object (and its underlying condition
        # variable + lock) every second — ~3.6k allocations/hour per
        # pulse thread.
        #
        # PERF-25: the loop now also watches ``stop_event``
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
        # the  finding). The defensive ``app._shutting_down`` check
        # is kept for callers that don't go through the registry.
        last_state = initial_state
        while not app._shutting_down:
            # single 60s wait — ``stop_event.set()`` from
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
                    log.info("[A11Y] macOS Accessibility permission granted")
                    # persist the app version at which a11y was
                    # last observed granted. The next granted→denied
                    # transition compares the current ``voice_typer.__version__``
                    # against this value — if they differ, the denial
                    # is likely a TCC reset on app update (macOS Sequoia
                    # sometimes invalidates TCC grants on bundle-id-stable
                    # binary updates), and we surface a different tray
                    # notification pointing the user at ``tccutil reset
                    # Accessibility <bundle-id>`` — the bundle ID is
                    # resolved at runtime (see ``_a11y_regrant_message``)
                    # instead of the generic "Open System Settings" message.
                    # ``last_known_a11y_version`` is a future config
                    # field (currently owned by another agent — using
                    # ``getattr``/``setattr`` so this code is forward-
                    # compatible when the field is added). When the
                    # field is absent, ``getattr`` returns ``None``
                    # (in-session tracking only — no cross-session
                    # persistence until the field is declared).
                    try:
                        import voice_typer as _vt

                        _current_vt_version = getattr(_vt, "__version__", None)
                        if _current_vt_version is not None:
                            app.config.last_known_a11y_version = _current_vt_version
                    except Exception:
                        log.debug("[A11Y] could not persist last_known_a11y_version", exc_info=True)
                    with contextlib.suppress(Exception):
                        app.tray.notify(
                            APP_NAME,
                            "Accessibility permission granted. Hotkeys are now active.",
                        )
                else:
                    log.warning("[A11Y] macOS Accessibility permission revoked")
                    # detect version-change-induced TCC reset
                    # and surface a more actionable notification.
                    _version_changed = False
                    try:
                        import voice_typer as _vt

                        _current_vt_version = getattr(_vt, "__version__", None)
                        _last_known_version = getattr(app.config, "last_known_a11y_version", None)
                        _version_changed = (
                            _current_vt_version is not None
                            and _last_known_version is not None
                            and _current_vt_version != _last_known_version
                        )
                        if _version_changed:
                            log.warning(
                                "[A11Y] a11y denied after app version change (%s -> %s) — likely TCC reset on update",
                                _last_known_version,
                                _current_vt_version,
                            )
                    except Exception:
                        log.debug("[A11Y] could not compare a11y version", exc_info=True)
                    with contextlib.suppress(Exception):
                        if _version_changed:
                            # Resolve the HOST app's bundle ID at runtime:
                            # both the Electron and Tauri builds work, and a
                            # future bundle-identifier change needs no code
                            # edit here. If resolution fails (dev-mode run),
                            # ``_a11y_regrant_message`` falls back to the
                            # generic walkthrough.
                            app.tray.notify_safety(
                                f"{APP_NAME} — Accessibility Re-grant",
                                _a11y_regrant_message(resolve_host_bundle_id()),
                            )
                        else:
                            app.tray.notify_safety(
                                f"{APP_NAME} — Accessibility Revoked",
                                "Global hotkeys have been disabled. Open System Settings "
                                "-> Privacy & Security -> Accessibility to re-grant.",
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


# Onboarding reset ( / re-run setup wizard) ─────────────────────


def reset_onboarding_complete(
    config_dir: Path | None = None,
    *,
    app: object | None = None,
) -> dict:
    """Delete the ``.onboarding_complete`` AND ``.onboarding_started``
    markers so the wizard re-runs on next launch.

    This is the backend primitive for the "Re-run setup wizard"
    affordance in Settings → Advanced. The renderer calls a future
    ``onboarding_reset`` IPC handler which delegates to this function;
    on next app launch, :meth:`OnboardingController.is_first_run`
    returns True (because the marker is gone) and the wizard re-appears.

    Marker consistency: BOTH ``.onboarding_complete`` and
    ``.onboarding_started`` are deleted. The
    :meth:`OnboardingController.reset` method deletes both — the IPC
    handler must do the same or it leaves a stale
    ``.onboarding_started`` marker. If that marker survives, the
    auto-heal (in ``startup_sequence``) treats the next launch as a
    mid-wizard crash and SKIPS the auto-heal, so the wizard never
    re-appears even though the user explicitly requested a re-run.

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
        # Delete the merged ``.onboarding_status.json`` document (which
        # holds the started flag, the completed flag, AND the fail
        # counter) so the wizard re-runs on next launch. Any legacy
        # ``.onboarding_complete`` / ``.onboarding_started`` markers
        # still on disk are removed too. Deleting the whole document
        # (rather than clearing one flag) keeps the flags consistent:
        # if a stale ``started`` flag survived, the XA-11-2 auto-heal
        # would treat the next launch as a mid-wizard crash and SKIP
        # re-running the wizard — defeating the whole point of a
        # "re-run setup" affordance.
        if not onboarding_status.reset_status(config_dir):
            raise OSError("could not delete the onboarding status document")
        log.info(
            "[ONBOARDING] Reset onboarding status: %s",
            onboarding_status.status_path(config_dir),
        )
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
