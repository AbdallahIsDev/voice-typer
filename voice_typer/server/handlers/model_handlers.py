"""Model IPC handler mixin: download_model, cancel_model_download,
test_llm_connection, delete_model.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

NEW-MODEL-001: added ``_handle_get_model_catalog`` to expose the full
``MODEL_REGISTRY`` to the renderer (rich metadata for the Models page).

NEW-PAUSE-001: added ``_handle_pause_model_download`` and
``_handle_resume_model_download`` so the renderer can pause/resume
in-progress downloads.
"""

from typing import Any
from voice_typer.server.ipc_server import log


class ModelHandlersMixin:
    """Mixin: model-management IPC handlers (download / cancel / test_llm / delete)."""

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
            model_name = (data or {}).get("model", "") if isinstance(data, dict) else ""
            if not model_name:
                resp["type"] = "error"
                resp["data"] = {"message": "Missing 'model' parameter"}
            else:
                result = self.service.download_model(model_name)
                resp["type"] = "download_model_result"
                resp["data"] = result
        except Exception as e:
            log.error("[IPC] download_model failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_cancel_model_download(self, data, resp) -> dict | None:
        """Handle the ``cancel_model_download`` IPC command."""
        # NEW-PRIV-011: cancel an in-progress HuggingFace download.
        try:
            result = self.service.cancel_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] cancel_model_download failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_pause_model_download(self, data, resp) -> dict | None:
        """Handle the ``pause_model_download`` IPC command.

        NEW-PAUSE-001: pause the in-progress model download.  Sets a
        module-level flag in :mod:`voice_typer.server.asr_setup` that
        the download polling loop checks between iterations.
        """
        try:
            result = self.service.pause_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] pause_model_download failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_resume_model_download(self, data, resp) -> dict | None:
        """Handle the ``resume_model_download`` IPC command.

        NEW-PAUSE-001: resume a paused model download.  Clears the
        module-level pause flag set by ``_handle_pause_model_download``.
        """
        try:
            result = self.service.resume_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] resume_model_download failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
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
        except Exception as e:
            log.error("[IPC] get_model_catalog failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
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
        except Exception as e:
            log.error("[IPC] test_llm_connection failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_delete_model(self, data, resp) -> dict | None:
        """Handle the ``delete_model`` IPC command."""
        # NEW-UX-005: actually delete the model files from disk,
        # not just remove from the UI list.
        try:
            model_name = (data or {}).get("model", "") if isinstance(data, dict) else ""
            if not model_name:
                resp["type"] = "error"
                resp["data"] = {"message": "Missing 'model' parameter"}
            else:
                result = self.service.delete_model(model_name)
                resp["type"] = "delete_model_result"
                resp["data"] = result
        except Exception as e:
            log.error("[IPC] delete_model failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
