"""Media-transcribe IPC handlers: start / cancel / status (ADR-0023)."""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    ResponseEnvelope,
    _error_response,
    _validate_dict_payload,
)

# ADR-0023 E9 tier-0 budget: first stream open plus retry room for one
# re-resolve (URL_EXPIRED) / seek-resume (STREAM_STALLED) before the
# temp-download fallback tier.
MAX_STREAM_ATTEMPTS = 3


class MediaHandlersMixin(HandlerBase):
    """Mixin: local-file and URL media transcription jobs (ADR-0023)."""

    def _handle_media_transcribe_start(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        def body(d: dict) -> dict:
            validated, error = _validate_dict_payload(
                d,
                {
                    "source": {"type": str, "required": True, "max_value_len": 4096},
                    "export_path": {"type": (str, type(None)), "required": False, "default": None},
                    "export_format": {"type": (str, type(None)), "required": False, "default": None},
                    # ADR-0023 E13: subtitle fast-path is OPT-IN (default off).
                    "use_subtitles": {"type": bool, "required": False, "default": False},
                },
            )
            if error:
                return error
            assert validated is not None
            from voice_typer.server.media_ingest.errors import JOB_BUSY, MediaIngestError
            from voice_typer.server.media_ingest.jobs import MediaJob, get_job_manager
            from voice_typer.server.media_ingest.sources import classify_source

            raw = str(validated["source"]).strip()
            try:
                classified = classify_source(raw)
            except ValueError:
                return _error_response(resp, "missing source", code=ErrorCodes.MISSING_FIELD)
            if classified.kind == "url":
                return self._start_url_media_job(classified.raw, validated, resp)
            if not self._media_model_selected():
                return _error_response(
                    resp, "no speech model installed (open Models to choose one)", code=ErrorCodes.NO_MODEL
                )
            manager = get_job_manager()

            def _runner(job: MediaJob) -> dict:
                return self._run_local_media_job(job, classified.raw, validated)

            try:
                job = manager.start(classified.raw, _runner)
            except MediaIngestError as exc:
                if exc.code == JOB_BUSY:
                    return _error_response(resp, "a transcription job is already running", code=ErrorCodes.JOB_BUSY)
                raise
            return {"type": "media_transcribe_result", "data": {"job_id": job.job_id, "status": "running"}}

        return self._wrap(
            cmd_name="media_transcribe_start", resp_type="media_transcribe_result", data=data, resp=resp, body=body
        )

    def _start_url_media_job(self, url: str, validated: dict, resp: ResponseEnvelope) -> dict:
        """Consent-gate, resolve a remote URL, then stream or download only its audio."""
        from voice_typer.server.asr_errors import ConsentRequiredError
        from voice_typer.server.media_ingest import runtime as _runtime, url_resolver as _resolver
        from voice_typer.server.media_ingest.errors import JOB_BUSY, RESOLVE_FAILED, MediaIngestError
        from voice_typer.server.media_ingest.jobs import MediaJob, get_job_manager

        if not getattr(self.app.config, "media_url_consent", False):
            raise ConsentRequiredError(
                "media URL consent required to transcribe a link",
                engine_name="media_ingest",
                consent_field="media_url_consent",
            )
        if not self._media_model_selected():
            return _error_response(
                resp, "no speech model installed (open Models to choose one)", code=ErrorCodes.NO_MODEL
            )
        manager = get_job_manager()
        runtime = _runtime.find_js_runtime()
        try:
            resolved = _resolver.resolve_media_url(
                url,
                js_runtimes=_runtime.js_runtime_options(runtime),
            )
        except MediaIngestError as exc:
            if exc.code == RESOLVE_FAILED and "youtube" in url.lower() and runtime is None:
                return _error_response(
                    resp,
                    "YouTube needs a JavaScript runtime; install Deno",
                    code=ErrorCodes.NOT_SUPPORTED,
                )
            raise
        if resolved.is_live:
            return _error_response(resp, "live streams are not supported", code=ErrorCodes.NOT_SUPPORTED)

        def _runner(job: MediaJob) -> dict:
            return self._run_url_media_job(job, resolved, runtime, validated)

        try:
            job = manager.start(url, _runner)
        except MediaIngestError as exc:
            if exc.code == JOB_BUSY:
                return _error_response(resp, "a transcription job is already running", code=ErrorCodes.JOB_BUSY)
            raise
        return {"type": "media_transcribe_result", "data": {"job_id": job.job_id, "status": "running"}}

    def _resolve_media(self, url: str, runtime):
        """Re-resolve a URL to a fresh signed audio URL after expiry."""
        from voice_typer.server.media_ingest import runtime as _runtime, url_resolver as _resolver

        return _resolver.resolve_media_url(
            url,
            js_runtimes=_runtime.js_runtime_options(runtime),
        )

    def _finish_media_job(
        self, job, text: str, *, duration: float | None = None, partial: bool = False, validated: dict
    ) -> dict:
        """Persist + export the final transcript and publish completion.

        Empty output (silent audio, or a cancel before the first window
        flushed) skips History so no junk row is created; the completion
        event then carries ``row_id: null`` so the renderer still leaves
        the running state.
        """
        from voice_typer.server import event_bus
        from voice_typer.server.media_ingest import storage as _storage

        stripped = text.strip()
        row_id: int | None = None
        if stripped:
            row_id = _storage.persist_result(
                self.app,
                stripped,
                duration=duration if duration is not None else 0.0,
                model=str(getattr(self.app.config, "model_size", "") or ""),
                device=str(getattr(self.app.config, "device", "") or ""),
                partial=partial,
            )
            export_path = validated.get("export_path")
            if isinstance(export_path, str) and export_path:
                _storage.export_text(stripped, export_path, fmt=str(validated.get("export_format") or "txt"))
        try:
            event_bus.publish(
                {
                    "type": "media_transcribe_complete",
                    "data": {"job_id": job.job_id, "row_id": row_id, "chars": len(stripped), "partial": partial},
                }
            )
        except Exception:  # noqa: BLE001, best-effort
            log.debug("[MEDIA] complete publish failed", exc_info=True)
        return {"row_id": row_id, "chars": len(stripped)}

    def _run_local_media_job(self, job, path: str, validated: dict) -> dict:
        """Background body: lazy-load model, decode, window-transcribe, persist."""
        from voice_typer.server.media_ingest import decoder as _decoder, engine_loop as _loop

        self._publish_media_progress(job, 0.0, "loading_model")
        backend = self._ensure_media_backend()
        chunks = _decoder.decode_chunks(path)
        state = _loop.transcribe_windows(
            chunks,
            backend,
            cancel_event=job.cancel_event,
            on_progress=lambda frac, eta: self._publish_media_progress(job, frac, "transcribing", eta),
        )
        return self._finish_media_job(
            job,
            state.text,
            duration=state.decoded_seconds,
            partial=job.cancel_event.is_set(),
            validated=validated,
        )

    def _run_url_media_job(self, job, resolved, runtime, validated: dict) -> dict:
        """URL job body: subtitles fast path, then tiered audio fallback (ADR-0023 E9/E10/E12/E15)."""
        from voice_typer.server.media_ingest import (
            decoder as _decoder,
            downloader as _downloader,
            engine_loop as _loop,
            runtime as _runtime,
            subtitles as _subtitles,
        )
        from voice_typer.server.media_ingest.errors import (
            DRM_REFUSED,
            STREAM_STALLED,
            URL_EXPIRED,
            MediaIngestError,
        )

        # E12/E13: official-subtitles fast path is OPT-IN (author-provided
        # captions; the UI states the accuracy tradeoff).
        if resolved.subtitles_available and bool(validated.get("use_subtitles")):
            lang = str(getattr(self.app.config, "language", "") or "").strip() or "en"
            try:
                text = _subtitles.fetch_subtitles(resolved.webpage_url, lang=lang)
            except Exception:  # noqa: BLE001, fast path degrades to audio
                log.debug("[MEDIA] subtitle fetch failed, falling back to audio", exc_info=True)
                text = None
            if text:
                return self._finish_media_job(job, text, validated=validated)

        # E15: the model lazy-loads only once audio work is unavoidable.
        self._publish_media_progress(job, 0.0, "loading_model")
        backend = self._ensure_media_backend()

        state = _loop.WindowsState()
        last_error: MediaIngestError | None = None
        for attempt in range(MAX_STREAM_ATTEMPTS):
            if job.cancel_event.is_set():
                break
            try:
                chunks = _decoder.decode_chunks(
                    resolved.audio_url,
                    remote=True,
                    start_seconds=state.decoded_seconds,
                )
                state = _loop.transcribe_windows(
                    chunks,
                    backend,
                    cancel_event=job.cancel_event,
                    on_progress=lambda frac, eta, _d=resolved.duration: self._publish_media_progress(
                        job, frac, "transcribing", eta, _d
                    ),
                    total_seconds=resolved.duration,
                    state=state,
                )
                return self._finish_media_job(
                    job,
                    state.text,
                    duration=resolved.duration,
                    partial=job.cancel_event.is_set(),
                    validated=validated,
                )
            except MediaIngestError as exc:
                if job.cancel_event.is_set():
                    break
                if exc.code == DRM_REFUSED:
                    raise
                last_error = exc
                log.warning("[MEDIA] stream attempt %d/%d failed (%s)", attempt + 1, MAX_STREAM_ATTEMPTS, exc.code)
                if exc.code == URL_EXPIRED and attempt + 1 < MAX_STREAM_ATTEMPTS:
                    try:
                        resolved = self._resolve_media(resolved.webpage_url, runtime)
                    except MediaIngestError as resolve_exc:
                        last_error = resolve_exc
                        break
                continue
        if job.cancel_event.is_set():
            return self._finish_media_job(
                job, state.text, duration=resolved.duration, partial=True, validated=validated
            )

        # Tier-2 (E9): download the audio once, decode from disk.
        if last_error is not None and last_error.code in (STREAM_STALLED, URL_EXPIRED):
            self._publish_media_progress(job, 0.0, "downloading")
            try:
                with _downloader.temp_audio_download(
                    resolved.webpage_url,
                    js_runtimes=_runtime.js_runtime_options(runtime),
                    on_progress=lambda frac: self._publish_media_progress(
                        job, frac, "downloading", None, resolved.duration
                    ),
                ) as tmp_path:
                    chunks = _decoder.decode_chunks(str(tmp_path))
                    state = _loop.transcribe_windows(
                        chunks,
                        backend,
                        cancel_event=job.cancel_event,
                        on_progress=lambda frac, eta: self._publish_media_progress(
                            job, frac, "transcribing", eta, resolved.duration
                        ),
                        total_seconds=resolved.duration,
                        state=state,
                    )
            except MediaIngestError:
                if job.cancel_event.is_set():
                    return self._finish_media_job(
                        job, state.text, duration=resolved.duration, partial=True, validated=validated
                    )
                raise
            return self._finish_media_job(
                job,
                state.text,
                duration=resolved.duration,
                partial=job.cancel_event.is_set(),
                validated=validated,
            )

        assert last_error is not None
        raise last_error

    def _publish_media_progress(
        self,
        job,
        fraction: float,
        phase: str,
        eta_seconds: float | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Best-effort progress event carrying the phase + ETA (ADR-0023 E16)."""
        from voice_typer.server import event_bus

        job.progress = fraction
        try:
            event_bus.publish(
                {
                    "type": "media_transcribe_progress",
                    "data": {
                        "job_id": job.job_id,
                        "progress": fraction,
                        "phase": phase,
                        "eta_seconds": eta_seconds,
                        "duration_seconds": duration_seconds,
                    },
                }
            )
        except Exception:  # noqa: BLE001, best-effort
            log.debug("[MEDIA] progress publish failed", exc_info=True)

    def _handle_media_transcribe_cancel(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        try:
            from voice_typer.server.media_ingest.jobs import get_job_manager

            cancelled = get_job_manager().cancel()
            resp["type"] = "media_transcribe_result"
            resp["data"] = {"cancelled": cancelled}
        except Exception as exc:
            self._respond_with_error(resp, exc, "media_transcribe_cancel")
        return resp

    def _handle_media_transcribe_status(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        try:
            from voice_typer.server.media_ingest.jobs import get_job_manager

            snapshot = get_job_manager().snapshot()
            resp["type"] = "media_transcribe_result"
            resp["data"] = {"job": snapshot}
        except Exception as exc:
            self._respond_with_error(resp, exc, "media_transcribe_status")
        return resp

    def _media_model_selected(self) -> bool:
        """True when a speech model is selected (it may still need a lazy load)."""
        models = getattr(self.app, "models", None)
        if models is None:
            return False
        try:
            if models.active_transcriber() is not None:
                return True
        except Exception:  # noqa: BLE001, probe falls through to config
            log.debug("[MEDIA] active_transcriber probe failed", exc_info=True)
        return bool(str(getattr(self.app.config, "model_size", "") or "").strip())

    def _ensure_media_backend(self):
        """E15: lazy-load the active ASR engine inside the job thread."""
        from voice_typer.server.media_ingest.errors import NO_ENGINE, MediaIngestError

        models = getattr(self.app, "models", None)
        if models is None:
            raise MediaIngestError(NO_ENGINE, "no speech model is loaded (open Models to choose one)")
        ensure = getattr(models, "ensure_active_engine_loaded", None)
        if callable(ensure):
            try:
                ensure()
            except Exception:  # noqa: BLE001, verified by the is_loaded re-check
                log.debug("[MEDIA] lazy model load raised", exc_info=True)
        engine = models.active_transcriber()
        if engine is None or not getattr(engine, "is_loaded", False):
            raise MediaIngestError(NO_ENGINE, "no speech model is loaded (open Models to choose one)")
        return engine
