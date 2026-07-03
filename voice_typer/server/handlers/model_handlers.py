"""Model IPC handler mixin: download_model, cancel_model_download,
test_llm_connection, delete_model.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.ipc_server import log


class ModelHandlersMixin:
    """Mixin: model-management IPC handlers (download / cancel / test_llm / delete)."""

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
