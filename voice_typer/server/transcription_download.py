"""HuggingFace cache-probe / download-gate helpers for ``TranscriptionEngine``.

Extracted from ``voice_typer/server/transcription.py`` (which stays the
public facade and keeps thin one-line delegator methods on the engine
class) so the load-path cache gate can be unit-tested in isolation:

* :func:`probe_cache` — phase 1 local-only cache probe. Returns
  ``(local_dir, integrity_failed)`` for the caller to turn into typed
  errors.
* :func:`require_model_downloaded` — the NEVER-auto-download gate:
  raises ``ModelNotDownloadedError`` on a cache miss and
  ``ModelIntegrityError`` on a tampered cache hit (without deleting
  anything — deletion is an explicit user action on the Models page).
* :func:`whisper_size_cached` — local-only probe used by the fallback
  chain to skip entries whose model has not been downloaded.

TEST PATCH COMPATIBILITY
------------------------
``_probe_cache`` / ``_require_model_downloaded`` are invoked through the
engine object (``engine._probe_cache(...)``) so class-level monkeypatches
(``monkeypatch.setattr("...TranscriptionEngine._require_model_downloaded",
...)``) keep taking effect. ``verify_model_integrity`` /
``MODEL_HASHES`` / ``ALLOW_PATTERNS_WHISPER`` are imported inside the
function bodies at call time (same as the pre-extraction inline bodies)
so tests patching ``voice_typer.server.security.verify_model_integrity``
keep working.
"""

from __future__ import annotations

import logging

from voice_typer.server.asr_errors import (
    ModelIntegrityError,
    ModelNotDownloadedError,
)

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
      caller can proceed to load.
    * ``(None, True)`` — cache hit BUT integrity check failed. The
      caller must refuse to load (raise ``ModelIntegrityError``)
      without deleting the tampered files — deletion is an explicit
      user action (Models page Delete button).
    * ``(None, False)`` — cache miss (or local probe raised). The
      caller raises ``ModelNotDownloadedError`` (never downloads).

    ``snapshot_download_fn`` is the ``huggingface_hub.snapshot_download``
    callable (injected so tests can pass a MagicMock). The call uses
    ``local_files_only=True`` so no network traffic is generated on
    the cache-probe path.
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
            "refusing to load tampered files (no automatic deletion).",
            model_size,
        )
        if progress_callback:
            progress_callback("Cached model failed integrity check; delete and re-download from the Models page.")
        return None, True

    return local_dir, False


def require_model_downloaded(engine, model_size: str, progress_callback=None) -> None:
    """Ensure the Whisper model is present in the local HF cache.

    The app never downloads models automatically: the user must
    explicitly click Download on the Models page (or the onboarding
    wizard) first. This gate refuses to load an uncached model and
    raises :class:`~voice_typer.server.asr_errors.ModelNotDownloadedError`
    so callers can point the user at the Models page. A cached-but-
    tampered model raises
    :class:`~voice_typer.server.asr_errors.ModelIntegrityError` and is
    NOT deleted automatically — deletion is an explicit user action
    (Models page Delete button).

    The probe is local-only (``local_files_only=True``) so no network
    traffic is generated and no consent is required — consent is only
    relevant for the explicit download path (``service.download_model``).
    """
    # Skip the gate for non-Whisper model sizes (e.g. "parakeet" or
    # "qwen") — those backends have their own load path.
    if not model_size or model_size in ("parakeet", "qwen"):
        log.debug(
            "[MODEL] Skipping download-required check for non-Whisper model '%s'",
            model_size,
        )
        return
    try:
        from huggingface_hub import snapshot_download

        repo_id = f"Systran/faster-whisper-{model_size}"

        # Use pinned revision from the MODEL_HASHES manifest.
        from voice_typer.server.security import MODEL_HASHES

        whisper_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

        # Shared allow-pattern list (see ``_model_integrity``).
        from voice_typer.server._model_integrity import ALLOW_PATTERNS_WHISPER

        if progress_callback:
            progress_callback(f"Checking model cache for '{model_size}'...")
        local_dir, integrity_failed = engine._probe_cache(
            snapshot_download,
            repo_id,
            whisper_revision,
            ALLOW_PATTERNS_WHISPER,
            model_size,
            progress_callback=progress_callback,
        )
        if local_dir is not None and not integrity_failed:
            log.info("[MODEL] Model '%s' already cached (integrity verified)", model_size)
            return
        if integrity_failed:
            raise ModelIntegrityError(
                f"The cached model '{model_size}' failed integrity verification. "
                "Delete it and download it again from the Models page to recover.",
                model_size=model_size,
                backend="whisper",
                repo_id=repo_id,
            )
        raise ModelNotDownloadedError(
            f"The Whisper model '{model_size}' is not downloaded yet. "
            "Open the Models page and click Download before using it.",
            model_size=model_size,
            backend="whisper",
            repo_id=repo_id,
        )
    except ImportError:
        # huggingface_hub unavailable — we cannot verify the cache, so
        # refuse to load (never auto-download) and point at Models page.
        raise ModelNotDownloadedError(
            f"The Whisper model '{model_size}' is not downloaded yet. "
            "Open the Models page and click Download before using it.",
            model_size=model_size,
            backend="whisper",
        ) from None


def whisper_size_cached(engine, model_size: str) -> bool:
    """Local-only probe: is ``model_size`` fully present in the HF cache?

    Used by the fallback chain to skip entries whose model has not been
    downloaded (the app never auto-downloads). Returns ``True`` when the
    probe is inconclusive (``huggingface_hub`` unavailable) so the load
    attempt is allowed to proceed — ``WhisperModel`` will surface its own
    error if the files are genuinely missing.
    """
    try:
        from huggingface_hub import snapshot_download

        repo_id = f"Systran/faster-whisper-{model_size}"

        from voice_typer.server.security import MODEL_HASHES

        revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

        from voice_typer.server._model_integrity import ALLOW_PATTERNS_WHISPER

        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            allow_patterns=ALLOW_PATTERNS_WHISPER,
            local_files_only=True,
        )
        return True
    except ImportError:
        # Cannot probe — allow the load attempt (WhisperModel will
        # surface its own error if the files are missing).
        return True
    except Exception:
        # Cache miss (or local probe failure) — never auto-download.
        return False
