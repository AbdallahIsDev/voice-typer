"""TY-39: tests for the ``crash_handler`` package split.

The original 1255-LOC ``crash_handler.py`` was split into a package
with 6 submodules + a facade ``__init__.py`` (AC-86 / TY-39). These
tests verify:

1. **Backward compatibility** — every name that tests previously
   imported from ``voice_typer.server.crash_handler`` is still
   accessible on the facade. This includes:
     - Public functions: ``set_crash_handler_config_dir``,
       ``report_pending_crash``, ``install_crash_handler``,
       ``remove_crash_handler``, ``install_python_excepthook``.
     - Private functions referenced by tests:
       ``_vectored_handler_impl``, ``_crash_excepthook``,
       ``_compute_crash_header``, ``_write_u32_hex``,
       ``_write_u64_hex``, ``_format_redacted_traceback``,
       ``_sweep_stale_diagnostics``.
     - Constants: ``STATUS_HEAP_CORRUPTION``,
       ``STATUS_ACCESS_VIOLATION``, ``STATUS_STACK_BUFFER_OVERRUN``,
       ``STATUS_FATAL_APP_EXIT``, ``_CRASH_CODES``,
       ``EXCEPTION_CONTINUE_SEARCH``.
     - Module-level mutable state (read + writable):
       ``_crash_file_path``, ``_PID``, ``_handler_handle``,
       ``_kernel32``, ``_crash_written``, ``_python_crash_dir``,
       ``_crash_header_bytes``, ``_original_excepthook``.
     - Read-only references: ``_crash_msg_buf``,
       ``_CRASH_MSG_LAYOUT``, ``_CRASH_MSG_BUF_SIZE``,
       ``_vectored_handler``.

2. **Per-platform guard** — on Linux, importing
   ``voice_typer.server.crash_handler`` does NOT load
   ``ctypes.wintypes`` (which would raise ``AttributeError`` on
   non-Windows). The Win32 ctypes guard stays at module-load time
   inside ``_win32_structs.py`` / ``_veh_kernel32.py`` /
   ``_veh_callback.py``.

3. **State proxying** — test mutations on
   ``crash_handler._kernel32 = None`` (etc.) are observable by the
   submodule functions that read/write the same state. This is the
   key invariant that lets the existing test suite (which resets
   module-level globals between tests) work without modification.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── 1. Backward compatibility: all previously-importable names ───────


# Names that ``tests/test_crash_handler.py`` reads or writes on the
# ``crash_handler`` module. Compiled from:
#   grep -rn "crash_handler\." tests/test_crash_handler.py
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
                f"crash_handler.{name} is missing — public function not re-exported by facade"
            )
            assert callable(getattr(crash_handler, name)), (
                f"crash_handler.{name} is not callable"
            )

    def test_private_functions_importable(self):
        from voice_typer.server import crash_handler

        for name in PRIVATE_FUNCTIONS:
            assert hasattr(crash_handler, name), (
                f"crash_handler.{name} is missing — private function not re-exported by facade"
            )
            assert callable(getattr(crash_handler, name)), (
                f"crash_handler.{name} is not callable"
            )

    def test_constants_importable(self):
        from voice_typer.server import crash_handler

        for name in CONSTANTS:
            assert hasattr(crash_handler, name), (
                f"crash_handler.{name} is missing — constant not re-exported by facade"
            )

    def test_constants_have_correct_values(self):
        """The status codes must match the original values exactly."""
        from voice_typer.server import crash_handler

        assert crash_handler.STATUS_HEAP_CORRUPTION == 0xC0000374
        assert crash_handler.STATUS_ACCESS_VIOLATION == 0xC0000005
        assert crash_handler.STATUS_STACK_BUFFER_OVERRUN == 0xC0000409
        assert crash_handler.STATUS_FATAL_APP_EXIT == 0x40000015
        assert crash_handler.EXCEPTION_CONTINUE_SEARCH == 0x0
        assert frozenset(
            {
                crash_handler.STATUS_HEAP_CORRUPTION,
                crash_handler.STATUS_ACCESS_VIOLATION,
                crash_handler.STATUS_STACK_BUFFER_OVERRUN,
                crash_handler.STATUS_FATAL_APP_EXIT,
            }
        ) == crash_handler._CRASH_CODES

    def test_structs_importable(self):
        from voice_typer.server import crash_handler

        for name in STRUCTS:
            assert hasattr(crash_handler, name), (
                f"crash_handler.{name} is missing — struct not re-exported by facade"
            )

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
                f"crash_handler.{name} is missing — read-only ref not re-exported by facade"
            )


# ── 2. Per-platform guard: Linux import must not load ctypes.wintypes ──


class TestPerPlatformGuard:
    """On Linux, importing crash_handler must NOT load ``ctypes.wintypes``.

    ``ctypes.wintypes`` only exists on Windows. Referencing it on Linux
    raises ``AttributeError`` at module-load time. The per-platform guard
    (``if sys.platform == "win32":``) must stay at module-load time
    inside the submodules so Linux imports remain cheap and error-free.
    """

    def test_import_does_not_load_wintypes(self):
        """On non-Windows, ``ctypes.wintypes`` must NOT be in
        ``sys.modules`` after importing crash_handler."""
        if sys.platform == "win32":
            pytest.skip("Windows-specific guard test — wintypes IS loaded on Windows")

        # Remove crash_handler and ctypes.wintypes from sys.modules so
        # we can re-import fresh and check what gets loaded.
        mods_to_remove = [
            k
            for k in list(sys.modules)
            if k == "voice_typer.server.crash_handler"
            or k.startswith("voice_typer.server.crash_handler.")
        ]
        for k in mods_to_remove:
            del sys.modules[k]
        # Also remove ctypes.wintypes if it was loaded by a prior test.
        sys.modules.pop("ctypes.wintypes", None)

        # Re-import crash_handler.
        import voice_typer.server.crash_handler  # noqa: F401

        # Assert ctypes.wintypes was NOT loaded.
        assert "ctypes.wintypes" not in sys.modules, (
            "TY-39: importing voice_typer.server.crash_handler on Linux loaded "
            "ctypes.wintypes — the per-platform guard is broken. Win32 ctypes "
            "must be guarded by ``if sys.platform == 'win32':`` at module-load "
            "time inside _win32_structs.py / _veh_kernel32.py / _veh_callback.py."
        )

    def test_submodule_imports_are_cheap_on_linux(self):
        """Each submodule can be imported independently on Linux without
        triggering wintypes."""
        if sys.platform == "win32":
            pytest.skip("Windows-specific guard test")

        submodules = [
            "voice_typer.server.crash_handler._constants",
            "voice_typer.server.crash_handler._win32_structs",
            "voice_typer.server.crash_handler._veh_kernel32",
            "voice_typer.server.crash_handler._veh_callback",
            "voice_typer.server.crash_handler._diagnostics_archive",
            "voice_typer.server.crash_handler._python_excepthook",
        ]
        for mod_name in submodules:
            # Remove if already imported.
            sys.modules.pop(mod_name, None)
            sys.modules.pop("ctypes.wintypes", None)
            __import__(mod_name)
            assert "ctypes.wintypes" not in sys.modules, (
                f"Importing {mod_name} on Linux loaded ctypes.wintypes — "
                f"per-platform guard broken in this submodule."
            )


# ── 3. State proxying: test mutations propagate to submodule functions ──


class TestStateProxying:
    """Test mutations on ``crash_handler.<state>`` must be observable by
    the submodule functions that read/write the same state.

    This is the key invariant that lets the existing test suite (which
    resets module-level globals between tests via
    ``crash_handler._crash_file_path = ""`` etc.) work without
    modification after the split.
    """

    def test_set_crash_handler_config_dir_writes_visible_on_facade(self, tmp_path):
        """``set_crash_handler_config_dir`` (defined in
        ``_diagnostics_archive``) writes to the facade's state vars —
        reads on ``crash_handler.<name>`` must see the new values."""
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
            # The function (in _diagnostics_archive) wrote to
            # _ch._crash_file_path etc. — reads on the facade must see
            # the new values.
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
        """When a test resets ``crash_handler._crash_written = True``,
        the next ``set_crash_handler_config_dir`` call must see the
        reset and clear it back to False."""
        from voice_typer.server import crash_handler

        saved = {
            "_crash_file_path": crash_handler._crash_file_path,
            "_PID": crash_handler._PID,
            "_python_crash_dir": crash_handler._python_crash_dir,
            "_crash_written": crash_handler._crash_written,
            "_crash_header_bytes": crash_handler._crash_header_bytes,
        }
        try:
            # Simulate a test that resets state — _crash_written = True.
            crash_handler._crash_written = True
            crash_handler.set_crash_handler_config_dir(tmp_path)
            # The function must have observed _crash_written=True (via
            # _ch._crash_written) and reset it to False.
            assert crash_handler._crash_written is False, (
                "set_crash_handler_config_dir did not observe the facade-level "
                "_crash_written=True reset — state proxying is broken."
            )
        finally:
            for k, v in saved.items():
                setattr(crash_handler, k, v)

    def test_install_crash_handler_reads_facade_handler_handle(self):
        """``install_crash_handler`` (in _python_excepthook) reads
        ``_ch._handler_handle`` — a test that sets
        ``crash_handler._handler_handle = None`` must be observed."""
        from voice_typer.server import crash_handler

        saved = crash_handler._handler_handle
        try:
            crash_handler._handler_handle = None
            # On Linux, install_crash_handler short-circuits on
            # sys.platform != "win32" — but it still reads
            # _ch._handler_handle first (the ``if _ch._handler_handle
            # is not None: return True`` check). Verify the function
            # returns False on Linux (not True, which would mean it
            # didn't see the None reset).
            result = crash_handler.install_crash_handler()
            assert result is False, (
                "install_crash_handler returned True on Linux — either the "
                "platform guard is broken or _handler_handle was not read "
                "from the facade."
            )
        finally:
            crash_handler._handler_handle = saved


# ── 4. Package structure ─────────────────────────────────────────────


class TestPackageStructure:
    """The crash_handler package must have the 6 submodules specified in
    the TY-39 split plan."""

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
            "crash_handler should be a package (directory with __init__.py), "
            "not a single .py module"
        )
        pkg_dir = Path(ch.__file__).parent
        for sub in self.EXPECTED_SUBMODULES:
            assert (pkg_dir / f"{sub}.py").exists(), (
                f"Submodule {sub}.py is missing from the crash_handler package"
            )

    def test_facade_is_not_the_old_monolith(self):
        """The facade __init__.py must be a thin re-export layer (~100 LOC),
        not the original 1255-LOC monolith."""
        import voice_typer.server.crash_handler as ch

        init_path = Path(ch.__file__)
        loc = len(init_path.read_text().splitlines())
        # The facade holds mutable state + re-exports — allow up to ~250
        # LOC for the state declarations + docstrings + re-export
        # imports. The original was 1255 LOC; the facade must be
        # substantially smaller.
        assert loc < 300, (
            f"crash_handler/__init__.py is {loc} LOC — expected a thin facade "
            f"(<300 LOC). The original monolith was 1255 LOC; the facade must "
            f"only hold mutable state + re-exports."
        )

    def test_old_monolith_removed(self):
        """The old ``crash_handler.py`` file must be removed (replaced by
        the package)."""
        import voice_typer.server.crash_handler as ch

        pkg_dir = Path(ch.__file__).parent
        old_file = pkg_dir.parent / "crash_handler.py"
        assert not old_file.exists(), (
            f"{old_file} still exists — the old monolith must be removed "
            f"after the split to avoid shadowing the package."
        )


# ── 5. Functional smoke test (Linux-runnable surface) ────────────────


class TestFunctionalSmoke:
    """Quick functional smoke test on the Linux-runnable surface to
    verify the split didn't break basic behavior."""

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

    def test_vectored_handler_is_none_on_linux(self):
        if sys.platform == "win32":
            pytest.skip("Windows-specific — _vectored_handler is set on Windows")
        from voice_typer.server import crash_handler

        assert crash_handler._vectored_handler is None, (
            "_vectored_handler should be None on non-Windows (no WINFUNCTYPE wrapping)"
        )

    def test_vectored_handler_impl_returns_continue_search_on_none(self):
        """``_vectored_handler_impl(None)`` returns EXCEPTION_CONTINUE_SEARCH
        on Linux (the ``exception_pointers.contents`` access raises)."""
        from voice_typer.server import crash_handler

        result = crash_handler._vectored_handler_impl(None)
        assert result == crash_handler.EXCEPTION_CONTINUE_SEARCH

    def test_install_crash_handler_returns_false_on_linux(self):
        if sys.platform == "win32":
            pytest.skip("Windows-specific")
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
        assert labels == expected, (
            f"_CRASH_MSG_LAYOUT labels diverged from design: {labels ^ expected}"
        )

    def test_crash_msg_buf_size_exceeds_layout_sum(self):
        """``_CRASH_MSG_BUF_SIZE`` must exceed the layout sum (headroom)."""
        from voice_typer.server import crash_handler

        layout_sum = sum(w for _, w in crash_handler._CRASH_MSG_LAYOUT)
        assert layout_sum < crash_handler._CRASH_MSG_BUF_SIZE
        assert len(crash_handler._crash_msg_buf) >= layout_sum
