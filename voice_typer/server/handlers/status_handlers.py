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
        # ADR 0008 §3.1: delegates to the service layer so this
        # handler doesn't tunnel through ``self.service._app._audio_processor``.
        try:
            resp["type"] = "audio_status"
            resp["data"] = self.service.get_audio_status()
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

            # PW-2: pass --trigger manual so the prewarm log records
            # that the user explicitly clicked "Run Prewarm Now".
            cmd = [
                python_bin, "-m", "voice_typer.server.prewarm",
                "--force", "--trigger", "manual",
            ]
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

    def _handle_open_prewarm_log(self, data, resp) -> dict | None:
        """Handle the ``open_prewarm_log`` IPC command.

        Task 2: opens the dedicated prewarm log file in the OS default
        text editor.  The file is ``prewarm.log`` (next to
        ``voice-typer.log``) — it contains only ``[PREWARM]`` messages
        via a logger-name filter applied by ``prewarm._setup_logging()``.

        The main ``voice-typer.log`` also contains these messages
        (it is the complete record).  This handler opens the filtered
        copy so users see only prewarm-related output.

        On Windows: ``os.startfile()`` opens with the default .log editor.
        On macOS: ``open <path>`` (LaunchServices).
        On Linux: ``xdg-open <path>`` (freedesktop default).

        Returns ``{"opened": True, "path": "..."}`` on success,
        ``{"opened": False, "path": "...", "reason": "not_found"}`` if
        the log file doesn't exist yet, or an error response if the OS
        can't open it.
        """
        import os
        import subprocess

        from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

        try:
            # The prewarm log lives in the app config dir. Use the same
            # resolution as _setup_logging() in prewarm.py.
            from voice_typer.server.config import _config_dir
            log_dir = _config_dir()
            log_file = log_dir / "prewarm.log"

            if not log_file.exists():
                # File doesn't exist yet (prewarm hasn't run this boot).
                # Create it with a header so the user's editor opens
                # successfully with context about why it's empty.
                try:
                    from datetime import datetime as _dt
                    log_file.write_text(
                        "# Prewarm log\n"
                        "#\n"
                        "# This file is created by the prewarm process\n"
                        "# when it runs.  It will be empty until\n"
                        "# prewarm executes (at boot, logon, or via\n"
                        "# the Run Prewarm Now button).\n"
                        "#\n"
                        "# Placeholder created: "
                        + _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                        + "\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass

            if not log_file.exists():
                resp["type"] = "prewarm_log"
                resp["data"] = {
                    "opened": False,
                    "path": str(log_file),
                    "reason": "not_found",
                }
                log.info("[IPC] open_prewarm_log: file not found at %s", log_file)
                return resp

            # Open with the OS default editor.
            if is_windows():
                os.startfile(str(log_file))  # type: ignore[attr-defined]
            elif is_macos():
                subprocess.Popen(
                    ["open", str(log_file)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif is_linux():
                subprocess.Popen(
                    ["xdg-open", str(log_file)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                # Unknown platform — try xdg-open as a last resort.
                subprocess.Popen(
                    ["xdg-open", str(log_file)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

            log.info("[IPC] open_prewarm_log: opened %s", log_file)
            resp["type"] = "prewarm_log"
            resp["data"] = {"opened": True, "path": str(log_file)}
        except FileNotFoundError as e:
            log.error("[IPC] open_prewarm_log: editor not found: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": f"No editor available to open the log: {e}"}
        except OSError as e:
            log.error("[IPC] open_prewarm_log: open failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": f"Failed to open log: {e}"}
        except Exception as e:
            log.error("[IPC] open_prewarm_log failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
