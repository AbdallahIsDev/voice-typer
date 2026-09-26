"""Consent gate, download dispatch, engine-family downloaders."""

from __future__ import annotations

import logging

from voice_typer.server import segmented_download as segdl
from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.asr_setup import ModelDownloadAborted, check_download_gate
from voice_typer.server.branding import APP_NAME
from voice_typer.server.service._download_helpers import DownloadOutcome

from ._constants import _PARAKEET_REASON_MESSAGES

log = logging.getLogger(__name__)


class DownloadsMixin:
    # Members provided by the composed ``ModelMixin`` (mixin.py);
    _download_queue: list[str]

    def test_llm_connection(self) -> dict[str, object]:
        """Test the LLM polish API connection.

        ``LLMPolisher.test_connection`` was previously
        dead, no IPC route or UI button invoked it.  We now expose
        it via the service layer so the renderer can wire up a "Test
        connection" button on the Settings page (where the user
        configures llm_api_key / llm_api_url / llm_model).

        Returns ``{"success": bool, "message": str}``.
        """
        cfg = getattr(self._app, "config", None)
        if cfg is None:
            return {"success": False, "message": "Config not loaded"}

        #  fix: gate on consent BEFORE sending any test request.
        if not getattr(cfg, "llm_polish_consent", False):
            return {
                "success": False,
                "message": "LLM polish consent not given. Enable LLM polish in Settings to test the connection.",
            }

        # Use the same consent + key-resolution logic as the polish path
        effective_key = getattr(cfg, "llm_api_key", "") or ""
        if not effective_key:
            return {"success": False, "message": "API key not configured"}

        try:
            from voice_typer.server.llm_polish import LLMPolisher

            polisher = LLMPolisher(
                api_key=effective_key,
                api_url=getattr(cfg, "llm_api_url", "") or None,
                model=getattr(cfg, "llm_model", "") or None,
                preset=getattr(cfg, "llm_preset", "professional"),
                enabled=True,
            )
            success, message = polisher.test_connection()
            return {"success": success, "message": message}
        except Exception as exc:
            log.warning("[SERVICE] test_llm_connection failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}

    def cancel_model_download(self, model_name: str | None = None) -> dict:
        """Cancel a model download, the active transfer and/or a queued
        request.

        With ``model_name`` (cancel-anywhere): if that model is waiting
        in the pending download queue, it is REMOVED from the queue
        without touching the active transfer (the queue advances and
        remaining positions are re-pushed as ``download_progress``
        events). If the named model is the ACTIVE download, the active
        cancel path below runs. A name that is neither queued nor
        active is a no-op.

        Without ``model_name`` (the legacy IPC shape): cancels the
         ACTIVE transfer only, queued items stay queued and drain when
        the active transfer exits (the queue's whole point is that the
        next request auto-starts).

        sets the cancellation event so the download_model
        polling loop stops waiting and returns a "cancelled" result.

         signals the active download's per-download
        Event (looked up in ``self._download_cancel_events`` under the
        lock). Without the per-download lookup, two concurrent
        ``download_model`` calls would each overwrite a shared attribute
        and only one would actually get cancelled.

         the legacy single-instance
        ``self._download_cancel_event`` fallback branch has been REMOVED.
        All cancel signals now flow through the per-download dict.

        ALSO signals the transfer gate (:func:`asr_setup.request_download_abort`)
        so the HuggingFace transfer threads unwind at the next chunk
        boundary, pre-fix, cancel only stopped the progress REPORTER
        and the daemon transfer thread kept downloading in the
        background.
        """
        if model_name is not None:
            removed_position = self._remove_queued_download(model_name)
            if removed_position is not None:
                log.info(
                    "[SERVICE] Queued download of '%s' (position %d) removed by user",
                    model_name,
                    removed_position,
                )
                return {"cancelled": True, "model": model_name, "removed_from_queue": True}
            if not self._is_active_download_model(model_name):
                log.debug(
                    "[SERVICE] cancel_model_download(%r): model is neither queued nor active",
                    model_name,
                )
                return {"cancelled": False}
            # Named the ACTIVE model, fall through to the active-cancel
        cancelled_any = False
        # Per-download dict path, signal the
        with self._download_cancel_lock:
            active_id = self._active_download_id
            active_event = self._download_cancel_events.get(active_id) if active_id is not None else None
        if active_event is not None:
            active_event.set()
            cancelled_any = True
        # ALSO signal the transfer gate whenever a gateable download is
        try:
            from voice_typer.server.asr_setup import (
                is_download_active,
                request_download_abort,
            )

            if is_download_active() and request_download_abort():
                cancelled_any = True
        except Exception:
            log.debug(
                "[SERVICE] transfer-gate abort signal failed",
                exc_info=True,
            )
        if cancelled_any:
            log.info("[SERVICE] Model download cancellation requested")
            return {"cancelled": True}
        return {"cancelled": False}

    def get_download_queue(self) -> dict[str, object]:
        """Snapshot of the pending download FIFO queue (front first).

        Read-only: returns the queued model NAMES in drain order under
        the cancel lock. The renderer calls this once on mount to
        hydrate its queue chips (``useModelDownloadQueue``); live
        updates keep flowing through ``download_progress`` events with
        ``queue_position``. An empty list is the normal idle state.
        """
        with self._download_cancel_lock:
            return {"queue": list(self._download_queue)}

    def _enqueue_download(self, model_name: str) -> DownloadOutcome:
        """Queue a download request behind the active gateable transfer.

        Called by the single-flight guards in
        ``_download_whisper_family`` / ``_download_parakeet`` when a
        second download request arrives while a gateable transfer is in
        flight (possibly paused). The queue is the missing UX layer on
        top of the single-flight gate: transfers stay serialized (the
        shared pause/abort events are module-level and MUST NOT be
        recycled under a live transfer), but the request is no longer
        refused with an error, it waits its turn and auto-starts when
        the active transfer exits.

        Holds model NAMES only (unbounded by design, short strings;
        the UI caps display, not storage). Duplicate enqueues are
        idempotent: the model keeps its existing position and the
        queued event is re-pushed so the renderer state refreshes.

        Re-click of the CURRENTLY-DOWNLOADING model does NOT queue: the
        transfer is already in flight, so the already-active outcome is
        returned instead (queueing the model behind itself would drain
        later as a cache-hit no-op transfer once the live one exits).

        Returns the queued outcome (success, the request was accepted)
        and pushes a ``download_progress`` event carrying the new
        ``queue_position`` field so the renderer can render the queued
        state from the existing event stream.
        """
        from voice_typer.server import event_bus
        from voice_typer.server.service._download_helpers import push_progress

        if self._is_active_download_model(model_name):
            # The requested model IS the active download, answer with
            log.info(
                "[SERVICE] Download of '%s' requested while the same model is already downloading",
                model_name,
            )
            return {
                "success": True,
                "model": model_name,
                "download_already_active": True,
                "message": f"Download of {model_name} is already in progress.",
            }
        with self._download_cancel_lock:
            if model_name in self._download_queue:
                position = self._download_queue.index(model_name) + 1
                already_queued = True
            else:
                self._download_queue.append(model_name)
                position = len(self._download_queue)
                already_queued = False
        log.info(
            "[SERVICE] Download of '%s' %s (position %d), another gateable download is active",
            model_name,
            "already queued" if already_queued else "queued",
            position,
        )
        push_progress(
            event_bus,
            model_name,
            0,
            f"Download of {model_name} already queued" if already_queued else f"Download of {model_name} queued",
            queue_position=position,
        )
        return {
            "success": True,
            "queued": True,
            "model": model_name,
            "queue_position": position,
            "message": "Queued, it starts automatically when the current download finishes.",
        }

    def _remove_queued_download(self, model_name: str) -> int | None:
        """Remove ``model_name`` from the pending queue; return the
        1-based position it held (``None`` when it was not queued).

        After a removal the remaining items advance, their refreshed
        positions are re-pushed as ``download_progress`` events so the
        renderer's queue state stays accurate.
        """
        with self._download_cancel_lock:
            if model_name not in self._download_queue:
                return None
            position = self._download_queue.index(model_name) + 1
            self._download_queue.remove(model_name)
            remaining = list(self._download_queue)
        self._push_queue_positions(remaining)
        return position

    def _push_queue_positions(self, names: list[str] | None = None) -> None:
        """Re-push the current ``queue_position`` for the given (or all)
        queued models.

        Only models still present in the queue get an event (the caller
        may pass a pre-removal snapshot; entries already re-queued or
        removed are skipped).
        """
        from voice_typer.server import event_bus
        from voice_typer.server.service._download_helpers import push_progress

        with self._download_cancel_lock:
            snapshot = list(self._download_queue) if names is None else [n for n in names if n in self._download_queue]
        for index, name in enumerate(snapshot, start=1):
            push_progress(
                event_bus,
                name,
                0,
                f"Download of {name} queued",
                queue_position=index,
            )

    def _is_active_download_model(self, model_name: str) -> bool:
        """True when ``model_name`` is the model of the ACTIVE download.

        ``_active_download_id`` is ``f"{model_name}:{hex}"``, the
        registered id is prefixed with the model name, so a prefix
        match on the ``:`` boundary identifies the active model.
        """
        with self._download_cancel_lock:
            active_id = self._active_download_id
        if active_id is None:
            return False
        return active_id.split(":", 1)[0] == model_name

    def _start_next_queued_download(self) -> None:
        """Auto-start the next queued download once the gate is free.

        Called from the ``download_model`` dispatcher's ``finally`` (so
        every exit, success, failure, cancel, advances the queue) and
        safe to call at any time: while a gateable transfer is still
        active it does nothing (the live download's own exit path will
        drain later). The next download runs on its OWN daemon thread
        because no IPC executor is waiting on queued requests (the
        caller's promise already resolved with the queued outcome).

        Self-healing by construction: if a concurrent download arms the
        gate between the check and the pop, the spawned thread's
        ``download_model`` hits the single-flight guard and re-queues
        the model, the next drain cycle picks it up again.
        """
        from voice_typer.server.asr_setup import is_download_active

        if is_download_active():
            return
        with self._download_cancel_lock:
            if not self._download_queue:
                return
            model_name = self._download_queue.pop(0)
            remaining = list(self._download_queue)
        # Refresh the remaining positions (outside the lock, it pushes
        self._push_queue_positions(remaining)
        import threading

        t = threading.Thread(
            target=self._queued_download_runner,
            args=(model_name,),
            name=f"model-download-queue-{model_name}",
            daemon=True,
        )
        t.start()

    def _queued_download_runner(self, model_name: str) -> None:
        """Thread body for a drained queued download (never raises, an
        exception in a daemon thread would silently drop the request)."""
        try:
            self.download_model(model_name)
        except Exception:
            log.exception("[SERVICE] Queued download of '%s' failed", model_name)

    def pause_model_download(self) -> dict:
        """Pause an in-progress model download.

        delegates to :func:`asr_setup.set_download_paused`.
        The transfer gate (:func:`asr_setup.get_download_tqdm_class`) parks
        the HuggingFace transfer thread at the next chunk boundary, so
        bytes genuinely stop flowing (pre-fix the pause only froze the
        progress REPORTER while the transfer ran to completion in the
        background). The polling loop pushes the ``paused: True``
        transition event, which the renderer renders as its amber
        "paused" state.
        """
        from voice_typer.server.asr_setup import set_download_paused

        paused = set_download_paused(True)
        if paused:
            log.info("[SERVICE] Model download pause requested")
        return {"paused": paused}

    def resume_model_download(self) -> dict:
        """Resume a paused model download.

        clears the module-level pause flag set by
        :meth:`pause_model_download`. The transfer gate unblocks the
        parked transfer thread at its next chunk boundary and the
        download continues (huggingface_hub re-requests with a Range
        header if the idle HTTP connection died during the pause).

        Mirrors the pause path: the boolean from
        :func:`asr_setup.set_download_paused` is forwarded so a no-op
        resume (no live download) returns ``{"resumed": False}`` and
        the renderer can revert its optimistic flip.
        """
        from voice_typer.server.asr_setup import set_download_paused

        resumed = set_download_paused(False)
        if resumed:
            log.info("[SERVICE] Model download resume requested")
        return {"resumed": resumed}

    def _require_huggingface_consent(self, model_name: str) -> DownloadOutcome | None:
        """Gate IPC-triggered HuggingFace downloads on explicit consent.

        Mirrors the consent gate in
        :meth:`voice_typer.server.transcription.TranscriptionEngine._pre_download_model`
        (transcription.py:835-849).  The IPC download path previously
        had NO consent check, so clicking "Download" on the Models page
        phoned home to huggingface.co (revealing the user's IP to a
        US-headquartered third party) without the explicit GDPR
        Art. 13/44 consent that ``config.huggingface_consent`` was
        specifically designed to gate ().

        Returns ``None`` when consent has been given, the caller
        proceeds with the download.  Returns a :data:`DownloadOutcome`
        failure dict AND publishes a ``consent_required`` event when
        consent is missing; the renderer is responsible for showing
        the consent dialog and retrying the download after the user
        accepts.

        Defensive: ``self._app.config`` may be ``None`` in degenerate
        paths (test stubs, benchmark harness).  Treat missing config
        as NOT consented, safe default per GDPR Art. 6/13.

        Returns a :data:`DownloadOutcome` (TypedDict) so the caller's
        ``return consent_err`` line type-checks without
        ``# type: ignore[return-value]``. The returned dict's runtime
        shape is preserved verbatim (``success``, ``error``,
        ``consent_required``, ``model``).
        """
        from voice_typer.server import event_bus

        cfg = getattr(self._app, "config", None)
        consent = False if cfg is None else bool(getattr(cfg, "huggingface_consent", False))
        if not consent:
            log.warning(
                "[SERVICE] HuggingFace consent not given, refusing to download "
                "model '%s' via IPC. The renderer should show the consent dialog.",
                model_name,
            )
            try:
                event_bus.publish(
                    {
                        "type": "consent_required",
                        "data": {
                            "provider": "huggingface",
                            "model": model_name,
                            "message": "HuggingFace consent required before downloading model.",
                        },
                    }
                )
            except Exception:
                log.debug("[SERVICE] consent_required event push failed", exc_info=True)
            return {
                "success": False,
                "error": "HuggingFace consent required",
                "consent_required": True,
                "model": model_name,
            }
        return None

    def download_model(self, model_name: str) -> dict[str, object]:
        """Download a model weight file via HuggingFace.

        Downloads the specified model (tiny, large-v3-turbo,
        large-v3, qwen, parakeet) to the local HF cache. Pushes
        ``download_progress`` events to the renderer so the Models page
        can update its progress bar and status text in real time, and
        fires a tray notification on completion / failure.
        Returns a result dict with success status.

        the return annotation is widened from the
        ``DownloadResult`` TypedDict union (removed) to
        ``dict[str, object]`` to match the actual runtime shape. The
        implementation returns plain ``dict`` literals (not TypedDict
        instances); the TypedDict union gave no real protection and
        caused 3 baselined ``bad-return`` pyrefly errors. The runtime
        shape is verified by ``tests/test_service_fixes.py``.

        now supports the turbo + distilled variants via
        :mod:`voice_typer.server.model_registry`.  The repo_id is
        resolved from the registry instead of being hard-coded.

        the polling loop checks
        :func:`asr_setup.is_download_paused` between iterations.  When
        paused, progress updates freeze and a ``paused: True`` event is
        pushed once per transition.  Resume clears the flag and pushes
        a ``resumed: True`` event.

        the Whisper and Parakeet branches now gate on
        :meth:`_require_huggingface_consent` before any HuggingFace
        network call, mirroring the consent gate that already lived in
        ``TranscriptionEngine._pre_download_model`` (transcription.py:835-849).
        The Qwen branch uses a local file path and does not phone home,
        so it is exempt from the consent gate.

        daemon=True is acceptable because _do_download only
        writes to the HF cache dir, no critical cleanup. The download
        completes or fails naturally; on force-kill the partial
        download is resumed on next start via HF's resume_download=True.

        the original 558-LOC god method has been split into a
        ~40-LOC dispatcher (this method) plus three branch methods
        (``_download_whisper_family``, ``_download_qwen``,
        ``_download_parakeet``).  The shared helpers
        (:func:`push_progress`, :func:`notify`,
        :func:`poll_download_progress`) live in
        :mod:`voice_typer.server.service._download_helpers`.  Each
        branch returns a :data:`DownloadOutcome` TypedDict; the
        dispatcher converts it to a plain ``dict`` via ``dict(outcome)``
        so the IPC layer sees the exact same runtime shape as before.
        All 10 distinct return shapes are preserved verbatim.

        The progress-polling loop (delegated to
        :func:`poll_download_progress` in
        :mod:`voice_typer.server.service._download_helpers`) walks
        ONLY the per-repo subdir to keep I/O bounded::

            model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"
            ... = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())

        (Regression guard, kept as a docstring snippet so the
        ``tests/test_perf_fixes.py::TestDownloadPollScopedToModelDir``
        source-pin still trips if a future refactor re-widens the
        rglob to walk the whole ``cache_dir``.)
        """
        try:
            # consult the model registry so we support
            from voice_typer.server.model_registry import get_model_metadata

            model_meta = get_model_metadata(model_name)
            is_whisper_family = model_meta is not None and model_meta.backend in ("whisper", "distil-whisper")
            if is_whisper_family:
                outcome = self._download_whisper_family(model_name, model_meta)
            elif model_name == "qwen":
                outcome = self._download_qwen(model_name)
            elif model_name == "parakeet":
                outcome = self._download_parakeet(model_name)
            else:
                log.warning(
                    "[SERVICE] Unknown model requested for download: '%s'",
                    model_name,
                )
                return {
                    "success": False,
                    "model": model_name,
                    "error": f"Unknown model: {model_name}",
                }
            return dict(outcome)  # Convert TypedDict to regular dict for IPC
        except ModelDownloadAborted:
            # An abort unwinding the transfer surfaces here as a
            log.info(
                "[SERVICE] Download of '%s' aborted via transfer gate",
                model_name,
            )
            try:
                from voice_typer.server.asr_setup import clear_download_pause_state

                clear_download_pause_state()
            except Exception:
                log.debug("[SERVICE] could not clear pause flag on abort", exc_info=True)
            return {
                "success": False,
                "model": model_name,
                "cancelled": True,
                "message": f"Download of {model_name} cancelled. Partial files remain in cache; retry to resume.",
            }
        except Exception as exc:
            log.exception("download_model failed for %s: %s", model_name, exc)
            # The per-download Event cleanup is handled by the
            try:
                from voice_typer.server.asr_setup import clear_download_pause_state

                clear_download_pause_state()
            except Exception:
                log.debug("[SERVICE] could not clear pause flag on failure", exc_info=True)
            from voice_typer.server import event_bus
            from voice_typer.server.service._download_helpers import (
                notify as _notify_helper,
                push_progress as _push_progress_helper,
            )

            _push_progress_helper(event_bus, model_name, 0, f"Download failed: {redact_secret(redact_url(str(exc)))}")
            _notify_helper(
                self._app.tray,
                model_name,
                APP_NAME,
                f"Failed to download {model_name}: {redact_secret(redact_url(str(exc)))}",
            )
            return {
                "success": False,
                "model": model_name,
                "error": redact_secret(redact_url(str(exc))),
            }
        finally:
            # Queue drain: EVERY exit path (success, failure, cancel,
            try:
                self._start_next_queued_download()
            except Exception:
                log.debug("[SERVICE] download-queue drain failed (non-fatal)", exc_info=True)

    def _download_whisper_family(self, model_name: str, model_meta) -> DownloadOutcome:
        """Whisper / distil-whisper branch of :meth:`download_model`.

        extracted from the original ``is_whisper_family`` branch
        of the monolithic ``download_model``.  Handles the
        HuggingFace consent gate, the  pause/resume state
        machine (via :func:`poll_download_progress` in Phase A and
        :func:`make_segmented_progress_tracker` in Phase B, with the
        shared pause/abort events kept alive across the handoff),
        and the per-download cancellation plumbing.

        Takes explicit args (``model_name``, ``model_meta``) so it can
        be unit-tested in isolation. Returns a :data:`DownloadOutcome`
        TypedDict with the same runtime shape the original branch
        produced.
        """
        # SINGLE-FLIGHT GUARD: only one gateable download may run at a
        from voice_typer.server.asr_setup import is_download_active

        if is_download_active():
            return self._enqueue_download(model_name)
        from voice_typer.server import event_bus
        from voice_typer.server.service._download_helpers import (
            notify as _notify,
            poll_download_progress,
            push_progress as _push_progress,
        )

        # HuggingFace consent gate.  Without this check,
        consent_err = self._require_huggingface_consent(model_name)
        if consent_err is not None:
            return consent_err
        log.info(
            "[SERVICE] Starting download for '%s' (repo=%s, backend=%s)",
            model_name,
            model_meta.repo_id if model_meta else "unknown",
            model_meta.backend if model_meta else "unknown",
        )
        # reset the pause + abort flags at the start of
        from voice_typer.server.asr_setup import (
            clear_download_pause_state,
            force_http_download_path,
            get_download_tqdm_class,
            reset_download_pause_state,
        )

        reset_download_pause_state()
        force_http_download_path()

        _push_progress(event_bus, model_name, 0, f"Starting download for {model_name}...")
        # pre-download via snapshot_download so we can
        download_id: str | None = None
        try:
            from huggingface_hub import snapshot_download

            from voice_typer.server import model_availability as _ma
            from voice_typer.server.config import _config_dir

            # use the registry's repo_id so
            assert model_meta is not None  # narrowed by is_whisper_family
            repo_id = model_meta.repo_id
            cache_dir = _ma.shared_hub_dir()

            # SEC-audit-005: Allowlist of file patterns permitted in downloads
            from voice_typer.server._model_integrity import (
                ALLOW_PATTERNS_WHISPER as SERVICE_ALLOW_PATTERNS_WHISPER,
            )
            from voice_typer.server.security import MODEL_HASHES

            _service_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

            _push_progress(event_bus, model_name, 5, f"Checking cache for {model_name}...")
            # Try local-only first; if cached, skip the polling.
            try:
                try:
                    snapshot_download(
                        repo_id=repo_id,
                        revision=_service_revision,
                        allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                        local_files_only=True,
                    )
                except Exception:
                    snapshot_download(
                        repo_id=repo_id,
                        revision=_service_revision,
                        allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                        local_files_only=True,
                        cache_dir=str(_ma.app_hub_dir(_config_dir())),
                    )
                log.info(
                    "[SERVICE] Model '%s' already cached (repo=%s), skipping download",
                    model_name,
                    repo_id,
                )
                # Status-only event at a NON-terminal percent: the single
                _push_progress(event_bus, model_name, 5, f"{model_name} already cached")
            except Exception:
                # pull target size from the
                target_mb = model_meta.download_size_mb if model_meta.download_size_mb else 500
                target_bytes = target_mb * 1024 * 1024
                # Segmented fast lane: big, pinned files download as
                from voice_typer.server.security import MODEL_HASHES as _MH

                seg_plan = segdl.plan_segmented_files(
                    repo_id=repo_id,
                    revision=_service_revision,
                    allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                    file_hashes=(_MH.get(repo_id, {}) or {}).get("files", {}),
                )
                seg_names = [p.filename for p in seg_plan] if seg_plan else []
                _push_progress(
                    event_bus,
                    model_name,
                    10,
                    f"Downloading {model_name} from HuggingFace...",
                    total_bytes=target_bytes,
                )
                # Start the download in a thread so we can poll
                import threading

                # Register a per-download
                download_id = self._register_download(model_name)
                download_err: list = []

                def _do_download():
                    try:
                        # use retry-with-backoff wrapper
                        from voice_typer.server.transcription import _download_with_retry

                        _download_with_retry(
                            snapshot_download,
                            repo_id=repo_id,
                            revision=_service_revision,
                            allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                            # Segmented fast lane owns the big files —
                            ignore_patterns=seg_names or None,
                            resume_download=True,
                            # pause/abort gate: intercepts every ~10 MB
                            tqdm_class=get_download_tqdm_class(),
                        )
                    except BaseException as e:
                        # ModelDownloadAborted is a BaseException, catch
                        download_err.append(e)

                # daemon=True is acceptable because
                t = threading.Thread(target=_do_download, daemon=True)
                t.start()
                log.info(
                    "[SERVICE] Download thread started for '%s' (target=%d MB)",
                    model_name,
                    target_mb,
                )
                # Poll cache size until download thread exits OR
                try:
                    poll_outcome, last_total_bytes_seen = poll_download_progress(
                        thread=t,
                        target_bytes=target_bytes,
                        target_mb=target_mb,
                        model_name=model_name,
                        repo_id=repo_id,
                        cache_dir=cache_dir,
                        download_id=download_id,
                        event_bus=event_bus,
                        is_cancelled_fn=self._is_download_cancelled,
                    )
                finally:
                    # Remove our per-download Event
                    self._unregister_download(download_id)
                # if cancelled, return early.
                if poll_outcome == "cancelled":
                    clear_download_pause_state()
                    return {
                        "success": False,
                        "model": model_name,
                        "cancelled": True,
                        "message": f"Download of {model_name} cancelled. "
                        "Partial files remain in cache; "
                        "retry to resume.",
                    }
                if download_err:
                    # B904: suppress context from the failed
                    first_err = download_err[0]
                    if isinstance(first_err, ModelDownloadAborted):
                        # The user cancelled: the transfer gate unwound the
                        log.info(
                            "[SERVICE] Download of '%s' aborted via transfer gate",
                            model_name,
                        )
                        clear_download_pause_state()
                        return {
                            "success": False,
                            "model": model_name,
                            "cancelled": True,
                            "message": f"Download of {model_name} cancelled. "
                            "Partial files remain in cache; "
                            "retry to resume.",
                        }
                    raise download_err[0] from None
                # Phase B, segmented fast lane for the big files (runs on
                if seg_plan:
                    try:
                        from voice_typer.server.asr_setup import (
                            is_download_paused as _seg_is_paused,
                        )
                        from voice_typer.server.service._download_helpers import (
                            make_segmented_progress_tracker as _make_seg_tracker,
                        )

                        try:
                            from huggingface_hub.utils import get_token as _get_token

                            _token = _get_token()
                        except Exception:
                            _token = None
                        _headers = {"Authorization": f"Bearer {_token}"} if _token else {}
                        try:
                            from voice_typer.server.service.offline_pack import (
                                proxy_env as _proxy_env,
                            )

                            _proxies = _proxy_env()
                        except Exception:
                            _proxies = None

                        _big_total = sum(p.size for p in seg_plan)
                        # Pause-aware progress tracker (owns the
                        _on_seg_progress = _make_seg_tracker(
                            event_bus=event_bus,
                            model_name=model_name,
                            target_mb=target_mb,
                            target_bytes=target_bytes,
                            phase_total_bytes=_big_total,
                            is_paused_fn=_seg_is_paused,
                        )

                        segdl.run_segmented_phase(
                            model_name=model_name,
                            repo_id=repo_id,
                            commit=_service_revision,
                            cache_dir=cache_dir,
                            seg_plan=seg_plan,
                            progress_cb=_on_seg_progress,
                            gate_check=check_download_gate,
                            headers=_headers,
                            proxies=_proxies,
                        )
                    except ModelDownloadAborted:
                        log.info(
                            "[SERVICE] Download of '%s' aborted via transfer gate",
                            model_name,
                        )
                        clear_download_pause_state()
                        return {
                            "success": False,
                            "model": model_name,
                            "cancelled": True,
                            "message": f"Download of {model_name} cancelled. "
                            "Partial files remain in cache; "
                            "retry to resume.",
                        }
                    except segdl.SegmentedDownloadError as e:
                        # Failover, not failure: anything the segmented
                        log.warning(
                            "[SERVICE] Segmented fast lane failed for '%s' (%s), falling back to classic download",
                            model_name,
                            e,
                        )
                        _push_progress(
                            event_bus,
                            model_name,
                            10,
                            f"Retrying {model_name} with standard download...",
                            total_bytes=target_bytes,
                        )
                        from voice_typer.server.transcription import (
                            _download_with_retry as _retry_classic,
                        )

                        _retry_classic(
                            snapshot_download,
                            repo_id=repo_id,
                            revision=_service_revision,
                            allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                            resume_download=True,
                            tqdm_class=get_download_tqdm_class(),
                        )
                    # Self-verify the assembled snapshot by HF's own
                    try:
                        snapshot_download(
                            repo_id=repo_id,
                            revision=_service_revision,
                            allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                            local_files_only=True,
                        )
                    except Exception as e:
                        log.warning(
                            "[SERVICE] Post-segmented snapshot probe failed "
                            "for '%s' (%s), falling back to classic download",
                            model_name,
                            e,
                        )
                        from voice_typer.server.transcription import (
                            _download_with_retry as _retry_verify,
                        )

                        _retry_verify(
                            snapshot_download,
                            repo_id=repo_id,
                            revision=_service_revision,
                            allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                            resume_download=True,
                            tqdm_class=get_download_tqdm_class(),
                        )
                log.info(
                    "[SERVICE] Download of '%s' complete (%d MB)",
                    model_name,
                    last_total_bytes_seen // (1024 * 1024),
                )
        except ImportError:
            # huggingface_hub is missing or broken (stripped venv /
            if download_id is not None:
                self._unregister_download(download_id)
            clear_download_pause_state()
            msg = _PARAKEET_REASON_MESSAGES["huggingface_hub_missing"]
            log.exception("[SERVICE] Download of '%s' failed: %s", model_name, msg)
            _push_progress(event_bus, model_name, 0, msg)
            _notify(self._app.tray, model_name, APP_NAME, f"Failed to download {model_name}: {msg}")
            return {
                "success": False,
                "error": msg,
                "reason": "huggingface_hub_missing",
                "model": model_name,
            }

        # VERIFY-LIGHT: skip the expensive full-model load verification.
        log.info("[SERVICE] Download of '%s' verified via HF cache (no full model load)", model_name)
        # Single terminal 100% push per download call: the cache-hit
        _push_progress(event_bus, model_name, 100, f"Download of {model_name} complete")
        # invalidate the tray models submenu cache
        try:
            from voice_typer.server.tray_models import (
                invalidate_model_availability_cache,
            )

            invalidate_model_availability_cache()
        except Exception:
            log.debug(
                "[SERVICE] failed to invalidate tray model cache",
                exc_info=True,
            )
        # Defense-in-depth cleanup. The ``finally:`` block inside the
        if download_id is not None:
            self._unregister_download(download_id)
        # clear the pause flag so subsequent
        clear_download_pause_state()
        _notify(self._app.tray, model_name, APP_NAME, f"Model '{model_name}' downloaded successfully")
        # On-disk model state changed, force the next status recompute.
        self._invalidate_model_status_cache()
        return {"success": True, "model": model_name}

    def _download_qwen(self, model_name: str) -> DownloadOutcome:
        """Qwen branch of :meth:`download_model`.

        extracted from the original ``elif model_name == "qwen"``
        branch of the monolithic ``download_model``.  Qwen uses a local
        file path (no HuggingFace call) so the  consent gate does
        not apply.  Returns a :data:`DownloadOutcome` with the same
        runtime shape the original branch produced.
        """
        import os

        from voice_typer.server import event_bus
        from voice_typer.server.service._download_helpers import (
            notify as _notify,
            push_progress as _push_progress,
        )

        log.info("[SERVICE] Download requested for '%s' (Qwen backend)", model_name)
        qwen_path = getattr(self._app.config, "qwen_model_path", None)
        if qwen_path and os.path.isdir(qwen_path):
            _push_progress(event_bus, model_name, 100, "Qwen model already cached")
            return {"success": True, "model": model_name, "message": "Qwen model already cached"}
        _notify(self._app.tray, model_name, APP_NAME, "Qwen model path not configured")
        return {
            "success": False,
            "model": model_name,
            "error": "Qwen model path not configured. Set qwen_model_path in Settings.",
        }

    def _download_parakeet(self, model_name: str) -> DownloadOutcome:
        """Parakeet branch of :meth:`download_model`.

        extracted from the original ``elif model_name ==
        "parakeet"`` branch of the monolithic ``download_model``.
        Handles the  HuggingFace consent gate and the
        structured-error unpack of ``download_parakeet_weights``.
        Returns a :data:`DownloadOutcome` with the same runtime shape
        the original branch produced.
        """
        # SINGLE-FLIGHT GUARD (same rationale as the whisper branch —
        from voice_typer.server.asr_setup import is_download_active

        if is_download_active():
            return self._enqueue_download(model_name)
        from voice_typer.server import event_bus
        from voice_typer.server.service._download_helpers import (
            notify as _notify,
            push_progress as _push_progress,
        )

        # HuggingFace consent gate.  Parakeet weights
        consent_err = self._require_huggingface_consent(model_name)
        if consent_err is not None:
            return consent_err
        log.info(
            "[SERVICE] Download requested for '%s' (Parakeet backend, ~2.5 GB)",
            model_name,
        )
        _push_progress(event_bus, model_name, 0, "Starting Parakeet download (~2.5 GB)...")
        from voice_typer.server.asr_setup import (
            download_parakeet_weights,
            reset_download_pause_state,
        )

        # Parakeet's transfer gate reads the same shared pause/abort
        reset_download_pause_state()

        # The unpack is defensive: some legacy / test fakes
        def _parakeet_progress(message: str) -> None:
            # Map the function's textual progress messages to
            _push_progress(event_bus, model_name, 50, message)

        _push_progress(event_bus, model_name, 50, "Downloading Parakeet weights from HuggingFace...")
        try:
            dpw_result = download_parakeet_weights(
                config=self._app.config,
                progress_callback=_parakeet_progress,
            )
        finally:
            # Release the gate's pause/abort events on EVERY exit —
            from voice_typer.server.asr_setup import clear_download_pause_state

            clear_download_pause_state()
        # Defensive unpack: handle both the documented 3-tuple
        if isinstance(dpw_result, tuple):
            success, reason, _exc_info = dpw_result
        else:
            success = bool(dpw_result)
            reason = "" if success else "unknown"
        if not success:
            msg = _PARAKEET_REASON_MESSAGES.get(reason, f"Download failed: {reason}")
            log.error(
                "[SERVICE] Parakeet download failed (reason=%s): %s",
                reason,
                msg,
            )
            _push_progress(event_bus, model_name, 0, msg)
            _notify(self._app.tray, model_name, APP_NAME, f"Failed to download {model_name}: {msg}")
            return {
                "success": False,
                "error": msg,
                "reason": reason,
                "model": model_name,
            }
        log.info("[SERVICE] Parakeet download complete")
        _push_progress(event_bus, model_name, 100, "Parakeet download complete")
        # invalidate the tray models submenu cache.
        try:
            from voice_typer.server.tray_models import (
                invalidate_model_availability_cache,
            )

            invalidate_model_availability_cache()
        except Exception:
            log.debug(
                "[SERVICE] failed to invalidate tray model cache",
                exc_info=True,
            )
        _notify(self._app.tray, model_name, APP_NAME, "Parakeet model downloaded successfully")
        return {"success": True, "model": model_name}
