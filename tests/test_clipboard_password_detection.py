"""Tests for G4-H-05: macOS / Linux password-field detection.

The pre-fix code unconditionally returned ``True`` from
``ClipboardManager._is_safe_paste_target`` on non-Windows, which allowed
dictated text to be pasted into password fields, SSH passphrase prompts,
credit-card forms, etc.

These tests exercise the platform-native password-field detection
helpers added in ``clipboard_target_safety.py``:

* ``_is_password_field_macos`` — uses pyobjc (AppKit + ApplicationServices)
  to query the focused UI element's ``AXRole`` / ``AXIsSecure``.
* ``_is_password_field_linux`` — uses pyatspi to walk the AT-SPI tree
  and check the focused accessible's role for ``ATSPI_ROLE_PASSWORD_TEXT``.

Since pyobjc / pyatspi are not installed in the test environment (and
would require a desktop session to actually query a UI), the helpers are
exercised via ``sys.modules`` mocks that inject fake modules with the
expected API surface.

The tests also pin the fallback behavior — when the platform library is
unavailable, the helper logs a WARNING (once) and returns ``False``
(allowing the caller to fall back to the legacy "paste allowed" path).
"""

from __future__ import annotations

import contextlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Mock pynput / pyperclip at import time so the clipboard module loads
# cleanly on a headless Linux box.
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server import clipboard_target_safety as safety_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402

# ---------------------------------------------------------------------------
# Display-env isolation (XS-22)
# ---------------------------------------------------------------------------
# Previously this module mutated the process environment at import time
# (setting DISPLAY=":99" and removing WAYLAND_DISPLAY) to keep clipboard
# code happy on a headless Linux box. Those mutations leaked into the
# entire test session. The autouse fixture below uses ``monkeypatch`` so
# the mutations are auto-restored after each test (no cross-test leak).
# XS-FIX-2 could consolidate this into ``tests/conftest.py`` as a
# session-scoped fixture; for now it is duplicated per-file because
# conftest.py is owned by another sub-agent.


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_unavailable_warnings():
    """Reset the once-only warning guards before each test.

    The guards are intentionally sticky in production so a noisy startup
    doesn't flood the log. Tests want to assert the WARNING is emitted
    on the first call after the guard is reset, so we reset between
    cases. We also clear the cached ``_PYATSPI_STATE_FOCUSED`` so a
    stale value from a previous test's fake pyatspi module doesn't
    leak into the next test's call.
    """
    safety_mod.reset_platform_unavailable_warnings()
    # Clear the cached STATE_FOCUSED so each test re-resolves it from
    # its own fake pyatspi module.
    safety_mod._PYATSPI_STATE_FOCUSED = None
    yield
    safety_mod.reset_platform_unavailable_warnings()
    safety_mod._PYATSPI_STATE_FOCUSED = None


# ===========================================================================
# Linux / AT-SPI2 password-field detection
# ===========================================================================


def _make_fake_pyatspi(*, focused_role: int) -> types.ModuleType:
    """Build a fake ``pyatspi`` module with a focused accessible.

    The fake desktop has one app, which has one focused window, which
    has one focused child whose role is ``focused_role``. The helper
    walks down through children with ``STATE_FOCUSED`` set, so this
    exercises the descent logic.
    """
    mod = types.ModuleType("pyatspi")

    # Constants exposed by the real pyatspi.
    mod.STATE_FOCUSED = 1 << 10
    mod.ROLE_PASSWORD_TEXT = 1 << 11
    mod.ROLE_TEXT = 1 << 12

    class _StateSet:
        def __init__(self, focused: bool):
            self._focused = focused

        def contains(self, state):
            return bool(self._focused and state == mod.STATE_FOCUSED)

    class _Accessible:
        def __init__(self, *, role: int, focused: bool, children=None):
            self._role = role
            self._state = _StateSet(focused=focused)
            self._children = children or []

        @property
        def childCount(self) -> int:  # noqa: N802
            return len(self._children)

        def getChildAtIndex(self, i):  # noqa: N802
            if 0 <= i < len(self._children):
                return self._children[i]
            return None

        def getState(self):  # noqa: N802
            return self._state

        def getRole(self):  # noqa: N802
            return self._role

    # Build the tree: desktop → app → window → focused leaf.
    # The FOCUSED state is set on the focused element itself (per
    # AT-SPI spec): the leaf has FOCUSED=True, its ancestors do NOT
    # (they may have ACTIVE / SHOWING but not FOCUSED).
    focused_leaf = _Accessible(role=focused_role, focused=True)
    window = _Accessible(role=mod.ROLE_TEXT, focused=False, children=[focused_leaf])
    app = _Accessible(role=mod.ROLE_TEXT, focused=False, children=[window])
    desktop = _Accessible(role=mod.ROLE_TEXT, focused=False, children=[app])

    class _Registry:
        @staticmethod
        def getDesktop(i):  # noqa: N802
            return desktop

    mod.Registry = _Registry
    return mod


class TestIsPasswordFieldLinux:
    """``_is_password_field_linux`` detects AT-SPI2 password-text fields."""

    def test_returns_true_when_focused_role_is_password_text(self):
        """Focused accessible with ROLE_PASSWORD_TEXT → True (paste blocked)."""
        fake = _make_fake_pyatspi(focused_role=1 << 11)  # ROLE_PASSWORD_TEXT
        with patch.dict(sys.modules, {"pyatspi": fake}), patch.object(clip_mod, "log") as mock_log:
            result = safety_mod._is_password_field_linux()
        assert result is True
        # A warning was logged announcing the password-field detection.
        warning_calls = [c for c in mock_log.warning.call_args_list if "Linux password field detected" in str(c)]
        assert len(warning_calls) == 1

    def test_returns_false_when_focused_role_is_plain_text(self):
        """Focused accessible with ROLE_TEXT → False (paste allowed)."""
        fake = _make_fake_pyatspi(focused_role=1 << 12)  # ROLE_TEXT
        with patch.dict(sys.modules, {"pyatspi": fake}):
            result = safety_mod._is_password_field_linux()
        assert result is False

    def test_returns_false_and_warns_when_pyatspi_missing(self):
        """When pyatspi is not installed, log a warning (once) + return False."""
        # Simulate "pyatspi not installed" by ensuring sys.modules has no
        # real pyatspi AND the lazy import raises ImportError. The
        # clipboard_target_safety module uses ``import pyatspi``; we patch
        # builtins.__import__ to raise ImportError for that exact module.
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _block_pyatspi(name, *args, **kwargs):
            if name == "pyatspi":
                raise ImportError("simulated: pyatspi not installed")
            return real_import(name, *args, **kwargs)

        # Clear any cached pyatspi module so the import retry fires.
        saved = sys.modules.pop("pyatspi", None)
        try:
            with patch("builtins.__import__", side_effect=_block_pyatspi), patch.object(clip_mod, "log") as mock_log:
                result1 = safety_mod._is_password_field_linux()
                result2 = safety_mod._is_password_field_linux()
        finally:
            if saved is not None:
                sys.modules["pyatspi"] = saved

        assert result1 is False
        assert result2 is False
        # First call logs a WARNING with the install hint.
        warning_calls = [c for c in mock_log.warning.call_args_list if "pyatspi not installed" in str(c)]
        assert len(warning_calls) == 1, "expected exactly ONE warning (once-only guard)"
        # Second call logs at DEBUG (already-warned path).
        debug_calls = [
            c for c in mock_log.debug.call_args_list if "pyatspi not installed" in str(c) and "already warned" in str(c)
        ]
        assert len(debug_calls) >= 1

    def test_returns_true_when_pyatspi_raises_on_get_desktop(self):
        """If pyatspi.Registry.getDesktop raises, fail closed (return True).

        EC-15: when the AT-SPI2 desktop bus is unavailable, we cannot
        traverse the accessibility tree to verify the focused element
        is not a password field. Failing closed (blocking paste) is
        safer than allowing paste into a potentially-sensitive field.
        """
        fake = _make_fake_pyatspi(focused_role=1 << 11)

        # Override getDesktop to raise.
        def _raise(_i):
            raise RuntimeError("AT-SPI bus unavailable")

        fake.Registry.getDesktop = staticmethod(_raise)
        with patch.dict(sys.modules, {"pyatspi": fake}), patch.object(clip_mod, "log"):
            result = safety_mod._is_password_field_linux()
        assert result is True


# ===========================================================================
# macOS / Accessibility API password-field detection
# ===========================================================================


def _make_fake_pyobjc(*, role: str = "AXTextField", is_secure: bool = False) -> dict:
    """Build fake ``AppKit`` and ``ApplicationServices`` modules.

    The fake emulates the pyobjc API surface used by
    ``_is_password_field_macos``: ``NSWorkspace.sharedWorkspace()``
    → ``frontmostApplication()`` → ``processIdentifier()``, and
    ``AXUIElementCreateApplication(pid)`` → AXUIElement.

    ``AXUIElementCopyAttributeValue`` returns ``(status, value)``
    tuples; status 0 = success.

    Parameters
    ----------
    role : str
        The role string returned for the focused element's ``AXRole``.
    is_secure : bool
        The value returned for the focused element's ``AXIsSecure``.
    """
    appkit = types.ModuleType("AppKit")

    class _NSRunningApp:
        def processIdentifier(self):  # noqa: N802
            return 1234

    class _Workspace:
        @staticmethod
        def sharedWorkspace():  # noqa: N802
            return _Workspace()

        def frontmostApplication(self):  # noqa: N802
            return _NSRunningApp()

    appkit.NSWorkspace = _Workspace

    app_services = types.ModuleType("ApplicationServices")

    # Track call counts so tests can assert on which attributes were queried.
    call_log: list = []

    def _AXUIElementCreateApplication(pid):  # noqa: N802
        call_log.append(("AXUIElementCreateApplication", pid))
        return MagicMock(name=f"app_elem[{pid}]")

    def _AXUIElementCopyAttributeValue(element, attribute, _reserved):  # noqa: N802
        call_log.append(("AXUIElementCopyAttributeValue", attribute))
        if attribute == "AXFocusedUIElement":
            return (0, MagicMock(name="focused_elem"))
        if attribute == "AXRole":
            return (0, role)
        if attribute == "AXIsSecure":
            return (0, is_secure)
        return (0, None)

    app_services.AXUIElementCreateApplication = _AXUIElementCreateApplication
    app_services.AXUIElementCopyAttributeValue = _AXUIElementCopyAttributeValue

    return {"AppKit": appkit, "ApplicationServices": app_services, "call_log": call_log}


class TestIsPasswordFieldMacOS:
    """``_is_password_field_macos`` detects AXSecureTextField password fields."""

    def test_returns_true_when_role_is_ax_secure_textfield(self):
        """AXRole == "AXSecureTextField" → True (paste blocked)."""
        fakes = _make_fake_pyobjc(role="AXSecureTextField", is_secure=False)
        with (
            patch.dict(sys.modules, {"AppKit": fakes["AppKit"], "ApplicationServices": fakes["ApplicationServices"]}),
            patch.object(clip_mod, "log") as mock_log,
        ):
            result = safety_mod._is_password_field_macos()
        assert result is True
        # Warning logged for the password-field detection.
        warning_calls = [c for c in mock_log.warning.call_args_list if "AXSecureTextField" in str(c)]
        assert len(warning_calls) == 1

    def test_returns_true_when_ax_is_secure_attribute_true(self):
        """AXIsSecure=True on a non-standard role → True (covers custom controls)."""
        fakes = _make_fake_pyobjc(role="AXTextField", is_secure=True)
        with (
            patch.dict(sys.modules, {"AppKit": fakes["AppKit"], "ApplicationServices": fakes["ApplicationServices"]}),
            patch.object(clip_mod, "log") as mock_log,
        ):
            result = safety_mod._is_password_field_macos()
        assert result is True
        warning_calls = [c for c in mock_log.warning.call_args_list if "AXIsSecure=True" in str(c)]
        assert len(warning_calls) == 1

    def test_returns_false_when_role_is_plain_textfield(self):
        """AXRole == "AXTextField" + AXIsSecure=False → False (paste allowed)."""
        fakes = _make_fake_pyobjc(role="AXTextField", is_secure=False)
        with (
            patch.dict(sys.modules, {"AppKit": fakes["AppKit"], "ApplicationServices": fakes["ApplicationServices"]}),
            patch.object(clip_mod, "log"),
        ):
            result = safety_mod._is_password_field_macos()
        assert result is False

    def test_returns_false_and_warns_when_pyobjc_missing(self):
        """When pyobjc (AppKit/ApplicationServices) is not installed,
        log a warning (once) + return False."""
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        blocked = {"AppKit", "ApplicationServices"}

        def _block_pyobjc(name, *args, **kwargs):
            if name in blocked:
                raise ImportError(f"simulated: {name} not installed")
            return real_import(name, *args, **kwargs)

        # Clear any cached modules so the import retry fires.
        saved_appkit = sys.modules.pop("AppKit", None)
        saved_app_services = sys.modules.pop("ApplicationServices", None)
        try:
            with patch("builtins.__import__", side_effect=_block_pyobjc), patch.object(clip_mod, "log") as mock_log:
                result1 = safety_mod._is_password_field_macos()
                result2 = safety_mod._is_password_field_macos()
        finally:
            if saved_appkit is not None:
                sys.modules["AppKit"] = saved_appkit
            if saved_app_services is not None:
                sys.modules["ApplicationServices"] = saved_app_services

        assert result1 is False
        assert result2 is False
        warning_calls = [c for c in mock_log.warning.call_args_list if "pyobjc" in str(c) and "not installed" in str(c)]
        assert len(warning_calls) == 1, "expected exactly ONE warning (once-only guard)"
        debug_calls = [
            c for c in mock_log.debug.call_args_list if "pyobjc not installed" in str(c) and "already warned" in str(c)
        ]
        assert len(debug_calls) >= 1

    def test_returns_false_when_no_frontmost_app(self):
        """If NSWorkspace.frontmostApplication() returns None, return False."""
        fakes = _make_fake_pyobjc(role="AXSecureTextField")

        # Override frontmostApplication to return None.
        fakes["AppKit"].NSWorkspace.frontmostApplication = lambda self: None
        with (
            patch.dict(sys.modules, {"AppKit": fakes["AppKit"], "ApplicationServices": fakes["ApplicationServices"]}),
            patch.object(clip_mod, "log"),
        ):
            result = safety_mod._is_password_field_macos()
        assert result is False

    def test_returns_true_when_ax_call_raises(self):
        """If AXUIElementCopyAttributeValue raises, fail closed (return True).

        EC-15: when the AX API call itself raises (e.g. broken
        accessibility permission), we cannot verify the focused
        element is not a password field. Failing closed (blocking
        paste) is safer than allowing paste into a potentially-
        sensitive field.
        """
        fakes = _make_fake_pyobjc(role="AXSecureTextField")

        def _raise(*args, **kwargs):
            raise RuntimeError("AX permission denied")

        fakes["ApplicationServices"].AXUIElementCopyAttributeValue = _raise
        with (
            patch.dict(sys.modules, {"AppKit": fakes["AppKit"], "ApplicationServices": fakes["ApplicationServices"]}),
            patch.object(clip_mod, "log"),
        ):
            result = safety_mod._is_password_field_macos()
        assert result is True


# ===========================================================================
# _is_safe_paste_target dispatcher (non-Windows)
# ===========================================================================


class TestIsSafePasteTargetDispatch:
    """``_is_safe_paste_target`` dispatches to the platform-native helper."""

    def test_dispatches_to_macos_helper_on_macos(self):
        """When is_macos() and not is_windows(), call _is_password_field_macos."""
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=True),
            patch.object(clip_mod, "is_linux", return_value=False),
            patch.object(clip_mod, "_is_password_field_macos", return_value=True) as mock_macos,
            patch.object(clip_mod, "_is_password_field_linux", return_value=False) as mock_linux,
            patch.object(clip_mod, "log"),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is False  # password field detected → paste blocked
        mock_macos.assert_called_once()
        mock_linux.assert_not_called()

    def test_dispatches_to_linux_helper_on_linux(self):
        """When is_linux() and not is_windows(), call _is_password_field_linux."""
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "is_linux", return_value=True),
            patch.object(clip_mod, "_is_password_field_linux", return_value=True) as mock_linux,
            patch.object(clip_mod, "_is_password_field_macos", return_value=False) as mock_macos,
            patch.object(clip_mod, "log"),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is False
        mock_linux.assert_called_once()
        mock_macos.assert_not_called()

    def test_returns_true_when_no_password_field_detected(self):
        """When platform helper returns False, paste is allowed (True)."""
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "is_linux", return_value=True),
            patch.object(clip_mod, "_is_password_field_linux", return_value=False),
            patch.object(clip_mod, "log"),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True

    def test_fails_open_when_platform_helper_raises(self):
        """If the platform helper itself raises, the dispatcher logs + returns True."""
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "is_linux", return_value=True),
            patch.object(clip_mod, "_is_password_field_linux", side_effect=RuntimeError("boom")),
            patch.object(clip_mod, "log") as mock_log,
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True  # fail-open on dispatcher exception
        # The dispatcher logs a warning about the failure.
        warning_calls = [
            c for c in mock_log.warning.call_args_list if "non-Windows password-field check raised" in str(c)
        ]
        assert len(warning_calls) == 1

    def test_returns_true_on_unknown_platform(self):
        """On an unknown platform (BSD, etc.), neither helper is called."""
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "is_linux", return_value=False),
            patch.object(clip_mod, "_is_password_field_macos") as mock_macos,
            patch.object(clip_mod, "_is_password_field_linux") as mock_linux,
            patch.object(clip_mod, "log"),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True
        mock_macos.assert_not_called()
        mock_linux.assert_not_called()


# ===========================================================================
# Signal handler registration (POSIX)
# ===========================================================================


class TestSignalHandlerRegistration:
    """POSIX signal handler for SIGTERM/SIGHUP restores pending snapshots."""

    def test_signal_restore_handler_calls_force_restore(self):
        """The signal handler invokes _force_restore_pending_at_exit()."""
        # The handler will re-raise the signal via os.kill, which
        # we must prevent from actually killing the test process.
        # Patch os.kill + signal.signal to no-ops.
        with (
            patch.object(clip_mod, "_force_restore_pending_at_exit") as mock_force,
            patch.object(clip_mod.os, "kill") as mock_kill,
            patch.object(clip_mod, "_signal_restore_handler", wraps=clip_mod._signal_restore_handler),
        ):
            # Re-import the inner logic by calling the handler directly.
            # We need to suppress the re-raise: patch the signal
            # module so SIG_DFL is a sentinel and signal() is a noop.
            import signal as signal_mod

            sentinel = object()
            with (
                patch.object(signal_mod, "SIG_DFL", sentinel),
                patch.object(signal_mod, "signal", return_value=None),
                contextlib.suppress(SystemExit),
            ):
                # The handler should run _force_restore_pending_at_exit
                # then call os.kill (patched to noop). Should not raise.
                clip_mod._signal_restore_handler(signal_mod.SIGTERM, None)
        mock_force.assert_called_once()
        # os.kill was called with our PID and the signum.
        if mock_kill.called:
            args, _ = mock_kill.call_args
            assert args[0] == clip_mod.os.getpid()
            assert args[1] == signal_mod.SIGTERM

    def test_signal_handler_registered_on_posix(self):
        """On POSIX, the module-level registration block ran successfully."""
        # The registration is module-level and runs at import. We just
        # assert the flag was set (which only happens when the signal
        # module exposes SIGHUP — i.e. POSIX).
        import signal as signal_mod

        if hasattr(signal_mod, "SIGHUP"):
            # The module-level registration flag should be True on POSIX.
            # (If the test runner is in a non-main thread, the
            # registration is skipped; pytest's main thread is the
            # default, so this should hold.)
            assert clip_mod._SIGNAL_HANDLERS_REGISTERED is True, (
                "POSIX signal handlers should have been registered at clipboard.py module import time"
            )
        else:
            # On Windows, SIGHUP doesn't exist — registration skipped.
            assert clip_mod._SIGNAL_HANDLERS_REGISTERED is False
