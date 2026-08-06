"""UIA singleton + focused-element fetching (clipboard injection infrastructure).

Contains the cached ``IUIAutomation`` COM singleton management that
drives the Windows password-field / contentEditable safety checks in
:mod:`.validation` and :mod:`.targets`:

* :func:`_get_uia_singleton` — lazily creates and caches the
  ``IUIAutomation`` COM instance (``_pkg._UIA_SINGLETON``) and the
  comtypes module reference (``_pkg._UIA_MODULE``). Creating a fresh
  instance on every paste was 10-50ms per call (cross-process RPC);
  caching eliminates that cost for every subsequent paste.
* :func:`_get_uia_focused_element` — wraps the cached singleton's
  ``GetFocusedElement()`` call with a fail-open try/except.

All cross-module references (``is_windows``, ``_log``, the mutable
``_UIA_*`` globals) go through ``_pkg.NAME`` so test patches / resets
on ``voice_typer.server.clipboard_target_safety.NAME`` propagate to the
functions defined here. A plain ``global NAME`` would write to THIS
submodule's namespace and be invisible to the test patches applied on
the package — hence the ``_pkg.NAME`` access pattern.
"""

from __future__ import annotations

import atexit
import contextlib

# ``_pkg`` is bound at module load time to the partial package object
# (``__init__.py`` is still executing when this submodule is loaded).
# Attribute lookups on ``_pkg`` happen at CALL TIME, by which point
# ``__init__.py`` has finished and all names are defined.
import voice_typer.server.clipboard_target_safety as _pkg


def _release_uia_resources_at_exit() -> None:
    """Release the cached UIA COM references before interpreter teardown.

    The IUIAutomation COM singleton (``_UIA_SINGLETON``) is cached for
    the process lifetime so pastes don't pay CoCreateInstance per call.
    If the comtypes proxy is only released by Python's GC during
    interpreter finalization, the COM ``Release()`` can fire AFTER the
    UIAutomationCore RPC server has begun shutting down, raising
    ``Windows fatal exception: code 0x80010108`` (RPC_E_DISCONNECTED)
    that terminates the process. In the test suite this kills pytest
    xdist workers ("node down: Not properly terminated") and aborts the
    run mid-suite; in production it is a crash at app exit. atexit
    handlers execute while COM is still initialized, so dropping the
    references here releases the COM proxies safely.
    """
    # Narrow suppression only (XS-36): the assignments cannot raise in
    # normal operation, but guard against an exotic atexit-time module
    # teardown race where ``_pkg`` is already partially torn down.
    with contextlib.suppress(AttributeError):
        _pkg._UIA_SINGLETON = None
    with contextlib.suppress(AttributeError):
        _pkg._UIA_MODULE = None


atexit.register(_release_uia_resources_at_exit)


def _get_uia_singleton():
    """Return the cached IUIAutomation instance, or None if unavailable.

    Caches both the comtypes module reference (from
    ``GetModule("UIAutomationCore.dll")``) and the IUIAutomation COM
    instance (in ``_pkg._UIA_SINGLETON`` and ``_pkg._UIA_MODULE``) so
    we don't pay the CoCreateInstance cost on every paste.

    Init is guarded by ``_pkg._UIA_SINGLETON_LOCK`` using double-checked
    locking so concurrent first-callers don't both run
    ``comtypes.client.GetModule`` + ``CoCreateInstance`` and overwrite
    each other's cached proxy. The fast path (init already attempted)
    is lock-free; only the cold path acquires the lock.

    Subtlety: the ``_pkg._UIA_SINGLETON_INIT_ATTEMPTED`` flag is set in
    a ``finally`` block AFTER ``_pkg._UIA_SINGLETON`` is assigned.
    Setting it earlier would let racing fast-path callers (which check
    the flag WITHOUT the lock) observe the flag set while
    ``_pkg._UIA_SINGLETON`` is still ``None`` — they'd return ``None``
    and permanently disable UIA checks for their code path even though
    init eventually succeeded.
    """
    # Fast path: init already completed — no lock needed.
    if _pkg._UIA_SINGLETON_INIT_ATTEMPTED:
        return _pkg._UIA_SINGLETON
    # Cold path: acquire the lock and re-check (another thread may
    # have completed the init while we were waiting).
    with _pkg._UIA_SINGLETON_LOCK:
        if _pkg._UIA_SINGLETON_INIT_ATTEMPTED:
            return _pkg._UIA_SINGLETON
        try:
            if not _pkg.is_windows():
                return None
            try:
                import comtypes.client

                _pkg._UIA_MODULE = comtypes.client.GetModule("UIAutomationCore.dll")
                _pkg._UIA_SINGLETON = comtypes.CoCreateInstance(
                    _pkg._UIA_MODULE.CUIAutomation._reg_clsid_,
                    interface=_pkg._UIA_MODULE.IUIAutomation,
                )
            except Exception as exc:
                _pkg._log().debug(
                    "[CLIPBOARD] IUIAutomation singleton init failed: %s — UIA checks disabled",
                    exc,
                )
                _pkg._UIA_SINGLETON = None
            return _pkg._UIA_SINGLETON
        finally:
            # Set the flag LAST so racing fast-path readers never see
            # the flag set with _pkg._UIA_SINGLETON still None.
            _pkg._UIA_SINGLETON_INIT_ATTEMPTED = True


def _get_uia_focused_element():
    """Return the focused UI element via the cached IUIAutomation singleton.

    Reuses the module-level ``_pkg._UIA_SINGLETON`` (via
    :func:`_pkg._get_uia_singleton`) so we don't pay CoCreateInstance +
    GetModule on every call. Returns None if UIA is unavailable or no
    element is focused.
    """
    uia = _pkg._get_uia_singleton()
    if uia is None:
        return None
    try:
        return uia.GetFocusedElement()
    except Exception as exc:
        _pkg._log().debug(
            "[CLIPBOARD] GetFocusedElement failed: %s — failing open",
            exc,
        )
        return None
