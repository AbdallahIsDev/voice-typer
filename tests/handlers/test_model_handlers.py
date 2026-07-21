"""Unit tests for ``ModelHandlersMixin`` (CR-12).

Covers the 8 model-management IPC handlers defined in
``voice_typer/server/handlers/model_handlers.py``:

- ``_handle_download_model`` — validates ``model`` name, calls
  ``service.download_model``.
- ``_handle_cancel_model_download`` — calls ``service.cancel_model_download``.
- ``_handle_pause_model_download`` / ``_handle_resume_model_download`` —
  toggle the in-progress download pause flag.
- ``_handle_get_model_catalog`` — returns the static ``MODEL_REGISTRY``.
- ``_handle_test_llm_connection`` — calls ``service.test_llm_connection``.
- ``_handle_import_model`` — validates ``dir_path`` against allowed roots,
  checks the directory exists, then calls ``service.import_model``.
- ``_handle_delete_model`` — validates ``model`` name, calls
  ``service.delete_model``.
"""

from __future__ import annotations


class TestDownloadModel:
    """``_handle_download_model`` — downloads a HuggingFace model."""

    def test_happy_path_returns_download_model_result(self, ipc_server, fake_service):
        fake_service.download_model.return_value = {"ok": True, "path": "/cache/small.en"}
        resp = ipc_server._handle_download_model({"model": "small.en"}, {})
        assert resp["type"] == "download_model_result"
        assert resp["data"] == {"ok": True, "path": "/cache/small.en"}
        fake_service.download_model.assert_called_once_with("small.en")

    def test_missing_model_returns_error(self, ipc_server, fake_service):
        """Empty/missing ``model`` field → ``{type: error, message: Missing 'model' parameter}``.

        The handler doesn't go through ``_validate_dict_payload`` here
        (it uses an inline ``if not model_name`` guard), so the error
        shape is the plain ``{message: ...}`` form, not the structured
        ``{code: missing_field, field: model}`` form.
        """
        resp = ipc_server._handle_download_model({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["message"] == "Missing 'model' parameter"
        fake_service.download_model.assert_not_called()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.download_model.side_effect = RuntimeError("network down")
        resp = ipc_server._handle_download_model({"model": "small.en"}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"


class TestCancelModelDownload:
    """``_handle_cancel_model_download`` — cancels an in-progress download."""

    def test_happy_path_returns_ack_with_result(self, ipc_server, fake_service):
        fake_service.cancel_model_download.return_value = {"cancelled": True}
        resp = ipc_server._handle_cancel_model_download({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"cancelled": True}
        fake_service.cancel_model_download.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.cancel_model_download.side_effect = RuntimeError("no download in progress")
        resp = ipc_server._handle_cancel_model_download({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"


class TestPauseAndResumeModelDownload:
    """``_handle_pause_model_download`` / ``_handle_resume_model_download``."""

    def test_pause_happy_path(self, ipc_server, fake_service):
        fake_service.pause_model_download.return_value = {"paused": True}
        resp = ipc_server._handle_pause_model_download({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"paused": True}

    def test_resume_happy_path(self, ipc_server, fake_service):
        fake_service.resume_model_download.return_value = {"resumed": True}
        resp = ipc_server._handle_resume_model_download({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"resumed": True}

    def test_pause_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.pause_model_download.side_effect = RuntimeError("no download")
        resp = ipc_server._handle_pause_model_download({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope.
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"


class TestGetModelCatalog:
    """``_handle_get_model_catalog`` — returns the static MODEL_REGISTRY."""

    def test_happy_path_returns_model_catalog(self, ipc_server):
        """The catalog is the static ``MODEL_REGISTRY`` from
        ``voice_typer.server.model_registry`` — no service call.

        Each entry is the ``to_dict()`` of a ``ModelMetadata`` instance.
        We assert the shape and that at least one model is returned
        (the registry is non-empty in production).
        """
        resp = ipc_server._handle_get_model_catalog({}, {})
        assert resp["type"] == "model_catalog"
        assert "models" in resp["data"]
        assert isinstance(resp["data"]["models"], list)
        assert len(resp["data"]["models"]) > 0, "MODEL_REGISTRY must be non-empty"
        # Each entry must be a dict (the to_dict() output).
        first = resp["data"]["models"][0]
        assert isinstance(first, dict)


class TestTestLlmConnection:
    """``_handle_test_llm_connection`` — tests the LLM Polisher connection."""

    def test_happy_path_returns_test_result(self, ipc_server, fake_service):
        fake_service.test_llm_connection.return_value = {"ok": True, "latency_ms": 120}
        resp = ipc_server._handle_test_llm_connection({}, {})
        assert resp["type"] == "test_llm_connection_result"
        assert resp["data"] == {"ok": True, "latency_ms": 120}
        fake_service.test_llm_connection.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.test_llm_connection.side_effect = RuntimeError("api key invalid")
        resp = ipc_server._handle_test_llm_connection({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"


class TestImportModel:
    """``_handle_import_model`` — scans a directory for HF cache folders."""

    def test_missing_dir_path_returns_error(self, ipc_server, fake_service):
        """Empty/missing ``dir_path`` → ``{type: error, message: Missing 'dir_path' parameter}``."""
        resp = ipc_server._handle_import_model({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["message"] == "Missing 'dir_path' parameter"
        fake_service.import_model.assert_not_called()

    def test_non_string_dir_path_returns_error(self, ipc_server, fake_service):
        """Non-string ``dir_path`` → same Missing-parameter error (the
        handler's ``isinstance(dir_path, str)`` check rejects lists/ints)."""
        resp = ipc_server._handle_import_model({"dir_path": ["/tmp"]}, {})
        assert resp["type"] == "error"
        assert "dir_path" in resp["data"]["message"]

    def test_path_outside_allowed_roots_returns_error(self, ipc_server, fake_service, monkeypatch):
        """RW-5: ``_validate_import_path`` rejects paths outside the
        home dir, OS temp, or HF cache.  We patch the validator to
        simulate a rejected path (avoids depending on the real
        filesystem layout in CI).
        """

        def _reject(path):
            raise ValueError("path outside allowed roots")

        monkeypatch.setattr("voice_typer.server.config._validate_import_path", _reject)
        resp = ipc_server._handle_import_model({"dir_path": "/etc/passwd"}, {})
        assert resp["type"] == "error"
        assert "path outside allowed roots" in resp["data"]["message"]
        fake_service.import_model.assert_not_called()

    def test_directory_not_found_returns_error(self, ipc_server, fake_service, monkeypatch, tmp_path):
        """Validated path that doesn't exist on disk → ``Directory not found`` error."""
        # Bypass the path validator (we want to test the next guard).
        monkeypatch.setattr("voice_typer.server.config._validate_import_path", lambda p: p)
        resp = ipc_server._handle_import_model({"dir_path": str(tmp_path / "does_not_exist")}, {})
        assert resp["type"] == "error"
        assert "Directory not found" in resp["data"]["message"]
        fake_service.import_model.assert_not_called()

    def test_happy_path_returns_import_model_result(self, ipc_server, fake_service, monkeypatch, tmp_path):
        """Valid directory → ``{type: import_model_result, data: <result>}``."""
        monkeypatch.setattr("voice_typer.server.config._validate_import_path", lambda p: p)
        # Create the directory so the os.path.isdir check passes.
        scan_dir = tmp_path / "my_models"
        scan_dir.mkdir()
        fake_service.import_model.return_value = {"imported": ["small.en"], "skipped": []}

        resp = ipc_server._handle_import_model({"dir_path": str(scan_dir)}, {})
        assert resp["type"] == "import_model_result"
        assert resp["data"] == {"imported": ["small.en"], "skipped": []}
        fake_service.import_model.assert_called_once_with(str(scan_dir))


class TestDeleteModel:
    """``_handle_delete_model`` — deletes a model from disk."""

    def test_happy_path_returns_delete_model_result(self, ipc_server, fake_service):
        fake_service.delete_model.return_value = {"deleted": True, "freed_bytes": 1000000}
        resp = ipc_server._handle_delete_model({"model": "small.en"}, {})
        assert resp["type"] == "delete_model_result"
        assert resp["data"] == {"deleted": True, "freed_bytes": 1000000}
        fake_service.delete_model.assert_called_once_with("small.en")

    def test_missing_model_returns_error(self, ipc_server, fake_service):
        """Empty/missing ``model`` → ``Missing 'model' parameter`` error."""
        resp = ipc_server._handle_delete_model({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["message"] == "Missing 'model' parameter"
        fake_service.delete_model.assert_not_called()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.delete_model.side_effect = RuntimeError("model in use")
        resp = ipc_server._handle_delete_model({"model": "small.en"}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"
