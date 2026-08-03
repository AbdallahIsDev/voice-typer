"""Safety validation rules for clipboard paste targets.

Contains the helpers that VALIDATE whether the focused target is a safe
paste destination:

* :func:`_warn_paste_safety_once` — one-shot WARNING / DEBUG dedup for
  per-paste safety-check failures (so a broken UIA install or AT-SPI
  bus doesn't spam the log at paste rate).
* :func:`reset_platform_unavailable_warnings` — test helper that resets
  the once-only warning guards for pyobjc / pyatspi unavailability
  (production code never calls this — the guards are intentionally
  sticky so a noisy startup doesn't flood the log).
* :func:`_ax_result_value` — extract the value from a pyobjc AX
  out-parameter tuple (consolidates three near-identical shape checks
  in :func:`_is_password_field_macos`).
* :func:`_is_password_field` — Windows password-field detection via
  UIA (``IsPasswordPropertyId``), with a credential-dialog heuristic
  fallback when comtypes is unavailable or the UIA call raises.
* :func:`_is_password_field_macos` — macOS password-field detection
  via pyobjc (Accessibility API: ``AXRole == "AXSecureTextField"`` or
  ``AXIsSecure == True``).
* :func:`_is_password_field_linux` — Linux password-field detection
  via AT-SPI2 (focused accessible with
  ``ATSPI_ROLE_PASSWORD_TEXT``).

All cross-module references (``is_windows``, ``_log``, mutable globals,
target-detection helpers in :mod:`.targets`, UIA helpers in
:mod:`.injection`) go through ``_pkg.NAME`` so test patches / resets on
``voice_typer.server.clipboard_target_safety.NAME`` propagate to the
functions defined here. A plain ``global NAME`` would write to THIS
submodule's namespace and be invisible to the test patches applied on
the package — hence the ``_pkg.NAME`` access pattern.
"""

from __future__ import annotations

import contextlib
from typing import Any  # noqa: F401  (used in type hints)

# ``_pkg`` is bound at module load time to the partial package object
# (``__init__.py`` is still executing when this submodule is loaded).
# Attribute lookups on ``_pkg`` happen at CALL TIME, by which point
# ``__init__.py`` has finished and all names are defined.
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
            "[CLIPBOARD] %s failed: %s — failing open (paste allowed); "
            "further occurrences of this failure will be logged at DEBUG",
            fn_name,
            exc,
            exc_info=True,
        )
    else:
        _pkg._log().debug(
            "[CLIPBOARD] %s failed: %s — failing open (paste allowed)",
            fn_name,
            exc,
            exc_info=True,
        )


def reset_platform_unavailable_warnings() -> None:
    """Reset the once-only warning guards (test helper).

    Production code never calls this — the guards are intentionally
    sticky so a noisy startup doesn't flood the log. Tests that want
    to assert the WARNING is emitted on the first call can call this
    between cases.
    """
    _pkg._PYOBJC_UNAVAILABLE_WARNED = False
    _pkg._PYATSPI_UNAVAILABLE_WARNED = False


def _ax_result_value(result: Any) -> Any:
    """Extract the value from a pyobjc AX out-parameter tuple.

    ``ApplicationServices.AXUIElementCopyAttributeValue`` returns a
    ``(OSStatus, value)`` tuple where ``OSStatus == 0`` signals success.
    Three near-identical 5-line shape checks in
    :func:`_is_password_field_macos` (for ``AXFocusedUIElement``,
    ``AXRole``, ``AXIsSecure``) each replicated the truthy / tuple /
    len>=2 / ``[0] == 0`` validation. This helper consolidates them so
    the validation logic lives in one place — the two later checks
    (``AXRole`` and ``AXIsSecure``) had already drifted semantically
    (they treated a non-tuple / short tuple as "not a password field"
    rather than failing closed); the unified helper makes the contract
    explicit.

    Returns the value (``result[1]``) on success, or ``None`` if the
    result is falsy, not a tuple, too short, or carries a non-zero
    ``OSStatus``. Callers that need to distinguish "API failed" from
    "API succeeded with a None value" should inspect the raw result
    themselves — every current caller treats both cases the same way.
    """
    if not result or not isinstance(result, tuple):
        return None
    if len(result) < 2 or result[0] != 0:
        return None
    return result[1]


def _is_password_field(focused: Any = None, hwnd: int | None = None) -> bool:
    """Check if the focused element is a password field.

    On Windows, uses UI Automation to check ``IsPasswordPropertyId``.
    If the focused element has ``IsPassword=True``, skip paste and warn
    the user that dictation into password fields is disabled for
    security. Returns ``True`` if a password field is detected,
    ``False`` otherwise.

    Parameters
    ----------
    focused :
        Pre-fetched UIA element (perf optimisation). When ``None`` the
        function fetches the focused element itself via
        :func:`_pkg._get_uia_focused_element` (in :mod:`.injection`)
        and manages the per-call COM apartment (``CoInitialize`` /
        ``CoUninitialize``).
    hwnd :
        Pre-fetched foreground window handle (perf optimisation). When
        the credential-dialog heuristic fallback fires, this avoids a
        redundant ``GetForegroundWindow`` call.

    History
    -------
    The docstring consolidates the per-ticket rationale that previously
    lived as multi-paragraph prose blocks inline in the docstring (and
    inline ``# CLIP-N:`` comments in the body). Inline body comments
    now describe only the *current* behavior; the ticket-level
    rationale lives here so a new maintainer can answer "why does this
    code look this way?" without replaying each historical fix.

    * Original implementation. No-op ctypes fallback silently failed
      open when comtypes was absent — now logs at WARNING and falls
      back to the credential-dialog window-class heuristic (fail-closed
      for known credential UI, fail-open otherwise to avoid blocking
      all dictation).
    * Module-level cached ``IUIAutomation`` singleton
      (``_pkg._UIA_SINGLETON``). The prior per-call
      ``CoCreateInstance`` was 10-50ms; caching eliminates that cost
      for every subsequent paste. The cached singleton is also reused
      by :func:`_is_content_editable` (in :mod:`.targets`).
    * (High, Security): when comtypes IS installed but the UIA call
      raises (desktop-bridge app, UAC dialog, broken COM registration),
      the prior code failed OPEN — paste was allowed into a potentially
      password-bearing field. Now ALSO calls
      :func:`_pkg._focused_window_is_credential_dialog` (in
      :mod:`.targets`) as a fallback; if the focused window class
      matches a known credential dialog, fail CLOSED (return True) so
      paste is blocked.
    * (Perf): ``focused`` parameter accepts a pre-fetched UIA element
      so callers (:meth:`ClipboardManager._is_safe_paste_target`)
      fetch it once and pass to both ``_is_password_field`` and
      ``_is_content_editable``. When ``focused`` is provided, the
      per-call ``CoInitialize`` / ``CoUninitialize`` is skipped (caller
      manages the COM apartment).
    * (Perf): ``hwnd`` parameter accepts a pre-fetched foreground
      window handle to avoid redundant ``GetForegroundWindow`` calls
      when invoking the credential-dialog heuristic fallback.
    * Per-branch fail-closed / fail-open semantics documented inline
      at each catch site (the outer try fails CLOSED; the inner
      comtypes-absence and UIA-error branches fail OPEN with the
      cred-dialog heuristic as the last resort).
    """
    if not _pkg.is_windows():
        return False
    try:
        # Try using comtypes for UI Automation (preferred path)
        try:
            import comtypes
            import comtypes.client

            # When called via _is_safe_paste_target with a pre-fetched
            # ``focused`` element, the caller has already CoInitialize'd
            # the thread. Skip the per-call init/teardown to avoid COM
            # ref-count churn on every paste.
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
                            "[CLIPBOARD] Password field detected — "
                            "dictation into password fields is disabled for security"
                        )
                        return True
            finally:
                if owns_com:
                    with contextlib.suppress(Exception):
                        comtypes.CoUninitialize()
        except ImportError:
            # comtypes not installed. Pre-fix this failed OPEN
            # (returned False → paste allowed into any field).
            # Now we log a WARNING (not INFO) so operators notice at
            # default log levels, and we fail CLOSED for known
            # credential-dialog window classes. The fail-closed path
            # only blocks when the focused window class matches a
            # known credential dialog (see _CRED_DIALOG_CLASSES); for
            # all other windows we still fail open to avoid blocking
            # legitimate dictation, but with a louder log.
            _pkg._log().warning(
                "[CLIPBOARD] comtypes not installed — password field detection "
                "disabled. Install 'comtypes' (pip install comtypes) to enable "
                "password field protection. Falling back to window-class heuristic."
            )
            # window-class heuristic for known credential
            # dialogs. This is a coarse fallback — it only catches the
            # standard Windows credential UI, not arbitrary password
            # fields in third-party apps. comtypes/UIA is required for
            # full coverage.
            try:
                if _pkg._focused_window_is_credential_dialog(hwnd):
                    _pkg._log().warning(
                        "[CLIPBOARD] Credential dialog window detected (comtypes "
                        "fallback) — dictation blocked for security"
                    )
                    return True
            except Exception as exc:
                # Wire the one-shot paste-safety warning so the
                # swallowed exception is visible (was silent ``pass``).
                # The function falls through to ``return False`` below
                # (fail-open) because the cred-dialog heuristic is the
                # last resort — we cannot positively identify a password
                # field, so we allow paste rather than block all
                # dictation when comtypes is merely absent.
                _warn_paste_safety_once(
                    "uia_password_cred_dialog_importerror_fallback",
                    "_is_password_field",
                    exc,
                )
        except Exception as exc:
            # (High, Security): comtypes is installed but the UIA call
            # raised (e.g. desktop-bridge app, UAC dialog, broken COM
            # registration). Previously this failed OPEN — paste was
            # allowed into a potentially password-bearing field with no
            # detection. Now we ALSO call the credential-dialog
            # window-class heuristic as a fallback; if the focused
            # window class matches a known credential dialog, fail
            # CLOSED (return True) so paste is blocked.
            _pkg._log().warning(
                "[CLIPBOARD] UIA password field check failed: %s — "
                "falling back to credential-dialog heuristic (CLIP-2)",
                exc,
            )
            try:
                if _pkg._focused_window_is_credential_dialog(hwnd):
                    _pkg._log().warning(
                        "[CLIPBOARD] Credential dialog window detected (UIA "
                        "failed) — dictation blocked for security (CLIP-2)"
                    )
                    return True
            except Exception as exc:
                # Wire the one-shot paste-safety warning so the
                # swallowed exception is visible (was silent ``pass``).
                # Falls through to ``return False`` (fail-open) for the
                # same reason as the ImportError branch above.
                _warn_paste_safety_once(
                    "uia_password_cred_dialog_uia_error_fallback",
                    "_is_password_field",
                    exc,
                )

        # No raw ctypes fallback: implementing IsPassword via raw ctypes
        # requires defining the full IUIAutomation COM interface vtable
        # by hand (80+ methods), which is fragile and error-prone.
        # comtypes is the supported way to call UIA from Python.
        return False
    except Exception as exc:
        # fail-closed — the outer try covers the whole password-field
        # detection path. If something unexpected broke it (not the
        # inner comtypes/UIA errors already handled above), block paste
        # rather than risk pasting into a credential prompt.
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
    (once) and returns ``False`` — the caller falls back to the
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
                "[CLIPBOARD] pyobjc (ApplicationServices/AppKit) not installed — "
                "macOS password field detection disabled. Install pyobjc "
                "(pip install pyobjc-framework-ApplicationServices "
                "pyobjc-framework-Cocoa) to enable password field protection. "
                "Falling back to fail-open (paste allowed)."
            )
            _pkg._PYOBJC_UNAVAILABLE_WARNED = True
        else:
            _pkg._log().debug("[CLIPBOARD] pyobjc not installed — macOS password field check skipped (already warned)")
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
            # fail-closed — if we cannot read the frontmost app's
            # PID, we cannot query its AX tree, so we cannot verify the
            # target is safe. Block paste.
            _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        if pid is None or pid <= 0:
            return False

        app_elem = ApplicationServices.AXUIElementCreateApplication(pid)
        if app_elem is None:
            return False

        # Get the focused UI element within the app.
        # AXUIElementCopyAttributeValue signature:
        #   (element, attribute, value out) -> OSStatus
        # pyobjc returns (OSStatus, value) tuple for the out-param.
        try:
            focused_result = ApplicationServices.AXUIElementCopyAttributeValue(app_elem, "AXFocusedUIElement", None)
        except Exception as exc:
            # fail-closed — if we cannot fetch the focused UI
            # element, we cannot check whether it is a password field.
            # Block paste.
            _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        # Consolidated AX-tuple shape check.
        focused = _ax_result_value(focused_result)
        if focused is None:
            return False

        # Check role: "AXSecureTextField" is the canonical macOS
        # password text field role.
        try:
            role_result = ApplicationServices.AXUIElementCopyAttributeValue(focused, "AXRole", None)
        except Exception as e:
            _pkg._log().debug("macOS AXRole fetch failed for focused element: %s", e, exc_info=True)
            role_result = None
        # Consolidated AX-tuple shape check.
        if _ax_result_value(role_result) == "AXSecureTextField":
            _pkg._log().warning(
                "[CLIPBOARD] macOS password field detected (AXSecureTextField) — "
                "dictation into password fields is disabled for security"
            )
            return True

        # Also check the AXIsSecure attribute (covers custom controls
        # that hide password input behind a non-standard role).
        try:
            secure_result = ApplicationServices.AXUIElementCopyAttributeValue(focused, "AXIsSecure", None)
        except Exception as e:
            _pkg._log().debug("macOS AXIsSecure fetch failed: %s", e, exc_info=True)
            secure_result = None
        # Consolidated AX-tuple shape check.
        if bool(_ax_result_value(secure_result)):
            _pkg._log().warning(
                "[CLIPBOARD] macOS password field detected (AXIsSecure=True) — "
                "dictation into password fields is disabled for security"
            )
            return True

        return False
    except Exception as exc:
        # Surface a one-shot WARNING (deduped via _warn_paste_safety_once)
        # so a degraded macOS AX infrastructure is visible to operators
        # instead of silently failing open at DEBUG.
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
    WARNING (once) and returns ``False`` — the caller falls back to
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
                "[CLIPBOARD] pyatspi not installed — Linux password field "
                "detection disabled. Install pyatspi (pip install pyatspi) "
                "or your distro's equivalent (apt install python3-pyatspi) "
                "to enable password field protection. Falling back to "
                "fail-open (paste allowed)."
            )
            _pkg._PYATSPI_UNAVAILABLE_WARNED = True
        else:
            _pkg._log().debug("[CLIPBOARD] pyatspi not installed — Linux password field check skipped (already warned)")
        return False

    # Resolve ``pyatspi.STATE_FOCUSED`` ONCE per call (the attribute
    # lookup is cheap — a single dict access on the pyatspi module) and
    # pass it explicitly to ``_find_focused_atspi_accessible`` (in
    # :mod:`.targets`) as a parameter. Replaces the prior pattern where
    # the module-level ``_PYATSPI_STATE_FOCUSED`` global was READ inside
    # the tree-walk helper — that hidden cross-function coupling meant
    # the helper would raise ``TypeError`` if called before this
    # function had initialized the global. The global is still SET (as
    # a backward-compat sentinel for external code that inspects /
    # patches it), but it is no longer READ by the helper.
    #
    # The defensive fallback chain (try the canonical attribute, then
    # retry, then use ``1 << 10``) is preserved verbatim.
    try:
        state_focused = pyatspi.STATE_FOCUSED
    except AttributeError:
        # Fallback for older pyatspi versions that may not expose STATE_FOCUSED.
        state_focused = 1 << 10  # fallback value
    # Backward-compat: mirror the resolved value into the module-level
    # global so external code (re-exports in ``clipboard/__init__.py``,
    # test patches in ``test_clipboard_password_detection.py``) that
    # reads or resets ``_PYATSPI_STATE_FOCUSED`` keeps working. The
    # tree-walk helper does NOT read this — it uses the local
    # ``state_focused`` parameter.
    _pkg._PYATSPI_STATE_FOCUSED = state_focused

    try:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception as exc:
            # fail-closed — if the AT-SPI2 desktop is unavailable, we
            # cannot traverse the accessibility tree to find the focused
            # element. Block paste.
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
            # fail-closed — if we cannot read the focused accessible's
            # role, we cannot determine whether it is a password field.
            # Block paste.
            _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
            return True

        try:
            password_role = pyatspi.ROLE_PASSWORD_TEXT
        except AttributeError:
            password_role = None
        if password_role is not None and role == password_role:
            _pkg._log().warning(
                "[CLIPBOARD] Linux password field detected "
                "(ATSPI_ROLE_PASSWORD_TEXT) — dictation into password "
                "fields is disabled for security"
            )
            return True

        return False
    except Exception as exc:
        # Surface a one-shot WARNING (deduped via _warn_paste_safety_once)
        # so a degraded Linux AT-SPI2 infrastructure is visible to
        # operators instead of silently failing open at DEBUG.
        _warn_paste_safety_once(
            "linux_atspi_outer_exception",
            "_is_password_field_linux",
            exc,
        )
        return False
