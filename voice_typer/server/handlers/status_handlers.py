"""Status IPC handler mixin: get_status, get_model_status,
get_volume_backend_status.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

(2026-07-30): ``_handle_get_rms_level`` and
``_handle_get_audio_status`` were REMOVED — both commands were
dropped from ``_COMMAND_REGISTRY`` and the renderer allowlist during
the Tauri migration. The service-layer methods
``service.get_rms_level`` / ``service.get_audio_status`` still exist
for internal callers; only the IPC dispatch routes were deleted.

(Wave 3, 2026-08-14): ``_handle_get_prewarm_status``,
``_handle_run_prewarm``, and ``_handle_open_prewarm_log`` were
REMOVED in lockstep with the matching ``_COMMAND_REGISTRY`` entries,
the TS ``ALLOWED_COMMANDS`` Set, and the Rust ``allowed_commands()``
literal (see the restoration note below for the reversal).

(RESTORED 2026-08-14): ``_handle_get_prewarm_status`` and
``_handle_open_prewarm_log`` were restored verbatim from commit
5a319872 (``voice_typer/server/handlers/status_handlers.py``) because
the Cache Status card in the About page is a user-facing product
feature, not prewarm machinery — plan §6.2 P-1 only removed the
machinery (separate prewarm binary, OS schedulers, resolver).
``_handle_run_prewarm`` was also restored the same day (second half
of the §6.3 addendum), but RE-IMPLEMENTED for the post-P-1
architecture: the old version spawned a detached
``pythonw -m voice_typer.server.prewarm --force`` subprocess, and that
module is deleted by design. The restored handler instead runs the
worker's warm phase in-process — :func:`run_prewarm_now` in
``prewarm/status.py`` executes :func:`warm_imports_for_worker` (a
pure file-paging pass over the runtime-pack libraries) on a daemon
thread and refreshes the status file. Same user-visible behavior
("Run Prewarm Now" re-warms the OS standby cache), zero deleted
machinery. "start/stop" of the automatic warm phase remains the
``fast_startup`` toggle in Settings → General, which gates the
worker's startup warm phase. The handler trio was re-registered in
lockstep across all three allowlists (Python registry ↔ TS
allowlist ↔ Rust allowlist) per the §6.4 IPC parity contract —
pinned by ``tests/test_command_registry_parity.py`` and
``tests/test_electron_ipc_and_build.py::TestAllowlistCorrectness::test_allowlist_matches_server_commands``.
"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import ErrorCodes, LegacyErrorCodes, _error_response  # noqa: F401
from voice_typer.server.platform_utils import is_windows


class StatusHandlersMixin(HandlerBase):
    """Mixin: status-query IPC handlers (get_status / get_model_status / ...).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak). Specific ``FileNotFoundError`` / ``OSError``
        catch branches keep their descriptive messages (which are safe —
        no Python internals / PII) but now route through
        :func:`_error_response` with an explicit ``code`` field
        so clients can branch on the code rather than
        pattern-matching the message text.
    """

    def _handle_get_status(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_status`` IPC command.

        (session-DE): this was the only status handler with NO
                ``try/except`` and NO ``_validate_dict_payload`` call. The fix
                wraps the body in a ``try/except Exception`` routing through
                :meth:`HandlerBase._respond_with_error` (so a service-layer
                exception gets the generic ``server.internal_error`` envelope
                with ``cmd_name='get_status'`` log attribution, instead of
                propagating to the dispatcher's outer catch-all and losing the
                command-name context). A non-dict ``data`` payload is now
                rejected with ``invalid_payload`` to match the documented
                ADR-0020 §2 contract that every sibling handler enforces.
                ``None`` is pre-coerced to ``{}`` to preserve the
                ``test_none_payload_is_coerced_to_empty_dict`` contract.
        """
        try:
            if not isinstance(data, dict):
                if data is None:
                    data = {}
                else:
                    resp["type"] = "error"
                    resp["data"] = {
                        "code": ErrorCodes.INVALID_PAYLOAD,
                        "message": "data must be an object",
                    }
                    return resp
            resp["type"] = "status"
            # get_status() now returns a dict with status +
            # xruns_since_start. Preserve backward-compat by passing
            # the whole dict through.
            status_data = self.service.get_status()
            if isinstance(status_data, dict):
                resp["data"] = status_data
            else:
                # Backward-compat: older service.get_status() returned a string.
                resp["data"] = {"status": status_data}
        except Exception as exc:
            # route through ``_respond_with_error`` so the
            # exception is attributed to ``get_status`` in the log
            # (instead of propagating to the dispatcher's generic
            # catch-all) and the renderer sees the standard
            # ``server.internal_error`` envelope.
            self._respond_with_error(resp, exc, "get_status")
        return resp

    def _handle_get_volume_backend_status(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_volume_backend_status`` IPC command."""
        # Returns the active volume backend's name + capability flags
        # delegates to service layer
        try:
            status = self.service.get_volume_backend_status()
            status["is_windows"] = is_windows()
            resp["type"] = "volume_backend_status"
            resp["data"] = status
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_volume_backend_status")
        return resp

    def _handle_get_model_status(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_model_status`` IPC command."""
        # Item 10/11: check which models are actually on disk.
        # Returns a dict mapping model name → {downloaded: bool, deps_ok: bool}.
        # delegates to service layer
        try:
            status = self.service.get_model_status()
            resp["type"] = "model_status"
            resp["data"] = status
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_model_status")
        return resp

    def _handle_get_prewarm_status(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_prewarm_status`` IPC command.

        ADR-0009 Issue 3: returns a snapshot of the prewarm cache state
        for the About page's "Cache Status" card. The probe runs in the
        IPC handler thread (small random 4K reads, ~1ms total) and
        degrades gracefully to ``unknown`` if the worker status file or
        model file is absent.
        """
        try:
            from voice_typer.server.prewarm import get_prewarm_status

            resp["type"] = "prewarm_status"
            resp["data"] = get_prewarm_status()
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_prewarm_status")
        return resp

    def _handle_run_prewarm(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``run_prewarm`` IPC command — re-warm the OS cache now.

        RESTORED 2026-08-14 (plan §6.3 addendum, second half). The
        pre-P-1 handler spawned a detached prewarm subprocess; that
        machinery is deleted by design (P-1). The restored handler
        instead re-runs the warm phase in-process via
        ``prewarm.status.run_prewarm_now()`` — a background daemon
        thread that calls :func:`warm_imports_for_worker` (pages the
        runtime-pack libraries' files into the OS standby cache) and
        refreshes the worker status file.

        Returns immediately with ``{"started": True}``. The frontend
        polls ``get_prewarm_status`` to show progress (the restored
        status response carries ``enabled`` / ``last_run`` /
        ``elapsed_s`` — the old ``prewarm_running`` field was dropped
        with the process-tracker machinery).
        """
        try:
            from voice_typer.server.prewarm.status import run_prewarm_now

            started = run_prewarm_now()
            log.info("[IPC] run_prewarm: started background warm run (started=%s)", started)
            resp["type"] = "prewarm_started"
            resp["data"] = {"started": started}
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "run_prewarm")
        return resp

    def _handle_open_prewarm_log(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``open_prewarm_log`` IPC command.

        Task 2: opens the prewarm log file in the OS default text
        editor.  Restored verbatim from 5a319872 with ONE adaptation:
        the file is now ``worker.log`` (next to ``voice-typer.log``) —
        the runtime-pack worker owns the warm phase, so its log is the
        dedicated prewarm record (it carries all ``[PREWARM]`` /
        ``[STARTUP] worker prewarm phase`` lines).

        The main ``voice-typer.log`` also contains these messages
        (it is the complete record).  This handler opens the worker's
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
            # The worker log lives in the app config dir's logs/ subdir
            # (O1 — every log file lives under ``logs/``).
            from voice_typer.server import _paths
            from voice_typer.server.log import get_log_file_path

            log_file = get_log_file_path(config_dir=_paths.config_dir(), process_name="worker")

            if not log_file.exists():
                # File doesn't exist yet (prewarm hasn't run this boot).
                # Create it with a header so the user's editor opens
                # successfully with context about why it's empty.
                # Atomic write (temp + os.replace) so a crash mid-write
                # cannot leave a half-truncated placeholder that the
                # editor would open as garbled text. durability=False
                # matches the existing prewarm pattern — this is a
                # non-critical placeholder file.
                try:
                    from datetime import datetime as _dt

                    from voice_typer.server.secure_file_io import (
                        _secure_atomic_write,
                    )

                    _secure_atomic_write(
                        log_file,
                        "# Prewarm log\n"
                        "#\n"
                        "# This file is created by the worker (which runs the\n"
                        "# prewarm warm-up phase at startup when Fast Startup is\n"
                        "# enabled). It will be empty until the worker executes.\n"
                        "#\n"
                        "# Placeholder created: " + _dt.now().strftime("%Y-%m-%d %H:%M:%S") + "\n",
                        durability=False,
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
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif is_linux():
                subprocess.Popen(
                    ["xdg-open", str(log_file)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Unknown platform — try xdg-open as a last resort.
                subprocess.Popen(
                    ["xdg-open", str(log_file)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            log.info("[IPC] open_prewarm_log: opened %s", log_file)
            resp["type"] = "prewarm_log"
            resp["data"] = {"opened": True, "path": str(log_file)}
        except FileNotFoundError as e:
            # Specific-exception branch — keep the
            # descriptive message (no Python internals / PII — the
            # exception text only echoes the editor binary path the
            # app itself chose) but stamp a structured ``code`` so
            # the renderer can branch on ``not_found``.
            #
            # (session-DE): drop the ``: {e}`` suffix — the
            # ``[Errno 2] No such file: '<path>'`` text embeds the
            # absolute editor path which leaks the username on
            # Windows / macOS. The full ``str(e)`` is still logged
            # server-side at ERROR (above).
            log.error("[IPC] open_prewarm_log: editor not found: %s", e)
            return _error_response(
                resp,
                "No editor available to open the log",
                code="server.not_found",
            )
        except OSError as e:
            # ``OSError`` from the editor ``Popen`` —
            # route through ``_error_response`` for envelope-shape
            # consistency (the ``str(e)`` is typically
            # "[Errno 13] Permission denied: …" — no Python internals).
            #
            # (session-DE): drop the ``: {e}`` suffix — the
            # embedded absolute path leaks the username.
            log.error("[IPC] open_prewarm_log: open failed: %s", e, exc_info=True)
            return _error_response(
                resp,
                "Failed to open log",
                code="server.handler_error",
            )
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "open_prewarm_log")
        return resp
