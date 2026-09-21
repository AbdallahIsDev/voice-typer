"""Config editor launcher (extracted from VoiceTyperApp._open_config_file).

 SEC-audit-011: opens ``config.json`` in the
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
branches already had, previously a Windows launch exception bubbled
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
respectively, pre-fix these were unbounded and could wedge the IPC
thread forever if the editor hung. The primary Windows
``ShellExecuteEx`` path was already bounded by  in
``voice_typer.server.platform_launch._windows_wait_for_process_exit``.

The platform flags (``is_windows``/``is_macos``/``is_linux``) are
imported at call time from their canonical home
(``voice_typer.server.platform_utils``) so tests that monkeypatch them
there continue to work unchanged. The Windows launch helpers
(``_windows_open_with_default_app`` etc.) are still resolved through the
app-module re-export seam (see ``_resolve``), tests patch those on
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
_EDITOR_SESSION_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _raise_editor_timeout(config_path: Any) -> None:
    """Raise a clear ``TimeoutError`` for an editor session timeout."""

    raise TimeoutError(
        f"Editor session for {config_path} exceeded the "
        f"{_EDITOR_SESSION_TIMEOUT_SECONDS // 60}-minute timeout "
        "and was killed. To recover: save any unsaved edits to a "
        "temporary file (config.json on disk was NOT modified by "
        "the launcher), then re-open the editor via 'Edit config' "
        "to start a fresh session."
    )


def _wait_for_editor_subprocess(proc: subprocess.Popen, config_path: Any) -> None:
    """Wait for *proc* to exit, bounded by ``_EDITOR_SESSION_TIMEOUT_SECONDS``."""

    try:
        proc.wait(timeout=_EDITOR_SESSION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # Kill the editor process so it doesn't keep running in the
        try:
            proc.kill()
        except Exception:
            log.warning(
                "[CONFIG-EDITOR] Failed to kill timed-out editor process",
                exc_info=True,
            )
        # Reap the (now killed) process to avoid a zombie. Best-effort:
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            log.warning(
                "[CONFIG-EDITOR] Editor process did not exit 5s after "
                "SIGKILL/TerminateProcess, it may be stuck in an "
                "unkillable syscall. Leaving it; the launcher will "
                "raise TimeoutError anyway."
            )
        except Exception:
            # Don't mask the original TimeoutExpired with a reaper
            log.warning(
                "[CONFIG-EDITOR] Reaper wait() raised after kill()",
                exc_info=True,
            )
        _raise_editor_timeout(config_path)


def _resolve(name: str, default: Callable[..., Any]) -> Callable[..., Any]:
    """Resolve a Windows launch helper from the ``voice_typer.server.app``"""

    app_mod = sys.modules.get("voice_typer.server.app")
    if app_mod is not None:
        return getattr(app_mod, name, default)
    return default


def _current_platform() -> str:
    """Return the current platform key (``"windows"``/``"macos"``/``"linux"``)."""
    from voice_typer.server.platform_utils import is_macos, is_windows

    if is_windows():
        return "windows"
    if is_macos():
        return "macos"
    return "linux"


def _launch_windows_editor(config_path: Any) -> None:
    """Windows-specific editor launch."""

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
            # bounded wait: see ``_wait_for_editor_subprocess``.
            proc = subprocess.Popen([str(notepad), str(config_path)])
            _wait_for_editor_subprocess(proc, config_path)
        else:
            os.startfile(str(config_path))  # type: ignore[attr-defined]


def _launch_macos_editor(config_path: Any) -> None:
    """macOS-specific editor launch, uses ``open -W`` (blocking)."""

    try:
        subprocess.run(
            ["open", "-W", str(config_path)],
            check=False,
            timeout=_EDITOR_SESSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # ``subprocess.run`` with a timeout already kills and reaps the
        _raise_editor_timeout(config_path)


def _launch_linux_editor(config_path: Any) -> None:
    """Linux-specific editor launch, uses ``xdg-open`` (blocking)."""

    try:
        subprocess.run(
            ["xdg-open", str(config_path)],
            check=False,
            timeout=_EDITOR_SESSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # ``subprocess.run`` with a timeout already kills and reaps the
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
    can access ``_config_mutation_lock``, ``config``, and ``tray``, the
    same attributes the original method used.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    def launch(self, config_path: Any) -> None:
        """Open ``config_path`` in the user's default editor."""

        try:
            # Step 1: save under the lock so the on-disk file is
            with self.app._config_mutation_lock:
                if not self.app.config.save():
                    log.warning("[CONFIG] Failed to save config before opening editor")

            # Step 2: launch the editor WITHOUT holding the lock.
            launcher = _PLATFORM_LAUNCHERS.get(_current_platform())
            if launcher is None:
                log.warning("[CONFIG] No editor launcher for platform")
                return
            # ``TimeoutError`` must propagate so the outer except can
            try:
                launcher(config_path)
            except TimeoutError:
                raise
            except Exception:
                # silently swallow non-timeout launch errors so a
                pass

            # Step 3: reload under the lock so the in-memory Config
            with self.app._config_mutation_lock:
                try:
                    self.app.config = type(self.app.config).load()
                except Exception as exc:
                    log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
                else:
                    # re-wire the in-process mutation lock on the
                    mutation_lock = getattr(self.app, "_config_mutation_lock", None)
                    if mutation_lock is not None:
                        self.app.config.set_mutation_lock(mutation_lock)
                    # ``getattr(..., [])`` is defensive against a
                    reload_warnings = list(getattr(self.app.config, "last_load_warnings", []) or [])
                    if reload_warnings:
                        first = reload_warnings[0]
                        # Truncate the first warning so the tray
                        if len(first) > 160:
                            first = first[:160] + "..."
                        try:
                            self.app.tray.notify(
                                APP_NAME,
                                f"Config loaded with {len(reload_warnings)} warning(s): {first}",
                            )
                        except Exception:
                            # ``tray.notify`` is best-effort, a
                            log.debug(
                                "[CONFIG] tray.notify for reload warnings failed",
                                exc_info=True,
                            )
        except TimeoutError as e:
            # editor session exceeded the bounded timeout.
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
def _default_windows_open_with_default_app(path: str) -> Any:
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
