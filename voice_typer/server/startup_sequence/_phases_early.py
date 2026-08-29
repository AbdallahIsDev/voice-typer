"""Early startup phases (1-4) + onboarding fail counter + StageResult.

Half of the ``StartupSequence`` phase decomposition, extracted verbatim
from the former ``startup_sequence.py`` monolith:

- phase 1 — startup banner + eager Silero VAD preload thread
- phase 2 — crash-diagnostics check + stale backup/``.tmp`` sweeps
- phase 3 — session-active marker + onboarding wizard check / auto-heal
  / persisted 3-failure circuit breaker
- phase 4 — corrections load + crash-recovery check + history retention

This module also owns the persisted onboarding fail-counter helpers
(the "after 3 failures" circuit breaker state) and the
:class:`StageResult` dataclass every phase returns.

Patch-target contract (C-ARCH-2): ``configure_corrections`` and
``_config_dir`` are bound HERE — tests patch
``voice_typer.server.startup_sequence._phases_early.configure_corrections``
and ``..._phases_early._config_dir`` (the owning submodule), not the
package root.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server import crash_handler as _crash_handler, onboarding_status
from voice_typer.server.branding import APP_NAME
from voice_typer.server.config import _config_dir
from voice_typer.server.startup_sequence import _maintenance
from voice_typer.server.text_cleanup import configure_corrections

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle described in the package
    # docstring.  At runtime, ``app`` is whatever object was passed to
    # ``StartupSequence.__init__`` (always a ``VoiceTyperApp`` in
    # production, but tests pass mocks that satisfy the same duck-typed
    # surface).
    from voice_typer.server.app import VoiceTyperApp

# Explicit logger name (not ``__name__``): tests capture logs at the
# pre-split ``voice_typer.server.startup_sequence`` logger (same
# convention as ``app_lifecycle.py``), and this module's records must
# keep landing there after the split.
log = logging.getLogger("voice_typer.server.startup_sequence")

# Onboarding fail counter: the "after 3 failures" circuit breaker
# (see the onboarding block in the session/onboarding phase) persists its
# counter so it actually trips even when each failure occurs in a
# different process session. Pre-fix the counter lived only on
# ``app._onboarding_fail_count`` (an in-memory attribute), so a user
# whose onboarding kept failing once per app-start would NEVER hit
# the circuit breaker and would be stuck on the onboarding wizard
# forever. The counter now lives in the single
# ``.onboarding_status.json`` document (``fail_count`` /
# ``last_fail_ts`` fields) managed by ``voice_typer.server.onboarding_status`` — which
# also holds the wizard's started/completed flags, replacing the
# legacy ``.onboarding_complete`` / ``.onboarding_started`` /
# ``.onboarding_fail_count`` markers.
# Stale-counter cutoff: if the last failure is older than this window
# (in seconds), the counter resets to 1 on the next failure. Prevents
# a user who hit 2 failures a year ago from being marked
# onboarding_failed the next time they restart with a transient
# failure. 7 days matches the onboarding wizard's "won't bother the
# user again" cadence.
_ONBOARDING_FAIL_COUNTER_TTL_SECONDS: float = 7 * 24 * 60 * 60.0


def _onboarding_fail_counter_path() -> Path:
    """Return the absolute path to the onboarding status file (which
    holds the fail counter alongside the started/completed flags)."""
    return onboarding_status.status_path(_config_dir())


def _read_onboarding_fail_count() -> tuple[int, float]:
    """Read the persisted onboarding fail counter.

    Returns ``(count, last_fail_ts)``. On any read failure (missing
    file, corrupt JSON, schema drift), returns ``(0, 0.0)`` — the
    safe default that lets the next failure start the counter fresh.
    """
    data = onboarding_status.read_status(_config_dir())
    return data["fail_count"], data["last_fail_ts"]


def _write_onboarding_fail_count(count: int, last_fail_ts: float) -> None:
    """Persist the onboarding fail counter to disk.

    Failures are best-effort — a write error is logged at DEBUG and
    swallowed (the in-memory counter on ``app._onboarding_fail_count``
    is still incremented, so the circuit breaker can still trip
    in-session even if persistence is broken). durability=False
    matches the existing autostart/prewarm pattern.
    """
    try:
        onboarding_status.write_status(
            _config_dir(),
            durability=False,
            fail_count=count,
            last_fail_ts=last_fail_ts,
        )
    except OSError as exc:
        log.debug(
            "[STARTUP] Could not persist onboarding fail counter to %s: %s",
            _onboarding_fail_counter_path(),
            exc,
        )


def _reset_onboarding_fail_count() -> None:
    """Clear the persisted onboarding fail counter.

    Called on successful onboarding completion so a future transient
    failure doesn't accumulate against the stale count. Best-effort:
    a write error is logged at DEBUG. The started/completed flags in
    the status document are preserved — resetting the counter must not
    un-complete onboarding.
    """
    try:
        onboarding_status.write_status(_config_dir(), fail_count=0, last_fail_ts=0.0)
    except OSError as exc:
        log.debug(
            "[STARTUP] Could not reset onboarding fail counter at %s: %s",
            _onboarding_fail_counter_path(),
            exc,
        )


@dataclass
class StageResult:
    """Outcome of a single startup phase.

    success=True means the phase completed normally and the next phase
    should run. success=False means the phase short-circuited the
    startup (currently only happens when ``app._shutting_down`` is set
    mid-startup — the phase already emitted the canonical
    "Interrupted after ..." / "_shutting_down is set, aborting startup"
    log line per the original monolithic ``run()`` body, so the
    orchestrator just returns without further logging).

    ``error`` carries a short description when the phase failed for a
    non-shutdown reason (currently unused — every phase swallows its
    own exceptions and logs them at debug/warning, matching the
    pre-refactor behavior). ``data`` is reserved for structured
    payloads (e.g. ``{"shutdown": True}``).
    """

    success: bool
    error: str | None = None
    data: dict | None = field(default=None)


class EarlyPhases:
    """Phases 1-4 of the startup sequence (mixin for ``StartupSequence``).

    ``app`` is a back-reference so the phases can read/write the app's
    state (config, tray, models, hotkeys, etc.) — same attribute surface
    as the pre-extraction monolith, just renamed from ``self.X`` to
    ``self._app.X``.
    """

    # Back-reference to the owning ``VoiceTyperApp`` (assigned by
    # ``StartupSequence.__init__`` in the package ``__init__``).
    _app: VoiceTyperApp

    def _phase_1_init_and_vad_preload(self) -> StageResult:
        """Phase 1 — anchor the startup duration + preload Silero VAD.

        Emits the canonical ``[STARTUP] Initializing: ...`` banner and
        spawns the eager VAD preload daemon thread (fire-and-forget,
        registered with the app's ThreadRegistry when available) so the
        VAD model is hot by the time the user first presses F2.
        """
        app = self._app
        log.info("[STARTUP] Initializing: autostart, microphones, hotkey, model...")

        # eagerly preload + warm the Silero VAD model on a
        # daemon thread (fire-and-forget) so the model is hot by the
        # time the user first presses F2. Otherwise the first
        # ``~150-600ms`` of speech is silently dropped via ring-buffer
        # overflow. The thread is best-effort: failures are logged at
        # DEBUG and the lazy-load fallback in ``compute_vad_prob`` is
        # preserved. The eager preload in ``VoiceTyperApp.__init__``
        # still runs (it was there first); this call makes the
        # preload observable to test fixtures that only instantiate
        # ``StartupSequence`` after patching ``vad.preload``.
        try:
            from voice_typer.server import vad

            def _vad_preload_worker() -> None:
                try:
                    vad.preload()
                except Exception:
                    log.debug("[STARTUP] vad.preload() failed", exc_info=True)

            # Register with the app's thread registry (mirroring
            # ``VoiceTyperApp._preload_vad_model``) so ``shutdown_all()``
            # joins it cleanly. Under the test suite, an unregistered
            # preload thread would otherwise outlive its test and — if
            # it woke during a ``real_torch`` window — load real torch
            # + the real Silero model concurrently with other tests'
            # native work, contributing to rare heap corruption.
            registry = getattr(app, "_thread_registry", None)
            if registry is not None and hasattr(registry, "spawn_and_register"):
                registry.spawn_and_register(
                    "vad-preload-startup",
                    _vad_preload_worker,
                    daemon=True,
                    join_timeout=2.0,
                )
            else:
                threading.Thread(
                    target=_vad_preload_worker,
                    name="vad-preload-startup",
                    daemon=True,
                ).start()
        except Exception:
            log.debug("[STARTUP] could not spawn vad-preload thread", exc_info=True)

        return StageResult(success=True)

    def _phase_2_crash_diagnostics(self) -> StageResult:
        """Phase 2 — detect leftover crash reports from a prior session.

        Reads ``crash_diagnostics.<PID>.txt`` written by the VEH
        handler on silent SEH exceptions, archives them for support,
        and — only when the previous session genuinely ended
        abnormally (the ``session_active`` marker is still present) —
        surfaces a calm user-facing recovery toast + Electron
        notification. Also sweeps stale corrupt-quarantine /
        pre-migration backup files (30-day retention) and stale
        ``.tmp`` atomic-write leftovers (5-min retention).
        """
        app = self._app
        # ── CRASH DIAGNOSTICS: check for leftover crash reports ──────
        # The VEH handler (crash_handler.py) writes crash_diagnostics.<PID>.txt
        # when a previous process was killed by STATUS_HEAP_CORRUPTION or
        # another silent SEH exception.  We read them here, log them to
        # voice-typer.log, and — only when the previous session genuinely
        # ended abnormally — show a calm user-facing notification.
        #
        # SESSION-STATE-GATE: the ``session_active`` marker (see
        # ``session_state.py``) is written at session start and removed
        # on every clean-shutdown path (``_do_cleanup`` /
        # ``_do_fast_cleanup``). A leftover ``python_crash.*.txt`` /
        # ``crash_diagnostics.*.txt`` file is NOT by itself evidence of
        # a crash: daemon threads that raise during interpreter teardown
        # (socket close, backend restart/reload kills) write markers
        # while the session is still exiting cleanly. The notification
        # therefore only fires when crash files exist AND the previous
        # session did not shut down cleanly (marker still present).
        try:
            # Resolve the config dir via ``app`` module attribute so
            # tests that monkeypatch ``voice_typer.server.app._config_dir``
            # (the ``tmp_config_dir`` fixture) are honored — mirrors the
            # lazy lookup pattern in ``single_instance._backend_pid_file``.
            from voice_typer.server import app as _app_module, session_state

            _startup_config_dir = _app_module._config_dir()
            _previous_session_abnormal = session_state.was_previous_session_abnormal(_startup_config_dir)
            # Sweep stale corrupt-quarantine and pre-migration backup files
            # (30-day retention). Mirrors the log-rotation and crash-diagnostics
            # sweeps. Best-effort — never aborts startup on a sweep error.
            with contextlib.suppress(Exception):
                _maintenance._sweep_stale_backup_files(_startup_config_dir)
            crash_summary = _crash_handler.report_pending_crash(_startup_config_dir)
            if crash_summary:
                if _previous_session_abnormal:
                    # Log at WARNING so it appears prominently in voice-typer.log
                    log.warning("[STARTUP] Previous session crashed! See log lines above for full diagnostics.")
                    # Genuine unexpected termination (no clean shutdown
                    # was recorded) — surface a calm, user-facing
                    # recovery toast. CRASH-NOTIFY: technical details
                    # (crash summary, stack traces, python commands)
                    # stay in the log/diagnostics only — never in a
                    # system notification. ``critical`` bypasses the
                    # show_notifications toggle so the user always sees
                    # crash alerts.
                    _crash_body = (
                        f"{APP_NAME} didn't close properly last time. "
                        "We've restarted it and recovered your app.\n\n"
                        "If this happens often, open Settings \u2192 Privacy \u2192 "
                        "Diagnostics for details and help."
                    )
                    try:
                        app.tray.notify_safety(APP_NAME, _crash_body)
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
                                    "title": APP_NAME,
                                    "message": _crash_body,
                                    "duration_ms": 15000,
                                    "critical": True,
                                    # Clicking the toast opens Settings
                                    # (Diagnostics live in Settings ->
                                    # Privacy) — the user's clear next
                                    # action, no terminal required.
                                    "click_path": "/settings",
                                },
                            }
                        )
                    except Exception as exc:
                        log.debug("[STARTUP] Could not publish crash event to frontend: %s", exc)
                else:
                    # The previous session shut down cleanly — the
                    # crash files are teardown noise (daemon-thread
                    # exceptions during interpreter exit, backend
                    # restart/reload kills) or stale leftovers, NOT a
                    # crash. They have already been archived by
                    # ``report_pending_crash`` for support; do not
                    # alarm the user and do not log a misleading
                    # "crashed" WARNING.
                    log.info(
                        "[STARTUP] Crash diagnostics found but previous session "
                        "shut down cleanly — suppressing crash notification "
                        "(diagnostics archived for support)"
                    )
        except Exception as exc:
            log.debug("[STARTUP] Crash diagnostic check failed: %s", exc)

        return StageResult(success=True)

    def _phase_3_session_and_onboarding(self) -> StageResult:
        """Phase 3 — record session-active marker + run onboarding wizard check.

        RACE-020: aborts startup (returns ``success=False``) if
        ``app._shutting_down`` is set; the canonical
        ``_shutting_down is set, aborting startup`` INFO line is
        emitted before returning. On a normal path, records the
        session-active marker (so a later crash is detectable on the
        next launch), then either auto-heals stale onboarding state
        (config.json exists but onboarding_completed is False and the
        ``.onboarding_started`` marker is missing) or saves the
        default config so the frontend's first-run IPC route can
        detect a genuine first run. Onboarding failures are persisted
        (with TTL-stale reset) and a 3-failure circuit breaker marks
        onboarding completed with a failure flag so the app stays
        usable.
        """
        app = self._app
        if app._shutting_down:
            log.info("[STARTUP] _shutting_down is set, aborting startup")
            return StageResult(success=False, data={"shutdown": True})

        # Session begins here: record the session-active marker AFTER
        # the previous session's crash check consumed its state, so a
        # crash later in this startup (or any time before a clean
        # shutdown) is detectable on the next launch. Aborting above
        # (``_shutting_down``) means no real session started — no marker.
        try:
            from voice_typer.server import app as _app_module, session_state

            session_state.mark_session_active(_app_module._config_dir())
        except Exception as exc:
            log.debug("[STARTUP] Could not mark session active: %s", exc)

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
        # user's hotkey, model, and microphone settings with onboarding
        # defaults (<caps_lock>, tiny, None).
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
                    started = onboarding_status.read_status(_config_dir()).get("started", False)
                    if config_file.exists() and not started:
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

        return StageResult(success=True)

    def _phase_4_corrections_and_recovery(self) -> StageResult:
        """Phase 4 — load corrections + crash recovery + history retention.

        Loads external text corrections (surfacing load errors via a
        tray notification), checks for unpasted transcriptions from a
        prior crashed session, applies the history retention policy
        on a daemon thread (so the SQLite DELETEs don't block the
        hotkey-registration critical path), and schedules the periodic
        retention sweep so the DB doesn't grow monotonically during
        long sessions.
        """
        app = self._app
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
                        f"Recovered {len(unpasted)} transcriptions from last session. Open History to view.",
                    )
            except Exception:
                # M-67: promote debug→warning so the failure surfaces in
                # the default log; include the traceback so operators can
                # diagnose why crash-recovery check failed.
                log.warning("[STARTUP] Crash recovery check failed", exc_info=True)

        # apply history retention policy at startup.
        # Previously the config keys were saved but never read.
        # spawn a daemon thread so the SQLite DELETEs (which
        # can take 100ms+ on a large history DB with index rebuilds)
        # don't block the startup critical path to hotkey registration
        # + model load. Retention is best-effort housekeeping — a
        # 100ms delay before stale entries are pruned is invisible to
        # the user, but a 100ms delay before F2 works is not. The
        # thread is a daemon so it never blocks process exit, and the
        # inner try/except ensures any DB error is swallowed (the
        # next startup will retry).
        # register with ``app._thread_registry`` so
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
        # register with the central ThreadRegistry.
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

        return StageResult(success=True)
