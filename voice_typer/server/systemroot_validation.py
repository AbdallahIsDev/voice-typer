"""SystemRoot environment-variable validation (Windows-only, security-critical).

CR-28 (config.py split): this module was extracted from
``voice_typer.server.config``.  The function here is re-exported from
``config.py`` so existing call sites — ``env_validation._validate_env_vars``
(function-level import) and the CR-19 regression test suite — keep
working unchanged.

Behavior is byte-level preserved from the original in ``config.py``
(same signature, same logic, same ``sys.exit(1)`` fail-closed
behavior on path-traversal / unusual-character inputs, same
reset-to-default on missing-directory).  The one structural change
is that ``is_windows`` is now looked up via the ``config`` module at
call time (function-level import) so tests that monkeypatch
``config.is_windows`` continue to drive the Windows-only code path on
the Linux CI runner.
"""

import logging
import os
import re
import sys

log = logging.getLogger("voice_typer.server.config")


def _validate_systemroot() -> None:
    """SEC-audit-011: Validate the SystemRoot environment variable on Windows.

    The ``SystemRoot`` env var (e.g. ``C:\\Windows``) is used by Python's
    ``os.path`` module and various Win32 APIs to locate system DLLs.  An
    attacker who can set this variable before our process starts could
    redirect DLL lookups to a malicious directory.  This function verifies
    that ``SystemRoot`` points to an existing directory on Windows and
    rejects values that contain path traversal sequences or unusual
    characters.

    On non-Windows platforms, this is a no-op.

    CR-19 fix — fail-closed vs reset-to-default decisions:
      - Path traversal (``..``)             → ``sys.exit(1)`` (security issue)
      - Unusual characters (``<>|"&'\\n\\r\\t``) → ``sys.exit(1)`` (security issue)
      - Missing directory                   → reset to ``C:\\Windows`` + continue (usability)
      - Missing ``System32\\notepad.exe``   → log warning + continue (not a hard blocker)

    Rationale: a malicious ``SystemRoot`` is a DLL-hijacking vector that
    could lead to arbitrary code execution with the user's privileges —
    better to refuse to start than to silently reset and continue.  A
    missing directory, on the other hand, is typically a misconfigured
    environment (e.g. a stripped-down Windows image) where the user can
    still benefit from the app starting with the default ``SystemRoot``.
    """
    # CR-28: function-level lookup of ``is_windows`` AND ``Path`` from
    # the config module so tests that monkeypatch ``config.is_windows``
    # and ``config.Path`` (see ``tests/test_validate_systemroot.py`` and
    # ``tests/regressions/security_test.py``) continue to drive the
    # Windows-only code path on the Linux CI runner after the
    # extraction.  Using a local ``from voice_typer.server import config``
    # at module top-level would also work but introduces a circular
    # import (config re-exports this function), so we defer it to
    # call time.
    from voice_typer.server import config as _cfg

    if not _cfg.is_windows():
        return

    # CR-28: tests patch ``config.Path`` with a fake Path class to
    # simulate missing/existing directories without touching the real
    # filesystem.  Use the config module's ``Path`` so the patches
    # apply.
    _Path = _cfg.Path  # noqa: N806  # module alias, not a local variable

    systemroot = os.environ.get("SYSTEMROOT", "")
    if not systemroot:
        # SystemRoot not set — unusual but not a direct attack vector
        # for our process.  Windows APIs may fail later; we just log.
        log.warning("[CONFIG] SystemRoot environment variable is not set")
        return

    # CR-19: Check for path traversal — fail-closed (security issue).
    # A malicious SystemRoot pointing at an attacker-controlled directory
    # with ``..`` segments is a classic DLL-injection vector.  Refusing
    # to start is safer than silently resetting (the user would have no
    # indication that their SystemRoot was being tampered with).
    if ".." in systemroot:
        log.error(
            "[CONFIG] SystemRoot contains path traversal ('..'): %s — "
            "possible DLL injection attack. ABORTING STARTUP (fail-closed).",
            systemroot,
        )
        sys.exit(1)

    # CR-19: Check for unusual characters that could indicate tampering —
    # fail-closed (same rationale as the path-traversal branch above).
    if re.search(r'[<>|"&\'\n\r\t]', systemroot):
        log.error(
            "[CONFIG] SystemRoot contains unusual characters: %r — possible "
            "injection attack. ABORTING STARTUP (fail-closed).",
            systemroot,
        )
        sys.exit(1)

    # CR-19: Verify the directory exists — reset to default + continue
    # (usability issue, not a direct security issue).  A user's
    # SystemRoot may be set to a path that no longer exists (e.g. they
    # moved their Windows installation) — refusing to start would lock
    # them out of the app entirely.  Resetting to the canonical default
    # lets the app start with a valid SystemRoot.
    if not _Path(systemroot).is_dir():
        log.warning(
            "[CONFIG] SystemRoot does not point to an existing directory: %s — "
            "resetting to default C:\\Windows (usability fallback).",
            systemroot,
        )
        default = r"C:\Windows"
        if _Path(default).is_dir():
            os.environ["SYSTEMROOT"] = default
        # If even C:\Windows doesn't exist, there's nothing more we can
        # do — leave SystemRoot as-is and let downstream Win32 APIs fail
        # with their own diagnostics.
        return

    # SEC-audit-011: Verify SystemRoot contains System32\notepad.exe.
    # This is the canonical sanity check — every valid Windows
    # installation has notepad.exe in System32.  If it's missing, the
    # SystemRoot value is almost certainly invalid or tampered.
    #
    # CR-19: Not a hard blocker — log warning + continue.  The caller is
    # expected to use a hardcoded fallback path for notepad specifically
    # (see ``system_handlers.py``).  Do NOT reset SystemRoot itself —
    # other system DLLs may still be valid even if notepad is missing.
    notepad_path = _Path(systemroot) / "System32" / "notepad.exe"
    if not notepad_path.exists():
        log.warning(
            "[CONFIG] SystemRoot does not contain System32\\notepad.exe: %s — "
            "caller should use hardcoded fallback for notepad.",
            systemroot,
        )
