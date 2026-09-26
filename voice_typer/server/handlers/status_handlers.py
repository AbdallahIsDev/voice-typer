"""Status IPC handlers: get_status / get_model_status / volume backend."""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (  # noqa: F401
    ErrorCodes,
    LegacyErrorCodes,
    ResponseEnvelope,
    _error_response,
)
from voice_typer.server.platform_utils import is_windows


class StatusHandlersMixin(HandlerBase):
    """Mixin: status-query IPC handlers."""

    def _handle_get_status(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_status`` IPC command.

        Validates payload; None coerces to {} (test contract).
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
            status_data = self.service.get_status()
            if isinstance(status_data, dict):
                resp["data"] = status_data
            else:
                # Backward-compat: older service.get_status() returned a string.
                resp["data"] = {"status": status_data}
        except Exception as exc:
            # (instead of propagating to the dispatcher's generic
            self._respond_with_error(resp, exc, "get_status")
        return resp

    def _handle_get_volume_backend_status(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_volume_backend_status`` IPC command."""
        # Returns the active volume backend's name + capability flags
        try:
            status = self.service.get_volume_backend_status()
            status["is_windows"] = is_windows()
            resp["type"] = "volume_backend_status"
            resp["data"] = status
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_volume_backend_status")
        return resp

    def _handle_get_model_status(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_model_status`` IPC command."""
        # Item 10/11: check which models are actually on disk.
        try:
            status = self.service.get_model_status()
            resp["type"] = "model_status"
            resp["data"] = status
            self._attach_model_storage_summary(status)
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_model_status")
        return resp

    def _attach_model_storage_summary(self, status: object) -> None:
        """Add the ``_storage`` summary to a ``get_model_status`` payload."""
        # Storage rides on the existing status command so the Models page
        # needs no extra round-trip (fewer parity touchpoints than a new
        # command). Probes are guarded: storage must never break per-model truth.
        try:
            from voice_typer.server import _paths

            config_dir = _paths.config_dir()
            hub = config_dir / "huggingface" / "hub"
            used = self._dir_size_bytes(str(hub)) if hub.is_dir() else 0
            if isinstance(status, dict):
                status["_storage"] = {
                    "used_bytes": used,
                    "hub_path": str(hub),
                    "config_dir": str(config_dir),
                }
        except Exception:
            log.debug("[IPC] get_model_status storage summary skipped", exc_info=True)

    @staticmethod
    def _dir_size_bytes(root: str) -> int:
        """Sum regular-file sizes under ``root`` without following symlinks."""
        import os

        total = 0
        try:
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    path = os.path.join(dirpath, name)
                    if os.path.islink(path):
                        continue
                    try:
                        total += os.path.getsize(path)
                    except OSError:
                        continue
        except OSError:
            pass
        return total

    def _handle_get_prewarm_status(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_prewarm_status`` IPC command."""
        try:
            # Module-attr call-time read (not ``from ... import``): test
            from voice_typer.server.prewarm import status

            resp["type"] = "prewarm_status"
            resp["data"] = status.get_prewarm_status()
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_prewarm_status")
        return resp

    def _handle_run_prewarm(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``run_prewarm`` IPC command, re-warm the OS cache now.

        Returns immediately with ``{"started": True}``. The frontend
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

    def _handle_open_data_folder(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``open_data_folder`` IPC command.

        Opens the app config dir (support case, not the raw hub) in the
        OS file manager. Mirrors ``_handle_open_prewarm_log``.
        """
        import os
        import subprocess

        from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

        try:
            from voice_typer.server import _paths
            from voice_typer.server.config import _is_path_within

            target = _paths.config_dir()
            resolved = target.resolve()
            hub = (target / "huggingface" / "hub").resolve()
            # Fixed target today, but keep the containment tripwire so a
            # future parameterized folder cannot escape config/hub roots.
            if not any(_is_path_within(resolved, root) for root in (resolved, hub)):
                return _error_response(
                    resp,
                    "Data folder is outside the allowed roots",
                    code=ErrorCodes.PATH_NOT_ALLOWED,
                )

            if not target.exists():
                resp["type"] = "data_folder"
                resp["data"] = {
                    "opened": False,
                    "path": str(target),
                    "reason": "not_found",
                }
                log.info("[IPC] open_data_folder: folder not found at %s", target)
                return resp

            # Open with the OS default file manager.
            if is_windows():
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif is_macos():
                subprocess.Popen(
                    ["open", str(target)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif is_linux():
                subprocess.Popen(
                    ["xdg-open", str(target)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Unknown platform, try xdg-open as a last resort.
                subprocess.Popen(
                    ["xdg-open", str(target)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            log.info("[IPC] open_data_folder: opened %s", target)
            resp["type"] = "data_folder"
            resp["data"] = {"opened": True, "path": str(target)}
        except FileNotFoundError as e:
            log.error("[IPC] open_data_folder: file manager not found: %s", e)
            return _error_response(
                resp,
                "No file manager available to open the folder",
                code="server.not_found",
            )
        except OSError as e:
            # route through ``_error_response`` for envelope-shape
            log.error("[IPC] open_data_folder: open failed: %s", e, exc_info=True)
            return _error_response(
                resp,
                "Failed to open folder",
                code="server.handler_error",
            )
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "open_data_folder")
        return resp

    def _handle_open_prewarm_log(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``open_prewarm_log`` IPC command.

        Returns ``{"opened": True, "path": "..."}`` on success,
        """
        import os
        import subprocess

        from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

        try:
            # The worker log lives in the app config dir's logs/ subdir
            from voice_typer.server import _paths
            from voice_typer.server.log import get_log_file_path

            log_file = get_log_file_path(config_dir=_paths.config_dir(), process_name="worker")

            if not log_file.exists():
                # File doesn't exist yet (prewarm hasn't run this boot).
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
                # Unknown platform, try xdg-open as a last resort.
                subprocess.Popen(
                    ["xdg-open", str(log_file)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            log.info("[IPC] open_prewarm_log: opened %s", log_file)
            resp["type"] = "prewarm_log"
            resp["data"] = {"opened": True, "path": str(log_file)}
        except FileNotFoundError as e:
            log.error("[IPC] open_prewarm_log: editor not found: %s", e)
            return _error_response(
                resp,
                "No editor available to open the log",
                code="server.not_found",
            )
        except OSError as e:
            # route through ``_error_response`` for envelope-shape
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
