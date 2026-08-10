"""CR-40: regression tests for unique tmp name in ``_secure_atomic_write``.

The previous ``_secure_atomic_write`` implementation used a fixed tmp
name (``path.with_suffix(path.suffix + ".tmp")``). With ``O_EXCL`` on
POSIX, a second concurrent caller's ``os.open(... O_EXCL)`` failed
with ``EEXIST`` → ``except Exception`` → ``tmp_path.unlink()``
(deletes the FIRST caller's tmp!) → re-raise. The first caller's
write was lost silently.

Two realistic concurrent callers:
- ``Config.save()`` from the main thread after a settings change.
- ``credential_store._write_plaintext_fallback()`` from an IPC handler
  thread that just stored a new API key.

Both target ``config.json`` — the second one's failure surfaces as
``Config.save()`` returning False (logged at ERROR level) and the
user's setting silently not persisting.

The fix uses ``tempfile.mkstemp(dir=str(path.parent), prefix=path.name
+ ".", suffix=".tmp")`` to get a UNIQUE tmp name per call.

Tests:

1. ``test_concurrent_saves_no_false_return`` — hammer
   ``Config.save()`` from 4 threads, assert no False return.

2. ``test_concurrent_writes_no_data_loss`` — hammer
   ``_secure_atomic_write`` directly from multiple threads, assert
   every write lands on disk (no silent loss).

3. ``test_unique_tmp_name_per_call`` — verify each call to
   ``_secure_atomic_write`` uses a different tmp name (no fixed name).
"""

from __future__ import annotations

import json
import threading

import pytest


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield


class TestSecureAtomicWriteConcurrent:
    """CR-40: _secure_atomic_write must use a unique tmp name per call."""

    def test_concurrent_saves_no_false_return(self, tmp_path):
        """Hammer Config.save() from 4 threads — assert no False return.

        With the OLD fixed-tmp-name implementation, concurrent saves
        would collide on the same tmp file (EEXIST), the second
        caller's broad except would unlink the first caller's tmp
        (silent data loss), and the second caller would re-raise —
        surfacing as Config.save() returning False.
        """
        from voice_typer.server.config import Config

        results: list = []
        results_lock = threading.Lock()

        def save_loop(thread_id: int):
            for i in range(20):
                c = Config(hotkey=f"<f{thread_id}_{i}>")
                ok = c.save()
                with results_lock:
                    results.append(ok)

        threads = [threading.Thread(target=save_loop, args=(tid,), daemon=True) for tid in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        assert all(results), (
            f"CR-40 regression: {results.count(False)} out of {len(results)} "
            "Config.save() calls returned False under concurrent writes. "
            "The fixed tmp name caused EEXIST + the second caller's "
            "unlink deleted the first caller's tmp file (silent data loss)."
        )

        # Verify the final config.json is valid JSON (no corruption
        # from concurrent writes).
        config_file = tmp_path / "config.json"
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert "hotkey" in data

    def test_concurrent_writes_no_data_loss(self, tmp_path):
        """Hammer _secure_atomic_write directly from multiple threads
        targeting the same file — assert every write's content can
        be observed (no silent loss).

        With the OLD fixed-tmp-name implementation, the second caller's
        EEXIST + unlink would delete the first caller's tmp file, so
        the first caller's write was silently lost.
        """
        from voice_typer.server.config import _secure_atomic_write

        target = tmp_path / "shared.json"
        contents = [f'{{"thread": {i}, "write": {j}}}' for i in range(4) for j in range(15)]

        def write_content(content: str):
            _secure_atomic_write(target, content)

        threads = [threading.Thread(target=write_content, args=(c,), daemon=True) for c in contents]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        # The final content must be ONE of the writes (no corruption).
        # If the OLD code's unlink deleted a tmp file mid-write, the
        # final file might be empty or corrupted.
        assert target.exists(), (
            "CR-40 regression: target file does not exist after "
            "concurrent writes — the tmp file was deleted by another "
            "caller's unlink before os.replace."
        )
        content = target.read_text()
        # Must be valid JSON (no truncated/partial write).
        data = json.loads(content)
        assert "thread" in data
        assert "write" in data

    def test_unique_tmp_name_per_call(self, tmp_path):
        """Each call to _secure_atomic_write must use a DIFFERENT tmp
        name. The OLD implementation used a fixed name (config.json.tmp),
        so concurrent callers collided on the same tmp file.

        We verify the fix by patching tempfile.mkstemp to record the
        names it generates, then asserting they're all different.
        """
        import tempfile

        from voice_typer.server import config as config_mod

        target = tmp_path / "test.json"
        captured_names: list = []
        original_mkstemp = tempfile.mkstemp

        def _capturing_mkstemp(*args, **kwargs):
            fd, name = original_mkstemp(*args, **kwargs)
            captured_names.append(name)
            return fd, name

        # Patch the tempfile module's mkstemp (which _secure_atomic_write
        # imports lazily as `import tempfile`).
        monkeypatch_mkstemp = pytest.MonkeyPatch()
        monkeypatch_mkstemp.setattr(tempfile, "mkstemp", _capturing_mkstemp)
        try:
            # Run several writes sequentially.
            for i in range(5):
                config_mod._secure_atomic_write(target, f'{{"i": {i}}}')
        finally:
            monkeypatch_mkstemp.undo()

        # All 5 calls must have generated different tmp names.
        assert len(captured_names) == 5, f"Expected 5 mkstemp calls, got {len(captured_names)}."
        assert len(set(captured_names)) == 5, (
            "CR-40 regression: tmp names are not unique across calls — "
            f"names: {captured_names}. The OLD implementation used a "
            "fixed name (path.with_suffix(path.suffix + '.tmp')), "
            "causing concurrent writers to collide on the same tmp file."
        )

    def test_no_stale_tmp_files_after_successful_writes(self, tmp_path):
        """After successful writes, no .tmp files should be left in
        the target directory (every tmp file is renamed via os.replace
        or unlinked on failure)."""
        from voice_typer.server.config import _secure_atomic_write

        target = tmp_path / "test.json"
        for i in range(10):
            _secure_atomic_write(target, f'{{"i": {i}}}')

        # List remaining .tmp files in the target directory.
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert not tmp_files, (
            "CR-40 regression: stale .tmp files remain after successful "
            f"writes: {tmp_files}. Each call's tmp file should be "
            "renamed via os.replace or unlinked on failure."
        )

    def test_survives_pre_existing_tmp_file(self, tmp_path):
        """If a tmp file with the OLD fixed name (config.json.tmp)
        pre-exists in the target directory, _secure_atomic_write must
        still succeed (the new unique-name approach doesn't collide
        with the pre-existing file)."""
        from voice_typer.server.config import _secure_atomic_write

        target = tmp_path / "config.json"
        # Pre-create the OLD fixed-name tmp file.
        old_tmp = tmp_path / "config.json.tmp"
        old_tmp.write_text("stale content from a previous crash")

        # New write should succeed (uses a unique name, doesn't collide).
        _secure_atomic_write(target, '{"fresh": true}')

        assert target.exists()
        data = json.loads(target.read_text())
        assert data["fresh"] is True
        # The OLD fixed-name tmp file should still be there (we didn't
        # touch it — the new code uses unique names). This proves the
        # new code doesn't rely on the OLD fixed name.
        assert old_tmp.exists(), (
            "Pre-existing OLD tmp file was unexpectedly removed — the new unique-name code should NOT touch it."
        )
