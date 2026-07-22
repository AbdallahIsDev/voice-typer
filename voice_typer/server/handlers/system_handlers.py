"""System IPC handler mixin: restart_app, quit_app, export_diagnostics,
check_accessibility, set_tray_locale, show_electron_notification.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import contextlib

from voice_typer.server import event_bus
from voice_typer.server.branding import APP_NAME
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _validate_dict_payload
from voice_typer.server.platform_utils import is_macos


class SystemHandlersMixin(HandlerBase):
    """Mixin: system-level IPC handlers (restart / quit / diagnostics / accessibility / ...).

    CR-20 / G4-CR-09: this mixin's ``except Exception`` catch-alls call
    :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
    no ``str(e)`` leak). Per-command validation errors (``invalid_field``,
    ``invalid_payload``) remain explicit envelopes the renderer switches on.
    The ``restart_app`` / ``quit_app`` handlers swallow their catch-all
    silently because the ack has already been sent to the client by the
    time the service raises — the error cannot be recovered from the IPC thread.

    G4-M-21 (``_handle_check_accessibility``): distinguishes
    ``subprocess.TimeoutExpired`` / ``FileNotFoundError`` from the
    generic ``granted=False`` path so the renderer can show a "click
    here to retry" CTA when the check fails for environmental reasons
    (vs. "open System Settings" CTA when the user has not granted
    permission).

    G4-M-08 (``_handle_show_electron_notification``): ``max_value_len``
    rules on ``title`` (256) and ``message`` (4096) so a misbehaving
    caller can't push a multi-MB notification body that the OS
    notification API would silently truncate or refuse to display.
    """

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
        except Exception as exc:
            # CR-20 / G4-CR-09: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "export_diagnostics")
        return resp

    def _handle_check_accessibility(self, data, resp) -> dict | None:
        """Handle the ``check_accessibility`` IPC command.

        PLAT-030: macOS Accessibility permission check.
        Returns ``{"granted": bool, "platform": "macos"|"windows"|"linux"}``.
        On non-macOS platforms, always returns granted=True (no
        accessibility permission required). The Electron UI uses
        this to show a persistent warning banner on macOS when
        the permission is missing, and to gate the onboarding
        wizard's "Grant Accessibility" step.

        G4-M-21 (session-7): the previous implementation collapsed every
        failure mode (subprocess timeout, missing ``osascript`` binary,
        ctypes ``LoadLibrary`` failure) into a generic ``granted: False``
        response — indistinguishable from "user has not granted the
        permission". The renderer had no way to tell the user "we couldn't
        run the check, click here to retry" vs. "you have not granted
        permission, click here to open System Settings". We now:

        * catch ``subprocess.TimeoutExpired`` and ``FileNotFoundError``
          distinctly (the two failure modes that occur in practice when
          macOS's ``osascript`` binary is missing or the system is
          unresponsive under load);
        * log at WARNING (not ERROR) because these are recoverable
          environmental issues, not server bugs;
        * return ``{"granted": False, "platform": "darwin",
          "reason": "check_failed"}`` so the renderer can show a
          "click here to retry" CTA instead of the "open System
          Settings" CTA.
        """
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
                            ["osascript", "-e", 'tell application "System Events" to UI elements enabled'],
                            capture_output=True,
                            text=True,
                            timeout=3,
                        )
                        granted = result.returncode == 0 and "true" in result.stdout.lower()
                    except _sp.TimeoutExpired:
                        # G4-M-21: osascript hung — environmental issue
                        # (system unresponsive), not a server bug. Log
                        # at WARNING and surface a ``check_failed`` reason
                        # so the renderer can show a retry CTA.
                        log.warning(
                            "[IPC] check_accessibility: osascript timed out after 3s — system may be unresponsive"
                        )
                        resp["type"] = "accessibility_status"
                        resp["data"] = {
                            "granted": False,
                            "platform": "darwin",
                            "reason": "check_failed",
                        }
                        return resp
                    except FileNotFoundError:
                        # G4-M-21: ``osascript`` binary missing — rare
                        # but possible on a stripped-down macOS install
                        # or a broken OS upgrade. Log at WARNING and
                        # surface ``check_failed``.
                        log.warning(
                            "[IPC] check_accessibility: osascript binary not found — cannot run accessibility check"
                        )
                        resp["type"] = "accessibility_status"
                        resp["data"] = {
                            "granted": False,
                            "platform": "darwin",
                            "reason": "check_failed",
                        }
                        return resp
            resp["type"] = "accessibility_status"
            resp["data"] = {
                "granted": granted,
                "platform": _sys.platform,
            }
        except Exception as exc:
            # CR-20 / G4-CR-09: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "check_accessibility")
        return resp

    def _handle_set_tray_locale(self, data, resp) -> dict | None:
        """Handle the ``set_tray_locale`` IPC command.

        TRAY-008 / UX-6: accepts ``locale`` (required) and an optional
        ``labels`` dict of translated tray-menu strings. When ``labels``
        is provided it is registered for that locale (merged over
        English) so the tray menu can localize into any of the 8
        renderer locales, not just the server-hard-coded en/es. The tray
        menu is rebuilt so the new labels take effect immediately.
        """
        try:
            from voice_typer.server.tray import (
                get_tray_locale,
                register_tray_labels,
                set_tray_locale,
            )

            validated, error = _validate_dict_payload(
                data,
                {
                    "locale": {"type": str, "required": False, "default": "en"},
                    "labels": {"type": dict, "required": False, "default": None},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            locale = validated["locale"]
            labels = validated["labels"]
            if labels is not None:
                register_tray_labels(locale, labels)
            set_tray_locale(locale)
            # Force a tray menu rebuild so the new labels show immediately.
            with contextlib.suppress(Exception):
                self.app.tray.invalidate_menu_cache()
            resp["type"] = "ack"
            resp["data"] = {"locale": get_tray_locale()}
        except Exception as exc:
            # CR-20 / G4-CR-09: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "set_tray_locale")
        return resp

    def _handle_set_esc_cancel_paused(self, data, resp) -> dict | None:
        """Handle the ``set_esc_cancel_paused`` IPC command.

        ARCH-ESC-001: this is now a thin wrapper around the
        ``KeyboardOwnership`` singleton. The ``paused`` flag sets the
        keyboard owner to ``"hotkey_capture"`` (paused=True) or
        ``"normal"`` (paused=False).

        The previous implementation mutated ``app._esc_cancel_paused``
        directly. That attribute is kept as a backward-compat alias
        for existing tests, but the canonical state lives in
        ``KeyboardOwnership``. The hotkey backends consult
        ``KeyboardOwnership.is_hotkey_capture_active()`` on every poll,
        so the moment this IPC lands, the next poll cycle honors it.

        The ``data`` dict should contain ``{"paused": true}`` or
        ``{"paused": false}``.
        """
        try:
            if data is None:
                data = {}
            # IPC-3: validate ``paused`` is a bool via the shared
            # ``_validate_dict_payload`` helper. ``required: False,
            # default: False`` preserves the existing
            # ``test_missing_paused_defaults_to_false`` contract
            # ({} → paused defaults to False → resume normal). The
            # strict ``bool`` type check rejects truthy non-bools
            # (e.g. ``{"paused": "true"}`` → ``invalid_field`` error)
            # that the previous ``bool((data or {}).get("paused",
            # False))`` coercion would have silently accepted as True.
            validated, error = _validate_dict_payload(
                data,
                {
                    "paused": {
                        "type": bool,
                        "required": False,
                        "default": False,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            paused = validated["paused"]
            # ARCH-ESC-001: update the canonical ownership state.
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            keyboard_ownership().set_owner(
                "hotkey_capture" if paused else "normal",
                reason="frontend hotkey capture" if paused else "capture ended",
            )
            # Backward-compat alias for tests that read app._esc_cancel_paused.
            self.app._esc_cancel_paused = paused
            log.info(
                "[IPC] ESC cancel %s (via frontend hotkey capture mode)",
                "PAUSED" if paused else "RESUMED",
            )
            resp["type"] = "ack"
            resp["data"] = {"paused": paused}
        except Exception as exc:
            # CR-20 / G4-CR-09: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "set_esc_cancel_paused")
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

        R4-F5 (IMPROVE-mode run, 2026-07-19): the per-field type
        checks + the ``duration_ms`` clamp now route through
        ``_validate_dict_payload``. The helper's ``clamp_range`` rule
        replaces the inline ``max(0, min(int(duration_ms), 24*60*60*1000))``
        coercion. Two Python-specific gotchas stay inline:

          * The ``bool`` subclass exclusion for ``duration_ms`` (a
            bool is an int in Python; without this guard, a caller
            who swaps the ``critical`` and ``duration_ms`` fields
            would have ``True`` silently coerced to ``duration_ms: 1``).
            The helper's ``isinstance(value, (int, float))`` check
            alone would NOT exclude bool — keeping the pre-check
            inline is clearer than burying the gotcha in a schema
            rule.
          * The ``None`` → default coercion for ``title``/``message``
            /``duration_ms``/``critical``. The helper's ``default``
            only fires when the field is ABSENT — a present ``None``
            fails the type check. Pre-coercing ``None`` to the
            default keeps the existing "missing or null → default"
            contract without adding a ``none_to_default`` rule.

        G4-M-08 (session-7): added ``max_value_len`` rules on
        ``title`` (256 chars) and ``message`` (4096 chars) so a
        misbehaving caller (or a renderer bug) can't push a 1 MB
        notification body that the OS notification API would
        either truncate silently or refuse to display. The caps
        match the OS notification API's practical limits on both
        macOS (``UNNotificationContent.title`` / ``body``) and
        Windows (``ToastNotification`` XML payload).
        """
        try:
            # R4-F5: pre-check the bool subclass exclusion for
            # ``duration_ms`` BEFORE invoking the helper. ``bool`` is
            # a subclass of ``int`` in Python — without this guard,
            # ``duration_ms: True`` would pass the helper's
            # ``isinstance(value, (int, float))`` check and then be
            # coerced to ``duration_ms: 1`` by ``clamp_range``, silently
            # accepting a misbehaving caller who swapped the
            # ``critical`` and ``duration_ms`` fields.
            if isinstance(data, dict) and isinstance(data.get("duration_ms"), bool):
                resp["type"] = "error"
                resp["data"] = {
                    "code": "invalid_field",
                    "field": "duration_ms",
                    "message": "'duration_ms' must be a number (milliseconds)",
                }
                return resp

            # R4-F5: pre-coerce ``None`` values to their defaults so the
            # helper's ``default`` rule (which only fires for ABSENT
            # fields) doesn't reject ``{"title": null}`` as a type
            # error. The previous inline impl had the same coercion.
            if isinstance(data, dict):
                if data.get("title") is None:
                    data = {**data, "title": APP_NAME}
                if data.get("message") is None:
                    data = {**data, "message": ""}
                if data.get("duration_ms") is None:
                    data = {**data, "duration_ms": 0}
                if data.get("critical") is None:
                    data = {**data, "critical": False}

            # R4-F5: route the dict-type + per-field type checks +
            # ``duration_ms`` clamp through ``_validate_dict_payload``.
            # The helper's ``clamp_range`` rule replaces the inline
            # ``max(0, min(int(duration_ms), 24*60*60*1000))`` coercion.
            #
            # G4-M-08 (session-7): added ``max_value_len`` rules on
            # ``title`` (256 chars) and ``message`` (4096 chars) so a
            # misbehaving caller (or a renderer bug) can't push a 1 MB
            # notification body that the OS notification API would
            # either truncate silently or refuse to display. The caps
            # match the OS notification API's practical limits on both
            # macOS (``UNNotificationContent.title`` / ``body``) and
            # Windows (``ToastNotification`` XML payload).
            validated, error = _validate_dict_payload(
                data,
                {
                    "title": {
                        "type": str,
                        "required": False,
                        "default": APP_NAME,
                        "max_value_len": 256,
                    },
                    "message": {
                        "type": str,
                        "required": False,
                        "default": "",
                        "max_value_len": 4096,
                    },
                    "duration_ms": {
                        "type": (int, float),
                        "required": False,
                        "default": 0,
                        "clamp_range": (0, 24 * 60 * 60 * 1000),
                    },
                    "critical": {"type": bool, "required": False, "default": False},
                },
            )
            if error:
                # The helper's non-dict path returns ``code:
                # "invalid_payload"`` with the ``"data must be an
                # object"`` message — different from the pre-R4-F5
                # handler-specific ``"show_electron_notification
                # requires data: object"``. The test was updated to
                # assert on ``code`` instead of the message text.
                resp["type"] = "error"
                resp["data"] = error["data"]
                return resp
            assert validated is not None  # narrowed by the error guard above
            title = validated["title"]
            message = validated["message"]
            duration_ms = int(validated["duration_ms"])
            critical = validated["critical"]

            event_bus.publish(
                {
                    # CR-8: renamed from "electron_notification" to the
                    # platform-agnostic "notification" — the Tauri Rust
                    # host no longer renames the event (it passes through
                    # unchanged), and a Rust-side backward-compat alias
                    # (see src-tauri/src/main.rs) handles old Python
                    # sidecars that still emit the legacy name during
                    # rolling upgrades. The renderer subscribes to
                    # "notification" via the generic `python-event`
                    # envelope, not by name.
                    "type": "notification",
                    "data": {
                        "title": title,
                        "message": message,
                        "duration_ms": duration_ms,
                        "critical": critical,
                    },
                }
            )
            resp["type"] = "ack"
        except Exception as exc:
            # CR-20 / G4-CR-09: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "show_electron_notification")
        return resp
