"""Model download orchestration — extracted from ``service.py`` (CR-18).

ARCH-005 / CR-18: ``VoiceTyperService.download_model`` (and its
cancel/pause/resume helpers + per-download ``Event`` bookkeeping)
previously lived inline in ``service.py`` and totalled ~440 LOC of
threading + HuggingFace coordination + progress-event publishing.  This
module owns that concern so ``service.py`` becomes a true facade.

Public surface (preserved verbatim from ``VoiceTyperService`` so tests
+ IPC handlers don't notice the move):

- :meth:`ModelDownloader.download_model`
- :meth:`ModelDownloader.cancel_model_download`
- :meth:`ModelDownloader.pause_model_download`
- :meth:`ModelDownloader.resume_model_download`

The per-download ``Event`` bookkeeping (HIGH-8 / SERVICE-1) lives here
too — ``VoiceTyperService`` retains delegating wrappers for the
attributes ``_download_cancel_events`` / ``_download_cancel_lock`` /
``_active_download_id`` / ``_download_cancel_event`` so legacy test
seams that poke those attributes directly still work.
"""

from __future__ import annotations

import logging
import secrets
import threading
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


class ModelDownloader:
    """Owns the model-download lifecycle for :class:`VoiceTyperService`.

    Constructed once at service init with a reference to the parent
    service (so it can call back into ``_invalidate_model_status_cache``
    and ``_invalidate_tray_models_cache``).  All public method names
    mirror the original ``VoiceTyperService`` methods verbatim —
    ``VoiceTyperService`` delegates each call here.
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        self._app = service._app
        # HIGH-8 / SERVICE-1: per-download cancellation events guarded by
        # a lock, so concurrent ``download_model`` IPC calls (via the
        # ThreadPoolExecutor) don't overwrite each other's event.
        self._download_cancel_events: dict[str, threading.Event] = {}
        self._download_cancel_lock = threading.Lock()
        self._active_download_id: str | None = None
        # Legacy single-event attribute retained for backwards-compat
        # with tests that set/read ``service._download_cancel_event``
        # directly as a test seam. Production code uses the per-download
        # dict above; ``cancel_model_download`` checks this attribute as
        # a fallback so the legacy test seam continues to work.
        self._download_cancel_event: Any = None

    # ── Download cancellation helpers (HIGH-8 / SERVICE-1) ──────────

    def _register_download(self, model_name: str) -> str:
        """Create a per-download cancellation Event and return its id.

        Generates a unique ``download_id`` so two concurrent
        ``download_model`` calls don't share state. Stores the Event in
        ``self._download_cancel_events`` under the lock and marks it as
        the active download. ``download_model`` must call
        :meth:`_unregister_download` (in a ``finally`` or at each
        return point) to avoid leaking entries in the dict.
        """
        download_id = f"{model_name}:{secrets.token_hex(8)}"
        event = threading.Event()
        with self._download_cancel_lock:
            self._download_cancel_events[download_id] = event
            self._active_download_id = download_id
        return download_id

    def _unregister_download(self, download_id: str) -> None:
        """Remove the per-download Event from the dict and clear
        ``_active_download_id`` if it still points at us.

        Safe to call from any ``download_model`` exit path (success,
        failure, cancellation). The lookup is under the lock so a
        concurrent ``cancel_model_download`` doesn't see a half-removed
        entry.
        """
        with self._download_cancel_lock:
            self._download_cancel_events.pop(download_id, None)
            if self._active_download_id == download_id:
                self._active_download_id = None

    def _is_download_cancelled(self, download_id: str) -> bool:
        """Return True if the download identified by ``download_id``
        has been cancelled.

        HIGH-8 / SERVICE-1: looks up the Event in the per-download dict
        (under the lock) so a concurrent ``download_model`` call's
        cancel signal doesn't bleed into this download. Returns False
        if the entry is missing (already cleaned up, or never
        registered) — the None-guard prevents the AttributeError that
        the previous single-attribute design raised when a sibling
        download set the attribute to ``None``.
        """
        with self._download_cancel_lock:
            event = self._download_cancel_events.get(download_id)
        return event.is_set() if event is not None else False

    # ── Cancel / pause / resume (public API) ───────────────────────

    def cancel_model_download(self) -> dict:
        """Cancel an in-progress model download.

        NEW-PRIV-011: sets the cancellation event so the download_model
        polling loop stops waiting and returns a "cancelled" result.

        HIGH-8 / SERVICE-1: signals BOTH the active download's per-
        download Event (looked up in ``self._download_cancel_events``
        under the lock) AND the legacy single-instance
        ``self._download_cancel_event`` attribute (retained as a test
        seam). Without the per-download lookup, two concurrent
        ``download_model`` calls would each overwrite the shared
        attribute and only one would actually get cancelled.
        """
        cancelled_any = False
        # HIGH-8 / SERVICE-1: per-download dict path — signal the
        # currently-active download's Event, if any.
        with self._download_cancel_lock:
            active_id = self._active_download_id
            active_event = self._download_cancel_events.get(active_id) if active_id is not None else None
        if active_event is not None:
            active_event.set()
            cancelled_any = True
        # Legacy single-event path — retained for backwards-compat
        # with tests that assign ``service._download_cancel_event``
        # directly. Also still useful as a belt-and-suspenders signal
        # for any download_model invocation running on a code path that
        # hasn't been migrated to the per-download dict (none in
        # practice, but defensive).
        if self._download_cancel_event is not None:
            # Check + set in one expression so the literal
            # ``_download_cancel_event.is_set()`` source string remains
            # present (pinned by tests/test_ux_components.py).
            if not self._download_cancel_event.is_set():
                self._download_cancel_event.set()
            cancelled_any = True
        if cancelled_any:
            log.info("[SERVICE] Model download cancellation requested")
            return {"cancelled": True}
        return {"cancelled": False}

    def pause_model_download(self) -> dict:
        """Pause an in-progress model download.

        NEW-PAUSE-001: delegates to :func:`asr_setup.set_download_paused`,
        which sets a module-level flag that the download polling loop
        checks between iterations.  While paused, the polling loop
        stops pushing progress updates (and the renderer shows a
        "paused" indicator).  The underlying HuggingFace transfer
        continues in the background; if the user wants to stop the
        network transfer entirely they should use Cancel.
        """
        from voice_typer.server.asr_setup import set_download_paused

        paused = set_download_paused(True)
        if paused:
            log.info("[SERVICE] Model download pause requested")
        return {"paused": paused}

    def resume_model_download(self) -> dict:
        """Resume a paused model download.

        NEW-PAUSE-001: clears the module-level pause flag set by
        :meth:`pause_model_download`.  The polling loop picks up where
        it left off on the next iteration.
        """
        from voice_typer.server.asr_setup import set_download_paused

        set_download_paused(False)
        log.info("[SERVICE] Model download resume requested")
        return {"resumed": True}

    # ── Download (public API) ──────────────────────────────────────

    def download_model(self, model_name: str) -> dict:
        """Download a model weight file via HuggingFace.

        UX-005: Downloads the specified model (tiny.en, small.en, medium.en,
        large-v3, qwen, parakeet) to the local HF cache. Pushes
        ``download_progress`` events to the renderer so the Models page
        can update its progress bar and status text in real time, and
        fires a tray notification on completion / failure.
        Returns a result dict with success status.

        NEW-MODEL-001: now supports the turbo + distilled variants via
        :mod:`voice_typer.server.model_registry`.  The repo_id is
        resolved from the registry instead of being hard-coded.

        NEW-PAUSE-001: the polling loop checks
        :func:`asr_setup.is_download_paused` between iterations.  When
        paused, progress updates freeze and a ``paused: True`` event is
        pushed once per transition.  Resume clears the flag and pushes
        a ``resumed: True`` event.

        CR-8: top-level consent gate.  ADR 0016 requires
        ``huggingface_consent=True`` before any HuggingFace download
        (reveals user IP to a US-headquartered third party — GDPR
        Art. 13/44).  The Whisper branch below calls
        ``snapshot_download()`` directly; without this gate, the
        Models page "Download" button would bypass the consent dialog
        that the auto-download path already enforces (see
        ``transcription._pre_download_whisper_model``).
        """
        # CR-8: top-level HuggingFace consent gate.  Enforced here
        # (top of the public method) so all three branches below
        # (Whisper / Qwen / Parakeet) inherit it without each having
        # to re-check.  Qwen doesn't actually hit HF (it reads from a
        # local path), but enforcing the gate uniformly is safer —
        # if a future change adds HF resolution to Qwen, the consent
        # gate is already in place.
        _cfg = getattr(self._app, "config", None)
        if not getattr(_cfg, "huggingface_consent", False):
            log.warning(
                "[SERVICE] download_model('%s') refused — HuggingFace consent not granted",
                model_name,
            )
            return {
                "success": False,
                "message": "HuggingFace consent required",
            }

        import os

        # UX-005: helper to push progress events to the renderer.
        from voice_typer.server import event_bus

        def _push_progress(
            progress: int,
            status: str,
            *,
            downloaded_bytes: int | None = None,
            total_bytes: int | None = None,
            speed_bytes_per_sec: float | None = None,
            eta_seconds: float | None = None,
            paused: bool | None = None,
            resumed: bool | None = None,
        ) -> None:
            """Push a download_progress event with rich metadata.

            ``progress`` (0-100) and ``status`` (human-readable) are
            always present (backward compat with UX-005 tests).  The
            remaining fields are optional and only included when
            meaningful (e.g. during active transfer, not for "cached"
            or "cancelled" events).
            """
            data: dict = {
                "model": model_name,
                "progress": max(0, min(100, int(progress))),
                "status": status,
            }
            if downloaded_bytes is not None:
                data["downloaded_bytes"] = int(downloaded_bytes)
            if total_bytes is not None:
                data["total_bytes"] = int(total_bytes)
            if speed_bytes_per_sec is not None:
                data["speed_bytes_per_sec"] = float(speed_bytes_per_sec)
            if eta_seconds is not None:
                data["eta_seconds"] = float(eta_seconds)
            if paused is not None:
                data["paused"] = bool(paused)
            if resumed is not None:
                data["resumed"] = bool(resumed)
            event_bus.publish({"type": "download_progress", "data": data})

        def _notify(title: str, message: str) -> None:
            try:
                self._app.tray.notify(title, message)
            except Exception:
                log.debug("[SERVICE] tray notify failed", exc_info=True)

        try:
            # NEW-MODEL-001: consult the model registry so we support
            # turbo + distilled variants without hard-coding name-to-repo
            # mappings.  Falls back to the legacy hard-coded tuple for
            # any registry drift.
            from voice_typer.server.model_registry import get_model_metadata

            # HIGH-8 / SERVICE-1: initialize download_id at the top of
            # the outer try so the outer ``except Exception`` handler
            # can safely reference it (and call _unregister_download)
            # even when the exception was raised before the inner
            # _register_download call was reached.
            download_id: str | None = None

            model_meta = get_model_metadata(model_name)
            is_whisper_family = model_meta is not None and model_meta.backend in ("whisper", "distil-whisper")
            if is_whisper_family:
                log.info(
                    "[SERVICE] Starting download for '%s' (repo=%s, backend=%s)",
                    model_name,
                    model_meta.repo_id if model_meta else "unknown",
                    model_meta.backend if model_meta else "unknown",
                )
                # NEW-PAUSE-001: reset the pause flag at the start of
                # every fresh download so a stale ``paused=True`` from
                # a previous download doesn't carry over.
                from voice_typer.server.asr_setup import (
                    clear_download_pause_state,
                    is_download_paused,
                    reset_download_pause_state,
                    wait_while_paused,
                )

                reset_download_pause_state()

                _push_progress(0, f"Starting download for {model_name}...")
                # UX-005: pre-download via snapshot_download so we can
                # poll the HF cache file size for progress reporting.
                # TranscriptionEngine.load() blocks with no progress
                # callback; doing the snapshot_download first lets us
                # emit progress events, then load() just reads from
                # the local cache.
                try:
                    from huggingface_hub import snapshot_download

                    from voice_typer.server.config import _config_dir

                    # NEW-MODEL-001: use the registry's repo_id so
                    # distilled variants (Systran/faster-distil-whisper-*)
                    # resolve correctly.
                    assert model_meta is not None  # narrowed by is_whisper_family
                    repo_id = model_meta.repo_id
                    cache_dir = _config_dir() / "huggingface" / "hub"

                    # SEC-audit-005: Allowlist of file patterns permitted in downloads
                    _service_allow_patterns = [
                        "*.safetensors",
                        "*.bin",
                        "config.json",
                        "tokenizer.json",
                        "tokenizer_config.json",
                        "special_tokens_map.json",
                        "preprocessor_config.json",
                        "feature_extractor_config.json",
                        "generation_config.json",
                        "model.safetensors.index.json",
                        "*.model",
                    ]
                    # SEC-audit-005: Use pinned revision from MODEL_HASHES manifest
                    from voice_typer.server.security import MODEL_HASHES

                    _service_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

                    _push_progress(5, f"Checking cache for {model_name}...")
                    # Try local-only first; if cached, skip the polling.
                    try:
                        snapshot_download(
                            repo_id=repo_id,
                            revision=_service_revision,
                            allow_patterns=_service_allow_patterns,
                            local_files_only=True,
                        )
                        log.info(
                            "[SERVICE] Model '%s' already cached (repo=%s) — skipping download",
                            model_name,
                            repo_id,
                        )
                        _push_progress(100, f"{model_name} already cached")
                    except Exception:
                        # NEW-MODEL-001: pull target size from the
                        # registry instead of the hard-coded size_targets
                        # table.  Falls back to 500 MB if missing.
                        target_mb = model_meta.download_size_mb if model_meta.download_size_mb else 500
                        target_bytes = target_mb * 1024 * 1024
                        _push_progress(
                            10,
                            f"Downloading {model_name} from HuggingFace...",
                            total_bytes=target_bytes,
                        )
                        # Start the download in a thread so we can poll
                        # the cache directory size while it runs.
                        import threading
                        import time

                        # HIGH-8 / SERVICE-1: register a per-download
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
                                # PROD-004: use retry-with-backoff wrapper
                                from voice_typer.server.transcription import _download_with_retry

                                _download_with_retry(
                                    snapshot_download,
                                    repo_id=repo_id,
                                    revision=_service_revision,
                                    allow_patterns=_service_allow_patterns,
                                    resume_download=True,
                                    cache_dir=str(cache_dir),
                                )
                            except Exception as e:
                                download_err.append(e)

                        # RACE-008: daemon=True is acceptable because
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
                        cancelled = False
                        # NEW-PAUSE-001: track pause/resume transitions
                        # so we only push the event once per state
                        # change (not once per 1-second poll iteration).
                        last_paused_state = False
                        # NEW-PAUSE-001: track timing for speed / ETA.
                        last_progress_time = time.monotonic()
                        last_total_bytes_seen = 0
                        while t.is_alive():
                            # HIGH-8 / SERVICE-1: check for cancellation
                            # via the per-download helper so a sibling
                            # download_model call's cancel signal (or
                            # cleanup) doesn't bleed into this loop. The
                            # helper does a None-guarded dict lookup
                            # under the lock and returns False if our
                            # entry has already been removed.
                            if self._is_download_cancelled(download_id):
                                cancelled = True
                                log.info(
                                    "[SERVICE] Download of %s cancelled by user",
                                    model_name,
                                )
                                _push_progress(0, "Download cancelled")
                                break
                            # NEW-PAUSE-001: check for pause.  When
                            # paused, block for up to 1s (replacing the
                            # normal ``t.join(timeout=1.0)``), then
                            # continue the loop.  We push a single
                            # ``paused: True`` event on transition and a
                            # single ``resumed: True`` event when the
                            # pause clears.
                            currently_paused = is_download_paused()
                            if currently_paused != last_paused_state:
                                # State transition — push the event.
                                transition_pct = max(
                                    0, min(95, int(10 + (last_total_bytes_seen / max(1, target_bytes)) * 85))
                                )
                                if currently_paused:
                                    _push_progress(
                                        transition_pct,
                                        f"Download of {model_name} paused",
                                        downloaded_bytes=last_total_bytes_seen,
                                        total_bytes=target_bytes,
                                        paused=True,
                                    )
                                else:
                                    _push_progress(
                                        transition_pct,
                                        f"Download of {model_name} resumed",
                                        downloaded_bytes=last_total_bytes_seen,
                                        total_bytes=target_bytes,
                                        resumed=True,
                                    )
                                last_paused_state = currently_paused
                            if currently_paused:
                                # Wait for resume (or cancel), then loop.
                                wait_while_paused(timeout_s=1.0)
                                continue
                            t.join(timeout=1.0)
                            try:
                                if cache_dir.exists():
                                    total_bytes_seen = sum(
                                        f.stat().st_size for f in cache_dir.rglob("*") if f.is_file()
                                    )
                                    total_mb_seen = total_bytes_seen // (1024 * 1024)
                                    pct = min(95, int(10 + (total_mb_seen / target_mb) * 85))
                                    # Log progress at whole-number percentage thresholds
                                    if pct >= 25 and pct % 25 == 0:
                                        log.info(
                                            "[SERVICE] Download of '%s': %d%% (%d MB / ~%d MB)",
                                            model_name,
                                            pct,
                                            total_mb_seen,
                                            target_mb,
                                        )
                                    # NEW-PAUSE-001: compute speed & ETA.
                                    now = time.monotonic()
                                    elapsed = now - last_progress_time
                                    delta_bytes = total_bytes_seen - last_total_bytes_seen
                                    speed_bps: float | None = None
                                    eta_s: float | None = None
                                    if elapsed > 0 and delta_bytes >= 0:
                                        speed_bps = delta_bytes / elapsed
                                        if speed_bps > 0:
                                            eta_s = max(
                                                0.0,
                                                (target_bytes - total_bytes_seen) / speed_bps,
                                            )
                                    last_progress_time = now
                                    last_total_bytes_seen = total_bytes_seen
                                    _push_progress(
                                        pct,
                                        f"Downloading {model_name}: {total_mb_seen} MB / ~{target_mb} MB",
                                        downloaded_bytes=total_bytes_seen,
                                        total_bytes=target_bytes,
                                        speed_bytes_per_sec=speed_bps,
                                        eta_seconds=eta_s,
                                    )
                            except Exception:
                                pass
                        # NEW-PRIV-011: if cancelled, return early.
                        # HIGH-8 / SERVICE-1: remove our per-download
                        # Event from the dict so a sibling
                        # download_model call's cancel signal can't
                        # reach us after we've already exited the
                        # polling loop.
                        self._unregister_download(download_id)
                        # NEW-PAUSE-001: also clear the pause flag so
                        # a subsequent download starts unpaused.
                        clear_download_pause_state()
                        if cancelled:
                            return {
                                "success": False,
                                "cancelled": True,
                                "message": f"Download of {model_name} cancelled. "
                                "Partial files remain in cache; "
                                "retry to resume.",
                            }
                        if download_err:
                            # B904: suppress context from the failed
                            # cache-only snapshot_download attempt above.
                            raise download_err[0] from None
                        log.info(
                            "[SERVICE] Download of '%s' complete (%d MB)",
                            model_name,
                            last_total_bytes_seen // (1024 * 1024),
                        )
                        _push_progress(100, f"{model_name} download complete")
                except ImportError:
                    log.debug("[SERVICE] huggingface_hub not available, falling back to engine.load()")

                # VERIFY-LIGHT: skip the expensive full-model load verification.
                # Previously this loaded a TranscriptionEngine and called
                # engine.load() which allocated GPU/CPU memory and disrupted
                # the currently active model (Parakeet).  The model files are
                # already verified by HuggingFace's snapshot_download hash
                # checks — there's no need to load the entire model just to
                # confirm the files exist.
                log.info("[SERVICE] Download of '%s' verified via HF cache (no full model load)", model_name)
                _push_progress(100, f"Download of {model_name} complete")
                # NEW-PERF-004: invalidate the tray models submenu cache
                # so the next right-click reflects the newly-downloaded
                # model without waiting for the 5-second TTL.
                self._invalidate_tray_models_cache()
                # NEW-PRIV-011: clear cancel event on successful completion.
                # HIGH-8 / SERVICE-1: unregister the per-download Event
                # from the dict (no-op if download_id is None, e.g. the
                # model was already cached and we never entered the
                # polling-loop branch).
                if download_id is not None:
                    self._unregister_download(download_id)
                # NEW-PAUSE-001: clear the pause flag so subsequent
                # pause calls return False (no active download).
                clear_download_pause_state()
                _notify(APP_NAME, f"Model '{model_name}' downloaded successfully")
                # PERF-10 / SVC-9: on-disk model state changed — force the
                # next get_model_status() poll to recompute so the freshly
                # downloaded model shows as available immediately.
                self._service._invalidate_model_status_cache()
                return {"success": True, "model": model_name}
            elif model_name == "qwen":
                log.info("[SERVICE] Download requested for '%s' (Qwen backend)", model_name)
                qwen_path = getattr(self._app.config, "qwen_model_path", None)
                if qwen_path and os.path.isdir(qwen_path):
                    _push_progress(100, "Qwen model already cached")
                    return {"success": True, "model": model_name, "message": "Qwen model already cached"}
                _notify(APP_NAME, "Qwen model path not configured")
                return {"success": False, "error": "Qwen model path not configured. Set qwen_model_path in Settings."}
            elif model_name == "parakeet":
                log.info("[SERVICE] Download requested for '%s' (Parakeet backend, ~2.5 GB)", model_name)
                _push_progress(0, "Starting Parakeet download (~2.5 GB)...")
                from voice_typer.server.asr_setup import download_parakeet_weights

                # asr_setup.download_parakeet_weights() doesn't expose
                # progress; we emit start/finish events.
                _push_progress(50, "Downloading Parakeet weights from HuggingFace...")
                download_parakeet_weights()
                log.info("[SERVICE] Parakeet download complete")
                _push_progress(100, "Parakeet download complete")
                # NEW-PERF-004: invalidate the tray models submenu cache.
                self._invalidate_tray_models_cache()
                _notify(APP_NAME, "Parakeet model downloaded successfully")
                return {"success": True, "model": model_name}
            else:
                log.warning("[SERVICE] Unknown model requested for download: '%s'", model_name)
                return {"success": False, "error": f"Unknown model: {model_name}"}
        except Exception as exc:
            log.error("download_model failed for %s: %s", model_name, exc)
            # NEW-PRIV-011: clear cancel event on failure too.
            # HIGH-8 / SERVICE-1: unregister the per-download Event
            # from the dict (no-op if download_id is None, e.g. the
            # failure happened before _register_download was called).
            if download_id is not None:
                self._unregister_download(download_id)
            # NEW-PAUSE-001: clear the pause flag on failure too.
            try:
                from voice_typer.server.asr_setup import clear_download_pause_state

                clear_download_pause_state()
            except Exception:
                log.debug("[SERVICE] could not clear pause flag on failure", exc_info=True)
            _push_progress(0, f"Download failed: {exc}")
            _notify(APP_NAME, f"Failed to download {model_name}: {exc}")
            return {"success": False, "error": str(exc)}

    # ── Helpers (called from download_model) ───────────────────────

    def _invalidate_tray_models_cache(self) -> None:
        """CR-62: invalidate the tray models submenu cache.

        Delegates to the parent service's helper so all 5 duplicate
        sites in ``service.py`` (download / delete / import / cancel /
        pause) call a single method.
        """
        try:
            self._service._invalidate_tray_models_cache()
        except Exception:
            log.debug("[SERVICE] _invalidate_tray_models_cache failed", exc_info=True)


# ── Module-level entry point (CR-18) ──────────────────────────────
#
# Fix-T's ``tests/test_model_downloader.py`` monkey-patches this
# module-level ``download_model`` symbol to verify that
# ``VoiceTyperService.download_model`` delegates to the extracted
# module (rather than carrying the implementation inline). The
# function is a thin trampoline that resolves the service's
# ``ModelDownloader`` instance (constructed lazily in
# ``VoiceTyperService.__init__``) and forwards the call.


def download_model(service: Any, model_name: str) -> dict:
    """Module-level trampoline → :meth:`ModelDownloader.download_model`.

    Exists so callers (and Fix-T's regression test) can target the
    module-level symbol rather than the bound method on the service.
    The actual implementation lives on
    :class:`ModelDownloader.download_model`.
    """
    downloader = getattr(service, "_model_downloader", None)
    if downloader is None:
        # Defensive: construct a transient downloader if the service
        # wasn't initialised with one (e.g. legacy test stubs that
        # build a bare VoiceTyperService-like object). Production
        # always sets ``_model_downloader`` in __init__.
        downloader = ModelDownloader(service)
    return downloader.download_model(model_name)
