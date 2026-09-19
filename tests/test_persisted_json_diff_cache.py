"""DJ-53: ``PersistedJSON.save`` no longer re-reads the file on every save."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from voice_typer.server.secure_file_io import PersistedJSON


def test_second_save_with_identical_content_does_not_re_read_file(
    tmp_path: Path,
) -> None:
    """Second save with identical content uses the cache, not disk."""
    store = PersistedJSON(tmp_path / "cache.json", default={})

    # Patch read_bytes to count how many times the file is read.
    real_read_bytes = Path.read_bytes
    read_calls: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        read_calls.append(self)
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        # First save: file does not exist yet, so no read.
        store.save({"key": "value"})
        reads_after_first = [p for p in read_calls if p == store.path]

        # Second save with identical content: cache hit, no read.
        store.save({"key": "value"})
        reads_after_second = [p for p in read_calls if p == store.path]

    assert reads_after_first == reads_after_second, (
        "Second save with identical content must NOT re-read the file, "
        f"first save reads={reads_after_first}, second save reads="
        f"{reads_after_second}. The DJ-53 cache should have prevented the "
        "second read."
    )


def test_load_populates_cache_so_first_save_does_not_re_read(
    tmp_path: Path,
) -> None:
    """``load()`` populates the cache from the on-disk bytes."""
    path = tmp_path / "loaded.json"
    # Pre-write a file so load() has something to read.
    path.write_text(json.dumps({"initial": True}, indent=2), encoding="utf-8")

    store = PersistedJSON(path, default={})
    loaded = store.load()
    assert loaded == {"initial": True}

    # Now save with DIFFERENT content. The .bak should be written
    real_read_bytes = Path.read_bytes
    read_calls: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        read_calls.append(self)
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        store.save({"updated": True})

    path_reads = [p for p in read_calls if p == store.path]
    assert path_reads == [], (
        "First save after load() must NOT re-read the file, the load() "
        "call should have populated the cache. Got reads: "
        f"{path_reads}"
    )

    bak_path = path.with_name(path.name + ".bak")
    assert bak_path.exists()
    bak_content = bak_path.read_text(encoding="utf-8")
    assert json.loads(bak_content) == {"initial": True}, (
        "The .bak should contain the previous on-disk content (from the cache populated by load()), not the new content"
    )


def test_save_with_different_content_updates_cache_and_writes_bak(
    tmp_path: Path,
) -> None:
    """After a save with different content, the cache is updated."""
    store = PersistedJSON(tmp_path / "update.json", default={})

    # First save: writes content A.
    store.save({"version": "A"})

    # Second save: writes content B (different from A). Should
    store.save({"version": "B"})
    bak_path = store.path.with_name(store.path.name + ".bak")
    assert bak_path.exists()
    bak_content = json.loads(bak_path.read_text(encoding="utf-8"))
    assert bak_content == {"version": "A"}

    # Third save: writes content B again (identical to just-saved).
    real_read_bytes = Path.read_bytes
    read_calls: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        read_calls.append(self)
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        store.save({"version": "B"})

    path_reads = [p for p in read_calls if p == store.path]
    assert path_reads == [], (
        "Third save (identical to second) must NOT re-read the file, "
        "the cache should have been updated by the second save"
    )

    # .bak should still contain content A (not overwritten because
    bak_content_after = json.loads(bak_path.read_text(encoding="utf-8"))
    assert bak_content_after == {"version": "A"}


def test_cache_invalidated_on_failed_load(tmp_path: Path) -> None:
    """A failed load (corrupt file) invalidates the cache."""
    path = tmp_path / "corrupt.json"
    # Write corrupt JSON.
    path.write_text("{not valid json", encoding="utf-8")

    store = PersistedJSON(path, default={"fallback": True})
    loaded = store.load()

    # The load failed (corrupt JSON), the default is returned.
    assert loaded == {"fallback": True}
    # The cache was invalidated.
    assert store._last_written_bytes is None, (
        "Cache should be None after a failed load (corrupt file was quarantined, cache would be stale)"
    )


def test_first_save_with_existing_file_reads_once_to_populate_cache(
    tmp_path: Path,
) -> None:
    """First save (cache cold) reads the file ONCE to populate the cache."""
    from voice_typer.server import secure_file_io

    path = tmp_path / "existing.json"
    # Pre-write so the file exists.
    path.write_text(json.dumps({"pre": "existing"}, indent=2), encoding="utf-8")

    store = PersistedJSON(path, default={})  # No load() called.

    real_read = secure_file_io._secure_read_text
    read_calls: list[Path] = []

    def counting_read(p, *args, **kwargs):
        read_calls.append(Path(p))
        return real_read(p, *args, **kwargs)

    with patch.object(secure_file_io, "_secure_read_text", counting_read):
        store.save({"pre": "existing"})  # Same content as on-disk.
        reads_after_first = [p for p in read_calls if p == store.path]

        # Second save: cache is warm → no read.
        store.save({"pre": "existing"})
        reads_after_second = [p for p in read_calls if p == store.path]

    assert len(reads_after_first) == 1, (
        "First save (cache cold, file exists) must read the file exactly "
        f"once to populate the cache. Got {len(reads_after_first)} reads."
    )
    assert reads_after_second == reads_after_first, (
        "Second save (cache warm) must NOT re-read the file. Got "
        f"{len(reads_after_second) - len(reads_after_first)} extra reads."
    )
