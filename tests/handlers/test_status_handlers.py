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

import os
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


# ── PERF-10: get_model_status TTL cache ───────────────────────────────


class TestModelStatusCache:
    """PERF-10: ``VoiceTyperService.get_model_status`` caches result for 5 s.

    The IPC renderer polls ``get_model_status`` every 2 s while the
    Models page is open, and each call performs ~28
    ``os.path.isdir()`` syscalls (one per model in MODEL_REGISTRY plus
    qwen/parakeet).  A 5 s TTL cache cuts the syscall rate by ~60 %
    without introducing user-visible staleness — the cache is
    invalidated by ``delete_model`` and ``download_model`` after any
    filesystem mutation.

    These tests construct a REAL ``VoiceTyperService`` (not the
    ``fake_service`` MagicMock from the conftest, which would not
    exercise the cache logic) backed by a ``MagicMock`` app and the
    shared ``tmp_config_dir`` fixture so the filesystem scan is
    isolated to a per-test temp directory.
    """

    def test_cache_hits_within_ttl(self, tmp_config_dir, monkeypatch):
        """Within TTL, second call returns cached result without re-querying FS.

        We wrap ``os.path.isdir`` with a counting proxy so we can
        assert that the first call touches the filesystem and the
        second call (issued immediately afterwards, well within the
        5-second TTL) does not.
        """
        from voice_typer.server.service import VoiceTyperService

        # Build a mock app whose config doesn't claim a qwen/parakeet
        # path (so those branches only consult the HF cache dir, not
        # an arbitrary MagicMock that would be truthy and trigger an
        # extra isdir call on a non-string path).
        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None

        service = VoiceTyperService(app)

        # Counting proxy around os.path.isdir — the cache hit/miss
        # signal we assert on.
        real_isdir = os.path.isdir
        isdir_calls = {"n": 0}

        def _counting_isdir(path):
            isdir_calls["n"] += 1
            return real_isdir(path)

        monkeypatch.setattr("os.path.isdir", _counting_isdir)

        # First call: populates the cache, must touch the filesystem.
        first_status = service.get_model_status()
        first_calls = isdir_calls["n"]
        assert first_calls > 0, (
            "First get_model_status() should have queried the filesystem "
            f"(expected >0 os.path.isdir calls, got {first_calls})"
        )

        # Second call within TTL: must NOT touch the filesystem.
        isdir_calls["n"] = 0
        second_status = service.get_model_status()
        assert isdir_calls["n"] == 0, (
            "Second get_model_status() within TTL should hit the cache "
            f"(expected 0 os.path.isdir calls, got {isdir_calls['n']})"
        )

        # The cached object must be returned verbatim (not a copy) so
        # the renderer can rely on identity for shallow-comparison.
        assert second_status is first_status, (
            "Cached get_model_status() should return the same dict object identity, not a freshly-computed copy"
        )

    def test_cache_invalidated_after_delete(self, tmp_config_dir, monkeypatch):
        """After ``delete_model``, the next ``get_model_status`` re-queries FS.

        We populate the cache by calling ``get_model_status`` once,
        then call ``delete_model`` (which must invalidate the cache),
        then call ``get_model_status`` again and assert:

        1. The filesystem was re-queried (cache miss).
        2. The deleted model is now reported as ``downloaded: False``
           (i.e. the new status reflects the mutation, not the stale
           cache).
        """
        from voice_typer.server.service import VoiceTyperService

        # Pre-create the HF cache directory with a "tiny.en" model
        # (repo_id = Systran/faster-whisper-tiny.en → cache subdir
        # models--Systran--faster-whisper-tiny.en).
        cache_dir = tmp_config_dir / "huggingface" / "hub"
        cache_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = cache_dir / "models--Systran--faster-whisper-tiny.en"
        repo_dir.mkdir(parents=True, exist_ok=True)

        # Active model is set to small.en (NOT tiny.en) so delete_model
        # doesn't refuse on the "cannot delete active model" guard.
        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None
        app.config.asr_backend = "whisper"
        app.config.model_size = "small.en"

        service = VoiceTyperService(app)

        # First call: populates the cache.  tiny.en should be reported
        # as downloaded because we created the cache subdir above.
        first_status = service.get_model_status()
        assert first_status["tiny.en"]["downloaded"] is True, (
            "Pre-condition: tiny.en should be downloaded before delete"
        )

        # Now wrap os.path.isdir with a counting proxy so we can
        # assert the next get_model_status actually re-queries.
        real_isdir = os.path.isdir
        isdir_calls = {"n": 0}

        def _counting_isdir(path):
            isdir_calls["n"] += 1
            return real_isdir(path)

        monkeypatch.setattr("os.path.isdir", _counting_isdir)

        # Delete the model — must invalidate the cache.
        result = service.delete_model("tiny.en")
        assert result["success"] is True, f"delete_model should succeed, got: {result}"
        # Sanity: the on-disk directory was actually removed.
        assert not repo_dir.exists(), "shutil.rmtree should have removed the dir"

        # Next get_model_status: cache was invalidated, must re-query.
        isdir_calls["n"] = 0
        second_status = service.get_model_status()
        assert isdir_calls["n"] > 0, (
            "After delete_model invalidated the cache, the next "
            "get_model_status should re-query the filesystem "
            f"(expected >0 os.path.isdir calls, got {isdir_calls['n']})"
        )
        # And the new status must reflect the deletion.
        assert second_status["tiny.en"]["downloaded"] is False, (
            "After delete_model, get_model_status should report tiny.en as not downloaded (stale cache would say True)"
        )

    def test_cache_expires_after_ttl(self, tmp_config_dir, monkeypatch):
        """After ``_MODEL_STATUS_CACHE_TTL_S`` elapses, the cache is bypassed.

        We patch ``time.monotonic`` (which ``get_model_status`` calls
        once at the top) to advance the clock past the TTL between
        calls, then assert the third call re-queries the filesystem.
        """
        from voice_typer.server.service import (
            _MODEL_STATUS_CACHE_TTL_S,
            VoiceTyperService,
        )

        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None

        service = VoiceTyperService(app)

        # Drive the cache clock manually.  ``get_model_status`` reads
        # ``time.monotonic()`` from the module-level ``time`` import
        # in voice_typer.server.service.model (where ``ModelMixin``
        # lives), so patching that binding controls the cache's view
        # of "now".
        fake_now = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.service.model.time.monotonic",
            lambda: fake_now[0],
        )

        # Counting proxy around os.path.isdir.
        real_isdir = os.path.isdir
        isdir_calls = {"n": 0}

        def _counting_isdir(path):
            isdir_calls["n"] += 1
            return real_isdir(path)

        monkeypatch.setattr("os.path.isdir", _counting_isdir)

        # t=0: first call populates the cache.
        service.get_model_status()
        assert isdir_calls["n"] > 0, "First call should query the filesystem"

        # t=TTL-0.1: still within TTL — cache hit.
        isdir_calls["n"] = 0
        fake_now[0] = _MODEL_STATUS_CACHE_TTL_S - 0.1
        service.get_model_status()
        assert isdir_calls["n"] == 0, (
            "Within TTL, get_model_status should hit the cache "
            f"(expected 0 os.path.isdir calls, got {isdir_calls['n']})"
        )

        # t=TTL+0.1: TTL expired — cache miss.
        isdir_calls["n"] = 0
        fake_now[0] = _MODEL_STATUS_CACHE_TTL_S + 0.1
        service.get_model_status()
        assert isdir_calls["n"] > 0, (
            "After TTL expires, get_model_status should re-query the "
            f"filesystem (expected >0 os.path.isdir calls, got {isdir_calls['n']})"
        )
