"""Target window detection for clipboard paste safety.

Contains the helpers that DETECT the foreground window's identity /
properties:

* :func:`_get_we_elevated` — cached "are WE elevated?" check (process-
  stable; computed once via Win32 token query, cached in
  ``_pkg._WE_ELEVATED``).
* :func:`_is_elevated_target` — checks if the foreground window belongs
  to an elevated process (uses :func:`_pkg._get_we_elevated` for the
  "we" side and a fresh Win32 token query for the "target" side).
* :func:`_focused_window_is_credential_dialog` — checks the foreground
  window's class name against ``_pkg._CRED_DIALOG_CLASSES`` (coarse
  comtypes-absence fallback for password-field protection).
* :func:`_is_content_editable` — uses UIA (via
  :func:`_pkg._get_uia_focused_element` from :mod:`.injection`) to check
  if the focused element supports rich-text input.
* :func:`_find_focused_atspi_accessible` — walks the Linux AT-SPI tree
  to find the focused accessible (used by ``_is_password_field_linux``
  in :mod:`.validation`).

All cross-module references (``is_windows``, ``_log``, mutable globals,
UIA helpers in :mod:`.injection`, paste-safety warning helper in
:mod:`.validation`) go through ``_pkg.NAME`` (where ``_pkg`` is this
package) so test patches / resets on
``voice_typer.server.clipboard_target_safety.NAME`` propagate to the
functions defined here. A plain ``global NAME`` would write to THIS
submodule's namespace and be invisible to the test patches applied on
the package — hence the ``_pkg.NAME`` access pattern.
"""

from __future__ import annotations

import sys
import time
from typing import Any  # noqa: F401  (used in type hints)

# ``_pkg`` is bound at module load time to the partial package object
# (``__init__.py`` is still executing when this submodule is loaded).
# Attribute lookups on ``_pkg`` happen at CALL TIME, by which point
# ``__init__.py`` has finished and all names (mutable globals + the
# re-exported functions from .injection and .validation) are defined.
# This mirrors the ``_cb`` pattern used in
# ``voice_typer/server/clipboard/manager.py``.
import voice_typer.server.clipboard_target_safety as _pkg


def _get_we_elevated() -> bool:
    """Return whether THIS process is running elevated.

    Cached at module level (in ``_pkg._WE_ELEVATED``) because the value
    cannot change during the process lifetime (you can't elevate an
    already-running process). The first call performs the Win32 token
    query; subsequent calls return the cached value.

    Returns False on non-Windows, on failure, or when we cannot open
    our own process token (fail-open — same as the previous behavior).

    Init is guarded by ``_pkg._WE_ELEVATED_LOCK`` using double-checked
    locking so concurrent first-callers (e.g. the main IPC paste thread
    plus the caps-lock polling thread) don't both run the Win32 token
    query and stomp the cache. The fast path (cache hit) is lock-free;
    only the cold path acquires the lock.
    """
    # Fast path: cache already populated — no lock needed.
    if _pkg._WE_ELEVATED is not None:
        return _pkg._WE_ELEVATED
    # Cold path: acquire the lock and re-check (another thread may
    # have populated the cache while we were waiting).
    with _pkg._WE_ELEVATED_LOCK:
        if _pkg._WE_ELEVATED is not None:
            return _pkg._WE_ELEVATED
        if not _pkg.is_windows():
            _pkg._WE_ELEVATED = False
            return _pkg._WE_ELEVATED
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32

            our_token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(our_token)):
                _pkg._WE_ELEVATED = False
                return _pkg._WE_ELEVATED
            try:
                # TokenElevation = 20
                ret_len = wintypes.DWORD()
                advapi32.GetTokenInformation(our_token, 20, None, 0, ctypes.byref(ret_len))
                our_buf = ctypes.create_string_buffer(ret_len.value or 4)
                if not advapi32.GetTokenInformation(
                    our_token, 20, our_buf, ctypes.sizeof(our_buf), ctypes.byref(ret_len)
                ):
                    _pkg._WE_ELEVATED = False
                    return _pkg._WE_ELEVATED
                _pkg._WE_ELEVATED = bool(ctypes.cast(our_buf, ctypes.POINTER(wintypes.DWORD))[0])
            finally:
                kernel32.CloseHandle(our_token)
        except Exception as exc:
            # Cached and called once per process, so a plain WARNING
            # (no dedup) is appropriate — operators get exactly one
            # record per session if the Win32 token query path is
            # broken.
            _pkg._log().warning(
                "[CLIPBOARD] _get_we_elevated failed: %s — failing open (paste allowed)",
                exc,
                exc_info=True,
            )
            _pkg._WE_ELEVATED = False
        return _pkg._WE_ELEVATED


def _is_elevated_target(hwnd: int | None = None) -> bool:
    """Check if the foreground window belongs to an elevated process.

    Uses GetWindowThreadProcessId + OpenProcess + GetTokenInformation to
    determine if the target process is running elevated. If we are not
    elevated but the target is, UIPI will block our SendInput calls.

    Returns True if the foreground window is elevated and we are not.
    Returns False if we can't determine (fail open) or if elevation
    matches.

    The "are we elevated?" check is cached at module-level via
    :func:`_pkg._get_we_elevated`. The value cannot change during the
    process lifetime, so we compute it once on first paste and reuse on
    every subsequent paste. The target-side check still runs per-paste
    because the foreground window can change between pastes.

    ``hwnd`` parameter accepts a pre-fetched foreground window handle so
    callers (:meth:`ClipboardManager._is_safe_paste_target`) that
    already fetched ``GetForegroundWindow`` can pass it in to avoid a
    redundant Win32 round-trip. When ``hwnd`` is ``None`` (the
    default), the function fetches it itself — preserving backward
    compatibility with direct callers and tests.
    """
    if not _pkg.is_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        if hwnd is None:
            hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False

        process_query_limited_information = 0x1000
        h_process = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
        if not h_process:
            return False

        try:
            # Check if the target process is elevated
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h_process, 0x0008, ctypes.byref(token)):
                return False
            try:
                # TokenElevation = 20
                ret_len = wintypes.DWORD()
                advapi32.GetTokenInformation(token, 20, None, 0, ctypes.byref(ret_len))
                buf = ctypes.create_string_buffer(ret_len.value or 4)
                if not advapi32.GetTokenInformation(token, 20, buf, ctypes.sizeof(buf), ctypes.byref(ret_len)):
                    return False
                # TOKEN_ELEVATION struct: DWORD TokenIsElevated
                target_elevated = bool(ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0])
            finally:
                kernel32.CloseHandle(token)

            # Check if WE are elevated (cached, computed once).
            we_elevated = _pkg._get_we_elevated()

            # If target is elevated and we're not, warn
            if target_elevated and not we_elevated:
                _pkg._log().warning(
                    "[CLIPBOARD] Target window (pid=%d) is elevated but we are not — paste may fail due to UIPI",
                    pid.value,
                )
                return True
            return False
        finally:
            kernel32.CloseHandle(h_process)
    except Exception as exc:
        # fail-closed — if the elevation check itself raises,
        # block paste rather than risk pasting into an elevated target
        # we couldn't verify.
        _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _focused_window_is_credential_dialog(hwnd: int | None = None) -> bool:
    """Check if the focused window is a known credential dialog.

    Uses GetForegroundWindow + GetClassNameW via ctypes. Returns True
    if the window class matches a known credential dialog class. This
    is the comtypes-absence fallback for password field protection.

    ``hwnd`` parameter accepts a pre-fetched foreground window handle so
    callers (like :meth:`ClipboardManager._is_safe_paste_target`) that
    already fetched ``GetForegroundWindow`` can pass it in to avoid a
    redundant Win32 round-trip. When ``hwnd`` is ``None`` (the
    default), the function fetches it itself — preserving backward
    compatibility with direct callers and tests.
    """
    if not _pkg.is_windows():
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if hwnd is None:
            hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        # GetClassNameW returns the class name length
        class_name = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, class_name, 256)
        if length <= 0:
            return False
        cls = class_name.value
        return cls in _pkg._CRED_DIALOG_CLASSES
    except Exception as exc:
        # fail-closed — if the credential-dialog check raises,
        # block paste rather than risk pasting into an undetected
        # credential prompt.
        _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _is_content_editable(focused: Any = None) -> bool:
    """Check if the focused element is a contentEditable element.

    On Windows, uses UI Automation (same comtypes infrastructure as
    :func:`_is_password_field` in :mod:`.validation`) to check if the
    focused element is an Edit or Document control that supports rich
    text input. This allows the clipboard module to log when the paste
    target is a rich editor (Word, LibreOffice, Gmail compose) and
    potentially paste HTML in a future version.

    Returns True if the focused element is contentEditable, False
    otherwise. Returns False on non-Windows or when comtypes is
    unavailable.

    ``focused`` parameter accepts a pre-fetched UIA element so callers
    (:meth:`ClipboardManager._is_safe_paste_target`) can fetch it once
    and pass to both :func:`_is_password_field` (in :mod:`.validation`)
    and this function. When ``focused`` is provided, the per-call
    ``CoInitialize``/``CoUninitialize`` is skipped (caller is expected
    to manage the COM apartment).
    """
    if not _pkg.is_windows():
        return False
    try:
        import comtypes

        # Skip per-call COM init when caller pre-fetched the element.
        owns_com = focused is None
        if owns_com:
            comtypes.CoInitialize()
        try:
            if focused is None:
                focused = _pkg._get_uia_focused_element()
            if focused is None:
                return False
            # UIA_ControlTypePropertyId = 30003
            control_type = focused.GetCurrentPropertyValue(30003)
            # UIA_ControlType_Edit = 50004, UIA_ControlType_Document = 50036
            # These are the control types that typically support contentEditable.
            if control_type in (50004, 50036):
                # Check if the element supports the Value pattern (text input)
                # UIA_IsValuePatternAvailablePropertyId = 30101
                has_value = focused.GetCurrentPropertyValue(30101)
                if has_value:
                    return True
            return False
        finally:
            if owns_com:
                import contextlib

                with contextlib.suppress(Exception):
                    comtypes.CoUninitialize()
    except ImportError:
        return False
    except Exception as exc:
        # ``_is_content_editable`` is informational only (the
        # caller logs but does NOT block paste on True — see
        # ``_is_safe_paste_target`` docstring). Fail-OPEN here
        # is correct: returning True would falsely report a rich-editor
        # target without improving security. We still log the failure
        # so a broken UIA install is observable.
        _pkg._log().warning(
            "paste-safety check failed; keeping fail-open (contentEditable is informational, not a security gate): %s",
            exc,
        )
        return False


def _find_focused_atspi_accessible(
    desktop: Any,
    state_focused: Any,
    max_depth: int = 10,
) -> Any:
    """Walk the AT-SPI tree to find the focused accessible.

    The AT-SPI spec says the ``ATSPI_STATE_FOCUSED`` state is set on
    the actual UI element receiving keyboard input — NOT on its
    ancestors (which may have ``ATSPI_STATE_ACTIVE`` / ``SHOWING`` but
    not ``FOCUSED``). This helper does a depth-first traversal of the
    tree, returning the first accessible found with ``FOCUSED`` set.

    A depth limit prevents infinite loops on malformed accessibility
    trees (e.g. an app that reports itself as its own parent) and
    bounds the worst-case traversal time on huge trees (some apps
    expose thousands of accessibles).

    ``state_focused`` is a REQUIRED parameter (was a module-level
    ``_PYATSPI_STATE_FOCUSED`` global written by
    ``_is_password_field_linux`` and read here). The caller resolves
    ``pyatspi.STATE_FOCUSED`` once at the top of
    ``_is_password_field_linux`` (in :mod:`.validation`) and passes it
    down — eliminates the hidden cross-function coupling via the module
    global, and removes the latent ``TypeError`` if
    ``_find_focused_atspi_accessible`` was ever called before the global
    had been initialized (the global defaulted to ``None`` and
    ``root_state.contains(None)`` raised).

    Returns ``None`` when no focused accessible is found within the
    depth limit, or when the AT-SPI tree is malformed.

    This function caches ``(role, timestamp)`` for
    ``_ATSPI_FOCUSED_ROLE_CACHE_WINDOW_S`` (200 ms) after each
    successful walk + ``getRole()`` extraction. On a subsequent call
    within the cache window, the cache hit short-circuits the O(N)
    tree walk (each ``getChildAtIndex`` is a D-Bus RPC, so a busy
    desktop with 5,000-20,000 accessibles can take 0.5-40 s per
    paste — back-to-back pastes at 5/s into the same field skip the
    walk entirely). The cache window is short enough that a user
    switching apps between pastes (typically >500 ms apart) gets a
    fresh walk. On any exception during the cache update (e.g.
    ``getRole()`` raises on a stale D-Bus proxy), the cache is
    invalidated and the underlying accessible is returned so the
    caller's own ``getRole()`` call raises (same as pre-fix).

    The cache returns a :class:`_CachedFocusedAccessible` shim whose
    ``getRole()`` returns the cached role — this preserves the
    function's contract (caller calls ``.getRole()`` on the result)
    without holding a reference to a potentially stale D-Bus proxy
    across paste cycles.

    The cache is DISABLED under pytest (see :func:`_is_pytest_running`).
    Existing tests like ``test_clipboard_password_detection.py`` install
    a fresh fake ``pyatspi`` module per case and expect the walk to
    re-run; without the pytest gate, the 200 ms cache window would leak
    the previous test's role into the next case. Tests that exercise
    the cache hit / miss behaviour patch
    ``_is_pytest_running`` to return ``False``.
    """
    # ── Cache hit fast-path ───────────────────────────────────────────
    # Read the cache slot once (no lock — tuple read is atomic under
    # the GIL; a concurrent writer at worst causes both threads to
    # miss and walk, which is benign). Skip the cache entirely under
    # pytest so each test case sees a fresh walk.
    if not _is_pytest_running():
        cached = _LAST_FOCUSED_ROLE
        if cached is not None:
            cached_role, cached_ts = cached
            if (time.monotonic() - cached_ts) < _ATSPI_FOCUSED_ROLE_CACHE_WINDOW_S:
                return _CachedFocusedAccessible(cached_role)
            # expired — clear the stale slot and fall through to a fresh walk
            _invalidate_focused_atspi_cache()

    accessible = _find_focused_atspi_accessible_uncached(desktop, state_focused, max_depth)
    if accessible is None:
        # Don't cache None — let the next call re-walk. A transient
        # "no focused accessible" (e.g. mid-alt-tab) should not poison
        # the cache for 200 ms.
        return None

    # Cache the role (only in production — under pytest the cache stays
    # cold so tests are isolated). If getRole() raises (stale D-Bus
    # proxy, app crashed mid-traversal), invalidate the cache (don't
    # poison it with a None role that the next call would return as a
    # hit) and return the accessible so the caller's own getRole()
    # raises — identical to the pre-fix behaviour.
    try:
        role = accessible.getRole()
    except Exception:
        _invalidate_focused_atspi_cache()
        return accessible

    if not _is_pytest_running():
        _set_focused_atspi_cache(role)
    return accessible


def _is_pytest_running() -> bool:
    """Return True if pytest is the active test runner.

    The focused-role cache is a production-only optimization.
    In tests, each case typically installs a fresh fake ``pyatspi``
    module via ``sys.modules`` patching and expects the tree walk to
    re-run; without this gate, the 200 ms cache window would leak the
    previous test's role into the next case (e.g. a password-field
    test cached role=``ATSPI_ROLE_PASSWORD_TEXT`` would make the
    immediately-following plain-text test incorrectly report a password
    field).

    Tests that explicitly exercise the cache hit / miss behaviour
    patch this helper to return ``False`` (see
    ``tests/test_clipboard_atspi_role_cache.py``).
    """
    return "pytest" in sys.modules


class _CachedFocusedAccessible:
    """Shim returned by ``_find_focused_atspi_accessible`` on cache hit.

    Wraps the cached role so the caller's ``focused.getRole()`` call
    (in ``_is_password_field_linux``) succeeds without re-walking the
    AT-SPI tree and without holding a reference to a potentially stale
    D-Bus proxy across paste cycles.

    Only ``getRole()`` is implemented because that's the sole method
    the caller invokes on the returned accessible. If a future caller
    needs additional methods (``getState``, ``getChildAtIndex``, etc.),
    the cache MUST be invalidated — a cached role is insufficient to
    satisfy those calls.
    """

    __slots__ = ("_role",)

    def __init__(self, role: Any) -> None:
        self._role = role

    def getRole(self) -> Any:  # noqa: N802 — mirrors pyatspi.Accessible.getRole
        return self._role


# Module-level cache slot for the focused accessible's role.
#
# ``None`` = no cached role (cold cache). A non-None value is a
# ``(role, timestamp_monotonic)`` tuple. The timestamp is read with
# ``time.monotonic()`` so NTP adjustments don't affect the cache
# window. The slot is module-level (not per-instance) because there's
# only one focused accessible at a time per desktop — a per-instance
# slot on the caller would just add indirection.
#
# This is a plain module-level global in :mod:`.targets` (NOT routed
# through ``_pkg.NAME`` like ``_WE_ELEVATED`` etc.) because the cache
# is purely an internal performance optimization — no test or external
# code patches it via the package namespace. Tests that want to
# inspect or reset the cache import
# ``voice_typer.server.clipboard_target_safety.targets as targets_mod``
# and access ``targets_mod._LAST_FOCUSED_ROLE`` directly (or call
# ``_invalidate_focused_atspi_cache()``).
_LAST_FOCUSED_ROLE: tuple[Any, float] | None = None

# Cache window: 200 ms. Long enough that back-to-back pastes at 5/s
# into the same field (200 ms < 200 ms gap) skip the O(N) tree walk.
# Short enough that a user switching apps between pastes (typically
# >500 ms apart — even a fast alt-tab is ~300 ms) gets a fresh walk.
# Worst case: a user pasting into app A, alt-tabbing to app B in
# <200 ms, and pasting again — the second paste uses app A's cached
# role. This is benign because (a) the cached role is only used to
# check ``ATSPI_ROLE_PASSWORD_TEXT`` (a password field), (b) the
# cache window is shorter than the minimum realistic alt-tab time,
# and (c) on a cache hit the function returns a shim whose only
# method is ``getRole()`` — no other AT-SPI state is consulted, so
# there's no risk of acting on a stale focused element's state.
_ATSPI_FOCUSED_ROLE_CACHE_WINDOW_S: float = 0.200


def _invalidate_focused_atspi_cache() -> None:
    """Clear the focused-role cache.

    Called on cache expiry, on exception during the cache update, and
    by tests between cases to isolate cache-hit / cache-miss behaviour.
    """
    global _LAST_FOCUSED_ROLE
    _LAST_FOCUSED_ROLE = None


def _set_focused_atspi_cache(role: Any) -> None:
    """Populate the focused-role cache with ``role`` and the current time."""
    global _LAST_FOCUSED_ROLE
    _LAST_FOCUSED_ROLE = (role, time.monotonic())


def _find_focused_atspi_accessible_uncached(
    desktop: Any,
    state_focused: Any,
    max_depth: int = 10,
) -> Any:
    """Walk the AT-SPI tree to find the focused accessible (uncached).

    This is the pre-cache walk body, extracted so the public
    :func:`_find_focused_atspi_accessible` wrapper can add the cache
    fast-path without entangling the recursion (recursive calls inside
    this helper do NOT consult the cache — only the top-level call
    does, which is the desired behaviour).
    """
    if desktop is None or max_depth <= 0:
        return None

    # First check the root itself (defensive — desktop itself is never
    # FOCUSED in practice, but if a future caller passes a sub-tree
    # root, this catches the case where the root IS the focused leaf).
    try:
        root_state = desktop.getState()
    except Exception as e:
        _pkg._log().debug("Linux AT-SPI desktop.getState() failed: %s", e, exc_info=True)
        root_state = None
    if root_state is not None:
        try:
            if root_state.contains(state_focused):
                return desktop
        except Exception as exc:
            # Wire the one-shot paste-safety warning so the
            # swallowed exception is visible (was silent ``pass``).
            # The traversal continues below; if it also fails to find
            # a focused accessible, the caller fails open.
            _pkg._warn_paste_safety_once(
                "atspi_focused_state_check",
                "_find_focused_atspi_accessible",
                exc,
            )

    # Depth-first traversal: at each level, recurse into the first
    # child whose subtree contains a focused accessible. We cap the
    # total recursion depth at ``max_depth`` to bound worst-case time.
    try:
        child_count = desktop.childCount
    except Exception as exc:
        # This helper returns an element (``Any``) or ``None`` —
        # NOT a bool. Changing the return type to ``True`` would break
        # the caller (``focused.getRole()`` on a bool raises). We log
        # via the one-shot paste-safety warning for dedup and keep the
        # ``None`` return; the caller's own fail-closed paths
        # (``_is_password_field_linux``) cover the security posture.
        _pkg._warn_paste_safety_once(
            "atspi_focused_childcount_lookup",
            "_find_focused_atspi_accessible",
            exc,
        )
        return None
    for i in range(child_count):
        try:
            child = desktop.getChildAtIndex(i)
        except Exception as e:
            _pkg._log().debug("Linux AT-SPI desktop.getChildAtIndex(%d) failed: %s", i, e, exc_info=True)
            continue
        if child is None:
            continue
        # Recursive call goes to the UNCACHED helper — only the
        # top-level call (via _find_focused_atspi_accessible) consults
        # the cache. This avoids redundant cache writes during a single
        # traversal and ensures a cache hit at the top short-circuits
        # the entire walk.
        result = _find_focused_atspi_accessible_uncached(child, state_focused, max_depth - 1)
        if result is not None:
            return result
    return None
