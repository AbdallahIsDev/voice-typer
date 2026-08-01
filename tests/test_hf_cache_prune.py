"""Tests for ``prune_model_cache`` (HF model cache size-based eviction).

Verifies the helper that enforces a size cap on the HuggingFace model
cache by deleting the oldest cached repos (by ``last_modified``) until
the total on-disk size is under the cap. Covers:

1. Happy path — oldest models pruned first, newest preserved.
2. Multiple deletions — prune loops until under the cap.
3. No-op when already under the cap.
4. Fallback path when ``huggingface_hub.scan_cache_dir`` raises
   ``ImportError`` (manual ``iterdir`` + ``stat().st_mtime`` scan).
5. Fallback path when ``huggingface_hub`` is not importable at all.
6. Returns 0 when ``cache_dir`` doesn't exist.
7. Default ``max_bytes`` equals 8 GB (the ``_MAX_MODEL_CACHE_GB``
   constant).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_fake_repo(cache_dir: Path, name: str, size_bytes: int, mtime_offset_s: float) -> Path:
    """Create a fake HF model cache dir ``models--Org--<name>``.

    The directory mimics the real HF hub cache layout
    (``models--Org--Name/snapshots/<commit>/weights.bin``) so
    ``huggingface_hub.scan_cache_dir`` recognises it as a cached repo.

    ``mtime_offset_s`` is subtracted from ``time.time()`` and applied
    to both the repo dir and its blob so the repo's ``last_modified``
    reflects the requested age (larger offset = older repo).
    """
    repo_dir = cache_dir / f"models--Org--{name}"
    snap_dir = repo_dir / "snapshots" / "abc123def456"
    snap_dir.mkdir(parents=True, exist_ok=True)
    blob = snap_dir / "weights.bin"
    # Use truncate to create a file of the exact requested size without
    # writing every byte (faster for large sizes).
    with open(blob, "wb") as f:
        f.truncate(size_bytes)
    # Age the dir + file mtimes so last_modified reflects the offset.
    target_mtime = time.time() - mtime_offset_s
    os.utime(blob, (target_mtime, target_mtime))
    os.utime(snap_dir, (target_mtime, target_mtime))
    os.utime(repo_dir, (target_mtime, target_mtime))
    return repo_dir


def _total_dir_size(cache_dir: Path) -> int:
    """Recompute the total on-disk size of all top-level dirs."""
    total = 0
    for entry in cache_dir.iterdir():
        if entry.is_dir():
            for f in entry.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
    return total


# ──────────────────────────────────────────────────────────────────
# Happy path: oldest pruned, newest preserved
# ──────────────────────────────────────────────────────────────────


def test_prune_deletes_oldest_until_under_cap(tmp_path):
    """Three repos (oldest→newest): 4 MB, 2 MB, 1 MB. Cap = 4 MB.

    Total = 7 MB > 4 MB → delete oldest (4 MB) → remaining 3 MB ≤ 4 MB.
    Exactly 1 repo pruned; the newest (1 MB) survives."""
    from voice_typer.server.asr_utils import prune_model_cache

    cache_dir = tmp_path / "hub"
    cache_dir.mkdir()
    _make_fake_repo(cache_dir, "oldest", 4 * 1024 * 1024, mtime_offset_s=300)
    _make_fake_repo(cache_dir, "middle", 2 * 1024 * 1024, mtime_offset_s=200)
    _make_fake_repo(cache_dir, "newest", 1 * 1024 * 1024, mtime_offset_s=100)

    pruned = prune_model_cache(cache_dir, max_bytes=4 * 1024 * 1024)

    assert pruned == 1, f"expected 1 repo pruned, got {pruned}"
    assert not (cache_dir / "models--Org--oldest").exists(), "oldest must be pruned"
    assert (cache_dir / "models--Org--middle").exists(), "middle must survive"
    assert (cache_dir / "models--Org--newest").exists(), "newest must survive"
    assert _total_dir_size(cache_dir) <= 4 * 1024 * 1024


def test_prune_deletes_multiple_until_under_cap(tmp_path):
    """Cap = 1 MB. Repos: 5 MB (oldest), 3 MB (middle), 0.5 MB (newest).

    Delete oldest (5 MB) → 3.5 MB > 1 MB. Delete middle (3 MB) → 0.5 MB
    ≤ 1 MB. 2 repos pruned; newest survives."""
    from voice_typer.server.asr_utils import prune_model_cache

    cache_dir = tmp_path / "hub"
    cache_dir.mkdir()
    _make_fake_repo(cache_dir, "oldest", 5 * 1024 * 1024, mtime_offset_s=400)
    _make_fake_repo(cache_dir, "middle", 3 * 1024 * 1024, mtime_offset_s=300)
    _make_fake_repo(cache_dir, "newest", 512 * 1024, mtime_offset_s=100)

    pruned = prune_model_cache(cache_dir, max_bytes=1024 * 1024)

    assert pruned == 2, f"expected 2 repos pruned, got {pruned}"
    assert not (cache_dir / "models--Org--oldest").exists()
    assert not (cache_dir / "models--Org--middle").exists()
    assert (cache_dir / "models--Org--newest").exists(), "newest must survive"
    assert _total_dir_size(cache_dir) <= 1024 * 1024


def test_prune_noop_when_under_cap(tmp_path):
    """When total size ≤ max_bytes, no repos are pruned."""
    from voice_typer.server.asr_utils import prune_model_cache

    cache_dir = tmp_path / "hub"
    cache_dir.mkdir()
    _make_fake_repo(cache_dir, "a", 100 * 1024, mtime_offset_s=300)
    _make_fake_repo(cache_dir, "b", 200 * 1024, mtime_offset_s=200)

    pruned = prune_model_cache(cache_dir, max_bytes=1024 * 1024 * 1024)

    assert pruned == 0
    assert (cache_dir / "models--Org--a").exists()
    assert (cache_dir / "models--Org--b").exists()


# ──────────────────────────────────────────────────────────────────
# Fallback path: scan_cache_dir raises ImportError
# ──────────────────────────────────────────────────────────────────


def test_prune_fallback_when_scan_cache_dir_raises_importerror(tmp_path, monkeypatch):
    """When ``huggingface_hub.scan_cache_dir`` raises ``ImportError``
    when called, the helper falls back to a manual mtime-based scan.

    The fallback must still delete oldest-first and preserve the newest.
    """
    from voice_typer.server import asr_utils

    cache_dir = tmp_path / "hub"
    cache_dir.mkdir()
    _make_fake_repo(cache_dir, "oldest", 4 * 1024 * 1024, mtime_offset_s=300)
    _make_fake_repo(cache_dir, "middle", 2 * 1024 * 1024, mtime_offset_s=200)
    _make_fake_repo(cache_dir, "newest", 1 * 1024 * 1024, mtime_offset_s=100)

    # Replace scan_cache_dir with a callable that raises ImportError
    # when invoked. The helper's ``except ImportError`` clause catches
    # this and runs the fallback path.
    import huggingface_hub

    def _boom(*_args, **_kwargs):
        raise ImportError("simulated: scan_cache_dir unavailable")

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", _boom)

    pruned = asr_utils.prune_model_cache(cache_dir, max_bytes=4 * 1024 * 1024)

    assert pruned == 1, f"fallback should prune 1 repo, got {pruned}"
    assert not (cache_dir / "models--Org--oldest").exists()
    assert (cache_dir / "models--Org--middle").exists()
    assert (cache_dir / "models--Org--newest").exists()


def test_prune_fallback_when_huggingface_hub_not_importable(tmp_path, monkeypatch):
    """When ``huggingface_hub`` cannot be imported at all, the
    ``from huggingface_hub import scan_cache_dir`` line raises
    ``ImportError`` and the fallback path runs."""
    import sys

    from voice_typer.server import asr_utils

    cache_dir = tmp_path / "hub"
    cache_dir.mkdir()
    _make_fake_repo(cache_dir, "oldest", 4 * 1024 * 1024, mtime_offset_s=300)
    _make_fake_repo(cache_dir, "newest", 1 * 1024 * 1024, mtime_offset_s=100)

    # Hide huggingface_hub from sys.modules and block re-import via a
    # meta-path finder that raises ImportError for that module name.
    real_hf = sys.modules.pop("huggingface_hub", None)

    import importlib.abc

    class _BlockHF(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "huggingface_hub":
                raise ImportError("simulated: huggingface_hub not installed")
            return None

    blocker = _BlockHF()
    monkeypatch.syspath_prepend("")  # no-op; just to touch monkeypatch
    sys.meta_path.insert(0, blocker)
    try:
        pruned = asr_utils.prune_model_cache(cache_dir, max_bytes=2 * 1024 * 1024)
    finally:
        if blocker in sys.meta_path:
            sys.meta_path.remove(blocker)
        if real_hf is not None:
            sys.modules["huggingface_hub"] = real_hf

    assert pruned == 1, f"fallback should prune 1 repo, got {pruned}"
    assert not (cache_dir / "models--Org--oldest").exists()
    assert (cache_dir / "models--Org--newest").exists()


# ──────────────────────────────────────────────────────────────────
# Edge case: cache_dir doesn't exist
# ──────────────────────────────────────────────────────────────────


def test_prune_returns_zero_when_cache_dir_missing(tmp_path):
    """If ``cache_dir`` doesn't exist, the helper returns 0 (no error).

    Both the ``scan_cache_dir`` path and the fallback path handle this
    gracefully — ``scan_cache_dir`` returns an empty scan, and the
    fallback checks ``cache_dir.exists()`` before iterating.
    """
    from voice_typer.server.asr_utils import prune_model_cache

    missing = tmp_path / "does_not_exist"
    assert not missing.exists()
    assert prune_model_cache(missing, max_bytes=1024) == 0


def test_prune_returns_zero_for_empty_cache_dir(tmp_path):
    """An existing but empty ``cache_dir`` prunes nothing."""
    from voice_typer.server.asr_utils import prune_model_cache

    cache_dir = tmp_path / "hub"
    cache_dir.mkdir()
    assert prune_model_cache(cache_dir, max_bytes=1024) == 0


# ──────────────────────────────────────────────────────────────────
# Default cap constant
# ──────────────────────────────────────────────────────────────────


def test_default_max_bytes_is_8gb():
    """The default ``max_bytes`` (when caller omits the arg) is 8 GB,
    matching the ``_MAX_MODEL_CACHE_GB`` constant."""
    import inspect

    from voice_typer.server import asr_utils

    assert asr_utils._MAX_MODEL_CACHE_GB == 8
    sig = inspect.signature(asr_utils.prune_model_cache)
    default = sig.parameters["max_bytes"].default
    assert default == 8 * 1024**3, f"default max_bytes should be 8 GiB ({8 * 1024**3}), got {default}"
