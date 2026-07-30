"""Config editor launcher (extracted from VoiceTyperApp._open_config_file).

XPLAT-01 / SEC-audit-011 / B-4 / CR-015: opens ``config.json`` in the
user's default editor and holds ``_config_mutation_lock`` for the full
editor session so a concurrent IPC ``set_config`` cannot atomically
clobber the file mid-edit (TOCTOU race). After the editor exits the
config is reloaded from disk so the user's saved edits take effect.

Windows: opens with the user's ``.json`` association via
``ShellExecuteEx`` (falls back to SystemRoot-validated Notepad, never a
bare PATH-resolved ``notepad``). macOS uses ``open -W``; Linux uses
``xdg-open``. All three branches block on the editor and reload
afterwards.

DR-19: the three platform branches previously each duplicated the
``with self.app._config_mutation_lock: save() → [launch] → reload()``
scaffold with only the middle ``[launch]`` call differing. The
per-platform launch logic is now factored into a strategy table
(``_PLATFORM_LAUNCHERS``) keyed by platform name; the
``ConfigEditorLauncher.launch`` body is platform-agnostic. As part of
the dedupe the Windows branch picked up the ``contextlib.suppress(
Exception)`` wrapper around its launch call that the macOS / Linux
branches already had — previously a Windows launch exception bubbled
out to the outer try/except and triggered a tray notification, while
the other two branches silently swallowed subprocess errors. The
three branches are now consistent.

XZ-EH-018: the inner ``contextlib.suppress(Exception)`` wrapper was
replaced with an explicit ``try/except`` that re-raises
``TimeoutError`` (so a 30-minute editor-session timeout surfaces as
a tray notification with a recovery hint) but still swallows other
launch errors (preserving the DR-19 contract). The notepad fallback
in ``_launch_windows_editor`` and the POSIX branches
(``_launch_macos_editor`` / ``_launch_linux_editor``) now use a
bounded ``wait(timeout=...)`` / ``subprocess.run(timeout=...)``
respectively — pre-fix these were unbounded and could wedge the IPC
thread forever if the editor hung. The primary Windows
``ShellExecuteEx`` path was already bounded by DE-68 in
``voice_typer.server.platform_launch._windows_wait_for_process_exit``.

The platform helpers (``is_windows``/``is_macos``/``is_linux``) and the
Windows launch helpers (``_windows_open_with_default_app`` etc.) are
resolved from the owning app's module namespace at call time so existing
tests that ``monkeypatch.setattr("voice_typer.server.app.is_windows",
...)`` continue to work unchanged.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


# XZ-EH-018: bounded timeout for editor subprocess waits.
#
# Pre-fix, the notepad fallback (``_launch_windows_editor``) called
# ``subprocess.Popen([...]).wait()`` with no timeout, and the POSIX
# branches (``_launch_macos_editor`` / ``_launch_linux_editor``) called
# ``subprocess.run([...], check=False)`` with no timeout. If the
# launched editor hung — or the user walked away with the editor
# open — the calling thread blocked forever. The caller
# (``ConfigEditorLauncher.launch``) holds ``_config_mutation_lock``
# AND inherits the IPC thread context, so a hung editor wedged the
# entire server: no further IPC requests were processed, the tray
# icon became unresponsive, and the user had to kill the process.
#
# 30 minutes is a generous upper bound for "the user is actively
# editing a config file": it's long enough that no realistic edit
# session will expire it, and short enough that a forgotten-open
# editor doesn't wedge the server forever. When the timeout fires
# the subprocess is killed (SIGKILL on POSIX, TerminateProcess on
# Windows via ``Popen.kill``) and a clear ``TimeoutError`` is raised
# so the caller can notify the user — never silently return.
#
# This mirrors the DE-68 fix on ``_windows_wait_for_process_exit``
# (``_WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS = 30 * 60 * 1000`` in
# ``voice_typer.server.platform_launch``), which used the same
# 30-minute rationale for the primary Windows ``ShellExecuteEx``
# path. The constant is duplicated here as seconds (not ms) to
# keep the unit consistent with ``Popen.wait(timeout=...)`` /
# ``subprocess.run(timeout=...)`` which both take seconds.
_EDITOR_SESSION_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _raise_editor_timeout(config_path: Any) -> None:
    """Raise a clear ``TimeoutError`` for an editor session timeout.

    XZ-EH-018: called by the platform launchers when the editor
    subprocess exceeds ``_EDITOR_SESSION_TIMEOUT_SECONDS``. The
    message tells the user what happened and how to recover — never
    silently return, because the caller holds ``_config_mutation_lock``
    and the IPC thread, so a silent return would leave the server in
    an ambiguous state (lock released but the editor is still
    running and may write to the file later).
    """

    raise TimeoutError(
        f"Editor session for {config_path} exceeded the "
        f"{_EDITOR_SESSION_TIMEOUT_SECONDS // 60}-minute timeout "
        "and was killed. To recover: save any unsaved edits to a "
        "temporary file (config.json on disk was NOT modified by "
        "the launcher), then re-open the editor via 'Edit config' "
        "to start a fresh session."
    )


def _wait_for_editor_subprocess(proc: subprocess.Popen, config_path: Any) -> None:
    """Wait for *proc* to exit, bounded by ``_EDITOR_SESSION_TIMEOUT_SECONDS``.

    XZ-EH-018: pre-fix the notepad fallback in
    ``_launch_windows_editor`` called ``proc.wait()`` with no
    timeout, blocking the IPC thread forever if the editor hung.

    On timeout the subprocess is killed (``Popen.kill`` sends
    SIGKILL on POSIX and calls ``TerminateProcess`` on Windows) and
    reaped, then a clear ``TimeoutError`` is raised via
    :func:`_raise_editor_timeout`.
    """

    try:
        proc.wait(timeout=_EDITOR_SESSION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # Kill the editor process so it doesn't keep running in the
        # background. ``Popen.kill`` is documented to send SIGKILL on
        # POSIX and call ``TerminateProcess`` on Windows. Best-effort
        # — if kill itself fails (e.g. already exited, or permission
        # denied) we still need to raise the TimeoutError so the
        # caller can notify the user.
        try:
            proc.kill()
        except Exception:
            log.warning(
                "[XZ-EH-018] Failed to kill timed-out editor process",
                exc_info=True,
            )
        # Reap the (now killed) process to avoid a zombie. Best-effort:
        # if the second wait also times out (process is unkillable,
        # e.g. stuck in a syscall on a hung FUSE mount) we don't want
        # to block forever again — the kill signal has been sent.
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            log.warning(
                "[XZ-EH-018] Editor process did not exit 5s after "
                "SIGKILL/TerminateProcess — it may be stuck in an "
                "unkillable syscall. Leaving it; the launcher will "
                "raise TimeoutError anyway."
            )
        except Exception:
            # Don't mask the original TimeoutExpired with a reaper
            # failure — the kill signal has been sent.
            log.warning(
                "[XZ-EH-018] Reaper wait() raised after kill()",
                exc_info=True,
            )
        _raise_editor_timeout(config_path)


def _resolve(name: str, default: Callable[..., Any]) -> Callable[..., Any]:
    """Resolve a helper from the ``voice_typer.server.app`` module, falling
    back to ``default`` if the app module isn't importable yet (e.g. during
    early import). Tests monkeypatch these names on the app module."""

    app_mod = sys.modules.get("voice_typer.server.app")
    if app_mod is not None:
        return getattr(app_mod, name, default)
    return default


def _current_platform() -> str:
    """Return the current platform key (``"windows"``/``"macos"``/``"linux"``).

    Resolves ``is_windows`` / ``is_macos`` from the owning app module at
    call time (mirrors the historical dynamic-lookup convention) so
    tests that monkeypatch ``voice_typer.server.app.is_windows`` etc.
    continue to take effect.
    """

    is_windows = _resolve("is_windows", _default_is_windows)
    is_macos = _resolve("is_macos", _default_is_macos)
    if is_windows():
        return "windows"
    if is_macos():
        return "macos"
    return "linux"


# ── Platform launch strategies (DR-19) ─────────────────────────────────


def _launch_windows_editor(config_path: Any) -> None:
    """Windows-specific editor launch.

    Uses ``ShellExecuteEx`` (via ``_windows_open_with_default_app``) to
    honor the user's ``.json`` association; falls back to a
    SystemRoot-validated Notepad (never a bare PATH-resolved
    ``notepad``) and finally to ``os.startfile``.
    """

    _windows_open_with_default_app = _resolve("_windows_open_with_default_app", _default_windows_open_with_default_app)
    _windows_wait_for_process_exit = _resolve("_windows_wait_for_process_exit", _default_windows_wait_for_process_exit)
    _windows_close_process_handle = _resolve("_windows_close_process_handle", _default_windows_close_process_handle)
    _systemroot_notepad_path = _resolve("_systemroot_notepad_path", _default_systemroot_notepad_path)

    handle = _windows_open_with_default_app(str(config_path))
    if handle is not None:
        try:
            _windows_wait_for_process_exit(handle)
        finally:
            _windows_close_process_handle(handle)
    else:
        notepad = _systemroot_notepad_path()
        if notepad is not None:
            # XZ-EH-018: bounded wait — see ``_wait_for_editor_subprocess``.
            # Pre-fix this was ``subprocess.Popen([...]).wait()`` with no
            # timeout, blocking the IPC thread forever if Notepad hung.
            proc = subprocess.Popen([str(notepad), str(config_path)])
            _wait_for_editor_subprocess(proc, config_path)
        else:
            os.startfile(str(config_path))  # type: ignore[attr-defined]


def _launch_macos_editor(config_path: Any) -> None:
    """macOS-specific editor launch — uses ``open -W`` (blocking).

    XZ-EH-018: bounded by ``_EDITOR_SESSION_TIMEOUT_SECONDS``. Pre-fix
    this was ``subprocess.run(..., check=False)`` with no timeout,
    blocking the IPC thread forever if ``open -W`` hung.
    """

    try:
        subprocess.run(
            ["open", "-W", str(config_path)],
            check=False,
            timeout=_EDITOR_SESSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # ``subprocess.run`` with a timeout already kills and reaps the
        # child before re-raising, so we just need to convert to the
        # launcher's TimeoutError contract.
        _raise_editor_timeout(config_path)


def _launch_linux_editor(config_path: Any) -> None:
    """Linux-specific editor launch — uses ``xdg-open`` (blocking).

    XZ-EH-018: bounded by ``_EDITOR_SESSION_TIMEOUT_SECONDS``. Pre-fix
    this was ``subprocess.run(..., check=False)`` with no timeout,
    blocking the IPC thread forever if ``xdg-open`` hung.
    """

    try:
        subprocess.run(
            ["xdg-open", str(config_path)],
            check=False,
            timeout=_EDITOR_SESSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # ``subprocess.run`` with a timeout already kills and reaps the
        # child before re-raising, so we just need to convert to the
        # launcher's TimeoutError contract.
        _raise_editor_timeout(config_path)


_PLATFORM_LAUNCHERS: dict[str, Callable[[Any], None]] = {
    "windows": _launch_windows_editor,
    "macos": _launch_macos_editor,
    "linux": _launch_linux_editor,
}


class ConfigEditorLauncher:
    """Open the config file in the user's editor, holding the mutation lock.

    Extracted verbatim from ``VoiceTyperApp._open_config_file`` so the
    behavior (subprocess calls, error handling, file locking, reload) is
    identical. The app delegates to ``launch(config_path)``.

    The launcher is constructed with a reference to the owning app so it
    can access ``_config_mutation_lock``, ``config``, and ``tray`` — the
    same attributes the original method used.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    def launch(self, config_path: Any) -> None:
        """Open ``config_path`` in the user's default editor.

        Holds ``app._config_mutation_lock`` for the full editor session
        (XPLAT-01 / SEC-audit-011 / B-4 / CR-015) and reloads the config
        from disk after the editor exits.

        DR-19: the per-platform launch logic is delegated to a strategy
        function from ``_PLATFORM_LAUNCHERS``. All three platform
        branches now wrap the launch call in
        ``contextlib.suppress(Exception)`` — the Windows branch
        previously lacked this wrapper (inconsistent with macOS / Linux
        which already had it).

        XZ-EH-018: ``TimeoutError`` raised by the platform launcher
        (when the editor exceeds ``_EDITOR_SESSION_TIMEOUT_SECONDS``)
        is NOT swallowed by the inner suppress — it propagates to the
        outer ``except`` block so the user gets a tray notification
        explaining what happened. Other launch exceptions (e.g. the
        editor binary not found) are still silently swallowed to
        preserve the DR-19 contract.
        """

        try:
            with self.app._config_mutation_lock:
                if not self.app.config.save():
                    log.warning("[CONFIG] Failed to save config before opening editor")
                launcher = _PLATFORM_LAUNCHERS.get(_current_platform())
                if launcher is None:
                    log.warning("[CONFIG] No editor launcher for platform")
                    return
                # XZ-EH-018: ``TimeoutError`` must propagate so the outer
                # except can notify the user. Other launch exceptions
                # (e.g. editor binary not found) are still suppressed
                # (DR-19 contract).
                try:
                    launcher(config_path)
                except TimeoutError:
                    raise
                except Exception:
                    # DR-19: silently swallow non-timeout launch errors
                    # so a transient launch failure doesn't surface as a
                    # tray notification (the historical behavior on
                    # macOS / Linux).
                    pass
                try:
                    self.app.config = type(self.app.config).load()
                except Exception as exc:
                    log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
        except TimeoutError as e:
            # XZ-EH-018: editor session exceeded the bounded timeout.
            # The platform launcher already killed the subprocess.
            # Notify the user with a helpful recovery message.
            log.warning("[CONFIG] Editor session timed out: %s", e)
            self.app.tray.notify(
                APP_NAME,
                f"Config editor timed out after "
                f"{_EDITOR_SESSION_TIMEOUT_SECONDS // 60} minutes and was "
                f"killed.\nSave any unsaved edits to a temporary file, "
                f"then re-open the editor via 'Edit config'.\n"
                f"Config file: {config_path}",
            )
        except Exception as e:
            log.warning("[CONFIG] Could not open editor: %s", e)
            self.app.tray.notify(APP_NAME, f"Config file:\n{config_path}")


# Lazily-imported defaults so this module is importable standalone (the
# app module re-exports these names, so in production the _resolve call
# above always finds them on the app module).
def _default_is_windows() -> bool:
    from voice_typer.server.platform_utils import is_windows

    return is_windows()


def _default_is_macos() -> bool:
    from voice_typer.server.platform_utils import is_macos

    return is_macos()


def _default_windows_open_with_default_app(path: str):  # type: ignore[no-untyped-def]
    from voice_typer.server.platform_launch import _windows_open_with_default_app

    return _windows_open_with_default_app(path)


def _default_windows_wait_for_process_exit(handle: Any) -> None:
    from voice_typer.server.platform_launch import _windows_wait_for_process_exit

    return _windows_wait_for_process_exit(handle)


def _default_windows_close_process_handle(handle: Any) -> None:
    from voice_typer.server.platform_launch import _windows_close_process_handle

    return _windows_close_process_handle(handle)


def _default_systemroot_notepad_path():
    from voice_typer.server.platform_launch import _systemroot_notepad_path

    return _systemroot_notepad_path()
