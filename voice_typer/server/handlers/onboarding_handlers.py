"""Onboarding IPC handler mixin: 13 onboarding_* commands.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _error_response, _validate_dict_payload


class OnboardingHandlersMixin(HandlerBase):
    """Mixin: onboarding-wizard IPC handlers (onboarding_start / onboarding_apply / ...).

    CR-20: this mixin is one of the four "representative" handlers
    migrated to :meth:`HandlerBase._respond_with_error` for the
    catch-all ``except Exception`` path. See
        ``voice_typer/server/handlers/_base.py`` for the migration plan.

    PVT-G5-095 (FA16, 2026-07-19): five of the onboarding handlers
    (``onboarding_set_microphone``, ``onboarding_set_hotkey``,
    ``onboarding_set_model``, ``onboarding_skip``, ``onboarding_apply``)
    delegate the ack-vs-error decision to whether the service's return
    dict contains an ``"error"`` key::

        resp["type"] = "ack" if "error" not in result else "error"

    This is an implicit contract between the handler and the service
    layer. ``ServiceProtocol`` (in ``voice_typer/server/providers.py``)
    documents it: ``service.onboarding_set_*`` / ``service.onboarding_skip``
    / ``service.onboarding_apply`` return ``{"error": "<message>"}`` on
    failure and ``{...}`` (no ``"error"`` key) on success. If the
    service ever renames ``"error"`` to ``"code"`` or stops including
    ``"error"`` on failure, the handler will silently report ``ack``
    for failures. The contract is documented inline at each call site
    below; a future refactor should switch the service to raising
    exceptions on failure (preferred per PVT-G5-095) so the catch-all
    ``except Exception`` envelope covers the failure path uniformly.

    DE-40: when the service returns an ``{"error": ...}`` dict, the
    handler additionally logs a WARNING with the command name and the
    error string. Previously the failure surfaced only via the IPC
    response envelope (``resp["type"] = "error"``) — server-side logs
    were silent, so an operator investigating a hung wizard had no
    breadcrumb tying the renderer's error toast back to the service
    call that produced it.

    DE-39: ``_handle_onboarding_start`` queries
    :meth:`service.onboarding_is_first_run` first and refuses to
    re-run the wizard after completion unless the caller passes
    ``{"force": true}`` in the data payload. This prevents a stale
    renderer (e.g. after a config reset) from re-launching the wizard
    over an already-completed onboarding state and surprising the user
    with a 6-step flow they thought was done.

    DE-41: ``_handle_onboarding_start``'s ``mark_started()`` failure
    is logged at WARNING with ``exc_info=True`` instead of being
    swallowed by ``except Exception: pass``. PVT-006 rationale: a
    missing ``.onboarding_started`` marker lets ``startup_sequence``'s
    auto-heal clobber an in-progress wizard on next restart — that's
    a real correctness risk, not "non-critical" as the prior comment
    claimed.
    """

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

    def _handle_onboarding_is_first_run(self, data, resp) -> dict | None:
        """Handle the ``onboarding_is_first_run`` IPC command."""
        try:
            result = self.service.onboarding_is_first_run()
            resp["type"] = "onboarding_first_run"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "onboarding_is_first_run")
        return resp

    def _handle_onboarding_start(self, data, resp) -> dict | None:
        """Handle the ``onboarding_start`` IPC command.

        PVT-006: also writes the ``.onboarding_started`` marker so
        ``startup_sequence.py``'s auto-heal logic can distinguish a
        genuine in-progress first-run wizard from a stale state. See
        :meth:`OnboardingController.mark_started` for the full rationale.

        DE-39: before delegating to the service, query
        :meth:`service.onboarding_is_first_run` and refuse to re-run
        the wizard after completion unless the caller passes
        ``{"force": true}`` in the data payload. Without this guard,
        a stale renderer (or any caller that forgets the
        ``onboarding_reset`` step) can re-launch the 6-step wizard
        over an already-completed onboarding state.

        DE-41: the ``mark_started()`` marker write is logged at WARNING
        with ``exc_info=True`` on failure (was silently swallowed).
        PVT-006 rationale: a missing marker lets ``startup_sequence``'s
        auto-heal clobber an in-progress wizard on next restart —
        "non-critical" was wrong; this is a real correctness risk.
        """
        try:
            # DE-39: re-run guard. ``data`` may be a non-dict (e.g. None
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
            # PVT-006: mark the wizard as started so auto-heal doesn't
            # clobber an in-progress first-run flow on restart.
            try:
                from voice_typer.server.onboarding import OnboardingController

                OnboardingController().mark_started()
            except Exception:
                # DE-41: was ``pass``. Promoted to WARNING + exc_info so
                # operators see when the auto-heal gate is left
                # unprotected — a missing marker is the precondition for
                # the auto-heal-clobbers-in-progress-wizard bug (PVT-006).
                log.warning(
                    "[IPC] onboarding_start: mark_started failed — auto-heal may clobber in-progress onboarding",
                    exc_info=True,
                )
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_start")
        return resp

    def _handle_onboarding_get_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_step`` IPC command."""
        try:
            result = self.service.onboarding_get_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_step")
        return resp

    def _handle_onboarding_next_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_next_step`` IPC command."""
        try:
            result = self.service.onboarding_next_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_next_step")
        return resp

    def _handle_onboarding_prev_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_prev_step`` IPC command."""
        try:
            result = self.service.onboarding_prev_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_prev_step")
        return resp

    def _handle_onboarding_set_microphone(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_microphone`` IPC command.

        CR-64: ``mic_id`` is allowed to be ``None`` (no microphone
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
            # PVT-G5-095 / Contract: ``service.onboarding_set_microphone``
            # returns ``{"error": "<message>"}`` on failure (e.g. mic
            # not found) and ``{...}`` (no ``"error"`` key) on success.
            # The handler delegates the ack-vs-error decision to that
            # key. See the class docstring for the full contract.
            #
            # DE-40: log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb (the IPC envelope
            # alone is invisible to operators reading voice-typer.log).
            if "error" in result:
                log.warning(
                    "[IPC] onboarding_set_microphone: service returned error: %s",
                    result.get("error"),
                )
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_microphone")
        return resp

    def _handle_onboarding_set_hotkey(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_hotkey`` IPC command.

        PVT-017: the default hotkey is ``<caps_lock>`` (matching
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
            # PVT-G5-095 / Contract: ``service.onboarding_set_hotkey``
            # returns ``{"error": "<message>"}`` on failure (e.g. hotkey
            # reserved by the OS) and ``{...}`` (no ``"error"`` key) on
            # success. See the class docstring for the full contract.
            #
            # DE-40: log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb.
            if "error" in result:
                log.warning(
                    "[IPC] onboarding_set_hotkey: service returned error: %s",
                    result.get("error"),
                )
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_hotkey")
        return resp

    def _handle_onboarding_set_model(self, data, resp) -> dict | None:
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
            # PVT-G5-095 / Contract: ``service.onboarding_set_model``
            # returns ``{"error": "<message>"}`` on failure (e.g. model
            # not available) and ``{...}`` (no ``"error"`` key) on
            # success. See the class docstring for the full contract.
            #
            # DE-40: log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb.
            if "error" in result:
                log.warning(
                    "[IPC] onboarding_set_model: service returned error: %s",
                    result.get("error"),
                )
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_model")
        return resp

    def _handle_onboarding_skip(self, data, resp) -> dict | None:
        """Handle the ``onboarding_skip`` IPC command."""
        try:
            result = self.service.onboarding_skip()
            # PVT-G5-095 / Contract: ``service.onboarding_skip`` returns
            # ``{"error": "<message>"}`` on failure and ``{...}`` (no
            # ``"error"`` key) on success. See the class docstring for
            # the full contract.
            #
            # DE-40: log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb.
            if "error" in result:
                log.warning(
                    "[IPC] onboarding_skip: service returned error: %s",
                    result.get("error"),
                )
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_skip")
        return resp

    def _handle_onboarding_apply(self, data, resp) -> dict | None:
        """Handle the ``onboarding_apply`` IPC command."""
        try:
            result = self.service.onboarding_apply()
            # PVT-G5-095 / Contract: ``service.onboarding_apply`` returns
            # ``{"error": "<message>"}`` on failure (e.g. config write
            # error) and ``{...}`` (no ``"error"`` key) on success. See
            # the class docstring for the full contract.
            #
            # DE-40: log the service-returned error at WARNING so the
            # failure leaves a server-side breadcrumb. ``onboarding_apply``
            # is the most consequential of the five (it writes
            # config.json + re-registers the hotkey); a silent failure
            # here is the worst-case "wizard says done but nothing
            # actually saved" bug, so the breadcrumb is essential.
            if "error" in result:
                log.warning(
                    "[IPC] onboarding_apply: service returned error: %s",
                    result.get("error"),
                )
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_apply")
        return resp

    def _handle_onboarding_get_microphones(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_microphones`` IPC command."""
        try:
            result = self.service.onboarding_get_microphones()
            resp["type"] = "onboarding_microphones"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_microphones")
        return resp

    def _handle_onboarding_get_model_options(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_model_options`` IPC command."""
        try:
            result = self.service.onboarding_get_model_options()
            resp["type"] = "onboarding_models"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_model_options")
        return resp

    def _handle_onboarding_get_model_catalog(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_model_catalog`` IPC command (UX-32).

        Returns the full rich-metadata model catalog (a superset of the
        curated ``MODEL_OPTIONS`` subset). Does NOT delegate to
        ``self.service`` — the catalog is pure static metadata from
        :mod:`voice_typer.server.model_registry`, shared with the Models
        page's ``get_model_catalog`` IPC via
        :meth:`OnboardingController.get_model_catalog`.
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            result = {"models": OnboardingController.get_model_catalog()}
            resp["type"] = "onboarding_model_catalog"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_model_catalog")
        return resp

    def _handle_onboarding_get_hotkey_presets(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_hotkey_presets`` IPC command."""
        try:
            result = self.service.onboarding_get_hotkey_presets()
            resp["type"] = "onboarding_hotkey_presets"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_hotkey_presets")
        return resp

    def _handle_onboarding_check_permissions(self, data, resp) -> dict | None:
        """Handle the ``onboarding_check_permissions`` IPC command (UX-4 / UX-27).

        Returns the platform-conditional permission state so the
        Permissions step can render the right setup walkthrough
        (macOS Accessibility / Linux ``input`` group + udev rule).

        PVT-052: the ``instructions`` dict now carries i18n *keys*
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
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_check_permissions")
        return resp

    def _handle_onboarding_request_keyboard_permission(self, data, resp) -> dict | None:
        """Handle the ``onboarding_request_keyboard_permission`` IPC command.

        PVT-057 / Fix 18: opens the OS permission UI so the user can
        grant the keyboard-monitoring permission without leaving the
        wizard. Delegates to
        :func:`voice_typer.server.permissions.request_keyboard_permission`,
        which deep-links to System Settings → Accessibility on macOS
        and runs ``pkexec install_permissions.py`` on Linux.

        NOTE: this handler must be registered in the
        ``_COMMAND_REGISTRY`` in :mod:`voice_typer.server.ipc_server`
        (owned by another agent's scope) before the renderer can invoke
        it. Until then, calls to this IPC return ``unknown_command``.
        The handler is in place so registration is a one-line change::

            "onboarding_request_keyboard_permission": "_handle_onboarding_request_keyboard_permission",
        """
        try:
            from voice_typer.server.permissions import request_keyboard_permission

            request_keyboard_permission()
            resp["type"] = "ack"
            resp["data"] = {"ok": True}
        except Exception as exc:
            self._respond_with_error(
                resp,
                exc,
                "onboarding_request_keyboard_permission",
            )
        return resp

    def _handle_onboarding_reset(self, data, resp) -> dict | None:
        """Handle the ``onboarding_reset`` IPC command (PVT-006).

        Clears both the ``.onboarding_complete`` and ``.onboarding_started``
        markers so the wizard reappears on next launch. Intended for a
        future "re-run onboarding" affordance in Settings and for tests.

        NOTE: like ``onboarding_request_keyboard_permission``, this must
        be registered in ``_COMMAND_REGISTRY`` before the renderer can
        invoke it. Registration is a one-line change::

            "onboarding_reset": "_handle_onboarding_reset",
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            OnboardingController().reset()
            resp["type"] = "ack"
            resp["data"] = {"ok": True}
        except Exception as exc:
            self._respond_with_error(resp, exc, "onboarding_reset")
        return resp
