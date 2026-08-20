"""Config-mutation domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( / Phase 4.5 spaghetti split). Owns the cross-cutting config
surface that doesn't belong to a single domain mixin:

* :meth:`ConfigMutationMixin.get_config`                — sanitized config read
* :meth:`ConfigMutationMixin.get_defaults`              — sanitized defaults read
* :meth:`ConfigMutationMixin.apply_config`              — atomic validate→mutate→save
* :meth:`ConfigMutationMixin.apply_config_side_effects` — post-mutation side effects
* :meth:`ConfigMutationMixin.change_model`              — ASR model switch wrapper
* :meth:`ConfigMutationMixin.set_active_backend`        — ASR backend switch wrapper
* :meth:`ConfigMutationMixin.reset_config_to_defaults`  — factory reset (config-only)
* :meth:`ConfigMutationMixin._keyring_status`           — shared keychain probe helper

These previously lived on :class:`VoiceTyperService` itself because
they delegate to :class:`ConfigApplier` ( /  / ) and
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

import contextlib
import logging

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class ConfigMutationMixin(ServiceMixinBase):
    """Config read / mutate / side-effects surface.

        Most mutating methods delegate to ``self._config_applier`` (the
        :class:`ConfigApplier` instance bound in
        :meth:`VoiceTyperService.__init__`) so the config-mutation lock
        (``_config_mutation_lock``) lives in exactly one place — see
    for the rationale and
        ``tests/regressions/concurrency_test.py`` for the regression
        guard that introspects ``ConfigApplier.apply_config`` for the
        lock acquisition.

        The exception is :meth:`reset_config_to_defaults`, which is a
        whole-config factory reset: it cannot go through
        ``ConfigApplier`` (which validates and applies an *update* dict
        to the live config) because it constructs a fresh
        :class:`Config` from scratch. It still acquires
        ``_config_mutation_lock`` directly so a concurrent
        ``set_config`` IPC call can't interleave with the reset.
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

        also includes a ``keyring_status`` field describing the
                OS keychain backend state, so the renderer can show
                "Stored securely in your OS keychain" indicators next to API
                key inputs (or a warning when only the plaintext fallback is
                available).
        """
        # import the canonical sanitizer from the
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

        includes the same ``keyring_status`` field as
                :meth:`get_config` so the renderer's "Reset to Defaults" flow
                can show the same keychain indicators.
        """
        from voice_typer.server.config import Config

        # import the canonical sanitizer from the
        # transport-neutral ``config_sanitizer`` module — see
        # :meth:`get_config` for rationale.
        from voice_typer.server.config_sanitizer import sanitize_config_for_ipc

        sanitized = sanitize_config_for_ipc(Config())
        # SVC-6: route through the shared helper (single try/except).
        sanitized["keyring_status"] = self._keyring_status()
        return sanitized

    # (High, partial): ``set_config`` and ``save_config``
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
    # removed in   ``Config.save()`` is now invoked
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

    # Config side effects () ──────────────────────────

    def apply_config_side_effects(self, updates: dict) -> dict:
        """Apply side effects after config changes. Delegates to ConfigApplier ( + , ).

                Returns
                -------
                dict
        (session-3): side-effect status dict from
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
        """Apply validated config updates atomically. Delegates to ConfigApplier ( + , ).

                Returns
                -------
                dict
        (session-3): side-effect status dict from
                    :meth:`ConfigApplier.apply_config` (shape
                    ``{"autostart_status": dict | None, "prewarm_status": dict | None}``).
                    Callers that previously discarded the return value still work.
        """
        return self._config_applier.apply_config(updates)

    def reset_config_to_defaults(self, *, preserve_api_keys: bool = True) -> dict:
        """factory-reset the in-memory + on-disk config to defaults.

        Snapshots the current ``config.json`` to ``config.json.bak``
        (so the user can recover their settings if they clicked
        "Reset to defaults" by mistake), then constructs a fresh
        :class:`Config` (all defaults) and — by default — preserves
        the 5 API-key fields (``openai_api_key`` / ``groq_api_key`` /
        ``deepgram_api_key`` / ``cloud_api_key`` / ``llm_api_key``)
        from the pre-reset config so the user doesn't have to re-enter
        their keys after a reset.  Set ``preserve_api_keys=False`` to
        also wipe API keys (rare; the GDPR delete path is the right
        tool for that — it also clears the keychain).

        This method does NOT touch:

          * ``history.db`` (transcription history — GDPR Art. 17
            delete is a separate, intentional action).
          * ``voice-typer-corrections.json`` / ``vocabulary.json`` /
            ``templates.json`` (user customizations — preserved across
            a factory reset).
          * ``voice-typer.log`` (runtime log — rotated normally).
          * OS keychain entries (only the in-memory + on-disk config
            are reset).

        Acquires ``app._config_mutation_lock`` so a concurrent
        ``set_config`` IPC call can't interleave attribute writes
        with the reset.  Calls ``Config.save_strict()`` so a disk
        failure is surfaced as a ``RuntimeError`` rather than a
        silent success.  Invalidates the cached ``LLMPolisher`` so
        the next polish request rebuilds with the reset config.

        Agent 2-j wires the IPC handler that calls this method
        (``config_handlers.reset_config_to_defaults``).

        Returns::

            {"success": bool,
             "backup_path": "/path/to/config.json.bak"}

        On backup or save failure, returns::

            {"success": False, "message": "..."}
        """
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config, _config_dir
        from voice_typer.server.secure_file_io import (
            _secure_atomic_write,
            _secure_read_text,
        )

        app = self._app
        # Use ``getattr`` instead of direct attribute access so the
        # static type checker doesn't flag the access (``app`` is typed
        # as :class:`AppProtocol` which doesn't declare
        # ``_config_mutation_lock`` per ADR-0008-§3.1 — see
        # ``providers.py`` for the full rationale). ``getattr`` returns
        # ``Any`` to the type checker and is functionally equivalent at
        # runtime.
        with getattr(app, "_config_mutation_lock"):  # noqa: B009 — ADR-0008-§3.1 excludes this attr from AppProtocol; direct access fails pyrefly
            config_dir = _config_dir()
            config_file = config_dir / "config.json"
            backup_path = config_dir / "config.json.bak"

            # 1. Snapshot current config.json → config.json.bak.
            # Best-effort: if config.json doesn't exist (fresh
            # install), skip the backup.  If the backup write fails
            # (disk full, permissions), return failure — we don't
            # want to reset without a recovery path.
            #
            # Use the shared secure helpers instead of ``shutil.copy2``:
            #   * ``_secure_read_text`` opens with ``O_NOFOLLOW`` on
            #     POSIX so a symlink-planted config.json can't be
            #     followed (defense against symlink-TOCTOU exfiltration
            #     into the .bak file).
            #   * ``_secure_atomic_write`` writes to a unique tmp file
            #     (``mkstemp`` + ``O_EXCL``), fsyncs, then ``os.replace``
            #     (atomic + does NOT follow the destination symlink),
            #     and chmod's to 0o600.  This is the same vulnerability
            #     class as the one already fixed in
            #     ``config.py:_backup_before_migration``.
            if config_file.exists():
                try:
                    raw = _secure_read_text(config_file)
                    _secure_atomic_write(backup_path, raw)
                except (OSError, ValueError) as exc:
                    log.error("[SERVICE] reset_config_to_defaults: backup failed: %s", exc)
                    return {
                        "success": False,
                        "message": "failed to back up current config (see log)",
                    }

            # 2. Snapshot the API-key fields from the live Config
            # (these hold the REAL values, not the keyring://
            # reference tokens — see ``Config.load``).  We preserve
            # them so the user doesn't have to re-enter their keys
            # after a factory reset.
            preserved_keys: dict[str, str] = {}
            old_config = getattr(app, "config", None)
            if preserve_api_keys and old_config is not None:
                for field in credential_store.PROVIDER_TO_CONFIG_FIELD.values():
                    try:
                        value = getattr(old_config, field, "")
                    except Exception:
                        value = ""
                    if value:
                        preserved_keys[field] = value

            # 3. Construct a fresh Config (all defaults).
            new_config = Config()

            # 4. Re-apply preserved API keys.
            for field, value in preserved_keys.items():
                try:
                    setattr(new_config, field, value)
                except Exception:
                    log.debug(
                        "[SERVICE] reset_config_to_defaults: could not restore %s",
                        field,
                        exc_info=True,
                    )

            # 5. Save to disk (raises on failure — see Config.save_strict).
            old_config = app.config
            try:
                # Swap the in-memory Config BEFORE save so save() reads
                # the new defaults (and routes preserved API keys
                # through credential_store if keyring is available).
                app.config = new_config
                new_config.save_strict()
            except Exception as exc:
                # HU-22: restore the pre-swap config so a save failure
                # does NOT leave the in-memory config diverged from disk
                # (the renderer/engine would show defaults while the old
                # values stay on disk and reappear on restart — a stale
                # API key could even stay active after the user believed
                # they reset everything).
                app.config = old_config
                log.error("[SERVICE] reset_config_to_defaults: save_strict failed: %s", exc)
                return {
                    "success": False,
                    "message": "failed to persist reset config to disk (see log)",
                }

            # 6. Invalidate cached LLMPolisher / CloudEngine so the
            # next request rebuilds with the reset config.
            #
            # Use ``setattr`` (see the GDPR-delete path above for the
            # full rationale).
            with contextlib.suppress(Exception):
                setattr(app, "_llm_polisher", None)  # noqa: B010 — attr deliberately not on AppProtocol (ADR-0008-§3.1); direct assignment fails pyrefly
            with contextlib.suppress(Exception):
                setattr(app, "_cloud_engine", None)  # noqa: B010 — attr deliberately not on AppProtocol (ADR-0008-§3.1); direct assignment fails pyrefly

            log.info(
                "[SERVICE] reset_config_to_defaults: reset to defaults, backup at %s, preserved %d API keys",
                backup_path,
                len(preserved_keys),
            )
            return {
                "success": True,
                "backup_path": str(backup_path) if backup_path.exists() else "",
            }


__all__ = ["ConfigMutationMixin"]
