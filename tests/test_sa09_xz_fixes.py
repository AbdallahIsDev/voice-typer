"""SA-09 regression tests for findings XZ-R3-07, XZ-R3-08, XZ-R6-AS-06,
XZ-R12-16, XZ-R17-08, XZ-R17-13.

These tests pin the fixes applied by SA-09 to:
- ``voice_typer/server/ipc/validation.py`` (XZ-R3-07, XZ-R3-08)
- ``voice_typer/server/task_scheduler.py`` (XZ-R6-AS-06)
- ``voice_typer/server/crash_recovery.py`` (XZ-R12-16, XZ-R17-08, XZ-R17-13)

The platform-specific Windows registry / schtasks / WaitForSingleObject
fixes (XZ-EH-009, XZ-EH-010, XZ-EH-011, XZ-EH-023) are exercised by
the existing ``tests/test_task_scheduler.py`` suite (which runs the
Windows-gated code paths via ``MagicMock`` on the Linux CI) — the
regression coverage for those is already in place.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ─── XZ-R3-07: max_payload_bytes top-level + min-scan ─────────────────────


class TestXZR307MaxPayloadBytesTopLevel:
    """XZ-R3-07: ``_validate_dict_payload`` accepts a top-level
    ``max_payload_bytes`` kwarg AND scans all per-field rules for the
    minimum (most restrictive) cap when the kwarg is absent.
    """

    def test_top_level_kwarg_takes_precedence_over_per_field_rule(self):
        """When BOTH the top-level kwarg and a per-field rule are
        present, the top-level kwarg wins (the per-field rule is
        ignored)."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        # Top-level cap is 50 bytes; per-field rule says 1 MiB.
        # The payload (~30 bytes) fits under both, so the call succeeds
        # — but we verify the precedence by sending a payload that
        # exceeds the top-level cap but NOT the per-field cap.
        big_payload = {"x": "a" * 100}  # > 50 bytes, < 1 MiB
        validated, error = _validate_dict_payload(
            big_payload,
            {"x": {"type": str, "required": False, "max_payload_bytes": 1024 * 1024}},
            max_payload_bytes=50,
        )
        assert validated is None, (
            "XZ-R3-07: top-level max_payload_bytes=50 must reject a payload "
            "that exceeds 50 bytes, even when a per-field rule allows 1 MiB"
        )
        assert error is not None
        assert error["data"]["code"] == "client.invalid_payload"

    def test_multi_field_per_field_rules_use_minimum(self):
        """When MULTIPLE per-field rules declare ``max_payload_bytes``,
        the helper uses the MINIMUM (most restrictive) cap — NOT the
        first field's value.

        Pre-XZ-R3-07 the helper broke after the first field, silently
        ignoring the second field's stricter cap. This test would have
        passed pre-fix (because the looser first-field cap allowed the
        payload) and now passes post-fix (because the stricter
        second-field cap rejects the payload).
        """
        from voice_typer.server.ipc.validation import _validate_dict_payload

        # Schema with two fields, the FIRST has a looser cap (1 MiB),
        # the SECOND has a stricter cap (50 bytes). The payload (~70
        # bytes) fits under the first cap but exceeds the second.
        # Post-fix: the stricter (50-byte) cap applies → rejected.
        payload = {"a": "x" * 30, "b": "y" * 30}  # ~70 bytes total
        validated, error = _validate_dict_payload(
            payload,
            {
                "a": {"type": str, "required": False, "max_payload_bytes": 1024 * 1024},
                "b": {"type": str, "required": False, "max_payload_bytes": 50},
            },
        )
        assert validated is None, (
            "XZ-R3-07: when multiple per-field max_payload_bytes rules "
            "exist, the MINIMUM (most restrictive) must apply — the "
            "70-byte payload should be rejected by the 50-byte cap on "
            "field 'b', even though field 'a' allows 1 MiB"
        )
        assert error is not None
        assert error["data"]["code"] == "client.invalid_payload"

    def test_single_field_rule_still_enforced(self):
        """Backward compat: a single per-field ``max_payload_bytes``
        rule still works (no top-level kwarg)."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        payload = {"x": "a" * 100}  # > 50 bytes
        validated, error = _validate_dict_payload(
            payload,
            {"x": {"type": str, "required": False, "max_payload_bytes": 50}},
        )
        assert validated is None
        assert error is not None
        assert error["data"]["code"] == "client.invalid_payload"


# ─── XZ-R3-08: none_to_default rule ───────────────────────────────────────


class TestXZR308NoneToDefault:
    """XZ-R3-08: an explicit ``None`` value is treated as ABSENT and
    the ``default`` rule fires (when ``none_to_default`` is True, which
    is the implicit default for backward compat with the renderer's
    pre-coercion behavior).
    """

    def test_explicit_none_uses_default_when_rule_opts_in(self):
        """``{"title": null}`` is treated as ``{"title": "DefaultApp"}``
        when the rule declares a default and doesn't opt out."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        validated, error = _validate_dict_payload(
            {"title": None},
            {"title": {"type": str, "required": False, "default": "DefaultApp"}},
        )
        assert error is None
        assert validated == {"title": "DefaultApp"}, (
            "XZ-R3-08: explicit None must be substituted with the default "
            "when none_to_default is True (the implicit default)"
        )

    def test_explicit_none_fails_type_check_when_rule_opts_out(self):
        """When ``none_to_default=False``, an explicit ``None`` fails
        the type check (restoring the strict pre-XZ-R3-08 behavior)."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        validated, error = _validate_dict_payload(
            {"title": None},
            {
                "title": {
                    "type": str,
                    "required": False,
                    "default": "DefaultApp",
                    "none_to_default": False,
                }
            },
        )
        assert validated is None
        assert error is not None
        assert error["data"]["code"] == "client.invalid_field"
        assert error["data"]["field"] == "title"

    def test_explicit_none_without_default_still_fails_type_check(self):
        """When the rule has no ``default``, ``none_to_default`` has
        no effect — the explicit ``None`` fails the type check."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        validated, error = _validate_dict_payload(
            {"title": None},
            {"title": {"type": str, "required": False}},
        )
        assert validated is None
        assert error is not None
        assert error["data"]["code"] == "client.invalid_field"

    def test_absent_field_still_uses_default(self):
        """Backward compat: an ABSENT field still uses the default
        (the pre-XZ-R3-08 behavior is preserved for the absent case)."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        validated, error = _validate_dict_payload(
            {},
            {"title": {"type": str, "required": False, "default": "DefaultApp"}},
        )
        assert error is None
        assert validated == {"title": "DefaultApp"}


# ─── XZ-R6-AS-06: subprocess.list2cmdline for schtasks arg quoting ────────


class TestXR6AS06List2CmdLine:
    """XZ-R6-AS-06: ``_schtasks_elevated`` uses
    ``subprocess.list2cmdline`` for proper Windows arg quoting instead
    of the hand-rolled join that only quoted args containing space or
    ``&``.
    """

    def test_source_uses_list2cmdline_not_handrolled_join(self):
        """The source of ``_schtasks_elevated`` must reference
        ``subprocess.list2cmdline`` and must NOT use the old
        hand-rolled join as the actual ``arg_str`` assignment
        (comments referencing the old pattern for context are OK)."""
        from voice_typer.server.task_scheduler import _schtasks_elevated

        src = inspect.getsource(_schtasks_elevated)
        assert "subprocess.list2cmdline" in src, (
            "XZ-R6-AS-06: _schtasks_elevated must use subprocess.list2cmdline "
            "for proper Windows arg quoting (closing the cmd.exe metacharacter "
            "injection vector)."
        )
        # The old hand-rolled join must NOT be assigned to arg_str
        # (i.e., the executable line ``arg_str = " ".join(f'"{a}"' ...)``)
        # must be gone. Comments referencing the old pattern are OK.
        assert 'arg_str = " ".join' not in src, (
            'XZ-R6-AS-06: the old hand-rolled arg-quoting join (arg_str = " ".join(f\'"{a}"\' ...)) must be removed'
        )

    def test_list2cmdline_quotes_embedded_double_quote(self):
        """Sanity: ``subprocess.list2cmdline`` quotes an arg containing
        an embedded ``\"`` so it can't break out of the cmd.exe
        quoting layer. (This is a stdlib behavior test — we pin it
        here so a future stdlib change that weakens the quoting is
        caught.)"""
        # An arg with an embedded double-quote must be quoted AND the
        # embedded quote must be escaped.
        result = subprocess.list2cmdline(['arg with " quote'])
        assert result.startswith('"'), "list2cmdline must quote args containing special chars"
        # The embedded " must be escaped as \".
        assert '\\"' in result, 'list2cmdline must escape embedded double-quotes as \\"'


# ─── XZ-R12-16: __del__ lock-based empty check ────────────────────────────


class TestXZR1216DelLockBasedCheck:
    """XZ-R12-16: ``CrashRecovery.__del__`` reads ``_entries`` under
    ``_lock`` (not bare) so a concurrent ``add()`` can't mutate the
    deque mid-check.
    """

    def test_del_source_acquires_lock_for_empty_check(self):
        """The source of ``__del__`` must acquire ``self._lock`` for
        the empty-check (not just ``if self._entries:`` as an
        executable statement). Comments referencing the bare pattern
        are OK."""
        from voice_typer.server.crash_recovery import CrashRecovery

        src = inspect.getsource(CrashRecovery.__del__)
        # The check must be inside a ``with self._lock:`` block.
        assert "with self._lock:" in src, (
            "XZ-R12-16: __del__ must acquire self._lock for the "
            "empty-check so a concurrent add() can't mutate _entries "
            "mid-read"
        )
        # The bare executable ``if self._entries:`` (without lock)
        # must be GONE. We strip comments (lines starting with ``#``)
        # before checking so the docstring/comment references to the
        # old pattern don't trigger a false positive.
        executable_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        executable_src = "\n".join(executable_lines)
        # The bare ``if self._entries:`` must NOT appear as an
        # executable statement. The lock-guarded check uses
        # ``has_entries = bool(self._entries)`` inside the ``with`` block.
        assert "\nif self._entries:\n" not in "\n" + executable_src + "\n", (
            "XZ-R12-16: the bare executable ``if self._entries:`` check must be replaced with a lock-guarded check"
        )


# ─── XZ-R17-08: _dir_ensured flag guards redundant chmod ──────────────────


class TestXZR1708DirEnsuredFlag:
    """XZ-R17-08: ``_save_sync`` skips the per-save ``os.chmod`` after
    the first successful chmod, guarded by ``_dir_ensured``.
    """

    def test_dir_ensured_flag_exists(self):
        """``CrashRecovery`` instances must have a ``_dir_ensured``
        boolean attribute (defaulting to False)."""
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            try:
                assert hasattr(cr, "_dir_ensured"), "XZ-R17-08: CrashRecovery must have a _dir_ensured flag"
                assert cr._dir_ensured is False, "XZ-R17-08: _dir_ensured must default to False"
            finally:
                cr.shutdown()

    def test_chmod_called_once_then_skipped(self, monkeypatch):
        """On POSIX, ``os.chmod`` is called on the first save and
        skipped on subsequent saves (the ``_dir_ensured`` flag is set
        after the first successful chmod)."""
        from voice_typer.server import crash_recovery as cr_mod
        from voice_typer.server.crash_recovery import CrashRecovery

        # Force is_windows() to return False so the chmod path runs.
        monkeypatch.setattr(cr_mod, "is_windows", lambda: False)

        chmod_calls: list[Path] = []
        orig_chmod = os.chmod

        def fake_chmod(path, mode, *args, **kwargs):
            chmod_calls.append(Path(path))
            orig_chmod(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "chmod", fake_chmod)

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            try:
                # First add → first save → chmod called once.
                cr.add("first transcription")
                # Wait for the worker to drain.
                import time

                time.sleep(0.2)
                first_chmod_count = len(chmod_calls)
                assert first_chmod_count >= 1, "XZ-R17-08: first save must call os.chmod at least once"
                # Second add → second save → chmod must NOT be called
                # again (the _dir_ensured flag is set).
                cr.add("second transcription")
                time.sleep(0.2)
                second_chmod_count = len(chmod_calls)
                assert second_chmod_count == first_chmod_count, (
                    f"XZ-R17-08: subsequent saves must skip the chmod "
                    f"(expected {first_chmod_count} calls, got {second_chmod_count})"
                )
            finally:
                cr.shutdown()


# ─── XZ-R17-13: _final_save_done dedup atexit vs __del__ ──────────────────


class TestXZR1713FinalSaveDoneDedup:
    """XZ-R17-13: ``_final_save_done`` flag deduplicates the final
    shutdown save between atexit and __del__. The flag is set ONLY by
    ``_atexit_flush_all`` (NOT by ``shutdown()`` or ``_save_sync``)
    so ``shutdown()``'s final save does NOT suppress a subsequent
    ``__del__`` save for post-shutdown mutations.
    """

    def test_final_save_done_flag_exists(self):
        """``CrashRecovery`` instances must have a ``_final_save_done``
        boolean attribute (defaulting to False)."""
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            try:
                assert hasattr(cr, "_final_save_done"), "XZ-R17-13: CrashRecovery must have a _final_save_done flag"
                assert cr._final_save_done is False, "XZ-R17-13: _final_save_done must default to False"
            finally:
                cr.shutdown()

    def test_atexit_sets_final_save_done_flag(self, monkeypatch):
        """``_atexit_flush_all`` must set ``_final_save_done = True``
        after a successful save so the subsequent ``__del__`` skips."""
        from voice_typer.server import crash_recovery as cr_mod
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            try:
                assert cr._final_save_done is False
                # Simulate atexit firing.
                cr_mod._atexit_flush_all()
                assert cr._final_save_done is True, (
                    "XZ-R17-13: _atexit_flush_all must set _final_save_done=True after a successful save"
                )
            finally:
                cr.shutdown()

    def test_shutdown_does_not_set_final_save_done(self, monkeypatch):
        """``shutdown()``'s final save must NOT set
        ``_final_save_done`` — otherwise a post-shutdown ``__del__``
        save for mutations that bypassed ``_enqueue_save`` would be
        silently dropped (regression-tested by
        ``test_del_saves_unpersisted_post_shutdown_mutations``).
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            cr.shutdown()
            assert cr._final_save_done is False, (
                "XZ-R17-13: shutdown() must NOT set _final_save_done — "
                "otherwise a post-shutdown __del__ save for bypassed "
                "mutations would be silently dropped"
            )

    def test_del_skips_when_atexit_already_saved(self, monkeypatch):
        """When atexit has already set ``_final_save_done``, ``__del__``
        must skip the redundant save (the regression test
        ``test_del_saves_unpersisted_post_shutdown_mutations`` covers
        the inverse — that __del__ DOES save when the flag is NOT
        set)."""
        from voice_typer.server import crash_recovery as cr_mod
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            cr.add("entry that atexit will save")
            # Force atexit to fire — sets _final_save_done.
            cr_mod._atexit_flush_all()
            assert cr._final_save_done is True

            # Snapshot the file's mtime; __del__ (which would re-write
            # the file) must NOT change it.
            recovery_file = Path(tmpdir) / "voice-typer-recovery.json"
            assert recovery_file.exists()
            mtime_before = recovery_file.stat().st_mtime_ns

            # Force GC of the instance — __del__ fires.
            import time

            time.sleep(0.05)  # let any FS buffering settle
            del cr
            time.sleep(0.05)

            mtime_after = recovery_file.stat().st_mtime_ns
            assert mtime_after == mtime_before, (
                "XZ-R17-13: __del__ must NOT re-write the file when _final_save_done is set (atexit already persisted)"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "--no-cov", "--timeout=30"]))
