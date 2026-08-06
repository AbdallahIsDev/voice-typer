"""regression tests for ``PersistedJSON`` symlink-
following and Windows-rename bugs.

(High):
    Pre-fix, ``PersistedJSON.save`` used ``Path.read_bytes()`` and
    ``Path.write_bytes()`` for the ``.bak`` comparison + write — both
    follow symlinks.  An attacker who planted symlinks at BOTH
    ``self._path`` and ``self._bak_path`` got a read-from-arbitrary-
    file + write-to-arbitrary-file primitive: the previous config
    (containing API keys for ``credential_store``) was read THROUGH
    the ``self._path`` symlink and written THROUGH the
    ``self._bak_path`` symlink to an attacker-chosen location.

    The fix:
      * Explicitly check ``is_symlink()`` on BOTH paths and SKIP the
        backup if either is a symlink (the main save via
        ``_secure_atomic_write`` is unaffected — it uses
        ``os.replace`` which does NOT follow the destination symlink,
        it replaces it).
      * Use ``_secure_read_text`` (POSIX ``O_NOFOLLOW`` + inode
        re-verification) for the existing-file read.
      * Use ``_secure_atomic_write`` for the ``.bak`` write (its
        ``os.replace`` semantics ensure we never write THROUGH a
        symlink).

(Low):
    Pre-fix, ``_quarantine_corrupt`` used ``Path.rename`` (a.k.a.
    ``os.rename``) which FAILS on Windows if the destination exists.
    Even though the ``while corrupt_path.exists()`` loop tries to
    find a non-existing destination, there's a TOCTOU race window
    where another process (or thread) can create the destination
    file in between the ``exists()`` check and the rename — causing
    the rename to fail on Windows and the corrupt file to be left
    in place (silent corruption-recovery failure).

    The fix uses ``os.replace`` which is atomic AND overwrites an
    existing destination on BOTH POSIX and Windows, closing the
    race.

Test approach (FR-7):
    1. Plant a regular file at ``self._path`` (legit content with
       API-key-like data) and a symlink at ``self._bak_path``
       pointing to an "attacker target" file.  Call ``save()`` with
       new content.  Assert the attacker target file was NOT
       overwritten with the exfiltrated config bytes (the backup is
       skipped because ``self._bak_path`` is a symlink).
    2. Plant a symlink at ``self._path`` pointing to a "sensitive"
       file (e.g. ``/tmp/.../sensitive``) and a regular file at
       ``self._bak_path``.  Call ``save()``.  Assert the sensitive
       file's bytes were NOT exfiltrated into the ``.bak`` (the
       backup is skipped because ``self._path`` is a symlink).
    3. Verify the main save still succeeds (the symlink at
       ``self._path`` is REPLACED by ``os.replace`` with a fresh
       regular file — the attacker's symlink is destroyed).

Test approach (FR-51):
    1. Monkeypatch ``os.rename`` to raise ``OSError`` (simulating
       Windows behaviour where dst exists).  Call
       ``_quarantine_corrupt`` and verify it does NOT raise (because
       the fix uses ``os.replace``, not ``os.rename``).
    2. Track ``os.replace`` calls and verify the fix invokes it.
    3. Pre-create the dst ``.corrupt-<ts>`` file (simulating a
       previous quarantine at the same timestamp) and verify the
       fix overwrites it (proving ``os.replace`` semantics, not
       ``os.rename``).
"""

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


# ---------------------------------------------------------------------------
# PersistedJSON.save refuses to follow symlinks on .bak read/write
# ---------------------------------------------------------------------------


@_POSIX_ONLY
class TestPersistedJSONSaveSymlinkDefense:
    """``PersistedJSON.save`` must refuse to follow symlinks on
    EITHER ``self._path`` (read side) or ``self._bak_path`` (write
    side).  Pre-fix, both used ``Path.read_bytes`` / ``write_bytes``
    which follow symlinks — a read-from-arbitrary-file + write-to-
    arbitrary-file primitive."""

    def test_bak_path_symlink_not_followed_on_write(self, tmp_path):
        """If ``self._bak_path`` is a symlink pointing to an attacker-
        chosen target file, the ``.bak`` write must NOT write through
        the symlink to the target.

        Scenario:
          * ``self._path`` is a regular file with previous-config
            content (containing API-key-like data).
          * ``self._bak_path`` is a symlink → ``attacker_target.json``.
          * ``save()`` is called with new content.

        Pre-fix: ``self._bak_path.write_bytes(existing_bytes)`` would
        follow the symlink and write the previous-config bytes to
        ``attacker_target.json`` — exfiltrating the API key.

        Post-fix: the ``is_symlink()`` check on ``self._bak_path``
        skips the backup; ``attacker_target.json`` is untouched.
        """
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
        # previous-config bytes (the .bak write was skipped because
        # the .bak path is a symlink).
        assert attacker_target.read_text() == "attacker-controlled content", (
            "regression: attacker_target.json was OVERWRITTEN with "
            "the previous config bytes via the .bak symlink. Pre-fix "
            "Path.write_bytes() followed the symlink and wrote the "
            "exfiltrated config (containing the API key) to the "
            "attacker-chosen location."
        )
        # The .bak symlink itself must still exist (we didn't touch
        # it — the backup was skipped).
        assert bak_path.is_symlink()

    def test_path_symlink_not_followed_on_read(self, tmp_path):
        """If ``self._path`` is a symlink pointing to a sensitive file,
        the ``.bak`` read must NOT follow the symlink to read the
        sensitive file's bytes.

        Scenario:
          * ``self._path`` is a symlink → ``sensitive.json`` (which
            contains secret data NOT intended for the config).
          * ``self._bak_path`` is a regular file (or doesn't exist).
          * ``save()`` is called.

        Pre-fix: ``self._path.read_bytes()`` would follow the symlink
        and read ``sensitive.json``'s bytes — exfiltrating them into
        the ``.bak``.

        Post-fix: the ``is_symlink()`` check on ``self._path`` skips
        the backup; ``sensitive.json`` is NOT read.
        """
        from voice_typer.server.secure_file_io import PersistedJSON

        # Sensitive file outside the "config" — not intended to be
        # read by PersistedJSON.
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
        # bytes (the backup was skipped because self._path is a
        # symlink).
        assert bak_path.read_text() == "previous bak content", (
            "regression: the .bak file was OVERWRITTEN with the "
            "sensitive file's bytes (read through the self._path "
            "symlink). Pre-fix Path.read_bytes() followed the symlink "
            "and exfiltrated the sensitive content into the .bak."
        )

    def test_save_still_proceeds_when_path_is_symlink(self, tmp_path):
        """Even when ``self._path`` is a symlink, the main save (via
        ``_secure_atomic_write`` → ``os.replace``) must still proceed.
        ``os.replace`` does NOT follow the destination symlink — it
        REPLACES the symlink itself with a fresh regular file.  So
        the attacker's symlink is destroyed and a real config file
        is written."""
        from voice_typer.server.secure_file_io import PersistedJSON

        sensitive = tmp_path / "sensitive.json"
        sensitive.write_text(json.dumps({"secret": "do-not-overwrite"}), encoding="utf-8")

        config_path = tmp_path / "config.json"
        config_path.symlink_to(sensitive)

        pj = PersistedJSON(config_path, default=None)
        pj.save({"hotkey": "<f5>"})

        # The symlink at config_path must have been REPLACED by a
        # regular file (os.replace does not follow the destination
        # symlink — it replaces the symlink itself).
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
        """Sanity check: when neither path is a symlink, the ``.bak``
        must be created with the previous content (no regression in
        the normal backup behaviour)."""
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
        """Sanity check: saving identical content must NOT churn the
        ``.bak`` (a re-save of the same content is a no-op for the
        backup slot).

        Note: the comparison is BYTE-for-byte.  ``save()`` writes
        ``json.dumps(data, indent=2, ensure_ascii=False)`` — so the
        pre-existing file must be in the SAME format for the bytes
        to match."""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        # Write the config in the EXACT format save() would produce
        # (json.dumps with indent=2, ensure_ascii=False) so the
        # byte-for-byte comparison detects identical content.
        canonical_content = json.dumps({"v": 1}, indent=2, ensure_ascii=False)
        config_path.write_text(canonical_content, encoding="utf-8")

        # Pre-create a .bak with sentinel content.
        bak_path = tmp_path / "config.json.bak"
        bak_path.write_text("sentinel-bak-content", encoding="utf-8")

        pj = PersistedJSON(config_path, default=None)
        pj.save({"v": 1})  # identical to existing content

        # The .bak must NOT have been overwritten (identical content
        # is a no-op for the backup slot).
        assert bak_path.read_text() == "sentinel-bak-content", (
            "regression: the .bak was overwritten even though "
            "the save content was byte-identical to the existing "
            "file. The 'no churn on identical content' invariant "
            "was broken."
        )


# ---------------------------------------------------------------------------
# PersistedJSON.save uses _secure_read_text + _secure_atomic_write
# ---------------------------------------------------------------------------


class TestPersistedJSONSaveUsesSecureHelpers:
    """source-level check that ``PersistedJSON.save`` uses
    ``_secure_read_text`` (not ``read_bytes``) and
    ``_secure_atomic_write`` (not ``write_bytes``) for the .bak
    path.  Pins the fix against a regression that reintroduces the
    symlink-following helpers.

    The check uses the AST (not a regex on the raw source) so that
    docstring mentions of ``Path.read_bytes`` / ``Path.write_bytes``
    (which explain the pre-fix bug) don't false-positive.
    """

    def _method_calls_in_save(self) -> set[str]:
        """Return the set of function/method names called in
        ``PersistedJSON.save``'s AST, EXCLUDING docstrings.

        Uses ``ast.walk`` on the parsed function body to collect
        every ``ast.Call`` whose ``func`` is either:

        * an ``ast.Attribute`` — i.e. anything of the form
          ``obj.method(...)`` (e.g. ``self._path.exists()``,
          ``json.dumps(...)``, ``log.debug(...)``).  The returned
          name is the attribute name (``exists``, ``dumps``, ``debug``).
        * an ``ast.Name`` — i.e. a bare function call
          ``func(...)`` (e.g. ``_secure_read_text(...)``,
          ``_secure_atomic_write(...)``).  The returned name is the
          ``id`` (``_secure_read_text``, ``_secure_atomic_write``).
        """
        import ast
        import inspect
        import textwrap

        from voice_typer.server.secure_file_io import PersistedJSON

        src = inspect.getsource(PersistedJSON.save)
        # Dedent so the source parses as a standalone function (the
        # method is indented inside a class).
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


# ---------------------------------------------------------------------------
# _quarantine_corrupt uses os.replace (not os.rename)
# ---------------------------------------------------------------------------


class TestQuarantineCorruptUsesOsReplace:
    """FR-51: ``_quarantine_corrupt`` must use ``os.replace`` (atomic,
    overwrites on both POSIX and Windows) rather than ``os.rename`` /
    ``Path.rename`` (fails on Windows if dst exists).

    The ``while corrupt_path.exists()`` loop tries to find a non-
    existing destination, but there's a TOCTOU race window where
    another process can create the destination file in between the
    ``exists()`` check and the rename.  On Windows, ``os.rename``
    fails with ``OSError`` (winerror 183) in that case — leaving the
    corrupt file in place and silently breaking the corruption-
    recovery path.  ``os.replace`` is atomic AND overwrites an
    existing destination on both POSIX and Windows, closing the race.
    """

    def test_quarantine_survives_os_rename_failure(self, tmp_path, monkeypatch):
        """If ``os.rename`` raises ``OSError`` (simulating Windows
        behaviour where dst exists), ``_quarantine_corrupt`` must
        still succeed — because the fix uses ``os.replace``, not
        ``os.rename``.

        We monkeypatch ``os.rename`` to ALWAYS raise.  Path.rename
        calls os.rename internally, so this also breaks Path.rename.
        The fix uses os.replace, which is NOT affected by this
        monkeypatch.
        """
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        config_path.write_text("corrupt content", encoding="utf-8")

        # Make os.rename always fail (simulating Windows behaviour).
        def fail_rename(*args, **kwargs):
            raise OSError("simulated Windows rename failure (FR-51 test)")

        monkeypatch.setattr(os, "rename", fail_rename)

        pj = PersistedJSON(config_path, default=None)
        # Must NOT raise — the fix uses os.replace, not os.rename.
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
        """``_quarantine_corrupt`` must call ``os.replace`` (not
        ``os.rename``).  We track ``os.replace`` calls and verify
        the fix invokes it."""
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
        """FR-51 (updated): the new ``_quarantine_corrupt`` implementation
        embeds PID + sub-second nanoseconds in the filename so two
        concurrent quarantine events produce DISTINCT filenames (no
        clobber).  This is STRICTLY BETTER than the previous
        ``os.replace``-overwrites-existing-dst behaviour (which lost
        forensic history when two processes corrupted the same file
        in the same second).

        The test pre-creates a stale ``.corrupt-<ts>-<pid>-<ns>`` file
        matching the EXACT filename the new implementation would
        produce (we mock ``time.time``, ``time.time_ns`` and ``os.getpid``
        to fixed values).  With the new PID+ns suffix, the implementation
        does NOT probe for an existing dst, so the pre-created file IS
        overwritten by ``os.replace`` (which is still the safety net for
        the essentially-impossible case where two calls pick the same
        PID+ns).  This test pins that ``os.replace`` safety-net
        behaviour — but note that the realistic common case is now
        distinct filenames (covered by
        ``test_quarantine_disambiguates_same_ts_dst_exists``).
        """
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        config_path.write_text("new corrupt content", encoding="utf-8")

        # Mock time.time, time.time_ns and os.getpid to fixed values
        # so we can predict the EXACT dst filename the new
        # implementation will produce. The module-level
        # ``_QUARANTINE_SUFFIX_SEQ`` counter must be reset to a fresh
        # ``itertools.count()`` so this test's first call consumes
        # seq=0 (other tests in this module may already have advanced
        # the real module counter).
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
        # implementation will produce (ts-pid-ns pattern).  This
        # simulates the essentially-impossible case where two calls
        # pick the same PID+ns — the os.replace safety-net must
        # overwrite it.
        dst = tmp_path / f"config.json.corrupt-{fixed_ts}-{fixed_pid}-{fixed_ns}"
        dst.write_text("previous quarantine content", encoding="utf-8")

        pj = PersistedJSON(config_path, default=None)
        # Must NOT raise — os.replace overwrites the dst file.
        pj._quarantine_corrupt()

        # The dst file must have been OVERWRITTEN with the new
        # corrupt content (os.replace semantics, not os.rename which
        # would fail on Windows).
        assert dst.read_text() == "new corrupt content", (
            "FR-51 regression: the dst file was NOT overwritten. "
            "Pre-fix Path.rename would fail on Windows (dst exists); "
            "the fix's os.replace overwrites the dst atomically on "
            "both POSIX and Windows."
        )
        # The src must be gone (moved to dst).
        assert not config_path.exists()

    def test_quarantine_disambiguates_same_ts_dst_exists(self, tmp_path, monkeypatch):
        """Sanity check (updated): the new ``_quarantine_corrupt``
        implementation embeds PID + sub-second nanoseconds in the
        filename, so even when ``time.time`` returns a fixed timestamp,
        two back-to-back quarantine calls produce DISTINCT filenames
        (the previous counter-loop ``.corrupt-<ts>.<N>`` pattern is
        gone — the PID+ns suffix makes collision essentially impossible
        without an ``exists()`` probe loop, closing the TOCTOU race).

        We mock ``time.time`` to a fixed timestamp, mock ``os.getpid``
        to a fixed PID, and use TWO distinct ``time.time_ns`` values to
        simulate two back-to-back quarantine calls (the second call's
        ``time.time_ns()`` reading will naturally differ from the
        first's).  Both calls produce distinct ``.corrupt-<ts>-<pid>-<ns>``
        filenames — neither is clobbered.
        """
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"

        fixed_pid = 99999
        fixed_ts = 1234567890
        ns_values = iter([111111, 222222])  # distinct ns for each call

        # Reset the module-level suffix counter so this test's first
        # call consumes seq=0 (a fresh ``itertools.count()`` starts at
        # 0); the second call then consumes seq=1.
        import itertools

        from voice_typer.server import secure_file_io as _sfio

        monkeypatch.setattr(_sfio, "_QUARANTINE_SUFFIX_SEQ", itertools.count())
        monkeypatch.setattr(time, "time", lambda: fixed_ts)
        monkeypatch.setattr(time, "time_ns", lambda: next(ns_values))
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
        # path (e.g. the user kept using the app and it corrupted
        # again).  With the new PID+ns suffix, this produces a DISTINCT
        # filename — no clobber, no overwrite.
        config_path.write_text("second corrupt content", encoding="utf-8")
        pj._quarantine_corrupt()
        # Second call consumes seq=1, so the suffix is ns+1 (222223),
        # keeping the two filenames DISTINCT.
        dst2 = tmp_path / f"config.json.corrupt-{fixed_ts}-{fixed_pid}-222223"
        assert dst2.exists()
        assert dst2.read_text() == "second corrupt content"

        # dst1 must be untouched (no clobber).
        assert dst1.read_text() == "first corrupt content"
        assert not config_path.exists()

        # No counter-loop pattern filenames should exist.
        import re as _re

        for f in tmp_path.glob("config.json.corrupt-*"):
            assert not _re.match(r"^config\.json\.corrupt-\d+\.\d+$", f.name), (
                f"Quarantine filename must NOT match the old counter-loop pattern (.corrupt-<ts>.<N>). Got: {f.name}"
            )

    def test_quarantine_handles_missing_file_gracefully(self, tmp_path):
        """Sanity check: if the file disappeared between the
        ``exists()`` check and the rename, ``_quarantine_corrupt``
        must NOT raise (best-effort — the file is gone, nothing to
        quarantine)."""
        from voice_typer.server.secure_file_io import PersistedJSON

        config_path = tmp_path / "config.json"
        # Don't create the file — _quarantine_corrupt should no-op.
        pj = PersistedJSON(config_path, default=None)
        pj._quarantine_corrupt()  # must NOT raise
        assert not config_path.exists()

    def test_quarantine_source_is_symlink_handles_gracefully(self, tmp_path):
        """FR-51 + interaction: if the source file is a symlink,
        ``_quarantine_corrupt`` must still move it aside (via
        ``os.replace`` which does NOT follow the source symlink —
        it moves the symlink itself).  This is the correct behaviour:
        the symlink is quarantined, the attacker's symlink is removed
        from the config path, and the next save can write a fresh
        regular file."""
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
        # the symlink itself, NOT the symlink target).
        assert not config_path.exists(), (
            "FR-51 regression: the symlink at config_path was NOT "
            "moved aside. os.replace should move the symlink itself."
        )
        quarantine_files = list(tmp_path.glob("config.json.corrupt-*"))
        assert len(quarantine_files) == 1
        # The quarantined file is the SYMLINK (not the target).  On
        # POSIX, renaming a symlink moves the symlink itself.
        assert quarantine_files[0].is_symlink()
        # The sensitive target must be untouched.
        assert sensitive.read_text() == "sensitive"
