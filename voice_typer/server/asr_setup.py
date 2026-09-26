"""ASR engine setup and model-load helpers."""

import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# A single module-level ``threading.Event`` controls the pause state

# Semantics:
_download_pause_event: threading.Event | None = None
_download_abort_event: threading.Event | None = None
_download_pause_lock = threading.Lock()


def reset_download_pause_state() -> None:
    """Initialize the pause + abort flags at the start of a download."""
    global _download_pause_event, _download_abort_event
    with _download_pause_lock:
        _download_pause_event = threading.Event()
        _download_abort_event = threading.Event()
        # Both start cleared (not paused / not aborted).


def clear_download_pause_state() -> None:
    """Clear the pause + abort flags at the end of a download."""
    global _download_pause_event, _download_abort_event
    with _download_pause_lock:
        _download_pause_event = None
        _download_abort_event = None


def set_download_paused(paused: bool) -> bool:
    """Set or clear the pause flag.

    Returns ``True`` if the flag was successfully updated, ``False``
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
    """
    with _download_pause_lock:
        if _download_pause_event is None:
            return False
        return _download_pause_event.is_set()


def is_download_active() -> bool:
    """Return ``True`` while a gateable model download is in flight."""
    with _download_pause_lock:
        return _download_pause_event is not None


def wait_while_paused(timeout_s: float = 1.0) -> bool:
    """Block while the download is paused."""
    with _download_pause_lock:
        ev = _download_pause_event
    if ev is None:
        return True
    # If not paused, return immediately.
    if not ev.is_set():
        return True
    # Wait for the pause to be cleared (or timeout).
    return ev.wait(timeout=timeout_s)


def request_download_abort() -> bool:
    """Signal the in-flight transfer threads to abort (cancel)."""
    global _download_abort_event
    with _download_pause_lock:
        if _download_abort_event is None:
            log.debug("[PAUSE] request_download_abort called with no active download")
            return False
        _download_abort_event.set()
    log.info("[PAUSE] Model download abort (cancel) requested")
    return True


def _abort_requested() -> bool:
    """Return ``True`` if the transfer must stop."""
    with _download_pause_lock:
        ev = _download_abort_event
    return ev is None or ev.is_set()


class ModelDownloadAborted(BaseException):
    """Raised inside the transfer thread when a download is aborted."""


def check_download_gate() -> None:
    """Enforce pause/abort at a transfer chunk boundary. Shared by the"""
    import time as _time

    if _abort_requested():
        raise ModelDownloadAborted("model download aborted (cancel)")
    while is_download_paused():
        if _abort_requested():
            raise ModelDownloadAborted("model download aborted while paused")
        _time.sleep(0.2)


def get_download_tqdm_class() -> type:
    """Return the download progress-bar class that enforces pause/abort."""
    from huggingface_hub.utils.tqdm import tqdm as _hf_tqdm

    class _DownloadGateTqdm(_hf_tqdm):
        def update(self, n: int | float | None = 1) -> None:
            check_download_gate()
            super().update(n)

        # Kept for back-compat with the gate unit tests (delegates to
        def _gate_check(self) -> None:
            check_download_gate()

    return _DownloadGateTqdm


def force_http_download_path() -> None:
    """Force huggingface_hub onto the HTTP chunk path (disable xet)."""
    os.environ["HF_HUB_DISABLE_XET"] = "true"
    try:
        from huggingface_hub import constants as _hf_constants

        _hf_constants.HF_HUB_DISABLE_XET = True
    except Exception:  # pragma: no cover - hf not installed yet
        log.debug(
            "[PAUSE] huggingface_hub not imported yet; env var alone applies",
            exc_info=True,
        )


# SEC-audit-005 / CRIT-5 / SEC-2: allow-list imported from the shared
from voice_typer.server._model_integrity import (  # noqa: E402
    ALLOW_PATTERNS_PARAKEET as _HF_ALLOW_PATTERNS,
    ALLOW_PATTERNS_PARAKEET_ONNX as _HF_ALLOW_PATTERNS_PARAKEET_ONNX,
)

# removed the module-level ``_CONFIG_DIR`` cache.

# maximum number of download attempts (1 initial + 3 retries)
_MAX_DOWNLOAD_ATTEMPTS = 4

# the local ``_check_disk_space`` and ``_ESTIMATED_MODEL_SIZES``


def ensure_hf_env():
    """Ensure ancillary HF transfer flags (never forces HF_HOME)."""
    # Disable symlink warnings on Windows (Developer Mode not required)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # Disable xet transfer protocol, can be extremely slow on some connections
    os.environ.setdefault("HF_HUB_DISABLE_XET", "true")
    # Suppress "unauthenticated requests" nag
    os.environ.setdefault("HF_HUB_DISABLE_UNVERIFIED_ACCESS_WARNING", "1")
    # Disable huggingface_hub telemetry (C-DATA-1: no unsolicited egress).
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _verify_model_integrity(repo_id: str, local_dir: str) -> tuple[bool, dict[str, Any]]:
    """Verify downloaded model files have valid structure."""
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
        from voice_typer.server.security.model_integrity import hash_file_cached

        for filename, expected_hash in pinned_files.items():
            file_path = model_path / filename
            if not file_path.exists():
                details["failed_file"] = filename
                details["expected_hash"] = expected_hash
                details["actual_hash"] = None
                break
            try:
                # Cache-aware: a digest computed by the failed
                actual_hash = hash_file_cached(repo_id, file_path, filename)
            except Exception as exc:
                # Record the unhashable file as ``failed_file``
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
    """Cache cleanup: best-effort delete a tampered HF cache dir."""
    from voice_typer.server.asr_utils import cleanup_hf_cache_dir

    cleanup_hf_cache_dir(repo_id, log_prefix="[ASR_SETUP]")


def _run_parakeet_segmented_phase(
    *,
    repo_id: str,
    commit: str,
    seg_plan: Any,
    progress_callback: Callable[[str], None] | None,
) -> None:
    """Fetch the planned big Parakeet files via the segmented engine."""
    from voice_typer.server import segmented_download as segdl
    from voice_typer.server.model_availability import shared_hub_dir

    try:
        from huggingface_hub.utils import get_token

        token = get_token()
    except Exception:
        token = None
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def on_file(filename: str, index: int, total: int) -> None:
        if progress_callback:
            progress_callback(f"Downloading {filename} (fast lane, file {index + 1}/{total})...")

    segdl.run_segmented_phase(
        model_name="parakeet",
        repo_id=repo_id,
        commit=commit,
        cache_dir=shared_hub_dir(),
        seg_plan=seg_plan,
        progress_cb=None,
        file_cb=on_file,
        gate_check=check_download_gate,
        headers=headers,
        proxies=None,
    )


def download_parakeet_weights(
    progress_callback: Callable[[str], None] | None = None,
    config: Any = None,
    force: bool = False,
) -> tuple[bool, str, tuple[type, BaseException, Any] | None]:
    """Download Parakeet TDT v3 model weights via huggingface_hub."""
    # defense-in-depth consent gate with safe default.
    if not force and (config is None or not bool(getattr(config, "huggingface_consent", False))):
        if progress_callback:
            progress_callback("huggingface_consent_false")
        return (False, "huggingface_consent_false", None)

    ensure_hf_env()
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        # Append the install command so the user
        log.exception(
            "[ASR_SETUP] huggingface_hub not available for Parakeet download "
            "(install with: pip install huggingface_hub)"
        )
        if progress_callback:
            progress_callback("huggingface_hub not installed, cannot download weights")
        return (False, "huggingface_hub_missing", None)

    # The engine is ONNX-only post-migration (parakeet_engine.py). The
    repo_id = "grikdotnet/parakeet-tdt-0.6b-fp16"

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
            allow_patterns=_HF_ALLOW_PATTERNS_PARAKEET_ONNX,
            local_files_only=True,
        )
        # allow_patterns is a superset of the pinned manifest files;
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
            _cleanup_failed_cache(repo_id)
    except Exception as exc:
        # previously a bare ``except Exception: pass``.

        # NOTE (Fix-I / Fix-D coordination): Fix-D also touches this
        log.debug(
            "[ASR_SETUP] cache probe failed (%s); will re-download",
            exc,
        )

    # (revised): Use the canonical disk space check from
    try:
        from voice_typer.server.transcription import _check_disk_space_for_download

        _check_disk_space_for_download(repo_id, "parakeet")  # raises on insufficient space
    except RuntimeError as e:
        msg = str(e)
        log.exception("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return (False, "disk_space_insufficient", sys.exc_info())
    except Exception as e:
        # If the canonical check can't be imported, log and
        log.debug("[ASR_SETUP] canonical disk space check unavailable, proceeding: %s", e)

    msg = "Downloading Parakeet TDT v3 model..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    # Segmented fast lane for the big ONNX files (same design as the
    from voice_typer.server import segmented_download as segdl
    from voice_typer.server.security import MODEL_HASHES as _MH

    seg_plan = segdl.plan_segmented_files(
        repo_id=repo_id,
        revision=parakeet_revision,
        allow_patterns=_HF_ALLOW_PATTERNS_PARAKEET_ONNX,
        file_hashes=(_MH.get(repo_id, {}) or {}).get("files", {}),
    )
    seg_names = [p.filename for p in seg_plan] if seg_plan else []

    # Force the gateable HTTP transfer path (see force_http_download_path):
    force_http_download_path()
    # (revised): Use the canonical _download_with_retry from
    try:
        from voice_typer.server.transcription import _download_with_retry

        local_dir = _download_with_retry(
            snapshot_download,
            max_attempts=_MAX_DOWNLOAD_ATTEMPTS,
            delays=tuple(2**i for i in range(_MAX_DOWNLOAD_ATTEMPTS)),  # keep exponential backoff
            repo_id=repo_id,
            revision=parakeet_revision,
            allow_patterns=_HF_ALLOW_PATTERNS_PARAKEET_ONNX,
            # Segmented fast lane owns the big files, exclude them here.
            ignore_patterns=seg_names or None,
            resume_download=True,
            # pause/abort gate: see get_download_tqdm_class.
            tqdm_class=get_download_tqdm_class(),
        )
        if seg_plan:
            _run_parakeet_segmented_phase(
                repo_id=repo_id,
                commit=parakeet_revision,
                seg_plan=seg_plan,
                progress_callback=progress_callback,
            )
            # Self-verify by HF's own definition before the integrity
            try:
                snapshot_download(
                    repo_id=repo_id,
                    revision=parakeet_revision,
                    allow_patterns=sorted(_HF_ALLOW_PATTERNS_PARAKEET_ONNX),
                    local_files_only=True,
                )
            except Exception as e:
                log.warning(
                    "[ASR_SETUP] Post-segmented snapshot probe failed (%s), falling back to classic download",
                    e,
                )
                if progress_callback:
                    progress_callback("Retrying with standard download...")
                local_dir = _download_with_retry(
                    snapshot_download,
                    max_attempts=_MAX_DOWNLOAD_ATTEMPTS,
                    delays=tuple(2**i for i in range(_MAX_DOWNLOAD_ATTEMPTS)),
                    repo_id=repo_id,
                    revision=parakeet_revision,
                    allow_patterns=_HF_ALLOW_PATTERNS_PARAKEET_ONNX,
                    resume_download=True,
                    tqdm_class=get_download_tqdm_class(),
                )
    except Exception as e:
        # Capture the full ``sys.exc_info()`` triple into the
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
        _cleanup_failed_cache(repo_id)
        return (False, "integrity_check_failed", None)
    msg = "Parakeet model download complete"
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)
    return (True, "", None)
