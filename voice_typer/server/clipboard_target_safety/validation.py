"""Safety validation rules for clipboard paste targets.

Contains the helpers that VALIDATE whether the focused target is a safe
paste destination:

* :func:`_warn_paste_safety_once`: one-shot WARNING / DEBUG dedup for
  per-paste safety-check failures (so a broken UIA install or AT-SPI
  bus doesn't spam the log at paste rate).
* :func:`reset_platform_unavailable_warnings`: test helper that resets
  the once-only warning guards for pyobjc / pyatspi unavailability
  (production code never calls this, the guards are intentionally
  sticky so a noisy startup doesn't flood the log).
* :func:`_ax_result_value`: extract the value from a pyobjc AX
  out-parameter tuple (consolidates three near-identical shape checks
  in :func:`_is_password_field_macos`).
* :func:`_is_password_field`: Windows password-field detection via
  UIA (``IsPasswordPropertyId``), with a credential-dialog heuristic
  fallback when comtypes is unavailable or the UIA call raises.
* :func:`_is_password_field_macos`: macOS password-field detection
  via pyobjc (Accessibility API: ``AXRole == "AXSecureTextField"`` or
  ``AXIsSecure == True``).
* :func:`_is_password_field_linux`: Linux password-field detection
  via AT-SPI2 (focused accessible with
  ``ATSPI_ROLE_PASSWORD_TEXT``).

All cross-module references (``is_windows``, ``_log``, mutable globals,
target-detection helpers in :mod:`.targets`, UIA helpers in
:mod:`.injection`) go through ``_pkg.NAME`` so test patches / resets on
``voice_typer.server.clipboard_target_safety.NAME`` propagate to the
functions defined here. A plain ``global NAME`` would write to THIS
submodule's namespace and be invisible to the test patches applied on
the package, hence the ``_pkg.NAME`` access pattern.
"""

from __future__ import annotations

import contextlib
from typing import Any  # noqa: F401  (used in type hints)

# Call-time _pkg lookups (C-ARCH-2): package is partial at module load.
import voice_typer.server.clipboard_target_safety as _pkg


def _warn_paste_safety_once(key: str, fn_name: str, exc: BaseException) -> None:
    """Emit a one-shot DEBUG log for a per-paste safety-check failure.

    The first time ``key`` is seen in this process we also bump it to
    WARNING so the operator notices; subsequent failures of the same
    kind are logged at DEBUG (with traceback) for forensic value without
    spamming the log at paste rate.
    """
    if key not in _pkg._PASTE_SAFETY_WARNED:
        _pkg._PASTE_SAFETY_WARNED.add(key)
        _pkg._log().warning(
            "[CLIPBOARD] %s failed: %s, failing open (paste allowed); "
            "further occurrences of this failure will be logged at DEBUG",
            fn_name,
            exc,
            exc_info=True,
        )
    else:
        _pkg._log().debug(
            "[CLIPBOARD] %s failed: %s, failing open (paste allowed)",
            fn_name,
            exc,
            exc_info=True,
        )


def reset_platform_unavailable_warnings() -> None:
    """Reset the once-only warning guards (test helper)."""
    _pkg._PYOBJC_UNAVAILABLE_WARNED = False
    _pkg._PYATSPI_UNAVAILABLE_WARNED = False


def _ax_result_value(result: Any) -> Any:
    """Extract the value from a pyobjc AX out-parameter tuple."""
    if not result or not isinstance(result, tuple):
        return None
    if len(result) < 2 or result[0] != 0:
        return None
    return result[1]


def _is_password_field(focused: Any = None, hwnd: int | None = None) -> bool:
    """Check if the focused element is a password field."""
    if not _pkg.is_windows():
        return False
    try:
        # Try using comtypes for UI Automation (preferred path)
        try:
            import comtypes
            import comtypes.client

            # When called via _is_safe_paste_target with a pre-fetched
            owns_com = focused is None
            if owns_com:
                comtypes.CoInitialize()
            try:
                if focused is None:
                    focused = _pkg._get_uia_focused_element()
                if focused is not None:
                    # UIA_IsPasswordPropertyId = 30022
                    is_password = focused.GetCurrentPropertyValue(30022)
                    if is_password:
                        _pkg._log().warning(
                            "[CLIPBOARD] Password field detected, "
                            "dictation into password fields is disabled for security"
                        )
                        return True
            finally:
                if owns_com:
                    with contextlib.suppress(Exception):
                        comtypes.CoUninitialize()
        except ImportError:
            # No comtypes: fail CLOSED for known credential dialogs; else open + WARNING.
            _pkg._log().warning(
                "[CLIPBOARD] comtypes not installed, password field detection "
                "disabled. Install 'comtypes' (pip install comtypes) to enable "
                "password field protection. Falling back to window-class heuristic."
            )
            # window-class heuristic for known credential
            try:
                if _pkg._focused_window_is_credential_dialog(hwnd):
                    _pkg._log().warning(
                        "[CLIPBOARD] Credential dialog window detected (comtypes "
                        "fallback), dictation blocked for security"
                    )
                    return True
            except Exception as exc:
                # Wire the one-shot paste-safety warning so the
                _warn_paste_safety_once(
                    "uia_password_cred_dialog_importerror_fallback",
                    "_is_password_field",
                    exc,
                )
        except Exception as exc:
            # UIA raised: fall back to credential-dialog class heuristic (fail closed if match).
            _pkg._log().warning(
                "[CLIPBOARD] UIA password field check failed: %s, falling back to credential-dialog heuristic (CLIP-2)",
                exc,
            )
            try:
                if _pkg._focused_window_is_credential_dialog(hwnd):
                    _pkg._log().warning(
                        "[CLIPBOARD] Credential dialog window detected (UIA "
                        "failed), dictation blocked for security (CLIP-2)"
                    )
                    return True
            except Exception as exc:
                # Wire the one-shot paste-safety warning so the
                _warn_paste_safety_once(
                    "uia_password_cred_dialog_uia_error_fallback",
                    "_is_password_field",
                    exc,
                )

        # No raw ctypes fallback: implementing IsPassword via raw ctypes
        return False
    except Exception as exc:
        # fail-closed, the outer try covers the whole password-field
        _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _is_password_field_macos() -> bool:
    """Detect macOS password fields via the Accessibility API.

    Uses ``pyobjc`` (``AppKit.NSWorkspace`` + ``ApplicationServices``)
    to query the focused UI element of the frontmost application:

      1. ``NSWorkspace.sharedWorkspace().frontmostApplication()`` →
         the frontmost ``NSRunningApplication``.
      2. ``AXUIElementCreateApplication(pid)`` → the AXUIElement for
         that app.
      3. ``AXUIElementCopyAttributeValue(app, "AXFocusedUIElement")``
         → the focused UI element.
      4. Check the element's ``AXRole`` (``AXSecureTextField`` ⇒
         password field) and the ``AXIsSecure`` attribute (``True`` ⇒
         password field, covers custom controls).

    Returns ``True`` if a password field is detected (paste should be
    blocked), ``False`` otherwise.

    Lazy import: if ``pyobjc`` is not installed (Linux/Windows hosts
    or a headless macOS without the AppKit bridge), logs a WARNING
    (once) and returns ``False``: the caller falls back to the
    legacy fail-open behavior of allowing paste. Residual risk:
    dictated text can still be pasted into macOS password fields
    until ``pyobjc`` is installed.

    Exceptions from the AX API (broken accessibility permission, app
    doesn't expose AX tree, etc.) are caught and logged at DEBUG —
    fail-open to avoid blocking legitimate dictation when the AX
    infrastructure is degraded.
    """
    try:
        import AppKit  # noqa: F401
        import ApplicationServices  # noqa: F401
    except ImportError:
        if not _pkg._PYOBJC_UNAVAILABLE_WARNED:
            _pkg._log().warning(
                "[CLIPBOARD] pyobjc (ApplicationServices/AppKit) not installed, "
                "macOS password field detection disabled. Install pyobjc "
                "(pip install pyobjc-framework-ApplicationServices "
                "pyobjc-framework-Cocoa) to enable password field protection. "
                "Falling back to fail-open (paste allowed)."
            )
            _pkg._PYOBJC_UNAVAILABLE_WARNED = True
        else:
            _pkg._log().debug("[CLIPBOARD] pyobjc not installed, macOS password field check skipped (already warned)")
        return False

    try:
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        if workspace is None:
            return False
        front_app = workspace.frontmostApplication()
        if front_app is None:
            return False
        try:
            pid = front_app.processIdentifier()
        except Exception as exc:
            # fail-closed, if we cannot read the frontmost app's
            _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        if pid is None or pid <= 0:
            return False

        app_elem = ApplicationServices.AXUIElementCreateApplication(pid)
        if app_elem is None:
            return False

        # Get the focused UI element within the app.
        try:
            focused_result = ApplicationServices.AXUIElementCopyAttributeValue(app_elem, "AXFocusedUIElement", None)
        except Exception as exc:
            # fail-closed, if we cannot fetch the focused UI
            _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        # Consolidated AX-tuple shape check.
        focused = _ax_result_value(focused_result)
        if focused is None:
            return False

        # Check role: "AXSecureTextField" is the canonical macOS
        try:
            role_result = ApplicationServices.AXUIElementCopyAttributeValue(focused, "AXRole", None)
        except Exception as e:
            _pkg._log().debug("macOS AXRole fetch failed for focused element: %s", e, exc_info=True)
            role_result = None
        # Consolidated AX-tuple shape check.
        if _ax_result_value(role_result) == "AXSecureTextField":
            _pkg._log().warning(
                "[CLIPBOARD] macOS password field detected (AXSecureTextField), "
                "dictation into password fields is disabled for security"
            )
            return True

        # Also check the AXIsSecure attribute (covers custom controls
        try:
            secure_result = ApplicationServices.AXUIElementCopyAttributeValue(focused, "AXIsSecure", None)
        except Exception as e:
            _pkg._log().debug("macOS AXIsSecure fetch failed: %s", e, exc_info=True)
            secure_result = None
        # Consolidated AX-tuple shape check.
        if bool(_ax_result_value(secure_result)):
            _pkg._log().warning(
                "[CLIPBOARD] macOS password field detected (AXIsSecure=True), "
                "dictation into password fields is disabled for security"
            )
            return True

        return False
    except Exception as exc:
        # Surface a one-shot WARNING (deduped via _warn_paste_safety_once)
        _warn_paste_safety_once(
            "macos_ax_outer_exception",
            "_is_password_field_macos",
            exc,
        )
        return False


def _is_password_field_linux() -> bool:
    """Detect Linux password fields via AT-SPI2.

    Uses ``pyatspi`` to query the focused accessible. The traversal:

      1. ``pyatspi.Registry.getDesktop(0)`` → the AT-SPI desktop.
      2. Walk down through children that have ``ATSPI_STATE_FOCUSED``
         set, descending until no child has the focused state. The leaf
         at that point is the focused UI element.
      3. If the leaf's role is ``ATSPI_ROLE_PASSWORD_TEXT``, treat it
         as a password field.

    Returns ``True`` if a password field is detected (paste should be
    blocked), ``False`` otherwise.

    Lazy import: if ``pyatspi`` is not installed (Linux hosts without
    the AT-SPI2 Python bindings, or non-Linux platforms), logs a
    WARNING (once) and returns ``False``: the caller falls back to
    the legacy fail-open behavior of allowing paste. Residual risk:
    dictated text can still be pasted into Linux password fields until
    ``pyatspi`` is installed (``pip install pyatspi`` or ``apt install
    python3-pyatspi``).

    Exceptions from AT-SPI2 (no desktop bus, broken registry, app that
    doesn't expose an accessible tree) are caught and logged at DEBUG —
    fail-open to avoid blocking legitimate dictation when the AT-SPI2
    infrastructure is degraded (e.g. raw framebuffer apps, headless
    sessions).
    """
    try:
        import pyatspi
    except ImportError:
        if not _pkg._PYATSPI_UNAVAILABLE_WARNED:
            _pkg._log().warning(
                "[CLIPBOARD] pyatspi not installed. Linux password field "
                "detection disabled. Install pyatspi (pip install pyatspi) "
                "or your distro's equivalent (apt install python3-pyatspi) "
                "to enable password field protection. Falling back to "
                "fail-open (paste allowed)."
            )
            _pkg._PYATSPI_UNAVAILABLE_WARNED = True
        else:
            _pkg._log().debug("[CLIPBOARD] pyatspi not installed. Linux password field check skipped (already warned)")
        return False

    # The defensive fallback chain (try the canonical attribute, then
    try:
        state_focused = pyatspi.STATE_FOCUSED
    except AttributeError:
        # Fallback for older pyatspi versions that may not expose STATE_FOCUSED.
        state_focused = 1 << 10  # fallback value
    # Backward-compat: mirror the resolved value into the module-level
    _pkg._PYATSPI_STATE_FOCUSED = state_focused

    try:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception as exc:
            # fail-closed, if the AT-SPI2 desktop is unavailable, we
            _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        if desktop is None:
            return False

        focused = _pkg._find_focused_atspi_accessible(desktop, state_focused, max_depth=10)
        if focused is None:
            return False

        try:
            role = focused.getRole()
        except Exception as exc:
            # fail-closed, if we cannot read the focused accessible's
            _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
            return True

        try:
            password_role = pyatspi.ROLE_PASSWORD_TEXT
        except AttributeError:
            password_role = None
        if password_role is not None and role == password_role:
            _pkg._log().warning(
                "[CLIPBOARD] Linux password field detected "
                "(ATSPI_ROLE_PASSWORD_TEXT), dictation into password "
                "fields is disabled for security"
            )
            return True

        return False
    except Exception as exc:
        # Surface a one-shot WARNING (deduped via _warn_paste_safety_once)
        _warn_paste_safety_once(
            "linux_atspi_outer_exception",
            "_is_password_field_linux",
            exc,
        )
        return False


# Once-only warning guard for the macOS secure-input check below. Lives


def _is_secure_input_enabled() -> bool:
    """Detect macOS "Secure Input" mode (Medium, Security).

    On macOS, an application can call ``EnableSecureEventInput()`` to
    put the system into Secure Input mode while a password field is
    focused. While Secure Input is active, the keyboard input stream
    is encrypted end-to-end (Kernel → event taps are disabled, HID
    monitors cannot observe keystrokes, and **synthesized keystrokes
    via ``CGEventPost`` / AppleScript / pynput are silently dropped**).
    A paste keystroke sent via pynput's Controller (the macOS backend
    posts CGEvents) is therefore lost, the user sees nothing happen
    and the dictated text never reaches the target.

    Detection options considered:

      1. ``proc_pidinfo(getpid(), PROC_PIDT_SHORTINFO, ...)`` and
         inspect ``P_SHORTINFO_SECUREINPUT``. This is the canonical
         per-process query but requires declaring the
         ``proc_bsdshortinfo`` struct via ``ctypes`` (8 fields, careful
         padding), fragile against macOS SDK changes.
      2. ``ioreg -l -w 0 | grep SecureInput``. ``ioreg`` reports the
         IORegistry's ``kIOUserClientClass`` entries; when Secure Input
         is active, the IOHIKeyboard instance reports
         ``SecureInput`` = ``true`` in its properties. This is the
         user-space observable shell-out the Apple docs recommend and
         is robust against SDK churn.

    We use option 2 (shell-out to ``ioreg``) for robustness. The
    command is invoked via ``subprocess.run`` with a 2-second timeout
    so a wedged ``ioreg`` cannot block the paste path. The output is
    grepped for ``SecureInput`` (case-sensitive, the IORegistry key
    is camelCased); a non-empty match means Secure Input is active.

    Returns ``True`` if Secure Input is active (paste should be
    skipped, the keystroke would be dropped). Returns ``False`` on
    non-macOS, on subprocess failure, or when Secure Input is not
    active. The first positive detection in a session logs a WARNING
    (deduped via ``_pkg._MACOS_SECURE_INPUT_WARNED``) and publishes a
    ``paste_deferred`` event via ``event_bus`` so the renderer can
    surface a tray toast; subsequent detections during the same
    session log at DEBUG (so the log is not flooded at paste rate
    when the user keeps a password dialog open).
    """
    if not _pkg.is_macos():
        # ``ioreg`` is a macOS-only binary; on Linux / Windows the
        return False
    import subprocess

    try:
        # ``-l`` = show properties; ``-w 0`` = no line wrapping (so
        proc = subprocess.run(
            ["ioreg", "-l", "-w", "0"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # FileNotFoundError: ``ioreg`` missing (sandboxed / stripped
        _pkg._log().debug(
            "[CLIPBOARD] ioreg probe for Secure Input failed, failing open",
            exc_info=True,
        )
        return False

    if proc.returncode != 0:
        _pkg._log().debug(
            "[CLIPBOARD] ioreg returned %d, cannot determine Secure Input state",
            proc.returncode,
        )
        return False

    # The IORegistry key is camelCased ``SecureInput``. Match
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if "SecureInput" not in stdout:
        return False

    # Secure Input is active. Log WARNING once per session (deduped)
    if not _pkg._MACOS_SECURE_INPUT_WARNED:
        _pkg._MACOS_SECURE_INPUT_WARNED = True
        _pkg._log().warning("[CLIPBOARD] Paste target has Secure Input enabled, synthesized keystroke was dropped")
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "paste_deferred",
                    "data": {
                        # ``reason`` only. The renderer maps it to a
                        "reason": "secure_input",
                    },
                }
            )
        except Exception:
            _pkg._log().debug(
                "[CLIPBOARD] could not publish paste_deferred (secure_input) event",
                exc_info=True,
            )
    else:
        _pkg._log().debug(
            "[CLIPBOARD] Paste target has Secure Input enabled, keystroke dropped (already warned this session)"
        )
    return True
