"""System IPC handler mixin: restart_app, quit_app, export_diagnostics,
check_accessibility, set_tray_locale, show_electron_notification.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.ipc_server import log, _push_event_now
from voice_typer.server.platform_utils import is_macos


class SystemHandlersMixin:
    """Mixin: system-level IPC handlers (restart / quit / diagnostics / accessibility / ...)."""

    def _handle_restart_app(self, data, resp) -> dict | None:
        """Handle the ``restart_app`` IPC command."""
        resp["type"] = "ack"
        # NEW-IPC-006: ensure ack carries an explicit ``data: {}`` for
        # shape consistency with the other ack responses.  This call
        # sends the response directly (returns None) so the
        # ``resp.setdefault("data", {})`` at the end of _dispatch
        # never runs for this branch — we add it here instead.
        resp.setdefault("data", {})
        try:
            self._send(resp)
            self.service.restart()
        except Exception as e:
            log.error("[IPC] restart_app failed: %s", e, exc_info=True)
            # The ack was already sent; can't recover from here.
        return None



    def _handle_quit_app(self, data, resp) -> dict | None:
        """Handle the ``quit_app`` IPC command."""
        resp["type"] = "ack"
        # NEW-IPC-006: same as restart_app — add explicit ``data: {}``.
        resp.setdefault("data", {})
        try:
            self._send(resp)
            self.service.quit()
        except Exception as e:
            log.error("[IPC] quit_app failed: %s", e, exc_info=True)
        return None

    def _handle_export_diagnostics(self, data, resp) -> dict | None:
        """Handle the ``export_diagnostics`` IPC command."""
        try:
            result = self.service.export_diagnostics()
            resp["type"] = "diagnostics_result"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] export_diagnostics failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_check_accessibility(self, data, resp) -> dict | None:
        """Handle the ``check_accessibility`` IPC command."""
        # PLAT-030: macOS Accessibility permission check.
        # Returns {"granted": bool, "platform": "macos"|"windows"|"linux"}.
        # On non-macOS platforms, always returns granted=True (no
        # accessibility permission required). The Electron UI uses
        # this to show a persistent warning banner on macOS when
        # the permission is missing, and to gate the onboarding
        # wizard's "Grant Accessibility" step.
        try:
            import sys as _sys
            granted = True
            if is_macos():
                try:
                    import ctypes
                    # AXIsProcessTrusted() is the official API.
                    # Returns True iff the process has Accessibility.
                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    granted = bool(app_services.AXIsProcessTrusted())
                except Exception:
                    # Fallback: osascript check
                    import subprocess as _sp
                    try:
                        result = _sp.run(
                            ["osascript", "-e",
                             'tell application "System Events" to UI elements enabled'],
                            capture_output=True, text=True, timeout=3,
                        )
                        granted = result.returncode == 0 and "true" in result.stdout.lower()
                    except Exception:
                        granted = False
            resp["type"] = "accessibility_status"
            resp["data"] = {
                "granted": granted,
                "platform": _sys.platform,
            }
        except Exception as e:
            log.error("[IPC] check_accessibility failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_set_tray_locale(self, data, resp) -> dict | None:
        """Handle the ``set_tray_locale`` IPC command."""
        # TRAY-008: Set the tray menu locale. Called by the Electron
        # UI when the user changes the UI language in Settings.
        # The tray menu is rebuilt on the next state change so the
        # new labels take effect.
        try:
            from voice_typer.server.tray import set_tray_locale, get_tray_locale
            locale = data if isinstance(data, str) else (data or {}).get("locale", "en")
            set_tray_locale(locale)
            # Force a tray menu rebuild so the new labels show immediately.
            try:
                self.app.tray.invalidate_menu_cache()
            except Exception:
                pass
            resp["type"] = "ack"
            resp["data"] = {"locale": get_tray_locale()}
        except Exception as e:
            log.error("[IPC] set_tray_locale failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_show_electron_notification(self, data, resp) -> dict | None:
        """Handle the ``show_electron_notification`` IPC command."""
        # TRAY-035: Push a notification to the Electron UI for
        # persistent/critical messages that need longer display
        # than the OS-default ~5s tray notification. The Electron
        # Notification API supports a `duration` parameter (via
        # setTimeout auto-close) and can show a toast/banner that
        # stays until dismissed.
        try:
            if not isinstance(data, dict):
                resp["type"] = "error"
                resp["data"] = {"message": "show_electron_notification requires data: object"}
                return resp
            title = str(data.get("title", "Voice Typer"))
            message = str(data.get("message", ""))
            duration_ms = int(data.get("duration_ms", 0))  # 0 = persistent
            critical = bool(data.get("critical", False))
            _push_event_now({
                "type": "electron_notification",
                "data": {
                    "title": title,
                    "message": message,
                    "duration_ms": duration_ms,
                    "critical": critical,
                },
            })
            resp["type"] = "ack"
        except Exception as e:
            log.error("[IPC] show_electron_notification failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
