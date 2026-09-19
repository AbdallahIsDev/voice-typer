"""Config IPC handler mixin: get_config, get_defaults, set_config."""

import contextlib
import ipaddress
from typing import cast

from voice_typer.server import event_bus
from voice_typer.server.config import validate_config_update
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (  # noqa: F401
    ErrorCodes,
    LegacyErrorCodes,
    ResponseEnvelope,
    _error_response,
)

# ``_config_mutation_lock`` case. The handler runs on every ``set_config``
_CONFIG_LOCK_MISSING_WARNED: bool = False


class ConfigHandlersMixin(HandlerBase):
    """Mixin: config-related IPC handlers (get_config / get_defaults / set_config)."""

    def _handle_get_config(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_config`` IPC command."""
        resp["type"] = "config"
        # SEC-003: service.get_config() returns a sanitized view — secret
        resp["data"] = self.service.get_config()
        return resp

    def _handle_get_defaults(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_defaults`` IPC command."""
        # Defaults from the Python Config dataclass (renderer Reset path).
        try:
            resp["type"] = "defaults"
            resp["data"] = self.service.get_defaults()
        except Exception as exc:
            self._respond_with_error(resp, exc, "get_defaults")
        return resp

    def _handle_set_config(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``set_config`` IPC command."""
        try:
            if not isinstance(data, dict):
                log.warning("[IPC] set_config rejected: data is %s, not dict", type(data).__name__)
                return _error_response(
                    resp,
                    "set_config requires data: object",
                    code="invalid_payload",
                )
            # SEC-002: validate against IPC_CONFIG_ALLOWLIST before touching
            validated, errors = validate_config_update(data)
            if errors:
                log.warning("[IPC] set_config rejected: %s", "; ".join(errors))
                # Full error list in data.errors; data.message stays errors[0]
                resp["type"] = "error"
                resp["data"] = {
                    "code": "invalid_field",
                    "message": errors[0],
                    "errors": list(errors),
                }
                return resp
            # Bubble edge change invalidates drag-persisted coords unless the
            if "bubble_position" in validated:
                validated.setdefault("bubble_x", None)
                validated.setdefault("bubble_y", None)
            accepted_keys = list(validated.keys())
            rejected_keys = [k for k in data if k not in validated]
            # RACE-011: hold _config_mutation_lock across change_model +
            model_errors: list[dict] = []
            applied: list[str] = []
            # change_model returns immediately (background load); model_loading
            model_loading: list[dict] = []
            failed_keys: set[str] = set()
            app_ref = self.app
            config_lock = getattr(app_ref, "_config_mutation_lock", None)
            global _CONFIG_LOCK_MISSING_WARNED
            if config_lock is None and not _CONFIG_LOCK_MISSING_WARNED:
                _CONFIG_LOCK_MISSING_WARNED = True
                log.warning(
                    "[IPC] set_config: app has no _config_mutation_lock, "
                    "running lock-free; concurrent set_config / change_model "
                    "may interleave (this warning fires once per process)"
                )
            with contextlib.ExitStack() as stack:
                if config_lock is not None:
                    stack.enter_context(config_lock)
                if "model_size" in validated and validated["model_size"] != getattr(
                    self.app.config, "model_size", None
                ):
                    # capture the OLD values BEFORE the call —
                    old_model_size = getattr(self.app.config, "model_size", None)
                    old_backend = getattr(self.app.config, "asr_backend", None)
                    try:
                        self.service.change_model(validated["model_size"])
                        applied.append("model_size")
                        # surface the "loading" status so the
                        model_loading.append(
                            {
                                "field": "model_size",
                                "status": "loading",
                                "previous": {
                                    "backend": old_backend,
                                    "model_size": old_model_size,
                                },
                                "pending": {"model_size": validated["model_size"]},
                            }
                        )
                    except Exception as e:
                        # partial-success envelope. : include the
                        log.error(
                            "[IPC] change_model(model_size=%s) failed: %s",
                            validated["model_size"],
                            e,
                            exc_info=True,
                        )
                        model_errors.append(
                            {
                                "code": LegacyErrorCodes.MODEL_SWITCH_FAILED,
                                "field": "model_size",
                                "value": validated["model_size"],
                            }
                        )
                        # drop the failed key from the persist set
                        failed_keys.add("model_size")
                if "asr_backend" in validated and validated["asr_backend"] != getattr(
                    self.app.config, "asr_backend", None
                ):
                    # capture the OLD backend BEFORE the call
                    old_backend = getattr(self.app.config, "asr_backend", None)
                    old_model_size = getattr(self.app.config, "model_size", None)
                    try:
                        self.service.set_active_backend(validated["asr_backend"])
                        applied.append("asr_backend")
                        # surface the "loading" status (see above).
                        model_loading.append(
                            {
                                "field": "asr_backend",
                                "status": "loading",
                                "previous": {
                                    "backend": old_backend,
                                    "model_size": old_model_size,
                                },
                                "pending": {"backend": validated["asr_backend"]},
                            }
                        )
                    except Exception as e:
                        # same partial-success pattern as above.
                        log.error(
                            "[IPC] set_active_backend(asr_backend=%s) failed: %s",
                            validated["asr_backend"],
                            e,
                            exc_info=True,
                        )
                        model_errors.append(
                            {
                                "code": LegacyErrorCodes.MODEL_SWITCH_FAILED,
                                "field": "asr_backend",
                                "value": validated["asr_backend"],
                            }
                        )
                        # same rationale as the model_size branch.
                        failed_keys.add("asr_backend")
                # RACE-011 + AUDIO-PRESET-SAVE-FIX + :
                to_persist = {k: v for k, v in validated.items() if k not in failed_keys}
                self.service.apply_config(to_persist)
                # ``trusted_extra_hosts`` must re-apply the allowlist
                if "trusted_extra_hosts" in to_persist:
                    try:
                        from voice_typer.server._secrets import extend_url_allowlist

                        extend_url_allowlist(
                            cast(list[str], to_persist["trusted_extra_hosts"]),
                            caller="set_config.trusted_extra_hosts",
                        )
                    except Exception:
                        log.debug("[IPC] set_config trusted_extra_hosts allowlist re-apply failed", exc_info=True)
                # build the ``applied`` echo list from
                applied.extend(k for k in to_persist if k not in applied)
            # also invalidate the tray models submenu's
            try:
                from voice_typer.server.tray_models import (
                    invalidate_model_availability_cache,
                )

                invalidate_model_availability_cache()
            except Exception:
                log.debug(
                    "[IPC] invalidate_model_availability_cache failed",
                    exc_info=True,
                )

            # Push a config_changed event so the renderer (App.tsx)
            try:
                event_bus.publish(
                    {
                        "type": "config_changed",
                        "data": to_persist,
                    }
                )
            except Exception:
                log.debug("[IPC] config_changed push failed", exc_info=True)

            # ``bubble_position`` is included because the handler above
            if any(
                k in validated
                for k in (
                    "bubble_behavior",
                    "bubble_click_to_toggle",
                    "bubble_mic_button",
                    "bubble_position",
                    "text_size",
                )
            ):
                try:
                    # (): delegate to the public
                    self.app.push_bubble_config(self.app.config)
                except Exception:
                    log.debug("[IPC] bubble_config push failed", exc_info=True)

            resp["type"] = "ack"
            # echo accepted + rejected keys so the
            response_data: dict = {}
            if rejected_keys:
                response_data["accepted"] = accepted_keys
                response_data["rejected"] = rejected_keys
            if model_errors:
                response_data["status"] = "partial"
                response_data["model_errors"] = model_errors
                response_data["applied"] = applied
            if model_loading:
                # ``asr_backend_ready`` event (published by the
                response_data["model_loading"] = model_loading
            if response_data:
                resp["data"] = response_data
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "set_config")
        return resp

    def _handle_add_trusted_endpoint(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``add_trusted_endpoint`` IPC command."""
        try:
            if not isinstance(data, dict) or not isinstance(data.get("host"), str):
                log.warning("[IPC] add_trusted_endpoint rejected: data.host must be a string")
                return _error_response(
                    resp,
                    "add_trusted_endpoint requires data.host: string",
                    code="invalid_payload",
                )
            raw_host = data["host"].strip()
            # Reject scheme/path/whitespace on the RAW value first —
            if not raw_host or "://" in raw_host or "/" in raw_host or " " in raw_host:
                log.warning("[IPC] add_trusted_endpoint rejected: invalid host %r", raw_host)
                return _error_response(
                    resp,
                    f"invalid host {raw_host!r}, expected a bare hostname like 'my-vllm.lan'",
                    code="invalid_field",
                )
            # ``_normalize_host`` in ``security.url_allowlist`` and the
            if raw_host.startswith("["):
                # Bracketed IPv6 (with optional ``:port``): ``[fc00::1]``
                closing = raw_host.find("]")
                if closing <= 0:
                    return _error_response(
                        resp,
                        f"invalid host {raw_host!r}, is not a valid IPv6 literal",
                        code="invalid_field",
                    )
                inner = raw_host[1:closing]
                try:
                    ipaddress.ip_address(inner)
                except ValueError:
                    return _error_response(
                        resp,
                        f"invalid host {raw_host!r}, is not a valid IPv6 literal",
                        code="invalid_field",
                    )
                host = inner
            elif raw_host.count(":") > 1:
                # Bare IPv6 literal (``fc00::1``) OR a multi-colon
                try:
                    ipaddress.ip_address(raw_host)
                except ValueError:
                    return _error_response(
                        resp,
                        f"invalid host {raw_host!r}, is not a valid IPv6 literal",
                        code="invalid_field",
                    )
                host = raw_host
            else:
                # Generic hostname / IPv4 / hostname:port, strip the
                host = raw_host.split(":")[0]
            host = host.strip().lower()
            if not host:
                log.warning("[IPC] add_trusted_endpoint rejected: invalid host %r", raw_host)
                return _error_response(
                    resp,
                    f"invalid host {raw_host!r}, expected a bare hostname like 'my-vllm.lan'",
                    code="invalid_field",
                )
            if not all(c.isalnum() or c in "-._" or c == ":" for c in host):
                return _error_response(
                    resp,
                    f"invalid host {raw_host!r}, contains invalid characters",
                    code="invalid_field",
                )

            from voice_typer.server._secrets import extend_url_allowlist

            # Apply to the runtime allowlist first, then persist.
            extend_url_allowlist([host], caller="add_trusted_endpoint")

            # Persist to config.json under trusted_extra_hosts (idempotent).
            current = list(getattr(self.app.config, "trusted_extra_hosts", []) or [])
            if host not in current:
                current.append(host)
                self.service.apply_config({"trusted_extra_hosts": current})

            resp["type"] = "ack"
            resp["data"] = {"host": host}
            return resp
        except Exception as exc:
            self._respond_with_error(resp, exc, "add_trusted_endpoint")
            return resp
