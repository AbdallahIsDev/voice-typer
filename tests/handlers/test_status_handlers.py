"""Unit tests for ``StatusHandlersMixin`` (CR-12).

Covers the 8 status-query IPC handlers defined in
``voice_typer/server/handlers/status_handlers.py``:

- ``_handle_get_status`` — returns ``{type: status, data: <dict|string>}``.
- ``_handle_get_rms_level`` — returns ``{type: rms_level, data: <result>}``.
- ``_handle_get_volume_backend_status`` — returns
  ``{type: volume_backend_status, data: <status with is_windows flag>}``.
- ``_handle_get_audio_status`` — returns ``{type: audio_status, data: <result>}``.
- ``_handle_get_model_status`` — returns ``{type: model_status, data: <result>}``.
- ``_handle_get_prewarm_status`` — returns
  ``{type: prewarm_status, data: <result>}`` (delegates to prewarm module).
- ``_handle_run_prewarm`` — spawns a detached prewarm subprocess.
- ``_handle_open_prewarm_log`` — opens the prewarm log file in the OS
  default text editor.

The status handlers are mostly thin pass-throughs to the service
layer; the interesting invariants are:

1. ``get_status`` handles both the new dict-returning services and
   the legacy string-returning services (backward-compat shim).
2. ``get_volume_backend_status`` augments the service's status dict
   with an ``is_windows`` boolean (so the renderer doesn't have to
   detect the platform itself).
3. ``run_prewarm`` spawns a real subprocess — we patch
   ``subprocess.Popen`` to assert the command shape without spawning.
4. ``open_prewarm_log`` returns ``{opened: False, reason: "not_found"}``
   when the log file doesn't exist yet (prewarm hasn't run this boot).
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock


class TestGetStatus:
    """``_handle_get_status`` — returns the current recording status."""

    def test_happy_path_dict_return_value(self, ipc_server, fake_service):
        """New-style: ``service.get_status()`` returns a dict — pass through."""
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
        """ERR-021 backward-compat: old services returned a bare string.

        The handler wraps it in ``{status: <string>}`` so the renderer
        always sees a dict in ``data``.
        """
        fake_service.get_status.return_value = "recording"
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "recording"}


class TestGetRmsLevel:
    """``_handle_get_rms_level`` — returns the current RMS / peak level."""

    def test_happy_path_returns_rms_level(self, ipc_server, fake_service):
        fake_service.get_rms_level.return_value = {"rms": 0.42, "peak": 0.85}
        resp = ipc_server._handle_get_rms_level({}, {})
        assert resp["type"] == "rms_level"
        assert resp["data"] == {"rms": 0.42, "peak": 0.85}

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_rms_level.side_effect = RuntimeError("recorder not started")
        resp = ipc_server._handle_get_rms_level({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestGetVolumeBackendStatus:
    """``_handle_get_volume_backend_status`` — augments with ``is_windows``."""

    def test_happy_path_includes_is_windows_flag(self, ipc_server, fake_service):
        """The handler adds ``is_windows`` to the service's status dict.

        The renderer uses this flag to decide whether to show the
        per-app volume slider (Windows only) or the "not available
        on this platform" message.
        """
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
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestGetAudioStatus:
    """``_handle_get_audio_status`` — returns the audio filter chain status."""

    def test_happy_path_returns_audio_status(self, ipc_server, fake_service):
        fake_service.get_audio_status.return_value = {
            "filter_chain": ["noise_suppressor", "highpass"],
            "degraded": False,
            "degraded_reasons": [],
            "latency_ms": 12.5,
            "vad_backend": "silero",
            "sample_rate": 16000,
        }
        resp = ipc_server._handle_get_audio_status({}, {})
        assert resp["type"] == "audio_status"
        assert resp["data"]["filter_chain"] == ["noise_suppressor", "highpass"]
        assert resp["data"]["vad_backend"] == "silero"

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_audio_status.side_effect = RuntimeError("chain not built")
        resp = ipc_server._handle_get_audio_status({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestGetModelStatus:
    """``_handle_get_model_status`` — returns which models are on disk."""

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
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestGetPrewarmStatus:
    """``_handle_get_prewarm_status`` — returns the OS file cache state."""

    def test_happy_path_returns_prewarm_status(self, ipc_server, monkeypatch):
        """The handler delegates to ``prewarm.get_prewarm_status()`` —
        patch it so the test doesn't actually probe the filesystem.
        """
        monkeypatch.setattr(
            "voice_typer.server.prewarm.get_prewarm_status",
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
            "voice_typer.server.prewarm.get_prewarm_status",
            lambda: (_ for _ in ()).throw(RuntimeError("sentinel missing")),
        )
        resp = ipc_server._handle_get_prewarm_status({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestRunPrewarm:
    """``_handle_run_prewarm`` — spawns a detached prewarm subprocess."""

    def test_happy_path_returns_prewarm_started_with_pid(self, ipc_server, monkeypatch):
        """Valid call → ``{type: prewarm_started, data: {started: True, pid: <int>}}``.

        We patch ``subprocess.Popen`` to avoid spawning a real
        prewarm subprocess (which would slow the test and depend on
        the prewarm module being importable in CI).
        """
        captured_cmd: list[list[str]] = []

        class _FakeProc:
            pid = 4242

        def _fake_popen(cmd, **kwargs):
            captured_cmd.append(cmd)
            # Assert the kwargs are appropriate for a detached spawn.
            assert "stdout" in kwargs and kwargs["stdout"] is subprocess.DEVNULL
            assert "stderr" in kwargs and kwargs["stderr"] is subprocess.DEVNULL
            return _FakeProc()

        monkeypatch.setattr(
            "subprocess.Popen",
            _fake_popen,
        )
        # The handler does ``import subprocess`` inside the function
        # body (line-level import), so patching the global
        # ``subprocess.Popen`` is the right seam — patching
        # ``voice_typer.server.handlers.status_handlers.subprocess``
        # would fail because the module never imports subprocess at
        # module top level.

        resp = ipc_server._handle_run_prewarm({}, {})

        assert resp["type"] == "prewarm_started"
        assert resp["data"]["started"] is True
        assert resp["data"]["pid"] == 4242
        # The command must include ``--force`` and ``--trigger manual``.
        assert len(captured_cmd) == 1
        cmd = captured_cmd[0]
        assert "--force" in cmd
        assert "--trigger" in cmd
        assert "manual" in cmd

    def test_popen_raises_oserror_returns_error(self, ipc_server, monkeypatch):
        """If ``subprocess.Popen`` raises ``OSError``, return ``error``."""
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("no fork")),
        )
        resp = ipc_server._handle_run_prewarm({}, {})
        assert resp["type"] == "error"
        assert "Failed to start prewarm" in resp["data"]["message"]


class TestOpenPrewarmLog:
    """``_handle_open_prewarm_log`` — opens prewarm.log in the OS editor."""

    def test_log_file_not_found_returns_opened_false_with_reason(self, ipc_server, monkeypatch, tmp_path):
        """When the log file doesn't exist AND can't be created, the
        handler returns ``{opened: False, reason: "not_found"}``.

        The handler tries to create an empty placeholder file if
        prewarm hasn't run yet — but if the config dir is read-only
        (e.g. permissions issue), the placeholder write fails and
        the handler falls through to the not_found path.
        """
        # Point _config_dir at a path inside tmp_path that doesn't exist.
        nonexistent_dir = tmp_path / "no_such_dir"
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: nonexistent_dir)

        resp = ipc_server._handle_open_prewarm_log({}, {})
        assert resp["type"] == "prewarm_log"
        assert resp["data"]["opened"] is False
        assert resp["data"]["reason"] == "not_found"
        assert "path" in resp["data"]

    def test_happy_path_opens_file_and_returns_opened_true(self, ipc_server, monkeypatch, tmp_path):
        """When the log file exists, open it via the OS default editor.

        The handler branches per-OS: ``os.startfile`` on Windows,
        ``open`` on macOS, ``xdg-open`` on Linux.  We stub BOTH opener
        seams so the test never actually launches an editor in CI, and
        assert only the observable behavior (``opened: True`` + the
        resolved ``path``) — not the opener mechanism — so the test is
        portable across Windows / macOS / Linux runners.
        """
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "prewarm.log"
        log_file.write_text("placeholder", encoding="utf-8")

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: log_dir)
        # Linux/macOS path: handler calls ``subprocess.Popen``.
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kw: MagicMock(),
        )
        # Windows path: handler calls ``os.startfile``.  This attribute
        # only exists on Windows Python builds, so use ``raising=False``
        # to no-op the patch on Linux/macOS runners.
        monkeypatch.setattr(
            "os.startfile",
            lambda path, *args, **kwargs: None,
            raising=False,
        )

        resp = ipc_server._handle_open_prewarm_log({}, {})
        assert resp["type"] == "prewarm_log"
        assert resp["data"]["opened"] is True
        assert resp["data"]["path"] == str(log_file)
