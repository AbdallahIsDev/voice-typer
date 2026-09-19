"""regression tests for ``PersistedJSON`` symlink-"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only: symlink creation + O_NOFOLLOW behavior",
)


@_POSIX_ONLY
class TestPersistedJSONSaveSymlinkDefense:
    """EITHER ``self._path`` (read side) or ``self._bak_path`` (write"""

    def test_bak_path_symlink_not_followed_on_write(self, tmp_path):
        """If ``self._bak_path`` is a symlink pointing to an attacker-"""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        # Previous config content with API-key-like data.
        previous_content = json.dumps({"openai_api_key": "sk-secret-12345"})
        config_path.write_text(previous_content, encoding="utf-8")

        # Plant a symlink at the .bak path → attacker_target.json.
        attacker_target = tmp_path / "attacker_target.json"
        attacker_target.write_text("attacker-controlled content", encoding="utf-8")
        bak_path = tmp_path / "config.json.bak"
        bak_path.symlink_to(attacker_target)

        pj = PersistedJSON(config_path, default=None)
        pj.save({"openai_api_key": "sk-new-key"})

        # the attacker_target file must NOT contain the
        assert attacker_target.read_text() == "attacker-controlled content", (
            "regression: attacker_target.json was OVERWRITTEN with "
            "the previous config bytes via the .bak symlink. Pre-fix "
            "Path.write_bytes() followed the symlink and wrote the "
            "exfiltrated config (containing the API key) to the "
            "attacker-chosen location."
        )
        # The .bak symlink itself must still exist (we didn't touch
        assert bak_path.is_symlink()

    def test_path_symlink_not_followed_on_read(self, tmp_path):
        """If ``self._path`` is a symlink pointing to a sensitive file,"""
        from voice_typer.server.secure_file_io import PersistedJSON

        # Sensitive file outside the "config", not intended to be
        sensitive = tmp_path / "sensitive.json"
        sensitive.write_text(json.dumps({"secret": "do-not-exfiltrate"}), encoding="utf-8")

        # Plant a symlink at the config path → sensitive.json.
        config_path = tmp_path / "config.json"
        config_path.symlink_to(sensitive)

        # The .bak path is a regular (non-symlink) file.
        bak_path = tmp_path / "config.json.bak"
        bak_path.write_text("previous bak content", encoding="utf-8")

        pj = PersistedJSON(config_path, default=None)
        pj.save({"hotkey": "<f5>"})

        # the .bak file must NOT contain the sensitive file's
        assert bak_path.read_text() == "previous bak content", (
            "regression: the .bak file was OVERWRITTEN with the "
            "sensitive file's bytes (read through the self._path "
            "symlink). Pre-fix Path.read_bytes() followed the symlink "
            "and exfiltrated the sensitive content into the .bak."
        )

    def test_save_still_proceeds_when_path_is_symlink(self, tmp_path):
        """Even when ``self._path`` is a symlink, the main save (via"""
        from voice_typer.server.secure_file_io import PersistedJSON

        sensitive = tmp_path / "sensitive.json"
        sensitive.write_text(json.dumps({"secret": "do-not-overwrite"}), encoding="utf-8")

        config_path = tmp_path / "config.json"
        config_path.symlink_to(sensitive)

        pj = PersistedJSON(config_path, default=None)
        pj.save({"hotkey": "<f5>"})

        assert not config_path.is_symlink(), (
            "the symlink at self._path should have been replaced "
            "by a regular file via os.replace (which does NOT follow "
            "the destination symlink)."
        )
        assert config_path.is_file()
        # The new content must be the saved config.
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data == {"hotkey": "<f5>"}
        # The sensitive file must NOT have been overwritten.
        sensitive_data = json.loads(sensitive.read_text(encoding="utf-8"))
        assert sensitive_data == {"secret": "do-not-overwrite"}, (
            "regression: the sensitive file (symlink target) was "
            "overwritten with the new config content. os.replace should "
            "have replaced the SYMLINK ITSELF, not the symlink target."
        )

    def test_normal_save_creates_bak(self, tmp_path):
        """Sanity check: when neither path is a symlink, the ``.bak``"""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"v": 1}), encoding="utf-8")

        pj = PersistedJSON(config_path, default=None)
        pj.save({"v": 2})

        # The .bak must contain the previous content.
        bak_path = tmp_path / "config.json.bak"
        assert bak_path.exists()
        bak_data = json.loads(bak_path.read_text(encoding="utf-8"))
        assert bak_data == {"v": 1}, f"regression: .bak should contain previous content {{'v': 1}}, got {bak_data}"
        # The main file must contain the new content.
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data == {"v": 2}

    def test_identical_content_no_bak_churn(self, tmp_path):
        """backup slot)."""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        # Write the config in the EXACT format save() would produce
        canonical_content = json.dumps({"v": 1}, indent=2, ensure_ascii=False)
        config_path.write_text(canonical_content, encoding="utf-8")

        # Pre-create a .bak with sentinel content.
        bak_path = tmp_path / "config.json.bak"
        bak_path.write_text("sentinel-bak-content", encoding="utf-8")

        pj = PersistedJSON(config_path, default=None)
        pj.save({"v": 1})  # identical to existing content

        # The .bak must NOT have been overwritten (identical content
        assert bak_path.read_text() == "sentinel-bak-content", (
            "regression: the .bak was overwritten even though "
            "the save content was byte-identical to the existing "
            "file. The 'no churn on identical content' invariant "
            "was broken."
        )


class TestPersistedJSONSaveUsesSecureHelpers:
    """source-level check that ``PersistedJSON.save`` uses"""

    def _method_calls_in_save(self) -> set[str]:
        """``PersistedJSON.save``'s AST, EXCLUDING docstrings."""
        import ast
        import inspect
        import textwrap

        from voice_typer.server.secure_file_io import PersistedJSON

        src = inspect.getsource(PersistedJSON.save)
        src = textwrap.dedent(src)
        tree = ast.parse(src)
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
        return calls

    def test_save_does_not_call_read_bytes(self):
        calls = self._method_calls_in_save()
        assert "read_bytes" not in calls, (
            f"regression: PersistedJSON.save calls .read_bytes() "
            f"which follows symlinks. The fix routes the read through "
            f"_secure_read_text (POSIX O_NOFOLLOW) instead. "
            f"(All method calls in save(): {sorted(calls)})"
        )

    def test_save_does_not_call_write_bytes(self):
        calls = self._method_calls_in_save()
        assert "write_bytes" not in calls, (
            f"regression: PersistedJSON.save calls .write_bytes() "
            f"which follows symlinks. The fix routes the write through "
            f"_secure_atomic_write (os.replace, no symlink follow) "
            f"instead. (All method calls in save(): {sorted(calls)})"
        )

    def test_save_uses_secure_read_text(self):
        calls = self._method_calls_in_save()
        assert "_secure_read_text" in calls, (
            "regression: PersistedJSON.save does not call "
            "_secure_read_text for the existing-file read. The fix "
            "routes the read through _secure_read_text (POSIX "
            "O_NOFOLLOW + inode re-verification). "
            f"(All method calls in save(): {sorted(calls)})"
        )

    def test_save_uses_is_symlink_check(self):
        calls = self._method_calls_in_save()
        assert "is_symlink" in calls, (
            "regression: PersistedJSON.save does not check "
            "is_symlink() on self._path / self._bak_path. The fix "
            "explicitly refuses to back up if either path is a "
            "symlink (defense-in-depth on top of _secure_read_text's "
            "O_NOFOLLOW). "
            f"(All method calls in save(): {sorted(calls)})"
        )


class TestQuarantineCorruptUsesOsReplace:
    """FR-51: ``_quarantine_corrupt`` must use ``os.replace`` (atomic,"""

    def test_quarantine_survives_os_rename_failure(self, tmp_path, monkeypatch):
        """If ``os.rename`` raises ``OSError`` (simulating Windows"""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        config_path.write_text("corrupt content", encoding="utf-8")

        # Make os.rename always fail (simulating Windows behaviour).
        def fail_rename(*args, **kwargs):
            raise OSError("simulated Windows rename failure (FR-51 test)")

        monkeypatch.setattr(os, "rename", fail_rename)

        pj = PersistedJSON(config_path, default=None)
        # Must NOT raise, the fix uses os.replace, not os.rename.
        pj._quarantine_corrupt()

        # The corrupt file must have been moved aside.
        assert not config_path.exists(), (
            "FR-51 regression: the corrupt file was NOT moved aside. "
            "The fix should use os.replace (which works even when "
            "os.rename fails on Windows)."
        )
        quarantine_files = list(tmp_path.glob("config.json.corrupt-*"))
        assert len(quarantine_files) == 1
        assert quarantine_files[0].read_text() == "corrupt content"

    def test_quarantine_calls_os_replace(self, tmp_path, monkeypatch):
        """``_quarantine_corrupt`` must call ``os.replace`` (not"""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        config_path.write_text("corrupt content", encoding="utf-8")

        # Track os.replace calls.
        replace_calls: list[tuple[str, str]] = []
        original_replace = os.replace

        def tracking_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
            replace_calls.append((str(src), str(dst)))
            return original_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        monkeypatch.setattr(os, "replace", tracking_replace)

        pj = PersistedJSON(config_path, default=None)
        pj._quarantine_corrupt()

        assert len(replace_calls) == 1, (
            f"FR-51 regression: expected exactly 1 os.replace call, "
            f"got {len(replace_calls)}. The fix should use os.replace "
            f"(not os.rename) so the quarantine works on Windows."
        )
        src, dst = replace_calls[0]
        assert src == str(config_path)
        assert "config.json.corrupt-" in dst

    def test_quarantine_overwrites_existing_dst(self, tmp_path, monkeypatch):
        """FR-51 (updated): the new ``_quarantine_corrupt`` implementation"""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        config_path.write_text("new corrupt content", encoding="utf-8")

        # Mock time.time, time.time_ns and os.getpid to fixed values
        import itertools

        from voice_typer.server import secure_file_io as _sfio

        monkeypatch.setattr(_sfio, "_QUARANTINE_SUFFIX_SEQ", itertools.count())
        fixed_ts = 12345
        fixed_pid = 99999
        fixed_ns = 777777
        monkeypatch.setattr(time, "time", lambda: fixed_ts)
        monkeypatch.setattr(time, "time_ns", lambda: fixed_ns)
        monkeypatch.setattr(os, "getpid", lambda: fixed_pid)

        # Pre-create the dst file at the EXACT filename the new
        dst = tmp_path / f"config.json.corrupt-{fixed_ts}-{fixed_pid}-{fixed_ns}"
        dst.write_text("previous quarantine content", encoding="utf-8")

        pj = PersistedJSON(config_path, default=None)
        # Must NOT raise, os.replace overwrites the dst file.
        pj._quarantine_corrupt()

        # The dst file must have been OVERWRITTEN with the new
        assert dst.read_text() == "new corrupt content", (
            "FR-51 regression: the dst file was NOT overwritten. "
            "Pre-fix Path.rename would fail on Windows (dst exists); "
            "the fix's os.replace overwrites the dst atomically on "
            "both POSIX and Windows."
        )
        # The src must be gone (moved to dst).
        assert not config_path.exists()

    def test_quarantine_disambiguates_same_ts_dst_exists(self, tmp_path, monkeypatch):
        """Sanity check (updated): the new ``_quarantine_corrupt``"""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"

        fixed_pid = 99999
        fixed_ts = 1234567890
        ns_values = iter([111111, 222222])  # distinct ns for each call

        # Reset the module-level suffix counter so this test's first
        import itertools

        from voice_typer.server import secure_file_io as _sfio

        monkeypatch.setattr(_sfio, "_QUARANTINE_SUFFIX_SEQ", itertools.count())
        # Patch secure_file_io's module-level ``time`` binding (a shim
        import types as _types

        monkeypatch.setattr(
            _sfio,
            "time",
            _types.SimpleNamespace(
                time=lambda: fixed_ts,
                time_ns=lambda: next(ns_values),
            ),
        )
        monkeypatch.setattr(os, "getpid", lambda: fixed_pid)

        # First quarantine.
        config_path.write_text("first corrupt content", encoding="utf-8")
        pj = PersistedJSON(config_path, default=None)
        pj._quarantine_corrupt()
        dst1 = tmp_path / f"config.json.corrupt-{fixed_ts}-{fixed_pid}-111111"
        assert dst1.exists()
        assert dst1.read_text() == "first corrupt content"
        assert not config_path.exists()

        # Second quarantine with a DIFFERENT corrupt file at the same
        config_path.write_text("second corrupt content", encoding="utf-8")
        pj._quarantine_corrupt()
        # Second call consumes seq=1, so the suffix is ns+1 (222223),
        dst2 = tmp_path / f"config.json.corrupt-{fixed_ts}-{fixed_pid}-222223"
        assert dst2.exists()
        assert dst2.read_text() == "second corrupt content"

        assert dst1.read_text() == "first corrupt content"
        assert not config_path.exists()

        # No counter-loop pattern filenames should exist.
        import re as _re

        for f in tmp_path.glob("config.json.corrupt-*"):
            assert not _re.match(r"^config\.json\.corrupt-\d+\.\d+$", f.name), (
                f"Quarantine filename must NOT match the old counter-loop pattern (.corrupt-<ts>.<N>). Got: {f.name}"
            )

    def test_quarantine_handles_missing_file_gracefully(self, tmp_path):
        """``exists()`` check and the rename, ``_quarantine_corrupt``"""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        # Don't create the file, _quarantine_corrupt should no-op.
        pj = PersistedJSON(config_path, default=None)
        pj._quarantine_corrupt()  # must NOT raise
        assert not config_path.exists()

    def test_quarantine_source_is_symlink_handles_gracefully(self, tmp_path):
        """FR-51 + interaction: if the source file is a symlink,"""
        from voice_typer.server.secure_file_io import PersistedJSON

        # Only run on POSIX (symlink creation).
        if sys.platform == "win32":
            pytest.skip("POSIX-only: symlink creation")

        # Plant a symlink at config_path → sensitive.json.
        sensitive = tmp_path / "sensitive.json"
        sensitive.write_text("sensitive", encoding="utf-8")
        config_path = tmp_path / "config.json"
        config_path.symlink_to(sensitive)

        pj = PersistedJSON(config_path, default=None)
        pj._quarantine_corrupt()  # must NOT raise

        # The symlink must have been moved aside (os.replace moves
        assert not config_path.exists(), (
            "FR-51 regression: the symlink at config_path was NOT "
            "moved aside. os.replace should move the symlink itself."
        )
        quarantine_files = list(tmp_path.glob("config.json.corrupt-*"))
        assert len(quarantine_files) == 1
        assert quarantine_files[0].is_symlink()
        # The sensitive target must be untouched.
        assert sensitive.read_text() == "sensitive"


class TestSaveChmodSingleSource:
    """
    EVERY success branch (see its body: ``_chmod_owner_only(target)``
    ``PersistedJSON.save`` must NOT re-chmod the same paths a second
    """

    def test_chmod_owner_only_invoked_exactly_once_per_write(self, tmp_path, monkeypatch):
        """A save that churns BOTH the ``.bak`` and the main file must"""
        import voice_typer.server.security.file_io as _fio
        from voice_typer.server.secure_file_io import PersistedJSON

        calls: list[str] = []
        real_chmod_owner_only = _fio._chmod_owner_only

        def counting_chmod_owner_only(path):
            calls.append(str(path))
            return real_chmod_owner_only(path)

        monkeypatch.setattr(_fio, "_chmod_owner_only", counting_chmod_owner_only)

        config_path = tmp_path / "config.json"
        config_path.write_text('{"previous": "content"}', encoding="utf-8")
        pj: PersistedJSON = PersistedJSON(config_path, default={})

        # New content differs from the on-disk content → the .bak is
        pj.save({"new": "content"})

        assert len(calls) == 2, (
            f"expected exactly 2 chmod-to-0o600 calls (one per "
            f"_secure_atomic_write: .bak + main file), got {len(calls)} for "
            f"{calls}, save() is re-chmoding a path that "
            f"_secure_atomic_write already chmod'd (redundant layer)"
        )
        from pathlib import Path as _Path

        chmodded = {_Path(c).name for c in calls}
        assert chmodded == {"config.json", "config.json.bak"}

    @_POSIX_ONLY
    def test_saved_files_have_owner_only_permissions(self, tmp_path):
        """
        End-to-end permission guarantee (state-based): after a save
        ``_secure_atomic_write``, this test pins the guarantee, not
        """
        import stat as _stat

        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        config_path.write_text('{"previous": "content"}', encoding="utf-8")
        pj: PersistedJSON = PersistedJSON(config_path, default={})
        pj.save({"new": "content"})

        for p in (config_path, config_path.with_name("config.json.bak")):
            mode = _stat.S_IMODE(p.stat().st_mode)
            assert mode == 0o600, f"{p.name} must be 0o600 owner-only, got {oct(mode)}"
