"""Status IPC handler mixin: get_status, get_audio_status, get_model_status,
get_volume_backend_status, get_rms_level.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import log
from voice_typer.server.platform_utils import is_windows


class StatusHandlersMixin:
    """Mixin: status-query IPC handlers (get_status / get_audio_status / ...)."""

    # ARCH-REFAC-002 / TASK-10: pyrefly null-safety fix.
    # These attributes are provided at runtime by the IPCServer host
    # class via multiple inheritance. Declaring them as ``Any`` here
    # lets pyrefly type-check the mixin methods in isolation without
    # requiring a Protocol that would couple the mixin to a specific
    # service/app implementation (MagicMock fixtures in tests rely on
    # the loose typing).
    service: "Any"
    app: "Any"
    _send: "Any"

    def _handle_get_status(self, data, resp) -> dict | None:
        """Handle the ``get_status`` IPC command."""
        resp["type"] = "status"
        # ERR-021: get_status() now returns a dict with status +
        # xruns_since_start. Preserve backward-compat by passing
        # the whole dict through.
        status_data = self.service.get_status()
        if isinstance(status_data, dict):
            resp["data"] = status_data
        else:
            # Backward-compat: older service.get_status() returned a string.
            resp["data"] = {"status": status_data}
        return resp

    def _handle_get_rms_level(self, data, resp) -> dict | None:
        """Handle the ``get_rms_level`` IPC command."""
        # AUDIO-RMS: return the current RMS level from the recorder.
        # Allows the Electron UI to show real-time audio level
        # without depending on the waveform bubble callback.
        try:
            result = self.service.get_rms_level()
            resp["type"] = "rms_level"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] get_rms_level failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_volume_backend_status(self, data, resp) -> dict | None:
        """Handle the ``get_volume_backend_status`` IPC command."""
        # Returns the active volume backend's name + capability flags
        # ARCH-005: delegates to service layer
        try:
            status = self.service.get_volume_backend_status()
            status["is_windows"] = is_windows()
            resp["type"] = "volume_backend_status"
            resp["data"] = status
        except Exception as e:
            log.error("[IPC] get_volume_backend_status failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_audio_status(self, data, resp) -> dict | None:
        """Handle the ``get_audio_status`` IPC command."""
        # ADR 0007: returns the current audio filter chain status
        # (filter names, degraded flags, VAD backend, sample rate).
        try:
            app = self.service._app
            processor = getattr(app, "_audio_processor", None)
            if processor is not None:
                resp["type"] = "audio_status"
                resp["data"] = {
                    "filter_chain": processor.filter_names,
                    "degraded": processor.is_degraded,
                    "degraded_reasons": processor.degraded_reasons,
                    "latency_ms": processor.total_latency_ms,
                    "vad_backend": "silero" if getattr(app.config, "use_silero_vad", True) else "rms",
                    "sample_rate": getattr(app.config, "sample_rate", 16000),
                }
            else:
                resp["type"] = "audio_status"
                resp["data"] = {
                    "filter_chain": [],
                    "degraded": False,
                    "degraded_reasons": [],
                    "latency_ms": 0.0,
                    "vad_backend": "rms",
                    "sample_rate": 16000,
                }
        except Exception as e:
            log.error("[IPC] get_audio_status failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_model_status(self, data, resp) -> dict | None:
        """Handle the ``get_model_status`` IPC command."""
        # Item 10/11: check which models are actually on disk.
        # Returns a dict mapping model name → {downloaded: bool, deps_ok: bool}.
        # ARCH-005: delegates to service layer
        try:
            status = self.service.get_model_status()
            resp["type"] = "model_status"
            resp["data"] = status
        except Exception as e:
            log.error("[IPC] get_model_status failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_prewarm_status(self, data, resp) -> dict | None:
        """Handle the ``get_prewarm_status`` IPC command.

        ADR-0009 Issue 3: returns a snapshot of the prewarm cache state
        for the About page's "Cache Status" card. The probe runs in the
        IPC handler thread (small random 4K reads, ~1ms total) and
        degrades gracefully to ``unknown`` if the sentinel or model
        file is absent.
        """
        try:
            from voice_typer.server.prewarm import get_prewarm_status
            resp["type"] = "prewarm_status"
            resp["data"] = get_prewarm_status()
        except Exception as e:
            log.error("[IPC] get_prewarm_status failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_run_prewarm(self, data, resp) -> dict | None:
        """Handle the ``run_prewarm`` IPC command.

        Task 3: triggers a manual prewarm run in a background subprocess.
        The user clicks "Run Prewarm Now" in the About page's Cache
        Status card to re-warm the OS file cache after eviction or on
        first run without rebooting.

        Launches ``pythonw.exe -m voice_typer.server.prewarm --force``
        as a detached subprocess so it doesn't block the IPC thread and
        survives the app's lifetime. ``--force`` bypasses the boot-
        sentinel dedup (the user explicitly asked for a re-run).

        Returns immediately with ``{"started": True}``. The frontend
        polls ``get_prewarm_status`` to track progress (the
        ``prewarm_running`` field flips to True while the subprocess is
        active, then back to False when it exits).
        """
        import subprocess
        import sys
        from pathlib import Path
        from voice_typer.server.platform_utils import is_windows

        try:
            # Resolve the Python interpreter — prefer pythonw.exe on
            # Windows (no console window flashes), fall back to
            # sys.executable. Mirrors task_scheduler._prewarm_command().
            python_bin = sys.executable
            if is_windows():
                pythonw = Path(sys.executable).parent / "pythonw.exe"
                if pythonw.exists():
                    python_bin = str(pythonw)

            cmd = [python_bin, "-m", "voice_typer.server.prewarm", "--force"]
            log.info("[IPC] run_prewarm: spawning %s", " ".join(cmd))

            # Detached subprocess so it survives the app's lifetime.
            # On Windows, CREATE_NO_WINDOW prevents a console flash.
            # On POSIX, start_new_session=True detaches from the app's
            # process group so the prewarm subprocess isn't killed when
            # the app exits.
            kwargs: dict = {}
            if is_windows():
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True

            # Redirect stdout/stderr to DEVNULL — prewarm logs to its
            # own file via _setup_logging(), and we don't want the
            # subprocess's pipe to keep the IPC thread alive.
            kwargs["stdout"] = subprocess.DEVNULL
            kwargs["stderr"] = subprocess.DEVNULL
            kwargs["stdin"] = subprocess.DEVNULL

            proc = subprocess.Popen(cmd, **kwargs)
            log.info(
                "[IPC] run_prewarm: spawned pid=%d (force=True)", proc.pid,
            )

            resp["type"] = "prewarm_started"
            resp["data"] = {"started": True, "pid": proc.pid}
        except FileNotFoundError as e:
            log.error("[IPC] run_prewarm: interpreter not found: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": f"Python interpreter not found: {e}"}
        except OSError as e:
            log.error("[IPC] run_prewarm: spawn failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": f"Failed to start prewarm: {e}"}
        except Exception as e:
            log.error("[IPC] run_prewarm failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
