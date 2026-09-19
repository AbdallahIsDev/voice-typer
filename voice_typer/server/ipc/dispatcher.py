"""Dispatcher mixin for the IPC server (split from ``ipc_server.py``)."""

from __future__ import annotations

import threading
import typing

from voice_typer.server.asr_errors import ConsentRequiredError
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.registry import _INSTANT_CONTROL_COMMANDS, _READONLY_COMMANDS
from voice_typer.server.ipc.validation import (
    CommandHandler,
    ErrorCodes,
    ResponseEnvelope,
    _error_response,
    _validate_dict_payload,
)
from voice_typer.server.log import reset_correlation_id, set_correlation_id


class DispatcherMixin:
    """Command-dispatch methods for :class:`IPCServer`."""

    def _dispatch(self, msg: dict) -> dict | None:
        """Route a command and return the response dict.

        Returns ``None`` for commands that send their response internally
        """
        # thread silently. The TCP and stdin transports pre-check this
        if not isinstance(msg, dict):
            # ErrorEnvelope contract: see validation.py
            err: ResponseEnvelope = {
                "type": "error",
                "data": {
                    "code": ErrorCodes.UNKNOWN_COMMAND,
                    "message": "message must be a JSON object",
                    "command": msg,
                },
            }
            return err

        # cooperative shutdown gate. When the app is shutting
        if getattr(self, "_cached_shutting_down", False) is True:
            return self._shutting_down_error(msg)

        # NOTE: the per-process rate limiter is NO LONGER enforced
        cmd = msg.get("type")
        data = msg.get("data")
        resp: ResponseEnvelope = {"id": msg.get("id")} if "id" in msg else {}

        # the duration of this dispatch.  Every log emitted by a handler
        _corr_token = None
        _req_id = msg.get("id") if isinstance(msg, dict) else None
        if _req_id is not None:
            _corr_token = set_correlation_id(str(_req_id))
        #  (pyrefly): ``_COMMAND_REGISTRY`` is typed ``dict[str, str]``
        cmd_key = cmd if isinstance(cmd, str) else ""
        # resolve the handler via the class-level ``_COMMAND_REGISTRY``
        handler_name = self._COMMAND_REGISTRY.get(cmd_key)
        handler: CommandHandler | None = None
        if handler_name is not None:
            _resolved = getattr(self, handler_name, None)
            if callable(_resolved):
                # the previous ``# type: ignore[assignment]`` suppression
                handler = typing.cast(CommandHandler, _resolved)
        try:
            if handler is None:
                result = self._handle_unknown_command(cmd, data, resp)
            elif cmd_key in _READONLY_COMMANDS or cmd_key in _INSTANT_CONTROL_COMMANDS:
                # read-only handlers bypass the dispatch lock —
                if getattr(self, "_cached_shutting_down", False) is True:
                    result = self._shutting_down_error(msg)
                else:
                    result = handler(data, resp)
            else:
                #  + : state-mutating handlers serialize on the
                with self._dispatch_lock:
                    if getattr(self, "_cached_shutting_down", False) is True:
                        result = self._shutting_down_error(msg)
                    else:
                        result = handler(data, resp)
        except ConsentRequiredError as exc:
            # envelope (NOT the generic ``server.internal_error`` toast)
            resp["type"] = "error"
            resp["data"] = {
                "code": "server.consent_required",
                "message": str(exc),
                "provider": getattr(exc, "provider", ""),
                "scope": getattr(exc, "scope", ""),
            }
            log.warning(
                "[IPC] consent required for %s: provider=%s scope=%s",
                cmd_key,
                getattr(exc, "provider", ""),
                getattr(exc, "scope", ""),
            )
            result = resp
        except Exception as exc:
            # Top-level catch-all so a handler bug (e.g. an uncaught
            result = _error_response(resp, "internal error", code=ErrorCodes.HANDLER_ERROR)
            log.error(
                "[IPC] handler %s raised: %s",
                cmd_key,
                exc,
                exc_info=True,
            )
        finally:
            if _corr_token is not None:
                reset_correlation_id(_corr_token)

        # client can always read `resp.data` without a defensive guard.
        if result is not None:
            result.setdefault("data", {})
            # envelope so clients using id-based request/response
            if isinstance(msg, dict) and "id" in msg and "id" not in result:
                result["id"] = msg["id"]

        return result

    def _shutting_down_error(self, msg: dict) -> ResponseEnvelope:
        """Build a structured ``server.shutting_down`` error envelope."""
        # ErrorEnvelope contract: see validation.py
        err: ResponseEnvelope = {
            "type": "error",
            "data": {
                "code": "server.shutting_down",
                "message": "server is shutting down",
            },
        }
        if isinstance(msg, dict) and "id" in msg:
            err["id"] = msg["id"]
        return err

    # the inline _COMMAND_REGISTRY dict literal previously lived

    def _handle_tray_click(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """ADR-0020 §6.5 / §16: dispatch a Tauri tray-menu click by item id."""
        validated, error = _validate_dict_payload(
            data,
            {
                "id": {"type": str, "required": True},
            },
        )
        if error:
            return error

        item_id = validated["id"]
        tray = getattr(self.app, "tray", None)
        #  align to the namespaced
        if tray is None or not hasattr(tray, "dispatch_tray_action"):
            # ErrorEnvelope contract: see validation.py
            resp["type"] = "error"
            resp["data"] = {"code": "server.unknown_tray_item", "id": item_id}
            return resp

        handled = tray.dispatch_tray_action(item_id)
        if not handled:
            # ErrorEnvelope contract: see validation.py
            resp["type"] = "error"
            resp["data"] = {"code": "server.unknown_tray_item", "id": item_id}
            return resp

        return {"type": "result", "data": {"ok": True}}

    def _handle_unknown_command(
        self, cmd: object | None, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope:
        """Handle the ``__unknown__`` IPC command."""
        # ErrorEnvelope contract: see validation.py
        resp["type"] = "error"
        # from "command handler raised" (server-side fault). The
        resp["data"] = {
            "code": "server.unknown_command",
            "message": f"Unknown command: {cmd}",
            "command": cmd,
        }
        # :class:`ErrorEnvelope` shape. The return type is
        return resp

    def _handle_shutdown(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Handle the ``shutdown`` IPC command ( / )."""
        #  (Medium): per-instance shutdown re-entrancy gate. The
        if self._shutdown_started.is_set():
            # Already shutting down. Return the same ack envelope so
            resp["type"] = "result"
            resp["data"] = {"ack": True}
            return resp
        self._shutdown_started.set()

        # Runbook §6.6 / ADR-0020 §10: log the cooperative-shutdown
        log.info("[SIDECAR-WS] shutdown received: releasing mic and exiting")

        # build the ack envelope FIRST and return it. The dispatch
        resp["type"] = "result"
        resp["data"] = {"ack": True}

        # thread so the synchronous ~95s _do_cleanup does NOT block the
        def _bg_cleanup() -> None:
            # delegate to the service layer (NOT
            try:
                self.service.quit()
            except BaseException as e:  # noqa: BLE001 —
                # The service-layer shutdown controller is best-effort;
                log.error(
                    "[IPC] shutdown: service.quit() raised: %s",
                    e,
                    exc_info=True,
                )

        # register the cleanup thread on the central
        cleanup_thread = threading.Thread(
            target=_bg_cleanup,
            name="ipc-shutdown-cleanup",
            daemon=True,
        )
        _registry = getattr(self.app, "_thread_registry", None)
        if _registry is not None:
            _registry.register(
                name="ipc-shutdown-cleanup",
                thread=cleanup_thread,
                stop_event=None,
                join_timeout=2.0,
            )
        cleanup_thread.start()
        return resp
