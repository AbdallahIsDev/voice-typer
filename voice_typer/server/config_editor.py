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

The platform helpers (``is_windows``/``is_macos``/``is_linux``) and the
Windows launch helpers (``_windows_open_with_default_app`` etc.) are
resolved from the owning app's module namespace at call time so existing
tests that ``monkeypatch.setattr("voice_typer.server.app.is_windows",
...)`` continue to work unchanged.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


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
            subprocess.Popen([str(notepad), str(config_path)]).wait()
        else:
            os.startfile(str(config_path))  # type: ignore[attr-defined]


def _launch_macos_editor(config_path: Any) -> None:
    """macOS-specific editor launch — uses ``open -W`` (blocking)."""

    subprocess.run(["open", "-W", str(config_path)], check=False)


def _launch_linux_editor(config_path: Any) -> None:
    """Linux-specific editor launch — uses ``xdg-open`` (blocking)."""

    subprocess.run(["xdg-open", str(config_path)], check=False)


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
        """

        try:
            with self.app._config_mutation_lock:
                if not self.app.config.save():
                    log.warning("[CONFIG] Failed to save config before opening editor")
                launcher = _PLATFORM_LAUNCHERS.get(_current_platform())
                if launcher is None:
                    log.warning("[CONFIG] No editor launcher for platform")
                    return
                with contextlib.suppress(Exception):
                    launcher(config_path)
                try:
                    self.app.config = type(self.app.config).load()
                except Exception as exc:
                    log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
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
