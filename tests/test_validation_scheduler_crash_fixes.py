"""SA-09 regression tests for findings XZ-R3-07, XZ-R3-08, XZ-R6-AS-06,"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


class TestMaxPayloadBytesTopLevel:
    """XZ-R3-07: ``_validate_dict_payload`` accepts a top-level"""

    def test_top_level_kwarg_takes_precedence_over_per_field_rule(self):
        """When BOTH the top-level kwarg and a per-field rule are"""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        # Top-level cap is 50 bytes; per-field rule says 1 MiB.
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
        """When MULTIPLE per-field rules declare ``max_payload_bytes``,"""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        # Schema with two fields, the FIRST has a looser cap (1 MiB),
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
            "exist, the MINIMUM (most restrictive) must apply, the "
            "70-byte payload should be rejected by the 50-byte cap on "
            "field 'b', even though field 'a' allows 1 MiB"
        )
        assert error is not None
        assert error["data"]["code"] == "client.invalid_payload"

    def test_single_field_rule_still_enforced(self):
        """Backward compat: a single per-field ``max_payload_bytes``"""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        payload = {"x": "a" * 100}  # > 50 bytes
        validated, error = _validate_dict_payload(
            payload,
            {"x": {"type": str, "required": False, "max_payload_bytes": 50}},
        )
        assert validated is None
        assert error is not None
        assert error["data"]["code"] == "client.invalid_payload"


class TestNoneToDefault:
    """is the implicit default for backward compat with the renderer's"""

    def test_explicit_none_uses_default_when_rule_opts_in(self):
        """``{\"title\": null}`` is treated as ``{\"title\": \"DefaultApp\"}``"""
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
        """When ``none_to_default=False``, an explicit ``None`` fails"""
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
        """When the rule has no ``default``, ``none_to_default`` has"""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        validated, error = _validate_dict_payload(
            {"title": None},
            {"title": {"type": str, "required": False}},
        )
        assert validated is None
        assert error is not None
        assert error["data"]["code"] == "client.invalid_field"

    def test_absent_field_still_uses_default(self):
        """Backward compat: an ABSENT field still uses the default"""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        validated, error = _validate_dict_payload(
            {},
            {"title": {"type": str, "required": False, "default": "DefaultApp"}},
        )
        assert error is None
        assert validated == {"title": "DefaultApp"}


class TestList2CmdLine:
    """XZ-R6-AS-06: ``_schtasks_elevated`` uses"""

    def test_source_uses_list2cmdline_not_handrolled_join(self):
        """
        The source of ``_schtasks_elevated`` must reference
        ``subprocess.list2cmdline`` and must NOT use the old
        """
        from voice_typer.server.task_scheduler import _schtasks_elevated

        src = inspect.getsource(_schtasks_elevated)
        assert "subprocess.list2cmdline" in src, (
            "XZ-R6-AS-06: _schtasks_elevated must use subprocess.list2cmdline "
            "for proper Windows arg quoting (closing the cmd.exe metacharacter "
            "injection vector)."
        )
        # The old hand-rolled join must NOT be assigned to arg_str
        assert 'arg_str = " ".join' not in src, (
            'XZ-R6-AS-06: the old hand-rolled arg-quoting join (arg_str = " ".join(f\'"{a}"\' ...)) must be removed'
        )

    def test_list2cmdline_quotes_embedded_double_quote(self):
        """Sanity: ``subprocess.list2cmdline`` quotes an arg containing"""
        result = subprocess.list2cmdline(['arg with " quote'])
        assert result.startswith('"'), "list2cmdline must quote args containing special chars"
        # The embedded " must be escaped as \".
        assert '\\"' in result, 'list2cmdline must escape embedded double-quotes as \\"'


# __del__ lock-based empty check ────────────────────────────


class TestDelLockBasedCheck:
    """XZ-R12-16: ``CrashRecovery._cleanup_flush_pending`` reads ``_entries``"""

    def test_del_source_acquires_lock_for_empty_check(self):
        """The source of ``_cleanup_flush_pending`` must acquire"""
        from voice_typer.server.crash_recovery import CrashRecovery

        src = inspect.getsource(CrashRecovery._cleanup_flush_pending)
        # The check must be inside a ``with self._lock:`` block.
        assert "with self._lock:" in src, (
            "XZ-R12-16: _cleanup_flush_pending must acquire self._lock for the "
            "empty-check so a concurrent add() can't mutate _entries "
            "mid-read"
        )
        # The bare executable ``if self._entries:`` (without lock)
        executable_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        executable_src = "\n".join(executable_lines)
        # The bare ``if self._entries:`` must NOT appear as an
        assert "\nif self._entries:\n" not in "\n" + executable_src + "\n", (
            "XZ-R12-16: the bare executable ``if self._entries:`` check must be replaced with a lock-guarded check"
        )


# _dir_ensured flag guards redundant chmod ──────────────────


class TestDirEnsuredFlag:
    """XZ-R17-08: ``_save_sync`` skips the per-save ``os.chmod`` after"""

    def test_dir_ensured_flag_exists(self):
        """``CrashRecovery`` instances must have a ``_dir_ensured``"""
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            try:
                assert hasattr(cr, "_dir_ensured"), "XZ-R17-08: CrashRecovery must have a _dir_ensured flag"
                assert cr._dir_ensured is False, "XZ-R17-08: _dir_ensured must default to False"
            finally:
                cr.shutdown()

    def test_chmod_called_once_then_skipped(self, monkeypatch):
        """On POSIX, the DIRECTORY ``os.chmod`` is called on the first"""
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
                config_dir = Path(tmpdir).resolve()
                dir_chmod_calls = [p for p in chmod_calls if p.resolve() == config_dir and p.is_dir()]
                first_dir_chmod_count = len(dir_chmod_calls)
                assert first_dir_chmod_count >= 1, (
                    f"XZ-R17-08: first save must call os.chmod on the "
                    f"config dir at least once (got {first_dir_chmod_count} "
                    f"dir chmods in {chmod_calls!r})"
                )
                # Second add → second save → dir-chmod must NOT be
                cr.add("second transcription")
                time.sleep(0.2)
                dir_chmod_calls = [p for p in chmod_calls if p.resolve() == config_dir and p.is_dir()]
                second_dir_chmod_count = len(dir_chmod_calls)
                assert second_dir_chmod_count == first_dir_chmod_count, (
                    f"XZ-R17-08: subsequent saves must skip the dir-chmod "
                    f"(expected {first_dir_chmod_count} calls, got "
                    f"{second_dir_chmod_count}; all chmod calls: "
                    f"{chmod_calls!r})"
                )
            finally:
                cr.shutdown()


# _final_save_done dedup atexit vs __del__ ──────────────────


class TestFinalSaveDoneDedup:
    """XZ-R17-13: ``_final_save_done`` flag deduplicates the final"""

    def test_final_save_done_flag_exists(self):
        """``CrashRecovery`` instances must have a ``_final_save_done``"""
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            try:
                assert hasattr(cr, "_final_save_done"), "XZ-R17-13: CrashRecovery must have a _final_save_done flag"
                assert cr._final_save_done is False, "XZ-R17-13: _final_save_done must default to False"
            finally:
                cr.shutdown()

    def test_atexit_sets_final_save_done_flag(self, monkeypatch):
        """``_atexit_flush_all`` must set ``_final_save_done = True``"""
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
        """``shutdown()``'s final save must NOT set"""
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            cr.shutdown()
            assert cr._final_save_done is False, (
                "XZ-R17-13: shutdown() must NOT set _final_save_done, "
                "otherwise a post-shutdown __del__ save for bypassed "
                "mutations would be silently dropped"
            )

    def test_del_skips_when_atexit_already_saved(self, monkeypatch):
        """When atexit has already set ``_final_save_done``, ``__del__``"""
        from voice_typer.server import crash_recovery as cr_mod
        from voice_typer.server.crash_recovery import CrashRecovery

        with tempfile.TemporaryDirectory() as tmpdir:
            cr = CrashRecovery(config_dir=Path(tmpdir))
            cr.add("entry that atexit will save")
            # Force atexit to fire, sets _final_save_done.
            cr_mod._atexit_flush_all()
            assert cr._final_save_done is True

            # the file) must NOT change it.
            recovery_file = Path(tmpdir) / "recovery.json"
            assert recovery_file.exists()
            mtime_before = recovery_file.stat().st_mtime_ns

            # Force GC of the instance, __del__ fires.
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
