"""Config IPC handler mixin: get_config, get_defaults, set_config.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import logging

from voice_typer.server import event_bus
from voice_typer.server.config import validate_config_update
from voice_typer.server.handlers._base import HandlerMixinBase
from voice_typer.server.ipc.validation import _error_response

# Local logger (not imported from ipc_server) to avoid a circular
# import: ipc.server imports this mixin back, so importing ipc_server
# here would create a cycle.
log = logging.getLogger("voice_typer.server.ipc_server")


class ConfigHandlersMixin(HandlerMixinBase):
    """Mixin: config-related IPC handlers (get_config / get_defaults / set_config)."""

    def _handle_get_config(self, data, resp) -> dict | None:
        """Handle the ``get_config`` IPC command."""
        resp["type"] = "config"
        # SEC-003: previously this returned config.__dict__.copy()
        # which exposed every *_api_key field in cleartext over the
        # loopback TCP socket.  Any local process could netcat the
        # IPC port and exfiltrate OpenAI/Groq/Deepgram/LLM keys.
        # We now return a sanitized view where secret fields are
        # replaced with a presence indicator ("" if unset,
        # "<redacted>" if set) so the renderer can show "key
        # configured" without ever receiving the key value.
        resp["data"] = self.service.get_config()
        return resp

    def _handle_get_defaults(self, data, resp) -> dict | None:
        """Handle the ``get_defaults`` IPC command."""
        # UX-018: return the default Config() values so the
        # renderer's "Reset to Defaults" button doesn't have to
        # hardcode 22+ field defaults (which silently drift from
        # the Python Config dataclass).  The renderer calls this
        # once, then sends the result via set_config.
        try:
            resp["type"] = "defaults"
            resp["data"] = self.service.get_defaults()
        except Exception as e:
            log.error("[IPC] get_defaults failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp

    def _handle_set_config(self, data, resp) -> dict | None:
        """Handle the ``set_config`` IPC command."""
        try:
            # NEW-IPC-005: reject non-dict data with an explicit error
            # instead of silently no-oping. Previously, if data was a
            # list/string/None, the isinstance guard skipped all
            # setattr + side-effect blocks but still returned
            # {type: "ack"} success — the worst IPC failure mode.
            if not isinstance(data, dict):
                resp["type"] = "error"
                resp["data"] = {"message": "set_config requires data: object"}
                log.warning("[IPC] set_config rejected: data is %s, not dict", type(data).__name__)
                return resp
            # SEC-002: validate the caller payload against the
            # explicit IPC allowlist BEFORE touching the Config
            # object.  Unknown keys are silently dropped (debug-
            # logged); type/range/enum violations abort the
            # entire payload atomically and return an error so
            # the renderer can surface the rejection.
            validated, errors = validate_config_update(data)
            if errors:
                log.warning("[IPC] set_config rejected: %s", "; ".join(errors))
                resp["type"] = "error"
                resp["data"] = {"message": errors[0]}
                return resp
            # NEW-IPC-015: echo accepted + rejected keys so the
            # renderer can show the user which fields were applied
            # and which were silently dropped (unknown keys).
            accepted_keys = list(validated.keys())
            rejected_keys = [k for k in data if k not in validated]
            # NEW-IPC-016: when model_size or asr_backend changes,
            # apply it to the active engine so the next dictation
            # uses the new model without requiring a restart.
            # ADR 0008 §3.1: route through the service layer rather
            # than calling ``self.app.change_model()`` /
            # ``self.app.models.set_active_backend()`` directly.
            if "model_size" in validated and validated["model_size"] != getattr(self.app.config, "model_size", None):
                try:
                    self.service.change_model(validated["model_size"])
                except Exception as e:
                    # CR-76: include the operation input in the log so
                    # operators can see which model_size failed without
                    # having to cross-reference the IPC payload.
                    log.warning("[IPC] change_model(model_size=%s) failed: %s", validated["model_size"], e)
            if "asr_backend" in validated and validated["asr_backend"] != getattr(self.app.config, "asr_backend", None):
                try:
                    self.service.set_active_backend(validated["asr_backend"])
                except Exception as e:
                    # CR-76: include the operation input in the log.
                    log.warning("[IPC] set_active_backend(asr_backend=%s) failed: %s", validated["asr_backend"], e)
            # Apply only allowlisted, validated values.
            # RACE-011 + AUDIO-PRESET-SAVE-FIX + ARCH-043:
            # ``service.apply_config`` holds the app's config-mutation
            # lock for the full setattr + side-effects + save sequence
            # so concurrent set_config IPC calls can't interleave, and
            # so side-effect mutations (e.g. noise_filter_* toggles
            # from the audio preset) are persisted to disk.  It then
            # invalidates the tray menu cache so the next menu build
            # picks up the new config values.
            self.service.apply_config(validated)
            # ARCH-007: also invalidate the tray models submenu's
            # HF download cache so the next right-click reflects the
            # current model download/active state immediately (rather
            # than waiting for the 5-second TTL).
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
            # can update UI-local state (font-scale, theme, etc.)
            # immediately instead of waiting for the next mount.
            # The event carries the validated updates so the
            # renderer doesn't need an extra get_config round-trip.
            try:
                event_bus.publish(
                    {
                        "type": "config_changed",
                        "data": validated,
                    }
                )
            except Exception:
                log.debug("[IPC] config_changed push failed", exc_info=True)

            # UX-10: if the bubble-relevant settings changed (bubble_behavior
            # / bubble_click_to_toggle / bubble_mic_button), push a dedicated
            # `bubble_config` event so the sandboxed bubble renderer (which
            # has no get_config) learns whether to show its mic button.
            if any(
                k in validated
                for k in (
                    "bubble_behavior",
                    "bubble_click_to_toggle",
                    "bubble_mic_button",
                )
            ):
                try:
                    on_config = getattr(self.app, "_waveform_bubble", None)
                    if on_config is not None and on_config.on_config is not None:
                        on_config.on_config(self.app.config)
                except Exception:
                    log.debug("[IPC] bubble_config push failed", exc_info=True)

            resp["type"] = "ack"
            # NEW-IPC-015: echo accepted + rejected keys so the
            # renderer can show the user which fields were applied
            # and which were silently dropped (unknown keys).
            # Only include data when there are rejected keys, so
            # the common case (all keys accepted) returns a plain
            # {type: "ack"} matching existing callers.
            if rejected_keys:
                resp["data"] = {"accepted": accepted_keys, "rejected": rejected_keys}
        except Exception as e:
            log.error("[IPC] set_config failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp
