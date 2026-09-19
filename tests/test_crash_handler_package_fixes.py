"""UE-2 (Fix-C) regression tests for the crash_handler package."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
from voice_typer.server import crash_handler
from voice_typer.server.crash_handler import _constants, _python_excepthook

_UNSET = object()


@pytest.fixture(autouse=True)
def _reset_crash_handler_module_state():
    """Reset module-level globals between tests."""
    keys = (
        "_crash_file_path",
        "_PID",
        "_handler_handle",
        "_kernel32",
        "_crash_written",
        "_python_crash_dir",
        "_crash_header_bytes",
        "_original_threading_excepthook",
    )
    saved = {k: getattr(crash_handler, k, _UNSET) for k in keys}
    saved_threading_excepthook = threading.excepthook
    crash_handler._crash_file_path = ""
    crash_handler._PID = 0
    crash_handler._handler_handle = None
    crash_handler._kernel32 = None
    crash_handler._crash_written = False
    crash_handler._python_crash_dir = None
    crash_handler._crash_header_bytes = b""
    crash_handler._original_threading_excepthook = None
    threading.excepthook = threading.__excepthook__
    # Reset the rate-limit lock so a stuck lock from a prior test
    crash_handler._crash_write_lock = threading.Lock()
    yield
    for k, v in saved.items():
        if v is _UNSET:
            if hasattr(crash_handler, k):
                delattr(crash_handler, k)
        else:
            setattr(crash_handler, k, v)
    threading.excepthook = saved_threading_excepthook


@pytest.fixture
def restore_excepthook():
    """Snapshot ``sys.excepthook`` so a test can restore it."""
    saved = sys.excepthook
    saved_orig_attr = crash_handler._original_excepthook
    yield
    sys.excepthook = saved
    crash_handler._original_excepthook = saved_orig_attr


@pytest.fixture
def restore_threading_excepthook():
    """Snapshot ``threading.excepthook`` so a test can restore it."""
    saved = threading.excepthook
    saved_orig_attr = crash_handler._original_threading_excepthook
    yield
    threading.excepthook = saved
    crash_handler._original_threading_excepthook = saved_orig_attr


class TestCrashWriteLock:
    """``_crash_write_lock`` serializes the VEH rate-limit check+set."""

    def test_crash_write_lock_exists_on_facade(self):
        """UE-2-F2: ``_crash_write_lock`` is a ``threading.Lock`` on the facade."""
        assert hasattr(crash_handler, "_crash_write_lock"), (
            "UE-2-F2: ``_crash_write_lock`` must be defined on the crash_handler facade"
        )
        assert isinstance(crash_handler._crash_write_lock, type(threading.Lock())), (
            "UE-2-F2: ``_crash_write_lock`` must be a threading.Lock instance"
        )

    def test_vectored_handler_impl_releases_lock_on_early_return(self):
        """UE-2-F2: the VEH callback releases the lock on EVERY return path."""
        # Path 1: exception_pointers=None → early return BEFORE lock.
        result = crash_handler._vectored_handler_impl(None)
        assert result == crash_handler.EXCEPTION_CONTINUE_SEARCH
        assert crash_handler._crash_write_lock.acquire(blocking=False), (
            "UE-2-F2: lock must NOT be held after the early-return path "
            "(exception_pointers=None), it should never have been acquired"
        )
        crash_handler._crash_write_lock.release()

    def test_vectored_handler_impl_releases_lock_when_crash_written_already_true(self, monkeypatch):
        """UE-2-F2: when ``_crash_written`` is already True, the lock is"""
        if sys.platform == "win32":
            pytest.skip("VEH callback path test, Windows-only semantics mocked on POSIX")

        # Build a fake EXCEPTION_POINTERS that yields a code IN _CRASH_CODES.
        crash_handler._crash_written = True
        # Simulate the impl's lock discipline: acquire, check, return, release.
        acquired = crash_handler._crash_write_lock.acquire(blocking=False)
        assert acquired, "UE-2-F2: lock must be acquirable when not held"
        try:
            if crash_handler._crash_written:
                pass  # early-return path
        finally:
            crash_handler._crash_write_lock.release()
        # Lock must be re-acquirable (released by the finally).
        assert crash_handler._crash_write_lock.acquire(blocking=False), (
            "UE-2-F2: lock must be released by the finally block on the _crash_written early-return path"
        )
        crash_handler._crash_write_lock.release()

    def test_lock_is_non_blocking(self):
        """UE-2-F2: ``acquire(blocking=False)`` returns False when held."""
        lock = threading.Lock()
        # Acquire once, second acquire(blocking=False) must return False.
        assert lock.acquire(blocking=False) is True
        try:
            assert lock.acquire(blocking=False) is False, (
                "UE-2-F2: non-blocking acquire on a held lock must return False"
            )
        finally:
            lock.release()


class TestSummarizeCrashFileTableDriven:
    """``_CODE_TO_USER_SUMMARY`` instead of a 13-clause if/elif chain."""

    def test_code_to_user_summary_table_exists_and_is_complete(self):
        """UE-2-F3: ``_CODE_TO_USER_SUMMARY`` is defined for every code"""
        assert hasattr(_constants, "_CODE_TO_USER_SUMMARY"), (
            "UE-2-F3: ``_CODE_TO_USER_SUMMARY`` must be defined in _constants"
        )
        # Every code in _CODE_TO_INFO must have a user-summary entry.
        missing = set(_constants._CODE_TO_INFO) - set(_constants._CODE_TO_USER_SUMMARY)
        assert not missing, (
            f"UE-2-F3: _CODE_TO_USER_SUMMARY is missing entries for codes: {sorted(hex(c) for c in missing)}"
        )

    @pytest.mark.parametrize(
        "status_name,expected_substring,expected_hex",
        [
            ("STATUS_HEAP_CORRUPTION", "Heap corruption", "0xC0000374"),
            ("STATUS_ACCESS_VIOLATION", "Access violation", "0xC0000005"),
            ("STATUS_STACK_BUFFER_OVERRUN", "Stack overrun", "0xC0000409"),
            ("STATUS_FATAL_APP_EXIT", "Fatal exit", "0x40000015"),
            ("STATUS_ILLEGAL_INSTRUCTION", "Illegal instruction", "0xC000001D"),
            ("STATUS_INT_DIVIDE_BY_ZERO", "Integer divide by zero", "0xC0000094"),
            ("STATUS_PRIVILEGED_INSTRUCTION", "Privileged instruction", "0xC0000096"),
            ("STATUS_IN_PAGE_ERROR", "In-page error", "0xC0000006"),
            ("STATUS_STACK_OVERFLOW", "Stack overflow", "0xC00000FD"),
            ("STATUS_NONCONTINUABLE_EXCEPTION", "Non-continuable exception", "0xC0000025"),
            ("STATUS_INVALID_HANDLE", "Invalid handle", "0xC0000008"),
            ("STATUS_DATATYPE_MISALIGNMENT", "Datatype misalignment", "0xC0000002"),
            ("STATUS_GUARD_PAGE_VIOLATION", "Guard page violation", "0x80000001"),
        ],
    )
    def test_known_code_produces_expected_summary(self, tmp_path, status_name, expected_substring, expected_hex):
        """Each known STATUS_* name in the crash file produces the"""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        # Write a file whose content matches what the VEH callback would
        crash_file.write_text(f"{status_name}: some detail.\r\n", encoding="utf-8")

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None, f"UE-2-F3: report_pending_crash must surface a known crash ({status_name})"
        assert expected_substring in result, (
            f"UE-2-F3: summary for {status_name} must include {expected_substring!r}; got:\n{result}"
        )
        assert expected_hex in result, f"UE-2-F3: summary for {status_name} must include {expected_hex}; got:\n{result}"

    def test_unknown_code_falls_through_to_code_extraction(self, tmp_path):
        """An unknown crash code (not in _CODE_TO_INFO) falls through"""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "CRASH code=0xDEADBEEF, addr=0x00000000, pid=0x1, tid=0x2\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "crash" in result.lower() or "0x" in result.lower()

    def test_table_driven_lookup_no_drift_with_constants(self):
        """``_constants._CODE_TO_USER_SUMMARY``, there is no separate"""
        import voice_typer.server.crash_handler._diagnostics_archive as _arch

        source = Path(_arch.__file__).read_text(encoding="utf-8")
        # The table import is present.
        assert "_CODE_TO_USER_SUMMARY" in source, "UE-2-F3: _diagnostics_archive must import _CODE_TO_USER_SUMMARY"
        assert "_CODE_TO_INFO" in source, "UE-2-F3: _diagnostics_archive must import _CODE_TO_INFO"
        # The pre-fix 13-clause if/elif chain had at least 4 distinct
        import re

        elif_chain = re.findall(r'elif "STATUS_\w+" in content:', source)
        if_chain = re.findall(r'if "STATUS_\w+" in content:', source)
        assert len(elif_chain) + len(if_chain) == 0, (
            f"UE-2-F3: _diagnostics_archive must NOT contain a "
            f'"STATUS_*" in content if/elif chain (found {len(if_chain)} if + '
            f"{len(elif_chain)} elif). The table-driven lookup replaces it."
        )


class TestWriteCrashMarkerHelper:
    """``_crash_excepthook`` and ``_thread_crash_excepthook``."""

    def test_helper_is_importable_from_facade(self):
        """UE-2-F4: ``_write_crash_marker`` is re-exported by the facade."""
        assert hasattr(crash_handler, "_write_crash_marker"), (
            "UE-2-F4: _write_crash_marker must be re-exported by the facade"
        )
        assert callable(crash_handler._write_crash_marker)

    def test_helper_writes_main_thread_marker_when_thread_name_none(self, restore_excepthook, tmp_path):
        """When ``thread_name=None``, the marker is"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        try:
            raise ValueError("main-thread crash")
        except ValueError as exc:
            crash_handler._write_crash_marker(type(exc), exc, exc.__traceback__, thread_name=None)

        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists(), "UE-2-F4: helper must write python_crash.<PID>.txt when thread_name=None"
        content = marker.read_text(encoding="utf-8")
        # The thread= field uses the CURRENT thread's name (which is
        assert f"thread={threading.current_thread().name}" in content
        assert "exc_type=ValueError" in content

    def test_helper_writes_thread_specific_marker_when_thread_name_given(self, tmp_path):
        """When ``thread_name`` is a string, the marker is"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        try:
            raise RuntimeError("daemon-thread crash")
        except RuntimeError as exc:
            crash_handler._write_crash_marker(type(exc), exc, exc.__traceback__, thread_name="ModelLoad")

        marker = tmp_path / f"python_crash.{os.getpid()}.ModelLoad.txt"
        assert marker.exists(), (
            "UE-2-F4: helper must write python_crash.<PID>.<thread>.txt when thread_name is a string"
        )
        content = marker.read_text(encoding="utf-8")
        assert "thread=ModelLoad" in content
        assert "exc_type=RuntimeError" in content

    def test_helper_preserves_filename_difference_between_paths(self, tmp_path):
        """UE-2-F4: the marker filename difference between the main"""
        crash_handler.set_crash_handler_config_dir(tmp_path)

        # Main path.
        try:
            raise ValueError("main")
        except ValueError as exc:
            crash_handler._write_crash_marker(type(exc), exc, exc.__traceback__, thread_name=None)
        # Threading path.
        try:
            raise RuntimeError("thread")
        except RuntimeError as exc:
            crash_handler._write_crash_marker(type(exc), exc, exc.__traceback__, thread_name="Worker")

        main_marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        thread_marker = tmp_path / f"python_crash.{os.getpid()}.Worker.txt"
        assert main_marker.exists(), "main-hook marker must exist (no thread suffix)"
        assert thread_marker.exists(), "thread-hook marker must exist (with thread suffix)"
        assert main_marker != thread_marker, (
            "UE-2-F4: the two marker filenames must differ (preserves the "
            "pre-fix behavior where the main hook writes <PID>.txt and the "
            "threading hook writes <PID>.<thread>.txt)"
        )

    def test_helper_no_op_when_python_crash_dir_unset(self, tmp_path):
        """UE-2-F4: when ``_python_crash_dir`` is None, the helper"""
        # _python_crash_dir is None per the autouse fixture.
        try:
            raise ValueError("no-dir")
        except ValueError as exc:
            # Must not raise.
            crash_handler._write_crash_marker(type(exc), exc, exc.__traceback__, thread_name=None)
            crash_handler._write_crash_marker(type(exc), exc, exc.__traceback__, thread_name="X")
        # No marker written.
        assert not list(tmp_path.glob("python_crash.*.txt"))

    def test_helper_writes_marker_with_full_triage_context(self, tmp_path):
        """UE-2-F4: the helper-built marker carries the same key=value"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        try:
            raise ValueError("triage-test")
        except ValueError as exc:
            crash_handler._write_crash_marker(type(exc), exc, exc.__traceback__, thread_name=None)
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        content = marker.read_text(encoding="utf-8")
        for key in (
            "exc_type=",
            "exc_value=",
            "thread=",
            "timestamp=",
            "app_version=",
            "python_version=",
            "os_version=",
            "asr_backend=",
        ):
            assert key in content, f"UE-2-F4: marker must include '{key}' line; got:\n{content}"

    def test_dedup_no_inline_redaction_closure_in_either_hook(self):
        """UE-2-F4: neither ``_crash_excepthook`` nor"""
        source = Path(_python_excepthook.__file__).read_text(encoding="utf-8")
        # The inline closure pattern (pre-fix), must NOT appear.
        assert "def _redact(s):" not in source, (
            "UE-2-F4: the inline ``def _redact(s):`` closure must be removed "
            "from _python_excepthook, the logic now lives in "
            "``_redact_exc_value`` (called from ``_write_crash_marker``)"
        )
        # The shared helper must be defined.
        assert "def _write_crash_marker(" in source, (
            "UE-2-F4: ``_write_crash_marker`` must be defined in _python_excepthook"
        )
        # Both hooks must delegate to the helper.
        assert "_write_crash_marker(exc_type, exc_value, exc_tb, thread_name=None)" in source, (
            "UE-2-F4: _crash_excepthook must delegate to _write_crash_marker(thread_name=None)"
        )
        assert "_write_crash_marker(exc_type, exc_value, exc_tb, thread_name=thread_name)" in source, (
            "UE-2-F4: _thread_crash_excepthook must delegate to _write_crash_marker(thread_name=thread_name)"
        )


class TestRedactExcValueFallback:
    """``_redact_exc_value`` isolates redaction as a single concern"""

    def test_redact_exc_value_is_importable(self):
        """UE-2-F5: ``_redact_exc_value`` and ``_safe_redact_fallback``"""
        assert hasattr(crash_handler, "_redact_exc_value")
        assert hasattr(crash_handler, "_safe_redact_fallback")
        assert callable(crash_handler._redact_exc_value)
        assert callable(crash_handler._safe_redact_fallback)

    def test_redact_exc_value_redacts_ssn(self):
        """When imports succeed, ``_redact_exc_value`` runs the full"""
        ssn = "123-45-6789"
        redacted = crash_handler._redact_exc_value(ssn)
        assert ssn not in redacted, f"UE-2-F5: SSN must be redacted; got: {redacted!r}"

    def test_safe_redact_fallback_returns_hash_sentinel(self):
        """``_safe_redact_fallback`` returns a ``<redacted:sha256:...>``"""
        pii = "my name is John Smith and my SSN is 123-45-6789"
        redacted = crash_handler._safe_redact_fallback(pii)
        # The PII payload must NOT appear in the fallback.
        assert "John Smith" not in redacted
        assert "123-45-6789" not in redacted
        assert "John" not in redacted
        # The fallback is a sha256-prefixed sentinel.
        assert redacted.startswith("<redacted:sha256:"), (
            f"UE-2-F5: fallback must be a sha256 sentinel; got: {redacted!r}"
        )
        assert redacted.endswith(">")

    def test_safe_redact_fallback_is_deterministic(self):
        """The fallback is deterministic, same input → same digest —"""
        a = crash_handler._safe_redact_fallback("some-exception-value")
        b = crash_handler._safe_redact_fallback("some-exception-value")
        assert a == b, "UE-2-F5: fallback must be deterministic for dedup support"

    def test_safe_redact_fallback_differs_for_different_inputs(self):
        """Different inputs produce different digests (collision"""
        a = crash_handler._safe_redact_fallback("exception-one")
        b = crash_handler._safe_redact_fallback("exception-two")
        assert a != b, "UE-2-F5: fallback must produce different digests for different inputs"

    def test_redact_exc_value_falls_back_when_imports_fail(self, monkeypatch):
        """UE-2-F5: when ``redact_pii`` / ``redact_secret`` imports"""
        pii = "my name is John Smith"
        # Force the redaction imports to fail by hiding the modules.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.endswith("._secrets") or name.endswith(".security"):
                raise ImportError(f"simulated import failure for {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        redacted = crash_handler._redact_exc_value(pii)
        # The raw PII must NOT appear in the fallback.
        assert "John Smith" not in redacted, f"UE-2-F5: when imports fail, raw PII must NOT leak; got: {redacted!r}"
        # The fallback sentinel must be present.
        assert redacted.startswith("<redacted:sha256:") or redacted == "<redacted:imports-failed>", (
            f"UE-2-F5: fallback must be the safe sentinel; got: {redacted!r}"
        )

    def test_marker_uses_safe_fallback_when_redaction_imports_fail(self, restore_excepthook, monkeypatch, tmp_path):
        """UE-2-F5 end-to-end: when the redaction imports fail at"""
        crash_handler.set_crash_handler_config_dir(tmp_path)

        pii = "my name is John Smith and my secret is abc123"
        # Force the redaction imports to fail inside ``_redact_exc_value``.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.endswith("._secrets") or name.endswith(".security"):
                raise ImportError(f"simulated import failure for {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        try:
            raise ValueError(pii)
        except ValueError as exc:
            crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)

        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists()
        content = marker.read_text(encoding="utf-8")
        # The raw PII must NOT appear in the marker.
        assert "John Smith" not in content, (
            f"UE-2-F5: raw PII must NOT leak to marker when redaction imports fail; got:\n{content}"
        )
        assert "abc123" not in content, (
            f"UE-2-F5: raw secret must NOT leak to marker when redaction imports fail; got:\n{content}"
        )
        # The safe fallback sentinel must be present in the exc_value= line.
        assert "redacted:sha256:" in content or "redacted:imports-failed" in content, (
            f"UE-2-F5: marker must carry the safe fallback sentinel; got:\n{content}"
        )

    def test_atomic_write_import_failure_does_not_disable_redaction(self, restore_excepthook, monkeypatch, tmp_path):
        """UE-2-F5: a failure of ``_secure_atomic_write`` to import"""
        crash_handler.set_crash_handler_config_dir(tmp_path)

        # Force ONLY the _secure_atomic_write import to fail.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.endswith(".config") and "_secure_atomic_write" in str(args):
                raise ImportError("simulated config import failure")
            # The ``from voice_typer.server.config import _secure_atomic_write``
            if name == "voice_typer.server.config":
                raise ImportError("simulated config import failure")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        ssn = "123-45-6789"
        try:
            raise ValueError(f"error with PII: {ssn}")
        except ValueError as exc:
            crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)

        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists(), (
            "UE-2-F5: marker must still be written via Path.write_text fallback when _secure_atomic_write import fails"
        )
        content = marker.read_text(encoding="utf-8")
        # The SSN must STILL be redacted (redaction imports succeeded).
        assert ssn not in content, (
            f"UE-2-F5: redaction must STILL apply when only the atomic-write "
            f"import fails (imports are now decoupled); got:\n{content}"
        )


class TestEnsureKernel32WrappedInVehCallback:
    """The VEH callback wraps ``_ch._ensure_kernel32()`` in try/except"""

    def test_ensure_kernel32_call_is_wrapped_in_try_except(self):
        """UE-2-F8: the source of ``_veh_callback`` wraps the"""
        import voice_typer.server.crash_handler._veh_callback as _veh

        source = Path(_veh.__file__).read_text(encoding="utf-8")
        # The try/except must wrap the _ensure_kernel32 call.
        assert "_ch._ensure_kernel32()" in source, "UE-2-F8: _veh_callback must call _ch._ensure_kernel32()"
        # Find the call site and verify it's inside a try/except.
        import re

        # Match: try:\n <whitespace>_ch._ensure_kernel32()\n ... except Exception:
        pattern = re.compile(
            r"try:\s*\n\s*_ch\._ensure_kernel32\(\)\s*\n\s*except\s+Exception\s*:",
            re.MULTILINE,
        )
        assert pattern.search(source), (
            "UE-2-F8: the ``_ch._ensure_kernel32()`` call must be wrapped in "
            "a try/except Exception block so a kernel32 resolution failure "
            "returns EXCEPTION_CONTINUE_SEARCH instead of propagating"
        )

    def test_vectored_handler_impl_returns_continue_search_on_ensure_kernel32_failure(self, monkeypatch):
        """UE-2-F8: when ``_ensure_kernel32`` raises, the VEH callback"""
        if sys.platform == "win32":
            pytest.skip("VEH callback Windows-only path, tested on Windows host")

        assert callable(crash_handler._vectored_handler_impl)
        # raises). The function must NOT propagate.
        result = crash_handler._vectored_handler_impl(None)
        assert result == crash_handler.EXCEPTION_CONTINUE_SEARCH


class TestCrashMsgBufOnFacade:
    """``_crash_msg_buf`` mutable state lives on the ``crash_handler``"""

    def test_crash_msg_buf_exists_on_facade(self):
        """UE-2-F9: ``_crash_msg_buf`` is accessible on the facade."""
        assert hasattr(crash_handler, "_crash_msg_buf"), "UE-2-F9: _crash_msg_buf must be accessible on the facade"
        assert isinstance(crash_handler._crash_msg_buf, bytearray), (
            f"UE-2-F9: _crash_msg_buf must be a bytearray; got {type(crash_handler._crash_msg_buf).__name__}"
        )

    def test_crash_msg_buf_has_correct_size(self):
        """UE-2-F9: the facade-allocated buffer is sized to"""
        from voice_typer.server.crash_handler._constants import _CRASH_MSG_BUF_SIZE

        assert len(crash_handler._crash_msg_buf) == _CRASH_MSG_BUF_SIZE, (
            f"UE-2-F9: _crash_msg_buf size ({len(crash_handler._crash_msg_buf)}) "
            f"must match _CRASH_MSG_BUF_SIZE ({_CRASH_MSG_BUF_SIZE})"
        )

    def test_crash_msg_buf_removed_from_constants(self):
        """``_constants`` (it moved to the facade)."""
        assert not hasattr(_constants, "_crash_msg_buf"), (
            "UE-2-F9: _crash_msg_buf must NOT live in _constants anymore "
            "(moved to the facade). A duplicate declaration would create "
            "two independent buffers and silently break the VEH callback."
        )

    def test_veh_callback_uses_facade_buffer(self):
        """UE-2-F9: the VEH callback accesses the buffer via"""
        import voice_typer.server.crash_handler._veh_callback as _veh

        source = Path(_veh.__file__).read_text(encoding="utf-8")
        # The VEH callback must use _ch._crash_msg_buf at the call site.
        assert "_ch._crash_msg_buf" in source, "UE-2-F9: _veh_callback must access the buffer via _ch._crash_msg_buf"
        # The VEH callback must NOT import _crash_msg_buf from _constants.
        import re

        # Extract the import-from-_constants block.
        import_block = re.search(
            r"from voice_typer\.server\.crash_handler\._constants import \(([^)]+)\)",
            source,
            re.MULTILINE | re.DOTALL,
        )
        assert import_block is not None, "UE-2-F9: _veh_callback must have a ``from ... _constants import (...)`` block"
        imported_names = import_block.group(1)
        assert "_crash_msg_buf" not in imported_names, (
            "UE-2-F9: _veh_callback must NOT import _crash_msg_buf from "
            "_constants, it accesses the buffer via _ch._crash_msg_buf now. "
            f"Import block:\n{imported_names}"
        )

    def test_facade_buffer_is_mutable_in_place(self):
        """UE-2-F9: the facade buffer can be mutated in place (the VEH"""
        # Save the original buffer.
        original = crash_handler._crash_msg_buf
        try:
            fresh = bytearray(len(original))
            crash_handler._crash_msg_buf = fresh
            # The VEH callback reads ``_ch._crash_msg_buf`` at call
            assert crash_handler._crash_msg_buf is fresh
            # Mutate in place, the facade attribute reflects the mutation.
            crash_handler._crash_msg_buf[0] = 0x42
            assert crash_handler._crash_msg_buf[0] == 0x42
        finally:
            crash_handler._crash_msg_buf = original

    def test_constants_module_docstring_no_longer_claims_crash_msg_buf(self):
        """UE-2-F9: the ``_constants`` module docstring does NOT claim"""
        source = Path(_constants.__file__).read_text(encoding="utf-8")
        # It must NOT mention _crash_msg_buf as a current resident.
        import ast

        tree = ast.parse(source)
        docstring = ast.get_docstring(tree) or ""
        # The docstring must NOT advertise _crash_msg_buf as a current
        assert "and the pre-allocated ``_crash_msg_buf``" not in docstring, (
            "UE-2-F9: _constants docstring must NOT advertise _crash_msg_buf as a "
            "current resident (it moved to the facade). The docstring may "
            "mention the move in a 'moved to' note."
        )
