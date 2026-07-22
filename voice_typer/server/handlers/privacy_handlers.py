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

Registration in ``_COMMAND_REGISTRY`` is already done at
``voice_typer/server/ipc_server.py`` (entries for
``delete_all_personal_data`` and ``export_gdpr_bundle``); the
handler method names follow the ``_handle_<cmd>`` convention so the
registry lookup (``getattr(self, handler_name)``) finds them via the
normal MRO. ``ServiceProtocol`` in ``voice_typer/server/providers.py``
also declares both service methods.

CR-20 / G4-CR-09 (MIGRATED): this mixin's ``except Exception`` catch-alls
call :meth:`HandlerBase._respond_with_error`, which emits the generic
WS-path error envelope (``{"code": "internal_error", "message":
"internal error"}``) and logs the traceback server-side. No
``str(e)`` is ever sent to the renderer — see
``voice_typer/server/handlers/_base.py`` for the migration plan.

G4 (GDPR-specific): ``PermissionError`` / ``OSError`` raised by the
service (when one or more user-data files are locked by another
process) is mapped to the structured ``code: "server.file_locked"``
envelope via :func:`_error_response` so the renderer can show
"these files are locked: <list>" with a retry CTA.
"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _error_response, _validate_dict_payload


class PrivacyHandlersMixin(HandlerBase):
    """Mixin: privacy / GDPR IPC handlers (delete_all_personal_data / export_gdpr_bundle).

    Inherits ``service`` / ``app`` / ``_send`` annotations from
    :class:`HandlerMixinBase` (via :class:`HandlerBase`) and the
    :meth:`_respond_with_error` helper for the catch-all ``Exception``
    path (CR-20 / G4-CR-09).

    Per-command known-error paths (e.g. file-locked on GDPR delete)
    use :func:`_error_response` with an explicit namespaced ``code``
    so the renderer can show the user a targeted message.
    """

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
        except (PermissionError, OSError) as exc:
            # G4-CR-09 / GDPR-specific: the service's
            # ``delete_all_personal_data`` raises ``PermissionError`` /
            # ``OSError`` when one or more user-data files are locked
            # by another process (e.g. the crash_diagnostics.txt file
            # is held open by a still-running crash reporter). The
            # service-side result already enumerates the locked files
            # in its ``failed`` dict — we surface a structured
            # ``code: "server.file_locked"`` envelope so the renderer
            # can show "these files are locked: <list>" with a retry
            # CTA instead of the generic "internal error" toast.
            log.warning(
                "[IPC] delete_all_personal_data: one or more files locked: %s",
                exc,
                exc_info=True,
            )
            _error_response(
                resp,
                "one or more files are locked by another process",
                code="server.file_locked",
            )
        except Exception as exc:
            # CR-20 / G4-CR-09: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "delete_all_personal_data")
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
        except (PermissionError, OSError) as exc:
            # G4-CR-09 / GDPR-specific: same file-locked envelope as
            # ``_handle_delete_all_personal_data``. The export path
            # reads every user-data file into a ZIP; if any file is
            # locked by another process, the read fails with
            # ``PermissionError`` / ``OSError``. Surface the structured
            # ``code: "server.file_locked"`` envelope so the renderer
            # can prompt the user to close the locking process and
            # retry.
            log.warning(
                "[IPC] export_gdpr_bundle: one or more files locked: %s",
                exc,
                exc_info=True,
            )
            _error_response(
                resp,
                "one or more files are locked by another process",
                code="server.file_locked",
            )
        except Exception as exc:
            # CR-20 / G4-CR-09: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "export_gdpr_bundle")
        return resp


__all__ = ["PrivacyHandlersMixin"]
