"""System IPC handlers: restart/quit, accessibility, tray locale, notification."""

import contextlib
import subprocess
import unicodedata
from typing import cast

from voice_typer.server import event_bus
from voice_typer.server.branding import APP_NAME
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (  # noqa: F401
    ErrorCodes,
    LegacyErrorCodes,
    ResponseEnvelope,
    Schema,
    _validate_dict_payload,
)
from voice_typer.server.platform_utils import is_linux, is_macos


def _has_control_chars(value) -> bool:
    """True if *value* has Unicode Cc/Cf control chars (tab allowed)."""
    if not isinstance(value, str):
        return False
    return any(unicodedata.category(ch) in ("Cc", "Cf") and ch != "\t" for ch in value)


def _enumerate_polkit_actions() -> list[str]:
    """Enumerate polkit actions for the app namespace via ``pkaction``.

    Tolerant of missing/timeout/non-zero pkaction (warn + empty list).
    """

    try:
        result = subprocess.run(
            ["pkaction"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("[IPC] reset_linux_permissions: pkaction unavailable (%s)", exc)
        return []
    if result.returncode != 0:
        log.warning(
            "[IPC] reset_linux_permissions: pkaction exited %d",
            result.returncode,
        )
        return []
    return sorted({line.strip() for line in result.stdout.splitlines() if "voicetyper" in line.strip().lower()})


def _polkit_check_authorization(action_id: str) -> str:
    """Query the current authorization state of *action_id* via ``pkcheck``."""

    try:
        result = subprocess.run(
            ["pkcheck", "--action-id", action_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning(
            "[IPC] reset_linux_permissions: pkcheck unavailable for %s (%s)",
            action_id,
            exc,
        )
        return "check_error"
    if result.returncode == 0:
        return "authorized"
    if result.returncode == 1:
        return "not_authorized"
    return "check_error"


def _reset_polkit_authorization() -> tuple[str | None, bool, str | None]:
    """Clear polkitd's cached authorization decisions by restarting it.

    Returns ``(command, ok, error)``: the exact command that succeeded
    """

    candidates = [
        ["pkexec", "systemctl", "restart", "polkit"],
        ["pkexec", "systemctl", "restart", "polkitd"],
        ["pkexec", "service", "polkit", "restart"],
    ]
    last_error: str | None = None
    for cmd in candidates:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_error = f"pkexec failed: {exc}"
            continue
        if result.returncode == 0:
            return " ".join(cmd), True, None
        if result.returncode == 126:
            # pkexec: the user dismissed the polkit authentication dialog.
            last_error = "pkexec: authentication dismissed"
        elif result.returncode == 127:
            # pkexec: not authorized / no authentication agent, or an
            last_error = "pkexec: not authorized (no authentication agent?)"
        else:
            last_error = result.stderr.strip() or (f"polkit daemon restart exited {result.returncode}")
    return None, False, last_error or "polkit daemon restart failed"


class SystemHandlersMixin(HandlerBase):
    """Mixin: system-level IPC handlers (restart / quit / accessibility / ...)."""

    def _handle_restart_app(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``restart_app`` IPC command."""
        resp["type"] = "ack"
        # ``resp.setdefault("data", {})`` at the end of _dispatch
        resp.setdefault("data", {})
        try:
            self._send(resp)
            self.service.restart()
        except Exception as e:
            log.error("[IPC] restart_app failed: %s", e, exc_info=True)
            # The ack was already sent; can't recover from here, but
            self._publish_service_failure("restart_failed", str(e))
        return None

    def _handle_quit_app(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``quit_app`` IPC command."""
        resp["type"] = "ack"
        # same as restart_app. Add explicit ``data: {}``.
        resp.setdefault("data", {})
        try:
            self._send(resp)
            self.service.quit()
        except Exception as e:
            log.error("[IPC] quit_app failed: %s", e, exc_info=True)
            self._publish_service_failure("quit_failed", str(e))
        return None

    def _publish_service_failure(self, kind: str, message: str) -> None:
        """Push a follow-up ``error`` event for a service-call failure."""
        try:
            event_bus.publish(
                {
                    "type": "error",
                    "data": {
                        "kind": kind,
                        "message": message,
                    },
                }
            )
        except Exception:
            log.debug(
                "[IPC] failed to publish service-failure event (kind=%s)",
                kind,
                exc_info=True,
            )

    def _handle_check_accessibility(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``check_accessibility`` IPC command.

        Returns ``{"granted": bool, "platform": "macos"|"windows"|"linux"}``.
        """

        def body(d: dict) -> dict:
            import sys as _sys

            granted = True
            # Canonical platform string. ``_sys.platform`` is
            platform_name = "macos" if is_macos() else _sys.platform
            if is_macos():
                try:
                    import ctypes

                    # AXIsProcessTrusted() is the official API.
                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    granted = bool(app_services.AXIsProcessTrusted())
                except Exception as exc:
                    log.warning(
                        "[IPC] check_accessibility: AXIsProcessTrusted ctypes load "
                        "failed (%s); treating as not-granted (check_failed)",
                        exc,
                    )
                    return {
                        "type": "accessibility_status",
                        "data": {
                            "granted": False,
                            "platform": "macos",
                            "reason": "check_failed",
                        },
                    }
            status_data: dict = {
                "granted": granted,
                "platform": platform_name,
            }
            if is_macos() and not granted:
                from voice_typer.server.server_platform.macos_bundle_id import (
                    resolve_host_bundle_id,
                )

                bundle_id = resolve_host_bundle_id()
                if bundle_id:
                    # (mirrors the reset handler's convention).
                    from voice_typer.server.server_platform.macos_bundle_id import (
                        tccutil_reset_command_str,
                    )

                    status_data["suggest_reset"] = True
                    status_data["reset_command"] = tccutil_reset_command_str("Accessibility", bundle_id)
                else:
                    status_data["suggest_reset"] = False
            return {"type": "accessibility_status", "data": status_data}

        return self._wrap(
            cmd_name="check_accessibility",
            resp_type="accessibility_status",
            data=data,
            resp=resp,
            body=body,
            schema={},
            pre_coerce=False,
        )

    def _handle_reset_macos_accessibility(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``reset_macos_accessibility`` IPC command."""

        def body(d: dict) -> dict:
            if not is_macos():
                return {
                    "type": "ack",
                    "data": {
                        "ok": False,
                        "command": None,
                        "error": "unsupported_platform",
                    },
                }

            from voice_typer.server.permissions import _open_macos_accessibility_settings
            from voice_typer.server.server_platform.macos_bundle_id import (
                resolve_host_bundle_id,
                tccutil_reset_command,
                tccutil_reset_command_str,
            )

            bundle_id = resolve_host_bundle_id()
            if not bundle_id:
                return {
                    "type": "ack",
                    "data": {
                        "ok": False,
                        "command": None,
                        "error": "bundle_id_unresolved",
                    },
                }

            # TCC-002: both forms come from the single construction point
            command = tccutil_reset_command_str("Accessibility", bundle_id)
            try:
                result = subprocess.run(
                    tccutil_reset_command("Accessibility", bundle_id),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                ok = result.returncode == 0
                tcc_error = None if ok else (result.stderr.strip() or "tccutil failed")
            except (subprocess.TimeoutExpired, OSError) as exc:
                ok = False
                tcc_error = f"tccutil failed: {exc}"

            # Re-open System Settings → Privacy & Security →
            _open_macos_accessibility_settings()

            return {
                "type": "ack",
                "data": {"ok": ok, "command": command, "error": tcc_error},
            }

        return self._wrap(
            cmd_name="reset_macos_accessibility",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={},
            pre_coerce=False,
        )

    def _handle_reset_linux_permissions(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``reset_linux_permissions`` IPC command."""

        def body(d: dict) -> dict:
            if not is_linux():
                return {
                    "type": "ack",
                    "data": {
                        "ok": False,
                        "command": None,
                        "error": "unsupported_platform",
                        "actions": [],
                        "checks": {},
                    },
                }

            actions = _enumerate_polkit_actions()
            command, ok, error_str = _reset_polkit_authorization()
            checks: dict[str, str] = {}
            if ok:
                for action_id in actions:
                    checks[action_id] = _polkit_check_authorization(action_id)

            return {
                "type": "ack",
                "data": {
                    "ok": ok,
                    "command": command,
                    "error": error_str,
                    "actions": actions,
                    "checks": checks,
                },
            }

        return self._wrap(
            cmd_name="reset_linux_permissions",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={},
            pre_coerce=False,
        )

    def _handle_set_tray_locale(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``set_tray_locale`` IPC command."""

        tray_locale_schema: Schema = {
            "locale": {
                "type": str,
                "required": False,
                "default": "en",
                # cap locale length (POSIX locale names
                "max_value_len": 64,
                # whole-payload DoS cap. The
                "max_payload_bytes": 64 * 1024,
            },
            "labels": {
                "type": dict,
                "required": False,
                "default": None,
            },
        }

        def body(d: dict) -> dict:
            from voice_typer.server.tray import (
                get_tray_locale,
                register_tray_labels,
                set_tray_locale,
            )

            # ``_wrap`` already schema-validated ``locale`` / ``labels``.
            locale = cast(str, d["locale"])
            labels = cast(dict[str, str] | None, d["labels"])
            # validate label dict contents. Keys must be
            if labels is not None:
                for k, v in labels.items():
                    if not isinstance(k, str) or len(k) > 64:
                        return self._error_response(
                            resp,
                            "label keys must be strings of <=64 chars",
                            code=ErrorCodes.INVALID_FIELD,
                            field="labels",
                        )
                    if not isinstance(v, str) or len(v) > 1024:
                        return self._error_response(
                            resp,
                            "label values must be strings of <=1024 chars",
                            code=ErrorCodes.INVALID_FIELD,
                            field="labels",
                        )
                register_tray_labels(locale, labels)
            set_tray_locale(locale)
            # an error envelope AFTER the tray labels were already
            with contextlib.suppress(Exception):
                from voice_typer.server import i18n as _server_i18n

                if labels:
                    _server_i18n.merge_labels(locale, labels)
                _server_i18n.set_locale(locale)
            # Force a tray menu rebuild so the new labels show immediately.
            with contextlib.suppress(Exception):
                self.app.tray.invalidate_menu_cache()
            return {"type": "ack", "data": {"locale": get_tray_locale()}}

        return self._wrap(
            cmd_name="set_tray_locale",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema=tray_locale_schema,
            pre_coerce=False,
        )

    def _handle_set_esc_cancel_paused(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``set_esc_cancel_paused`` IPC command."""

        def body(d: dict) -> dict:
            payload = {} if d is None else d
            # validate ``paused`` is a bool via the shared
            validated, error = _validate_dict_payload(
                payload,
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
            paused = validated["paused"]
            # update the canonical ownership state.
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
            return {"type": "ack", "data": {"paused": paused}}

        return self._wrap(
            cmd_name="set_esc_cancel_paused",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            pre_coerce=False,
        )

    def _handle_show_notification(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """NOT registered in ``_COMMAND_REGISTRY`` / renderer allowlist.
        SEC-VALIDATE-001 (this review): explicit per-field input
        """

        def body(d: dict) -> dict:
            # pre-check the bool subclass exclusion for
            if isinstance(d, dict) and isinstance(d.get("duration_ms"), bool):
                return self._error_response(
                    resp,
                    "'duration_ms' must be a number (milliseconds)",
                    code=ErrorCodes.INVALID_FIELD,
                    field="duration_ms",
                )

            # pre-coerce ``None`` values to their defaults so the
            payload = d
            if isinstance(payload, dict):
                if payload.get("title") is None:
                    payload = {**payload, "title": APP_NAME}
                if payload.get("message") is None:
                    payload = {**payload, "message": ""}
                if payload.get("duration_ms") is None:
                    payload = {**payload, "duration_ms": 0}
                if payload.get("critical") is None:
                    payload = {**payload, "critical": False}

            # either truncate silently or refuse to display. The caps
            validated, error = _validate_dict_payload(
                payload,
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
                    "click_path": {
                        "type": str,
                        "required": False,
                        "default": "",
                        "max_value_len": 256,
                    },
                    "click_consent_field": {
                        "type": str,
                        "required": False,
                        "default": "",
                        "max_value_len": 128,
                    },
                },
            )
            if error:
                # handler-specific ``"show_notification
                return error
            title = validated["title"]
            message = validated["message"]
            duration_ms = int(cast(int | str, validated["duration_ms"]))
            critical = validated["critical"]
            click_path = validated["click_path"]
            click_consent_field = validated["click_consent_field"]

            # ANSI escapes (``\x1b[31m``), terminal bell (``\x07``),
            for fname in ("title", "message"):
                if _has_control_chars(validated.get(fname, "")):
                    return self._error_response(
                        resp,
                        f"'{fname}' contains a control character",
                        code=ErrorCodes.INVALID_FIELD,
                        field=fname,
                    )

            event_bus.publish(
                {
                    # envelope, not by name.
                    "type": "notification",
                    "data": {
                        "title": title,
                        "message": message,
                        "duration_ms": duration_ms,
                        "critical": critical,
                        **({"click_path": click_path} if click_path else {}),
                        **({"click_consent_field": click_consent_field} if click_consent_field else {}),
                    },
                }
            )
            return {"type": "ack"}

        return self._wrap(
            cmd_name="show_notification",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            pre_coerce=False,
        )
