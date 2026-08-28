"""Model IPC handler mixin: download_model, cancel_model_download,
delete_model, import_model.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

(2026-07-30): ``_handle_test_llm_connection`` was REMOVED — the
renderer's Settings page now uses ``service.test_llm_connection``
directly (not over IPC). The TS allowlist also dropped the entry.
The service-layer method ``service.test_llm_connection`` still exists
(called from other internal paths); only the IPC dispatch route was
deleted.

added ``_handle_get_model_catalog`` to expose the full
``MODEL_REGISTRY`` to the renderer (rich metadata for the Models page).

added ``_handle_pause_model_download`` and
``_handle_resume_model_download`` so the renderer can pause/resume
in-progress downloads.

MODEL-IMPORT: added ``_handle_import_model`` so the renderer can scan
and import pre-downloaded models from a local directory.
"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    ResponseEnvelope,
)


class ModelHandlersMixin(HandlerBase):
    """Mixin: model-management IPC handlers (download / cancel / delete).

    this mixin is one of the four "representative" handlers
        migrated to :meth:`HandlerBase._respond_with_error` for the
        catch-all ``except Exception`` path. See
        ``voice_typer/server/handlers/_base.py`` for the migration plan.
    """

    # The ``service`` / ``app`` / ``_send`` annotations are
    # inherited from :class:`HandlerMixinBase` — no per-mixin
    # re-declaration needed (the duplicate block removed here was one
    # of four that the  centralization refactor missed).

    def _handle_download_model(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``download_model`` IPC command.

        Migrated to :meth:`HandlerBase._wrap` with ``pre_coerce=False``
        — the helper handles the surrounding ``try/except`` →
        ``_respond_with_error`` catch-all while passing non-dict
        ``data`` through unchanged so the schema still rejects it with
        ``invalid_payload``.
        """

        def body(d: dict) -> dict:
            # ``d`` is the schema-validated dict: ``model`` is a str
            # (default ``""`` when absent), so the missing-name guard
            # below is the same "Missing 'model' parameter" error the
            # pre-schema implementation produced.
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
            log.info("[IPC] cancel_model_download called")
            result = self.service.cancel_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "cancel_model_download")
        return resp

    def _handle_pause_model_download(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``pause_model_download`` IPC command.

        pause the in-progress model download.  Sets a
                module-level flag in :mod:`voice_typer.server.asr_setup` that
                the download polling loop checks between iterations.
        """
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
        """Handle the ``resume_model_download`` IPC command.

        resume a paused model download.  Clears the
                module-level pause flag set by ``_handle_pause_model_download``.
        """
        try:
            log.info("[IPC] resume_model_download called")
            result = self.service.resume_model_download()
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "resume_model_download")
        return resp

    def _handle_get_model_catalog(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_model_catalog`` IPC command.

        return the full ``MODEL_REGISTRY`` as a list of
                plain dicts so the renderer can populate the Models page with
                rich metadata (VRAM, supported languages, speed/accuracy
                ratings, descriptions, repo IDs).

                The renderer uses this to:
                  - Render model cards with VRAM, language, and speed badges
                  - Show accurate download sizes (matching the backend's
                    ``_MODEL_SIZE_MB`` table)
                  - Filter by backend (whisper / qwen / parakeet)

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
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_model_catalog")
        return resp

    def _handle_import_model(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``import_model`` IPC command.

                MODEL-IMPORT: scan a directory for HuggingFace model cache
                folders and import any recognized models into the app's HF
                cache.  ``data`` should contain ``{"dir_path": "..."}``.

                Returns the result dict from ``self.service.import_model()``.

                ``dir_path`` is validated to be within an allowed root
                (home directory, OS temp dir, or HF cache) before being passed
                to ``import_model``.  Without this check, an IPC payload could
                request scanning — and copying into the app's HF cache — any
                directory on the filesystem, including ones the user did not
                pick via the file chooser.

        Migrated to :meth:`HandlerBase._wrap` with ``pre_coerce=False``
                — the helper handles the surrounding ``try/except`` →
                ``_respond_with_error`` catch-all while passing non-dict
                ``data`` through unchanged so the schema still rejects
                it with ``invalid_payload``.
        """

        def body(d: dict) -> dict:
            # ``d`` is the schema-validated dict: ``dir_path`` is a str
            # (default ``""`` when absent). Narrow ``dir_path`` from
            # ``object`` to ``str`` for pyrefly — the ``isinstance``
            # check is always True at runtime (the schema guaranteed
            # the type) but narrows the static type so the downstream
            # ``_validate_import_path(dir_path: str)`` call type-checks.
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
            # passing it to import_model.  Resolve the path to an
            # absolute, canonical form first so the validation is not
            # bypassed by ``..`` sequences or relative paths.
            try:
                from voice_typer.server.config import _validate_import_path

                dir_path = _validate_import_path(dir_path)
            except ValueError as exc:
                # Per-command validation error: the ValueError raised by
                # ``_validate_import_path`` carries a sanitized,
                # app-defined message (no Python internals, no PII
                # beyond the user-supplied path). Route through
                # ``_error_response`` to stamp a structured ``code`` so
                # the renderer can branch on ``client.path_not_allowed``
                # rather than pattern-matching the message text. The
                # message is still echoed because it carries the
                # user-supplied path that was rejected.
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
        """Handle the ``delete_model`` IPC command.

        Actually delete the model files from disk,
        not just remove from the UI list.

        Migrated to :meth:`HandlerBase._wrap` with ``pre_coerce=False``
        — the helper handles the surrounding ``try/except`` →
        ``_respond_with_error`` catch-all while passing non-dict
        ``data`` through unchanged so the schema still rejects it with
        ``invalid_payload``.
        """

        def body(d: dict) -> dict:
            # ``d`` is the schema-validated dict: ``model`` is a str
            # (default ``""`` when absent); the missing-name guard
            # below is the same "Missing 'model' parameter" error the
            # pre-schema implementation produced.
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
