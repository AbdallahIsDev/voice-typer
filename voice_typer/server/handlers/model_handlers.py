"""Model IPC handler mixin: download_model, cancel_model_download,"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    ResponseEnvelope,
)


class ModelHandlersMixin(HandlerBase):
    """Mixin: model-management IPC handlers (download / cancel / delete)."""

    # The ``service`` / ``app`` / ``_send`` annotations are

    def _handle_download_model(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``download_model`` IPC command."""

        def body(d: dict) -> dict:
            # ``d`` is the schema-validated dict: ``model`` is a str
            model_name = d.get("model", "") or ""
            if not model_name:
                log.warning("[IPC] download_model called without model name")
                return self._error_response(
                    resp,
                    "Missing 'model' parameter",
                    code=ErrorCodes.MISSING_FIELD,
                    field="model",
                )
            log.info("[IPC] download_model called for '%s'", model_name)
            result = self.service.download_model(model_name)
            return {"type": "download_model_result", "data": result}

        return self._wrap(
            cmd_name="download_model",
            resp_type="download_model_result",
            data=data,
            resp=resp,
            body=body,
            schema={"model": {"type": str, "required": False, "default": ""}},
            pre_coerce=False,
        )

    def _handle_cancel_model_download(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``cancel_model_download`` IPC command."""
        # cancel an in-progress HuggingFace download.
        try:
            model = data.get("model") if isinstance(data, dict) else None
            model_name = model if isinstance(model, str) and model else None
            if model_name:
                log.info("[IPC] cancel_model_download called for '%s'", model_name)
            else:
                log.info("[IPC] cancel_model_download called")
            result = self.service.cancel_model_download(model_name)
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "cancel_model_download")
        return resp

    def _handle_pause_model_download(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``pause_model_download`` IPC command."""
        try:
            log.info("[IPC] pause_model_download called")
            result = self.service.pause_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "pause_model_download")
        return resp

    def _handle_resume_model_download(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``resume_model_download`` IPC command."""
        try:
            log.info("[IPC] resume_model_download called")
            result = self.service.resume_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "resume_model_download")
        return resp

    def _handle_get_download_queue(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_download_queue`` IPC command."""
        try:
            log.debug("[IPC] get_download_queue called")
            result = self.service.get_download_queue()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_download_queue")
        return resp

    def _handle_get_model_catalog(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_model_catalog`` IPC command."""
        try:
            from voice_typer.server.model_registry import get_all_models

            models = [m.to_dict() for m in get_all_models()]
            resp["type"] = "model_catalog"
            resp["data"] = {"models": models}
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_model_catalog")
        return resp

    def _handle_import_model(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``import_model`` IPC command.

        Returns the result dict from ``self.service.import_model()``.
        """

        def body(d: dict) -> dict:
            # ``d`` is the schema-validated dict: ``dir_path`` is a str
            dir_path_raw = d.get("dir_path", "")
            dir_path = dir_path_raw if isinstance(dir_path_raw, str) else ""
            if not dir_path:
                log.warning("[IPC] import_model called without dir_path")
                return self._error_response(
                    resp,
                    "Missing 'dir_path' parameter",
                    code=ErrorCodes.MISSING_FIELD,
                    field="dir_path",
                )

            # validate dir_path is within an allowed root before
            try:
                from voice_typer.server.config import _validate_import_path

                dir_path = _validate_import_path(dir_path)
            except ValueError as exc:
                # Per-command validation error: the ValueError raised by
                log.warning("[IPC] import_model path rejected: %s", exc)
                return self._error_response(
                    resp,
                    str(exc),
                    code=ErrorCodes.PATH_NOT_ALLOWED,
                )

            import os

            if not os.path.isdir(dir_path):
                log.warning("[IPC] import_model: directory not found: %s", dir_path)
                return self._error_response(
                    resp,
                    f"Directory not found: {dir_path}",
                    code=ErrorCodes.NOT_FOUND,
                    field="dir_path",
                )
            log.info("[IPC] import_model called for path: %s", dir_path)
            result = self.service.import_model(dir_path)
            return {"type": "import_model_result", "data": result}

        return self._wrap(
            cmd_name="import_model",
            resp_type="import_model_result",
            data=data,
            resp=resp,
            body=body,
            schema={"dir_path": {"type": str, "required": False, "default": ""}},
            pre_coerce=False,
        )

    def _handle_delete_model(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``delete_model`` IPC command."""

        def body(d: dict) -> dict:
            # ``d`` is the schema-validated dict: ``model`` is a str
            model_name = d.get("model", "") or ""
            if not model_name:
                log.warning("[IPC] delete_model called without model name")
                return self._error_response(
                    resp,
                    "Missing 'model' parameter",
                    code=ErrorCodes.MISSING_FIELD,
                    field="model",
                )
            log.info("[IPC] delete_model called for '%s'", model_name)
            result = self.service.delete_model(model_name)
            return {"type": "delete_model_result", "data": result}

        return self._wrap(
            cmd_name="delete_model",
            resp_type="delete_model_result",
            data=data,
            resp=resp,
            body=body,
            schema={"model": {"type": str, "required": False, "default": ""}},
            pre_coerce=False,
        )
