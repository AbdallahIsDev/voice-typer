"""Unit tests for ``StatusHandlersMixin`` (CR-12)."""

from __future__ import annotations

from unittest.mock import MagicMock


class TestGetStatus:
    """``_handle_get_status``, returns the current recording status."""

    def test_happy_path_dict_return_value(self, ipc_server, fake_service):
        """New-style: ``service.get_status()`` returns a dict, pass through."""
        fake_service.get_status.return_value = {
            "status": "recording",
            "xruns_since_start": 2,
            "loaded_via": "prewarm",
        }
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "status"
        assert resp["data"] == {
            "status": "recording",
            "xruns_since_start": 2,
            "loaded_via": "prewarm",
        }

    def test_legacy_string_return_value_wrapped_in_dict(self, ipc_server, fake_service):
        """ERR-021 backward-compat: old services returned a bare string."""
        fake_service.get_status.return_value = "recording"
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "recording"}


class TestGetVolumeBackendStatus:
    """``_handle_get_volume_backend_status``, augments with ``is_windows``."""

    def test_happy_path_includes_is_windows_flag(self, ipc_server, fake_service):
        """The handler adds ``is_windows`` to the service's status dict."""
        fake_service.get_volume_backend_status.return_value = {
            "is_available": True,
            "backend_name": "pycaw",
            "supports_per_session": True,
        }
        resp = ipc_server._handle_get_volume_backend_status({}, {})
        assert resp["type"] == "volume_backend_status"
        assert resp["data"]["is_available"] is True
        assert resp["data"]["backend_name"] == "pycaw"
        # The handler must add the platform flag (not the service).
        assert "is_windows" in resp["data"]
        assert isinstance(resp["data"]["is_windows"], bool)

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_volume_backend_status.side_effect = RuntimeError("no backend")
        resp = ipc_server._handle_get_volume_backend_status({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestGetModelStatus:
    """``_handle_get_model_status``, returns which models are on disk."""

    def test_happy_path_returns_model_status(self, ipc_server, fake_service):
        fake_service.get_model_status.return_value = {
            "small.en": {"downloaded": True, "deps_ok": True},
            "medium.en": {"downloaded": False, "deps_ok": False},
        }
        resp = ipc_server._handle_get_model_status({}, {})
        assert resp["type"] == "model_status"
        assert resp["data"]["small.en"]["downloaded"] is True
        assert resp["data"]["medium.en"]["downloaded"] is False

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_model_status.side_effect = RuntimeError("registry corrupt")
        resp = ipc_server._handle_get_model_status({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


# RESTORED 2026-08-14 verbatim from 5a319872


class TestGetPrewarmStatus:
    """``_handle_get_prewarm_status``, returns the OS file cache state."""

    def test_happy_path_returns_prewarm_status(self, ipc_server, monkeypatch):
        """The handler delegates to ``prewarm.status.get_prewarm_status()``"""
        monkeypatch.setattr(
            "voice_typer.server.prewarm.status.get_prewarm_status",
            lambda: {
                "label": "Hot",
                "cache_ratio": 0.95,
                "last_run_at": "2025-01-01T00:00:00Z",
                "elapsed_seconds": 0.5,
            },
        )
        resp = ipc_server._handle_get_prewarm_status({}, {})
        assert resp["type"] == "prewarm_status"
        assert resp["data"]["label"] == "Hot"
        assert resp["data"]["cache_ratio"] == 0.95

    def test_prewarm_module_raises_returns_error(self, ipc_server, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.prewarm.status.get_prewarm_status",
            lambda: (_ for _ in ()).throw(RuntimeError("sentinel missing")),
        )
        resp = ipc_server._handle_get_prewarm_status({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestRunPrewarm:
    """``_handle_run_prewarm``, re-runs the warm phase on demand."""

    def test_happy_path_returns_started(self, ipc_server, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.prewarm.status.run_prewarm_now",
            lambda: True,
        )
        resp = ipc_server._handle_run_prewarm({}, {})
        assert resp["type"] == "prewarm_started"
        assert resp["data"]["started"] is True

    def test_warm_pass_raises_returns_error(self, ipc_server, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.prewarm.status.run_prewarm_now",
            lambda: (_ for _ in ()).throw(RuntimeError("sentinel failure")),
        )
        resp = ipc_server._handle_run_prewarm({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestOpenPrewarmLog:
    """``_handle_open_prewarm_log``, opens the worker log in the OS editor."""

    def test_log_file_not_found_returns_opened_false_with_reason(self, ipc_server, monkeypatch, tmp_path):
        """handler returns ``{opened: False, reason: \"not_found\"}``."""
        nonexistent_dir = tmp_path / "no_such_dir"
        monkeypatch.setattr("voice_typer.server._paths._config_dir", lambda: nonexistent_dir)

        resp = ipc_server._handle_open_prewarm_log({}, {})
        assert resp["type"] == "prewarm_log"
        assert resp["data"]["opened"] is False
        assert resp["data"]["reason"] == "not_found"
        assert "path" in resp["data"]

    def test_happy_path_opens_file_and_returns_opened_true(self, ipc_server, monkeypatch, tmp_path):
        """When the log file exists, open it via the OS default editor."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "worker.log"
        log_file.write_text("placeholder", encoding="utf-8")

        monkeypatch.setattr("voice_typer.server._paths._config_dir", lambda: tmp_path)
        # Linux/macOS path: handler calls ``subprocess.Popen``.
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kw: MagicMock(),
        )
        # Windows path: handler calls ``os.startfile``.  This attribute
        monkeypatch.setattr(
            "os.startfile",
            lambda path, *args, **kwargs: None,
            raising=False,
        )

        resp = ipc_server._handle_open_prewarm_log({}, {})
        assert resp["type"] == "prewarm_log"
        assert resp["data"]["opened"] is True
        assert resp["data"]["path"] == str(log_file)
