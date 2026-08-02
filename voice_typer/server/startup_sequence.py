"""Startup sequence orchestration for VoiceTyperApp.

Phase 5: extracted from ``VoiceTyperApp._do_startup`` (~340 lines)
to reduce the god-class size. Each phase is gated by
``app._shutting_down`` so a ``quit()`` during startup short-circuits
cleanly.

Phase ordering is FIXED — see the dependency graph in worklog.md.
Reordering risks:

(a) Hotkey registration before model load means F2 works even if the
    model fails to load — without this, a model-load failure leaves the
    user with no way to interact with the app.
(b) Mic enumeration before hotkey registration means the tray menu has
    mics available when the hotkey is bound (the menu is built lazily
    on first show, but the mic list is captured at startup).
(c) Onboarding auto-heal must run before any ``config.save()`` to avoid
    clobbering user settings — the wizard's ``apply_settings()`` overwrites
    the user's hotkey, model, and microphone selections with onboarding
    defaults (``<caps_lock>``, ``small.en``, ``None``).

The class does NOT import ``app.py`` at module load (would create an
import cycle: ``app`` imports ``startup_sequence`` indirectly via the
``StartupSequence(self)`` call inside ``_do_startup``).  The runtime
import is local to ``_do_startup`` itself, so this module never
appears in ``app.py``'s import-time graph.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server import crash_handler as _crash_handler
from voice_typer.server.branding import APP_NAME
from voice_typer.server.config import _config_dir
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows
from voice_typer.server.server_platform import is_autostart_enabled
from voice_typer.server.text_cleanup import configure_corrections

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle described in the module
    # docstring.  At runtime, ``app`` is whatever object was passed to
    # ``__init__`` (always a ``VoiceTyperApp`` in production, but tests
    # pass mocks that satisfy the same duck-typed surface).
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)

# name of the onboarding-fail-counter persistence file
# (lives in the config dir alongside config.json). The file holds a
# tiny JSON document: ``{"count": <int>, "last_fail_ts": <epoch-float>}``.
# The counter survives process restarts so the "after 3 failures"
# circuit breaker (see ``_do_onboarding_check``) actually trips even
# when each failure occurs in a different process session. Pre-fix
# the counter lived only on ``app._onboarding_fail_count`` (an
# in-memory attribute), so a user whose onboarding kept failing once
# per app-start would NEVER hit the circuit breaker and would be
# stuck on the onboarding wizard forever.
_ONBOARDING_FAIL_COUNTER_FILENAME = ".onboarding_fail_count"
# Stale-counter cutoff: if the last failure is older than this window
# (in seconds), the counter resets to 1 on the next failure. Prevents
# a user who hit 2 failures a year ago from being marked
# onboarding_failed the next time they restart with a transient
# failure. 7 days matches the onboarding wizard's "won't bother the
# user again" cadence.
_ONBOARDING_FAIL_COUNTER_TTL_SECONDS: float = 7 * 24 * 60 * 60.0

# Stale backup file retention: files older than this are swept at startup.
# 30 days matches the log-rotation sweep and crash-diagnostics sweep cadence.
_BACKUP_RETENTION_MAX_AGE_SECONDS: float = 30 * 24 * 60 * 60.0

# Glob patterns for corrupt-quarantine and pre-migration backup files that
# accumulate indefinitely without an automatic sweep. The GDPR purge function
# (service/privacy.py) only cleans these on explicit user action; this sweep
# runs at every startup to bound disk usage. Files newer than the retention
# period are preserved for forensic value.
_BACKUP_FILE_GLOBS: tuple[str, ...] = (
    "history.db.pre-migration-v*.bak",
    "history.db.corrupt-*",
    "config.json.corrupt-*",
    "config.json.pre-migration-v*.bak",
    "config.json.v*.bak",
    "config.json.bak.failed-migration-*",
    "voice-typer-recovery.json.corrupt.*",
)


def _sweep_stale_backup_files(config_dir: Path) -> None:
    """Delete stale corrupt-quarantine and pre-migration backup files.

    Mirrors the pattern of ``_sweep_stale_log_rotations`` (log/__init__.py)
    and ``_sweep_stale_diagnostics`` (crash_handler/_diagnostics_archive.py).
    Files newer than ``_BACKUP_RETENTION_MAX_AGE_SECONDS`` are preserved for
    forensic value. Per-file errors are swallowed so one bad file never
    aborts the sweep.
    """
    if config_dir is None:
        return
    config_path = Path(config_dir)
    if not config_path.is_dir():
        return
    now = time.time()
    for pattern in _BACKUP_FILE_GLOBS:
        try:
            for file_path in config_path.glob(pattern):
                try:
                    if not file_path.is_file():
                        continue
                    age = now - file_path.stat().st_mtime
                    if age > _BACKUP_RETENTION_MAX_AGE_SECONDS:
                        file_path.unlink()
                        log.info(
                            "[STARTUP] swept stale backup file (age=%.0f days): %s",
                            age / 86400.0,
                            file_path.name,
                        )
                except OSError as exc:
                    log.debug(
                        "[STARTUP] could not sweep backup file %s: %s",
                        file_path.name,
                        exc,
                    )
        except OSError as exc:
            log.debug("[STARTUP] glob error for pattern %s: %s", pattern, exc)



def _onboarding_fail_counter_path() -> Path:
    """Return the absolute path to the onboarding fail-counter file."""
    return _config_dir() / _ONBOARDING_FAIL_COUNTER_FILENAME


def _read_onboarding_fail_count() -> tuple[int, float]:
    """Read the persisted onboarding fail counter.

    Returns ``(count, last_fail_ts)``. On any read failure (missing
    file, corrupt JSON, schema drift), returns ``(0, 0.0)`` — the
    safe default that lets the next failure start the counter fresh.
    """
    path = _onboarding_fail_counter_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, 0.0
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0, 0.0
    if not isinstance(data, dict):
        return 0, 0.0
    count = data.get("count", 0)
    last_fail_ts = data.get("last_fail_ts", 0.0)
    if not isinstance(count, int) or count < 0:
        return 0, 0.0
    if not isinstance(last_fail_ts, (int, float)):
        last_fail_ts = 0.0
    return count, float(last_fail_ts)


def _write_onboarding_fail_count(count: int, last_fail_ts: float) -> None:
    """Persist the onboarding fail counter to disk.

    Failures are best-effort — a write error is logged at DEBUG and
    swallowed (the in-memory counter on ``app._onboarding_fail_count``
    is still incremented, so the circuit breaker can still trip
    in-session even if persistence is broken).
    """
    path = _onboarding_fail_counter_path()
    payload = json.dumps({"count": count, "last_fail_ts": last_fail_ts})
    try:
        # Atomic write (temp + os.replace) so a crash mid-write cannot
        # leave a half-truncated JSON document that the load helper
        # would treat as count=0 on next startup — defeating the
        # onboarding-fail circuit breaker. durability=False matches
        # the existing autostart/prewarm pattern.
        from voice_typer.server.secure_file_io import _secure_atomic_write

        _secure_atomic_write(path, payload, durability=False)
    except OSError as exc:
        log.debug(
            "[STARTUP] Could not persist onboarding fail counter to %s: %s",
            path,
            exc,
        )


def _reset_onboarding_fail_count() -> None:
    """Clear the persisted onboarding fail counter.

    Called on successful onboarding completion so a future transient
    failure doesn't accumulate against the stale count. Best-effort:
    a missing file is a no-op, a write error is logged at DEBUG.
    """
    path = _onboarding_fail_counter_path()
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        log.debug(
            "[STARTUP] Could not reset onboarding fail counter at %s: %s",
            path,
            exc,
        )


class StartupSequence:
    """Orchestrates the multi-phase background startup of VoiceTyperApp.

    The previous monolithic ``VoiceTyperApp._do_startup`` (~340 lines)
    is now ``StartupSequence(app).run()``.  ``app`` is a back-reference
    so the sequence can read/write the app's state (config, tray, models,
    hotkeys, etc.) — same attribute surface as before, just renamed
    from ``self.X`` to ``self._app.X``.
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app

    def run(self) -> None:
        """Top-level entry — equivalent to the old ``_do_startup`` body.

        RACE-020: checks ``self._app._shutting_down`` between each major
        step so that a ``quit()`` call during startup doesn't proceed
        with model downloads or background loads after the app has
        begun shutdown.
        """
        app = self._app
        log.info("[STARTUP] Initializing: autostart, microphones, hotkey, model...")

        # ── CRASH DIAGNOSTICS: check for leftover crash reports ──────
        # The VEH handler (crash_handler.py) writes crash_diagnostics.<PID>.txt
        # when a previous process was killed by STATUS_HEAP_CORRUPTION or
        # another silent SEH exception.  We read them here, log them to
        # voice-typer.log, show a notification, and delete them.
        try:
            # Sweep stale corrupt-quarantine and pre-migration backup files
            # (30-day retention). Mirrors the log-rotation and crash-diagnostics
            # sweeps. Best-effort — never aborts startup on a sweep error.
            with contextlib.suppress(Exception):
                _sweep_stale_backup_files(_config_dir())
            crash_summary = _crash_handler.report_pending_crash(_config_dir())
            if crash_summary:
                # Log at WARNING so it appears prominently in voice-typer.log
                log.warning("[STARTUP] Previous session crashed! See log lines above for full diagnostics.")
                # critical — bypass notification toggle so the
                # user always sees crash alerts.
                try:
                    app.tray.notify_safety(
                        f"{APP_NAME} — Previous Session Crashed",
                        "The app was restarted automatically after "
                        "an unexpected shutdown.\n\n"
                        f"{crash_summary}\n\n"
                        "To prevent this: free up RAM/disk space, "
                        "or try a smaller model in Settings. "
                        "See voice-typer.log for full diagnostics.",
                    )
                except Exception as exc:
                    log.debug("[STARTUP] Could not show crash notification: %s", exc)
                # Also publish an event to the in-process event bus so
                # the Electron frontend can show an in-app notification
                # (toast / snackbar) if the UI window is open.
                # event name was renamed from "electron_notification"
                # to the platform-agnostic "notification" — the Tauri
                # Rust host passes the event through unchanged (the old
                # rename match arm was removed). A Rust-side backward-
                # compat alias handles old Python sidecars still emitting
                # the legacy name during rolling upgrades.
                try:
                    from voice_typer.server import event_bus

                    event_bus.publish(
                        {
                            "type": "notification",
                            "data": {
                                "title": f"{APP_NAME} — Previous Session Crashed",
                                "message": crash_summary,
                                "duration_ms": 15000,
                                "critical": True,
                            },
                        }
                    )
                except Exception as exc:
                    log.debug("[STARTUP] Could not publish crash event to frontend: %s", exc)
        except Exception as exc:
            log.debug("[STARTUP] Crash diagnostic check failed: %s", exc)

        if app._shutting_down:
            log.info("[STARTUP] _shutting_down is set, aborting startup")
            return

        # #8: Onboarding wizard — detect first run and let the React UI
        # show the wizard. Previously this auto-applied defaults and
        # marked onboarding complete, which prevented the wizard from
        # ever appearing (the 275-line Onboarding.tsx was dead code).
        # Now we just save the config with onboarding_completed=False
        # so the frontend can detect first-run via the
        # `onboarding_is_first_run` IPC route and route the user to
        # the wizard. The wizard's apply/skip handler flips the flag
        # to True and marks the .onboarding_complete marker.
        #
        # ONBOARDING-STALE-FIX: When config.json already exists on disk
        # (the user has been using the app) but onboarding_completed is
        # False and the .onboarding_complete marker is missing, we
        # auto-heal the state. Without this fix, is_first_run() returns
        # True on every restart, the frontend routes to the onboarding
        # wizard, and the wizard's apply_settings() overwrites the
        # user's custom hotkey, model, and microphone settings with
        # onboarding defaults (<caps_lock>, small.en, None).
        if not app.config.onboarding_completed:
            try:
                from voice_typer.server.onboarding import OnboardingController

                onboarding = OnboardingController()
                if onboarding.is_first_run():
                    # Check if config.json already exists on disk.
                    # If it does, this is NOT a genuine first install
                    # but a stale onboarding state where the marker was
                    # lost/deleted and onboarding_completed was never
                    # flipped to True. Auto-heal by marking onboarding
                    # complete to prevent the wizard from showing on
                    # every restart and overwriting the user's settings.
                    config_file = _config_dir() / "config.json"
                    started_marker = _config_dir() / ".onboarding_started"
                    if config_file.exists() and not started_marker.exists():
                        log.info(
                            "[STARTUP] Config file exists but onboarding "
                            "flag is False and marker is missing -- "
                            "fixing stale onboarding state to prevent "
                            "wizard from overwriting user settings"
                        )
                        app.config.onboarding_completed = True
                        onboarding.mark_complete()
                        app.config.save()
                        # clear the persisted fail counter
                        # — the auto-heal path means onboarding is now
                        # complete (no longer failing), so a future
                        # transient failure should start fresh instead
                        # of accumulating against the stale count.
                        _reset_onboarding_fail_count()
                    else:
                        # Genuine first run -- no config.json exists yet.
                        # Save the default config so the frontend can
                        # detect first-run and show the wizard.
                        log.info(
                            "[STARTUP] First run detected -- deferring to React "
                            "onboarding wizard (config.onboarding_completed=False)"
                        )
                        app.config.save()
            except Exception as e:
                # previously this was log.debug, which is
                # invisible at default log levels. If onboarding check
                # persistently fails the user is stuck on first-run
                # forever with no indication of why. Promote to
                # log.exception and notify the tray; after N consecutive
                # failures we mark onboarding completed with a failure
                # flag so the app remains usable.
                log.exception("[STARTUP] Onboarding check failed: %s", e)
                try:
                    # persist the fail counter to disk so
                    # the "after 3 failures" circuit breaker actually
                    # trips across process restarts. Pre-fix the
                    # counter lived only on ``app._onboarding_fail_count``
                    # (in-memory), so a user whose onboarding failed
                    # once per app-start would never hit the breaker
                    # and be stuck on the onboarding wizard forever.
                    # Read the persisted counter, apply a TTL so a
                    # stale counter from months ago doesn't trip on
                    # the next transient failure, increment, and
                    # persist back. The in-memory attribute is also
                    # updated so any in-session retry logic that reads
                    # ``app._onboarding_fail_count`` sees the same
                    # value as the persisted file.
                    persisted_count, last_fail_ts = _read_onboarding_fail_count()
                    now = time.time()
                    if (
                        persisted_count > 0
                        and last_fail_ts > 0
                        and (now - last_fail_ts) > _ONBOARDING_FAIL_COUNTER_TTL_SECONDS
                    ):
                        # Stale counter — start fresh. Log at INFO so
                        # an operator can correlate the reset with the
                        # subsequent failure log.
                        log.info(
                            "[STARTUP] Onboarding fail counter reset (last failure %.1f days ago > TTL %.1f days)",
                            (now - last_fail_ts) / 86400.0,
                            _ONBOARDING_FAIL_COUNTER_TTL_SECONDS / 86400.0,
                        )
                        persisted_count = 0
                    new_count = persisted_count + 1
                    app._onboarding_fail_count = new_count
                    _write_onboarding_fail_count(new_count, now)
                    if new_count >= 3:
                        app.config.onboarding_completed = True
                        app.config.onboarding_failed = True
                        try:
                            app.config.save()
                        except Exception:
                            log.exception("[STARTUP] Could not save onboarding_failed flag")
                        # reset the persisted counter once
                        # the circuit breaker trips so a future
                        # onboarding reset (user clears
                        # onboarding_completed in settings) starts
                        # fresh instead of immediately re-tripping.
                        _reset_onboarding_fail_count()
                        # critical — bypass show_notifications toggle.
                        with contextlib.suppress(Exception):
                            app.tray.notify_safety(
                                APP_NAME,
                                "Onboarding setup kept failing. The app will "
                                "start with default settings. Open Settings to "
                                "configure manually.",
                            )
                    elif app.config.show_notifications:
                        with contextlib.suppress(Exception):
                            app.tray.notify(
                                APP_NAME,
                                "Onboarding setup failed; will retry on next start.",
                            )
                except Exception:
                    log.exception("[STARTUP] Onboarding failure-handler itself failed")

        # Load external text corrections (if available) before any transcription
        # surface load errors to the user via tray notification
        # so they know why their corrections aren't taking effect.
        try:
            err = configure_corrections(config_dir=app.config.config_dir)
            if err is not None:
                # critical — bypass toggle (broken corrections file).
                try:
                    app.tray.notify_safety(
                        f"{APP_NAME} — Corrections Error",
                        f"{err}\nCorrections will use built-in defaults. Fix the file and restart.",
                    )
                except Exception:
                    log.debug("[STARTUP] Could not show corrections error notification")
        except Exception:
            log.debug("[STARTUP] External corrections load failed, using built-in defaults")

        # P2: Crash recovery -- check for unpasted transcriptions
        if app.config.crash_recovery_enabled:
            try:
                unpasted = app._crash_recovery.check_on_startup()
                if unpasted:
                    log.info("[STARTUP] Found %d unpasted transcriptions from previous session", len(unpasted))
                    # critical — bypass toggle (recovered user data).
                    app.tray.notify_safety(
                        APP_NAME,
                        f"Recovered {len(unpasted)} transcription(s) from last session. Open History to view.",
                    )
            except Exception:
                # M-67: promote debug→warning so the failure surfaces in
                # the default log; include the traceback so operators can
                # diagnose why crash-recovery check failed.
                log.warning("[STARTUP] Crash recovery check failed", exc_info=True)

        # apply history retention policy at startup.
        # Previously the config keys were saved but never read.
        # PERF-14: spawn a daemon thread so the SQLite DELETEs (which
        # can take 100ms+ on a large history DB with index rebuilds)
        # don't block the startup critical path to hotkey registration
        # + model load. Retention is best-effort housekeeping — a
        # 100ms delay before stale entries are pruned is invisible to
        # the user, but a 100ms delay before F2 works is not. The
        # thread is a daemon so it never blocks process exit, and the
        # inner try/except ensures any DB error is swallowed (the
        # next startup will retry).
        # PERF-25: register with ``app._thread_registry`` so
        # ``shutdown_all()`` can signal + join this thread instead of
        # orphaning it (the retention sweep can take 10s+ on a huge
        # DB, and we want clean shutdown to wait briefly for it).
        import threading as _threading

        retention_stop_event = _threading.Event()

        def _apply_retention_bg(stop_event: _threading.Event) -> None:
            try:
                app.history_db.apply_retention(
                    retention_days=app.config.history_retention_days,
                    max_entries=app.config.history_max_entries,
                    retention_count=app.config.history_retention_count,
                )
            except Exception:
                # M-67: promote debug→warning so the failure surfaces in
                # the default log; include the traceback so operators can
                # diagnose why history-retention apply failed.
                log.warning("[STARTUP] History retention apply failed", exc_info=True)
            finally:
                # Clear the stop_event so the registry's join sees a
                # finished thread (defensive — the thread exits on its
                # own, but this makes the contract explicit).
                with contextlib.suppress(Exception):
                    # L-6 (IMPROVE-2026-07-19): removed dead
                    # `# type: ignore[unused-ignore]` meta-suppression —
                    # there was no other `# type: ignore` on this line
                    # to suppress the "unused ignore" warning for. The
                    # `stop_event.set()` call has no type issues.
                    stop_event.set()

        retention_thread = _threading.Thread(
            target=_apply_retention_bg,
            args=(retention_stop_event,),
            name="history-retention-apply",
            daemon=True,
        )
        retention_thread.start()
        # PERF-25: register with the central ThreadRegistry.
        registry = getattr(app, "_thread_registry", None)
        if registry is not None:
            try:
                registry.register(
                    name="history-retention-apply",
                    thread=retention_thread,
                    stop_event=retention_stop_event,
                    join_timeout=2.0,
                )
            except Exception:
                log.debug(
                    "[STARTUP] could not register history-retention-apply with ThreadRegistry",
                    exc_info=True,
                )

        # schedule PERIODIC retention sweeps so the DB doesn't
        # grow monotonically during long sessions. The one-shot
        # ``_apply_retention_bg`` above only prunes at startup; an 8-hour
        # dictation session at ~1 transcription/minute accumulates ~480
        # new rows above the configured ``history_max_entries`` ceiling
        # and the DB file never shrinks during the session (VACUUM only
        # runs inside ``apply_retention``). The periodic sweep calls
        # ``apply_retention`` every 10 minutes (default) on a daemon
        # thread registered with ThreadRegistry. ``apply_retention`` is
        # already chunked + safe to call at runtime (it acquires the
        # writer-thread lock per chunk, so concurrent ``add_transcription``
        # calls are not blocked for the full sweep). The sweep re-reads
        # ``app.config.history_*`` on each tick so a mid-session config
        # change (e.g. the user lowers ``history_max_entries`` from 1000
        # to 500) takes effect on the next sweep without requiring an
        # app restart. Best-effort — failures are logged + swallowed.
        try:
            app.history_db.schedule_periodic_retention(
                interval_s=600.0,
                app=app,
                retention_days=app.config.history_retention_days,
                max_entries=app.config.history_max_entries,
                retention_count=app.config.history_retention_count,
            )
        except Exception:
            log.warning(
                "[STARTUP] could not schedule periodic history retention — DB will grow until next app launch",
                exc_info=True,
            )

        # PLAT-WAYLAND: Warn if running on Wayland and
        # suggest wtype/ydotool as fallback for global hotkeys.
        if is_linux() and os.environ.get("XDG_SESSION_TYPE") == "wayland" and not app.config.wayland_warned:
            log.warning("[STARTUP] Wayland detected -- global hotkeys may not work")
            # check if wtype or ydotool is available as a fallback
            import shutil

            wtype_available = shutil.which("wtype") is not None
            ydotool_available = shutil.which("ydotool") is not None
            if not wtype_available and not ydotool_available:
                log.warning(
                    "[STARTUP] Neither wtype nor ydotool found. "
                    "Install one for hotkey support on Wayland: "
                    "'sudo apt install wtype' or 'sudo apt install ydotool'"
                )
                # critical — bypass toggle (hotkeys broken).
                app.tray.notify_safety(
                    f"{APP_NAME} — Wayland Hotkeys",
                    "Global hotkeys may not work on Wayland. "
                    "Install 'wtype' or 'ydotool' for hotkey support, "
                    "or use the tray menu's Toggle Dictation option.",
                )
            else:
                log.info(
                    "[STARTUP] Wayland hotkey fallback available: %s",
                    "wtype" if wtype_available else "ydotool",
                )
            app.config.wayland_warned = True
            app.config.save()

        # macOS accessibility permission check.
        # On macOS, global hotkeys require Accessibility permission.
        # The app can't request it directly, but we can detect it's
        # missing and notify the user.
        _has_accessibility = False
        if is_macos():
            try:
                # Use AXIsProcessTrusted() via ctypes for the
                # definitive check.  AXIsProcessTrusted() is the official
                # API — it returns True iff the process has Accessibility
                # permission.  We load it from ApplicationServices.framework
                # via ctypes (no PyObjC dependency required).
                #
                # the prior fallback to `osascript -e 'tell
                # application "System Events" to keystroke " "'` was
                # removed because:
                #   1. It runs on the critical startup thread (before
                #      hotkey registration at line 528 and before the
                #      parallel prewarm/mic work at line 516) and blocks
                #      for up to 3s on the subprocess + osascript
                #      interpreter startup.
                #   2. It synthesizes a REAL keystroke via System Events
                #      (a space character), which is invasive — it
                #      focuses the frontmost app and types into whatever
                #      has keyboard focus. A user running Voice Typer
                #      at login could see the space land in their
                #      password prompt or terminal.
                #   3. The osascript path is reached whenever ctypes
                #      ApplicationServices load fails (stripped-down
                #      macOS installs, code-signed bundles with
                #      restricted dyld env, CI runners), so it's a
                #      real production path — not just a dev fallback.
                #
                # Replacement strategy: if the ctypes probe fails (load
                # error / symbol missing), treat the result as
                # "permission not granted" (False) and let the periodic
                # A11yPulse (started at line 406 below) detect the grant
                # within 60s. A11yPulse uses the SAME ctypes probe but
                # runs it off the startup hot path, so a transient
                # load failure on startup doesn't wedge the user — the
                # next A11yPulse tick re-tries and updates the tray.
                #
                # VALIDATE ON MACOS HOST: AXIsProcessTrusted() returns
                # the correct value on real macOS (the existing
                # tests/test_a11y_pulse.py suite exercises the same
                # ctypes probe on macOS CI).
                try:
                    import ctypes

                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    _has_accessibility = bool(app_services.AXIsProcessTrusted())
                except Exception as exc:
                    # drop the osascript fallback entirely. Treat
                    # ctypes-load failure as "permission not granted"
                    # (False) — the A11yPulse task (started below at
                    # line 406) will re-probe within 60s and update
                    # `_has_accessibility` + the tray icon when the
                    # grant is detected. This avoids the 3s osascript
                    # subprocess on the startup hot path AND avoids
                    # the invasive keystroke synthesis.
                    log.warning(
                        "[STARTUP] macOS AXIsProcessTrusted ctypes load "
                        "failed (%s); treating as not-granted. A11yPulse "
                        "will re-probe within 60s.",
                        exc,
                    )
                    _has_accessibility = False

                if not _has_accessibility:
                    log.warning("[STARTUP] macOS Accessibility permission not granted")
                    # critical — bypass toggle (hotkeys broken).
                    app.tray.notify_safety(
                        f"{APP_NAME} — Accessibility Permission",
                        "Global hotkeys require Accessibility permission. "
                        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility "
                        f"and add {APP_NAME} (or Terminal).",
                    )
            except Exception:
                # M-67: promote debug→warning so the failure surfaces in
                # the default log; include the traceback so operators can
                # diagnose why the macOS accessibility check failed.
                log.warning("[STARTUP] macOS accessibility check failed", exc_info=True)

            # Start a periodic accessibility health monitor.
            # If the user grants permission AFTER startup, the app will
            # detect it within 60 seconds and clear the warning. If the
            # user revokes permission mid-session, the app will re-warn.
            # Phase 2: invoke startup_tasks directly. The
            # ``app._start_accessibility_pulse`` delegate was removed; callers
            # now target startup_tasks (and tests monkeypatch startup_tasks).
            from voice_typer.server import startup_tasks

            startup_tasks.start_accessibility_pulse(app, _has_accessibility)

        # 1. Sync autostart config with platform
        log.debug("[STARTUP] Syncing autostart")
        # Phase 2: invoke startup_tasks directly. The
        # ``app._sync_autostart`` delegate was removed; callers now target
        # startup_tasks (and tests monkeypatch startup_tasks).
        from voice_typer.server import startup_tasks

        # (a): sync_autostart returns a result dict whose
        # ``actual_post_sync`` field carries the post-sync OS-level
        # autostart state (True iff the OS-level autostart entry is
        # currently registered). Use that field instead of calling
        # ``is_autostart_enabled()`` a second time — the pre- path
        # called the platform helper twice back-to-back on every startup,
        # and the second call always returned the same value as the one
        # sync_autostart already read internally. Falling back to a direct
        # ``is_autostart_enabled()`` call only when sync_autostart's
        # result lacks the field (older test stubs that monkeypatch
        # sync_autostart to return ``None``).
        autostart_result = startup_tasks.sync_autostart(app)
        if isinstance(autostart_result, dict) and "actual_post_sync" in autostart_result:
            autostart_enabled = bool(autostart_result["actual_post_sync"])
        else:
            # Test-stub fallback: the monkeypatched sync_autostart returned
            # ``None`` (or a dict without the field). Fall back to the
            # direct platform read so the tray menu shows the real state.
            autostart_enabled = is_autostart_enabled()
        app.tray.set_autostart_enabled(autostart_enabled)

        # RACE-020: check for shutdown after each major step
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after autostart sync")
            return

        # 1b. Sync the OS-level prewarm scheduled task.
        #     fast_startup is always enabled; the prewarm task is registered
        #     at startup so the OS file cache is kept warm.  Cheap (a single
        #     schtasks /Query) and self-healing: if the user deleted the task
        #     or moved machines, it gets re-registered.
        #
        # PERF-: prewarm sync + mic enumeration are independent
        # I/O-bound tasks. Run them in parallel so the total startup
        # time is max(t_prewarm, t_mics) instead of t_prewarm + t_mics.
        #
        # the previous implementation used ``ThreadPoolExecutor``,
        # whose worker threads are NON-daemon on Python 3.9+ (CPython's
        # ``_python_exit`` atexit handler joins them with no timeout).
        # If ``sync_prewarm_task`` got stuck inside ``subprocess.run``
        # (``schtasks`` with a 30s timeout against a hung Windows Task
        # Scheduler service), the pool's ``shutdown(wait=False,
        # cancel_futures=True)`` returned immediately but the in-flight
        # worker continued running for up to ~20 more seconds — and
        # ``_python_exit`` then blocked process exit on it (up to 30s
        # hang on Windows Task Scheduler).
        #
        # Fix: use ``_run_parallel_with_timeout`` from ``_timeout_utils``,
        # which dispatches each task via ``_run_with_timeout`` — and
        # ``_run_with_timeout`` wraps the call in a daemon
        # ``threading.Thread``. Daemon threads are NOT registered in
        # CPython's ``_threads_queues``, so ``_python_exit`` skips them
        # entirely. A stuck ``schtasks`` worker therefore does NOT
        # block process exit.
        from voice_typer.server._timeout_utils import (
            TIMEOUT as _TIMEOUT_SENTINEL,
        )
        from voice_typer.server._timeout_utils import (
            _run_parallel_with_timeout,
        )

        # RACE-020: pass the shutdown event to executor tasks so they
        # can abort early if the app is quitting during startup.
        _shutdown_event = app._shutting_down_event if hasattr(app, "_shutting_down_event") else None

        # log the trigger regime that will be registered, so
        # operators can verify from the app-start logs which triggers
        # are in effect.  On Windows the XML task uses BootTrigger +
        # EventTrigger (both system-start), and the Run-key fallback
        # fires at logon.  On POSIX, prewarm_scheduler_posix uses
        # RunAtLoad (macOS) or OnBootSec (Linux).
        _triggers = (
            "boot + event (Task Scheduler XML)"
            if is_windows()
            else "logon (Run-key fallback) or OnBootSec/RunAtLoad (POSIX)"
        )
        log.info(
            "[STARTUP] Syncing prewarm task (triggers: %s)",
            _triggers,
        )

        def _startup_parallel_work() -> None:
            # split the parallel pool. Pre-fix, both ``sync_prewarm_task``
            # and ``load_microphones`` ran in parallel with a 10s per-task
            # timeout, and hotkey registration (line 777) ran AFTER both
            # completed. So if ``sync_prewarm_task`` hung (Windows Task
            # Scheduler can be slow on a cold boot), the user couldn't
            # press F2 to start dictation for up to 10s after the tray
            # icon appeared — a regression on the primary interaction path.
            # Only ``load_microphones`` actually needs to complete before
            # hotkey registration (the tray menu needs the mic list);
            # ``sync_prewarm_task`` is pure housekeeping (re-syncing the
            # Windows Task Scheduler entry / Run-key fallback / launchd
            # plist / systemd unit) and can complete any time later.
            #
            # Fix: spawn ``sync_prewarm_task`` on a fire-and-forget daemon
            # thread (no wait, no timeout); run only ``load_microphones``
            # in the bounded parallel pool with a shorter 5s timeout.
            # ``sync_prewarm_task`` is idempotent and best-effort, so a
            # hung/slow run has no correctness impact (the next launch
            # will re-sync).
            def _prewarm_task() -> None:
                startup_tasks.sync_prewarm_task(app, _shutdown_event)

            prewarm_thread = threading.Thread(
                target=_prewarm_task,
                name="startup-prewarm-sync",
                daemon=True,
            )
            prewarm_thread.start()
            log.debug(
                "[STARTUP] DJ-4: prewarm sync dispatched to fire-and-forget "
                "daemon thread (no wait, no timeout) — hotkey registration "
                "proceeds without waiting on it"
            )

            def _mic_task() -> None:
                startup_tasks.load_microphones(app, _shutdown_event)

            items = [
                ("mic", _mic_task, 5.0),
            ]
            results = _run_parallel_with_timeout(items)
            for label, value in results:
                # ``_run_parallel_with_timeout`` captures per-call
                # failures into the result tuple (caller decides
                # whether to re-raise / log / ignore). ``TIMEOUT``
                # means the task did not finish within its budget;
                # the daemon worker is leaked (and will be reaped at
                # process exit by virtue of being a daemon).
                if value is _TIMEOUT_SENTINEL:
                    log.warning(
                        "[STARTUP] %s task did not complete within 5s budget "
                        "(daemon worker leaked; will not block process exit)",
                        label,
                    )
                elif isinstance(value, BaseException):
                    log.warning("[STARTUP] %s task failed: %s", label, value)
                else:
                    # Task completed successfully (return value is
                    # whatever the task function returned — typically
                    # ``None`` for these two startup tasks).
                    pass
            # PERF-: the 30s ``sd.query_devices()`` device-change
            # poller (``_start_device_change_poller``) was removed from
            # startup because it is fully redundant with the
            # event-driven ``MicrophoneDeviceWatcher`` started in
            # ``Recorder.__init__`` (WM_DEVICECHANGE on Windows,
            # ``/dev/snd`` polling on Linux, CoreAudio property-listener
            # on macOS). The watcher is the sole source of truth; the
            # 30s poller was a defence-in-depth fallback that cost
            # ~1-5ms of CPU every 30s and allocated a fresh
            # ``threading.Event()`` object every second.  Phase 1
            # also deleted the now-orphaned ``_start_device_change_poller``
            # delegate from this class — see test_bugfix_regressions.py
            # ``TestAudioMicDeviceChangePoller`` for the full history.

        # 1b. Create desktop launcher shortcut on first run (if absent)
        # (Run before parallel work so the shortcut exists before mic
        # enumeration — they're independent but shortcut creation is
        # fast and quick to fail.)
        # Phase 2: call startup_tasks directly.
        startup_tasks.ensure_desktop_shortcut(app)

        log.debug("[STARTUP] Running prewarm sync + mic enumeration")
        _startup_parallel_work()

        # RACE-020: check for shutdown after parallel work
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted before hotkey registration")
            return

        # 3. Register hotkey BEFORE model load so F2 works even if model fails
        log.debug("[STARTUP] Registering hotkey")
        # Phase 2: invoke HotkeyDispatcher directly. The
        # ``app._register_hotkey`` delegate was removed; callers now target
        # ``app.hotkeys`` (and tests monkeypatch app.hotkeys.register).
        app.hotkeys.register()

        # RACE-020: check for shutdown after hotkey registration
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after hotkey registration")
            return

        # Warmup handled synchronously in recording.py on first recording start.

        # 4. Create transcription engine and load model -- IN THE BACKGROUND.
        #
        # The model load is the dominant cost on a cold boot (~30-45s the
        # first time after Windows starts, dominated by reading ~6 GB of
        # torch + model-weight files off disk).  Running it in a daemon
        # thread lets the app reach "Ready" (well, "Loading model…") within
        # ~1s of launch; the user sees the tray icon, can open settings,
        # and -- if they press F2 before the load finishes -- gets queued
        # and auto-started once it completes.  See toggle_dictation().
        #
        # #2 ModelManager owns the load thread now; the
        # ``self._model_load_thread`` property delegate on VoiceTyperApp
        # reads/writes through to ``self.models._model_load_thread`` so
        # existing code that checks the thread (e.g. toggle_dictation)
        # keeps working.
        log.debug("[STARTUP] Loading model in background")
        app.models.start_background_load()

        # RACE-020: check for shutdown after background model load start
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after model load start")
            return

        # After restart: auto-open the Electron window so it appears fresh
        # once the new instance is fully ready.  The VOICE_TYPER_RESTART
        # env var is set by restart_app() before launching the new process.
        if os.environ.get("VOICE_TYPER_RESTART"):
            log.info("[STARTUP] Restart detected -- opening Electron window")
            try:
                app.tray.open_electron_window()
            except Exception as e:
                log.warning("[STARTUP] Failed to open Electron window after restart: %s", e)

        # Show the bubble at startup if always_visible mode is enabled AND
        # bubble_show_on_startup is True (user's preference in Settings).
        if app.config.bubble_behavior == "always_visible" and app.config.bubble_show_on_startup:
            try:
                app._waveform_bubble.show()
                log.info("[STARTUP] Bubble shown at startup (always_visible mode)")
            except Exception as e:
                log.warning("[STARTUP] Failed to show bubble at startup: %s", e)
            # push the bubble-relevant config (bubble_behavior
            # bubble_click_to_toggle / bubble_mic_button) so the bubble
            # renderer knows whether to show its mic button. The bubble
            # is sandboxed and cannot call get_config itself.
            try:
                cb = app._waveform_bubble.on_config
                if cb is not None:
                    cb(app.config)
            except Exception as e:
                log.debug("[STARTUP] Failed to push bubble config: %s", e)

        log.info("[STARTUP] Startup complete, model still loading in background")
