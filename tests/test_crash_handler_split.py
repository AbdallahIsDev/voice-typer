"""tests for the ``crash_handler`` package split."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ``crash_handler`` module. Compiled from:
PUBLIC_FUNCTIONS = [
    "set_crash_handler_config_dir",
    "report_pending_crash",
    "install_crash_handler",
    "remove_crash_handler",
    "install_python_excepthook",
]
PRIVATE_FUNCTIONS = [
    "_vectored_handler_impl",
    "_crash_excepthook",
    "_compute_crash_header",
    "_write_u32_hex",
    "_write_u64_hex",
    "_format_redacted_traceback",
    "_sweep_stale_diagnostics",
    "_archive_crash_file",
    "_enforce_archive_retention",
    "_get_active_asr_backend",
    "_ensure_kernel32",
    "_write_to_file",
    "_write_timestamp",
]
CONSTANTS = [
    "STATUS_HEAP_CORRUPTION",
    "STATUS_ACCESS_VIOLATION",
    "STATUS_STACK_BUFFER_OVERRUN",
    "STATUS_FATAL_APP_EXIT",
    # extended fatal codes
    "STATUS_ILLEGAL_INSTRUCTION",
    "STATUS_INT_DIVIDE_BY_ZERO",
    "STATUS_PRIVILEGED_INSTRUCTION",
    "STATUS_IN_PAGE_ERROR",
    "STATUS_STACK_OVERFLOW",
    "STATUS_NONCONTINUABLE_EXCEPTION",
    "STATUS_INVALID_HANDLE",
    "STATUS_DATATYPE_MISALIGNMENT",
    "STATUS_GUARD_PAGE_VIOLATION",
    "_CRASH_CODES",
    "EXCEPTION_CONTINUE_SEARCH",
    "GENERIC_WRITE",
    "FILE_SHARE_READ",
    "FILE_SHARE_WRITE",
    "OPEN_ALWAYS",
    "FILE_ATTRIBUTE_NORMAL",
]
MUTABLE_STATE = [
    "_crash_file_path",
    "_PID",
    "_handler_handle",
    "_kernel32",
    "_crash_written",
    "_python_crash_dir",
    "_crash_header_bytes",
    "_original_excepthook",
    "_vectored_handler",
]
READONLY_REFS = [
    "_crash_msg_buf",
    "_CRASH_MSG_LAYOUT",
    "_CRASH_MSG_BUF_SIZE",
    "_BOM",
    "_SEP",
    "_CRASH_LABEL",
    "_CODE_LABEL",
    "_ADDR_LABEL",
    "_PID_LABEL",
    "_TID_LABEL",
    "_NL",
    "_NAME_HEAP",
    "_NAME_ACCESS",
    "_NAME_STACK",
    "_NAME_FATAL",
    "_NAME_ILLEGAL_INSTRUCTION",
    "_NAME_INT_DIVIDE_BY_ZERO",
    "_NAME_PRIVILEGED_INSTRUCTION",
    "_NAME_IN_PAGE_ERROR",
    "_NAME_STACK_OVERFLOW",
    "_NAME_NONCONTINUABLE",
    "_NAME_INVALID_HANDLE",
    "_NAME_MISALIGNMENT",
    "_NAME_GUARD_PAGE",
    "_NAME_UNKNOWN",
    "_HEX_CHARS",
]
STRUCTS = [
    "_ExceptionRecord",
    "_ExceptionPointers",
    "_SYSTEMTIME",
]


class TestBackwardCompatNames:
    """Every name that tests previously imported is still on the facade."""

    def test_public_functions_importable(self):
        from voice_typer.server import crash_handler

        for name in PUBLIC_FUNCTIONS:
            assert hasattr(crash_handler, name), (
                f"crash_handler.{name} is missing, public function not re-exported by facade"
            )
            assert callable(getattr(crash_handler, name)), f"crash_handler.{name} is not callable"

    def test_private_functions_importable(self):
        from voice_typer.server import crash_handler

        for name in PRIVATE_FUNCTIONS:
            assert hasattr(crash_handler, name), (
                f"crash_handler.{name} is missing, private function not re-exported by facade"
            )
            assert callable(getattr(crash_handler, name)), f"crash_handler.{name} is not callable"

    def test_constants_importable(self):
        from voice_typer.server import crash_handler

        for name in CONSTANTS:
            assert hasattr(crash_handler, name), f"crash_handler.{name} is missing, constant not re-exported by facade"

    def test_constants_have_correct_values(self):
        """The status codes must match the original values exactly."""
        from voice_typer.server import crash_handler

        assert crash_handler.STATUS_HEAP_CORRUPTION == 0xC0000374
        assert crash_handler.STATUS_ACCESS_VIOLATION == 0xC0000005
        assert crash_handler.STATUS_STACK_BUFFER_OVERRUN == 0xC0000409
        assert crash_handler.STATUS_FATAL_APP_EXIT == 0x40000015
        assert crash_handler.EXCEPTION_CONTINUE_SEARCH == 0x0
        original_four = frozenset(
            {
                crash_handler.STATUS_HEAP_CORRUPTION,
                crash_handler.STATUS_ACCESS_VIOLATION,
                crash_handler.STATUS_STACK_BUFFER_OVERRUN,
                crash_handler.STATUS_FATAL_APP_EXIT,
            }
        )
        assert original_four <= crash_handler._CRASH_CODES, (
            "original four codes must remain in _CRASH_CODES after the extension"
        )

    def test_extended_codes_present(self):
        """the 9 extended fatal codes (added to ``_CRASH_CODES``)"""
        from voice_typer.server import crash_handler

        assert crash_handler.STATUS_ILLEGAL_INSTRUCTION == 0xC000001D
        assert crash_handler.STATUS_INT_DIVIDE_BY_ZERO == 0xC0000094
        assert crash_handler.STATUS_PRIVILEGED_INSTRUCTION == 0xC0000096
        assert crash_handler.STATUS_IN_PAGE_ERROR == 0xC0000006
        assert crash_handler.STATUS_STACK_OVERFLOW == 0xC00000FD
        assert crash_handler.STATUS_NONCONTINUABLE_EXCEPTION == 0xC0000025
        assert crash_handler.STATUS_INVALID_HANDLE == 0xC0000008
        assert crash_handler.STATUS_DATATYPE_MISALIGNMENT == 0xC0000002
        assert crash_handler.STATUS_GUARD_PAGE_VIOLATION == 0x80000001

    def test_structs_importable(self):
        from voice_typer.server import crash_handler

        for name in STRUCTS:
            assert hasattr(crash_handler, name), f"crash_handler.{name} is missing, struct not re-exported by facade"

    def test_mutable_state_writable(self):
        """Test mutations on facade state must propagate (TY-39 invariant)."""
        from voice_typer.server import crash_handler

        # Save original values.
        saved = {k: getattr(crash_handler, k) for k in MUTABLE_STATE}
        try:
            # Write test values.
            crash_handler._crash_file_path = "/test/path"
            crash_handler._PID = 99999
            crash_handler._handler_handle = 12345
            crash_handler._kernel32 = "fake_kernel32"
            crash_handler._crash_written = True
            crash_handler._python_crash_dir = Path("/tmp/test")
            crash_handler._crash_header_bytes = b"test_header"

            # Verify reads see the written values.
            assert crash_handler._crash_file_path == "/test/path"
            assert crash_handler._PID == 99999
            assert crash_handler._handler_handle == 12345
            assert crash_handler._kernel32 == "fake_kernel32"
            assert crash_handler._crash_written is True
            assert crash_handler._python_crash_dir == Path("/tmp/test")
            assert crash_handler._crash_header_bytes == b"test_header"
        finally:
            # Restore.
            for k, v in saved.items():
                setattr(crash_handler, k, v)

    def test_readonly_refs_importable(self):
        from voice_typer.server import crash_handler

        for name in READONLY_REFS:
            assert hasattr(crash_handler, name), (
                f"crash_handler.{name} is missing, read-only ref not re-exported by facade"
            )


class TestPerPlatformGuard:
    """On Linux, importing crash_handler must NOT load ``ctypes.wintypes``."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows-specific guard test, wintypes IS loaded on Windows",
    )
    def test_import_does_not_load_wintypes(self):
        """On non-Windows, ``ctypes.wintypes`` must NOT be in"""
        mods_to_remove = [
            k
            for k in list(sys.modules)
            if k == "voice_typer.server.crash_handler" or k.startswith("voice_typer.server.crash_handler.")
        ]
        # Save the ORIGINAL module objects so they can be restored after
        saved_modules = {k: sys.modules[k] for k in mods_to_remove}
        for k in mods_to_remove:
            del sys.modules[k]
        # Also remove ctypes.wintypes if it was loaded by a prior test.
        sys.modules.pop("ctypes.wintypes", None)

        # Re-import crash_handler.
        import voice_typer.server.crash_handler  # noqa: F401

        # Assert ctypes.wintypes was NOT loaded.
        assert "ctypes.wintypes" not in sys.modules, (
            "importing voice_typer.server.crash_handler on Linux loaded "
            "ctypes.wintypes, the per-platform guard is broken. Win32 ctypes "
            "must be guarded by ``if sys.platform == 'win32':`` at module-load "
            "time inside _win32_structs.py / _veh_kernel32.py / _veh_callback.py."
        )
        # Restore the ORIGINAL module identities so pre-existing
        for k, mod in saved_modules.items():
            sys.modules[k] = mod
        # Restore the parent-package attribute as well. The re-import
        import voice_typer.server as _server_pkg

        for k in mods_to_remove:
            if k == "voice_typer.server.crash_handler":
                _server_pkg.crash_handler = saved_modules[k]
        assert _server_pkg.crash_handler is sys.modules["voice_typer.server.crash_handler"], (
            "purge/restore must leave the package attribute consistent with sys.modules"
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows-specific guard test",
    )
    def test_submodule_imports_are_cheap_on_linux(self):
        """Each submodule can be imported independently on Linux without"""
        submodules = [
            "voice_typer.server.crash_handler._constants",
            "voice_typer.server.crash_handler._win32_structs",
            "voice_typer.server.crash_handler._veh_kernel32",
            "voice_typer.server.crash_handler._veh_callback",
            "voice_typer.server.crash_handler._diagnostics_archive",
            "voice_typer.server.crash_handler._python_excepthook",
        ]
        # Snapshot the ORIGINAL submodule objects (sys.modules entries +
        from voice_typer.server import crash_handler as _facade_mod

        _orig_submods = {m: sys.modules.get(m) for m in submodules}
        _orig_facade_attrs = {
            m.rsplit(".", 1)[-1]: getattr(_facade_mod, m.rsplit(".", 1)[-1], None) for m in submodules
        }
        try:
            for mod_name in submodules:
                # Remove if already imported.
                sys.modules.pop(mod_name, None)
                sys.modules.pop("ctypes.wintypes", None)
                __import__(mod_name)
                assert "ctypes.wintypes" not in sys.modules, (
                    f"Importing {mod_name} on Linux loaded ctypes.wintypes, "
                    "per-platform guard broken in this submodule."
                )
        finally:
            # Undo the re-imports completely: restore the original
            for m, mod in _orig_submods.items():
                if mod is None:
                    sys.modules.pop(m, None)
                else:
                    sys.modules[m] = mod
            for attr, mod in _orig_facade_attrs.items():
                if mod is None:
                    if hasattr(_facade_mod, attr):
                        delattr(_facade_mod, attr)
                else:
                    setattr(_facade_mod, attr, mod)


class TestStateProxying:
    """the submodule functions that read/write the same state."""

    def test_set_crash_handler_config_dir_writes_visible_on_facade(self, tmp_path):
        """``_diagnostics_archive``) writes to the facade's state vars —"""
        from voice_typer.server import crash_handler

        saved = {
            "_crash_file_path": crash_handler._crash_file_path,
            "_PID": crash_handler._PID,
            "_python_crash_dir": crash_handler._python_crash_dir,
            "_crash_written": crash_handler._crash_written,
            "_crash_header_bytes": crash_handler._crash_header_bytes,
        }
        try:
            crash_handler.set_crash_handler_config_dir(tmp_path)
            assert crash_handler._crash_file_path != ""
            assert "crash_diagnostics" in crash_handler._crash_file_path
            import os

            assert str(os.getpid()) in crash_handler._crash_file_path
            assert os.getpid() == crash_handler._PID
            assert crash_handler._python_crash_dir == tmp_path.resolve()
            assert crash_handler._crash_written is False
            assert crash_handler._crash_header_bytes  # non-empty
        finally:
            for k, v in saved.items():
                setattr(crash_handler, k, v)

    def test_facade_reset_visible_to_submodule_function(self, tmp_path):
        """When a test resets ``crash_handler._crash_written = True``,"""
        from voice_typer.server import crash_handler

        saved = {
            "_crash_file_path": crash_handler._crash_file_path,
            "_PID": crash_handler._PID,
            "_python_crash_dir": crash_handler._python_crash_dir,
            "_crash_written": crash_handler._crash_written,
            "_crash_header_bytes": crash_handler._crash_header_bytes,
        }
        try:
            # Simulate a test that resets state, _crash_written = True.
            crash_handler._crash_written = True
            crash_handler.set_crash_handler_config_dir(tmp_path)
            # The function must have observed _crash_written=True (via
            assert crash_handler._crash_written is False, (
                "set_crash_handler_config_dir did not observe the facade-level "
                "_crash_written=True reset, state proxying is broken."
            )
        finally:
            for k, v in saved.items():
                setattr(crash_handler, k, v)

    def test_install_crash_handler_reads_facade_handler_handle(self, monkeypatch):
        """``install_crash_handler`` (in _python_excepthook) reads"""
        from voice_typer.server import crash_handler

        # Force the non-Windows short-circuit so the test is
        monkeypatch.setattr(sys, "platform", "linux")

        saved = crash_handler._handler_handle
        try:
            crash_handler._handler_handle = None
            result = crash_handler.install_crash_handler()
            assert result is False, (
                "install_crash_handler returned True on a non-Windows "
                "platform, either the platform guard is broken or "
                "_handler_handle was not read from the facade."
            )
        finally:
            crash_handler._handler_handle = saved


class TestPackageStructure:
    """The crash_handler package must have the 6 submodules specified in"""

    EXPECTED_SUBMODULES = [
        "_constants",
        "_win32_structs",
        "_veh_kernel32",
        "_veh_callback",
        "_diagnostics_archive",
        "_python_excepthook",
    ]

    def test_all_submodules_exist(self):
        import voice_typer.server.crash_handler as ch

        # __path__ is set on packages (not modules).
        assert hasattr(ch, "__path__"), (
            "crash_handler should be a package (directory with __init__.py), not a single .py module"
        )
        pkg_dir = Path(ch.__file__).parent
        for sub in self.EXPECTED_SUBMODULES:
            assert (pkg_dir / f"{sub}.py").exists(), f"Submodule {sub}.py is missing from the crash_handler package"

    def test_facade_is_not_the_old_monolith(self):
        """The facade __init__.py must be a thin re-export layer (~100 LOC),"""
        import voice_typer.server.crash_handler as ch

        init_path = Path(ch.__file__)
        loc = len(init_path.read_text().splitlines())
        # The facade holds mutable state + re-exports, allow up to ~250
        assert loc < 300, (
            f"crash_handler/__init__.py is {loc} LOC, expected a thin facade "
            f"(<300 LOC). The original monolith was 1255 LOC; the facade must "
            f"only hold mutable state + re-exports."
        )

    def test_old_monolith_removed(self):
        """The old ``crash_handler.py`` file must be removed (replaced by"""
        import voice_typer.server.crash_handler as ch

        pkg_dir = Path(ch.__file__).parent
        old_file = pkg_dir.parent / "crash_handler.py"
        assert not old_file.exists(), (
            f"{old_file} still exists, the old monolith must be removed after the split to avoid shadowing the package."
        )


class TestFunctionalSmoke:
    """Quick functional smoke test on the Linux-runnable surface to"""

    def test_write_u32_hex_writes_8_hex_digits(self):
        """``_write_u32_hex`` writes exactly 8 hex digits (no 0x prefix)."""
        from voice_typer.server import crash_handler

        buf = bytearray(8)
        n = crash_handler._write_u32_hex(0xDEADBEEF, buf, 0)
        assert n == 8
        assert bytes(buf) == b"DEADBEEF"

    def test_write_u64_hex_writes_16_hex_digits(self):
        """``_write_u64_hex`` writes exactly 16 hex digits."""
        from voice_typer.server import crash_handler

        buf = bytearray(16)
        n = crash_handler._write_u64_hex(0x123456789ABCDEF0, buf, 0)
        assert n == 16
        assert bytes(buf) == b"123456789ABCDEF0"

    def test_format_redacted_traceback_none_returns_empty(self):
        from voice_typer.server import crash_handler

        assert crash_handler._format_redacted_traceback(None) == ""

    def test_compute_crash_header_returns_bytes(self):
        from voice_typer.server import crash_handler

        header = crash_handler._compute_crash_header()
        assert isinstance(header, bytes)
        assert b"VOICE-TYPER CRASH DIAGNOSTICS HEADER" in header
        assert b"END HEADER" in header

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows-specific, _vectored_handler is set on Windows",
    )
    def test_vectored_handler_is_none_on_linux(self):
        from voice_typer.server import crash_handler

        assert crash_handler._vectored_handler is None, (
            "_vectored_handler should be None on non-Windows (no WINFUNCTYPE wrapping)"
        )

    def test_vectored_handler_impl_returns_continue_search_on_none(self):
        """``_vectored_handler_impl(None)`` returns EXCEPTION_CONTINUE_SEARCH"""
        from voice_typer.server import crash_handler

        result = crash_handler._vectored_handler_impl(None)
        assert result == crash_handler.EXCEPTION_CONTINUE_SEARCH

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows-specific",
    )
    def test_install_crash_handler_returns_false_on_linux(self):
        from voice_typer.server import crash_handler

        assert crash_handler.install_crash_handler() is False

    def test_remove_crash_handler_is_idempotent(self):
        from voice_typer.server import crash_handler

        # Must not raise even when no handler is installed.
        crash_handler.remove_crash_handler()
        crash_handler.remove_crash_handler()

    def test_crash_msg_buf_layout_matches_design(self):
        """The ``_CRASH_MSG_LAYOUT`` labels must match the GT-B2-14 design."""
        from voice_typer.server import crash_handler

        labels = {label for label, _ in crash_handler._CRASH_MSG_LAYOUT}
        expected = {
            "bom",
            "timestamp",
            "crash_label",
            "code",
            "addr",
            "pid",
            "tid",
            "nl1",
            "name",
            "nl2",
        }
        assert labels == expected, f"_CRASH_MSG_LAYOUT labels diverged from design: {labels ^ expected}"

    def test_crash_msg_buf_size_exceeds_layout_sum(self):
        """``_CRASH_MSG_BUF_SIZE`` must exceed the layout sum (headroom)."""
        from voice_typer.server import crash_handler

        layout_sum = sum(w for _, w in crash_handler._CRASH_MSG_LAYOUT)
        assert layout_sum < crash_handler._CRASH_MSG_BUF_SIZE
        assert len(crash_handler._crash_msg_buf) >= layout_sum
