"""Tests for ``MicrophoneDeviceWatcher._post_quit_to_windows`` (SI-18).

on 64-bit Windows, ``_post_quit_to_windows`` called
``user32.PostMessageW(hwnd, ...)`` without first setting
``PostMessageW.restype`` / ``PostMessageW.argtypes``. ctypes defaults
to ``c_int`` restype and untyped argtypes, which TRUNCATES the 64-bit
``HWND`` handle to 32 bits. The truncated handle is almost never a
valid window, so ``PostMessageW`` returns 0 (failure) without posting
``WM_QUIT`` — the ``GetMessageW`` pump never wakes and ``stop()``'s
2s ``join`` times out, leaking a thread on every ``stop()`` on 64-bit
Windows.

These tests run on Linux by mocking ``ctypes.windll`` (which doesn't
exist on non-Windows) — following the same pattern as
``tests/test_microphone_watcher.py::fake_windows_windll``. They
verify:

- ``restype`` is set to ``wintypes.BOOL`` before the ``PostMessageW`` call.
- ``argtypes`` is set to ``[HWND, UINT, WPARAM, LPARAM]`` before the call.
- ``PostMessageW`` is actually invoked with the watcher's hwnd + WM_QUIT.
- A failure return (``PostMessageW`` returns 0) does NOT raise — the
  method logs and returns, so ``stop()`` still completes (the pump
  thread will time out independently, but the caller is not blocked).
- No-op when ``_windows_hwnd`` is unset (e.g. pump never started).
- No-op when ``ctypes``/``wintypes``/``windll`` are unavailable (e.g.
  the watcher thread crashed before reaching the Win32 setup).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

# WM_QUIT — the message ``_post_quit_to_windows`` posts to wake the
# blocking ``GetMessageW`` pump. 0x0012 is the Win32 constant
# (winuser.h ``#define WM_QUIT 0x0012``).
_WM_QUIT = 0x0012


@pytest.fixture
def fake_windll_for_post_quit():
    """Mock ``ctypes.windll`` + ``ctypes.get_last_error`` so
    ``_post_quit_to_windows`` runs on Linux.

    Yields a dict with the mocked ``user32`` and a captured-args
    helper so individual tests can assert on the ``PostMessageW``
    call and the restype/argtypes attributes set before it.
    """
    mock_user32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32

    # Default: PostMessageW succeeds (returns nonzero).
    mock_user32.PostMessageW.return_value = 1

    with (
        patch("ctypes.windll", mock_windll, create=True),
        patch("ctypes.get_last_error", return_value=0, create=True),
    ):
        yield {
            "user32": mock_user32,
            "windll": mock_windll,
        }


class TestPostQuitToWindowsArgtypes:
    """``_post_quit_to_windows`` must set argtypes/restype
    BEFORE calling ``PostMessageW`` so the 64-bit HWND is not
    truncated to ``c_int``."""

    def test_post_quit_sets_restype_to_bool(self, fake_windll_for_post_quit):
        """``PostMessageW.restype`` is set to ``wintypes.BOOL`` before
        the call."""
        from ctypes import wintypes

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x0000000A_00001234  # 64-bit hwnd

        watcher._post_quit_to_windows()

        # restype must be wintypes.BOOL (not the ctypes default c_int).
        assert fake_windll_for_post_quit["user32"].PostMessageW.restype is wintypes.BOOL, (
            "PostMessageW.restype must be wintypes.BOOL — without this, the "
            "BOOL return is read as c_int and may truncate on 64-bit Windows"
        )

    def test_post_quit_sets_argtypes_to_hwnd_uint_wparam_lparam(self, fake_windll_for_post_quit):
        """``PostMessageW.argtypes`` is set to
        ``[HWND, UINT, WPARAM, LPARAM]`` before the call.

        Without ``argtypes``, ctypes treats every argument as ``c_int``
        — which on 64-bit Windows truncates the 64-bit HWND to 32 bits,
        producing an invalid handle that ``PostMessageW`` silently
        rejects (returns 0).
        """
        from ctypes import wintypes

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x0000000B_00005678  # 64-bit hwnd

        watcher._post_quit_to_windows()

        argtypes = fake_windll_for_post_quit["user32"].PostMessageW.argtypes
        assert argtypes == [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ], (
            "PostMessageW.argtypes must be [HWND, UINT, WPARAM, LPARAM] — "
            f"got {argtypes!r}. Without HWND in argtypes, the 64-bit handle "
            "is truncated to c_int and PostMessageW returns 0."
        )

    def test_post_quit_calls_post_message_with_hwnd_and_wm_quit(self, fake_windll_for_post_quit):
        """``PostMessageW`` is called with the watcher's hwnd and
        ``WM_QUIT`` (0x0012) — the message that wakes the blocking
        ``GetMessageW`` pump so it exits and ``stop()``'s join
        succeeds."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        hwnd = 0x1234_ABCD
        watcher._windows_hwnd = hwnd

        watcher._post_quit_to_windows()

        fake_windll_for_post_quit["user32"].PostMessageW.assert_called_once()
        call_args = fake_windll_for_post_quit["user32"].PostMessageW.call_args
        # ``PostMessageW(hwnd, WM_QUIT, 0, 0)`` — wparam/lparam are 0.
        assert call_args.args[0] == hwnd, f"PostMessageW called with hwnd={call_args.args[0]!r}, expected {hwnd!r}"
        assert call_args.args[1] == _WM_QUIT, (
            f"PostMessageW called with msg={call_args.args[1]!r}, expected WM_QUIT (0x0012)"
        )

    def test_post_quit_sets_argtypes_before_call(self, fake_windll_for_post_quit):
        """The argtypes/restype assignment MUST happen before the
        ``PostMessageW`` call — not after. This is the actual SI-18
        fix: without prior argtypes, the HWND is truncated DURING the
        call (the truncation happens in ctypes' argument conversion,
        which runs before the function pointer is invoked).

        We verify the ordering by capturing the state of
        ``PostMessageW.restype`` / ``argtypes`` at the moment
        ``PostMessageW`` is invoked (via a side_effect that snapshots
        them).
        """
        from ctypes import wintypes

        captured = {}

        def snapshot_then_call(*args, **kwargs):
            # Snapshot the restype/argtypes at call time (BEFORE the
            # MagicMock's default return value is produced).
            captured["restype_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.restype
            captured["argtypes_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.argtypes
            return 1  # success

        fake_windll_for_post_quit["user32"].PostMessageW.side_effect = snapshot_then_call

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0xDEAD_BEEF

        watcher._post_quit_to_windows()

        assert captured.get("restype_at_call") is wintypes.BOOL, (
            "restype was not set to wintypes.BOOL BEFORE the PostMessageW call — "
            "the 64-bit HWND truncation happens during ctypes' argument "
            "conversion, which runs BEFORE the function pointer is invoked. "
            f"Captured restype at call time: {captured.get('restype_at_call')!r}"
        )
        assert captured.get("argtypes_at_call") == [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ], (
            "argtypes was not set to [HWND, UINT, WPARAM, LPARAM] BEFORE the "
            "PostMessageW call. Captured argtypes at call time: "
            f"{captured.get('argtypes_at_call')!r}"
        )

    def test_post_quit_returns_without_raising_on_failure(self, fake_windll_for_post_quit, caplog):
        """If ``PostMessageW`` returns 0 (failure — e.g. window
        already destroyed by a concurrent ``DestroyWindow``), the
        method must NOT raise. ``stop()`` calls
        ``_post_quit_to_windows`` unconditionally and a raise would
        break the stop flow.

        The method logs a debug message so the failure is traceable
        but does not propagate.
        """
        fake_windll_for_post_quit["user32"].PostMessageW.return_value = 0  # failure

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x10000

        with caplog.at_level(
            logging.DEBUG,
            logger="voice_typer.server.microphone_watcher",
        ):
            # Must not raise.
            watcher._post_quit_to_windows()

        # A debug log was emitted (the failure is traceable but silent).
        debug_msgs = [r.message for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("PostMessageW(WM_QUIT) returned 0" in m for m in debug_msgs), (
            f"Expected 'PostMessageW(WM_QUIT) returned 0' debug log on failure, got: {debug_msgs}"
        )

    def test_post_quit_noop_when_hwnd_unset(self, fake_windll_for_post_quit):
        """When ``_windows_hwnd`` is unset (pump never started, or
        already cleaned up), ``PostMessageW`` is NOT called.

        This is the normal early-stop path: ``stop()`` is called
        before the watcher thread has created the window, so there's
        nothing to post to. The production code uses
        ``getattr(self, "_windows_hwnd", None)`` so the attribute
        being ABSENT (the constructor doesn't pre-initialise it) is
        equivalent to ``None``.
        """
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        # ``_windows_hwnd`` is NOT set by the constructor — verify the
        # production code's ``getattr(..., None)`` handles the missing
        # attribute gracefully.
        assert getattr(watcher, "_windows_hwnd", None) is None

        watcher._post_quit_to_windows()

        fake_windll_for_post_quit["user32"].PostMessageW.assert_not_called()

    def test_post_quit_noop_when_hwnd_falsy(self, fake_windll_for_post_quit):
        """When ``_windows_hwnd`` is 0 (window creation failed but the
        attribute was set to a falsy value), ``PostMessageW`` is NOT
        called — there's no valid window to post to."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0

        watcher._post_quit_to_windows()

        fake_windll_for_post_quit["user32"].PostMessageW.assert_not_called()

    def test_post_quit_noop_when_windll_unavailable(self, caplog):
        """When ``ctypes.windll`` is unavailable (non-Windows, or
        ``ctypes`` failed to expose it), the method logs and returns
        without raising.
        This is the normal Linux/macOS path — ``_post_quit_to_windows``
        is called unconditionally from ``stop()`` and must not raise
        on platforms where ``windll`` doesn't exist.
        On any platform we patch ``ctypes.windll`` with a stub whose
        ``user32`` lookup raises ``AttributeError`` — simulating the
        "windll exists but user32 lookup fails" edge case (Windows) and
        the naturally-absent case (POSIX) uniformly.
        """
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._windows_hwnd = 0x1234  # would normally trigger the call

        with (
            caplog.at_level(
                logging.DEBUG,
                logger="voice_typer.server.microphone_watcher",
            ),
            patch("ctypes.windll", _NoUser32Windll(), create=True),
        ):
            watcher._post_quit_to_windows()

        debug_msgs = [r.message for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("windll unavailable" in m for m in debug_msgs), (
            f"Expected 'windll unavailable' debug log on non-Windows, got: {debug_msgs}"
        )


class _NoUser32Windll:
    """Stand-in for ``ctypes.windll`` whose ``user32`` lookup fails.
    A plain class attribute would not raise — the production code does
    ``ctypes.windll.user32`` (attribute access). A ``@property`` on the
    class raises ``AttributeError`` on access, which is exactly the
    failure the production code guards against.
    """

    @property
    def user32(self) -> None:
        raise AttributeError("mock: user32 unavailable")


# ── End-to-end: ``stop()`` triggers ``_post_quit_to_windows`` ──────────


class TestStopTriggersPostQuit:
    """Verify that ``stop()`` actually invokes
    ``_post_quit_to_windows`` with the SI-18 argtypes fix in place.

    This is the regression guard for the original SI-18 bug: a 64-bit
    Windows user could plug/unplug a mic, then quit the app, and
    ``stop()`` would time out (2s) leaking the watcher thread because
    ``PostMessageW`` silently failed on the truncated HWND.  This
    test simulates the quit path on Linux by mocking ``windll`` and
    asserting that the SI-18 argtypes/restype are present at the
    moment ``PostMessageW`` is invoked.
    """

    def test_stop_invokes_post_quit_with_argtypes_set(self, fake_windll_for_post_quit):
        """``stop()`` → ``_post_quit_to_windows`` → ``PostMessageW``
        with ``argtypes`` already set (the SI-18 fix).

        ``stop()`` only calls ``_post_quit_to_windows`` when both:
        (a) ``self._platform == "windows"`` and
        (b) ``self._thread is not None``.
        We mock the thread to a non-None sentinel whose ``join`` and
        ``is_alive`` return immediately so the stop flow completes
        synchronously.
        """
        from ctypes import wintypes

        captured = {}

        def snapshot(*args, **kwargs):
            captured["argtypes_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.argtypes
            captured["restype_at_call"] = fake_windll_for_post_quit["user32"].PostMessageW.restype
            return 1

        fake_windll_for_post_quit["user32"].PostMessageW.side_effect = snapshot

        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._platform = "windows"
        # Simulate the watcher thread having created a window.
        watcher._windows_hwnd = 0xCAFE_F00D
        # Provide a fake non-None thread so ``stop()`` doesn't early-return
        # at ``if self._thread is None: return``. The fake thread's
        # ``join`` and ``is_alive`` are no-ops so stop() completes
        # synchronously without waiting on a real thread.
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = False
        watcher._thread = fake_thread

        watcher.stop()

        assert captured.get("restype_at_call") is wintypes.BOOL, (
            "stop() → _post_quit_to_windows did not set restype = wintypes.BOOL before PostMessageW was invoked"
        )
        assert captured.get("argtypes_at_call") == [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ], (
            "stop() → _post_quit_to_windows did not set argtypes = "
            "[HWND, UINT, WPARAM, LPARAM] before PostMessageW was invoked"
        )
