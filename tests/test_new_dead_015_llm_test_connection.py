"""Regression tests for NEW-DEAD-015: LLMPolisher.test_connection wired up.

Previously ``LLMPolisher.test_connection()`` was dead — defined but
never invoked by any IPC route or UI button.  The fix:

1. Adds ``VoiceTyperService.test_llm_connection()`` that constructs an
   LLMPolisher from the live config and calls ``test_connection()``.
2. Adds an IPC route ``test_llm_connection`` that delegates to the
   service method.
3. Adds ``test_llm_connection`` to the renderer's IPC command
   allowlist so the Electron main process will forward it.
"""
from __future__ import annotations

from unittest import mock

import pytest

from voice_typer.server import ipc_server
from voice_typer.server.ipc_server import IPCServer


@pytest.fixture
def server_with_mock_app():
    app = mock.MagicMock()
    return IPCServer(app)


class TestServiceTestMethod:
    """NEW-DEAD-015: VoiceTyperService must expose test_llm_connection()."""

    def test_service_has_test_llm_connection_method(self):
        from voice_typer.server.service import VoiceTyperService
        assert hasattr(VoiceTyperService, "test_llm_connection"), (
            "VoiceTyperService must have a test_llm_connection method "
            "so the renderer can test the LLM polish API connection"
        )

    def test_service_returns_failure_when_no_api_key(self, server_with_mock_app):
        """When the config has no llm_api_key, the service must return
        success=False with a helpful message."""
        srv = server_with_mock_app
        # Mock config with empty key.
        srv.app.config = mock.MagicMock()
        srv.app.config.llm_api_key = ""
        srv.app.config.llm_api_url = ""
        srv.app.config.llm_model = ""
        srv.app.config.llm_preset = "professional"

        result = srv.service.test_llm_connection()
        assert result["success"] is False
        assert "key" in result["message"].lower()

    def test_service_constructs_polisher_and_calls_test(self, server_with_mock_app):
        """When the config has an API key, the service must construct an
        LLMPolisher and call its test_connection() method.
        """
        srv = server_with_mock_app
        srv.app.config = mock.MagicMock()
        srv.app.config.llm_api_key = "sk-test-key"
        srv.app.config.llm_api_url = "https://api.openai.com/v1"
        srv.app.config.llm_model = "gpt-4"
        srv.app.config.llm_preset = "professional"

        # Mock the LLMPolisher constructor + test_connection.
        fake_polisher = mock.MagicMock()
        fake_polisher.test_connection.return_value = (True, "Connected (model: gpt-4)")

        with mock.patch(
            "voice_typer.server.llm_polish.LLMPolisher",
            return_value=fake_polisher,
        ) as mock_ctor:
            result = srv.service.test_llm_connection()

        assert result["success"] is True
        assert "Connected" in result["message"]
        # Constructor was called with the config values.
        mock_ctor.assert_called_once()
        _, kwargs = mock_ctor.call_args
        assert kwargs["api_key"] == "sk-test-key"
        assert kwargs["api_url"] == "https://api.openai.com/v1"
        assert kwargs["model"] == "gpt-4"


class TestIpcRoute:
    """NEW-DEAD-015: the IPC dispatcher must route test_llm_connection."""

    def test_ipc_dispatches_test_llm_connection(self, server_with_mock_app):
        """``_dispatch({'type': 'test_llm_connection'})`` must call
        ``service.test_llm_connection()`` and return the result.
        """
        srv = server_with_mock_app
        srv.service.test_llm_connection = mock.MagicMock(
            return_value={"success": True, "message": "Connected"}
        )

        result = srv._dispatch({"id": 1, "type": "test_llm_connection"})

        srv.service.test_llm_connection.assert_called_once()
        assert result["type"] == "test_llm_connection_result"
        assert result["data"] == {"success": True, "message": "Connected"}

    def test_ipc_handles_service_exception(self, server_with_mock_app):
        """When the service raises, the IPC route must return an error."""
        srv = server_with_mock_app
        srv.service.test_llm_connection = mock.MagicMock(
            side_effect=RuntimeError("boom")
        )

        result = srv._dispatch({"id": 1, "type": "test_llm_connection"})

        assert result["type"] == "error"
        assert "boom" in result["data"]["message"]


class TestRendererAllowlist:
    """NEW-DEAD-015: the Electron main process allowlist must include
    test_llm_connection so the renderer can invoke it."""

    def test_allowlist_includes_test_llm_connection(self):
        from pathlib import Path
        main_ts = (
            Path(__file__).resolve().parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "main"
            / "index.ts"
        )
        source = main_ts.read_text(encoding="utf-8")
        assert '"test_llm_connection"' in source, (
            "Electron main process ALLOWED_COMMANDS must include "
            "'test_llm_connection' so the renderer can invoke it"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
