"""DJ-53: ``PersistedJSON.save`` no longer re-reads the file on every save.

The previous implementation called ``self._path.read_bytes()`` on
every save to decide whether the single-slot ``.bak`` needed
refreshing. For a 50 KB vocabulary file at 10 saves/min during a
rapid-editing session, that's 1.5 MB/s of disk reads just for the
diff check — pure waste.

The DJ-53 fix caches the last-written (or last-loaded) bytes on
the instance (``self._last_written_bytes``). The cache is populated
lazily on the first save (if the file exists) and on every
successful load. Subsequent saves compare against the cached bytes
instead of re-reading the file. The ``.bak`` is then written only
when content actually changes.

These tests assert:

1. After a successful save, the next save with identical content
   does NOT re-read the file (cache hit on
   ``self._last_written_bytes``).
2. After a successful load, the cache is populated so the first
   save does NOT re-read the file.
3. After a save with DIFFERENT content, the ``.bak`` IS written
   (with the previous on-disk bytes), and the cache is updated to
   the new content.
4. The cache is invalidated on a failed load (corrupt file) so the
   next save re-reads (or treats as a fresh file).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from voice_typer.server.secure_file_io import PersistedJSON


def test_second_save_with_identical_content_does_not_re_read_file(
    tmp_path: Path,
) -> None:
    """Second save with identical content uses the cache, not disk.

    The first save populates the cache (either by reading the file
    if it exists, or by treating it as a fresh write). The second
    save with the SAME content finds ``existing_bytes ==
    content_bytes`` in the cache and skips both the ``.bak`` write
    AND the file read.
    """
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
        "Second save with identical content must NOT re-read the file — "
        f"first save reads={reads_after_first}, second save reads="
        f"{reads_after_second}. The DJ-53 cache should have prevented the "
        "second read."
    )


def test_load_populates_cache_so_first_save_does_not_re_read(
    tmp_path: Path,
) -> None:
    """``load()`` populates the cache from the on-disk bytes.

    After a successful load, the next save should use the cached
    bytes (not re-read the file) for the ``.bak`` diff check.
    """
    path = tmp_path / "loaded.json"
    # Pre-write a file so load() has something to read.
    path.write_text(json.dumps({"initial": True}, indent=2), encoding="utf-8")

    store = PersistedJSON(path, default={})
    loaded = store.load()
    assert loaded == {"initial": True}

    # Now save with DIFFERENT content. The .bak should be written
    # using the CACHED bytes (from load), not by re-reading the file.
    real_read_bytes = Path.read_bytes
    read_calls: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        read_calls.append(self)
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        store.save({"updated": True})

    path_reads = [p for p in read_calls if p == store.path]
    assert path_reads == [], (
        "First save after load() must NOT re-read the file — the load() "
        "call should have populated the cache. Got reads: "
        f"{path_reads}"
    )

    # The .bak should contain the PREVIOUS on-disk content (from the
    # cache), not the new content.
    bak_path = path.with_name(path.name + ".bak")
    assert bak_path.exists()
    bak_content = bak_path.read_text(encoding="utf-8")
    assert json.loads(bak_content) == {"initial": True}, (
        "The .bak should contain the previous on-disk content (from the cache populated by load()), not the new content"
    )


def test_save_with_different_content_updates_cache_and_writes_bak(
    tmp_path: Path,
) -> None:
    """After a save with different content, the cache is updated.

    The next save with the NEW content (identical to the just-saved
    content) should be a cache hit — no read, no .bak write.
    """
    store = PersistedJSON(tmp_path / "update.json", default={})

    # First save: writes content A.
    store.save({"version": "A"})

    # Second save: writes content B (different from A). Should
    # trigger a .bak write containing content A.
    store.save({"version": "B"})
    bak_path = store.path.with_name(store.path.name + ".bak")
    assert bak_path.exists()
    bak_content = json.loads(bak_path.read_text(encoding="utf-8"))
    assert bak_content == {"version": "A"}

    # Third save: writes content B again (identical to just-saved).
    # Should be a cache hit — no read, no .bak write. The .bak should
    # still contain content A (NOT overwritten because content is
    # unchanged).
    real_read_bytes = Path.read_bytes
    read_calls: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        read_calls.append(self)
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        store.save({"version": "B"})

    path_reads = [p for p in read_calls if p == store.path]
    assert path_reads == [], (
        "Third save (identical to second) must NOT re-read the file — "
        "the cache should have been updated by the second save"
    )

    # .bak should still contain content A (not overwritten because
    # content B == content B).
    bak_content_after = json.loads(bak_path.read_text(encoding="utf-8"))
    assert bak_content_after == {"version": "A"}


def test_cache_invalidated_on_failed_load(tmp_path: Path) -> None:
    """A failed load (corrupt file) invalidates the cache.

    The next save should treat the file as fresh (re-read or
    no-prior-file), not use stale cached bytes.
    """
    path = tmp_path / "corrupt.json"
    # Write corrupt JSON.
    path.write_text("{not valid json", encoding="utf-8")

    store = PersistedJSON(path, default={"fallback": True})
    loaded = store.load()

    # The load failed (corrupt JSON) — the default is returned.
    assert loaded == {"fallback": True}
    # The cache was invalidated.
    assert store._last_written_bytes is None, (
        "Cache should be None after a failed load (corrupt file was quarantined — cache would be stale)"
    )


def test_first_save_with_existing_file_reads_once_to_populate_cache(
    tmp_path: Path,
) -> None:
    """First save (cache cold) reads the file ONCE to populate the cache.

    On the very first save after instantiation (no prior load), if
    the file exists on disk, the cache must be populated by reading
    the file once. Subsequent saves use the cache.

    XE-8-A (Option b): patches ``_secure_read_text`` (the actual read
    helper used by ``PersistedJSON.save``) instead of ``Path.read_bytes``
    (which ``_secure_read_text`` does NOT call — it uses ``os.open`` +
    ``os.fdopen``). The previous version patched ``Path.read_bytes`` and
    was vacuously failing because the patch was never invoked.
    """
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
        # First save: cache is cold, file exists → read once (via the
        # .bak-diff path, which reads the existing file to decide whether
        # a .bak write is needed).
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
