"""PERF-10: ``VoiceTyperService.get_model_status`` TTL cache tests.

WR-3: this module was extracted from ``tests/handlers/test_status_handlers.py``
(lines 305-503 of that file). The class tests ``VoiceTyperService`` TTL cache
behaviour — a service-layer concern, not a handler concern. Moving it out of
the handler test file keeps ``test_status_handlers.py`` focused on the
``StatusHandlersMixin`` IPC dispatch surface and lets this file use the
shared ``tmp_config_dir`` fixture without an implicit cross-file dependency.

The three tests below are unchanged from their original implementations —
they construct a REAL ``VoiceTyperService`` (not the ``fake_service``
MagicMock from the handler conftest, which would not exercise the cache
logic) backed by a ``MagicMock`` app and the shared ``tmp_config_dir``
fixture so the filesystem scan is isolated to a per-test temp directory.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

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
        # in voice_typer.server.service, so patching that binding
        # controls the cache's view of "now".
        fake_now = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.service.time.monotonic",
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
