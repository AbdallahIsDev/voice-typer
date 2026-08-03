"""Pre-download / cache-probe helpers for ``TranscriptionEngine``.

Extracted from ``voice_typer/server/transcription.py`` so the engine
module stays under the 500-line maintenance budget. The four phase
helpers (``probe_cache``, ``require_consent``, ``check_disk``,
``download_and_verify``) plus the orchestrator (``pre_download_model``)
live here as module-level functions that take the engine instance as
their first argument — mirroring the pattern already used by
``transcription_errors.py`` and ``transcription_result.py``.

TEST PATCH COMPATIBILITY
------------------------
Several tests monkeypatch the *module-level* bindings on
``voice_typer.server.transcription``::

    monkeypatch.setattr(
        "voice_typer.server.transcription.cleanup_hf_cache_dir", ...
    )
    monkeypatch.setattr(
        "voice_typer.server.transcription._check_disk_space_for_download", ...
    )
    monkeypatch.setattr(
        "voice_typer.server.transcription._download_with_retry", ...
    )
    monkeypatch.setattr(
        "voice_typer.server.transcription._require_huggingface_consent", ...
    )

For those patches to take effect when the function bodies live here,
the helpers MUST resolve those names via **late binding** — i.e.
``from voice_typer.server import transcription as _t`` and then
``_t.cleanup_hf_cache_dir(...)`` inside the function body (NOT a
top-level ``from voice_typer.server.transcription import
cleanup_hf_cache_dir`` which would freeze the pre-patch reference).
"""

from __future__ import annotations

import logging

# Use the ``transcription`` logger name so log records emitted from this
# extracted module are captured by tests that filter by
# ``logger="voice_typer.server.transcription"`` (the historical logger
# name when this code lived inline in ``transcription.py``).
log = logging.getLogger("voice_typer.server.transcription")


def probe_cache(
    engine,
    snapshot_download_fn,
    repo_id: str,
    revision: str,
    allow_patterns,
    model_size: str,
    progress_callback=None,
) -> tuple[str | None, bool]:
    """Phase 1: probe the HuggingFace cache (local-only).

    Returns ``(local_dir, integrity_failed)``:

    * ``(path, False)`` — cache hit AND integrity verified. The
      orchestrator can return immediately (no download needed).
    * ``(None, True)`` — cache hit BUT integrity check failed. The
      orchestrator must clean the tampered cache and re-download
      (after consent). The ``local_dir`` is dropped on this path
      because the caller must NOT trust the tampered files — only
      the ``integrity_failed`` flag is propagated.
    * ``(None, False)`` — cache miss (or local probe raised). The
      orchestrator falls through to consent + download.

    ``snapshot_download_fn`` is the ``huggingface_hub.snapshot_download``
    callable (injected so tests can pass a MagicMock). The call uses
    ``local_files_only=True`` so no network traffic is generated on
    the cache-probe path — consent is only required for the actual
    download (see :func:`require_consent`).
    """
    try:
        local_dir = snapshot_download_fn(
            repo_id=repo_id,
            revision=revision,
            allow_patterns=allow_patterns,
            local_files_only=True,
        )
    except Exception:
        log.debug("[MODEL] HF cache probe failed — will attempt download", exc_info=True)
        return None, False

    from voice_typer.server.security import verify_model_integrity

    if not verify_model_integrity(local_dir, repo_id):
        log.error(
            "[MODEL] Cached model '%s' failed integrity check (cache hit path) — "
            "will remove tampered cache after consent confirmation.",
            model_size,
        )
        if progress_callback:
            progress_callback("Cached model failed integrity check; re-downloading after consent.")
        return None, True

    return local_dir, False


def require_consent(
    engine,
    model_size: str,
    progress_callback,
    integrity_failed: bool,
    repo_id: str,
) -> None:
    """Phase 2: require HuggingFace consent + clean tampered cache.

    Delegates the consent check to the canonical
    :func:`asr_utils._require_huggingface_consent` helper so the
    gate, the log message, the progress-callback wording, and the
    typed ``ConsentRequiredError`` surface stay in sync across all
    three download paths (Whisper, Parakeet, Models-page).

    After consent is confirmed, if ``integrity_failed`` is True
    (the cache was tampered), the tampered cache directory is
    removed so the re-download below fetches fresh files. Pre-fix
    this cleanup happened inline in ``pre_download_model`` —
    extracted here so the orchestrator is a thin delegator.
    """
    # Late binding: tests patch
    # ``voice_typer.server.transcription._require_huggingface_consent``;
    # resolving via ``_t`` ensures the patched version is used.
    from voice_typer.server import transcription as _t

    _t._require_huggingface_consent(
        engine.config,
        model_size,
        log_prefix="[MODEL]",
        progress_message="HuggingFace consent required before downloading model.",
        progress_callback=progress_callback,
    )

    # Consent confirmed.  Now safe to delete a tampered cache
    # (if any) — the re-download in ``download_and_verify`` will
    # fetch fresh files. Deferred from ``probe_cache`` because
    # the consent check above may block the re-download, which
    # would leave the user with no model at all (deleting the
    # only copy before consent would be destructive).
    if integrity_failed:
        log.info(
            "[MODEL] Removing tampered cache for '%s' after consent confirmed.",
            model_size,
        )
        # Late binding again: tests patch
        # ``voice_typer.server.transcription.cleanup_hf_cache_dir``.
        _t.cleanup_hf_cache_dir(repo_id, log_prefix="[MODEL]")


def check_disk(engine, repo_id: str, model_size: str) -> None:
    """Phase 3: check disk space before downloading.

    Thin delegator to :func:`asr_utils._check_disk_space_for_download`
    so the orchestrator reads as a sequence of named phases. The
    underlying helper raises ``RuntimeError`` with a user-friendly
    message if insufficient space is detected.
    """
    # Late binding: tests patch
    # ``voice_typer.server.transcription._check_disk_space_for_download``.
    from voice_typer.server import transcription as _t

    _t._check_disk_space_for_download(repo_id, model_size)


def download_and_verify(
    engine,
    snapshot_download_fn,
    repo_id: str,
    revision: str,
    allow_patterns,
    progress_callback,
    model_size: str,
) -> None:
    """Phase 4: download with retry + verify integrity after download.

    Wraps ``snapshot_download_fn`` in :func:`asr_utils._download_with_retry`
    (exponential backoff for transient CDN / rate-limit failures),
    then runs :func:`security.verify_model_integrity` against the
    pinned ``MODEL_HASHES`` manifest. On integrity failure the
    tampered cache directory is cleaned (so the next launch
    re-downloads) and ``RuntimeError`` is raised with the message
    ``"Model integrity verification failed for <repo_id>"`` so the
    outer ``except RuntimeError`` re-raises (WhisperModel does NOT
    silently load bad files on the current launch).
    """
    # Late binding: tests patch
    # ``voice_typer.server.transcription._download_with_retry`` and
    # ``voice_typer.server.transcription.cleanup_hf_cache_dir``.
    from voice_typer.server import transcription as _t

    local_dir = _t._download_with_retry(
        snapshot_download_fn,
        repo_id=repo_id,
        revision=revision,
        allow_patterns=allow_patterns,
        resume_download=True,
    )
    from voice_typer.server.security import verify_model_integrity

    if not verify_model_integrity(local_dir, repo_id):
        log.error(
            "[MODEL] Model '%s' integrity check failed after download",
            model_size,
        )
        if progress_callback:
            progress_callback("Download completed but integrity check failed")
        _t.cleanup_hf_cache_dir(repo_id, log_prefix="[MODEL]")
        raise RuntimeError(f"Model integrity verification failed for {repo_id}")


def pre_download_model(engine, model_size: str, progress_callback=None):
    """Pre-download model files via huggingface_hub if not already cached.

    This ensures the user sees download progress before WhisperModel blocks
    on the download internally.

    PERF- previously this blocked the calling thread, adding
    2-15s of cold-start latency before model loading could begin.
    We now check the cache first (fast path); if the model is
    already cached, we return immediately so load can proceed. If
    not cached, we download synchronously — load() already runs on
    a background thread, so parallelizing would just add complexity
    without measurable benefit (WhisperModel.__init__ needs the
    files anyway).

    HuggingFace downloads reveal the user's IP to a
    US-headquartered third party.  We check the
    ``huggingface_consent`` config flag before downloading; if
    consent hasn't been given, we raise a ConsentRequiredError so
    the IPC layer can surface a consent dialog to the renderer.
    The cache-check path (``local_files_only=True``) does NOT
    require consent — it only reads local files and never
    contacts HuggingFace.

    Orchestrates 4 phase helpers (``probe_cache``,
    ``require_consent``, ``check_disk``, ``download_and_verify``)
    so each phase is independently testable. The 188-line
    monolith body is now a thin delegator.
    """
    # Late binding for ConsentRequiredError so any future test patch
    # on ``voice_typer.server.transcription.ConsentRequiredError``
    # continues to take effect (currently it is the asr_errors
    # canonical class re-exported through transcription).
    from voice_typer.server import transcription as _t

    consent_required_error = _t.ConsentRequiredError

    # Skip pre-download for non-Whisper model sizes (e.g. "parakeet" or "qwen")
    if not model_size or model_size in ("parakeet", "qwen"):
        log.debug("[MODEL] Skipping pre-download for non-Whisper model '%s'", model_size)
        return
    try:
        from huggingface_hub import snapshot_download

        repo_id = f"Systran/faster-whisper-{model_size}"

        # SEC-audit-005: Use pinned revision from MODEL_HASHES manifest
        from voice_typer.server.security import MODEL_HASHES

        whisper_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

        # Use the shared
        # ``ALLOW_PATTERNS_WHISPER`` list from ``_model_integrity``
        # instead of an inline duplicate.  Whisper-family repos ship
        # ``model.bin`` (CTranslate2 native format) which is kept
        # here — CTranslate2 loads it without going through
        # ``torch.load`` (the pickle RCE vector), so the ``*.bin``
        # risk is bounded to "wrong weights → bad transcription"
        # rather than "arbitrary code execution".  The Parakeet path
        # (``parakeet_engine.py`` / ``asr_setup.py``) uses the
        # ``ALLOW_PATTERNS_PARAKEET`` list which omits ``*.bin``
        # because Parakeet ships ``model.safetensors`` only.
        from voice_typer.server._model_integrity import ALLOW_PATTERNS_WHISPER

        _whisper_allow_patterns = ALLOW_PATTERNS_WHISPER

        if progress_callback:
            progress_callback(f"Checking model cache for '{model_size}'...")
        # Phase 1: probe cache (local-only, no consent needed).
        local_dir, integrity_failed = probe_cache(
            engine,
            snapshot_download,
            repo_id,
            whisper_revision,
            _whisper_allow_patterns,
            model_size,
            progress_callback=progress_callback,
        )
        if local_dir is not None and not integrity_failed:
            log.info("[MODEL] Model '%s' already cached (integrity verified)", model_size)
            return

        # Phase 2: require consent + clean tampered cache (if any).
        require_consent(engine, model_size, progress_callback, integrity_failed, repo_id)

        log.info("[MODEL] Model '%s' not cached, downloading...", model_size)
        if progress_callback:
            progress_callback(f"Downloading model '{model_size}' (varies by size)...")

        # Phase 3: check disk space before downloading.
        check_disk(engine, repo_id, model_size)

        # Phase 4: download with retry + verify integrity.
        download_and_verify(
            engine,
            snapshot_download,
            repo_id,
            whisper_revision,
            _whisper_allow_patterns,
            progress_callback,
            model_size,
        )
        log.info("[MODEL] Model '%s' download complete", model_size)
    except ImportError:
        log.debug("[MODEL] huggingface_hub not available, skipping pre-download")
    except consent_required_error:
        # re-raise ``ConsentRequiredError`` so it
        # propagates to the IPC layer — do NOT let the broad
        # ``except Exception`` below swallow it (which would turn a
        # user-actionable consent dialog into a silent warning log).
        raise
    except RuntimeError:
        # re-raise integrity-check failures (raised in
        # ``download_and_verify``) so WhisperModel does NOT silently load
        # the bad files on the current launch. The cache dir was
        # already cleaned, so the next launch will
        # re-download — but the current launch must fail fast.
        raise
    except Exception as exc:
        log.warning("[MODEL] Pre-download failed (WhisperModel will retry): %s", exc)
