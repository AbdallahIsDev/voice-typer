"""Config IPC handler mixin: get_config, get_defaults, set_config.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import log, _push_event_now
from voice_typer.server.config import validate_config_update


class ConfigHandlersMixin:
    """Mixin: config-related IPC handlers (get_config / get_defaults / set_config)."""

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
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
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
            rejected_keys = [k for k in data.keys() if k not in validated]
            # NEW-IPC-016: when model_size or asr_backend changes,
            # apply it to the active engine so the next dictation
            # uses the new model without requiring a restart.
            if (
                "model_size" in validated
                and validated["model_size"]
                    != getattr(self.app.config, "model_size", None)
            ):
                try:
                    self.app.change_model(validated["model_size"])
                except Exception as e:
                    log.warning("[IPC] change_model failed: %s", e)
            if (
                "asr_backend" in validated
                and validated["asr_backend"]
                    != getattr(self.app.config, "asr_backend", None)
            ):
                try:
                    self.app.models.set_active_backend(
                        validated["asr_backend"]
                    )
                except Exception as e:
                    log.warning("[IPC] set_active_backend failed: %s", e)
            # Apply only allowlisted, validated values.
            # RACE-011: hold the app's config-mutation lock for the
            # full apply+save sequence so a concurrent
            # SettingsController.apply() (from the deprecated
            # tkinter settings window) can't interleave attribute
            # writes with this IPC-driven update. Without this
            # lock, half the fields could come from IPC and half
            # from the tkinter window, producing a torn config.
            # AUDIO-PRESET-SAVE-FIX: run apply_config_side_effects INSIDE
            # the config-mutation lock and save AFTER it, so that any
            # side-effect mutations (e.g. noise_filter_* toggles from
            # the audio preset) are persisted to disk.
            #
            # The previous order (save first, then apply side effects
            # outside the lock) meant that when the user set
            # ``audio_preset: "off"``, only the preset name was saved;
            # the individual ``noise_filter_*`` toggles (set to False
            # by ``_apply_audio_preset``) were NOT persisted. On
            # restart, ``Config.load()`` found ``audio_preset: "off"``
            # but the ``noise_filter_*`` fields were still at their
            # default ``True`` values, so the filter chain was built
            # with all filters ON — the preset appeared to reset to
            # Auto even though the UI showed Off.
            with self.app._config_mutation_lock:
                for k, v in validated.items():
                    setattr(self.app.config, k, v)
                # Apply side effects inside the lock so Config
                # mutations from the preset are visible to save().
                self.service.apply_config_side_effects(data)
                self.app.config.save()
            # ARCH-043: invalidate the tray menu cache so the next
            # menu build picks up the new config values (model size,
            # hotkey, etc.). Without this, the tray menu shows stale
            # state until the next state-changed event.
            try:
                self.app.tray.invalidate_menu_cache()
            except Exception:
                log.debug("[IPC] tray.invalidate_menu_cache failed", exc_info=True)
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
                _push_event_now({
                    "type": "config_changed",
                    "data": validated,
                })
            except Exception:
                log.debug("[IPC] config_changed push failed", exc_info=True)

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
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
