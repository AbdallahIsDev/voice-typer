"""Privacy IPC handler mixin: delete_all_personal_data, export_gdpr_bundle.

CR-87 / CR-88 (GDPR right-to-delete / right-to-export): the prior
implementation only exposed ``clear_history`` and ``export_diagnostics``
(a support-ticket bundle that redacts PII — NOT a GDPR Art. 20
portability export). This mixin adds two new IPC commands:

* ``delete_all_personal_data`` — wipes every user-data file the app
  owns (history DB, crash recovery, config.json, corrections,
  vocabulary, templates, voice-typer.log, crash_diagnostics.*.txt).
  Models in ``~/.cache/whisper`` / ``~/.cache/huggingface`` are
  correctly EXCLUDED — they are not user data, they are downloaded
  model weights the user can re-fetch.

* ``export_gdpr_bundle`` — produces a GDPR Art. 20 portability
  bundle of the same user-data files (NOT redacted, unlike
  ``export_diagnostics``), so the user can request their data and
  take it to another provider.

The actual deletion / bundling logic lives in the service layer
(``service.delete_all_personal_data`` and
``service.export_gdpr_bundle``, both implemented by Fix-D). The
handlers here are thin envelopes that:

1. Validate the (empty) payload via ``_validate_dict_payload``.
2. Call the service method.
3. Wrap the result in the standard
   ``{"type": <cmd>_result, "data": <result>}`` envelope.
4. Surface the service's ``{"success": bool, "failed": [...],
   "message": "..."}`` shape unchanged so the renderer can show the
   user exactly which files were deleted/exported and which failed
   (e.g. a file locked by another process).

Registration in ``_COMMAND_REGISTRY`` is owned by Fix-A — see the
``# TODO Fix-A`` note at the bottom of this file. The handler method
names follow the ``_handle_<cmd>`` convention so the registry lookup
(``getattr(self, handler_name)``) finds them via the normal MRO.

CR-20 (NOT YET MIGRATED): this mixin is NOT one of the four
representative handlers migrated to
:meth:`HandlerBase._respond_with_error`. Its ``except Exception``
blocks still emit ``str(e)`` directly. Migrate incrementally — see
``voice_typer/server/handlers/_base.py`` for the migration plan.
"""

from typing import Any

from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _validate_dict_payload


class PrivacyHandlersMixin:
    """Mixin: privacy / GDPR IPC handlers (delete_all_personal_data / export_gdpr_bundle)."""

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

    def _handle_delete_all_personal_data(self, data, resp) -> dict | None:
        """Handle the ``delete_all_personal_data`` IPC command (CR-87).

        GDPR Art. 17 right-to-erasure. Wipes every user-data file the
        app owns:

        * ``history.db`` (transcription history)
        * ``crash_recovery.json`` (transcription recovery state)
        * ``config.json`` (user settings + plaintext API key fallback)
        * ``voice-typer-corrections.json`` (vocabulary corrections)
        * ``voice-typer-vocabulary.json`` (user vocabulary)
        * ``voice-typer-templates.json`` (user templates)
        * ``voice-typer.log`` (runtime log)
        * ``crash_diagnostics.*.txt`` (crash dumps)

        Models in ``~/.cache/whisper`` / ``~/.cache/huggingface`` are
        EXCLUDED (they are downloadable weights, not user data).

        Response shape::

            {"type": "delete_all_personal_data_result",
             "data": {"success": bool,
                      "deleted": ["history.db", ...],
                      "skipped": ["models/...", ...],
                      "failed": {"voice-typer.log": "PermissionError: ..."}}}

        The renderer uses ``data.failed`` to show the user which
        files could not be deleted (e.g. locked by another process)
        so they can manually delete them.
        """
        try:
            # Even though this command takes no fields, run it
            # through ``_validate_dict_payload`` with an empty schema
            # so a non-dict payload (e.g. ``{"data": "not-a-dict"}``)
            # is rejected with ``invalid_payload`` rather than
            # silently accepted. Mirrors the pattern used by
            # ``_handle_toggle_dictation``.
            if data is None:
                data = {}
            _, error = _validate_dict_payload(data, {})
            if error:
                return error
            # Fix-D implements the service method. The handler is a
            # thin envelope — all deletion logic lives in the service
            # layer so the same code path can be exercised from the
            # CLI / a future ``--delete-all-personal-data`` flag.
            result = self.service.delete_all_personal_data()
            resp["type"] = "delete_all_personal_data_result"
            resp["data"] = result
        except Exception as e:
            # CR-20 TODO: migrate to ``self._respond_with_error``.
            log.error("[IPC] delete_all_personal_data failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_export_gdpr_bundle(self, data, resp) -> dict | None:
        """Handle the ``export_gdpr_bundle`` IPC command (CR-88).

        GDPR Art. 20 right-to-data-portability. Produces a ZIP bundle
        of every user-data file the app owns (the same set as
        :meth:`_handle_delete_all_personal_data`):

        * ``history.db`` (transcription history — raw text included)
        * ``crash_recovery.json`` (raw text included)
        * ``config.json`` (plaintext API key fallback included if
          keyring was unavailable when the user stored the key)
        * ``voice-typer-corrections.json``
        * ``voice-typer-vocabulary.json``
        * ``voice-typer-templates.json``
        * ``voice-typer.log``

        Unlike ``export_diagnostics`` (which redacts PII for a
        support-ticket bundle), this export is the user's OWN data
        verbatim — no redaction. The renderer must warn the user
        that the bundle contains their raw transcription text and
        any plaintext API keys before they share it.

        Response shape::

            {"type": "export_gdpr_bundle_result",
             "data": {"success": bool,
                      "path": "/tmp/voice-typer-gdpr-<ts>.zip",
                      "files": ["history.db", ...],
                      "size_bytes": 12345}}

        The renderer uses ``data.path`` to offer a "Show in file
        manager" button.
        """
        try:
            if data is None:
                data = {}
            _, error = _validate_dict_payload(data, {})
            if error:
                return error
            # Fix-D implements the service method.
            result = self.service.export_gdpr_bundle()
            resp["type"] = "export_gdpr_bundle_result"
            resp["data"] = result
        except Exception as e:
            # CR-20 TODO: migrate to ``self._respond_with_error``.
            log.error("[IPC] export_gdpr_bundle failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp


# TODO Fix-A: register these handlers in ``_COMMAND_REGISTRY`` (in
# ``voice_typer/server/ipc_server.py`` or ``ipc/server.py`` —
# whichever file owns the canonical registry after the CR-1
# deduplication). Add the two entries:
#
#     "delete_all_personal_data": "_handle_delete_all_personal_data",
#     "export_gdpr_bundle":       "_handle_export_gdpr_bundle",
#
# Also add the corresponding command names to the renderer's
# ALLOWED_COMMANDS allowlist in ``client/src/main/index.ts`` so the
# Tauri Rust host's allowlist check passes (see ADR-0015).
#
# Also add the two methods to ``ServiceProtocol`` in
# ``voice_typer/server/providers.py`` (owned by Fix-K) so the AST
# introspection test in ``tests/test_di_providers.py`` passes:
#
#     def delete_all_personal_data(self) -> dict: ...
#     def export_gdpr_bundle(self) -> dict: ...

__all__ = ["PrivacyHandlersMixin"]
