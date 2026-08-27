"""Config editor launcher (extracted from VoiceTyperApp._open_config_file).

 SEC-audit-011 / B-4: opens ``config.json`` in the
user's default editor and holds ``_config_mutation_lock`` for the full
editor session so a concurrent IPC ``set_config`` cannot atomically
clobber the file mid-edit (TOCTOU race). After the editor exits the
config is reloaded from disk so the user's saved edits take effect.

Windows: opens with the user's ``.json`` association via
``ShellExecuteEx`` (falls back to SystemRoot-validated Notepad, never a
bare PATH-resolved ``notepad``). macOS uses ``open -W``; Linux uses
``xdg-open``. All three branches block on the editor and reload
afterwards.

the three platform branches previously each duplicated the
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

the inner ``contextlib.suppress(Exception)`` wrapper was
replaced with an explicit ``try/except`` that re-raises
``TimeoutError`` (so a 30-minute editor-session timeout surfaces as
a tray notification with a recovery hint) but still swallows other
launch errors (preserving the  contract). The notepad fallback
in ``_launch_windows_editor`` and the POSIX branches
(``_launch_macos_editor`` / ``_launch_linux_editor``) now use a
bounded ``wait(timeout=...)`` / ``subprocess.run(timeout=...)``
respectively — pre-fix these were unbounded and could wedge the IPC
thread forever if the editor hung. The primary Windows
``ShellExecuteEx`` path was already bounded by  in
``voice_typer.server.platform_launch._windows_wait_for_process_exit``.

The platform flags (``is_windows``/``is_macos``/``is_linux``) are
imported at call time from their canonical home
(``voice_typer.server.platform_utils``) so tests that monkeypatch them
there continue to work unchanged. The Windows launch helpers
(``_windows_open_with_default_app`` etc.) are still resolved through the
app-module re-export seam (see ``_resolve``) — tests patch those on
``voice_typer.server.app``.
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


# bounded timeout for editor subprocess waits.
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
# This mirrors the  fix on ``_windows_wait_for_process_exit``
# (``_WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS = 30 * 60 * 1000`` in
# ``voice_typer.server.platform_launch``), which used the same
# 30-minute rationale for the primary Windows ``ShellExecuteEx``
# path. The constant is duplicated here as seconds (not ms) to
# keep the unit consistent with ``Popen.wait(timeout=...)`` /
# ``subprocess.run(timeout=...)`` which both take seconds.
_EDITOR_SESSION_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _raise_editor_timeout(config_path: Any) -> None:
    """Raise a clear ``TimeoutError`` for an editor session timeout.

    called by the platform launchers when the editor
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

    pre-fix the notepad fallback in
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
    """Resolve a Windows launch helper from the ``voice_typer.server.app``
    module's re-export seam, falling back to ``default`` if the app module
    isn't importable yet (e.g. during early import). Tests monkeypatch
    these helper names on the app module."""

    app_mod = sys.modules.get("voice_typer.server.app")
    if app_mod is not None:
        return getattr(app_mod, name, default)
    return default


def _current_platform() -> str:
    """Return the current platform key (``"windows"``/``"macos"``/``"linux"``).

    Imports ``is_windows`` / ``is_macos`` at call time from their
    canonical home (``voice_typer.server.platform_utils``) so tests that
    monkeypatch them there continue to take effect.
    """
    from voice_typer.server.platform_utils import is_macos, is_windows

    if is_windows():
        return "windows"
    if is_macos():
        return "macos"
    return "linux"


# ── Platform launch strategies () ─────────────────────────────────


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
            # bounded wait — see ``_wait_for_editor_subprocess``.
            # Pre-fix this was ``subprocess.Popen([...]).wait()`` with no
            # timeout, blocking the IPC thread forever if Notepad hung.
            proc = subprocess.Popen([str(notepad), str(config_path)])
            _wait_for_editor_subprocess(proc, config_path)
        else:
            os.startfile(str(config_path))  # type: ignore[attr-defined]


def _launch_macos_editor(config_path: Any) -> None:
    """macOS-specific editor launch — uses ``open -W`` (blocking).

    bounded by ``_EDITOR_SESSION_TIMEOUT_SECONDS``. Pre-fix
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

    bounded by ``_EDITOR_SESSION_TIMEOUT_SECONDS``. Pre-fix
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

        The mutation lock is held ONLY for the brief save (before the
        editor opens) and the brief reload (after the editor closes) —
        NOT for the full editor session. Holding the lock for the
        entire editor session blocked the tray thread + every
        concurrent IPC ``set_config`` call for as long as the user
        kept the editor open (potentially the 30-minute timeout
        window), making the app appear frozen.

        The per-platform launch logic is delegated to a strategy
        function from ``_PLATFORM_LAUNCHERS``. All three platform
        branches wrap the launch call in a suppress-non-timeout
        exception filter — the Windows branch historically lacked
        this wrapper (inconsistent with macOS / Linux which already
        had it).

        ``TimeoutError`` raised by the platform launcher
        (when the editor exceeds ``_EDITOR_SESSION_TIMEOUT_SECONDS``)
        is NOT swallowed by the inner suppress — it propagates to the
        outer ``except`` block so the user gets a tray notification
        explaining what happened. Other launch exceptions (e.g. the
        editor binary not found) are still silently swallowed to
        preserve the historical contract.

        TOCTOU note (B-4 / SEC-audit-011 lineage): the original fix
        held the lock for the full editor session to prevent a
        concurrent ``set_config`` from clobbering the file mid-edit.
        That cure was worse than the disease: the lock also blocked
        every other IPC handler that touched the config (tray menu
        state reads, microphone-list polls, etc.) for the entire
        editor session. The split-lock approach below preserves the
        save/reload atomicity (each is still under the lock) while
        releasing the lock during the editor wait. A concurrent
        ``set_config`` during the editor session now succeeds — its
        save will land on disk, and the user's manual edits (if any)
        will be made on top of the latest on-disk state. The editor's
        save (when the user picks "File → Save") then wins, exactly
        as it would if the user had two editors open on the same
        file. The 30-minute bounded timeout on the editor wait is
        preserved (see ``_EDITOR_SESSION_TIMEOUT_SECONDS``).
        """

        try:
            # Phase 1: save under the lock so the on-disk file is
            # consistent before the editor opens it. The lock is
            # released immediately after the save returns — we do NOT
            # hold it during the editor session.
            with self.app._config_mutation_lock:
                if not self.app.config.save():
                    log.warning("[CONFIG] Failed to save config before opening editor")

            # Phase 2: launch the editor WITHOUT holding the lock.
            # This fix launches the editor without the lock: the tray thread + every IPC
            # handler that acquires ``_config_mutation_lock`` (tray
            # menu state, microphone list, set_config, etc.) stays
            # responsive while the user edits the file. The 30-minute
            # bounded wait is preserved by the platform launcher (see
            # ``_EDITOR_SESSION_TIMEOUT_SECONDS``).
            launcher = _PLATFORM_LAUNCHERS.get(_current_platform())
            if launcher is None:
                log.warning("[CONFIG] No editor launcher for platform")
                return
            # ``TimeoutError`` must propagate so the outer except can
            # notify the user. Other launch exceptions (e.g. editor
            # binary not found) are still suppressed (historical
            # contract on macOS / Linux).
            try:
                launcher(config_path)
            except TimeoutError:
                raise
            except Exception:
                # silently swallow non-timeout launch errors so a
                # transient launch failure doesn't surface as a tray
                # notification (the historical behavior on macOS /
                # Linux).
                pass

            # Phase 3: reload under the lock so the in-memory Config
            # swap is atomic w.r.t. concurrent IPC readers. The lock
            # is held only for the duration of the load + re-wire,
            # not for the editor wait that preceded it.
            with self.app._config_mutation_lock:
                try:
                    self.app.config = type(self.app.config).load()
                except Exception as exc:
                    log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
                else:
                    # re-wire the in-process mutation lock on the
                    # freshly reloaded ``Config`` instance.
                    # ``Config.load()`` returns a brand-new object
                    # whose ``_mutation_lock`` instance attribute is
                    # unset (falls back to the ``ClassVar`` default of
                    # ``None`` — see config.py:1081), so without this
                    # re-wiring every subsequent ``config.save()`` would
                    # run unlocked until the next app restart, re-opening
                    # the torn-snapshot race that the
                    # ``VoiceTyperApp.__init__`` wiring closed. The lock
                    # object itself is owned by ``VoiceTyperApp`` and
                    # survives the config reload, so we just re-register
                    # the same ``RLock`` reference on the new Config.
                    mutation_lock = getattr(self.app, "_config_mutation_lock", None)
                    if mutation_lock is not None:
                        self.app.config.set_mutation_lock(mutation_lock)
                    # Surface ``last_load_warnings`` to the user as a
                    # tray notification. Pre-fix, a hand-edited
                    # ``config.json`` with an invalid value (e.g.
                    # ``asr_backend="invalid"``) loaded silently — the
                    # user editing the file got no toast, no IPC error,
                    # no UI banner. The sanitizer (config_sanitizer.py)
                    # now ships ``last_load_warnings`` to the renderer
                    # via the ``get_config`` IPC response, but that
                    # only fires on the NEXT ``get_config`` poll.
                    # A tray notification here closes the immediate-
                    # feedback gap: the user sees "Config loaded with
                    # N warnings" the moment the editor exits.
                    #
                    # ``getattr(..., [])`` is defensive against a
                    # Config-like test double that didn't set the
                    # attribute (the production Config always sets it
                    # in ``__post_init__`` via ``object.__setattr__``).
                    # ``or []`` handles the ``None`` sentinel from
                    # ``__post_init__`` (the attribute is initialized to
                    # ``None`` and only replaced with a list in
                    # ``load()`` once warnings are collected).
                    reload_warnings = list(getattr(self.app.config, "last_load_warnings", []) or [])
                    if reload_warnings:
                        first = reload_warnings[0]
                        # Truncate the first warning so the tray
                        # notification stays readable on a single line.
                        # The full warning list is available to the
                        # renderer via the next ``get_config`` IPC
                        # response (see sanitize_config_for_ipc).
                        if len(first) > 160:
                            first = first[:160] + "..."
                        try:
                            self.app.tray.notify(
                                APP_NAME,
                                f"Config loaded with {len(reload_warnings)} warning(s): {first}",
                            )
                        except Exception:
                            # ``tray.notify`` is best-effort — a
                            # failure here (e.g. the tray icon isn't
                            # initialized yet on early startup) must
                            # NOT mask the successful config reload.
                            # The warnings are still in
                            # ``last_load_warnings`` and will surface
                            # via the next ``get_config`` IPC poll.
                            log.debug(
                                "[CONFIG] tray.notify for reload warnings failed",
                                exc_info=True,
                            )
        except TimeoutError as e:
            # editor session exceeded the bounded timeout.
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
