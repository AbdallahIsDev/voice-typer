"""Single authoritative answer to "is model X fully on disk?"."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Maximum age of a fingerprint-identical entry. mtime equality is the
_FRESHNESS_MAX_AGE_S = 300.0


# repo snapshot dir leaf for an HF repo id.
def _snapshot_dir_name(repo_id: str) -> str:
    return f"models--{repo_id.replace('/', '--')}"


def snapshot_dir(config_dir: Path | str, repo_id: str) -> Path:
    """Canonical HF snapshot dir every consumer resolves identically."""
    return Path(config_dir) / "huggingface" / "hub" / _snapshot_dir_name(repo_id)


# Stored per key: (verdict, fingerprint, monotonic timestamp).
_store: dict[tuple[str, str, tuple[str, ...]], tuple[bool, tuple[Any, ...], float]] = {}
_LOCK = threading.Lock()

# Single-flight guard: at startup the tray menu build and the Models
_IN_FLIGHT: dict[tuple[str, str, tuple[str, ...]], threading.Event] = {}

# Bound on waiting for an in-flight probe owned by another thread. Real
_IN_FLIGHT_WAIT_S = 30.0

# Override hook for the in-flight-download check (tests replace it;
_download_active_impl: Callable[[], bool] | None = None


def _download_in_flight() -> bool:
    """True while any gateable model download is running (see module doc)."""
    global _download_active_impl
    fn = _download_active_impl
    if fn is None:
        try:
            from voice_typer.server.asr_setup import (
                is_download_active as fn,
            )
        except Exception:
            return False
        _download_active_impl = fn
    try:
        return bool(fn())
    except Exception:
        return False


def _default_probe(repo_id: str) -> bool:
    """The loader's own local-only snapshot probe (kept lazy so this"""
    from voice_typer.server.transcription_download import (
        is_model_snapshot_complete,
    )

    return is_model_snapshot_complete(repo_id)


def _fingerprint(watch_dirs: list[Path]) -> tuple[Any, ...]:
    parts: list[Any] = []
    for d in watch_dirs:
        try:
            st = os.stat(d)
            parts.append((str(d), st.st_mtime_ns))
        except OSError:
            parts.append((str(d), None))
    return tuple(parts)


def is_available(
    repo_id: str,
    config_dir: Path | str,
    *,
    extra_watch_dirs: list[Path | str] | None = None,
    probe: Callable[[str], bool] | None = None,
) -> bool:
    """Authoritative "is this repo fully on disk?" verdict."""
    cfg = Path(config_dir)
    watch = [snapshot_dir(cfg, repo_id)]
    for extra in extra_watch_dirs or []:
        watch.append(Path(extra))
    key = (repo_id, str(cfg), tuple(str(d) for d in watch))
    check_probe = probe if probe is not None else _default_probe

    # Join-or-own loop: serve a fresh verdict, join an in-flight probe
    event: threading.Event | None = None
    for _ in range(3):
        now = time.monotonic()
        with _LOCK:
            entry = _store.get(key)
            if entry is not None and not _download_in_flight():
                verdict, fingerprint, ts = entry
                if (now - ts) < _FRESHNESS_MAX_AGE_S and fingerprint == _fingerprint(watch):
                    return verdict
            event = _IN_FLIGHT.get(key)
            if event is None:
                event = threading.Event()
                _IN_FLIGHT[key] = event
                break
        # Another thread owns the probe for this key: wait for its
        event.wait(timeout=_IN_FLIGHT_WAIT_S)
    else:
        event = None
    try:
        verdict = bool(check_probe(repo_id))
    except Exception:
        with _LOCK:
            if event is not None and _IN_FLIGHT.get(key) is event:
                del _IN_FLIGHT[key]
                event.set()
        raise
    fingerprint = _fingerprint(watch)
    with _LOCK:
        _store[key] = (verdict, fingerprint, time.monotonic())
        if event is not None and _IN_FLIGHT.get(key) is event:
            del _IN_FLIGHT[key]
            event.set()
    return verdict


def invalidate(repo_id: str | None = None, config_dir: Path | str | None = None) -> None:
    """Drop cached verdicts (all, or one repo)."""
    with _LOCK:
        if repo_id is None:
            _store.clear()
            return
        drop = [key for key in _store if key[0] == repo_id and (config_dir is None or key[1] == str(Path(config_dir)))]
        for key in drop:
            del _store[key]


def _store_snapshot_for_test() -> dict:
    """Return a shallow copy of the store (tests assert sharing)."""
    with _LOCK:
        return dict(_store)
