"""Model IPC handler mixin: download_model, cancel_model_download,
test_llm_connection, delete_model, import_model.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

NEW-MODEL-001: added ``_handle_get_model_catalog`` to expose the full
``MODEL_REGISTRY`` to the renderer (rich metadata for the Models page).

NEW-PAUSE-001: added ``_handle_pause_model_download`` and
``_handle_resume_model_download`` so the renderer can pause/resume
in-progress downloads.

MODEL-IMPORT: added ``_handle_import_model`` so the renderer can scan
and import pre-downloaded models from a local directory.
"""

from typing import Any

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _validate_dict_payload


class ModelHandlersMixin(HandlerBase):
    """Mixin: model-management IPC handlers (download / cancel / test_llm / delete).

    CR-20: this mixin is one of the four "representative" handlers
    migrated to :meth:`HandlerBase._respond_with_error` for the
    catch-all ``except Exception`` path. See
    ``voice_typer/server/handlers/_base.py`` for the migration plan.
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

    def _handle_download_model(self, data, resp) -> dict | None:
        """Handle the ``download_model`` IPC command."""
        try:
            # IPC-3: validate the ``model`` field type via the shared
            # ``_validate_dict_payload`` helper so the ADR-0020 §2 claim
            # ("every handler re-validates via _validate_dict_payload")
            # holds. ``required: False, default: ""`` preserves the
            # existing inline missing-field error message
            # ("Missing 'model' parameter") that callers depend on;
            # only the *type* of a present ``model`` is checked here.
            validated, error = _validate_dict_payload(
                data,
                {
                    "model": {"type": str, "required": False, "default": ""},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            model_name = validated.get("model", "") or ""
            if not model_name:
                log.warning("[IPC] download_model called without model name")
                resp["type"] = "error"
                resp["data"] = {"message": "Missing 'model' parameter"}
            else:
                log.info("[IPC] download_model called for '%s'", model_name)
                result = self.service.download_model(model_name)
                resp["type"] = "download_model_result"
                resp["data"] = result
        except Exception as exc:
            # CR-20: emit the generic WS-path envelope instead of
            # leaking ``str(exc)`` to the renderer. CR-76's intent
            # (correlate failure with operation input) is satisfied by
            # the entry INFO log above (which records ``model_name``)
            # plus the ``cmd_name`` argument to ``_respond_with_error``
            # (which records the operation).
            self._respond_with_error(resp, exc, "download_model")
        return resp

    def _handle_cancel_model_download(self, data, resp) -> dict | None:
        """Handle the ``cancel_model_download`` IPC command."""
        # NEW-PRIV-011: cancel an in-progress HuggingFace download.
        try:
            log.info("[IPC] cancel_model_download called")
            result = self.service.cancel_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "cancel_model_download")
        return resp

    def _handle_pause_model_download(self, data, resp) -> dict | None:
        """Handle the ``pause_model_download`` IPC command.

        NEW-PAUSE-001: pause the in-progress model download.  Sets a
        module-level flag in :mod:`voice_typer.server.asr_setup` that
        the download polling loop checks between iterations.
        """
        try:
            log.info("[IPC] pause_model_download called")
            result = self.service.pause_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "pause_model_download")
        return resp

    def _handle_resume_model_download(self, data, resp) -> dict | None:
        """Handle the ``resume_model_download`` IPC command.

        NEW-PAUSE-001: resume a paused model download.  Clears the
        module-level pause flag set by ``_handle_pause_model_download``.
        """
        try:
            log.info("[IPC] resume_model_download called")
            result = self.service.resume_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "resume_model_download")
        return resp

    def _handle_get_model_catalog(self, data, resp) -> dict | None:
        """Handle the ``get_model_catalog`` IPC command.

        NEW-MODEL-001: return the full ``MODEL_REGISTRY`` as a list of
        plain dicts so the renderer can populate the Models page with
        rich metadata (VRAM, supported languages, speed/accuracy
        ratings, descriptions, repo IDs).

        The renderer uses this to:
          - Render model cards with VRAM, language, and speed badges
          - Show accurate download sizes (matching the backend's
            ``_MODEL_SIZE_MB`` table)
          - Filter by backend (whisper vs distil-whisper)

        Response shape::

            {"type": "model_catalog", "data": {"models": [<metadata-dict>, ...]}}

        Each metadata-dict has the fields defined on
        :class:`voice_typer.server.model_registry.ModelMetadata`.
        """
        try:
            from voice_typer.server.model_registry import get_all_models

            models = [m.to_dict() for m in get_all_models()]
            resp["type"] = "model_catalog"
            resp["data"] = {"models": models}
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_model_catalog")
        return resp

    def _handle_test_llm_connection(self, data, resp) -> dict | None:
        """Handle the ``test_llm_connection`` IPC command."""
        # NEW-DEAD-015: wire up the previously-dead
        # ``LLMPolisher.test_connection`` method so the renderer can
        # add a "Test connection" button on the Settings page.
        try:
            result = self.service.test_llm_connection()
            resp["type"] = "test_llm_connection_result"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "test_llm_connection")
        return resp

    def _handle_import_model(self, data, resp) -> dict | None:
        """Handle the ``import_model`` IPC command.

        MODEL-IMPORT: scan a directory for HuggingFace model cache
        folders and import any recognized models into the app's HF
        cache.  ``data`` should contain ``{"dir_path": "..."}``.

        Returns the result dict from ``self.service.import_model()``.

        RW-5: ``dir_path`` is validated to be within an allowed root
        (home directory, OS temp dir, or HF cache) before being passed
        to ``import_model``.  Without this check, an IPC payload could
        request scanning — and copying into the app's HF cache — any
        directory on the filesystem, including ones the user did not
        pick via the file chooser.
        """
        try:
            # IPC-3: validate ``dir_path`` is a string via the shared
            # ``_validate_dict_payload`` helper. ``required: False,
            # default: ""`` preserves the existing inline missing-field
            # error message ("Missing 'dir_path' parameter") that
            # callers depend on; only the *type* of a present
            # ``dir_path`` is checked here.
            validated, error = _validate_dict_payload(
                data,
                {
                    "dir_path": {"type": str, "required": False, "default": ""},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            dir_path = validated.get("dir_path", "") or ""
            if not dir_path:
                log.warning("[IPC] import_model called without dir_path")
                resp["type"] = "error"
                resp["data"] = {"message": "Missing 'dir_path' parameter"}
                return resp

            # RW-5: validate dir_path is within an allowed root before
            # passing it to import_model.  Resolve the path to an
            # absolute, canonical form first so the validation is not
            # bypassed by ``..`` sequences or relative paths.
            try:
                from voice_typer.server.config import _validate_import_path

                dir_path = _validate_import_path(dir_path)
            except ValueError as exc:
                log.warning("[IPC] import_model path rejected: %s", exc)
                resp["type"] = "error"
                resp["data"] = {"message": str(exc)}
                return resp

            import os

            if not os.path.isdir(dir_path):
                log.warning("[IPC] import_model: directory not found: %s", dir_path)
                resp["type"] = "error"
                resp["data"] = {"message": f"Directory not found: {dir_path}"}
            else:
                log.info("[IPC] import_model called for path: %s", dir_path)
                result = self.service.import_model(dir_path)
                resp["type"] = "import_model_result"
                resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope (no str(exc) leak).
            # CR-76 correlation: ``dir_path`` is logged at INFO on
            # entry above; ``cmd_name`` here records the operation.
            self._respond_with_error(resp, exc, "import_model")
        return resp

    def _handle_delete_model(self, data, resp) -> dict | None:
        """Handle the ``delete_model`` IPC command."""
        # NEW-UX-005: actually delete the model files from disk,
        # not just remove from the UI list.
        try:
            # IPC-3: validate the ``model`` field type via the shared
            # ``_validate_dict_payload`` helper. ``required: False,
            # default: ""`` preserves the existing inline missing-field
            # error message ("Missing 'model' parameter") that callers
            # depend on; only the *type* of a present ``model`` is
            # checked here.
            validated, error = _validate_dict_payload(
                data,
                {
                    "model": {"type": str, "required": False, "default": ""},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            model_name = validated.get("model", "") or ""
            if not model_name:
                log.warning("[IPC] delete_model called without model name")
                resp["type"] = "error"
                resp["data"] = {"message": "Missing 'model' parameter"}
            else:
                log.info("[IPC] delete_model called for '%s'", model_name)
                result = self.service.delete_model(model_name)
                resp["type"] = "delete_model_result"
                resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope (no str(exc) leak).
            # CR-76 correlation: ``model_name`` is logged at INFO on
            # entry above; ``cmd_name`` here records the operation.
            self._respond_with_error(resp, exc, "delete_model")
        return resp
