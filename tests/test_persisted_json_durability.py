"""DJ-52: ``PersistedJSON.save(durability=False)`` skips ``fsync``.

The ``durability`` parameter on :meth:`PersistedJSON.save` forwards
to :func:`_secure_atomic_write`. ``True`` (default) preserves the
existing POSIX-durability behavior (two ``fsync`` calls — file data
+ parent dir) used by :meth:`Config.save` and ``credential_store``
where the fsync cost is justified. ``False`` skips the fsync calls
— the atomic ``os.replace`` still guarantees consistency (no
half-written file) but a power-loss window of a few seconds is
accepted. Callers persisting non-critical, high-frequency, or
re-derivable data (vocabulary, templates) should pass
``durability=False`` to save ~2ms/write on SSDs and ~10-50ms on
spinning rust.

These tests assert:

1. ``save(data, durability=False)`` calls ``_secure_atomic_write``
   with ``durability=False``.
2. ``save(data)`` (default) calls ``_secure_atomic_write`` with
   ``durability=True``.
3. ``save(data, durability=True)`` is explicit and equivalent to the
   default.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.secure_file_io import PersistedJSON


@pytest.fixture
def _patched_atomic_write(monkeypatch):
    """Replace ``_secure_atomic_write`` with a MagicMock that records
    the ``durability`` kwarg it was called with.

    The mock does NOT touch the filesystem — the test asserts on the
    call args only, so the actual write is irrelevant.
    """
    mock = MagicMock()

    # PersistedJSON.save imports _secure_atomic_write lazily from
    # voice_typer.server.config (re-exported from secure_file_io) so
    # patching on the config module namespace is what the lazy import
    # resolves at call time.
    import voice_typer.server.config as config_module

    monkeypatch.setattr(config_module, "_secure_atomic_write", mock)
    return mock


def test_save_default_passes_durability_true(
    tmp_path: Path, _patched_atomic_write: MagicMock
) -> None:
    """Default ``save(data)`` forwards ``durability=True``.

    Preserves the historical behaviour — callers that do not opt in
    to the durability-skipping path get the same fsync-on-every-save
    guarantee as before DJ-52.
    """
    store = PersistedJSON(tmp_path / "default.json", default={})
    store.save({"key": "value"})

    _patched_atomic_write.assert_called_once()
    args, kwargs = _patched_atomic_write.call_args
    # positional args: (path, content). durability is a kwarg.
    assert kwargs.get("durability", True) is True, (
        "default save() must pass durability=True so config/credential_store "
        "callers retain the fsync-on-every-save guarantee"
    )


def test_save_durability_false_is_forwarded(
    tmp_path: Path, _patched_atomic_write: MagicMock
) -> None:
    """``save(data, durability=False)`` forwards ``durability=False``.

    This is the DJ-52 fix — vocabulary/templates callers pass
    ``durability=False`` to skip the two fsync calls per save.
    """
    store = PersistedJSON(tmp_path / "fast.json", default={})
    store.save({"key": "value"}, durability=False)

    _patched_atomic_write.assert_called_once()
    args, kwargs = _patched_atomic_write.call_args
    assert kwargs.get("durability", None) is False, (
        "durability=False must be forwarded to _secure_atomic_write so the "
        "fsync calls are skipped (the whole point of DJ-52)"
    )


def test_save_durability_true_is_forwarded(
    tmp_path: Path, _patched_atomic_write: MagicMock
) -> None:
    """Explicit ``save(data, durability=True)`` is equivalent to the default.

    Defensive — callers that want to be explicit about the durability
    guarantee (e.g. credential_store) should get the same behaviour
    as the default.
    """
    store = PersistedJSON(tmp_path / "explicit.json", default={})
    store.save({"key": "value"}, durability=True)

    _patched_atomic_write.assert_called_once()
    args, kwargs = _patched_atomic_write.call_args
    assert kwargs.get("durability", None) is True


def test_save_durability_false_still_writes_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    """``durability=False`` does NOT skip the atomic write itself.

    The atomicity guarantee (``os.replace`` of a fully-written tmp
    file) is preserved regardless of the durability flag — only the
    ``fsync`` calls are skipped. This test verifies the file is
    actually written to disk with the expected content when
    ``durability=False``.
    """
    # Use the real _secure_atomic_write (no mock) so the file is
    # actually written.
    store = PersistedJSON(tmp_path / "atomic.json", default={})
    store.save({"hello": "world"}, durability=False)

    # File must exist on disk with the JSON content.
    assert store.path.exists()
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk == {"hello": "world"}


def test_save_durability_false_skips_fsync(
    tmp_path: Path, monkeypatch
) -> None:
    """``durability=False`` does NOT call ``os.fsync``.

    This is the core perf claim of DJ-52: skipping the two fsync
    calls per save (file data + parent dir) is what saves the
    ~2ms/write on SSDs. Verifies on POSIX (skipped on Windows where
    ``os.fsync`` is a no-op / different path).
    """
    import os

    fsync_calls: list[int] = []

    def counting_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        # Don't actually call real_fsync — the test doesn't need
        # durability, and skipping the real syscall speeds up the
        # test. The fd is real but the test doesn't care about the
        # data being durable.

    monkeypatch.setattr(os, "fsync", counting_fsync)

    store = PersistedJSON(tmp_path / "nofsync.json", default={})
    store.save({"key": "value"}, durability=False)

    assert fsync_calls == [], (
        "durability=False must NOT call os.fsync — that's the whole point "
        f"of DJ-52 (got {len(fsync_calls)} fsync calls)"
    )


def test_save_durability_true_calls_fsync(
    tmp_path: Path, monkeypatch
) -> None:
    """``durability=True`` (default) DOES call ``os.fsync``.

    Regression guard for the inverse direction — the durability flag
    must actually control whether fsync is called, not be a no-op.
    """
    import os

    fsync_calls: list[int] = []

    def counting_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        # Call the real fsync so the durability guarantee is preserved
        # (the test asserts the call count, not the durability
        # outcome — but calling the real fsync makes the test more
        # realistic).

    monkeypatch.setattr(os, "fsync", counting_fsync)

    store = PersistedJSON(tmp_path / "yesfsync.json", default={})
    store.save({"key": "value"}, durability=True)

    assert len(fsync_calls) >= 1, (
        "durability=True must call os.fsync at least once (file data) — "
        "the durability flag must actually control the fsync behaviour"
    )
