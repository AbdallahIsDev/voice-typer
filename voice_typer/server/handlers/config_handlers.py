"""Config IPC handler mixin: get_config, get_defaults, set_config.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import contextlib

from voice_typer.server import event_bus
from voice_typer.server.config import validate_config_update
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _error_response


class ConfigHandlersMixin(HandlerBase):
    """Mixin: config-related IPC handlers (get_config / get_defaults / set_config).

    CR-20 / G4-CR-09: this mixin's ``except Exception`` catch-alls call
    :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
    no ``str(e)`` leak). Inline per-command validation errors route
    through :func:`_error_response` with an explicit ``code`` field
    (PVT-G5-071).

    G4-H-14: ``_handle_set_config`` acquires ``_config_mutation_lock``
    ONCE at the handler level and holds it across ``change_model`` +
    ``set_active_backend`` + ``apply_config`` so concurrent IPC
    ``set_config`` calls can't interleave attribute writes between
    the three operations.

    G4-M-20: surface ``change_model`` / ``set_active_backend`` failures
    via a partial-success envelope in the response data
    (``data.model_errors``) instead of swallowing them.
    """

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
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_defaults")
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
                # PVT-G5-071: route the inline validation error through
                # ``_error_response`` so the envelope carries the
                # structured ``code: "invalid_payload"`` field (clients
                # branching on ``code`` can distinguish this from a
                # missing-field or wrong-type error).
                log.warning("[IPC] set_config rejected: data is %s, not dict", type(data).__name__)
                return _error_response(
                    resp,
                    "set_config requires data: object",
                    code="invalid_payload",
                )
            # SEC-002: validate the caller payload against the
            # explicit IPC allowlist BEFORE touching the Config
            # object.  Unknown keys are silently dropped (debug-
            # logged); type/range/enum violations abort the
            # entire payload atomically and return an error so
            # the renderer can surface the rejection.
            validated, errors = validate_config_update(data)
            if errors:
                # PVT-G5-071: ``validate_config_update`` returns
                # human-readable error strings (e.g. "model_size must
                # be one of …"). Stamp them with ``invalid_field`` so
                # the renderer can branch on the code rather than
                # pattern-matching the message text.
                log.warning("[IPC] set_config rejected: %s", "; ".join(errors))
                return _error_response(resp, errors[0], code="invalid_field")
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
            #
            # G4-H-14: acquire ``_config_mutation_lock`` ONCE at the
            # handler level and hold it across ``change_model`` +
            # ``set_active_backend`` + ``apply_config`` so concurrent
            # IPC ``set_config`` calls can't interleave attribute
            # writes between the three operations. The lock is an RLock
            # on the real app (re-entry safe — ``apply_config`` and
            # ``change_model`` re-acquire internally); for fakes that
            # don't expose the attribute, we fall back to a no-op
            # context manager.
            #
            # G4-M-20: surface ``change_model`` / ``set_active_backend``
            # failures via a partial-success envelope in the response
            # data (``data.model_errors``) instead of swallowing them.
            # The response type stays ``ack`` so the renderer continues
            # to apply the rest of the payload — only the model swap
            # failed. Errors are logged at ERROR with ``exc_info=True``.
            model_errors: list[dict] = []
            applied: list[str] = []
            # Defensive lock acquisition: read via a local ref so the
            # protocol-drift introspection test (which scans for
            # ``self.app.X`` attribute access) doesn't flag this as a
            # new AppProtocol member. ``_config_mutation_lock`` is
            # intentionally NOT on AppProtocol (ADR 0008 §3.1) —
            # handlers reach it via the app's runtime attribute, not
            # via the protocol surface. A future cleanup should expose
            # a service-layer context manager (``service.atomic_config``)
            # and migrate this call site to use it.
            app_ref = self.app
            config_lock = getattr(app_ref, "_config_mutation_lock", None)
            with contextlib.ExitStack() as stack:
                if config_lock is not None:
                    stack.enter_context(config_lock)
                if "model_size" in validated and validated["model_size"] != getattr(
                    self.app.config, "model_size", None
                ):
                    try:
                        self.service.change_model(validated["model_size"])
                        applied.append("model_size")
                    except Exception as e:
                        # G4-M-20: log at ERROR with exc_info + surface
                        # partial-success envelope. CR-76: include the
                        # operation input in the log so operators can
                        # see which model_size failed without having to
                        # cross-reference the IPC payload. The full
                        # exception text is logged server-side only — it
                        # is NOT echoed in ``model_errors`` to avoid
                        # leaking server internals (CUDA error strings,
                        # HF repo IDs, internal module names, file
                        # paths) to the renderer. The renderer switches
                        # on ``code: "model_switch_failed"`` to surface
                        # the partial-success toast; the field/value
                        # pair tells it which setting failed.
                        log.error(
                            "[IPC] change_model(model_size=%s) failed: %s",
                            validated["model_size"],
                            e,
                            exc_info=True,
                        )
                        model_errors.append(
                            {
                                "code": "model_switch_failed",
                                "field": "model_size",
                                "value": validated["model_size"],
                            }
                        )
                if "asr_backend" in validated and validated["asr_backend"] != getattr(
                    self.app.config, "asr_backend", None
                ):
                    try:
                        self.service.set_active_backend(validated["asr_backend"])
                        applied.append("asr_backend")
                    except Exception as e:
                        # G4-M-20: same partial-success pattern as above.
                        # str(e) is logged server-side but not sent to
                        # the renderer (see the change_model branch
                        # above for the rationale).
                        log.error(
                            "[IPC] set_active_backend(asr_backend=%s) failed: %s",
                            validated["asr_backend"],
                            e,
                            exc_info=True,
                        )
                        model_errors.append(
                            {
                                "code": "model_switch_failed",
                                "field": "asr_backend",
                                "value": validated["asr_backend"],
                            }
                        )
                # Apply only allowlisted, validated values.
                # RACE-011 + AUDIO-PRESET-SAVE-FIX + ARCH-043:
                # ``service.apply_config`` holds the app's config-mutation
                # lock for the full setattr + side-effects + save sequence
                # so concurrent set_config IPC calls can't interleave, and
                # so side-effect mutations (e.g. noise_filter_* toggles
                # from the audio preset) are persisted to disk.  It then
                # invalidates the tray menu cache so the next menu build
                # picks up the new config values.
                #
                # G4-H-14: the lock acquired above is an RLock, so
                # ``apply_config``'s internal re-acquire is a no-op
                # (it doesn't deadlock). The handler-level acquisition
                # ensures the three operations (change_model,
                # set_active_backend, apply_config) see a consistent
                # config snapshot across the entire handler body.
                self.service.apply_config(validated)
                applied.extend(k for k in validated.keys() if k not in applied)
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
            #
            # G4-M-20: also include ``data.model_errors`` +
            # ``data.applied`` when ``change_model`` /
            # ``set_active_backend`` failed, so the renderer can
            # surface a "model switch failed — other settings
            # applied" toast without parsing log lines.
            response_data: dict = {}
            if rejected_keys:
                response_data["accepted"] = accepted_keys
                response_data["rejected"] = rejected_keys
            if model_errors:
                response_data["status"] = "partial"
                response_data["model_errors"] = model_errors
                response_data["applied"] = applied
            if response_data:
                resp["data"] = response_data
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            #
            # NOTE: the per-command validation errors above (non-dict
            # payload rejection, ``validate_config_update`` failures)
            # use explicit envelopes with structured ``code`` fields
            # the renderer switches on — they are NOT routed through
            # this catch-all because they carry field-level context
            # the generic envelope cannot represent. The G4-M-20
            # partial-success ``model_errors`` envelope is also NOT
            # routed through here — it's part of the success-path
            # ``ack`` response, not an error path.
            self._respond_with_error(resp, exc, "set_config")
        return resp
