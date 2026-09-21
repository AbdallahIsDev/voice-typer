"""Early startup phase steps."""

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
from voice_typer.server.text_cleanup import configure_corrections

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle described in the package
    from voice_typer.server.app import VoiceTyperApp

# pre-split ``voice_typer.server.startup_sequence`` logger (same
log = logging.getLogger("voice_typer.server.startup_sequence")

# Onboarding fail counter: the "after 3 failures" circuit breaker
_ONBOARDING_FAIL_COUNTER_TTL_SECONDS: float = 7 * 24 * 60 * 60.0


def _onboarding_fail_counter_path() -> Path:
    """Return the absolute path to the onboarding status file (which
    holds the fail counter alongside the started/completed flags)."""
    return onboarding_status.status_path(_config_dir())


def _read_onboarding_fail_count() -> tuple[int, float]:
    """Read the persisted onboarding fail counter.

    Returns ``(count, last_fail_ts)``. On any read failure (missing
    """
    data = onboarding_status.read_status(_config_dir())
    return data["fail_count"], data["last_fail_ts"]


def _write_onboarding_fail_count(count: int, last_fail_ts: float) -> None:
    """Persist the onboarding fail counter to disk."""
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
    """Clear the persisted onboarding fail counter."""
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
    """Outcome of a single startup phase."""

    success: bool
    error: str | None = None
    data: dict | None = field(default=None)


class EarlyPhases:
    """Phases 1-4 of the startup sequence (mixin for ``StartupSequence``)."""

    # Back-reference to the owning ``VoiceTyperApp`` (assigned by
    _app: VoiceTyperApp

    def _phase_1_init_and_vad_preload(self) -> StageResult:
        """Anchor the startup duration + preload Silero VAD."""
        app = self._app
        log.info("[STARTUP] Initializing: autostart, microphones, hotkey, model...")

        # eagerly preload + warm the Silero VAD model on a
        try:
            from voice_typer.server import vad

            _vad_enabled = True
            try:
                from voice_typer.server.vad_processor import VadProcessor

                _vad_probe = VadProcessor.__new__(VadProcessor)
                _vad_enabled = bool(_vad_probe.compute_vad_enabled(getattr(app, "config", None)))
            except Exception:
                _vad_enabled = True
                log.debug("[STARTUP] VAD-enabled probe failed, preloading anyway", exc_info=True)
            if not _vad_enabled:
                log.debug("[STARTUP] VAD disabled by config, skipping Silero preload")
            else:

                def _vad_preload_worker() -> None:
                    try:
                        vad.preload()
                    except Exception:
                        log.debug("[STARTUP] vad.preload() failed", exc_info=True)

                # Register with the app's thread registry (mirroring what
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
        """Detect leftover crash reports from a prior session."""
        app = self._app
        # The VEH handler (crash_handler.py) writes crash_diagnostics.<PID>.txt
        try:
            # Resolve the config dir via ``app`` module attribute so
            from voice_typer.server import app as _app_module, session_state

            _startup_config_dir = _app_module._config_dir()
            _previous_session_abnormal = session_state.was_previous_session_abnormal(_startup_config_dir)
            # NOTE: the stale corrupt-quarantine / pre-migration backup
            crash_summary = _crash_handler.report_pending_crash(_startup_config_dir)
            if crash_summary:
                if _previous_session_abnormal:
                    # Log at WARNING so it appears prominently in voice-typer.log
                    log.warning("[STARTUP] Previous session crashed! See log lines above for full diagnostics.")
                    # Genuine unexpected termination (no clean shutdown
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
                                    "click_path": "/settings",
                                },
                            }
                        )
                    except Exception as exc:
                        log.debug("[STARTUP] Could not publish crash event to frontend: %s", exc)
                else:
                    # The previous session shut down cleanly, the
                    log.info(
                        "[STARTUP] Crash diagnostics found but previous session "
                        "shut down cleanly, suppressing crash notification "
                        "(diagnostics archived for support)"
                    )
        except Exception as exc:
            log.debug("[STARTUP] Crash diagnostic check failed: %s", exc)

        return StageResult(success=True)

    def _phase_3_session_and_onboarding(self) -> StageResult:
        """RACE-020: aborts startup (returns ``success=False``) if"""
        app = self._app
        if app._shutting_down:
            log.info("[STARTUP] _shutting_down is set, aborting startup")
            return StageResult(success=False, data={"shutdown": True})

        # Session begins here: record the session-active marker AFTER
        try:
            from voice_typer.server import app as _app_module, session_state

            session_state.mark_session_active(_app_module._config_dir())
        except Exception as exc:
            log.debug("[STARTUP] Could not mark session active: %s", exc)

        # #8: Onboarding wizard, detect first run and let the React UI
        if not app.config.onboarding_completed:
            try:
                from voice_typer.server.onboarding import OnboardingController

                onboarding = OnboardingController()
                if onboarding.is_first_run():
                    # Check if config.json already exists on disk.
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
                        _reset_onboarding_fail_count()
                    else:
                        # Genuine first run -- no config.json exists yet.
                        log.info(
                            "[STARTUP] First run detected -- deferring to React "
                            "onboarding wizard (config.onboarding_completed=False)"
                        )
                        app.config.save()
            except Exception as e:
                # previously this was log.debug, which is
                log.exception("[STARTUP] Onboarding check failed: %s", e)
                try:
                    # persist the fail counter to disk so
                    persisted_count, last_fail_ts = _read_onboarding_fail_count()
                    now = time.time()
                    if (
                        persisted_count > 0
                        and last_fail_ts > 0
                        and (now - last_fail_ts) > _ONBOARDING_FAIL_COUNTER_TTL_SECONDS
                    ):
                        # Stale counter. Start fresh. Log at INFO so
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
                        _reset_onboarding_fail_count()
                        # critical, bypass show_notifications toggle.
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
        """Load corrections + crash recovery + history retention."""
        app = self._app
        # Load external text corrections (if available) before any transcription
        try:
            err = configure_corrections(config_dir=app.config.config_dir)
            if err is not None:
                # critical, bypass toggle (broken corrections file).
                try:
                    app.tray.notify_safety(
                        f"{APP_NAME}, Corrections Error",
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
                    count = len(unpasted)
                    log.info("[STARTUP] Found %d unpasted transcriptions from previous session", count)
                    body = f"Found {count} unpasted transcriptions from previous session. Open History to review them."
                    try:
                        app.tray.notify_safety(APP_NAME, body)
                    except Exception:
                        log.debug("[STARTUP] Could not show recovery notification")
                    try:
                        from voice_typer.server import event_bus

                        event_bus.publish(
                            {
                                "type": "notification",
                                "data": {
                                    "title": APP_NAME,
                                    "message": body,
                                    "duration_ms": 15000,
                                    "critical": False,
                                    "click_path": "/history",
                                },
                            }
                        )
                    except Exception:
                        log.debug("[STARTUP] Could not publish recovery event to frontend")
            except Exception:
                # Promote debug→warning so the failure surfaces in
                log.warning("[STARTUP] Crash recovery check failed", exc_info=True)

        # apply history retention policy at startup.
        import threading as _threading

        retention_stop_event = _threading.Event()

        # Resolve the lazy history database once on this thread BEFORE
        try:
            _history_db = app.history_db
        except Exception:
            _history_db = None
            log.debug("[STARTUP] history_db pre-resolve failed, worker will retry", exc_info=True)

        def _apply_retention_bg(stop_event: _threading.Event) -> None:
            try:
                target = _history_db if _history_db is not None else app.history_db
                target.apply_retention(
                    retention_days=app.config.history_retention_days,
                    max_entries=app.config.history_max_entries,
                    retention_count=app.config.history_retention_count,
                )
            except Exception:
                # Promote debug→warning so the failure surfaces in
                log.warning("[STARTUP] History retention apply failed", exc_info=True)
            finally:
                # finished thread (defensive, the thread exits on its
                with contextlib.suppress(Exception):
                    # `# type: ignore[unused-ignore]` meta-suppression —
                    # there was no other `# type: ignore` on this line
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
        try:
            _periodic_target = _history_db if _history_db is not None else app.history_db
            _periodic_target.schedule_periodic_retention(
                interval_s=600.0,
                app=app,
                retention_days=app.config.history_retention_days,
                max_entries=app.config.history_max_entries,
                retention_count=app.config.history_retention_count,
            )
        except Exception:
            log.warning(
                "[STARTUP] could not schedule periodic history retention. DB will grow until next app launch",
                exc_info=True,
            )

        return StageResult(success=True)
