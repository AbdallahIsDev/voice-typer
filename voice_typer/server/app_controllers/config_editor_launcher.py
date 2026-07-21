"""CR-24: ConfigEditorLauncher — extracted from
``VoiceTyperApp._open_config_file``.

Owns the "open config.json in the user's editor" feature: spawns the
editor, blocks until it exits, and reloads the config from disk so the
user's saved edits take effect.

The actual logic lived on ``VoiceTyperApp._open_config_file``
(749-855 in the pre-CR-24 ``app.py``).  The behaviour is preserved
verbatim — only the class boundary moved — with two bug fixes folded
in:

    - APP-3 (CR-24): ``self.config.save()`` previously ran OUTSIDE
      ``_config_mutation_lock`` (a TOCTOU race vs. concurrent IPC
      ``set_config``).  Now it runs INSIDE each platform branch's
      ``with self._config_mutation_lock:`` block so the save and the
      editor launch are atomic with respect to concurrent
      ``set_config`` calls.
    - CR-80: the macOS / Linux / Windows branches each call the new
      :meth:`_reload_config_under_lock` helper after the platform-
      specific editor launch.  The helper encapsulates the
      ``type(self.config).load() + log.warning`` pattern that was
      previously inlined in each branch (and was the source of the
      copy-paste drift where the Windows branch reloaded OUTSIDE the
      lock while macOS / Linux reloaded INSIDE).

``VoiceTyperApp`` keeps a thin 1-line delegation
(``def _open_config_file(self): return self.config_editor.open_config_file()``)
so tests that do ``monkeypatch.setattr("voice_typer.server.app.
_open_config_file", ...)`` or ``app._open_config_file()`` keep working
unchanged.

SEC-audit-011 / B-4 / XPLAT-01 / APP-3.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from voice_typer.server.branding import APP_NAME

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class ConfigEditorLauncher:
    """Owns the "open config.json in editor" feature.

    CR-24: extracted from ``VoiceTyperApp._open_config_file``.  The app
    passes itself (``app``) so the launcher can read/write
    ``app.config``, acquire ``app._config_mutation_lock``, and surface
    notifications via ``app.tray.notify``.
    """

    def __init__(self, app: VoiceTyperApp | Any) -> None:
        self._app = app

    def _reload_config_under_lock(self) -> None:
        """CR-80: Reload config from disk.

        Must be called while holding ``_config_mutation_lock`` so the
        reload is consistent with the lock-release point (a concurrent
        IPC ``set_config`` call cannot interleave between the editor
        closing and the reload).

        Failure to reload is logged but does NOT propagate: a malformed
        ``config.json`` (e.g. user mid-save in the editor) shouldn't
        crash the launcher.  The next ``set_config`` IPC call will
        re-validate and surface the error to the UI.
        """
        app = self._app
        try:
            app.config = type(app.config).load()
        except Exception as exc:
            log.warning("[CONFIG] Failed to reload config after editor: %s", exc)

    def open_config_file(self) -> None:
        """Open the config file in the user's default editor.

        XPLAT-01: on Windows the file opens in the user's ``.json`` file
        association (e.g. VS Code, Notepad++, Sublime) instead of being
        forced into Notepad. We obtain the editor process handle via
        ``ShellExecuteEx`` so we can still block until it exits and reload
        afterwards — ``os.startfile`` cannot do this (it returns
        immediately with no handle, which is what caused the old
        reload-after-close / lock-coverage regressions).

        SEC-audit-011 / B-4: ``_config_mutation_lock`` is acquired BEFORE
        spawning the editor and held for the entire editor session (until
        the editor process exits), so a concurrent IPC ``set_config``
        cannot atomically replace ``config.json`` via
        ``_secure_atomic_write`` while the user is mid-edit (a TOCTOU
        race). After the editor exits we reload the config from disk so
        the user's saved edits take effect.

        APP-3 (CR-24 bug fix): ``self.config.save()`` previously ran
        OUTSIDE ``_config_mutation_lock`` (before the platform-specific
        ``with`` block).  This opened a TOCTOU race: our save() writes
        the in-memory config to disk, then a concurrent IPC ``set_config``
        call writes its OWN version to disk via
        ``_secure_atomic_write``, and the editor opens the file written
        by the IPC call — NOT by our save.  The fix moves
        ``self.config.save()`` INSIDE the ``with self._config_mutation_lock:``
        block in each platform branch, so the save and the editor launch
        are atomic with respect to concurrent set_config calls.

        CR-80: each platform branch calls :meth:`_reload_config_under_lock`
        after the platform-specific editor launch.  The helper
        encapsulates the ``type(self.config).load() + log.warning``
        pattern that was previously inlined (with drift — Windows
        reloaded OUTSIDE the lock while macOS / Linux reloaded INSIDE).

        On the rare Windows path where no ``.json`` handler is associated,
        we fall back to the SystemRoot-validated Notepad path (never a bare
        PATH-resolved ``notepad``). macOS uses ``open -W`` and Linux uses
        ``xdg-open``; both block on the editor and reload afterwards.
        """
        app = self._app
        config_file = app.config.config_dir / "config.json"
        # CR-24 / APP-3: save() is now called INSIDE each platform
        # branch's ``with app._config_mutation_lock:`` block (see below).
        # The pre-fix code called save() here, OUTSIDE the lock — see the
        # APP-3 docstring on this method for the TOCTOU race that caused.
        import subprocess

        # Look up the platform helpers + Windows editor-launch helpers
        # from the app module at call time so tests that monkeypatch
        # voice_typer.server.app.{is_windows, is_macos, is_linux,
        # _windows_open_with_default_app, _windows_wait_for_process_exit,
        # _windows_close_process_handle, _systemroot_notepad_path, os}
        # still take effect (mirrors the convention in
        # settings_controller.py).
        from voice_typer.server import app as _app_module

        try:
            if _app_module.is_windows():
                # XPLAT-01 + SEC-audit-011 / B-4: open with the user's
                # default editor (respects .json associations — VS Code,
                # Notepad++, Sublime) and obtain a process handle so we can
                # block until it exits and reload afterward. ``os.startfile``
                # returns immediately with no handle (the cause of the old
                # reload/lock regression), so we use ShellExecuteEx instead.
                # Hold _config_mutation_lock for the whole editor session so
                # a concurrent IPC set_config cannot atomically clobber
                # config.json mid-edit (TOCTOU, SEC-audit-011).
                with app._config_mutation_lock:
                    # APP-3: save inside the lock so the save and the
                    # editor launch are atomic w.r.t. concurrent set_config.
                    if not app.config.save():
                        log.warning("[CONFIG] Failed to save config before opening editor")
                    handle = _app_module._windows_open_with_default_app(str(config_file))
                    if handle is not None:
                        try:
                            _app_module._windows_wait_for_process_exit(handle)
                        finally:
                            _app_module._windows_close_process_handle(handle)
                    else:
                        # No associated handler for .json: use the
                        # SystemRoot-validated Notepad path (SEC-audit-011),
                        # never a bare PATH-resolved "notepad" (cwd tamperable).
                        notepad = _app_module._systemroot_notepad_path()
                        if notepad is not None:
                            subprocess.Popen([str(notepad), str(config_file)]).wait()
                        else:
                            # Last resort: no Notepad at the validated path.
                            # os.startfile is non-blocking, so the reload below
                            # runs immediately; the user can re-trigger a reload
                            # via the UI after editing.
                            _app_module.os.startfile(str(config_file))  # type: ignore[attr-defined]
                    # CR-80: reload from disk (inside the lock) so the
                    # user's saved edits are picked up atomically with
                    # the lock release.
                    self._reload_config_under_lock()
            elif _app_module.is_macos():
                # B-4: ``open -W`` blocks until the editor exits (vanilla
                # ``open`` returns immediately after launching). Hold the
                # lock for the full editor session so a concurrent IPC
                # ``set_config`` call (which goes through
                # ``service.apply_config`` → ``with app._config_mutation_lock``)
                # blocks until the user finishes editing.
                with app._config_mutation_lock:
                    # APP-3: save inside the lock.
                    if not app.config.save():
                        log.warning("[CONFIG] Failed to save config before opening editor")
                    with contextlib.suppress(Exception):
                        subprocess.run(
                            ["open", "-W", str(config_file)],
                            check=False,
                        )
                    # CR-80: reload from disk (inside the lock).
                    self._reload_config_under_lock()
            else:
                # B-4: Linux. ``xdg-open`` may return before the editor
                # closes (depends on the desktop environment — some DEs
                # spawn the editor as a detached process), but we still
                # block on its exit and hold the lock during that window
                # so a concurrent IPC ``set_config`` call can't interleave
                # with the launch. After the spawn returns we reload the
                # config from disk so any saved edits are picked up.
                with app._config_mutation_lock:
                    # APP-3: save inside the lock.
                    if not app.config.save():
                        log.warning("[CONFIG] Failed to save config before opening editor")
                    with contextlib.suppress(Exception):
                        subprocess.run(
                            ["xdg-open", str(config_file)],
                            check=False,
                        )
                    # CR-80: reload from disk (inside the lock).
                    self._reload_config_under_lock()
        except Exception as e:
            log.warning("[CONFIG] Could not open editor: %s", e)
            app.tray.notify(APP_NAME, f"Config file:\n{config_file}")
