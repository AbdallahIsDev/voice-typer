"""Unit tests for ``MediaHandlersMixin`` (ADR-0023 Phase 1)."""

from __future__ import annotations


class TestMediaStartValidation:
    def test_missing_source_rejected(self, ipc_server, fake_service):
        resp = ipc_server._handle_media_transcribe_start({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"

    def test_url_requires_media_consent(self, ipc_server, fake_service):
        ipc_server.app.config.media_url_consent = False
        resp = ipc_server._handle_media_transcribe_start({"source": "https://example.com/a.mp4"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.consent_required"
        assert resp["data"]["consent_field"] == "media_url_consent"

    def test_no_model_selected_rejected(self, ipc_server, fake_service):
        ipc_server.app.config.media_url_consent = True
        ipc_server.app.models.active_transcriber.return_value = None
        ipc_server.app.config.model_size = ""
        resp = ipc_server._handle_media_transcribe_start({"source": "https://example.com/a.mp4"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.no_model"

    def test_local_file_starts_job(self, ipc_server, fake_service, monkeypatch, tmp_path):
        import voice_typer.server.media_ingest.jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "_MANAGER", None)
        started = {}

        class _Mgr:
            def start(self, source, runner):
                started["source"] = source

                class _J:
                    job_id = "abc123"
                    status = "running"

                return _J()

        monkeypatch.setattr(jobs_mod, "get_job_manager", lambda: _Mgr())
        f = tmp_path / "clip.wav"
        f.write_bytes(b"RIFF")
        resp = ipc_server._handle_media_transcribe_start({"source": str(f)}, {})
        assert resp["type"] == "media_transcribe_result"
        assert resp["data"]["job_id"] == "abc123"
        assert started["source"] == str(f)


class TestMediaCancelStatus:
    def test_cancel_idle(self, ipc_server, fake_service, monkeypatch):
        import voice_typer.server.media_ingest.jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "_MANAGER", None)
        resp = ipc_server._handle_media_transcribe_cancel({}, {})
        assert resp["type"] == "media_transcribe_result"
        assert resp["data"] == {"cancelled": False}

    def test_status_idle(self, ipc_server, fake_service, monkeypatch):
        import voice_typer.server.media_ingest.jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "_MANAGER", None)
        resp = ipc_server._handle_media_transcribe_status({}, {})
        assert resp["type"] == "media_transcribe_result"
        assert resp["data"] == {"job": None}


class TestMediaUrlStart:
    def test_url_resolves_then_starts_job(self, ipc_server, fake_service, monkeypatch):
        import voice_typer.server.media_ingest.jobs as jobs_mod
        import voice_typer.server.media_ingest.runtime as js_runtime
        import voice_typer.server.media_ingest.url_resolver as resolver

        monkeypatch.setattr(jobs_mod, "_MANAGER", None)
        monkeypatch.setattr(
            resolver,
            "resolve_media_url",
            lambda url, factory=None, js_runtimes=None: resolver.ResolvedMedia(audio_url="https://cdn.example/a.m4a"),
        )
        monkeypatch.setattr(js_runtime, "find_js_runtime", lambda **kwargs: None)
        started = {}

        class _Mgr:
            def start(self, source, runner):
                started["source"] = source

                class _J:
                    job_id = "url123"
                    status = "running"

                return _J()

        monkeypatch.setattr(jobs_mod, "get_job_manager", lambda: _Mgr())
        resp = ipc_server._handle_media_transcribe_start({"source": "https://example.com/v"}, {})
        assert resp["type"] == "media_transcribe_result"
        assert resp["data"]["job_id"] == "url123"
        assert started["source"] == "https://example.com/v"

    def test_missing_js_runtime_has_actionable_error(self, ipc_server, fake_service, monkeypatch):
        import voice_typer.server.media_ingest.jobs as jobs_mod
        import voice_typer.server.media_ingest.runtime as js_runtime
        import voice_typer.server.media_ingest.url_resolver as resolver
        from voice_typer.server.media_ingest.errors import RESOLVE_FAILED, MediaIngestError

        monkeypatch.setattr(jobs_mod, "_MANAGER", None)

        def _boom(url, factory=None, js_runtimes=None):
            raise MediaIngestError(RESOLVE_FAILED, "boom")

        monkeypatch.setattr(resolver, "resolve_media_url", _boom)
        monkeypatch.setattr(js_runtime, "find_js_runtime", lambda **kwargs: None)
        resp = ipc_server._handle_media_transcribe_start({"source": "https://www.youtube.com/watch?v=x"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.not_supported"
