"""Single authoritative answer to "is model X fully on disk?" (BP-158).

Previously every consumer grew its own stat-the-disk check with its
own TTL band-aid: the service status poll (5 s TTL dict in
``service/model/_status.py``) and the tray submenu (5 s TTL dict in
``tray_models.py``). N consumers × every-5-s expiry meant sustained
redundant snapshot walks forever, a known-stale window (a download
finishing inside the TTL still read "not downloaded"), and two
caches that could disagree.

This module owns ONE store keyed by ``(repo_id, config_dir,
extra-watch fingerprint)``. Freshness comes from filesystem mtime,
not expiry:

- each check stats the watched directories (1 syscall each);
- if every mtime matches the stored fingerprint AND the entry is
  younger than ``_FRESHNESS_MAX_AGE_S``, the cached bool is served;
- otherwise the full ``is_model_snapshot_complete()`` probe runs
  once and the entry is re-stored.

Correctness therefore comes from mtime + explicit invalidation, not
from TTL expiry: ``invalidate()`` is called on every mutation path
(download complete / delete / import, the same call sites that
already invalidated the two legacy caches), so a finished download
reflects instantly everywhere instead of within 5 s.

Mid-download guard: while ANY gateable download is in flight
(``asr_setup.is_download_active()``), the freshness shortcut is
skipped and every check re-probes. Downloads are rare and long;
during one, truth matters more than syscall savings, and this closes
the mtime-granularity hole (two writes landing in the same mtime
tick) that could otherwise flash a stale True. Resolved lazily so
this module stays import-light.

Thread-safety: the tray tick, IPC handlers, and download threads all
consult this store, every read-modify-write holds ``_LOCK``.
"""

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
# real freshness signal; the age cap only bounds how long a *stale
# filesystem view* (e.g. a network mount that stopped updating mtimes)
# can be trusted. 300 s matches the entry's Fix prescription.
_FRESHNESS_MAX_AGE_S = 300.0


# repo snapshot dir leaf for an HF repo id.
def _snapshot_dir_name(repo_id: str) -> str:
    return f"models--{repo_id.replace('/', '--')}"


def snapshot_dir(config_dir: Path | str, repo_id: str) -> Path:
    """Canonical HF snapshot dir every consumer resolves identically.

    Centralizes the ``huggingface/hub/models--<repo>`` recipe that was
    previously inlined (with identical strings) in both the service
    status probe and the tray probe.
    """
    return Path(config_dir) / "huggingface" / "hub" / _snapshot_dir_name(repo_id)


# Stored per key: (verdict, fingerprint, monotonic timestamp).
# Fingerprint = tuple of (str(path), mtime_ns | None) over every
# watched directory, so ANY layout change (add/remove/touch) busts it.
_store: dict[tuple[str, ...], tuple[bool, tuple[Any, ...], float]] = {}
_LOCK = threading.Lock()

# Override hook for the in-flight-download check (tests replace it;
# production lazily resolves ``asr_setup.is_download_active`` once).
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
    """The loader's own local-only snapshot probe (kept lazy so this
    module stays import-light, ``transcription_download`` pulls the
    HF toolchain)."""
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
    """Authoritative "is this repo fully on disk?" verdict.

    *config_dir* anchors the HF cache layout; *extra_watch_dirs* adds
    configured local model paths (qwen/parakeet) to the freshness
    fingerprint. *probe* overrides the snapshot probe (tests inject a
    call-counting stub; production uses the loader's own probe).
    """
    cfg = Path(config_dir)
    watch = [snapshot_dir(cfg, repo_id)]
    for extra in extra_watch_dirs or []:
        watch.append(Path(extra))
    key = (repo_id, str(cfg), tuple(str(d) for d in watch))
    check_probe = probe if probe is not None else _default_probe

    now = time.monotonic()
    with _LOCK:
        entry = _store.get(key)
        if entry is not None and not _download_in_flight():
            verdict, fingerprint, ts = entry
            if (now - ts) < _FRESHNESS_MAX_AGE_S and fingerprint == _fingerprint(watch):
                return verdict
    verdict = bool(check_probe(repo_id))
    fingerprint = _fingerprint(watch)
    with _LOCK:
        _store[key] = (verdict, fingerprint, time.monotonic())
    return verdict


def invalidate(repo_id: str | None = None, config_dir: Path | str | None = None) -> None:
    """Drop cached verdicts (all, or one repo).

    Called on every mutation path (download complete / delete /
    import), the same sites that already invalidated the two legacy
    caches. With no args the whole store clears (matches the legacy
    ``clear()`` semantics); with a repo (+ optional config dir) only
    matching entries drop.
    """
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
