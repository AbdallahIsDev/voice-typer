"""System IPC handler mixin: restart_app, quit_app, export_diagnostics,
check_accessibility, set_tray_locale, show_electron_notification.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.ipc_server import log, _push_event_now
from voice_typer.server.platform_utils import is_macos


class SystemHandlersMixin:
    """Mixin: system-level IPC handlers (restart / quit / diagnostics / accessibility / ...)."""

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

    def _handle_set_esc_cancel_paused(self, data, resp) -> dict | None:
        """Handle the ``set_esc_cancel_paused`` IPC command.

        ESC-FIX-001: pause/resume the global ESC cancel hotkey so the
        frontend (HotkeyPicker in hotkey capture mode) can temporarily
        disable it, preventing the backend from processing Escape while
        the UI is capturing a custom hotkey.

        The ``data`` dict should contain ``{"paused": true}`` or
        ``{"paused": false}``.
        """
        try:
            paused = bool((data or {}).get("paused", False))
            self.app._esc_cancel_paused = paused
            log.info(
                "[IPC] ESC cancel %s (via frontend hotkey capture mode)",
                "PAUSED" if paused else "RESUMED",
            )
            resp["type"] = "ack"
            resp["data"] = {"paused": paused}
        except Exception as e:
            log.error("[IPC] set_esc_cancel_paused failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_show_electron_notification(self, data, resp) -> dict | None:
        """Handle the ``show_electron_notification`` IPC command.

        TRAY-035: Push a notification to the Electron UI for
        persistent/critical messages that need longer display
        than the OS-default ~5s tray notification. The Electron
        Notification API supports a ``duration`` parameter (via
        setTimeout auto-close) and can show a toast/banner that
        stays until dismissed.

        SEC-VALIDATE-001 (this review): explicit per-field input
        validation.  Previously the handler coerced every field with
        ``str()`` / ``int()`` / ``bool()`` and relied on the
        surrounding try/except to convert ``ValueError`` (from
        ``int("not-a-number")``) into an "error" response.  That
        produced two failure modes:

          1. The error message echoed the raw Python exception text
             back to the client (``"invalid literal for int() with
             base 10: 'abc'"``) — a minor information leak and a
             poor UX.
          2. ``bool("false")`` returned ``True`` because any
             non-empty string is truthy — a JSON ``"critical":
             "false"`` from a misbehaving caller would silently
             escalate the notification to critical.

        We now validate each field explicitly and return a structured
        ``code: "invalid_field"`` error before pushing the event, so
        the client gets a stable contract and no Python internals
        leak.  ``duration_ms`` is also clamped to ``[0, 24h]`` so a
        caller can't schedule a ``setTimeout`` that effectively never
        fires (which would leave a "persistent" notification that the
        user can't dismiss via the auto-close path).
        """
        try:
            if not isinstance(data, dict):
                resp["type"] = "error"
                resp["data"] = {"message": "show_electron_notification requires data: object"}
                return resp

            title = data.get("title", APP_NAME)
            if title is None:
                title = APP_NAME
            if not isinstance(title, str):
                resp["type"] = "error"
                resp["data"] = {
                    "code": "invalid_field",
                    "field": "title",
                    "message": "'title' must be a string",
                }
                return resp

            message = data.get("message", "")
            if message is None:
                message = ""
            if not isinstance(message, str):
                resp["type"] = "error"
                resp["data"] = {
                    "code": "invalid_field",
                    "field": "message",
                    "message": "'message' must be a string",
                }
                return resp

            duration_ms = data.get("duration_ms", 0)
            if duration_ms is None:
                duration_ms = 0
            # ``bool`` is a subclass of ``int`` in Python — exclude it
            # explicitly so ``critical: true`` doesn't sneak through as
            # ``duration_ms: 1`` if a caller swaps the fields.
            if not isinstance(duration_ms, (int, float)) or isinstance(duration_ms, bool):
                resp["type"] = "error"
                resp["data"] = {
                    "code": "invalid_field",
                    "field": "duration_ms",
                    "message": "'duration_ms' must be a number (milliseconds)",
                }
                return resp
            # Clamp to [0, 24h] — see method docstring.
            duration_ms = max(0, min(int(duration_ms), 24 * 60 * 60 * 1000))

            critical = data.get("critical", False)
            if critical is None:
                critical = False
            if not isinstance(critical, bool):
                resp["type"] = "error"
                resp["data"] = {
                    "code": "invalid_field",
                    "field": "critical",
                    "message": "'critical' must be a boolean",
                }
                return resp

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
