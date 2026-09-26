"""Unit tests for the media_ingest package (ADR-0023 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from voice_typer.server.media_ingest import errors as errors_mod
from voice_typer.server.media_ingest.jobs import MediaJobManager
from voice_typer.server.media_ingest.sources import classify_source


class TestClassifySource:
    def test_http_url(self):
        assert classify_source("https://example.com/a.mp4").kind == "url"

    def test_file_path(self):
        src = classify_source("C:\\media\\clip.mp4")
        assert src.kind == "file"

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            classify_source("   ")


class TestJobManager:
    def test_start_and_snapshot(self):
        mgr = MediaJobManager()
        job = mgr.start("f", lambda j: {"ok": True})
        assert job.job_id
        snap = mgr.snapshot()
        assert snap is not None and snap["job_id"] == job.job_id

    def test_second_start_rejected_while_running(self):
        import threading

        mgr = MediaJobManager()
        gate = threading.Event()
        release = threading.Event()

        def _runner(job):
            gate.set()
            release.wait(timeout=5)
            return {}

        mgr.start("a", _runner)
        assert gate.wait(timeout=5)
        with pytest.raises(errors_mod.MediaIngestError):
            mgr.start("b", lambda j: {})
        release.set()

    def test_cancel_signals_event(self):
        import threading

        mgr = MediaJobManager()
        gate = threading.Event()
        release = threading.Event()

        def _runner(job):
            gate.set()
            release.wait(timeout=5)
            return {}

        mgr.start("a", _runner)
        assert gate.wait(timeout=5)
        assert mgr.cancel() is True
        release.set()

    def test_cancel_idle_false(self):
        assert MediaJobManager().cancel() is False


class TestEngineLoop:
    def test_windows_join_text(self):
        from voice_typer.server.media_ingest import engine_loop as loop

        sr = loop.TARGET_SAMPLE_RATE
        chunks = [np.zeros(sr * 5, dtype=np.float32) for _ in range(7)]

        class _Backend:
            def __init__(self):
                self.calls = 0

            def transcribe_with_fallback(self, audio, *a, **k):
                self.calls += 1
                return f"w{self.calls}"

            def request_abort(self):
                pass

        backend = _Backend()
        state = loop.transcribe_windows(iter(chunks), backend)
        assert state.text == "w1 w2"
        assert state.decoded_seconds == pytest.approx(35.0)
        assert backend.calls == 2

    def test_cancel_returns_partial(self):
        import threading

        from voice_typer.server.media_ingest import engine_loop as loop

        sr = loop.TARGET_SAMPLE_RATE
        chunks = [np.zeros(sr * 5, dtype=np.float32)]

        class _NoopBackend:
            def transcribe_with_fallback(self, audio, *a, **k):
                raise AssertionError("must not transcribe after cancel")

            def request_abort(self):
                pass

        event = threading.Event()
        event.set()
        # Cooperative cancel: no raise, the partial state is returned so
        # the caller can persist it (ADR-0023 E14).
        state = loop.transcribe_windows(iter(chunks), _NoopBackend(), cancel_event=event)
        assert state.text == ""
        assert state.decoded_seconds == pytest.approx(0.0)

    def test_progress_reports_fraction_and_eta(self):
        from voice_typer.server.media_ingest import engine_loop as loop

        sr = loop.TARGET_SAMPLE_RATE
        chunks = [np.zeros(sr * 5, dtype=np.float32) for _ in range(6)]

        class _Backend:
            def transcribe_with_fallback(self, audio, *a, **k):
                return "w"

            def request_abort(self):
                pass

        seen: list[tuple[float, float | None]] = []
        state = loop.transcribe_windows(
            iter(chunks),
            _Backend(),
            on_progress=lambda frac, eta: seen.append((frac, eta)),
            total_seconds=30.0,
        )
        assert state.decoded_seconds == pytest.approx(30.0)
        assert seen, "progress callback never fired"
        fracs = [f for f, _ in seen]
        assert fracs == sorted(fracs)
        assert 0.0 < fracs[0] < 1.0
        assert seen[-1][0] == pytest.approx(1.0)
        assert seen[-1][1] == pytest.approx(0.0)


class TestStorageExport:
    @pytest.mark.parametrize("fmt", ["txt", "srt", "vtt", "json"])
    def test_export_formats(self, tmp_path, fmt):
        from voice_typer.server.media_ingest import storage as storage_mod

        out = storage_mod.export_text("hello world", str(tmp_path / f"out.{fmt}"), fmt=fmt)
        body = Path(out).read_text(encoding="utf-8")
        assert "hello world" in body

    def test_export_bad_format(self, tmp_path):
        from voice_typer.server.media_ingest import storage as storage_mod

        with pytest.raises(ValueError):
            storage_mod.export_text("x", str(tmp_path / "out.zzz"), fmt="zzz")


class TestUrlResolver:
    def test_selects_audio_only_url(self):
        from voice_typer.server.media_ingest import url_resolver as resolver

        class _DL:
            def __init__(self, options):
                self.options = options

            def extract_info(self, url, download=False):
                assert download is False
                audio = {
                    "url": "https://cdn.example/a.m4a",
                    "vcodec": "none",
                    "acodec": "mp4a",
                    "protocol": "https",
                    "abr": 128,
                }
                video = {
                    "url": "https://cdn.example/v.mp4",
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "protocol": "https",
                    "abr": 256,
                }
                return {
                    "title": "Demo",
                    "duration": 61.0,
                    "webpage_url": url,
                    "formats": [audio, video],
                }

        resolved = resolver.resolve_media_url(
            "https://example.com/watch?v=1",
            factory=_DL,
        )
        assert resolved.audio_url == "https://cdn.example/a.m4a"
        assert resolved.title == "Demo"

    def test_rejects_playlist(self):
        from voice_typer.server.media_ingest import url_resolver as resolver

        class _DL:
            def __init__(self, options):
                pass

            def extract_info(self, url, download=False):
                return {"_type": "playlist", "entries": []}

        with pytest.raises(errors_mod.MediaIngestError):
            resolver.resolve_media_url("https://example.com/list", factory=_DL)


class TestJsRuntime:
    def test_prefers_deno(self, tmp_path):
        from voice_typer.server.media_ingest import runtime as js_runtime

        def _which(name, path=None):
            return "/bin/deno" if name == "deno" else None

        found = js_runtime.find_js_runtime(which=_which, pack_root=tmp_path)
        assert found is not None and found.name == "deno"
        assert js_runtime.js_runtime_options(found) == {"deno": "/bin/deno"}

    def test_rejects_old_node(self, monkeypatch, tmp_path):
        import subprocess

        from voice_typer.server.media_ingest import runtime as js_runtime

        class _Done:
            returncode = 0
            stdout = "v18.0.0\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done())

        def _which(name, path=None):
            return "/bin/node" if name == "node" else None

        found = js_runtime.find_js_runtime(which=_which, pack_root=tmp_path)
        assert found is None


class TestMiniUpdate:
    def test_newer_remote_marks_update(self, tmp_path):
        from voice_typer.server.media_ingest import mini_update as extractor_update

        state_file = tmp_path / "media-extractor.json"

        def _get(url, max_bytes=0, timeout=0):
            return '{"yt_dlp_version": "2025.01.01"}'

        state = extractor_update.check_refresh(
            local_backend="2024.01.01",
            local_solver="1",
            state_file=state_file,
            now=1,
            http_get=_get,
        )
        assert state.update_available is True
        assert state_file.is_file()
        assert extractor_update.load_state(state_file) is not None


class TestExtractorRefreshStartupCheck:
    """ADR-0023: the launch-time extractor probe is CHECK-ONLY."""

    def test_shutdown_short_circuits_before_any_check(self, monkeypatch):
        import threading

        from voice_typer.server import startup_tasks
        from voice_typer.server.media_ingest import mini_update as extractor_update

        calls = {"n": 0}

        def _spy(**kwargs):
            calls["n"] += 1
            raise AssertionError("must not run after shutdown")

        monkeypatch.setattr(extractor_update, "check_refresh", _spy)
        event = threading.Event()
        event.set()
        result = startup_tasks.check_media_extractor_refresh(object(), event)
        assert result == {"checked": False, "reason": "shutdown"}
        assert calls["n"] == 0

    def test_reports_update_without_downloading(self, monkeypatch):
        from voice_typer.server import startup_tasks
        from voice_typer.server.media_ingest import mini_update as extractor_update

        state = extractor_update.ExtractorRefreshState(
            backend="yt-dlp",
            backend_version="2024.01.01",
            solver_version="1",
            checked_at=123,
            update_available=True,
            remote_backend_version="2025.01.01",
            remote_solver_version="2",
        )
        monkeypatch.setattr(extractor_update, "check_refresh", lambda **kwargs: state)
        result = startup_tasks.check_media_extractor_refresh(object())
        assert result == {"checked": True, "update_available": True, "checked_at": 123}

    def test_failure_is_swallowed(self, monkeypatch):
        from voice_typer.server import startup_tasks
        from voice_typer.server.media_ingest import mini_update as extractor_update

        def _boom(**kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(extractor_update, "check_refresh", _boom)
        assert startup_tasks.check_media_extractor_refresh(object()) == {
            "checked": False,
            "reason": "error",
        }

    def test_module_has_no_install_path(self):
        """The freshness module must stay metadata-only (no exec/download)."""
        import inspect

        from voice_typer.server.media_ingest import mini_update as extractor_update

        source = inspect.getsource(extractor_update)
        for forbidden in ("subprocess", "urlretrieve", "pip install", "shutil.copy"):
            assert forbidden not in source
