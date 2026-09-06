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
    def test_llm_connection(self) -> dict[str, object]:
        """Test the LLM polish API connection.

        ``LLMPolisher.test_connection`` was previously
        dead — no IPC route or UI button invoked it.  We now expose
        it via the service layer so the renderer can wire up a "Test
        connection" button on the Settings page (where the user
        configures llm_api_key / llm_api_url / llm_model).

        Returns ``{"success": bool, "message": str}``.
        """
        cfg = getattr(self._app, "config", None)
        if cfg is None:
            return {"success": False, "message": "Config not loaded"}

        #  fix: gate on consent BEFORE sending any test request.
        # The polish production path (dictation_pipeline.py:650) requires
        # BOTH `llm_polish` AND `llm_polish_consent` to be True before
        # sending any HTTP request to the LLM endpoint. The previous
        # implementation of test_llm_connection bypassed the consent gate
        # — a user who explicitly denied consent (llm_polish_consent=False)
        # but had an API key configured could trigger an outbound HTTP POST
        # to llm_api_url (with Authorization: Bearer <key> header + the
        # literal "Hello" body) by clicking "Test Connection" in Settings.
        # The request leaks the user's IP, the existence of an active API
        # key, and a Python urllib User-Agent to the configured LLM
        # endpoint, despite explicit user opt-out.
        if not getattr(cfg, "llm_polish_consent", False):
            return {
                "success": False,
                "message": "LLM polish consent not given. Enable LLM polish in Settings to test the connection.",
            }

        # Use the same consent + key-resolution logic as the polish path
        # (dictation_pipeline.py:288-300).
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

    # ── Model import ──────────────────────────────────────────────────────

    def cancel_model_download(self) -> dict:
        """Cancel an in-progress model download.

        sets the cancellation event so the download_model
        polling loop stops waiting and returns a "cancelled" result.

         SERVICE-1: signals the active download's per-download
        Event (looked up in ``self._download_cancel_events`` under the
        lock). Without the per-download lookup, two concurrent
        ``download_model`` calls would each overwrite a shared attribute
        and only one would actually get cancelled.

         the legacy single-instance
        ``self._download_cancel_event`` fallback branch has been REMOVED.
        All cancel signals now flow through the per-download dict.

        ALSO signals the transfer gate (:func:`asr_setup.request_download_abort`)
        so the HuggingFace transfer threads unwind at the next chunk
        boundary — pre-fix, cancel only stopped the progress REPORTER
        and the daemon transfer thread kept downloading in the
        background.
        """
        cancelled_any = False
        #  SERVICE-1: per-download dict path — signal the
        # currently-active download's Event, if any.
        with self._download_cancel_lock:
            active_id = self._active_download_id
            active_event = self._download_cancel_events.get(active_id) if active_id is not None else None
        if active_event is not None:
            active_event.set()
            cancelled_any = True
        # ALSO signal the transfer gate whenever a gateable download is
        # active — the Parakeet path never registers a per-download
        # Event (it downloads synchronously inside the IPC call), so the
        # registry lookup alone could not stop it. The gate raises
        # ModelDownloadAborted at the next chunk boundary (works from a
        # PAUSED state too — the parked gate wakes and unwinds).
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
        """
        from voice_typer.server.asr_setup import set_download_paused

        set_download_paused(False)
        log.info("[SERVICE] Model download resume requested")
        return {"resumed": True}

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

        Returns ``None`` when consent has been given — the caller
        proceeds with the download.  Returns a :data:`DownloadOutcome`
        failure dict AND publishes a ``consent_required`` event when
        consent is missing; the renderer is responsible for showing
        the consent dialog and retrying the download after the user
        accepts.

        Defensive: ``self._app.config`` may be ``None`` in degenerate
        paths (test stubs, benchmark harness).  Treat missing config
        as NOT consented — safe default per GDPR Art. 6/13.

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
                "[SERVICE] HuggingFace consent not given — refusing to download "
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
        writes to the HF cache dir — no critical cleanup. The download
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

        (Regression guard — kept as a docstring snippet so the
        ``tests/test_perf_fixes.py::TestDownloadPollScopedToModelDir``
        source-pin still trips if a future refactor re-widens the
        rglob to walk the whole ``cache_dir``.)
        """
        try:
            # consult the model registry so we support
            # turbo + distilled variants without hard-coding name-to-repo
            # mappings.  Falls back to the legacy hard-coded tuple for
            # any registry drift.
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
            # BaseException (NOT Exception) — map it to the same
            # cancelled outcome the poll-loop path returns so a cancel
            # never reaches the user as an error toast.
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
            # ``finally:`` block in each ``_download_*`` branch method
            # (e.g. ``_download_whisper_family``). The outer
            # ``download_id`` here is always ``None`` — Python does
            # not propagate assignments from nested method scopes —
            # so a previous ``if download_id is not None`` guard was
            # dead code and has been removed.
            # clear the pause flag on failure too.
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

    def _download_whisper_family(self, model_name: str, model_meta) -> DownloadOutcome:
        """Whisper / distil-whisper branch of :meth:`download_model`.

        extracted from the original ``is_whisper_family`` branch
        of the monolithic ``download_model``.  Handles the
        HuggingFace consent gate, the  pause/resume state
        machine (via :func:`poll_download_progress`), and the
        per-download cancellation plumbing ( / SERVICE-1).

        Takes explicit args (``model_name``, ``model_meta``) so it can
        be unit-tested in isolation. Returns a :data:`DownloadOutcome`
        TypedDict with the same runtime shape the original branch
        produced.
        """
        # SINGLE-FLIGHT GUARD: only one gateable download may run at a
        # time (the shared pause/abort events are module-level). Without
        # this, a second download_model IPC — e.g. the renderer's Retry
        # after its promise timed out during a long PAUSE — would start
        # a second transfer and recycle the events underneath the live
        # one (the parked gate would wake and both downloads would run).
        from voice_typer.server.asr_setup import is_download_active

        if is_download_active():
            log.info(
                "[SERVICE] Download of '%s' refused — another download is already in progress (possibly paused)",
                model_name,
            )
            return {
                "success": False,
                "model": model_name,
                "download_already_active": True,
                "error": "Another model download is already in progress (it may be paused). Resume or cancel it first.",
            }
        from voice_typer.server import event_bus
        from voice_typer.server.service._download_helpers import (
            notify as _notify,
            poll_download_progress,
            push_progress as _push_progress,
        )

        # HuggingFace consent gate.  Without this check,
        # clicking "Download" on the Models page would phone
        # home to huggingface.co before the user had explicitly
        # opted in via the consent dialog ().
        # Mirrors TranscriptionEngine._pre_download_model
        # (transcription.py:835-849).  The gate must fire BEFORE
        # any snapshot_download call (including the
        # local_files_only cache probe) so that a user who has
        # NOT consented cannot trigger any HuggingFace Hub
        # interaction from the IPC path.
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
        # every fresh download so stale state from a previous download
        # doesn't carry over, and force the gateable HTTP transfer path
        # (the pause/abort gate lives in the HTTP chunk loop's progress
        # callbacks — the xet path reports from native threads where a
        # blocking callback does not stop the transfer).
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
        # poll the HF cache file size for progress reporting.
        # TranscriptionEngine.load() blocks with no progress
        # callback; doing the snapshot_download first lets us
        # emit progress events, then load() just reads from
        # the local cache.
        download_id: str | None = None
        try:
            from huggingface_hub import snapshot_download

            from voice_typer.server.config import _config_dir

            # use the registry's repo_id so
            # distilled variants (Systran/faster-distil-whisper-*)
            # resolve correctly.
            assert model_meta is not None  # narrowed by is_whisper_family
            repo_id = model_meta.repo_id
            cache_dir = _config_dir() / "huggingface" / "hub"

            # SEC-audit-005: Allowlist of file patterns permitted in downloads
            # (E7: the SAME pinned list the loader's cache probe uses —
            # a duplicated inline list drifted risk). SEC-audit-005 also
            # pins the download revision to the MODEL_HASHES manifest.
            from voice_typer.server._model_integrity import (
                ALLOW_PATTERNS_WHISPER as SERVICE_ALLOW_PATTERNS_WHISPER,
            )
            from voice_typer.server.security import MODEL_HASHES

            _service_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

            _push_progress(event_bus, model_name, 5, f"Checking cache for {model_name}...")
            # Try local-only first; if cached, skip the polling.
            try:
                snapshot_download(
                    repo_id=repo_id,
                    revision=_service_revision,
                    allow_patterns=SERVICE_ALLOW_PATTERNS_WHISPER,
                    local_files_only=True,
                )
                log.info(
                    "[SERVICE] Model '%s' already cached (repo=%s) — skipping download",
                    model_name,
                    repo_id,
                )
                # Status-only event at a NON-terminal percent: the single
                # 100% push for this download call is the shared terminal
                # push below ("Download of ... complete"). Pushing the
                # terminal percent here too made every cache hit emit TWO
                # 100% events; the bar must reach 100 exactly once.
                _push_progress(event_bus, model_name, 5, f"{model_name} already cached")
            except Exception:
                # pull target size from the
                # registry instead of the hard-coded size_targets
                # table.  Falls back to 500 MB if missing.
                target_mb = model_meta.download_size_mb if model_meta.download_size_mb else 500
                target_bytes = target_mb * 1024 * 1024
                # Segmented fast lane: big, pinned files download as
                # concurrent Range segments (see segmented_download);
                # everything else stays on the classic snapshot path.
                # Planning NEVER raises (None/[] = classic for all).
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
                # the cache directory size while it runs.
                import threading

                #  SERVICE-1: register a per-download
                # cancellation Event in the dict (under the
                # lock) instead of overwriting the shared
                # ``self._download_cancel_event`` attribute.
                # Two concurrent download_model calls now each
                # get their own Event keyed by download_id, so
                # neither can clobber the other's reference.
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
                            # exclude them here so both paths never fetch
                            # the same bytes (None == today's behavior).
                            ignore_patterns=seg_names or None,
                            resume_download=True,
                            cache_dir=str(cache_dir),
                            # pause/abort gate: intercepts every ~10 MB
                            # chunk boundary — pause BLOCKS the transfer
                            # thread, cancel raises ModelDownloadAborted
                            # (a BaseException, so the retry wrapper
                            # cannot swallow it and resume downloading).
                            tqdm_class=get_download_tqdm_class(),
                        )
                    except BaseException as e:
                        # ModelDownloadAborted is a BaseException — catch
                        # it here (the thread boundary swallows
                        # BaseExceptions silently) so download_err
                        # carries it for the cancelled-outcome mapping.
                        download_err.append(e)

                # daemon=True is acceptable because
                # _do_download only writes to the HF cache dir —
                # no critical cleanup. The download completes or
                # fails naturally; on force-kill the partial
                # download is resumed on next start via HF's
                # resume_download=True.
                t = threading.Thread(target=_do_download, daemon=True)
                t.start()
                log.info(
                    "[SERVICE] Download thread started for '%s' (target=%d MB)",
                    model_name,
                    target_mb,
                )
                # Poll cache size until download thread exits OR
                # the user cancels OR the user pauses.
                # the polling loop + pause/resume state
                # machine was extracted to
                # :func:`poll_download_progress` in
                # :mod:`voice_typer.server.service._download_helpers`.
                #
                # Wrap the poll + cleanup in a try/finally so the
                # per-download Event is ALWAYS unregistered, even if
                # ``poll_download_progress`` raises a non-ImportError
                # exception (e.g. OSError, RuntimeError). Pre-fix the
                # cleanup at the former line 1049 was skipped on
                # raise, leaking the Event in
                # ``_download_cancel_events`` forever.
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
                    #  SERVICE-1: remove our per-download Event
                    # from the dict so a sibling download_model
                    # call's cancel signal can't reach us after
                    # we've already exited the polling loop. Also
                    # clear the pause flag so a subsequent download
                    # starts unpaused. Both are idempotent — the
                    # post-try/except cleanup below and the outer
                    # ``download_model`` except handler may call
                    # them again, which is a harmless no-op.
                    self._unregister_download(download_id)
                    clear_download_pause_state()
                # if cancelled, return early.
                if poll_outcome == "cancelled":
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
                    # cache-only snapshot_download attempt above.
                    first_err = download_err[0]
                    if isinstance(first_err, ModelDownloadAborted):
                        # The user cancelled: the transfer gate unwound the
                        # HuggingFace download. Map to the same cancelled
                        # outcome the poll-loop path returns (the renderer's
                        # cancel handler has already cleared local state;
                        # the pending download_model promise resolves as a
                        # clean stop).
                        log.info(
                            "[SERVICE] Download of '%s' aborted via transfer gate",
                            model_name,
                        )
                        return {
                            "success": False,
                            "model": model_name,
                            "cancelled": True,
                            "message": f"Download of {model_name} cancelled. "
                            "Partial files remain in cache; "
                            "retry to resume.",
                        }
                    raise download_err[0] from None
                # Phase B — segmented fast lane for the big files (runs on
                # THIS thread now that the poll loop exited, so its direct
                # progress pushes are the single source of truth — no bar
                # jitter). Pause/cancel keep working through the shared
                # gate (pause blocks inside the engine, cancel raises
                # ModelDownloadAborted → mapped below).
                if seg_plan:
                    try:
                        import time as _phase_time

                        from voice_typer.server.service._download_helpers import (
                            push_progress as _phase_push,
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

                        _seg_last_push = [0.0]
                        _seg_lock = threading.Lock()
                        _big_total = sum(p.size for p in seg_plan)

                        def _on_seg_progress(done: int, _total: int) -> None:
                            # `done` is already cumulative across files
                            # (the phase runner aggregates). Throttled to
                            # ~4 Hz; same 10–95 scale the poll loop uses
                            # so the bar reads continuously.
                            now = _phase_time.monotonic()
                            with _seg_lock:
                                if now - _seg_last_push[0] < 0.25 and done < _big_total:
                                    return
                                _seg_last_push[0] = now
                            _pct = min(95, int(10 + (done / max(1, _big_total)) * 85))
                            _mb = done // (1024 * 1024)
                            _phase_push(
                                event_bus,
                                model_name,
                                _pct,
                                f"Downloading {model_name}: {_mb} MB / ~{target_mb} MB",
                                downloaded_bytes=done,
                                total_bytes=target_bytes,
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
                        # path cannot handle falls back to the classic
                        # full-repo snapshot (today's behavior), which
                        # refetches the big files single-stream.
                        log.warning(
                            "[SERVICE] Segmented fast lane failed for '%s' (%s) — falling back to classic download",
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
                            cache_dir=str(cache_dir),
                            tqdm_class=get_download_tqdm_class(),
                        )
                    # Self-verify the assembled snapshot by HF's own
                    # definition (local-only probe): a layout the probe
                    # rejects would confuse faster-whisper into a
                    # re-download, so fail over to classic instead.
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
                            "for '%s' (%s) — falling back to classic download",
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
                            cache_dir=str(cache_dir),
                            tqdm_class=get_download_tqdm_class(),
                        )
                log.info(
                    "[SERVICE] Download of '%s' complete (%d MB)",
                    model_name,
                    last_total_bytes_seen // (1024 * 1024),
                )
        except ImportError:
            # huggingface_hub is missing or broken (stripped venv /
            # damaged install). This arm previously only logged a debug
            # line claiming a "fallback to engine.load()" — a fallback
            # that no longer exists — and then FELL THROUGH to the
            # success report: 100% progress, a "downloaded successfully"
            # toast, and {"success": True} with NO model files on
            # disk. Report a structured failure instead (same shape as
            # the Parakeet path's reason-table unpack), and release the
            # single-flight gate so the next download attempt isn't
            # refused as "already active" (both cleanup calls are
            # idempotent no-ops if the cache-miss branch's ``finally``
            # already ran).
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
        # Previously this loaded a TranscriptionEngine and called
        # engine.load() which allocated GPU/CPU memory and disrupted
        # the currently active model (Parakeet).  The model files are
        # already verified by HuggingFace's snapshot_download hash
        # checks — there's no need to load the entire model just to
        # confirm the files exist.
        log.info("[SERVICE] Download of '%s' verified via HF cache (no full model load)", model_name)
        # Single terminal 100% push per download call: the cache-hit
        # branch reports "already cached" as a status-only event at a
        # non-terminal percent, and the cache-miss branch reaches here
        # after its per-chunk progress stream (which caps at 95%). This
        # is the ONLY event any success path pushes at 100%.
        _push_progress(event_bus, model_name, 100, f"Download of {model_name} complete")
        # invalidate the tray models submenu cache
        # so the next right-click reflects the newly-downloaded
        # model without waiting for the 5-second TTL.
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
        # cache-miss branch already unregisters the per-download Event
        # and clears the pause flag (and is the authoritative cleanup
        # path on exceptions). These calls are retained for the
        # cache-hit path (where ``download_id`` is ``None`` and the
        # ``finally`` never ran) and as belt-and-braces on the success
        # path — both ``_unregister_download`` and
        # ``clear_download_pause_state`` are idempotent no-ops if
        # already done.
        if download_id is not None:
            self._unregister_download(download_id)
        # clear the pause flag so subsequent
        # pause calls return False (no active download).
        clear_download_pause_state()
        _notify(self._app.tray, model_name, APP_NAME, f"Model '{model_name}' downloaded successfully")
        # PERF-10 / SVC-9: on-disk model state changed — force the
        # next get_model_status() poll to recompute so the freshly
        # downloaded model shows as available immediately.
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
        # only one gateable download at a time).
        from voice_typer.server.asr_setup import is_download_active

        if is_download_active():
            log.info(
                "[SERVICE] Download of '%s' refused — another download is already in progress (possibly paused)",
                model_name,
            )
            return {
                "success": False,
                "model": model_name,
                "download_already_active": True,
                "error": "Another model download is already in progress (it may be paused). Resume or cancel it first.",
            }
        from voice_typer.server import event_bus
        from voice_typer.server.service._download_helpers import (
            notify as _notify,
            push_progress as _push_progress,
        )

        # HuggingFace consent gate.  Parakeet weights
        # are fetched from huggingface.co via
        # download_parakeet_weights(); gate the network call
        # on explicit user consent ().  Mirrors
        # TranscriptionEngine._pre_download_model
        # (transcription.py:835-849).  Must fire BEFORE the
        # asr_setup import + call so a user who has NOT
        # consented cannot trigger any HuggingFace Hub
        # interaction from the IPC path.
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
        # events as the whisper branch — arm them for this download and
        # clean them up on every exit (reset is idempotent; a
        # download_already_active refusal returns BEFORE this line).
        reset_download_pause_state()

        # surface silent failures. Previously the
        # service called ``download_parakeet_weights()`` with no
        # arguments and discarded the return value, so every
        # failure (consent gate, missing huggingface_hub, disk
        # space, retry exhaustion, integrity check) was logged
        # as "complete" and pushed to the UI as 100% progress +
        # "downloaded successfully". The user saw a green
        # success toast but no model files were fetched.
        #
        # Now we:
        #   1. Forward ``config=self._app.config`` so the consent
        #      gate inside ``download_parakeet_weights`` passes
        #      (the upstream ``_require_huggingface_consent``
        #      check above already verified consent; this is
        #      defense-in-depth).
        #   2. Forward a ``progress_callback`` that bridges the
        #      function's progress messages to the renderer's
        #      ``download_progress`` event bus.
        #   3. Unpack the ``(success, reason, exc_info)`` 3-tuple
        #      and short-circuit to a structured error return on
        #      failure, mapping the reason code to a
        #      user-facing message via ``_PARAKEET_REASON_MESSAGES``.
        #
        # The unpack is defensive: some legacy / test fakes
        # return a bare ``bool`` rather than the 3-tuple. Treat
        # truthy → success, falsy → failure with reason
        # "unknown" so the test fakes don't break.
        def _parakeet_progress(message: str) -> None:
            # Map the function's textual progress messages to
            # the renderer's ``download_progress`` event. We
            # don't know the byte-count, so we leave the rich
            # metadata fields unset and just forward the status.
            _push_progress(event_bus, model_name, 50, message)

        _push_progress(event_bus, model_name, 50, "Downloading Parakeet weights from HuggingFace...")
        try:
            dpw_result = download_parakeet_weights(
                config=self._app.config,
                progress_callback=_parakeet_progress,
            )
        finally:
            # Release the gate's pause/abort events on EVERY exit —
            # success, failure, or an abort unwinding through here (the
            # download_model dispatcher maps ModelDownloadAborted to the
            # cancelled outcome; this finally must still run).
            from voice_typer.server.asr_setup import clear_download_pause_state

            clear_download_pause_state()
        # Defensive unpack: handle both the documented 3-tuple
        # and the legacy/test bare-bool shape.
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
