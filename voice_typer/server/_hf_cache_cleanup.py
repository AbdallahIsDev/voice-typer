"""Shared HF cache-cleanup helpers: best-effort delete of tampered cache dirs.

This module is the CANONICAL entry point for HuggingFace cache-directory
cleanup after an integrity-check failure.  It exists to consolidate the
DRY violation: ``_cleanup_failed_cache`` was duplicated 3x across
``transcription.py``, ``asr_setup.py``, and ``parakeet_engine.py``
(plus the sibling helper ``_cleanup_hf_cache_dir`` in
``parakeet_engine.py``).

Pre-consolidation
-----------------
Each of the three ASR modules defined its own copy of the same body:

    cache_root = _config_dir() / "huggingface" / "hub"
    model_dir = cache_root / f"models--{repo_id.replace('/', '--')}"
    if model_dir.exists():
        shutil.rmtree(model_dir, ignore_errors=True)

Centralizing the cleanup here ensures any future fix to the path
resolution, the ignore_errors flag, or the log format only has to land
in one place.  The actual implementation body lives in
:func:`voice_typer.server.asr_utils.cleanup_hf_cache_dir` (which was
the first consolidation landing — see commit history); this module is a
thin, focused facade that re-exports it so callers can import the
cache-cleanup helper without pulling in the broader ``asr_utils``
namespace (which also contains GPU-memory release, download retry, disk
space checks, consent gating, cache-pruning, and audio chunking).

Backward compatibility
----------------------
The original module-local names are preserved as aliases at the bottom
of this file so existing callers/tests that import
``_cleanup_failed_cache`` or ``_cleanup_hf_cache_dir`` keep working:

- :data:`_cleanup_failed_cache` — alias of :func:`cleanup_failed_cache`
- :data:`_cleanup_hf_cache_dir` — alias of :func:`cleanup_hf_cache_dir`

The two public functions have different signatures on purpose:

- :func:`cleanup_failed_cache(repo_id, log_prefix="")` — the canonical
  signature used by the Whisper transcription path.  Takes the repo_id
  and an optional log prefix tag.
- :func:`cleanup_hf_cache_dir(repo_id, log_prefix="")` — same signature
  (was the original canonical name in ``asr_utils``; kept for parity).

Both names exist because the project's two historical helpers
(``_cleanup_failed_cache`` in asr_setup/transcription and
``_cleanup_hf_cache_dir`` in parakeet_engine) had drifted in naming
but implemented identical logic.  Centralizing here lets us keep both
names as stable surfaces while sharing one implementation body.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def cleanup_failed_cache(repo_id: str, log_prefix: str = "") -> None:
    """Best-effort delete a tampered HuggingFace cache directory.

    Thin canonical wrapper that delegates to
    :func:`voice_typer.server.asr_utils.cleanup_hf_cache_dir` (the
    single source of truth for the cleanup body — previously the same
    logic was duplicated 3x across ``transcription.py``,
    ``asr_setup.py``, and ``parakeet_engine.py``).

    Parameters
    ----------
    repo_id : str
        HuggingFace repository identifier (e.g.
        ``"Systran/faster-whisper-small.en"`` or
        ``"nvidia/parakeet-tdt-0.6b-v3"``).
    log_prefix : str
        Prefix tag for log messages so each calling module's logs are
        identifiable (e.g. ``"[MODEL]"``, ``"[PARAKEET]"``,
        ``"[ASR_SETUP]"``).  Defaults to ``""`` (no prefix).

    Notes
    -----
    Best-effort: logs but does not raise if the cleanup itself fails
    (e.g. file is locked on Windows, permission denied on POSIX).  The
    integrity hard-fail (``raise RuntimeError`` / fall-through to
    re-download) is the security gate; this cleanup is just hygiene so
    a retry doesn't silently re-load the same tampered files.
    """
    # Imported lazily inside the function body to avoid an import
    # cycle (``asr_utils`` imports from ``config`` which transitively
    # imports other server modules).  The late-binding also lets tests
    # monkeypatch ``asr_utils.cleanup_hf_cache_dir`` and have the
    # patched version take effect when this wrapper is called.
    from voice_typer.server.asr_utils import cleanup_hf_cache_dir as _impl

    _impl(repo_id, log_prefix=log_prefix)


def cleanup_hf_cache_dir(repo_id: str, log_prefix: str = "") -> None:
    """Best-effort delete a tampered HuggingFace cache directory.

    Canonical entry point — delegates to
    :func:`voice_typer.server.asr_utils.cleanup_hf_cache_dir` (the
    single source of truth for the cleanup body).  Kept under this name
    in this module for parity with ``asr_utils`` so callers can import
    the cache-cleanup helper from either location.

    Parameters
    ----------
    repo_id : str
        HuggingFace repository identifier (e.g.
        ``"Systran/faster-whisper-small.en"``).
    log_prefix : str
        Prefix tag for log messages.  Defaults to ``""`` (no prefix).

    See :func:`cleanup_failed_cache` for the full contract (the two
    functions are intentionally identical — they exist as separate
    names to preserve backward compatibility with both the historical
    ``_cleanup_failed_cache`` and ``_cleanup_hf_cache_dir`` call sites).
    """
    from voice_typer.server.asr_utils import cleanup_hf_cache_dir as _impl

    _impl(repo_id, log_prefix=log_prefix)


# ─── Backward-compat aliases ──────────────────────────────────────────────
#
# Preserve the underscore-prefixed names that the original 3 modules
# (transcription.py, asr_setup.py, parakeet_engine.py) used internally
# so any stray ``from voice_typer.server._hf_cache_cleanup import
# _cleanup_failed_cache`` (or ``_cleanup_hf_cache_dir``) imports — and
# any monkeypatch.setattr that targets these names on this module —
# keep working without code changes.
_cleanup_failed_cache = cleanup_failed_cache
_cleanup_hf_cache_dir = cleanup_hf_cache_dir

__all__ = [
    "cleanup_failed_cache",
    "cleanup_hf_cache_dir",
    "_cleanup_failed_cache",
    "_cleanup_hf_cache_dir",
]
