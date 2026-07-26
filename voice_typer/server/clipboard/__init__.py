"""Clipboard management and auto-paste package (PVT-23 split).

This package was extracted from the original 1432-LOC ``clipboard.py``
monolith into four focused modules:

* :mod:`.linux`  — Linux/Wayland clipboard primitives (``wl-copy`` /
  ``wl-paste`` / ``wtype``) + pynput lazy-import helpers.
* :mod:`.windows` — Win32 clipboard abstraction (``Win32Clipboard``)
  + ``_win32_empty_clipboard`` + ``_send_ctrl_v_win32`` SendInput
  helper.
* :mod:`.manager` — ``ClipboardManager`` orchestrator + atexit
  handler + ``_pending_restores`` registry.

Design contract preserved from the original monolith:

- copy() ALWAYS puts text on the clipboard.
- paste() ALWAYS sends a paste keystroke (Ctrl+V or platform equivalent).
  Terminal emulators use Shift+Insert instead of Ctrl+V.
- On Windows, paste uses Win32 SendInput with all four events (Ctrl down,
  V down, V up, Ctrl up) submitted as a single atomic INPUT batch to
  avoid applications interpreting key-up as a duplicate paste event.

PLAT-001: On Windows, we always prefer SendInput over
pynput.keyboard.Controller for sending keystrokes.  pynput uses
SendInput internally on Windows, but when UIPI (User Interface
Privilege Isolation) blocks it (e.g. targeting an elevated process
from a non-elevated one), pynput silently fails.  Our
_send_ctrl_v_win32() uses the same Win32 SendInput API directly and
logs the failure, then falls back to the pynput path as a last
resort.

PLAT-027: All direct ctypes.windll.user32 clipboard calls are wrapped
in the Win32Clipboard context manager, which handles
OpenClipboard/CloseClipboard lifecycle, EmptyClipboard, and
GetClipboardSequenceNumber.

PLAT-CONTENT: We do not detect contentEditable elements. Pasted text
is always plain text. In a future version, consider detecting
contentEditable elements (via UI Automation on Windows) and pasting
rich text. For now, we log when the paste target appears to be a
rich editor (e.g. Word, LibreOffice).

PLAT-007 (source-string pin — see
tests/regressions/security_test.py::TestClipboardRetryNarrowedException):
the copy() retry block in :mod:`.manager` MUST catch ``OSError``
(narrowed from the pre-fix broad ``Exception`` pattern) and check
``winerror == 5`` (ERROR_ACCESS_DENIED) before retrying. The exact
source patterns pinned by the test are reproduced below so
``inspect.getsource(voice_typer.server.clipboard)`` — which returns
this ``__init__.py``'s source after the PVT-23 package split —
continues to satisfy the source-string assertion::

    # in ClipboardManager.copy() (manager.py):
    #     except OSError as copy_err:
    #         winerror = getattr(copy_err, "winerror", None)
    #         if winerror == 5 and attempt < 2:
    #             ...

The pre-fix broad-Exception catch on ``copy_err`` MUST NOT be
reintroduced into the retry block.

Module-level state (lives in this package's namespace so tests that
patch ``voice_typer.server.clipboard.<name>`` keep working):

* ``_Key`` / ``_Controller`` — lazily-populated pynput symbols.
* ``_pending_restores`` / ``_pending_restores_lock`` — atexit
  registry of pending delayed-restores.

All public + private symbols re-exported below so existing
``from voice_typer.server.clipboard import X`` call sites (and the
~12 monkeypatch sites in the test suite) work unchanged.
"""

from __future__ import annotations

# ─── Standard library imports ────────────────────────────────────────
import atexit  # noqa: F401  (re-exported for tests that inspect atexit usage)
import contextlib
import logging
import os  # noqa: F401  (used by linux primitives; re-exported)
import shutil  # noqa: F401  (used by linux primitives; re-exported)
import subprocess  # noqa: F401  (used by linux primitives; re-exported)
import threading  # noqa: F401  (re-exported for tests)
import time  # noqa: F401  (patched by tests via clip_mod.time)
from typing import Any  # noqa: F401

# ─── Third-party + sibling imports ───────────────────────────────────
import pyperclip  # noqa: F401  (patched by tests via clip_mod.pyperclip)

from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: F401
from voice_typer.server.platform_utils import (  # noqa: F401
    is_linux,
    is_macos,
    is_windows,
)

# Package-level logger. Tests patch ``voice_typer.server.clipboard.log``.
log = logging.getLogger(__name__)

# ─── Pynput lazy-import state ────────────────────────────────────────
# Lives in the PACKAGE namespace (not in any submodule) so tests that
# do ``patch.object(clip_mod, "_Controller", MagicMock())`` /
# ``clip_mod._Key = None`` actually mutate the state that
# ``ClipboardManager`` reads via ``_cb._Controller`` / ``_cb._Key``.
# ``_ensure_pynput_imported()`` (in .linux) writes these attributes on
# first use.
#
# YJ-22 (retry / partial fix): narrow ``_Controller`` from ``Any`` to
# ``type | None``. Safe — the only downstream usage is instantiation
# (``_cb._Controller()`` in :mod:`voice_typer.server.clipboard.manager`,
# line 141), and ``type`` is callable. ``None`` accepts the initial
# empty binding. No ``# type: ignore[assignment]`` marker is needed
# because ``None`` is in ``type | None``.
#
# ``_Key`` is left as ``Any``. YJ-22's prescribed narrowing to
# ``type | None`` broke 6 downstream ``_cb._Key.cmd`` /
# ``_cb._Key.shift`` / ``_cb._Key.insert`` / ``_cb._Key.ctrl``
# accesses in :mod:`voice_typer.server.clipboard.manager` (because
# ``type`` and ``None`` don't expose pynput's ``Key`` enum members).
# YJ-FIX-B2 reverted both annotations; this retry re-applies the
# narrowing ONLY to ``_Controller`` (where it's safe) and leaves
# ``_Key: Any`` with a documented rationale. The full narrowing for
# ``_Key`` requires either a ``voice_typer/stubs/pynput/keyboard.pyi``
# stub + ``TYPE_CHECKING`` import (so ``_Key: type[Key] | None``
# resolves) or a ``Protocol`` exposing the enum members — both are
# larger changes deferred to a future session. The
# ``# type: ignore[assignment]`` markers YJ-22 removed stay dropped
# (``Any`` accepts ``None`` without a marker, and ``type | None``
# accepts ``None`` without a marker).
_Key: Any = None
_Controller: type | None = None


# ─── Re-exports from submodules ──────────────────────────────────────
# Import order matters: .linux and .windows have NO inter-submodule
# dependencies. .manager depends on both (via the package attribute
# lookups described above), so it MUST be imported last.

# ARCH-11: Win32 UI Automation focus / password-field / elevated-target
# detection was extracted to ``clipboard_target_safety``. Re-export the
# names here so internal callers (``ClipboardManager._is_safe_paste_target``)
# and external tests that patch ``voice_typer.server.clipboard.<name>``
# keep working unchanged.
#
# G4-H-05 (session-4): also re-export the platform-native password-field
# helpers (macOS pyobjc / Linux pyatspi) so ClipboardManager can dispatch
# to them from ``_is_safe_paste_target`` on non-Windows platforms.
from voice_typer.server.clipboard_target_safety import (  # noqa: E402,F401
    _CRED_DIALOG_CLASSES,
    _PYATSPI_STATE_FOCUSED,
    _PYATSPI_UNAVAILABLE_WARNED,
    _PYOBJC_UNAVAILABLE_WARNED,
    _UIA_MODULE,
    _UIA_SINGLETON,
    _UIA_SINGLETON_INIT_ATTEMPTED,
    _WE_ELEVATED,
    _find_focused_atspi_accessible,
    _focused_window_is_credential_dialog,
    _get_uia_focused_element,
    _get_uia_singleton,
    _get_we_elevated,
    _is_content_editable,
    _is_elevated_target,
    _is_password_field,
    _is_password_field_linux,
    _is_password_field_macos,
    reset_platform_unavailable_warnings,
)

from .linux import (  # noqa: E402,F401
    _RICH_EDITOR_PROCESS_NAMES,
    _TERMINAL_PROCESS_NAMES,
    _WTYPE_SHORT_TEXT_THRESHOLD,
    _copy_to_clipboard,
    _ensure_pynput_imported,
    _have_wl_clipboard,
    _have_wtype,
    _is_wayland_paste_session,
    _is_wayland_session,
    _linux_copy,
    _linux_paste,
    _linux_paste_via_wtype,
    _linux_wayland_copy,
    _linux_wayland_paste,
    _paste_from_clipboard,
)
from .manager import (  # noqa: E402,F401
    ClipboardCopyError,
    ClipboardManager,
    _force_restore_pending_at_exit,
    _pending_restores,
    _pending_restores_lock,
)
from .windows import (  # noqa: E402,F401
    Win32Clipboard,
    _send_ctrl_v_win32,
    _win32_empty_clipboard,
)

__all__ = [
    # Public API
    "ClipboardCopyError",
    "ClipboardManager",
    "ClipboardSnapshot",
    "Win32Clipboard",
    # Linux / Wayland primitives
    "_RICH_EDITOR_PROCESS_NAMES",
    "_TERMINAL_PROCESS_NAMES",
    "_copy_to_clipboard",
    "_ensure_pynput_imported",
    "_have_wl_clipboard",
    "_have_wtype",
    "_is_wayland_paste_session",
    "_is_wayland_session",
    "_linux_copy",
    "_linux_paste",
    "_linux_paste_via_wtype",
    "_linux_wayland_copy",
    "_linux_wayland_paste",
    "_paste_from_clipboard",
    # Win32 primitives
    "_send_ctrl_v_win32",
    "_win32_empty_clipboard",
    # atexit / pending-restores registry
    "_force_restore_pending_at_exit",
    "_pending_restores",
    "_pending_restores_lock",
    # Pynput lazy-import state (mutated by _ensure_pynput_imported)
    "_Controller",
    "_Key",
    # Target-safety re-exports (ARCH-11)
    "_CRED_DIALOG_CLASSES",
    "_PYATSPI_UNAVAILABLE_WARNED",
    "_PYATSPI_STATE_FOCUSED",
    "_PYOBJC_UNAVAILABLE_WARNED",
    "_UIA_MODULE",
    "_UIA_SINGLETON",
    "_UIA_SINGLETON_INIT_ATTEMPTED",
    "_WE_ELEVATED",
    "_find_focused_atspi_accessible",
    "_focused_window_is_credential_dialog",
    "_get_uia_focused_element",
    "_get_uia_singleton",
    "_get_we_elevated",
    "_is_content_editable",
    "_is_elevated_target",
    "_is_password_field",
    "_is_password_field_linux",
    "_is_password_field_macos",
    "reset_platform_unavailable_warnings",
    # Platform utils (re-exported so tests can patch via clip_mod.is_windows etc.)
    "is_linux",
    "is_macos",
    "is_windows",
    # Third-party (re-exported so tests can patch via clip_mod.pyperclip etc.)
    "pyperclip",
    "subprocess",
    "time",
    "log",
]


# ─── G4 (signal handler, session-4): SIGTERM / SIGHUP → force-restore on exit ────
#
# ``atexit`` only fires on NORMAL interpreter shutdown (return from main,
# ``sys.exit()``, unhandled ``SystemExit``). It does NOT fire on POSIX
# signals like SIGTERM (default disposition = terminate) or SIGHUP
# (terminal hangup). Without these handlers, a ``kill <pid>`` issued
# during the 150 ms restore-delay window would orphan the user's
# borrowed clipboard content forever — the daemon thread is killed
# before it can call ``snapshot.restore()``.
#
# We install ``_signal_restore_handler`` for SIGTERM and SIGHUP on POSIX.
# The handler:
#   1. Calls ``_force_restore_pending_at_exit()`` to synchronously
#      restore any pending snapshots (best-effort — per-snapshot
#      failures are logged but do not abort the loop).
#   2. Restores the signal's default disposition and re-raises it so
#      the process exits with the conventional 128+signum status.
#
# Residual risk: SIGKILL (``kill -9``) cannot be caught by design —
# the kernel terminates the process immediately without running any
# handler. A SIGKILL during the restore-delay window will leak the
# borrowed clipboard content. This is an accepted residual risk;
# operators should prefer SIGTERM for graceful shutdown.
#
# POSIX-only: Windows lacks SIGHUP, and ``signal.signal`` for SIGTERM
# on Windows doesn't behave like the POSIX equivalent. The guard
# ``hasattr(signal, "SIGHUP")`` selects POSIX platforms.
#
# Thread-safety: ``signal.signal`` may only be called from the main
# thread of the main interpreter. If clipboard.py is first imported
# from a worker thread (rare — app startup runs in the main thread),
# the registration is skipped silently; the atexit handler still
# covers normal-shutdown cases.


def _signal_restore_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
    """G4 signal handler: restore pending snapshots, then re-raise.

    Called by the OS when the process receives SIGTERM or SIGHUP.
    Synchronously runs ``_force_restore_pending_at_exit()`` so any
    clipboard snapshot borrowed by ``copy()`` but not yet restored by
    the daemon thread is restored before the process dies. Then
    restores the signal's default disposition and re-raises it so the
    process exits with the conventional ``128 + signum`` status.

    Never raises — signal handlers must not propagate exceptions
    (the interpreter behavior is implementation-defined). If
    re-raising the signal fails for any reason, force-exit via
    ``os._exit(128 + signum)`` so the process still terminates.
    ``os._exit`` is used (rather than ``raise SystemExit``) because
    ``SystemExit`` can be caught by frameworks that override
    ``sys.excepthook`` or run ``try: except SystemExit:`` blocks —
    which would leave the process in an inconsistent half-shutdown
    state (clipboard already restored, but the interpreter still
    running). ``os._exit`` bypasses the interpreter's exception
    machinery and the ``atexit`` table (which is fine here — the
    clipboard restore has already run synchronously above).
    """
    with contextlib.suppress(Exception):
        _force_restore_pending_at_exit()
    # Restore default disposition and re-raise so the process exits
    # with the conventional 128+signum status (matches what bash/$?
    # reports for signal-killed processes).
    try:
        import os as _os_module
        import signal as _signal_module

        _signal_module.signal(signum, _signal_module.SIG_DFL)
        _os_module.kill(_os_module.getpid(), signum)
    except Exception:
        # If re-raise fails (e.g. signal already in flight, or
        # os.kill unavailable), force exit via os._exit so the
        # process still terminates. 128+signum is the POSIX convention.
        # os._exit (rather than raise SystemExit) is used because
        # SystemExit can be caught by try/except SystemExit handlers
        # or frameworks overriding sys.excepthook, leaving the process
        # in a half-shutdown state. The atexit table is intentionally
        # bypassed — _force_restore_pending_at_exit() has already run
        # synchronously above.
        import os as _os_module_fallback

        _os_module_fallback._exit(128 + signum)


_SIGNAL_HANDLERS_REGISTERED = False
if not _SIGNAL_HANDLERS_REGISTERED:
    try:
        import signal as _signal_module

        # POSIX-only: SIGHUP exists only on POSIX. On Windows,
        # ``signal.SIGTERM`` exists but the Windows equivalent of this
        # whole mechanism is different (SetConsoleCtrlHandler), so we
        # skip registration entirely on non-POSIX platforms.
        if hasattr(_signal_module, "SIGHUP"):
            _signal_module.signal(_signal_module.SIGTERM, _signal_restore_handler)
            _signal_module.signal(_signal_module.SIGHUP, _signal_restore_handler)
            _SIGNAL_HANDLERS_REGISTERED = True
    except (ValueError, OSError):
        # ValueError: not in the main thread (signal.signal can only
        # be called from the main thread). OSError: platform-specific
        # failure. Both are non-fatal — the atexit handler still
        # covers normal-shutdown cases.
        pass
    except Exception:  # pragma: no cover — defensive
        pass
