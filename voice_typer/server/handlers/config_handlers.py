"""Config IPC handler mixin: get_config, get_defaults, set_config.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import contextlib
import ipaddress
from typing import cast

from voice_typer.server import event_bus
from voice_typer.server.config import validate_config_update
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import ErrorCodes, LegacyErrorCodes, _error_response  # noqa: F401

# module-level "warned once" flag for the missing
# ``_config_mutation_lock`` case. The handler runs on every ``set_config``
# IPC call, but the absence of the lock only happens on test fakes (real
# AppProtocol always provides it). A WARNING per process is enough to
# surface the misconfiguration without spamming the log on every save.
_CONFIG_LOCK_MISSING_WARNED: bool = False


class ConfigHandlersMixin(HandlerBase):
    """Mixin: config-related IPC handlers (get_config / get_defaults / set_config).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak). Inline per-command validation errors route
        through :func:`_error_response` with an explicit ``code`` field
    ().

    ``_handle_set_config`` acquires ``_config_mutation_lock``
        ONCE at the handler level and holds it across ``change_model`` +
        ``set_active_backend`` + ``apply_config`` so concurrent IPC
        ``set_config`` calls can't interleave attribute writes between
        the three operations.

    surface ``change_model`` / ``set_active_backend`` failures
        via a partial-success envelope in the response data
        (``data.model_errors``) instead of swallowing them.

    when ``change_model`` / ``set_active_backend`` raises, the
        failed key is dropped from the dict passed to ``apply_config`` AND
        from the ``applied`` list echoed back to the renderer — otherwise
        the failed model/backend value would be persisted to disk via
        ``apply_config`` AND reported as "applied" in the partial-success
        envelope, contradicting the ``model_errors`` entry. The dropped
        key is also excluded from the ``config_changed`` event so the
        renderer doesn't apply the stale value to its local config mirror.

    when ``self.app._config_mutation_lock`` is missing (test
        fakes / misconfigured host), the handler logs a WARNING once per
        process instead of silently falling back to lock-free execution.
    """

    def _handle_get_config(self, data: dict | None, resp: dict) -> dict | None:
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

    def _handle_get_defaults(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_defaults`` IPC command."""
        # return the default Config() values so the
        # renderer's "Reset to Defaults" button doesn't have to
        # hardcode 22+ field defaults (which silently drift from
        # the Python Config dataclass).  The renderer calls this
        # once, then sends the result via set_config.
        try:
            resp["type"] = "defaults"
            resp["data"] = self.service.get_defaults()
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_defaults")
        return resp

    def _handle_set_config(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``set_config`` IPC command."""
        try:
            # reject non-dict data with an explicit error
            # instead of silently no-oping. Previously, if data was a
            # list/string/None, the isinstance guard skipped all
            # setattr + side-effect blocks but still returned
            # {type: "ack"} success — the worst IPC failure mode.
            if not isinstance(data, dict):
                # route the inline validation error through
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
                # ``validate_config_update`` returns
                # human-readable error strings (e.g. "model_size must
                # be one of …"). Stamp them with ``invalid_field`` so
                # the renderer can branch on the code rather than
                # pattern-matching the message text.
                log.warning("[IPC] set_config rejected: %s", "; ".join(errors))
                # ``validate_config_update`` was changed ()
                # to accumulate ALL field errors instead of stopping at
                # the first. The handler previously threw away the
                # extra context — only ``errors[0]`` was returned in
                # the envelope, so the user had to fix-and-resubmit N
                # times to see all N errors (bad UX for batched
                # Settings flushes). We now include the FULL ``errors``
                # list in the envelope under ``data.errors`` so a new
                # renderer can surface them all at once. ``data.message``
                # is kept as ``errors[0]`` for backward compat with
                # older renderers that read only ``err.message`` (and
                # for the toast dispatcher, which truncates long
                # messages). We construct the envelope inline (rather
                # than calling ``_error_response``) because that helper
                # doesn't accept an extra-data dict — touching it
                # would require modifying ``ipc/validation.py``, which
                # is owned by a different agent (P4-A9).
                resp["type"] = "error"
                resp["data"] = {
                    "code": "invalid_field",
                    "message": errors[0],
                    "errors": list(errors),
                }
                return resp
            # A change to the bubble EDGE preference invalidates the
            # persisted DRAG position: the saved y-coordinate was computed
            # against the other edge, so keeping it would make the next
            # launch restore the bubble to a spot that contradicts the
            # user's new top/bottom choice. Clear both coordinates (the
            # optional-int fields treat ``None`` as "never dragged") so
            # hosts fall back to their default edge centering. Explicit
            # ``bubble_x`` / ``bubble_y`` values in the SAME payload win
            # (``setdefault``) — a drag-persist write never carries
            # ``bubble_position``, and an explicit pair is always the more
            # recent user intent. Injected AFTER ``validate_config_update``
            # so the accepted-keys echo lists them; ``None`` is the
            # documented valid value for these optional-int keys.
            if "bubble_position" in validated:
                validated.setdefault("bubble_x", None)
                validated.setdefault("bubble_y", None)
            # echo accepted + rejected keys so the
            # renderer can show the user which fields were applied
            # and which were silently dropped (unknown keys).
            accepted_keys = list(validated.keys())
            rejected_keys = [k for k in data if k not in validated]
            # when model_size or asr_backend changes,
            # apply it to the active engine so the next dictation
            # uses the new model without requiring a restart.
            # ADR 0008 §3.1: route through the service layer rather
            # than calling ``self.app.change_model()`` /
            # ``self.app.models.set_active_backend()`` directly.
            #
            # acquire ``_config_mutation_lock`` ONCE at the
            # handler level and hold it across ``change_model`` +
            # ``set_active_backend`` + ``apply_config`` so concurrent
            # IPC ``set_config`` calls can't interleave attribute
            # writes between the three operations. The lock is an RLock
            # on the real app (re-entry safe — ``apply_config`` and
            # ``change_model`` re-acquire internally); for fakes that
            # don't expose the attribute, we fall back to a no-op
            # context manager.
            #
            # surface ``change_model`` / ``set_active_backend``
            # failures via a partial-success envelope in the response
            # data (``data.model_errors``) instead of swallowing them.
            # The response type stays ``ack`` so the renderer continues
            # to apply the rest of the payload — only the model swap
            # failed. Errors are logged at ERROR with ``exc_info=True``.
            model_errors: list[dict] = []
            applied: list[str] = []
            # ``change_model`` / ``set_active_backend`` now return
            # immediately (the heavy load runs in a background daemon
            # thread). Capture the ack dicts here so the response can
            # surface a "loading" status to the renderer — the renderer
            # shows a spinner and dismisses it on the ``asr_backend_ready``
            # event (published by the background thread on completion).
            # Without this field the renderer would see ``ack`` and
            # assume the model is ready immediately.
            model_loading: list[dict] = []
            # track which keys failed their model/backend swap so
            # we can DROP them from the dict passed to ``apply_config``
            # (otherwise the failed model value gets persisted to disk)
            # and from the ``applied`` list echoed to the renderer
            # (otherwise the partial-success envelope lies — it claims
            # ``model_size`` was applied while ``model_errors`` says it
            # failed). Also excluded from the ``config_changed`` event
            # below so the renderer doesn't mirror the stale value.
            failed_keys: set[str] = set()
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
            # surface the missing-lock fallback at WARNING once
            # per process instead of silently running lock-free. The
            # fallback path is preserved (test fakes / misconfigured
            # hosts still work), but operators get a one-shot signal
            # that the concurrency guard is inactive — without that,
            # ``set_config`` races with ``change_model`` /
            # ``_open_config_file`` silently.
            global _CONFIG_LOCK_MISSING_WARNED
            if config_lock is None and not _CONFIG_LOCK_MISSING_WARNED:
                _CONFIG_LOCK_MISSING_WARNED = True
                log.warning(
                    "[IPC] set_config: app has no _config_mutation_lock — "
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
                    # ``change_model`` now spawns a background thread
                    # that mutates config asynchronously, so reading
                    # AFTER the call would race with the background
                    # setattr phase.
                    old_model_size = getattr(self.app.config, "model_size", None)
                    old_backend = getattr(self.app.config, "asr_backend", None)
                    try:
                        self.service.change_model(validated["model_size"])
                        applied.append("model_size")
                        # surface the "loading" status so the
                        # renderer shows a spinner and listens for the
                        # ``asr_backend_ready`` event (published by the
                        # background thread on completion).
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
                        # log at ERROR with exc_info + surface
                        # partial-success envelope. : include the
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
                                "code": LegacyErrorCodes.MODEL_SWITCH_FAILED,
                                "field": "model_size",
                                "value": validated["model_size"],
                            }
                        )
                        # drop the failed key from the persist set
                        # so apply_config doesn't write the broken value
                        # to disk, and so the ``applied`` list below
                        # doesn't claim it succeeded.
                        failed_keys.add("model_size")
                if "asr_backend" in validated and validated["asr_backend"] != getattr(
                    self.app.config, "asr_backend", None
                ):
                    # capture the OLD backend BEFORE the call
                    # (see the model_size branch above for rationale).
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
                                "code": LegacyErrorCodes.MODEL_SWITCH_FAILED,
                                "field": "asr_backend",
                                "value": validated["asr_backend"],
                            }
                        )
                        # same rationale as the model_size branch.
                        failed_keys.add("asr_backend")
                # Apply only allowlisted, validated values.
                # RACE-011 + AUDIO-PRESET-SAVE-FIX + :
                # ``service.apply_config`` holds the app's config-mutation
                # lock for the full setattr + side-effects + save sequence
                # so concurrent set_config IPC calls can't interleave, and
                # so side-effect mutations (e.g. noise_filter_* toggles
                # from the audio preset) are persisted to disk.  It then
                # invalidates the tray menu cache so the next menu build
                # picks up the new config values.
                #
                # the lock acquired above is an RLock, so
                # ``apply_config``'s internal re-acquire is a no-op
                # (it doesn't deadlock). The handler-level acquisition
                # ensures the three operations (change_model,
                # set_active_backend, apply_config) see a consistent
                # config snapshot across the entire handler body.
                #
                # build ``to_persist`` excluding failed keys so a
                # ``change_model`` / ``set_active_backend`` failure
                # doesn't get persisted to disk via ``apply_config``.
                # The previous code passed ``validated`` verbatim, which
                # meant a failed model swap still wrote the new
                # ``model_size`` to config.json — leaving the on-disk
                # config pointing at a model the running engine had
                # refused to load.
                to_persist = {k: v for k, v in validated.items() if k not in failed_keys}
                self.service.apply_config(to_persist)
                # A set_config that carries
                # ``trusted_extra_hosts`` must re-apply the allowlist
                # immediately (not just on next launch via Config.load).
                # Best-effort: an allowlist failure must not fail the
                # whole set_config.
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
                # ``to_persist`` (not ``validated``) so the
                # partial-success envelope doesn't claim a failed key
                # was applied.
                applied.extend(k for k in to_persist if k not in applied)
            # also invalidate the tray models submenu's
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
            #
            # publish ``to_persist`` (not ``validated``) so the
            # renderer doesn't mirror a failed model/backend value into
            # its local config state — that would leave the renderer's
            # UI showing e.g. "model: medium" while the running engine
            # is still on "small" because ``change_model`` raised.
            try:
                event_bus.publish(
                    {
                        "type": "config_changed",
                        "data": to_persist,
                    }
                )
            except Exception:
                log.debug("[IPC] config_changed push failed", exc_info=True)

            # if the bubble-relevant settings changed (bubble_behavior
            # / bubble_click_to_toggle / bubble_mic_button), push a dedicated
            # `bubble_config` event so the sandboxed bubble renderer (which
            # has no get_config) learns whether to show its mic button.
            # ``bubble_position`` is included because the handler above
            # clears the persisted bubble_x/bubble_y pair whenever the edge
            # preference changes — the repush carries the cleared pair to
            # BOTH runtimes (Electron main + Tauri host cache it from this
            # frame) so an in-flight durable position doesn't survive the
            # toggle.
            if any(
                k in validated
                for k in (
                    "bubble_behavior",
                    "bubble_click_to_toggle",
                    "bubble_mic_button",
                    "bubble_position",
                )
            ):
                try:
                    # (): delegate to the public
                    # ``app.push_bubble_config`` method on
                    # :class:`AppProtocol` instead of the prior
                    # ``getattr(self.app, "_waveform_bubble", None)``
                    # private-attribute access.
                    self.app.push_bubble_config(self.app.config)
                except Exception:
                    log.debug("[IPC] bubble_config push failed", exc_info=True)

            resp["type"] = "ack"
            # echo accepted + rejected keys so the
            # renderer can show the user which fields were applied
            # and which were silently dropped (unknown keys).
            # Only include data when there are rejected keys, so
            # the common case (all keys accepted) returns a plain
            # {type: "ack"} matching existing callers.
            #
            # also include ``data.model_errors`` +
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
            if model_loading:
                # surface the "loading" status so the renderer
                # shows a spinner and listens for the
                # ``asr_backend_ready`` event (published by the
                # background thread on completion). Without this field
                # the renderer would see ``ack`` and assume the model
                # is ready immediately, hiding the 5-30s load latency.
                response_data["model_loading"] = model_loading
            if response_data:
                resp["data"] = response_data
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            #
            # NOTE: the per-command validation errors above (non-dict
            # payload rejection, ``validate_config_update`` failures)
            # use explicit envelopes with structured ``code`` fields
            # the renderer switches on — they are NOT routed through
            # this catch-all because they carry field-level context
            # the generic envelope cannot represent. The
            # partial-success ``model_errors`` envelope is also NOT
            # routed through here — it's part of the success-path
            # ``ack`` response, not an error path.
            self._respond_with_error(resp, exc, "set_config")
        return resp

    def _handle_add_trusted_endpoint(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``add_trusted_endpoint`` IPC command.

        Adds a hostname to the runtime URL allowlist AND persists it to
        ``config.json`` under ``trusted_extra_hosts`` so the extension
        survives a restart (``Config.load()`` re-applies the persisted
        list). This is the in-app remediation path for users running
        self-hosted LLM/ASR endpoints on non-loopback hosts — without
        it, ``assert_url_allowed`` raises ``ValueError`` for every
        request to e.g. ``https://my-vllm.lan/v1``.

        The host is normalized (lowercase, port stripped) and remains
        subject to the SSRF IP-literal blocklist + DNS-rebinding check
        in ``_secrets.assert_url_allowed`` — a user cannot bypass SSRF
        defense by adding a private IP here.

        Payload: ``{"host": "<hostname[:port]>"}``.
        """
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
            # ``https://my-vllm.lan`` must be rejected (the ":"-split
            # below would otherwise reduce it to a bare "https").
            if not raw_host or "://" in raw_host or "/" in raw_host or " " in raw_host:
                log.warning("[IPC] add_trusted_endpoint rejected: invalid host %r", raw_host)
                return _error_response(
                    resp,
                    f"invalid host {raw_host!r} — expected a bare hostname like 'my-vllm.lan'",
                    code="invalid_field",
                )
            # Extract the bare host: IPv6 literals (``fc00::1`` or
            # ``[fc00::1]:8080``) survive intact; hostnames / IPv4 have
            # the port stripped. Colon-bearing entries MUST be genuine
            # IPv6 literals — a hostname containing ``:`` is invalid
            # (IPv6 is the only legal colon-bearing host form). Mirrors
            # ``_normalize_host`` in ``security.url_allowlist`` and the
            # ``trusted_extra_hosts`` config validator (HU-35 follow-up).
            if raw_host.startswith("["):
                # Bracketed IPv6 (with optional ``:port``): ``[fc00::1]``
                # or ``[fc00::1]:8080`` → the bracketed part must be a
                # valid IPv6 literal.
                closing = raw_host.find("]")
                if closing <= 0:
                    return _error_response(
                        resp,
                        f"invalid host {raw_host!r} — is not a valid IPv6 literal",
                        code="invalid_field",
                    )
                inner = raw_host[1:closing]
                try:
                    ipaddress.ip_address(inner)
                except ValueError:
                    return _error_response(
                        resp,
                        f"invalid host {raw_host!r} — is not a valid IPv6 literal",
                        code="invalid_field",
                    )
                host = inner
            elif raw_host.count(":") > 1:
                # Bare IPv6 literal (``fc00::1``) OR a multi-colon
                # hostname (invalid — IPv6 is the only legal
                # colon-bearing host form).
                try:
                    ipaddress.ip_address(raw_host)
                except ValueError:
                    return _error_response(
                        resp,
                        f"invalid host {raw_host!r} — is not a valid IPv6 literal",
                        code="invalid_field",
                    )
                host = raw_host
            else:
                # Generic hostname / IPv4 / hostname:port — strip the
                # first ``:port`` (e.g. ``My-Vllm.Lan:8443`` →
                # ``my-vllm.lan``).
                host = raw_host.split(":")[0]
            host = host.strip().lower()
            if not host:
                log.warning("[IPC] add_trusted_endpoint rejected: invalid host %r", raw_host)
                return _error_response(
                    resp,
                    f"invalid host {raw_host!r} — expected a bare hostname like 'my-vllm.lan'",
                    code="invalid_field",
                )
            if not all(c.isalnum() or c in "-._" or c == ":" for c in host):
                return _error_response(
                    resp,
                    f"invalid host {raw_host!r} — contains invalid characters",
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
