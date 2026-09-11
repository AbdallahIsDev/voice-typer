"""BP-158: single authoritative model-availability verdicts.

All consumers (service status poll, tray submenu, tooltip probe,
model-manager precheck) consult ``model_availability.is_available``.
Freshness comes from directory mtime + explicit invalidation, not
TTL expiry. Hermetic: fake repo dirs, controlled mtimes, an injected
call-counting probe stub (the real snapshot probe never runs here).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from voice_typer.server import model_availability as ma


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch: pytest.MonkeyPatch):
    """Isolate the module-global store + download-flight hook per test."""
    ma.invalidate()
    monkeypatch.setattr(ma, "_download_active_impl", lambda: False)
    yield
    ma.invalidate()
    monkeypatch.setattr(ma, "_download_active_impl", lambda: False)


def _make_repo(config_dir: Path, repo_id: str) -> Path:
    snap = config_dir / "huggingface" / "hub" / f"models--{repo_id.replace('/', '--')}"
    snap.mkdir(parents=True)
    return snap


def _set_mtime(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


class TestMtimeFreshness:
    """Unchanged layout ⇒ probe runs once no matter how many checks."""

    def test_100_checks_one_probe(self, tmp_path: Path):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        repo_dir = _make_repo(tmp_path, "org/repo")
        _set_mtime(repo_dir, 1_700_000_000_000_000_000)
        for _ in range(100):
            assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert calls == ["org/repo"], f"expected exactly 1 probe, got {len(calls)}"

    def test_mtime_bump_forces_reprobe(self, tmp_path: Path):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return len(calls) > 1  # False, then True (download landed)

        repo_dir = _make_repo(tmp_path, "org/repo")
        _set_mtime(repo_dir, 1_700_000_000_000_000_000)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is False
        _set_mtime(repo_dir, 1_700_000_100_000_000_000)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert len(calls) == 2


class TestSharedStore:
    """Service-style and tray-style calls consult ONE store."""

    def test_service_and_tray_share_one_probe_call(self, tmp_path: Path):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        repo_dir = _make_repo(tmp_path, "org/repo")
        _set_mtime(repo_dir, 1_700_000_000_000_000_000)
        # service poll shape…
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        # …tray submenu shape (same repo, same dir) hits the store.
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert calls == ["org/repo"]

    def test_invalidate_forces_reprobe(self, tmp_path: Path):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        repo_dir = _make_repo(tmp_path, "org/repo")
        _set_mtime(repo_dir, 1_700_000_000_000_000_000)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        ma.invalidate("org/repo", tmp_path)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert len(calls) == 2

    def test_invalidate_all_clears_everything(self, tmp_path: Path):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        for repo in ("org/a", "org/b"):
            _set_mtime(_make_repo(tmp_path, repo), 1_700_000_000_000_000_000)
            assert ma.is_available(repo, tmp_path, probe=probe) is True
        assert len(ma._store_snapshot_for_test()) == 2
        ma.invalidate()
        assert ma._store_snapshot_for_test() == {}


class TestInProgressOverride:
    """While a download is in flight, freshness is bypassed (re-probe)."""

    def test_active_download_skips_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        repo_dir = _make_repo(tmp_path, "org/repo")
        _set_mtime(repo_dir, 1_700_000_000_000_000_000)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert len(calls) == 1
        monkeypatch.setattr(ma, "_download_active_impl", lambda: True)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert len(calls) == 3, "in-flight download must bypass the freshness shortcut"


class TestAdversarialMutation:
    """A mutation that preserves mtime is invisible until invalidated —
    then the store re-probes and corrects. This pins the documented
    contract: mtime is a fast path, invalidation is the authority."""

    def test_pinned_mtime_serves_stale_then_corrects_on_invalidate(self, tmp_path: Path):
        verdicts = [True]

        def probe(repo_id: str) -> bool:
            return verdicts[-1]

        repo_dir = _make_repo(tmp_path, "org/repo")
        pinned = 1_700_000_000_000_000_000
        _set_mtime(repo_dir, pinned)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        # Mutate behind the cache's back (files removed) but pin the
        # mtime so the fingerprint still matches.
        for child in repo_dir.iterdir():
            if child.is_file():
                child.unlink()
        _set_mtime(repo_dir, pinned)
        verdicts.append(False)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        ma.invalidate("org/repo", tmp_path)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is False


class TestExtraWatchDirs:
    """Configured local paths (qwen/parakeet) join the fingerprint."""

    def test_configured_path_change_busts_cache(self, tmp_path: Path):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        _set_mtime(_make_repo(tmp_path, "org/repo"), 1_700_000_000_000_000_000)
        local = tmp_path / "custom-model"
        local.mkdir()
        _set_mtime(local, 1_700_000_000_000_000_000)
        assert ma.is_available("org/repo", tmp_path, extra_watch_dirs=[local], probe=probe) is True
        assert len(calls) == 1
        (local / "weights.bin").write_bytes(b"x")
        _set_mtime(local, 1_700_000_100_000_000_000)
        assert ma.is_available("org/repo", tmp_path, extra_watch_dirs=[local], probe=probe) is True
        assert len(calls) == 2


class TestSnapshotDir:
    """The canonical HF layout recipe is centralized."""

    def test_snapshot_dir_layout(self, tmp_path: Path):
        assert ma.snapshot_dir(tmp_path, "org/repo") == (tmp_path / "huggingface" / "hub" / "models--org--repo")


class TestTooltipCheapness:
    """The 1 s tooltip tick's probe cost: 10 checks, 1 probe.

    (The tick still recomputes the tooltip string every second, the
    mm:ss clock must tick, but the filesystem cost is a single stat
    per check once warm. Publish dedup already suppresses identical
    tooltips.)
    """

    def test_ten_checks_one_probe(self, tmp_path: Path):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        repo_dir = _make_repo(tmp_path, "org/repo")
        _set_mtime(repo_dir, 1_700_000_000_000_000_000)
        for _ in range(10):
            assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert len(calls) == 1


class TestMonotonicHygiene:
    """Entries much older than the freshness window re-probe even when
    the mtime matches (bounds trust in a stale filesystem view)."""

    def test_ancient_entry_reprobes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        calls = []

        def probe(repo_id: str) -> bool:
            calls.append(repo_id)
            return True

        repo_dir = _make_repo(tmp_path, "org/repo")
        _set_mtime(repo_dir, 1_700_000_000_000_000_000)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert len(calls) == 1
        real_monotonic = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + 10_000.0)
        assert ma.is_available("org/repo", tmp_path, probe=probe) is True
        assert len(calls) == 2
