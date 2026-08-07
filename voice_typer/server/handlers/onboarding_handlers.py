"""Onboarding IPC handler mixin: onboarding_* commands.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

(2026-07-30): ``_handle_onboarding_get_step``,
``_handle_onboarding_get_model_catalog``, and
``_handle_onboarding_request_keyboard_permission`` were REMOVED — the
renderer no longer invokes them (wizard state is held client-side;
the renderer uses ``get_model_catalog`` for catalog data; and the
permission flow now uses ``onboarding_check_permissions`` + a
Tauri-side invocation). The service-layer methods still exist for
internal callers; only the IPC dispatch routes were deleted.
"""

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ResponseEnvelope,
    _error_response,
    _validate_dict_payload,
)


def _redact_service_error(result: dict) -> dict:
    """redact a service-returned ``{"error": str(exc)}`` dict
        in place + return it, so exception strings never reach the renderer.

        The five onboarding ``set_*`` / ``skip`` / ``apply`` handlers
        historically delegated the ack-vs-error decision to whether the
        service's return dict contained an ``"error"`` key, and passed the
        dict straight through to the renderer. ``service.py:1452-1453``
        returns ``{"error": str(exc)}`` unredacted — so an exception
        message containing a secret (API key in a cloud-config validation
        error, a file path under the user's home dir, a CUDA error string
        with internal module names) would be exfiltrated to the renderer
        (and to ``voice-typer.log`` if the renderer logged it).

        The proper fix (per the finding's "Change service methods to
        ``raise``" recommendation) is to migrate the service to raising
    typed exceptions so the handler's  catch-all
        (:meth:`HandlerBase._respond_with_error`) fires and emits the
        generic ``internal error`` envelope. That migration is
        cross-file work in ``voice_typer/server/service.py`` (owned by
        another agent).

        This helper is the minimum handler-side mitigation: when the
        service returns a dict with a string-valued ``"error"`` key, we
        apply :func:`redact_secret` (strips ``Bearer ...``, ``sk-...``,
        ``gsk_...``, long bare alphanumeric runs, ``--token=...`` flag
        forms, etc.) AND :func:`redact_url` (strips user-info from URLs,
        replaces query-string API-key params) to the error string before
        storing it in ``resp["data"]``. The handler also logs the
        redacted error at ERROR server-side so operators have a
        breadcrumb (the unredacted message never leaves the process —
        :func:`redact_secret` / :func:`redact_url` are pure regex
        transformations with no I/O, and the unredacted ``str(exc)`` is
        dropped after redaction).

        Parameters
        ----------
        result : dict
            The service-returned dict. Mutated in place: if it contains a
            string-valued ``"error"`` key, that value is replaced with its
            redacted form.

        Returns
        -------
        dict
            The same ``result`` dict (returned so callers can write
            ``resp["data"] = _redact_service_error(result)`` as a one-liner).
    """
    err = result.get("error")
    if isinstance(err, str) and err:
        result["error"] = redact_url(redact_secret(err))
    return result


class OnboardingHandlersMixin(HandlerBase):
    """Mixin: onboarding-wizard IPC handlers (onboarding_start / onboarding_apply / ...).

    this mixin is one of the four "representative" handlers
        migrated to :meth:`HandlerBase._respond_with_error` for the
        catch-all ``except Exception`` path. See
            ``voice_typer/server/handlers/_base.py`` for the migration plan.

        Five of the onboarding handlers
        (``onboarding_set_microphone``, ``onboarding_set_hotkey``,
        ``onboarding_set_model``, ``onboarding_skip``, ``onboarding_apply``)
        delegate the ack-vs-error decision to whether the service's return
        dict contains a non-None ``"error"`` value::

            resp["type"] = "ack" if result.get("error") is None else "error"

        the check was previously ``"error" not in result`` (key
        presence), which misreported ``{"error": None}`` as ``error``. The
        fix uses ``result.get("error") is not None`` so a ``None`` value
        is correctly treated as "no error". The full typed-exception
        migration (service methods raise ``OnboardingError`` instead of
        returning ``{"error": ...}`` dicts) was deferred — it's cross-file
        work that touches ``ServiceProtocol`` and every set_*/skip/apply
        caller, outside this finding's scope.

        This is an implicit contract between the handler and the service
        layer. ``ServiceProtocol`` (in ``voice_typer/server/providers.py``)
        documents it: ``service.onboarding_set_*`` / ``service.onboarding_skip``
        / ``service.onboarding_apply`` return ``{"error": "<message>"}`` on
        failure and ``{...}`` (no ``"error"`` key, or ``{"error": None}``)
        on success. The contract is documented inline at each call site
        below; a future refactor should switch the service to raising
        exceptions on failure (preferred) so the catch-all
        ``except Exception`` envelope covers the failure path uniformly.

    when the service returns an ``{"error": ...}`` dict, the
        handler additionally logs a WARNING with the command name and the
        error string. Previously the failure surfaced only via the IPC
        response envelope (``resp["type"] = "error"``) — server-side logs
        were silent, so an operator investigating a hung wizard had no
        breadcrumb tying the renderer's error toast back to the service
        call that produced it.

    ``_handle_onboarding_start`` queries
        :meth:`service.onboarding_is_first_run` first and refuses to
        re-run the wizard after completion unless the caller passes
        ``{"force": true}`` in the data payload. This prevents a stale
        renderer (e.g. after a config reset) from re-launching the wizard
        over an already-completed onboarding state and surprising the user
        with a 6-step flow they thought was done.

    ``_handle_onboarding_start``'s ``mark_started()`` failure
        is logged at WARNING with ``exc_info=True`` instead of being
        swallowed by ``except Exception: pass``. Rationale: a
        missing ``.onboarding_started`` marker lets ``startup_sequence``'s
        auto-heal clobber an in-progress wizard on next restart — that's
        a real correctness risk, not "non-critical" as the prior comment
        claimed.
    """

    # The ``service`` / ``app`` / ``_send`` annotations are
    # inherited from :class:`HandlerMixinBase` — no per-mixin
    # re-declaration needed (the duplicate block removed here was one
    # of four that the  centralization refactor missed).

    def _handle_onboarding_is_first_run(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_is_first_run`` IPC command."""
        try:
            result = self.service.onboarding_is_first_run()
            resp["type"] = "onboarding_first_run"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "onboarding_is_first_run")
        return resp

    def _handle_onboarding_start(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_start`` IPC command.

                Also writes the ``.onboarding_started`` marker so
                ``startup_sequence.py``'s auto-heal logic can distinguish a
                genuine in-progress first-run wizard from a stale state. See
                :meth:`OnboardingController.mark_started` for the full rationale.

        before delegating to the service, query
                :meth:`service.onboarding_is_first_run` and refuse to re-run
                the wizard after completion unless the caller passes
                ``{"force": true}`` in the data payload. Without this guard,
                a stale renderer (or any caller that forgets the
                ``onboarding_reset`` step) can re-launch the 6-step wizard
                over an already-completed onboarding state.

        the ``mark_started()`` marker write is logged at WARNING
                with ``exc_info=True`` on failure (was silently swallowed).
                Rationale: a missing marker lets ``startup_sequence``'s
                auto-heal clobber an in-progress wizard on next restart —
                "non-critical" was wrong; this is a real correctness risk.
        """
        try:
            # re-run guard. ``data`` may be a non-dict (e.g. None
            # from a renderer that sends no payload) — coerce safely
            # before reading ``force``.
            data_dict = data if isinstance(data, dict) else {}
            force = bool(data_dict.get("force", False))
            first_run_result = self.service.onboarding_is_first_run()
            is_first_run = bool(first_run_result.get("is_first_run", True))
            if not is_first_run and not force:
                log.warning(
                    "[IPC] onboarding_start: rejected — onboarding already complete; pass {force: true} to re-run"
                )
                return _error_response(
                    resp,
                    "Onboarding already complete; pass {force: true} to re-run",
                    code="onboarding_already_complete",
                )
            result = self.service.onboarding_start()
            # Mark the wizard as started so auto-heal doesn't
            # clobber an in-progress first-run flow on restart.
            try:
                from voice_typer.server.onboarding import OnboardingController

                OnboardingController().mark_started()
            except Exception:
                # was ``pass``. Promoted to WARNING + exc_info so
                # operators see when the auto-heal gate is left
                # unprotected — a missing marker is the precondition for
                # the auto-heal-clobbers-in-progress-wizard bug.
                log.warning(
                    "[IPC] onboarding_start: mark_started failed — auto-heal may clobber in-progress onboarding",
                    exc_info=True,
                )
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_start")
        return resp

    def _handle_onboarding_next_step(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_next_step`` IPC command."""
        try:
            result = self.service.onboarding_next_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_next_step")
        return resp

    def _handle_onboarding_prev_step(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_prev_step`` IPC command."""
        try:
            result = self.service.onboarding_prev_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_prev_step")
        return resp

    def _handle_onboarding_set_microphone(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_microphone`` IPC command.

        ``mic_id`` is allowed to be ``None`` (no microphone
                detected case). The renderer sends ``mic_id: null`` when no
                microphones are present, so the validator accepts both ``str``
                and ``NoneType``. The ``OnboardingController.set_microphone``
                stores ``None`` verbatim, which :meth:`apply_settings` then
                skips writing to the config (preserving the default).
        """
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "mic_id": {
                        "type": (str, type(None)),
                        "required": True,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.onboarding_set_microphone(validated["mic_id"])
            # Contract: ``service.onboarding_set_microphone``
            # returns ``{"error": "<message>"}`` on failure (e.g. mic
            # not found) and ``{...}`` (no ``"error"`` key) on success.
            # The handler delegates the ack-vs-error decision to that
            # key. See the class docstring for the full contract.
            #
            # log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb (the IPC envelope
            # alone is invisible to operators reading voice-typer.log).
            # redact the error string before forwarding to
            # the renderer so exception messages containing secrets
            # (API keys, file paths) are not exfiltrated.
            # use result.get("error") is not None so
            # {"error": None} is treated as success (ack), not misreported as error.
            if result.get("error") is not None:
                log.warning(
                    "[IPC] onboarding_set_microphone: service returned error: %s",
                    result.get("error"),
                )
                result = _redact_service_error(result)
            resp["type"] = "ack" if result.get("error") is None else "error"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_microphone")
        return resp

    def _handle_onboarding_set_hotkey(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_hotkey`` IPC command.

        The default hotkey is ``<caps_lock>`` (matching
        :attr:`OnboardingController.selected_hotkey` and the first entry
        of :attr:`OnboardingController.HOTKEY_PRESETS`). Previously the
        default was ``<f2>``, which silently overrode the backend's
        Caps Lock default when the renderer sent no explicit value.
        """
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "hotkey": {"type": str, "required": False, "default": "<caps_lock>"},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.onboarding_set_hotkey(validated["hotkey"])
            # Contract: ``service.onboarding_set_hotkey``
            # returns ``{"error": "<message>"}`` on failure (e.g. hotkey
            # reserved by the OS) and ``{...}`` (no ``"error"`` key) on
            # success. See the class docstring for the full contract.
            #
            # log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb.
            # redact the error string before forwarding.
            # use result.get("error") is not None so
            # {"error": None} is treated as success (ack), not misreported as error.
            if result.get("error") is not None:
                log.warning(
                    "[IPC] onboarding_set_hotkey: service returned error: %s",
                    result.get("error"),
                )
                result = _redact_service_error(result)
            resp["type"] = "ack" if result.get("error") is None else "error"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_hotkey")
        return resp

    def _handle_onboarding_set_model(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_model`` IPC command."""
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "model": {"type": str, "required": False, "default": "small.en"},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.onboarding_set_model(validated["model"])
            # Contract: ``service.onboarding_set_model``
            # returns ``{"error": "<message>"}`` on failure (e.g. model
            # not available) and ``{...}`` (no ``"error"`` key) on
            # success. See the class docstring for the full contract.
            #
            # log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb.
            # redact the error string before forwarding.
            # use result.get("error") is not None so
            # {"error": None} is treated as success (ack), not misreported as error.
            if result.get("error") is not None:
                log.warning(
                    "[IPC] onboarding_set_model: service returned error: %s",
                    result.get("error"),
                )
                result = _redact_service_error(result)
            resp["type"] = "ack" if result.get("error") is None else "error"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_model")
        return resp

    def _handle_onboarding_set_backend(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_backend`` IPC command.

        Stores the local-vs-cloud choice from the wizard's Model step
        (``"local"`` → download + run a local AI model, where the user
        clicks Download explicitly — the app never auto-downloads;
        ``"cloud"`` → connect a cloud transcription API, whose API key
        + consent the wizard persists through the allowlisted
        ``set_config`` fields, mirroring the Models page).
        """
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "backend": {"type": str, "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.onboarding_set_backend(validated["backend"])
            # Same ack-vs-error contract as the sibling onboarding
            # handlers (see the class docstring).
            if result.get("error") is not None:
                log.warning(
                    "[IPC] onboarding_set_backend: service returned error: %s",
                    result.get("error"),
                )
                result = _redact_service_error(result)
            resp["type"] = "ack" if result.get("error") is None else "error"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_backend")
        return resp

    def _handle_onboarding_skip(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_skip`` IPC command."""
        try:
            result = self.service.onboarding_skip()
            # Contract: ``service.onboarding_skip`` returns
            # ``{"error": "<message>"}`` on failure and ``{...}`` (no
            # ``"error"`` key) on success. See the class docstring for
            # the full contract.
            #
            # log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb.
            # redact the error string before forwarding.
            # use result.get("error") is not None so
            # {"error": None} is treated as success (ack), not misreported as error.
            if result.get("error") is not None:
                log.warning(
                    "[IPC] onboarding_skip: service returned error: %s",
                    result.get("error"),
                )
                result = _redact_service_error(result)
            resp["type"] = "ack" if result.get("error") is None else "error"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_skip")
        return resp

    def _handle_onboarding_apply(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_apply`` IPC command."""
        try:
            result = self.service.onboarding_apply()
            # Contract: ``service.onboarding_apply`` returns
            # ``{"error": "<message>"}`` on failure (e.g. config write
            # error) and ``{...}`` (no ``"error"`` key) on success. See
            # the class docstring for the full contract.
            #
            # log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb. ``onboarding_apply``
            # is the most consequential of the five (it writes
            # config.json + re-registers the hotkey); a silent failure
            # here is the worst-case "wizard says done but nothing
            # actually saved" bug, so the breadcrumb is essential.
            # redact the error string before forwarding to
            # the renderer. ``onboarding_apply`` failures are the most
            # likely to leak sensitive context (config-write errors
            # can include ``str(exc)`` from the underlying
            # ``PermissionError`` / ``OSError`` which carry absolute
            # file paths under the user's home directory; cloud-config
            # validation errors can include API keys). The unredacted
            # message is logged at WARNING above (operator-only) and
            # at ERROR below; only the redacted form lands in
            # ``resp["data"]``.
            # use result.get("error") is not None so
            # {"error": None} is treated as success (ack), not misreported as
            # error. The full typed-exception migration (service methods raise
            # OnboardingError) was deferred - cross-file work outside scope.
            if result.get("error") is not None:
                log.warning(
                    "[IPC] onboarding_apply: service returned error: %s",
                    result.get("error"),
                )
                # also log at ERROR with the same redacted
                # form so the failure is visible in the ERROR-level
                # log filter operators commonly tail.
                redacted_result = _redact_service_error(dict(result))
                log.error(
                    "[IPC] onboarding_apply failed (redacted): %s",
                    redacted_result.get("error"),
                )
                result = _redact_service_error(result)
            resp["type"] = "ack" if result.get("error") is None else "error"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_apply")
        return resp

    def _handle_onboarding_get_microphones(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_get_microphones`` IPC command."""
        try:
            result = self.service.onboarding_get_microphones()
            resp["type"] = "onboarding_microphones"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_microphones")
        return resp

    def _handle_onboarding_get_model_options(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_get_model_options`` IPC command."""
        try:
            result = self.service.onboarding_get_model_options()
            resp["type"] = "onboarding_models"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_model_options")
        return resp

    def _handle_onboarding_get_hotkey_presets(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_get_hotkey_presets`` IPC command."""
        try:
            result = self.service.onboarding_get_hotkey_presets()
            resp["type"] = "onboarding_hotkey_presets"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_hotkey_presets")
        return resp

    def _handle_onboarding_check_permissions(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_check_permissions`` IPC command ( / ).

        Returns the platform-conditional permission state so the
        Permissions step can render the right setup walkthrough
        (macOS Accessibility / Linux ``input`` group + udev rule).

        The ``instructions`` dict now carries i18n *keys*
        (``title_key`` / ``steps_keys``) instead of literal English
        strings. The renderer resolves them via ``t(key)``.

        Does NOT delegate to ``self.service`` — the permission probe
        lives in :mod:`voice_typer.server.permissions` (via
        :meth:`OnboardingController.check_permissions`) and is shared
        with the hotkey-adapter runtime path.
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            result = OnboardingController().check_permissions()
            resp["type"] = "onboarding_permissions"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_check_permissions")
        return resp

    def _handle_onboarding_reset(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_reset`` IPC command.

        Clears both the ``.onboarding_complete`` and ``.onboarding_started``
        markers so the wizard reappears on next launch. Intended for a
        future "re-run onboarding" affordance in Settings and for tests.

        Registered in ``_COMMAND_REGISTRY`` under the ``onboarding_reset``
        key (see :mod:`voice_typer.server.ipc_server`).
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            OnboardingController().reset()
            resp["type"] = "ack"
            resp["data"] = {"ok": True}
        except Exception as exc:
            self._respond_with_error(resp, exc, "onboarding_reset")
        return resp
