"""Clipboard paste-target safety checks (extracted from ``manager.py``).

clipboard split, pass b. Contains the implementation of the four safety helpers
that lived as staticmethods on :class:`ClipboardManager`:

* :func:`_is_safe_paste_target_impl`: verifies the foreground window
  is safe for pasting (blocks UAC dialogs, credential prompts, Winlogon,
  elevated targets, password fields).
* :func:`_is_terminal_process_impl`: name-based terminal detection.
* :func:`_detect_focused_process_impl`: Win32 focused-process name
  probe (for terminal/rich-editor logging).
* :func:`_get_frontmost_pid_macos_impl`: macOS frontmost-app PID
  probe (for TOCTOU re-check).

Design contract (preserved verbatim from the pre-split ``manager.py``):

* All patchable symbols (``is_windows``, ``is_macos``, ``is_linux``,
  ``log``, ``_is_elevated_target``, ``_is_password_field``,
  ``_is_content_editable``, ``_get_uia_focused_element``,
  ``_is_password_field_macos``, ``_is_password_field_linux``,
  ``_TERMINAL_PROCESS_NAMES``) are looked up via the PACKAGE
  (``_cb.X``) at call time. NOT via this module's globals, so test
  patches like
  ``patch.object(clip_mod, "is_windows", return_value=True)`` /
  ``patch("voice_typer.server.clipboard._is_elevated_target", ...)``
  actually take effect on the code paths in this module.
* :meth:`ClipboardManager._is_safe_paste_target` /
  :meth:`ClipboardManager._is_terminal_process` /
  :meth:`ClipboardManager._detect_focused_process` /
  :meth:`ClipboardManager._get_frontmost_pid_macos` remain as thin
  delegator staticmethods on the class so the public API and the
  ``patch.object(ClipboardManager, "_is_safe_paste_target", ...)``
  patch surface used by ~12 tests keep working unchanged.
"""

from __future__ import annotations

import contextlib
from typing import Any

from voice_typer.server import clipboard as _cb


def _is_safe_paste_target_impl() -> bool:
    """Check that the foreground window is safe for pasting.

    Blocks paste into UAC dialogs, credential prompts, and
    Winlogon windows to prevent credential theft.

     (Medium, Security): the outer ``except Exception`` now
    distinguishes "safety-check infrastructure is broken" from
    "target is safe." Previously any exception (including from
    ``_is_password_field`` or ``_is_elevated_target``) returned
    ``True`` (fail-open): meaning a broken UIA install would
    silently disable credential-prompt blocking. Now:

      * Exceptions from ``_is_password_field`` or
        ``_is_elevated_target`` → log WARNING, return ``False``
        (fail-closed). We'd rather skip paste than risk pasting
        into a credential prompt with broken detection.
      * Exceptions from ``_is_content_editable`` → log DEBUG,
        continue (fail-open). contentEditable is informational
        only, not a security gate.
      * Truly outer exceptions (e.g. ``ctypes`` itself broken) →
        still fail-open. This indicates a broken Python install,
        not a security infra issue.

     (Perf): the focused UIA element is fetched ONCE at the
    top via :func:`_get_uia_focused_element` and passed to both
    :func:`_is_password_field` and :func:`_is_content_editable`.
    Previously each helper fetched the focused element separately
    (2x ``GetFocusedElement`` RPCs per paste).

     (Perf): ``CoInitialize`` / ``CoUninitialize`` are
    hoisted here to wrap both ``_is_password_field`` and
    ``_is_content_editable`` calls. Previously each helper did
    its own init/teardown (2x ``CoInitialize`` + 2x
    ``CoUninitialize`` per paste).

     (Perf): ``GetForegroundWindow`` is fetched ONCE at
    the top and passed to ``_is_elevated_target`` and
    ``_is_password_field`` (for the cred-dialog fallback).
    Previously each helper fetched it separately.
    """
    if not _cb.is_windows():
        #  (session-4): dispatch to platform-native password-field
        try:
            if _cb.is_macos():
                if _cb._is_password_field_macos():
                    _cb.log.info("[CLIPBOARD] Paste blocked, macOS password field is focused")
                    return False
                # macOS Secure Input guard. While Secure Input is active,
                if _cb._is_secure_input_enabled():
                    _cb.log.info("[CLIPBOARD] Paste blocked, macOS Secure Input is active")
                    return False
            elif _cb.is_linux():  # noqa: SIM102
                if _cb._is_password_field_linux():
                    _cb.log.info("[CLIPBOARD] Paste blocked. Linux password field is focused")
                    return False
        except Exception:
            # Password-state detection raised — fail closed.
            _cb.log.warning(
                "[CLIPBOARD] non-Windows password-field check raised, failing closed",
                exc_info=True,
            )
            return False
        return True
    try:
        import ctypes

        user32 = ctypes.windll.user32
        # Fetch hwnd ONCE and pass to all helpers (avoids redundant
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return True

        # Check window class name for security-sensitive windows
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        class_name = buf.value

        # Block UAC/consent dialogs and credential prompts.
        blocked_classes = {"Credential Dialog Xaml Host", "CredDialog"}
        if class_name in blocked_classes:
            _cb.log.warning("[CLIPBOARD] Blocked paste into security-sensitive window (class=%s)", class_name)
            return False

        #  (revised): Block paste if the target is elevated
        try:
            if _cb._is_elevated_target(hwnd):
                _cb.log.warning(
                    "[CLIPBOARD] Target window is elevated but we are not, blocking paste to avoid UIPI failure"
                )
                return False
        except Exception:
            _cb.log.warning(
                "[CLIPBOARD] _is_elevated_target check raised, blocking paste (fail-closed)",
                exc_info=True,
            )
            return False

        # 5: fetch the focused UIA element ONCE and hoist
        focused = None
        com_initialized = False
        # Pre-bind ``comtypes`` to None so the ``finally`` block
        comtypes: Any = None
        try:
            import comtypes

            comtypes.CoInitialize()
            com_initialized = True
            focused = _cb._get_uia_focused_element()
        except ImportError:
            # comtypes unavailable, _is_password_field will fall
            pass
        except Exception:
            # COM init or the focused-element query failed. _is_password_field
            # re-resolves the element itself and now fails CLOSED on an unknown
            # state, so proceeding with focused=None here is safe.
            _cb.log.debug(
                "[CLIPBOARD] focused-element fetch failed in _is_safe_paste_target "
                "(the password check re-resolves and fails closed)",
                exc_info=True,
            )

        try:
            # check if the focused element is a password field.
            try:
                if _cb._is_password_field(focused, hwnd):
                    return False
            except Exception:
                _cb.log.warning(
                    "[CLIPBOARD] _is_password_field check raised, blocking paste (fail-closed)",
                    exc_info=True,
                )
                return False

            # PLAT-CONTENT: check if the focused element is a
            try:
                if _cb._is_content_editable(focused):
                    _cb.log.info(
                        "[CLIPBOARD] Paste target is a contentEditable element, "
                        "pasting plain text (rich text formatting may be lost)"
                    )
            except Exception:
                _cb.log.debug("[CLIPBOARD] contentEditable check failed", exc_info=True)
        finally:
            if com_initialized:
                with contextlib.suppress(Exception):
                    # ``import comtypes as _ct`` was a defensive
                    comtypes.CoUninitialize()

        return True
    except (ImportError, AttributeError):
        # outer exception, fail-open ONLY for truly
        _cb.log.warning(
            "[CLIPBOARD] _is_safe_paste_target infra unavailable (ImportError/AttributeError), failing open",
            exc_info=True,
        )
        return True  # Fail open, don't block paste on outer infra error
    except Exception:
        # any OTHER exception here is security-relevant
        _cb.log.warning(
            "[CLIPBOARD] _is_safe_paste_target outer exception, failing CLOSED",
            exc_info=True,
        )
        return False


def _is_terminal_process_impl(process_name: str | None) -> bool:
    if not process_name:
        return False
    return process_name.lower().strip() in _cb._TERMINAL_PROCESS_NAMES


def _detect_focused_process_impl() -> str | None:
    """Detect the focused process name (Windows only)."""
    if not _cb.is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        process_query_limited_information = 0x1000
        h_process = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
        if not h_process:
            return None

        try:
            size = wintypes.DWORD(512)
            buf = ctypes.create_unicode_buffer(512)
            if kernel32.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(h_process)
    except (OSError, AttributeError):
        # narrowed from bare ``except Exception: pass``. The
        _cb.log.debug(
            "[CLIPBOARD] _detect_focused_process Win32 query failed",
            exc_info=True,
        )
    return None


def _get_frontmost_pid_macos_impl() -> int | None:
    """return the frontmost macOS app's PID for TOCTOU re-check.

    Uses ``AppKit.NSWorkspace`` (pyobjc) to fetch
    ``frontmostApplication().processIdentifier()``. Returns ``None``
    if pyobjc is unavailable, the workspace is unavailable, or the
    query raises. The caller treats ``None`` as "unknown, skip the
    TOCTOU re-check" (fail-open), preserving the legacy behavior on
    systems without pyobjc.
    """
    try:
        import AppKit  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        if workspace is None:
            return None
        front_app = workspace.frontmostApplication()
        if front_app is None:
            return None
        pid = front_app.processIdentifier()
        # pyobjc returns an NSInteger; coerce to plain int so the
        return int(pid) if pid is not None else None
    except Exception:
        _cb.log.debug(
            "[CLIPBOARD] _get_frontmost_pid_macos raised, skipping TOCTOU re-check",
            exc_info=True,
        )
        return None


__all__ = [
    "_detect_focused_process_impl",
    "_get_frontmost_pid_macos_impl",
    "_is_safe_paste_target_impl",
    "_is_terminal_process_impl",
]
