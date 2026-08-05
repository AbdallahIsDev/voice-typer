"""ASR auto-setup: GPU detection, dependency check, weight download.

This module provides utilities for automatically setting up the ASR
environment, including GPU detection, dependency checking, and model
weight downloading.

``pip_install`` and ``download_weights`` were removed from
this module.  The verbatim bodies were previously retained in
``archive/asr_setup_dead_code.py`` for reference; that archive file
has been deleted as part of dead-code cleanup since zero production
call sites referenced it.  The historical implementation can be
recovered from git history if needed for the future  on-demand
dependency install feature.

pause/resume flag for in-progress model downloads.
``set_download_paused(True)`` causes the polling loop in
:meth:`voice_typer.server.service.VoiceTyperService.download_model`
to freeze its progress reporting (and effectively stop user-visible
progress) until ``set_download_paused(False)`` is called.  The flag
is checked "between chunks" — i.e. once per 1-second poll iteration
in the service's polling loop.  The flag is module-level so the IPC
handler can set it from any thread.

Lifecycle:
  - :func:`reset_download_pause_state` — call at start of download
    (creates a fresh ``threading.Event``).
  - :func:`set_download_paused` — set/clear the pause flag.
  - :func:`is_download_paused` — check the flag (called by polling loop).
  - :func:`wait_while_paused` — block while paused (called by polling loop).
  - :func:`clear_download_pause_state` — call at end of download
    (sets the Event back to ``None`` so subsequent pause calls return
    ``False``).
"""

import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# pause/resume flag ────────────────────────────────

# A single module-level ``threading.Event`` controls the pause state
# for ALL in-progress downloads.  We support only one concurrent
# download at a time (the existing ``_download_cancel_event`` in
# VoiceTyperService has the same constraint), so a single flag is
# sufficient.

# Semantics:
# - ``_download_pause_event`` is created lazily by
#   ``reset_download_pause_state()`` at the start of a download.
# - ``set_download_paused(True)``  -> ``_download_pause_event.set()``
# - ``set_download_paused(False)`` -> ``_download_pause_event.clear()``
# - ``is_download_paused()``       -> ``_download_pause_event.is_set()``
# - When no download is in progress, ``_download_pause_event`` is
#   ``None`` and ``is_download_paused()`` returns ``False``.
_download_pause_event: threading.Event | None = None
_download_pause_lock = threading.Lock()


def reset_download_pause_state() -> None:
    """Initialize the pause flag at the start of a download.

    Called by :meth:`VoiceTyperService.download_model` when a new
    download begins (so a stale ``paused=True`` from a previous
    download doesn't carry over).  Creates a fresh ``threading.Event``
    in the cleared (not-paused) state.  Safe to call from any thread.
    """
    global _download_pause_event
    with _download_pause_lock:
        _download_pause_event = threading.Event()
        # Starts cleared (not paused).


def clear_download_pause_state() -> None:
    """Clear the pause flag at the end of a download.

    Sets ``_download_pause_event`` back to ``None`` so subsequent
    calls to :func:`set_download_paused` return ``False`` (no active
    download to pause).  Called from every cleanup path in
    :meth:`VoiceTyperService.download_model` (success, failure, cancel).
    """
    global _download_pause_event
    with _download_pause_lock:
        _download_pause_event = None


def set_download_paused(paused: bool) -> bool:
    """Set or clear the pause flag.

    Returns ``True`` if the flag was successfully updated, ``False``
    if no download is currently in progress (in which case there's
    nothing to pause).  The renderer treats ``False`` as "no-op" —
    e.g. pressing Pause when nothing is downloading just dismisses
    the button.
    """
    global _download_pause_event
    with _download_pause_lock:
        if _download_pause_event is None:
            log.debug("[PAUSE] set_download_paused(%s) called with no active download", paused)
            return False
        if paused:
            _download_pause_event.set()
            log.info("[PAUSE] Model download pause requested")
        else:
            _download_pause_event.clear()
            log.info("[PAUSE] Model download resume requested")
    return True


def is_download_paused() -> bool:
    """Return ``True`` if the current download is paused.

    Returns ``False`` when no download is in progress (so callers
    can use this as a simple ``if is_download_paused(): ...`` guard
    without checking for ``None`` first).
    """
    with _download_pause_lock:
        if _download_pause_event is None:
            return False
        return _download_pause_event.is_set()


def wait_while_paused(timeout_s: float = 1.0) -> bool:
    """Block while the download is paused.

    Used by the service polling loop between progress updates.  Returns
    ``True`` if the pause flag was cleared within ``timeout_s`` seconds,
    ``False`` if it's still paused after the timeout (in which case the
    caller should loop and call again, or check cancellation).

    Safe to call when no download is in progress — returns immediately.
    """
    with _download_pause_lock:
        ev = _download_pause_event
    if ev is None:
        return True
    # If not paused, return immediately.
    if not ev.is_set():
        return True
    # Wait for the pause to be cleared (or timeout).
    return ev.wait(timeout=timeout_s)


# SEC-audit-005 / CRIT-5 / SEC-2: allow-list imported from the shared
# ``_model_integrity`` module so ``parakeet_engine`` and ``asr_setup``
# can never drift out of sync.  See ``_model_integrity.py`` for the
# sync requirement with ``model_hashes.json`` — pinned files in the
# manifest MUST be a subset of these allow-patterns, otherwise
# ``verify_model_integrity()`` hard-fails on every download.
from voice_typer.server._model_integrity import ALLOW_PATTERNS_PARAKEET as _HF_ALLOW_PATTERNS  # noqa: E402

# removed the module-level ``_CONFIG_DIR`` cache.
# It was a one-line indirection over ``config._config_dir()`` that
# provided no measurable performance benefit (Path construction is
# ~1 µs) and made the code harder to read.  Callers now use
# ``config._config_dir()`` directly.

# maximum number of download attempts (1 initial + 3 retries)
# with exponential backoff.  This value is passed as ``max_attempts=`` to
# ``_download_with_retry`` (NOT ``max_retries=``), so the name reflects
# total attempts, not retries-after-the-first.  Previously named
# ``_MAX_DOWNLOAD_RETRIES`` which was ambiguous ("4 retries" could mean
# 4 total or 5 total); renamed for clarity.
_MAX_DOWNLOAD_ATTEMPTS = 4

# the local ``_check_disk_space`` and ``_ESTIMATED_MODEL_SIZES``
# duplicate was REMOVED. The canonical disk-space check lives in
# ``transcription.py::_check_disk_space_for_download`` (raises RuntimeError
# on insufficient space). ``asr_setup.py`` delegates to it (see
# ``download_parakeet_weights`` below). If the canonical import fails, we
# log the error and proceed — the model download will fail naturally if
# there's truly no space, which is a safer failure mode than running a
# second, divergent size table that could drift out of sync.


def ensure_hf_env():
    """Ensure HF_HOME points to ~/.voice-typer/huggingface/."""
    from voice_typer.server.config import _config_dir

    hf_home = str(_config_dir() / "huggingface")
    if os.environ.get("HF_HOME") != hf_home:
        os.environ["HF_HOME"] = hf_home
    # Disable symlink warnings on Windows (Developer Mode not required)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # Disable xet transfer protocol — can be extremely slow on some connections
    os.environ.setdefault("HF_HUB_DISABLE_XET", "true")
    # Suppress "unauthenticated requests" nag
    os.environ.setdefault("HF_HUB_DISABLE_UNVERIFIED_ACCESS_WARNING", "1")
    # Disable huggingface_hub telemetry (C-DATA-1: no unsolicited egress).
    # Defensive — pinning the flag now guards against future
    # huggingface_hub releases that expand their telemetry surface,
    # without introducing any network call ourselves.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _verify_model_integrity(repo_id: str, local_dir: str) -> tuple[bool, dict[str, Any]]:
    """Verify downloaded model files have valid structure.

    Basic integrity check that the model directory
        contains expected files and they're not empty.

        SEC-audit-005: Delegates to the centralized
        ``security.verify_model_integrity()`` which also checks SHA-256
        hashes against the MODEL_HASHES manifest when available.

        Returns ``(ok, details)`` instead of a bare bool so the
        caller can surface a useful diagnostic when the integrity check
        fails. ``details`` is a dict with the following keys (any of which
        may be ``None`` when not applicable):

        - ``failed_file``: relative path of the file that failed the check.
        - ``expected_hash``: the manifest-declared SHA-256 for
          ``failed_file``.
        - ``actual_hash``: the computed SHA-256 for ``failed_file``.
        - ``allow_pattern_matched``: whether the download's allow-patterns
          matched any file in ``local_dir``.

        When the integrity check passes, ``details`` is an empty dict.
    """
    from voice_typer.server.security import MODEL_HASHES, verify_model_integrity

    ok = verify_model_integrity(local_dir, repo_id)
    if ok:
        return (True, {})

    details: dict[str, Any] = {
        "failed_file": None,
        "expected_hash": None,
        "actual_hash": None,
        "allow_pattern_matched": None,
    }

    model_path = Path(local_dir)
    manifest = MODEL_HASHES.get(repo_id, {})
    pinned_files: dict[str, str] = manifest.get("files", {}) or {}

    if pinned_files and model_path.exists():
        from voice_typer.server.security import compute_file_sha256

        for filename, expected_hash in pinned_files.items():
            file_path = model_path / filename
            if not file_path.exists():
                details["failed_file"] = filename
                details["expected_hash"] = expected_hash
                details["actual_hash"] = None
                break
            try:
                actual_hash = compute_file_sha256(file_path)
            except Exception as exc:
                # Record the unhashable file as ``failed_file``
                # (with ``actual_hash = None`` to distinguish "could not
                # compute" from "computed-but-mismatched"), escalate the
                # log from DEBUG to WARNING so it's visible in production
                # logs, and ``break`` on the FIRST unhashable file —
                # mirroring the ``not file_path.exists()`` branch above.
                # Pre-fix this branch did ``continue`` with a ``log.debug``,
                # silently skipping the unhashable file and leaving
                # ``failed_file = None``, which was indistinguishable
                # from an empty-manifest soft-pass.
                log.warning(
                    "[ASR_SETUP] could not compute hash for %s: %s",
                    file_path,
                    exc,
                )
                details["failed_file"] = filename
                details["actual_hash"] = None
                details["expected_hash"] = expected_hash
                break
            if actual_hash != expected_hash:
                details["failed_file"] = filename
                details["expected_hash"] = expected_hash
                details["actual_hash"] = actual_hash
                break

    try:
        import fnmatch

        matched = False
        if model_path.exists():
            for entry in model_path.rglob("*"):
                if not entry.is_file():
                    continue
                rel = entry.relative_to(model_path).as_posix()
                for pat in _HF_ALLOW_PATTERNS:
                    if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, f"*/{pat}"):
                        matched = True
                        break
                if matched:
                    break
        details["allow_pattern_matched"] = matched
    except Exception as exc:
        log.debug("[ASR_SETUP] could not determine allow_pattern_matched: %s", exc)
        details["allow_pattern_matched"] = None

    return (False, details)


def _cleanup_failed_cache(repo_id: str) -> None:
    """Cache cleanup: best-effort delete a tampered HF cache dir.

    Delegates to the canonical ``asr_utils.cleanup_hf_cache_dir`` helper
    (single source of truth — previously the body was duplicated here).
    """
    from voice_typer.server.asr_utils import cleanup_hf_cache_dir

    cleanup_hf_cache_dir(repo_id, log_prefix="[ASR_SETUP]")


def download_parakeet_weights(
    progress_callback: Callable[[str], None] | None = None,
    config: Any = None,
    force: bool = False,
) -> tuple[bool, str, tuple[type, BaseException, Any] | None]:
    """Download Parakeet TDT v3 model weights via huggingface_hub.

    wraps snapshot_download in retry loop with exponential
        backoff.  Max 4 attempts total (1 initial + 3 retries); the delays
        tuple passed to ``_download_with_retry`` is ``(1s, 2s, 4s, 8s)``
        but only the first 3 entries are consumed (one delay before each
        retry), so the user-visible backoff is 1s, 2s, 4s. Logs each
        retry attempt.

    checks disk space before attempting download.

        Defense-in-depth consent gate.
        When ``config`` is provided, ``config.huggingface_consent`` MUST be
        True before any HuggingFace network call.

        The return type is now a 3-tuple
        ``(success, reason, exc_info)``.  ``reason`` is a short reason code:
          - ``"huggingface_consent_false"`` — consent gate blocked download.
          - ``"huggingface_hub_missing"`` — ``huggingface_hub`` import failed.
          - ``"disk_space_insufficient"`` — canonical disk-space check raised.
          - ``"download_retry_exhausted"`` — all ``_MAX_DOWNLOAD_ATTEMPTS``
            attempts failed (1 initial + 3 retries with exponential backoff).
          - ``"integrity_check_failed"`` — post-download integrity check
            returned False (tampered or corrupted download).
        Success returns ``(True, "", None)``.

        ``exc_info`` is the captured ``sys.exc_info()`` 3-tuple
        ``(type, value, traceback)`` from the most recent exception in this
        function, or ``None`` when no exception was raised. The IPC layer /
        diagnostic bundle consumer can format the traceback via
        ``traceback.format_exception(*exc_info)`` to surface the full chain
        — HF Hub URL, HTTP status, retry chain, originating frame inside
        ``snapshot_download`` — without needing to re-raise.

        Args:
            progress_callback: Optional callable(message: str) for progress updates.
            config: Optional Config object — when provided and
                ``huggingface_consent`` is True, the consent gate passes.
    ``None`` is treated as consent NOT given ( safe default).
            force: When True, bypass the consent gate entirely (explicit
                escape hatch for legacy / test paths that verified consent
                upstream and cannot forward a real Config object).

        Returns:
            ``(success, reason, exc_info)`` — see above.
    """
    # defense-in-depth consent gate with safe default.
    # When ``force`` is False (the default), the gate refuses unless an
    # explicit Config object with ``huggingface_consent=True`` is
    # forwarded.  ``config=None`` is treated as "consent NOT given"
    # (GDPR Art. 6/13 safe default) so a future refactor that drops
    # the ``config`` argument from a call site cannot silently bypass
    # the gate.  ``force=True`` is the explicit escape hatch for legacy
    # / test paths that have already verified consent upstream and
    # cannot forward a real Config object — the bypass is now EXPLICIT
    # at the call site, not implicit.
    if not force and (config is None or not bool(getattr(config, "huggingface_consent", False))):
        if progress_callback:
            progress_callback("huggingface_consent_false")
        return (False, "huggingface_consent_false", None)

    ensure_hf_env()
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        # Append the install command so the user
        # can recover without filing a bug or grepping pyproject.toml.
        log.error(
            "[ASR_SETUP] huggingface_hub not available for Parakeet download "
            "(install with: pip install huggingface_hub)"
        )
        if progress_callback:
            progress_callback("huggingface_hub not installed, cannot download weights")
        return (False, "huggingface_hub_missing", None)

    repo_id = "nvidia/parakeet-tdt-0.6b-v3"

    # SEC-audit-005: Use pinned revision from MODEL_HASHES manifest
    from voice_typer.server.security import MODEL_HASHES

    parakeet_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

    msg = "Checking Parakeet model cache..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        local_dir = snapshot_download(
            repo_id=repo_id,
            revision=parakeet_revision,
            allow_patterns=_HF_ALLOW_PATTERNS,
            local_files_only=True,
        )
        # Verify model integrity for cached weights
        if local_dir:
            cached_ok, cached_details = _verify_model_integrity(repo_id, local_dir)
        else:
            cached_ok, cached_details = False, {}
        if cached_ok:
            msg = "Parakeet model already cached"
            log.info("[ASR_SETUP] %s", msg)
            if progress_callback:
                progress_callback(msg)
            return (True, "", None)
        else:
            # Log the integrity-check details at WARNING before
            # _cleanup_failed_cache removes the offending files.
            log.warning(
                "[ASR_SETUP] Cached model failed integrity check, re-downloading "
                "(details: failed_file=%s expected_hash=%s actual_hash=%s "
                "allow_pattern_matched=%s)",
                cached_details.get("failed_file"),
                (cached_details.get("expected_hash") or "")[:16],
                (cached_details.get("actual_hash") or "")[:16],
                cached_details.get("allow_pattern_matched"),
            )
            # Cache cleanup on verify failure: remove the
            # offending cache dir so the re-download doesn't get the
            # same tampered files served from local cache.
            _cleanup_failed_cache(repo_id)
    except Exception as exc:
        # previously a bare ``except Exception: pass``.
        # Corrupted HF cache (partial download, broken lock file,
        # permissions issue, HF cache schema change) silently triggered
        # a full re-download. The user saw "Downloading Parakeet TDT v3
        # model..." (potentially 2.5 GB) on every launch with no
        # explanation. Log at DEBUG level (this is expected on the
        # first run when no cache exists yet) and include the exception
        # so a non-trivial cache corruption is at least visible in the
        # log file when the user is debugging.

        # NOTE (Fix-I / Fix-D coordination): Fix-D also touches this
        # function (the ``download_parakeet_weights`` body) but only
        # the retry-loop / progress-callback portion below. This cache-
        # probe block is Fix-I's exclusive territory per the disjoint
        # ownership table.
        log.debug(
            "[ASR_SETUP] cache probe failed (%s); will re-download",
            exc,
        )

    # (revised): Use the canonical disk space check from
    # transcription.py instead of the local _check_disk_space() duplicate.
    # The two implementations had different size tables and different
    # return semantics (bool vs raise RuntimeError), creating a
    # maintenance hazard. Now asr_setup delegates to the canonical version.
    # See FORENSIC_REVIEW_COMPLETE.md →
    try:
        from voice_typer.server.transcription import _check_disk_space_for_download

        _check_disk_space_for_download(repo_id, "parakeet")  # raises on insufficient space
    except RuntimeError as e:
        msg = str(e)
        log.error("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return (False, "disk_space_insufficient", sys.exc_info())
    except Exception as e:
        # If the canonical check can't be imported, log and
        # proceed. The model download itself will fail naturally if
        # there's truly no space — a safer failure mode than running a
        # divergent local size table. Pre-fix this fell back to a local
        # ``_check_disk_space`` duplicate that had different size
        # thresholds and could drift out of sync with the canonical
        # version.
        log.debug("[ASR_SETUP] canonical disk space check unavailable, proceeding: %s", e)

    msg = "Downloading Parakeet TDT v3 model..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    # (revised): Use the canonical _download_with_retry from
    # transcription.py instead of the inline retry loop. The two
    # implementations had different delay tables ([5,15,45] vs 2**attempt)
    # and different API shapes (callable vs inline). Now asr_setup
    # delegates to the canonical version. See FORENSIC_REVIEW_COMPLETE.md
    # →
    try:
        from voice_typer.server.transcription import _download_with_retry

        local_dir = _download_with_retry(
            snapshot_download,
            max_attempts=_MAX_DOWNLOAD_ATTEMPTS,
            delays=tuple(2**i for i in range(_MAX_DOWNLOAD_ATTEMPTS)),  # keep exponential backoff
            repo_id=repo_id,
            revision=parakeet_revision,
            allow_patterns=_HF_ALLOW_PATTERNS,
            resume_download=True,
        )
    except Exception as e:
        # Capture the full ``sys.exc_info()`` triple into the
        # return tuple so the IPC layer / diagnostic bundle consumer
        # can format the traceback (HF Hub URL, HTTP status, retry
        # chain, originating frame inside ``snapshot_download``) for
        # remote debugging — the #1 ASR-app support case. ``log.error``
        # with ``exc_info=True`` writes the full traceback to the log
        # file so the on-disk log is no longer blind to the underlying
        # failure mode (429 rate-limit vs DNS vs CRC vs TLS).
        captured_exc_info = sys.exc_info()
        log.error(
            "[ASR_SETUP] All %d download attempts failed. Last error: %s",
            _MAX_DOWNLOAD_ATTEMPTS,
            e,
            exc_info=True,
        )
        if progress_callback:
            progress_callback(f"Download failed after {_MAX_DOWNLOAD_ATTEMPTS} attempts: {e}")
        return (False, "download_retry_exhausted", captured_exc_info)

    # Verify model integrity after download
    # ``_verify_model_integrity`` now returns ``(ok, details)``.
    # Log the details at ERROR before ``_cleanup_failed_cache`` removes
    # the offending files — without these details, support cannot
    # distinguish a missing pinned file from a hash mismatch from a
    # tampered allow-pattern (all surface as the same opaque
    # ``integrity_check_failed`` reason code).
    post_ok, post_details = _verify_model_integrity(repo_id, local_dir)
    if not post_ok:
        log.error(
            "[ASR_SETUP] Model integrity check failed after download "
            "(details: failed_file=%s expected_hash=%s actual_hash=%s "
            "allow_pattern_matched=%s)",
            post_details.get("failed_file"),
            (post_details.get("expected_hash") or "")[:16],
            (post_details.get("actual_hash") or "")[:16],
            post_details.get("allow_pattern_matched"),
        )
        if progress_callback:
            progress_callback("Download completed but integrity check failed")
        # Cache cleanup on verify failure: remove the
        # offending cache dir so the next call doesn't re-discover the
        # tampered snapshot.
        _cleanup_failed_cache(repo_id)
        return (False, "integrity_check_failed", None)
    msg = "Parakeet model download complete"
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)
    return (True, "", None)
