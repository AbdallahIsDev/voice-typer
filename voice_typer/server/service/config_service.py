"""Config-mutation domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(DT-26 / Phase 4.5 spaghetti split). Owns the cross-cutting config
surface that doesn't belong to a single domain mixin:

* :meth:`ConfigMutationMixin.get_config`                — sanitized config read
* :meth:`ConfigMutationMixin.get_defaults`              — sanitized defaults read
* :meth:`ConfigMutationMixin.apply_config`              — atomic validate→mutate→save
* :meth:`ConfigMutationMixin.apply_config_side_effects` — post-mutation side effects
* :meth:`ConfigMutationMixin.change_model`              — ASR model switch wrapper
* :meth:`ConfigMutationMixin.set_active_backend`        — ASR backend switch wrapper
* :meth:`ConfigMutationMixin._keyring_status`           — shared keychain probe helper

These previously lived on :class:`VoiceTyperService` itself because
they delegate to :class:`ConfigApplier` (PVT-21 / CR-18 / CR-65) and
touch the cross-cutting config-mutation lock. They are extracted here
as a :class:`ConfigMutationMixin` so :class:`VoiceTyperService`
shrinks back to a thin composition root (``__init__`` + ``restart`` /
``quit`` + the TypedDict response shapes). Every public method name
and signature is preserved verbatim; the mixin is composed via
multiple inheritance so ``VoiceTyperService.apply_config`` resolves
to ``ConfigMutationMixin.apply_config`` (MRO), which is what the
regression guards in ``tests/regressions/concurrency_test.py``
(``inspect.getsource(VoiceTyperService.apply_config)`` must contain
``_config_applier``) and ``tests/test_config_applier.py`` assert.
"""

import logging

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class ConfigMutationMixin(ServiceMixinBase):
    """Config read / mutate / side-effects surface.

    All mutating methods delegate to ``self._config_applier`` (the
    :class:`ConfigApplier` instance bound in
    :meth:`VoiceTyperService.__init__`) so the config-mutation lock
    (``_config_mutation_lock``) lives in exactly one place — see
    PVT-21 / CR-18 for the rationale and
    ``tests/regressions/concurrency_test.py`` for the regression
    guard that introspects ``ConfigApplier.apply_config`` for the
    lock acquisition.
    """

    # ── Config ──────────────────────────────────────────────────

    def _keyring_status(self) -> dict[str, object]:
        """SVC-6: probe the OS keychain backend once and return a
        status dict shaped ``{available, backend, fallback, reason}``.

        Centralizes the duplicated try/except that previously lived in
        both :meth:`get_config` and :meth:`get_defaults`. Wrapping the
        ``credential_store.get_keyring_status()`` call here means a
        broken keyring library never breaks the IPC ``get_config`` /
        ``get_defaults`` paths (which would lock the renderer out of
        all settings). Both callers now route through this helper so
        the probe has a single source of truth.
        """
        try:
            from voice_typer.server import credential_store

            return credential_store.get_keyring_status()
        except Exception as exc:
            log.debug("[SERVICE] keyring_status probe failed: %s", exc)
            return {
                "available": False,
                "backend": None,
                "fallback": True,
                "reason": f"credential_store probe failed: {exc}",
            }

    def get_config(self) -> dict[str, object]:
        """Return the sanitized config (API keys redacted).

        RW-01: also includes a ``keyring_status`` field describing the
        OS keychain backend state, so the renderer can show
        "Stored securely in your OS keychain" indicators next to API
        key inputs (or a warning when only the plaintext fallback is
        available).
        """
        # EC-FIX-15 / EC-22: import the canonical sanitizer from the
        # transport-neutral ``config_sanitizer`` module instead of
        # reaching DOWN into the IPC transport layer (``ipc_server``),
        # which created a real import cycle (ipc_server imports
        # VoiceTyperService from this module).
        from voice_typer.server.config_sanitizer import sanitize_config_for_ipc

        sanitized = sanitize_config_for_ipc(self._app.config)
        # SVC-6: route through the shared helper (single try/except).
        sanitized["keyring_status"] = self._keyring_status()
        return sanitized

    def get_defaults(self) -> dict[str, object]:
        """Return default config values (sanitized).

        RW-01: includes the same ``keyring_status`` field as
        :meth:`get_config` so the renderer's "Reset to Defaults" flow
        can show the same keychain indicators.
        """
        from voice_typer.server.config import Config

        # EC-FIX-15 / EC-22: import the canonical sanitizer from the
        # transport-neutral ``config_sanitizer`` module — see
        # :meth:`get_config` for rationale.
        from voice_typer.server.config_sanitizer import sanitize_config_for_ipc

        sanitized = sanitize_config_for_ipc(Config())
        # SVC-6: route through the shared helper (single try/except).
        sanitized["keyring_status"] = self._keyring_status()
        return sanitized

    # PVT-G5-024 (High, partial): ``set_config`` and ``save_config``
    # were REMOVED from this service layer.
    #
    # Rationale:
    #   - ``set_config`` (validated-config helper) had 0 production
    #     callers — the IPC ``set_config`` command is implemented in
    #     ``handlers/config_handlers.py::_handle_set_config``, which
    #     calls ``config.validate_config_update`` directly and then
    #     delegates to ``service.apply_config`` (NOT this method).
    #   - ``save_config`` (``self._app.config.save()`` wrapper) had 0
    #     production callers; the IPC ``save_config`` command was
    #     removed in ERR-IPC-003.  ``Config.save()`` is now invoked
    #     inside ``service.apply_config`` under the config-mutation
    #     lock so disk writes can't race.
    #
    # Callers should use:
    #   - ``config.validate_config_update(updates)`` directly for
    #     validation, OR
    #   - ``service.apply_config(updates)`` for the full atomic
    #     validate→mutate→side-effects→save→tray-invalidate flow.
    #
    # Tests that pinned the old methods (notably
    # ``tests/fixtures/ipc_test_helpers.py:155`` which assigns
    # ``service.set_config.return_value = ...`` on a MagicMock, and
    # ``tests/test_di_providers.py:544`` which asserts ``set_config``
    # is declared on ``ServiceProtocol``) need follow-up updates —
    # see the FA11-retry return summary.

    # ── Config side effects (ARCH-005) ──────────────────────────

    def apply_config_side_effects(self, updates: dict) -> dict:
        """Apply side effects after config changes. Delegates to ConfigApplier (CR-61 + CR-97, PVT-21).

        Returns
        -------
        dict
            PVT-060 (session-3): side-effect status dict from
            :meth:`ConfigApplier.apply_config_side_effects` (shape
            ``{"autostart_status": dict | None, "prewarm_status": dict | None}``).
            Callers that previously discarded the return value still work.
        """
        return self._config_applier.apply_config_side_effects(updates)

    def change_model(self, model_size: str) -> None:
        """Switch the active ASR model to ``model_size``.

        Wraps ``self._app.change_model()`` so the IPC ``set_config``
        handler doesn't call ``self.app.change_model()`` directly
        (ADR 0008 §3.1).
        """
        self._app.change_model(model_size)

    def set_active_backend(self, backend: str) -> None:
        """Set the active ASR backend (e.g. ``"whisper"``, ``"qwen"``).

        Wraps ``self._app.models.set_active_backend()`` so the IPC
        ``set_config`` handler doesn't reach into ``app.models``
        directly (ADR 0008 §3.1).
        """
        self._app.models.set_active_backend(backend)

    def apply_config(self, updates: dict) -> dict:
        """Apply validated config updates atomically. Delegates to ConfigApplier (CR-61 + CR-97, PVT-21).

        Returns
        -------
        dict
            PVT-060 (session-3): side-effect status dict from
            :meth:`ConfigApplier.apply_config` (shape
            ``{"autostart_status": dict | None, "prewarm_status": dict | None}``).
            Callers that previously discarded the return value still work.
        """
        return self._config_applier.apply_config(updates)


__all__ = ["ConfigMutationMixin"]
