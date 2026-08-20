"""Focused unit tests for the extracted ``PrivacyMixin`` helpers.

The pre-refactor ``PrivacyMixin.delete_all_personal_data`` /
``PrivacyMixin.export_gdpr_bundle`` were 275-LOC / 189-LOC monoliths.
The refactor extracted the per-step work into 11 private
``@staticmethod`` helpers so the two public methods are now ~20-LOC
orchestrators. The full GDPR pipeline is still covered by the
existing ``tests/test_gdpr_delete.py`` / ``tests/test_gdpr_export.py``
/ ``tests/test_reset_config_to_defaults.py`` suites — these tests run
unchanged against the refactor (regression guard).

This file adds DIRECT unit tests for each helper so a future change
that breaks one helper is surfaced with a focused failure pointing
at the helper (rather than a diffuse failure somewhere in the
275-LOC monolith).  Each test constructs the minimum input the
helper needs (a tmp ``config_dir``, a fake ``hdb`` / ``app``, the
``erased`` / ``failed`` accumulators) and asserts on the helper's
contract.

These tests do NOT depend on a live ``VoiceTyperService`` /
``VoiceTyperApp`` — they call the ``@staticmethod`` helpers directly
via ``PrivacyMixin._gdpr_*``.  This keeps them fast (no service
construction) and isolates the helper-under-test from the rest of
the pipeline.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service.privacy import PrivacyMixin

# ── _gdpr_checkpoint_history_db ────────────────────────────────────────


def test_checkpoint_history_db_none_is_noop() -> None:
    """Passing ``hdb=None`` must be a silent no-op (fresh-install path)."""
    # No exception raised, no methods called.
    PrivacyMixin._gdpr_checkpoint_history_db(None, close=True)
    PrivacyMixin._gdpr_checkpoint_history_db(None, close=False)


def test_checkpoint_history_db_calls_checkpoint_with_truncate_kwarg() -> None:
    """When ``checkpoint`` accepts the ``truncate=`` kwarg, it is used."""
    hdb = MagicMock()
    PrivacyMixin._gdpr_checkpoint_history_db(hdb, close=False)
    hdb.checkpoint.assert_called_once_with(truncate=True)
    hdb.close.assert_not_called()


def test_checkpoint_history_db_close_flag_calls_close() -> None:
    """``close=True`` triggers ``hdb.close()`` (delete path)."""
    hdb = MagicMock()
    PrivacyMixin._gdpr_checkpoint_history_db(hdb, close=True)
    hdb.checkpoint.assert_called_once_with(truncate=True)
    hdb.close.assert_called_once_with()


def test_checkpoint_history_db_falls_back_to_positional_on_typeerror() -> None:
    """Older ``checkpoint`` signatures without ``truncate=`` fall back to positional."""
    hdb = MagicMock()

    def _checkpoint(*args, **kwargs):  # noqa: ANN001
        if kwargs:
            raise TypeError("no kwargs supported on this build")
        # Positional call succeeds.
        return None

    hdb.checkpoint.side_effect = _checkpoint
    PrivacyMixin._gdpr_checkpoint_history_db(hdb, close=False)
    # First call (with kwarg) raises TypeError; second call (positional) succeeds.
    assert hdb.checkpoint.call_count == 2


def test_checkpoint_history_db_swallows_checkpoint_exception() -> None:
    """A raising ``checkpoint`` must NOT propagate (best-effort contract)."""
    hdb = MagicMock()
    hdb.checkpoint.side_effect = RuntimeError("writer thread died")
    # Must not raise.
    PrivacyMixin._gdpr_checkpoint_history_db(hdb, close=False)
    hdb.close.assert_not_called()


def test_checkpoint_history_db_swallows_close_exception() -> None:
    """A raising ``close`` must NOT propagate (best-effort contract)."""
    hdb = MagicMock()
    hdb.close.side_effect = RuntimeError("already closed")
    PrivacyMixin._gdpr_checkpoint_history_db(hdb, close=True)
    hdb.close.assert_called_once_with()


# ── _gdpr_unlink_personal_files ────────────────────────────────────────


def test_unlink_personal_files_removes_existing_files(tmp_path: Path) -> None:
    """Hardcoded personal files are unlinked and recorded in ``erased``."""
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "voice-typer.log").write_text("log line")

    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_unlink_personal_files(tmp_path, erased, failed)

    assert not (tmp_path / "config.json").exists()
    assert not (tmp_path / "voice-typer.log").exists()
    assert str(tmp_path / "config.json") in erased
    assert str(tmp_path / "voice-typer.log") in erased
    assert failed == {}


def test_unlink_personal_files_skips_missing_files(tmp_path: Path) -> None:
    """A fresh-install config dir (no artifacts) is a silent no-op."""
    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_unlink_personal_files(tmp_path, erased, failed)
    assert erased == []
    assert failed == {}


def test_unlink_personal_files_captures_unlink_failure(tmp_path: Path) -> None:
    """A failed unlink surfaces in ``failed`` rather than raising."""
    target = tmp_path / "config.json"
    target.write_text("{}")

    erased: list = []
    failed: dict = {}

    # Force unlink to raise PermissionError.
    original_unlink = Path.unlink

    def _raising_unlink(self, *args, **kwargs):  # noqa: ANN001
        if self == target:
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    Path.unlink = _raising_unlink  # type: ignore[method-assign]
    try:
        PrivacyMixin._gdpr_unlink_personal_files(tmp_path, erased, failed)
    finally:
        Path.unlink = original_unlink  # type: ignore[method-assign]

    assert erased == []
    assert str(target) in failed
    assert "PermissionError" in failed[str(target)]


# ── _gdpr_unlink_personal_globs ────────────────────────────────────────


def test_unlink_personal_globs_removes_matched_files(tmp_path: Path) -> None:
    """Glob-matched files are unlinked and recorded in ``erased``."""
    (tmp_path / "voice-typer.log.1").write_text("rotated 1")
    (tmp_path / "voice-typer.log.2").write_text("rotated 2")
    (tmp_path / "mic-test-20240101.wav").write_bytes(b"RIFF")

    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_unlink_personal_globs(tmp_path, erased, failed)

    assert not (tmp_path / "voice-typer.log.1").exists()
    assert not (tmp_path / "voice-typer.log.2").exists()
    assert not (tmp_path / "mic-test-20240101.wav").exists()
    assert len(erased) == 3
    assert failed == {}


def test_unlink_personal_globs_no_matches_is_noop(tmp_path: Path) -> None:
    """A config dir with no matching globs leaves ``erased`` empty."""
    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_unlink_personal_globs(tmp_path, erased, failed)
    assert erased == []
    assert failed == {}


# ── _gdpr_rmtree_rust_logs ─────────────────────────────────────────────


def test_rmtree_rust_logs_removes_directory(tmp_path: Path) -> None:
    """The ``logs/`` subdir is recursively removed and recorded."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "voice-typer.log").write_text("rust log line")
    (logs_dir / "voice-typer.log.1").write_text("rotated rust log")

    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_rmtree_rust_logs(tmp_path, erased, failed)

    assert not logs_dir.exists()
    assert str(logs_dir) in erased
    assert failed == {}


def test_rmtree_rust_logs_missing_dir_is_noop(tmp_path: Path) -> None:
    """A missing ``logs/`` dir is a silent no-op (fresh install)."""
    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_rmtree_rust_logs(tmp_path, erased, failed)
    assert erased == []
    assert failed == {}


# ── _gdpr_rmtree_db_dir ─────────────────────────────────────────────────


def test_rmtree_db_dir_removes_directory(tmp_path: Path) -> None:
    """The ``db/`` subdir (O2 history DB) is recursively removed."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    (db_dir / "history.db").write_text("history bytes")
    (db_dir / "history.db-wal").write_text("wal")
    (db_dir / "history.db.corrupt-123").write_text("quarantined")

    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_rmtree_db_dir(tmp_path, erased, failed)

    assert not db_dir.exists()
    assert str(db_dir) in erased
    assert failed == {}


def test_rmtree_db_dir_missing_dir_is_noop(tmp_path: Path) -> None:
    """A missing ``db/`` dir is a silent no-op (fresh install / pre-O2)."""
    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_rmtree_db_dir(tmp_path, erased, failed)
    assert erased == []
    assert failed == {}


# ── _gdpr_rmtree_crash_archive ─────────────────────────────────────────


def test_rmtree_crash_archive_removes_directory(tmp_path: Path) -> None:
    """The ``crash_diagnostics/`` subdir is recursively removed."""
    archive_dir = tmp_path / "crash_diagnostics"
    archive_dir.mkdir()
    (archive_dir / "dump1.txt").write_text("crash")
    (archive_dir / "dump2.txt").write_text("crash")

    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_rmtree_crash_archive(tmp_path, erased, failed)

    assert not archive_dir.exists()
    assert str(archive_dir) in erased
    assert failed == {}


def test_rmtree_crash_archive_missing_dir_is_noop(tmp_path: Path) -> None:
    """A missing archive dir is a silent no-op."""
    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_rmtree_crash_archive(tmp_path, erased, failed)
    assert erased == []
    assert failed == {}


# ── _gdpr_clear_keychain ───────────────────────────────────────────────


def test_clear_keychain_calls_delete_secret_per_provider() -> None:
    """``delete_secret`` is called once per provider in
    ``PROVIDER_TO_CONFIG_FIELD``."""
    from voice_typer.server import credential_store

    app = SimpleNamespace(config=MagicMock())
    failed: dict = {}

    # Spy on delete_secret without changing its behavior.
    original = credential_store.delete_secret
    call_log: list = []

    def _spy(provider, config=None):  # noqa: ANN001
        call_log.append((provider, config))

    credential_store.delete_secret = _spy  # type: ignore[method-assign]
    try:
        PrivacyMixin._gdpr_clear_keychain(app, failed)
    finally:
        credential_store.delete_secret = original  # type: ignore[method-assign]

    expected_providers = list(credential_store.PROVIDER_TO_CONFIG_FIELD)
    assert len(call_log) == len(expected_providers)
    assert [c[0] for c in call_log] == expected_providers
    # Each call passes the app's config object.
    for _, cfg in call_log:
        assert cfg is app.config
    assert failed == {}


def test_clear_keychain_captures_provider_failure() -> None:
    """A raising ``delete_secret`` is recorded in ``failed`` per-provider."""
    from voice_typer.server import credential_store

    app = SimpleNamespace(config=MagicMock())
    failed: dict = {}

    original = credential_store.delete_secret

    def _raising(provider, config=None):  # noqa: ANN001
        if provider == "openai":
            raise RuntimeError("keyring broken")
        return None

    credential_store.delete_secret = _raising  # type: ignore[method-assign]
    try:
        PrivacyMixin._gdpr_clear_keychain(app, failed)
    finally:
        credential_store.delete_secret = original  # type: ignore[method-assign]

    assert "keychain:openai" in failed
    assert "RuntimeError" in failed["keychain:openai"]


# ── _gdpr_invalidate_cached_engines ────────────────────────────────────


def test_invalidate_cached_engines_sets_attrs_to_none() -> None:
    """``_llm_polisher`` and ``_cloud_engine`` are set to ``None``."""
    app = SimpleNamespace(_llm_polisher="polisher", _cloud_engine="engine")
    PrivacyMixin._gdpr_invalidate_cached_engines(app)
    assert app._llm_polisher is None
    assert app._cloud_engine is None


def test_invalidate_cached_engines_swallows_missing_attrs() -> None:
    """An app object without the cached attrs is silently initialized.

    ``setattr`` creates the attribute if it doesn't exist — the
    ``contextlib.suppress`` contract is that no exception propagates
    even if the underlying ``__setattr__`` would reject the write
    (e.g. a frozen dataclass).  For a plain ``SimpleNamespace`` the
    write succeeds and the attrs end up as ``None``.
    """
    app = SimpleNamespace()  # no _llm_polisher / _cloud_engine
    # Must not raise.
    PrivacyMixin._gdpr_invalidate_cached_engines(app)
    assert app._llm_polisher is None
    assert app._cloud_engine is None


# ── _gdpr_recreate_history_db ──────────────────────────────────────────


def test_recreate_history_db_assigns_new_instance(tmp_path: Path) -> None:
    """A fresh ``HistoryDB`` is constructed and assigned to ``app.history_db``."""
    from voice_typer.server import config as cfg_mod

    # Redirect config_dir to tmp_path so HistoryDB() resolves to a
    # writable test location.
    mp = pytest.MonkeyPatch()
    mp.setattr(cfg_mod, "_config_dir", lambda: tmp_path)
    try:
        app = SimpleNamespace(history_db=MagicMock())
        PrivacyMixin._gdpr_recreate_history_db(app)
        # The new HistoryDB is a real instance, not the mock.
        from voice_typer.server.history_db import HistoryDB

        assert isinstance(app.history_db, HistoryDB)
    finally:
        mp.undo()


def test_recreate_history_db_swallows_construction_failure() -> None:
    """If ``HistoryDB()`` raises, the exception is logged and swallowed."""

    app = SimpleNamespace(history_db="closed-instance")

    # Force HistoryDB construction to raise.
    import voice_typer.server.history_db as hdb_mod

    original = hdb_mod.HistoryDB

    class _RaisingHistoryDB:
        def __init__(self, *args, **kwargs):  # noqa: ANN001
            raise OSError("disk full")

    hdb_mod.HistoryDB = _RaisingHistoryDB  # type: ignore[misc]
    try:
        # Must not raise.
        PrivacyMixin._gdpr_recreate_history_db(app)
    finally:
        hdb_mod.HistoryDB = original  # type: ignore[misc]

    # ``app.history_db`` is left as the closed instance.
    assert app.history_db == "closed-instance"


# ── _gdpr_post_cleanup_sweep ───────────────────────────────────────────


def test_post_cleanup_sweep_unlinks_recreated_lock_files(tmp_path: Path) -> None:
    """Re-created ``config.json.lock`` and ``.restart_token`` are unlinked."""
    (tmp_path / "config.json.lock").write_text('{"pid": 123}')
    (tmp_path / ".restart_token").write_text("token")

    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_post_cleanup_sweep(tmp_path, erased, failed)

    assert not (tmp_path / "config.json.lock").exists()
    assert not (tmp_path / ".restart_token").exists()
    assert len(erased) == 2
    assert failed == {}


def test_post_cleanup_sweep_skips_missing_files(tmp_path: Path) -> None:
    """Missing lock files are silently skipped."""
    erased: list = []
    failed: dict = {}
    PrivacyMixin._gdpr_post_cleanup_sweep(tmp_path, erased, failed)
    assert erased == []
    assert failed == {}


# ── _gdpr_build_zip ────────────────────────────────────────────────────


def test_build_zip_writes_all_existing_files(tmp_path: Path) -> None:
    """All existing personal-data files are added to the zip."""
    (tmp_path / "config.json").write_text(json.dumps({"key": "secret"}))
    (tmp_path / "voice-typer.log").write_text("log line")
    (tmp_path / "voice-typer.log.1").write_text("rotated log")
    (tmp_path / "mic-test-20240101.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        PrivacyMixin._gdpr_build_zip(zf, tmp_path)

    zip_buf.seek(0)
    with zipfile.ZipFile(zip_buf, "r") as zf:
        names = zf.namelist()
    assert "config.json" in names
    assert "voice-typer.log" in names
    assert "voice-typer.log.1" in names
    assert "mic-test-20240101.wav" in names


def test_build_zip_skips_missing_files(tmp_path: Path) -> None:
    """Missing personal-data files are silently skipped."""
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        PrivacyMixin._gdpr_build_zip(zf, tmp_path)

    zip_buf.seek(0)
    with zipfile.ZipFile(zip_buf, "r") as zf:
        assert zf.namelist() == []


def test_build_zip_skips_directories(tmp_path: Path) -> None:
    """A glob-matched directory is silently skipped (only files are zipped)."""
    # ``mic-test-*`` matches the glob — but a directory named
    # ``mic-test-bad`` should NOT be added to the zip.
    (tmp_path / "mic-test-bad").mkdir()
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        PrivacyMixin._gdpr_build_zip(zf, tmp_path)

    zip_buf.seek(0)
    with zipfile.ZipFile(zip_buf, "r") as zf:
        assert zf.namelist() == []


# ── _gdpr_rotate_exports ───────────────────────────────────────────────


def test_rotate_exports_keeps_five_most_recent(tmp_path: Path) -> None:
    """Only the 5 most-recent ``gdpr-export-*.zip`` files survive rotation."""
    import time

    paths = []
    for i in range(7):
        p = tmp_path / f"gdpr-export-2024010{i}-120000.zip"
        p.write_bytes(b"zip")
        # Set distinct mtimes so the rotation order is deterministic.
        mtime = time.time() + i  # later index → newer mtime
        import os as _os

        _os.utime(p, (mtime, mtime))
        paths.append(p)

    PrivacyMixin._gdpr_rotate_exports(tmp_path)

    remaining = sorted(tmp_path.glob("gdpr-export-*.zip"))
    assert len(remaining) == 5
    # The 5 newest (i = 2..6) survive; the 2 oldest (i = 0, 1) are unlinked.
    surviving_names = {p.name for p in remaining}
    assert "gdpr-export-20240102-120000.zip" in surviving_names  # i=2
    assert "gdpr-export-20240106-120000.zip" in surviving_names  # i=6
    assert "gdpr-export-20240100-120000.zip" not in surviving_names  # i=0
    assert "gdpr-export-20240101-120000.zip" not in surviving_names  # i=1


def test_rotate_exports_no_exports_is_noop(tmp_path: Path) -> None:
    """A config dir with no exports is a silent no-op."""
    PrivacyMixin._gdpr_rotate_exports(tmp_path)
    assert list(tmp_path.glob("gdpr-export-*.zip")) == []


def test_rotate_exports_under_five_does_nothing(tmp_path: Path) -> None:
    """With ≤5 exports, nothing is unlinked."""
    for i in range(5):
        (tmp_path / f"gdpr-export-2024010{i}-120000.zip").write_bytes(b"zip")

    PrivacyMixin._gdpr_rotate_exports(tmp_path)

    assert len(list(tmp_path.glob("gdpr-export-*.zip"))) == 5


# ── orchestrator thinness (refactor contract) ─────────────────────────


def test_delete_all_personal_data_is_thin_orchestrator() -> None:
    """``delete_all_personal_data`` must be a thin orchestrator.

    Asserts the public method exists, has the documented name, and
    delegates to the extracted helpers.  The full pipeline behavior
    is covered by ``tests/test_gdpr_delete.py`` — this test only
    verifies the refactor's structural contract.
    """
    import inspect

    method = PrivacyMixin.delete_all_personal_data
    src = inspect.getsource(method)
    # The orchestrator must NOT inline the per-step work — it must
    # delegate to the extracted helpers.
    assert "_gdpr_checkpoint_history_db" in src
    assert "_gdpr_unlink_personal_files" in src
    assert "_gdpr_unlink_personal_globs" in src
    assert "_gdpr_rmtree_rust_logs" in src
    assert "_gdpr_clear_keychain" in src
    assert "_gdpr_invalidate_cached_engines" in src
    assert "_gdpr_recreate_history_db" in src
    assert "_gdpr_post_cleanup_sweep" in src


def test_export_gdpr_bundle_is_thin_orchestrator() -> None:
    """``export_gdpr_bundle`` must be a thin orchestrator."""
    import inspect

    method = PrivacyMixin.export_gdpr_bundle
    src = inspect.getsource(method)
    assert "_gdpr_checkpoint_history_db" in src
    assert "_gdpr_build_zip" in src
    assert "_gdpr_rotate_exports" in src
