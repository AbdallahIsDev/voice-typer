"""Background startup tasks after app construction (non-blocking)."""

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
_APP_SERVICES_LIB: Any | None = None
_APP_SERVICES_LIB_LOADED: bool = False


def _a11y_regrant_message(bundle_id: str | None) -> str:
    """Build the macOS Accessibility re-grant notification body."""
    # The command string comes from the single construction
    from voice_typer.server.server_platform.macos_bundle_id import tccutil_reset_command_str

    if bundle_id:
        return (
            f"{APP_NAME} was updated. Accessibility permission may "
            f"need to be re-granted. Run: {tccutil_reset_command_str('Accessibility', bundle_id)}"
        )
    return (
        f"{APP_NAME} was updated. Accessibility permission may "
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
        bool function raises (defensive, the production ``enable_autostart``
        catches exceptions internally and returns False, so ``error`` will
        typically be ``None`` even on failure). A future refactor that
        routes through ``enable_autostart_ex`` directly will populate
        ``error`` with the real failure reason.
    """
    # Import the autostart facade module at call time so tests that
    from voice_typer.server.server_platform import autostart as _autostart

    # One-time per-install cleanup of legacy autostart entries
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
    result: dict = {"registered": False, "error": None, "actual_post_sync": False}
    try:
        actual = _autostart.is_autostart_enabled()
        if app.config.autostart and not actual:
            log.info("[CONFIG] Config says autostart=true but it is disabled -- enabling")
            registered = _autostart.enable_autostart()
            # capture the post-enable state. enable_autostart()
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
            # Already in sync, report the current state.
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
        # (a): on failure we don't know the post-sync OS state, leave
        result = {"registered": False, "error": str(e), "actual_post_sync": False}
    return result


def sync_prewarm_task(app: AppProtocol, shutdown_event: threading.Event | None = None) -> dict:
    """No-op stub retained for caller compatibility."""
    _ = app  # unused, kept for signature backward-compat
    _ = shutdown_event  # unused, kept for signature backward-compat
    return {"registered": False, "error": None}


def check_offline_pack_on_launch(app: AppProtocol, shutdown_event: threading.Event | None = None) -> dict:
    """Launch-time pack check: integrity + always-on remote update check.

    Runs on a fire-and-forget daemon thread at startup (see
    ``StartupSequence._startup_parallel_work``). Never blocks the window:

    1. Cheap local existence scan (no SHA-256).
    2. Pack present → background checksum AND remote update check
       (``check_offline_pack_update`` with ``trigger_download=True``).
    3. Pack missing → ``offline_pack_missing`` event + remote update
       check (silent re-download). Always-on: no consent gate.

    Remote fetch uses ``LAUNCH_MANIFEST_TIMEOUT_S`` so a stalled logon
    network cannot pin the thread. Best-effort: never raises.
    """
    try:
        from voice_typer.server import event_bus as _event_bus_module
        from voice_typer.server.service import offline_pack, update_check

        config = getattr(app, "config", None)
        event_bus = _event_bus_module

        local_version: str | None = None
        try:
            local_version = update_check._local_offline_pack_version()
        except Exception:  # noqa: BLE001
            log.debug("[PACK] launch-time local pack scan failed", exc_info=True)

        if shutdown_event is not None and shutdown_event.is_set():
            return {"checked": False, "reason": "shutdown"}

        checksum: str | None = None
        missing_event = False
        if local_version is not None:
            try:
                background = offline_pack.BackgroundChecksum(local_version, event_bus=event_bus)
                background.start()
                checksum = "background"
            except Exception:  # noqa: BLE001
                log.exception("[PACK] background checksum spawn failed for %s", local_version)
            log.info(
                "[PACK] offline pack %s present at launch, background checksum started",
                local_version,
            )
        else:
            missing_event = True
            try:
                offline_pack._publish_event(
                    event_bus,
                    "offline_pack_missing",
                    {
                        "version": None,
                        "path": str(offline_pack._default_offline_pack_root()),
                    },
                )
            except Exception:  # noqa: BLE001
                log.debug("[PACK] offline_pack_missing publish failed", exc_info=True)
            log.info("[PACK] offline pack missing at launch, always-on re-download check")

        if shutdown_event is not None and shutdown_event.is_set():
            return {
                "checked": False,
                "reason": "shutdown",
                "installed_version": local_version,
            }

        # Always-on: remote check on every launch (present or missing).
        update_result: dict | None = None
        try:
            result = update_check.check_offline_pack_update(
                config,
                event_bus,
                trigger_download=True,
                manifest_timeout=update_check.LAUNCH_MANIFEST_TIMEOUT_S,
            )
            update_result = dict(result)
        except Exception:  # noqa: BLE001
            log.exception("[PACK] launch-time pack update check failed (best-effort)")

        return {
            "checked": True,
            "installed_version": local_version,
            "checksum": checksum,
            "missing_event": missing_event,
            "update_check": update_result,
        }
    except Exception:  # noqa: BLE001
        log.exception("[PACK] launch-time pack check failed (best-effort)")
        return {"checked": False, "reason": "error"}


def check_media_extractor_refresh(app: AppProtocol, shutdown_event: threading.Event | None = None) -> dict:
    """Launch-time CHECK-ONLY freshness probe for the media extractor (ADR-0023).

    Compares the installed yt-dlp / solver versions against the published
    ``media-extractor.json`` manifest and persists the result. It NEVER
    downloads or installs anything: the offline pack stays the heavy
    software boundary and the user-initiated update path owns installs.
    Runs fire-and-forget on a daemon thread; best-effort, never raises.
    """
    _ = app  # state is advisory metadata, nothing on the app is mutated
    try:
        if shutdown_event is not None and shutdown_event.is_set():
            return {"checked": False, "reason": "shutdown"}
        from voice_typer.server.media_ingest import mini_update as _mini_update

        state = _mini_update.check_refresh()
        if state.update_available:
            log.info(
                "[MEDIA] extractor refresh available: yt-dlp %s -> %s, solver %s -> %s (check-only; user-installed)",
                state.backend_version,
                state.remote_backend_version,
                state.solver_version,
                state.remote_solver_version,
            )
        else:
            log.info(
                "[MEDIA] extractor freshness check complete (yt-dlp=%s solver=%s, no update)",
                state.backend_version,
                state.solver_version,
            )
        return {
            "checked": True,
            "update_available": state.update_available,
            "checked_at": state.checked_at,
        }
    except Exception:  # noqa: BLE001
        log.debug("[MEDIA] launch-time extractor refresh check failed (best-effort)", exc_info=True)
        return {"checked": False, "reason": "error"}


def ensure_desktop_shortcut(app: AppProtocol) -> None:
    """Create the Desktop + Start Menu shortcuts on first run."""
    if not is_windows():
        return
    desktop = Path.home() / "Desktop"
    legacy_bat = desktop / "Lausu.bat"

    # 1. Migrate: remove the legacy backend-only .bat so the broken
    try:
        if legacy_bat.exists() and "-m voice_typer" in legacy_bat.read_text(encoding="utf-8", errors="replace"):
            legacy_bat.unlink()
            log.info("[STARTUP] Removed legacy backend-only shortcut: %s", legacy_bat)
    except OSError:
        pass

    # 2. Ensure the universal-launcher shortcut exists.
    try:
        create_launcher_shortcut()
    except Exception as e:
        log.debug("[STARTUP] Desktop shortcut creation skipped: %s", e)


def _reconcile_configured_microphone(app: AppProtocol, mics: list[dict]) -> None:
    """Validate ``app.config.microphone`` against the live device list."""
    from voice_typer.server.server_platform.microphone_list import find_microphone_by_id

    try:
        mic_id = app.config.microphone
    except AttributeError:
        return
    # Only str/None are meaningful persisted values. Anything else is an
    if mic_id is not None and not isinstance(mic_id, str):
        return
    if mic_id is None:
        log.debug("[MIC] Startup microphone check: System Default (no persisted selection)")
        return
    if not mics:
        # An EMPTY enumeration is NOT evidence that the configured device
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
    lock = getattr(app, "_config_mutation_lock", None)
    with contextlib.ExitStack() as stack:
        if lock is not None:
            stack.enter_context(lock)
        app.config.microphone = None
        saved = app.config.save()
    if saved:
        log.warning(
            "[MIC] Configured microphone %r is not available on this machine "
            "(%d input device(s) found), recovered to System Default and "
            "persisted null.",
            mic_id,
            len(mics),
        )
        _publish_mic_reconciled(app, {"microphone": None})
    else:
        log.error(
            "[MIC] Configured microphone %r is unavailable and persisting the "
            "System Default fallback FAILED, stale id left on disk; will "
            "retry at next startup.",
            mic_id,
        )


def reconcile_configured_model(app: AppProtocol) -> bool:
    """Clear ``config.model_size`` when the configured ASR model isn't on disk."""
    from voice_typer.server.model_registry import NO_MODEL_SIZE
    from voice_typer.server.tray_models import is_active_model_downloaded

    config = app.config
    # "No model selected" already, nothing to do.
    if getattr(config, "model_size", None) == NO_MODEL_SIZE:
        return False
    backend = getattr(config, "asr_backend", "whisper") or "whisper"
    # Cloud backends have no local model to install, don't touch.
    if backend in ("openai", "groq", "deepgram", "custom"):
        return False
    # Model IS on disk, nothing to do.
    if is_active_model_downloaded(config):
        return False
    # Configured model is definitively absent, clear it.
    config.model_size = NO_MODEL_SIZE
    try:
        ok = config.save()
    except Exception as e:
        log.warning("[MODEL] failed to persist reconciled model_size: %s", e)
        return False
    if not ok:
        log.warning("[MODEL] failed to persist reconciled model_size (save returned False)")
        return False
    log.info(
        "[MODEL] configured %s model is not installed, cleared model_size to 'no model selected' (NO_MODEL_SIZE)",
        backend,
    )
    return True


def _publish_mic_reconciled(app: AppProtocol, updates: dict) -> None:
    """Push a ``config_changed`` event after startup reconciliation."""
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
    the predecessor renderer can refresh its microphone dropdown without
    a manual "Refresh" click. The comparison is done via ``old_ids``
    and ``new_ids`` sets.
    """
    # Import list_microphones at call time so tests that monkeypatch
    from voice_typer.server.server_platform.microphone_list import list_microphones

    # Accessors for the app's off-protocol ``_microphones`` attribute
    from voice_typer.server.service._app_internals import app_microphones, set_app_microphones

    # RACE-020: abort early if shutting down
    if shutdown_event is not None and shutdown_event.is_set():
        return
    try:
        mics = list_microphones()
        # Startup reconciliation: validate the PERSISTED selection against
        try:
            _reconcile_configured_microphone(app, mics)
        except Exception:
            # Belt-and-braces: a reconciler bug must never downgrade the
            log.warning("[MIC] Microphone reconciliation failed", exc_info=True)
        # AUDIO-MIC: detect device changes by comparing the new
        app_mics = app_microphones(app)
        old_ids = {m["id"] for m in app_mics} if app_mics else set()
        new_ids = {m["id"] for m in mics}
        set_app_microphones(app, mics)
        app.tray.set_microphones(mics)
        # Log INFO on first load or when device count changes.
        if not old_ids:
            log.info("[RECORDING] Found %d microphones", len(mics))
        elif len(mics) != len(old_ids):
            log.info("[RECORDING] Microphone count changed: %d -> %d", len(old_ids), len(mics))
        # AUDIO-MIC: push a device-change IPC event if the device
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
                # Best-effort: a failed notification publish must never break
                log.debug("[AUDIO-MIC] microphones_changed publish failed", exc_info=True)
    except Exception as e:
        log.warning("[RECORDING] Could not enumerate microphones: %s", e)


def start_accessibility_pulse(app: AppProtocol, initial_state: bool) -> None:
    """Periodically re-check macOS Accessibility permission."""

    def _check_accessibility() -> bool:
        """Return True if Accessibility permission is granted."""
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
        # PERF-25: the loop now also watches ``stop_event``
        # 60-iteration 1s loop (PERF-25) was added so shutdown signals
        # the  finding). The defensive ``app._shutting_down`` check
        last_state = initial_state
        while not app._shutting_down:
            # single 60s wait, ``stop_event.set()`` from
            if stop_event.wait(timeout=60.0):
                return
            if app._shutting_down or stop_event.is_set():
                return
            current = _check_accessibility()
            if current != last_state:
                if current:
                    log.info("[A11Y] macOS Accessibility permission granted")
                    # persist the app version at which a11y was
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
                                "[A11Y] a11y denied after app version change (%s -> %s), likely TCC reset on update",
                                _last_known_version,
                                _current_vt_version,
                            )
                    except Exception:
                        log.debug("[A11Y] could not compare a11y version", exc_info=True)
                    with contextlib.suppress(Exception):
                        if _version_changed:
                            # Resolve the HOST app's bundle ID at runtime:
                            app.tray.notify_safety(
                                f"{APP_NAME}, Accessibility Re-grant",
                                _a11y_regrant_message(resolve_host_bundle_id()),
                            )
                        else:
                            app.tray.notify_safety(
                                f"{APP_NAME}, Accessibility Revoked",
                                "Global hotkeys have been disabled. Open System Settings "
                                "-> Privacy & Security -> Accessibility to re-grant.",
                            )
                last_state = current

    # PERF-25: dedicated stop_event so ``app._thread_registry`` can
    stop_event = threading.Event()
    t = threading.Thread(target=_pulse_loop, args=(stop_event,), daemon=True, name="A11yPulse")
    # RACE-008: daemon=True is acceptable, the pulse only reads
    t.start()
    # PERF-25: register with the central ThreadRegistry so
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
    :meth:`OnboardingController.reset` method deletes both, the IPC
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
        Optional :class:`voice_typer.server.app.LausuApp` instance.
        When provided, the ``onboarding_completed`` flag is mutated on
        the live ``app.config`` object and persisted via
        ``app.config.save_strict()``: which acquires the config-mutation
        lock so the write cannot race a concurrent
        ``set_config`` IPC handler. When ``None`` (e.g. tests), falls
        back to a fresh ``Config.load()`` snapshot + ``cfg.save()``;
        this bypasses the lock and is acceptable for the test-only path
        but callers should pass ``app`` whenever one is in scope.

    Returns
    -------
    dict
        ``{"reset": bool, "error": str | None}`` where ``reset`` is
        True if the marker was deleted (or already absent, idempotent).
        The renderer surfaces ``error`` if the deletion failed (e.g.
        permission denied on the marker file).
    """
    try:
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        # Delete the merged ``.onboarding_status.json`` document (which
        if not onboarding_status.reset_status(config_dir):
            raise OSError("could not delete the onboarding status document")
        log.info(
            "[ONBOARDING] Reset onboarding status: %s",
            onboarding_status.status_path(config_dir),
        )
        # Also clear the ``onboarding_completed`` flag in config.json so
        if app is not None:
            try:
                cfg = getattr(app, "config", None)
                if cfg is not None and getattr(cfg, "onboarding_completed", False):
                    # Acquire the app's config-mutation lock around the
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
