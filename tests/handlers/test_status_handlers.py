"""Unit tests for ``StatusHandlersMixin`` (CR-12).

Covers the 3 status-query IPC handlers defined in
``voice_typer/server/handlers/status_handlers.py``:

- ``_handle_get_status`` — returns ``{type: status, data: <dict|string>}``.
- ``_handle_get_volume_backend_status`` — returns
  ``{type: volume_backend_status, data: <status with is_windows flag>}``.
- ``_handle_get_model_status`` — returns ``{type: model_status, data: <result>}``.

The status handlers are mostly thin pass-throughs to the service
layer; the interesting invariants are:

1. ``get_status`` handles both the new dict-returning services and
   the legacy string-returning services (backward-compat shim).
2. ``get_volume_backend_status`` augments the service's status dict
   with an ``is_windows`` boolean (so the renderer doesn't have to
   detect the platform itself).

UE-15 (2026-07-30): ``_handle_get_rms_level`` and
``_handle_get_audio_status`` were deleted — both commands were
dropped from ``_COMMAND_REGISTRY`` and the renderer allowlist during
the Tauri migration. The corresponding ``TestGetRmsLevel`` and
``TestGetAudioStatus`` classes were removed in lockstep.

(Wave 3, 2026-08-14): ``_handle_get_prewarm_status``,
``_handle_run_prewarm``, and ``_handle_open_prewarm_log`` were
REMOVED from ``StatusHandlersMixin`` (and the matching
``_COMMAND_REGISTRY`` / TS allowlist / Rust allowlist entries) —
prewarm became a worker startup phase (master plan §6.2 P-1), so
the slim core no longer spawns a separate prewarm process. The
``TestGetPrewarmStatus``, ``TestRunPrewarm``, and
``TestOpenPrewarmLog`` classes were deleted in lockstep.

(RESTORED 2026-08-14): ``_handle_get_prewarm_status`` and
``_handle_open_prewarm_log`` were restored verbatim from commit
5a319872 — the About-page Cache Status card is a user-facing product
feature (plan §6.3 addendum), not prewarm machinery. The matching
``TestGetPrewarmStatus`` and ``TestOpenPrewarmLog`` classes were
re-added below (``TestRunPrewarm`` stays deleted — its handler was
not restored: it spawned the removed standalone-prewarm subprocess).
The open-log tests point at ``worker.log`` — the restored handler
opens the worker's log (the worker exe runs the warm phase now).
"""

from __future__ import annotations

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
        # generic WS-path envelope (no ``str(exc)`` leak).
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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


# RESTORED 2026-08-14 verbatim from 5a319872
# (tests/handlers/test_status_handlers.py) — the handler + its test
# coverage belong to the user-facing Cache Status card (plan §6.3
# addendum). TestRunPrewarm stays deleted (handler not restored).


class TestGetPrewarmStatus:
    """``_handle_get_prewarm_status`` — returns the OS file cache state."""

    def test_happy_path_returns_prewarm_status(self, ipc_server, monkeypatch):
        """The handler delegates to ``prewarm.status.get_prewarm_status()``
        (module-attr call-time read) — patch it so the test doesn't
        actually probe the filesystem.
        """
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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestRunPrewarm:
    """``_handle_run_prewarm`` — re-runs the warm phase on demand.

    RESTORED 2026-08-14 (plan §6.3 addendum 2nd half): the handler
    delegates to ``prewarm.status.run_prewarm_now()`` (a background
    daemon thread running ``warm_imports_for_worker`` + a status-file
    refresh). Patch it so the test doesn't actually warm the cache.
    """

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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestOpenPrewarmLog:
    """``_handle_open_prewarm_log`` — opens the worker log in the OS editor.

    RESTORED 2026-08-14: the handler opens ``worker.log`` (the worker
    exe runs the warm phase; its log carries the ``[PREWARM]`` lines).
    """

    def test_log_file_not_found_returns_opened_false_with_reason(self, ipc_server, monkeypatch, tmp_path):
        """When the log file doesn't exist AND can't be created, the
        handler returns ``{opened: False, reason: "not_found"}``.

        The handler tries to create an empty placeholder file if
        prewarm hasn't run yet — but if the config dir is read-only
        (e.g. permissions issue), the placeholder write fails and
        the handler falls through to the not_found path.
        """
        # Point the config-dir resolver at a path inside tmp_path that
        # doesn't exist. The handler resolves it via ``_paths.config_dir``
        # (the documented test seam: ``_paths._config_dir``, see
        # ``voice_typer/server/_paths.py``).
        nonexistent_dir = tmp_path / "no_such_dir"
        monkeypatch.setattr("voice_typer.server._paths._config_dir", lambda: nonexistent_dir)

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
        log_file = log_dir / "worker.log"
        log_file.write_text("placeholder", encoding="utf-8")

        # ``_handle_open_prewarm_log`` resolves ``worker.log`` as
        # ``<_paths.config_dir()>/logs/worker.log`` (O1), so the resolver
        # must return ``tmp_path`` (NOT ``log_dir``) here.
        monkeypatch.setattr("voice_typer.server._paths._config_dir", lambda: tmp_path)
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
