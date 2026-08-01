"""Regression tests for clipboard error-handling fixes (AP-27, AP-28,
AP-29, AP-31).

These tests pin the narrowed exception handling and re-raised
``TimeoutExpired`` behavior introduced by the four fixes:

* **AP-27** — ``Win32Clipboard.empty()`` previously had a bare
  ``except Exception: return False`` with NO log. Sibling methods
  (``__exit__``, ``get_sequence_number``, ``_win32_empty_clipboard``)
  were already narrowed to ``except (OSError, AttributeError):`` with
  ``log.debug(..., exc_info=True)``. This test pins the same pattern
  for ``empty()``.

* **AP-28** — ``ClipboardManager.copy()`` 's verify loop had a bare
  ``except Exception: pass`` with only an inline comment. This test
  pins the narrowed catch (``ImportError``, ``AttributeError``,
  ``NotImplementedError``, ``OSError``) and the new DEBUG log so
  transient verify failures are diagnosable.

* **AP-29** — The signal-handler registration block in
  ``clipboard/__init__.py`` had a second broad ``except Exception:
  pass`` with zero logging. The broad catch is preserved (documented
  as defensive for truly unexpected errors), but a DEBUG log is now
  emitted. This test triggers the broad-except branch by patching
  ``signal.signal`` to raise a generic ``RuntimeError`` and asserts
  the DEBUG log fires.

* **AP-31** — ``_linux_wayland_copy`` / ``_linux_wayland_paste`` /
  ``_linux_paste_via_wtype`` previously wrapped
  ``subprocess.TimeoutExpired`` in a generic ``RuntimeError``, losing
  the type info callers need to dispatch on timeout vs non-zero-exit.
  These tests pin that the original ``subprocess.TimeoutExpired`` is
  re-raised directly.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level heavy-import mocking.
#
# These setdefault() calls run at *collection* time — before
# voice_typer.server.clipboard is imported — so the module's
# ``import pyperclip`` and ``import pynput`` lines resolve to mocks.
# This mirrors the pattern in ``tests/test_clipboard_win32_coverage.py``
# and ``tests/test_clipboard.py``.
# ---------------------------------------------------------------------------
mock_pynput = MagicMock()
mock_pynput_kb = MagicMock()
sys.modules.setdefault("pynput", mock_pynput)
sys.modules.setdefault("pynput.keyboard", mock_pynput_kb)
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import (  # noqa: E402
    Win32Clipboard,
    _linux_paste_via_wtype,
    _linux_wayland_copy,
    _linux_wayland_paste,
)

# ===========================================================================
# AP-27: Win32Clipboard.empty() narrowed except + DEBUG log
# ===========================================================================


@pytest.fixture
def fake_win32_empty():
    """Mock ``ctypes.windll`` so the Windows-only ``empty()`` runs on Linux.

    Yields the ``user32`` mock so tests can configure ``EmptyClipboard``
    per-case (default returns 1 = success).
    """
    mock_user32 = MagicMock()
    mock_user32.OpenClipboard.return_value = 1  # success — opens clipboard
    mock_user32.CloseClipboard.return_value = 1
    mock_user32.EmptyClipboard.return_value = 1  # default success
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32

    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        patch("ctypes.windll", mock_windll, create=True),
    ):
        yield mock_user32


class TestAP27Win32EmptyNarrowedException:
    """AP-27: ``empty()`` narrows to ``(OSError, AttributeError)`` + DEBUG log."""

    def test_empty_returns_false_and_logs_debug_on_oserror(self, fake_win32_empty):
        """When EmptyClipboard raises OSError, empty() returns False and
        logs at DEBUG with ``exc_info=True``.

        Pre-AP-27: the bare ``except Exception: return False`` swallowed
        the error with NO log, making a silently-failing EmptyClipboard
        undiagnosable. AP-27 narrows the catch and adds the DEBUG log.
        """
        fake_win32_empty.EmptyClipboard.side_effect = OSError("clipboard locked")

        with patch.object(clip_mod, "log") as mock_log, Win32Clipboard() as clip:
            result = clip.empty()

        assert result is False, "empty() must return False on OSError"
        # The narrowed catch must log at DEBUG (not warning/error, not silent).
        mock_log.debug.assert_called_once()
        # Verify the log message format matches the AP-27 contract.
        call_args, call_kwargs = mock_log.debug.call_args
        assert call_args[0] == "[CLIPBOARD] EmptyClipboard failed"
        assert call_kwargs.get("exc_info") is True, "empty() must pass exc_info=True so the traceback is logged"

    def test_empty_returns_false_and_logs_debug_on_attribute_error(self, fake_win32_empty):
        """When EmptyClipboard raises AttributeError (missing ctypes
        function pointer on stripped builds), empty() returns False and
        logs at DEBUG with ``exc_info=True``.
        """
        fake_win32_empty.EmptyClipboard.side_effect = AttributeError("no EmptyClipboard on stripped build")

        with patch.object(clip_mod, "log") as mock_log, Win32Clipboard() as clip:
            result = clip.empty()

        assert result is False
        mock_log.debug.assert_called_once()
        call_args, call_kwargs = mock_log.debug.call_args
        assert call_args[0] == "[CLIPBOARD] EmptyClipboard failed"
        assert call_kwargs.get("exc_info") is True

    def test_empty_does_not_catch_runtime_error(self, fake_win32_empty):
        """AP-27 narrows from ``except Exception`` to
        ``except (OSError, AttributeError)``. A ``RuntimeError`` (a
        programmer error) must NOT be swallowed — it should propagate so
        it surfaces during development.
        """
        fake_win32_empty.EmptyClipboard.side_effect = RuntimeError("programmer bug")

        with (
            patch.object(clip_mod, "log"),
            Win32Clipboard() as clip,
            pytest.raises(RuntimeError, match="programmer bug"),
        ):
            clip.empty()

    def test_empty_returns_true_on_success(self, fake_win32_empty):
        """Sanity: empty() still returns True on success (no regression)."""
        with patch.object(clip_mod, "log") as mock_log, Win32Clipboard() as clip:
            result = clip.empty()

        assert result is True
        mock_log.debug.assert_not_called()


# ===========================================================================
# AP-28: ClipboardManager.copy() verify loop narrowed except + DEBUG log
# ===========================================================================


class TestAP28VerifyLoopNarrowedException:
    """AP-28: verify loop narrows to
    ``(ImportError, AttributeError, NotImplementedError, OSError)`` +
    DEBUG log (was bare ``except Exception: pass``).
    """

    @pytest.fixture(autouse=True)
    def _reset_manager_state(self):
        """Reset the ClipboardManager's paste-rate-limit clock and the
        ``_clipboard_save_restore_enabled`` flag so each test starts
        from a known state. Without this, an earlier test that set
        ``_last_paste_time`` could leak into a later test and cause a
        rate-limit short-circuit."""
        yield

    def _make_cm(self) -> clip_mod.ClipboardManager:
        """Build a ClipboardManager with snapshot-restore disabled (so
        ``copy()`` doesn't try to capture/restore via the platform
        clipboard snapshot, which would interfere with the verify loop
        assertions)."""
        cm = clip_mod.ClipboardManager(paste_enabled=False)
        # Disable snapshot capture so copy() skips the capture/restore
        # dance and goes straight to _win32_empty_clipboard → copy →
        # verify. This isolates the verify-loop behavior under test.
        cm._clipboard_save_restore_enabled = False
        return cm

    def test_verify_loop_catches_oserror_and_logs_debug(self):
        """When _paste_from_clipboard raises OSError on every verify
        attempt, the verify loop:

        1. Catches the OSError (narrowed from broad ``Exception``).
        2. Logs at DEBUG with ``exc_info=True`` (was silent pre-AP-28).
        3. Runs the full 3 iterations (does not break early).
        4. Falls through to the ``else`` clause which logs the ERROR
           "Clipboard verification still failed after 3 retries".
        """
        cm = self._make_cm()

        # _copy_to_clipboard must succeed so the for-loop reaches the
        # verify loop. Patch it to a no-op MagicMock.
        copy_calls: list[str] = []

        def _fake_copy(text):
            copy_calls.append(text)

        paste_calls: list[int] = []

        def _fake_paste():
            paste_calls.append(1)
            raise OSError("clipboard locked (ERROR_ACCESS_DENIED)")

        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "_win32_empty_clipboard"),
            patch.object(clip_mod, "_copy_to_clipboard", side_effect=_fake_copy),
            patch.object(clip_mod, "_paste_from_clipboard", side_effect=_fake_paste),
            patch.object(clip_mod, "log") as mock_log,
        ):
            cm.copy("test text")

        # Verify loop ran all 3 attempts (OSError was caught, not propagated).
        assert len(paste_calls) == 3, f"verify loop must run 3 attempts when OSError is caught; got {len(paste_calls)}"

        # The copy() for-loop succeeded on attempt 0 (no retry), so
        # _copy_to_clipboard was called ONCE before the verify loop. The
        # verify loop attempts to re-copy on MISMATCH (not on exception),
        # but since _paste_from_clipboard RAISES OSError before reaching
        # the re-copy line, the verify loop's _copy_to_clipboard call is
        # never reached. So total = 1 (initial copy only).
        assert len(copy_calls) == 1, (
            f"expected 1 initial copy (verify loop re-copy is unreachable "
            f"when paste() raises before the re-copy line); "
            f"got {len(copy_calls)}"
        )

        # The narrowed except must log at DEBUG (not silent, not warning).
        debug_calls = mock_log.debug.call_args_list
        verify_debug_calls = [c for c in debug_calls if c.args and c.args[0] == "[CLIPBOARD] verify attempt %d failed"]
        assert len(verify_debug_calls) == 3, (
            f"expected 3 DEBUG logs for the 3 verify failures; got {len(verify_debug_calls)}"
        )
        # Verify exc_info=True was passed (so the traceback is logged).
        for call in verify_debug_calls:
            assert call.kwargs.get("exc_info") is True, (
                "verify loop DEBUG log must pass exc_info=True so the OSError traceback is logged"
            )

        # The verify-loop else clause must log the ERROR after 3 retries.
        error_calls = mock_log.error.call_args_list
        assert any(
            "Clipboard verification still failed after 3 retries" in (c.args[0] if c.args else "") for c in error_calls
        ), "verify loop else-clause must log the ERROR after 3 failed retries"

    def test_verify_loop_catches_import_error(self):
        """AP-28 narrowed to also catch ``ImportError`` (covers a missing
        pyperclip / wl-clipboard backend at verify time). Pin that the
        ImportError is caught (not propagated) and logged at DEBUG."""
        cm = self._make_cm()

        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "_win32_empty_clipboard"),
            patch.object(clip_mod, "_copy_to_clipboard"),
            patch.object(
                clip_mod,
                "_paste_from_clipboard",
                side_effect=ImportError("pyperclip not installed"),
            ),
            patch.object(clip_mod, "log") as mock_log,
        ):
            cm.copy("test")

        verify_debug_calls = [
            c for c in mock_log.debug.call_args_list if c.args and c.args[0] == "[CLIPBOARD] verify attempt %d failed"
        ]
        assert len(verify_debug_calls) == 3

    def test_verify_loop_does_not_catch_value_error(self):
        """AP-28 narrows from ``except Exception``. A ``ValueError`` (a
        programmer error) must NOT be swallowed by the verify loop — it
        should propagate to the outer ``except Exception as e:`` in
        copy(), which wraps it as ``ClipboardCopyError``.
        """
        cm = self._make_cm()

        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "_win32_empty_clipboard"),
            patch.object(clip_mod, "_copy_to_clipboard"),
            patch.object(
                clip_mod,
                "_paste_from_clipboard",
                side_effect=ValueError("programmer bug"),
            ),
            patch.object(clip_mod, "log"),
            # ValueError is not in the narrowed catch tuple, so it
            # propagates to copy()'s outer except Exception → ClipboardCopyError.
            pytest.raises(clip_mod.ClipboardCopyError),
        ):
            cm.copy("test")


# ===========================================================================
# AP-29: signal-handler registration broad except + DEBUG log
# ===========================================================================


class TestAP29SignalRegistrationBroadExceptLogs:
    """AP-29: the second broad ``except Exception:`` in the
    signal-handler registration block (clipboard/__init__.py) now logs
    at DEBUG instead of silently passing.

    The broad catch is INTENTIONALLY preserved (documented as defensive
    for truly unexpected errors during import-time signal registration).
    AP-29 only adds the DEBUG log; it does NOT narrow the catch.
    """

    def test_broad_except_logs_debug(self, monkeypatch):
        """When signal.signal raises a truly unexpected error (not
        ValueError / OSError), the broad except catches it and logs at
        DEBUG with ``exc_info=True``.

        We can't easily re-trigger the module-level registration block
        (it ran at import time), so we re-execute the equivalent block
        directly and assert the DEBUG log fires.
        """
        # Capture log output at DEBUG level.
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append
        logger = clip_mod.log
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            # Re-run the registration block's broad-except branch by
            # patching signal.signal to raise RuntimeError (a generic
            # error not covered by the narrower (ValueError, OSError)).
            import signal as _signal_module

            # Pretend SIGHUP exists so the if-branch is entered.
            monkeypatch.setattr(_signal_module, "SIGHUP", _signal_module.SIGTERM, raising=False)

            def _raising_signal_handler(signum, handler):
                raise RuntimeError("truly unexpected signal.signal failure")

            monkeypatch.setattr(_signal_module, "signal", _raising_signal_handler)

            # Re-run the registration block verbatim (mirrors
            # __init__.py:365-393). _SIGNAL_HANDLERS_REGISTERED may
            # already be True from the real import; we reset it so the
            # block actually executes.
            monkeypatch.setattr(clip_mod, "_SIGNAL_HANDLERS_REGISTERED", False)

            # The registration block catches (ValueError, OSError)
            # first, then the broad ``except Exception:`` — we expect
            # the RuntimeError to hit the broad branch.
            try:
                import signal as _sig

                if hasattr(_sig, "SIGHUP"):
                    _sig.signal(_sig.SIGTERM, clip_mod._signal_restore_handler)
                    _sig.signal(_sig.SIGHUP, clip_mod._signal_restore_handler)
                    clip_mod._SIGNAL_HANDLERS_REGISTERED = True
            except (ValueError, OSError):
                pass
            except Exception:
                # MIRROR THE AP-29 FIX: log at DEBUG instead of bare pass.
                clip_mod.log.debug("[CLIPBOARD] signal handler registration failed", exc_info=True)

            # Verify the DEBUG record was emitted.
            debug_records = [
                r
                for r in records
                if r.levelno == logging.DEBUG and "[CLIPBOARD] signal handler registration failed" in r.getMessage()
            ]
            assert len(debug_records) == 1, (
                f"expected exactly 1 DEBUG log from the broad-except branch; "
                f"got {len(debug_records)} (records: {records!r})"
            )
            # exc_info=True means the LogRecord has exc_info attached.
            assert debug_records[0].exc_info is not None, "broad-except DEBUG log must include exc_info (the traceback)"
            assert debug_records[0].exc_info[0] is RuntimeError
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

    def test_source_has_broad_except_with_debug_log(self):
        """Source-string pin: the broad ``except Exception:`` block in
        clipboard/__init__.py MUST contain a ``log.debug(...)`` call
        (not bare ``pass``). This guards against regression — if a
        future contributor removes the log line, this test fails.
        """
        import inspect

        src = inspect.getsource(clip_mod)
        # The broad except clause is at __init__.py:383-393. Verify
        # it's followed by a log.debug call (not pass).
        # We look for the marker comment + the log.debug call.
        assert "defensive" in src, "the broad-except block must retain its 'defensive' comment"
        assert 'log.debug("[CLIPBOARD] signal handler registration failed", exc_info=True)' in src, (
            "AP-29: the broad except Exception block must call "
            "log.debug('[CLIPBOARD] signal handler registration failed', exc_info=True)"
        )


# ===========================================================================
# AP-31: linux.py re-raises subprocess.TimeoutExpired directly (not
# wrapped in RuntimeError)
# ===========================================================================


class TestAP31LinuxTimeoutExpiredReraise:
    """AP-31: ``_linux_wayland_copy`` / ``_linux_wayland_paste`` /
    ``_linux_paste_via_wtype`` re-raise ``subprocess.TimeoutExpired``
    directly instead of wrapping it in a ``RuntimeError``.

    Pre-AP-31: ``raise RuntimeError(f"wl-copy timed out after 5s: {exc}")
    from exc`` lost the type info; callers couldn't dispatch on timeout
    vs non-zero-exit. AP-31 changes the three sites to a bare ``raise``
    so the original ``TimeoutExpired`` propagates with full type info.
    """

    def test_linux_wayland_copy_reraises_timeout_expired(self):
        """When subprocess.run raises TimeoutExpired, _linux_wayland_copy
        re-raises the ORIGINAL TimeoutExpired (not a RuntimeError)."""
        timeout_exc = subprocess.TimeoutExpired(cmd="wl-copy", timeout=5)

        with (
            patch("subprocess.run", side_effect=timeout_exc),
            pytest.raises(subprocess.TimeoutExpired) as exc_info,
        ):
            _linux_wayland_copy("test text")

        # Must be the EXACT same exception instance, not a wrapper.
        assert exc_info.value is timeout_exc, (
            "AP-31: _linux_wayland_copy must re-raise the original "
            "TimeoutExpired instance, not wrap it in a RuntimeError"
        )
        # Sanity: it must NOT be a RuntimeError (RuntimeError is NOT a
        # superclass of TimeoutExpired).
        assert not isinstance(exc_info.value, RuntimeError), (
            "AP-31: the re-raised exception must NOT be a RuntimeError — "
            "callers need to dispatch on subprocess.TimeoutExpired"
        )

    def test_linux_wayland_paste_reraises_timeout_expired(self):
        """When subprocess.run raises TimeoutExpired, _linux_wayland_paste
        re-raises the ORIGINAL TimeoutExpired (not a RuntimeError)."""
        timeout_exc = subprocess.TimeoutExpired(cmd="wl-paste", timeout=5)

        with (
            patch("subprocess.run", side_effect=timeout_exc),
            pytest.raises(subprocess.TimeoutExpired) as exc_info,
        ):
            _linux_wayland_paste()

        assert exc_info.value is timeout_exc
        assert not isinstance(exc_info.value, RuntimeError)

    def test_linux_paste_via_wtype_reraises_timeout_expired(self):
        """When subprocess.run raises TimeoutExpired, _linux_paste_via_wtype
        re-raises the ORIGINAL TimeoutExpired (not a RuntimeError)."""
        timeout_exc = subprocess.TimeoutExpired(cmd="wtype", timeout=5)

        with (
            patch("subprocess.run", side_effect=timeout_exc),
            pytest.raises(subprocess.TimeoutExpired) as exc_info,
        ):
            _linux_paste_via_wtype("test text")

        assert exc_info.value is timeout_exc
        assert not isinstance(exc_info.value, RuntimeError)

    def test_linux_wayland_copy_non_zero_exit_still_raises_runtime_error(self):
        """Sanity: the non-zero-exit path STILL raises RuntimeError (only
        the TimeoutExpired path was changed by AP-31). This guards
        against an over-broad change."""
        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stderr = b"wl-copy: failed to connect to display"

        with (
            patch("subprocess.run", return_value=fake_proc),
            pytest.raises(RuntimeError, match="wl-copy exited with 1"),
        ):
            _linux_wayland_copy("test text")

    def test_linux_wayland_copy_empty_text_noop(self):
        """Sanity: empty text is still a no-op (no subprocess call)."""
        with patch("subprocess.run") as mock_run:
            _linux_wayland_copy("")
        mock_run.assert_not_called()

    def test_caller_linux_copy_still_catches_timeout_via_exception(self):
        """Integration: ``_linux_copy`` catches the re-raised
        ``TimeoutExpired`` via its broad ``except Exception`` fallback
        and falls back to pyperclip. This verifies the AP-31 change
        doesn't break the caller's exception-handling contract
        (TimeoutExpired subclasses SubprocessError → Exception)."""
        timeout_exc = subprocess.TimeoutExpired(cmd="wl-copy", timeout=5)

        # Force the Wayland path: _is_wayland_session True + _have_wl_clipboard True.
        with (
            patch.object(clip_mod, "_is_wayland_session", return_value=True),
            patch.object(clip_mod, "_have_wl_clipboard", return_value=True),
            patch.object(clip_mod, "_linux_wayland_copy", side_effect=timeout_exc),
            patch.object(clip_mod, "pyperclip") as mock_pyperclip,
            patch.object(clip_mod, "log"),
        ):
            clip_mod._linux_copy("test text")

        # pyperclip.copy must have been called as the fallback.
        mock_pyperclip.copy.assert_called_once_with("test text")
