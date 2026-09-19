"""DJ-52: ``PersistedJSON.save(durability=False)`` skips ``fsync``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.secure_file_io import PersistedJSON


@pytest.fixture
def _patched_atomic_write(monkeypatch):
    """Replace ``_secure_atomic_write`` with a MagicMock that records"""
    mock = MagicMock()

    import voice_typer.server.config as config_module

    monkeypatch.setattr(config_module, "_secure_atomic_write", mock)
    return mock


def test_save_default_passes_durability_true(tmp_path: Path, _patched_atomic_write: MagicMock) -> None:
    """Default ``save(data)`` forwards ``durability=True``."""
    store = PersistedJSON(tmp_path / "default.json", default={})
    store.save({"key": "value"})

    _patched_atomic_write.assert_called_once()
    args, kwargs = _patched_atomic_write.call_args
    assert kwargs.get("durability", True) is True, (
        "default save() must pass durability=True so config/credential_store "
        "callers retain the fsync-on-every-save guarantee"
    )


def test_save_durability_false_is_forwarded(tmp_path: Path, _patched_atomic_write: MagicMock) -> None:
    """``save(data, durability=False)`` forwards ``durability=False``."""
    store = PersistedJSON(tmp_path / "fast.json", default={})
    store.save({"key": "value"}, durability=False)

    _patched_atomic_write.assert_called_once()
    args, kwargs = _patched_atomic_write.call_args
    assert kwargs.get("durability", None) is False, (
        "durability=False must be forwarded to _secure_atomic_write so the "
        "fsync calls are skipped (the whole point of DJ-52)"
    )


def test_save_durability_true_is_forwarded(tmp_path: Path, _patched_atomic_write: MagicMock) -> None:
    """Explicit ``save(data, durability=True)`` is equivalent to the default."""
    store = PersistedJSON(tmp_path / "explicit.json", default={})
    store.save({"key": "value"}, durability=True)

    _patched_atomic_write.assert_called_once()
    args, kwargs = _patched_atomic_write.call_args
    assert kwargs.get("durability", None) is True


def test_save_durability_false_still_writes_atomically(tmp_path: Path, monkeypatch) -> None:
    """``durability=False`` does NOT skip the atomic write itself."""
    # Use the real _secure_atomic_write (no mock) so the file is
    store = PersistedJSON(tmp_path / "atomic.json", default={})
    store.save({"hello": "world"}, durability=False)

    # File must exist on disk with the JSON content.
    assert store.path.exists()
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk == {"hello": "world"}


def test_save_durability_false_skips_fsync(tmp_path: Path, monkeypatch) -> None:
    """``durability=False`` does NOT call ``os.fsync``."""
    import os

    fsync_calls: list[int] = []

    def counting_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        # Don't actually call real_fsync, the test doesn't need

    monkeypatch.setattr(os, "fsync", counting_fsync)

    store = PersistedJSON(tmp_path / "nofsync.json", default={})
    store.save({"key": "value"}, durability=False)

    assert fsync_calls == [], (
        f"durability=False must NOT call os.fsync, that's the whole point of DJ-52 (got {len(fsync_calls)} fsync calls)"
    )


def test_save_durability_true_calls_fsync(tmp_path: Path, monkeypatch) -> None:
    """``durability=True`` (default) DOES call ``os.fsync``."""
    import os

    fsync_calls: list[int] = []

    def counting_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        # Call the real fsync so the durability guarantee is preserved

    monkeypatch.setattr(os, "fsync", counting_fsync)

    store = PersistedJSON(tmp_path / "yesfsync.json", default={})
    store.save({"key": "value"}, durability=True)

    assert len(fsync_calls) >= 1, (
        "durability=True must call os.fsync at least once (file data), "
        "the durability flag must actually control the fsync behaviour"
    )
