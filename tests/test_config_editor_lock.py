"""B-4: Config editor mutation lock regression tests.

The Windows notepad path in ``VoiceTyperApp._open_config_file`` (now
delegated to :class:`voice_typer.server.config_editor.ConfigEditorLauncher`)
has always acquired ``_config_mutation_lock`` for the full editor
session so a concurrent IPC ``set_config`` call can't atomically
overwrite ``config.json`` while Notepad is mid-edit (SEC-audit-011).

B-4 fixes the same TOCTOU race on the macOS (``open``) and Linux
(``xdg-open``) paths: they previously used non-blocking
``subprocess.Popen`` and did NOT acquire the lock, so a concurrent IPC
``set_config`` call (which goes through ``service.apply_config`` →
``with app._config_mutation_lock``) could silently overwrite the user's
manual edits while the editor was still open.

These tests pin the fix BEHAVIORALLY (no ``inspect.getsource``):

1. For every platform branch: when the editor is open, a concurrent
   ``set_config`` call (mimicked by trying to acquire the same lock from
   another thread) blocks until the editor closes, then proceeds.

2. ``config.save()`` happens INSIDE ``_config_mutation_lock`` (the lock
   is held when save is called) — pins CR-015.

3. The macOS branch uses ``open -W`` (not vanilla ``open``) and the
   macOS/Linux branches do NOT use non-blocking ``subprocess.Popen``.

4. After the editor closes, the config is reloaded from disk so the
   user's saved edits are picked up (all three platforms).
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

# ── APP-3 / CR-015: config.save() must happen inside _config_mutation_lock ─


class TestSaveInsideLock:
    """Behavioral replacement for the source-string APP-3 tests.

    Verifies ``config.save()`` is called WHILE ``_config_mutation_lock``
    is held (not before the lock is acquired) and exactly once per
    launch — for every platform branch.
    """

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_save_called_inside_lock(self, tmp_config_dir, monkeypatch, platform):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, platform)

        save_lock_states: list[bool] = []
        original_save = app.config.save

        def _tracking_save():
            owned = _lock_owned(app)
            save_lock_states.append(owned)
            return original_save()

        app.config.save = _tracking_save

        editor = _FakeEditor()
        _install_fake_editor(monkeypatch, editor, platform)

        thread, errors = _run_open_config_in_thread(app)
        assert editor.opened.wait(timeout=5.0)
        editor.close_event.set()
        thread.join(timeout=5.0)

        assert errors == [], f"_open_config_file raised: {errors}"
        assert len(save_lock_states) == 1, (
            "CR-015: config.save() must be called exactly once per "
            f"_open_config_file launch (got {len(save_lock_states)} calls)."
        )
        assert save_lock_states[0] is True, (
            "CR-015: config.save() must be called INSIDE "
            "_config_mutation_lock (the lock must be held when save runs) "
            "so a concurrent IPC set_config can't overwrite the file "
            "between our save and the editor launch (TOCTOU race)."
        )


# ── B-4: macOS must use `open -W`, macOS/Linux must not use bare Popen ────


class TestB4MacosLinuxCommandShape:
    """Behavioral replacement for the source-string command-shape tests.

    Verifies:
    - macOS uses ``open -W`` (not vanilla ``open``).
    - macOS and Linux do NOT spawn a non-blocking ``subprocess.Popen``.
    """

    def test_macos_uses_open_w(self, tmp_config_dir, monkeypatch):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, "macos")

        editor = _FakeEditor()
        _install_fake_editor(monkeypatch, editor, "macos")

        thread, errors = _run_open_config_in_thread(app)
        assert editor.opened.wait(timeout=5.0)
        editor.close_event.set()
        thread.join(timeout=5.0)

        assert errors == [], f"_open_config_file raised: {errors}"
        assert editor.call_args is not None
        assert editor.call_args[0] == "open", f"macOS path must invoke 'open'; got {editor.call_args[0]!r}"
        assert "-W" in editor.call_args, (
            "B-4: macOS path must use 'open -W' so the spawn blocks until "
            f"the editor exits. Args were: {editor.call_args!r}"
        )

    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_no_bare_popen(self, tmp_config_dir, monkeypatch, platform):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, platform)

        popen_calls: list = []
        import subprocess as _subprocess

        original_popen = _subprocess.Popen

        def _tracking_popen(*args, **kwargs):
            popen_calls.append(args)
            return original_popen(*args, **kwargs)

        monkeypatch.setattr(_subprocess, "Popen", _tracking_popen)

        editor = _FakeEditor()
        _install_fake_editor(monkeypatch, editor, platform)

        thread, errors = _run_open_config_in_thread(app)
        assert editor.opened.wait(timeout=5.0)
        editor.close_event.set()
        thread.join(timeout=5.0)

        assert errors == [], f"_open_config_file raised: {errors}"
        assert popen_calls == [], (
            f"B-4: {platform} branch must NOT use non-blocking "
            f"subprocess.Popen; use subprocess.run (blocking) inside the "
            f"lock instead. Popen calls: {popen_calls}"
        )


# ── B-4: all platforms reload config after editor closes ─────────────────


class TestB4ReloadAfterEditor:
    """Behavioral replacement for the source-string reload-count test.

    Verifies every platform branch reloads the config from disk after
    the editor closes (picks up the user's saved edits). Writes a
    different value to disk while the editor is "open" and checks the
    in-memory config reflects it after the launch returns.
    """

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_config_reloaded_after_editor_closes(self, tmp_config_dir, monkeypatch, platform):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, platform)

        if platform == "windows":
            monkeypatch.setattr(
                "voice_typer.server.app._windows_open_with_default_app",
                lambda path: None,
            )
            monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

        from voice_typer.server.config import Config

        original_load = Config.load
        load_calls: list = []

        def _tracking_load(*args, **kwargs):
            result = original_load(*args, **kwargs)
            load_calls.append(result)
            return result

        monkeypatch.setattr(Config, "load", _tracking_load)

        editor = _FakeEditor()

        config_path = app.config.config_dir / "config.json"

        def _run_with_disk_write(args, **kwargs):
            result = editor.run(args, **kwargs)
            config_path.write_text(json.dumps({"show_notifications": False}), encoding="utf-8")
            return result

        def _popen_wait_with_disk_write(args, **kwargs):
            result = editor.popen_wait(args, **kwargs)
            config_path.write_text(json.dumps({"show_notifications": False}), encoding="utf-8")
            return result

        import subprocess as _subprocess

        if platform == "windows":

            class _FakeProc:
                def __init__(self, a):
                    self._a = a

                def wait(self):
                    return _popen_wait_with_disk_write(self._a)

            def _popen(args, **kwargs):
                return _FakeProc(args)

            monkeypatch.setattr(_subprocess, "Popen", _popen)
        else:
            monkeypatch.setattr(_subprocess, "run", _run_with_disk_write)

        assert app.config.show_notifications is True

        app._open_config_file()

        assert len(load_calls) >= 1, (
            f"B-4: {platform} branch must call Config.load() after the "
            "editor closes so the user's saved edits are picked up."
        )
        assert app.config.show_notifications is False, (
            f"B-4: {platform} branch: after the editor closes, the "
            "in-memory config must reflect the user's saved edits on disk."
        )


# ── Runtime behavior (lock held for full editor session) ─────────────────


def _make_app(tmp_config_dir, monkeypatch):
    """Build a VoiceTyperApp with mocked hardware/GUI deps.

    Mirrors the ``app`` fixture in tests/test_app.py but inlined here
    so this test file is self-contained and doesn't depend on
    test_app.py's fixture state.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    return instance


def _force_platform(monkeypatch, platform: str) -> None:
    """Monkeypatch the platform helpers in voice_typer.server.app."""
    flags = {
        "windows": (True, False, False),
        "macos": (False, True, False),
        "linux": (False, False, True),
    }
    win, mac, lin = flags[platform]
    monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: win)
    monkeypatch.setattr("voice_typer.server.app.is_macos", lambda: mac)
    monkeypatch.setattr("voice_typer.server.app.is_linux", lambda: lin)


def _lock_owned(app) -> bool:
    lock = app._config_mutation_lock
    if hasattr(lock, "_is_owned"):
        return lock._is_owned()
    return True


class _FakeEditor:
    """Simulates an editor that stays open until signaled to close.

    When ``subprocess.run`` (or ``Popen().wait()``) is called, the
    fake editor:

    1. Sets ``opened`` so the test knows the editor has launched
       (and therefore the lock should be held).
    2. Waits on ``close_event`` so the call blocks — mimicking the
       editor being open.
    3. Returns after ``close_event`` is set — mimicking the editor
       closing.
    """

    def __init__(self) -> None:
        self.opened = threading.Event()
        self.close_event = threading.Event()
        self.call_count = 0
        self.call_args: list | None = None
        self.lock_held_when_called: list[bool] = []  # one per call

    def run(self, args, **kwargs):
        self.call_count += 1
        self.call_args = args
        self.opened.set()
        self.close_event.wait(timeout=10.0)
        return MagicMock(returncode=0)

    def popen_wait(self, args, **kwargs):
        """Mimics Popen(args).wait() — same blocking semantics as run()."""
        self.call_count += 1
        self.call_args = args
        self.opened.set()
        self.close_event.wait(timeout=10.0)
        return MagicMock(returncode=0)


def _install_fake_editor(monkeypatch, editor: _FakeEditor, platform: str) -> None:
    """Wire ``editor`` into the right subprocess hook for ``platform``."""
    import subprocess as _subprocess

    if platform == "windows":
        monkeypatch.setattr(
            "voice_typer.server.app._windows_open_with_default_app",
            lambda path: None,
        )
        monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

        class _FakeProc:
            def __init__(self, a):
                self._a = a

            def wait(self):
                return editor.popen_wait(self._a)

        monkeypatch.setattr(_subprocess, "Popen", lambda a, **k: _FakeProc(a))
    else:
        monkeypatch.setattr(_subprocess, "run", lambda a, **k: editor.run(a, **k))


def _run_open_config_in_thread(app):
    """Run ``app._open_config_file()`` in a background thread.

    Returns the thread handle. Use ``thread.join(timeout=...)`` to wait
    for completion.
    """
    errors: list = []

    def _target():
        try:
            app._open_config_file()
        except Exception as exc:  # pragma: no cover — re-raised below
            errors.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread, errors


def _assert_concurrent_set_config_blocks(app, editor, timeout=5.0):
    """Mimic a concurrent IPC ``set_config`` call.

    Spawns a thread that tries to acquire ``app._config_mutation_lock``
    (exactly what ``service.apply_config`` does on the IPC set_config
    path). Verifies the thread blocks while the editor is open, then
    completes after the editor closes.
    """
    acquired = threading.Event()

    def _acquire_lock():
        with app._config_mutation_lock:
            acquired.set()

    setter_thread = threading.Thread(target=_acquire_lock, daemon=True)
    setter_thread.start()

    time.sleep(0.15)
    assert not acquired.is_set(), (
        "B-4: a concurrent set_config call (acquiring _config_mutation_lock) "
        "must BLOCK while the config editor is open — but the lock was "
        "acquired immediately, which means _open_config_file is not holding "
        "the lock for the full editor session."
    )

    editor.close_event.set()

    assert acquired.wait(timeout=timeout), (
        "B-4: after the editor closes, the blocked set_config call must proceed and acquire _config_mutation_lock."
    )
    setter_thread.join(timeout=2.0)
    assert not setter_thread.is_alive(), "setter thread should have exited"


class TestB4MacosRuntime:
    """Runtime test for the macOS ``open -W`` branch."""

    def test_lock_held_during_editor_session(self, tmp_config_dir, monkeypatch):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, "macos")

        editor = _FakeEditor()

        def _run(args, **kwargs):
            assert _lock_owned(app), (
                "B-4: _config_mutation_lock must be acquired by the "
                "current thread BEFORE subprocess.run is called on macOS."
            )
            return editor.run(args, **kwargs)

        import subprocess as _subprocess

        monkeypatch.setattr(_subprocess, "run", _run)

        thread, errors = _run_open_config_in_thread(app)

        assert editor.opened.wait(timeout=5.0), "Editor should have been launched (subprocess.run called) within 5s."

        _assert_concurrent_set_config_blocks(app, editor)

        thread.join(timeout=5.0)
        assert not thread.is_alive(), "_open_config_file should have returned after the editor closed."
        assert errors == [], f"_open_config_file raised: {errors}"

        assert editor.call_args is not None
        assert editor.call_args[0] == "open", f"Expected 'open' command, got {editor.call_args[0]!r}"
        assert "-W" in editor.call_args, (
            "B-4: macOS path must use 'open -W' so the spawn blocks until "
            f"the editor exits. Args were: {editor.call_args!r}"
        )


class TestB4LinuxRuntime:
    """Runtime test for the Linux ``xdg-open`` branch."""

    def test_lock_held_during_editor_session(self, tmp_config_dir, monkeypatch):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, "linux")

        editor = _FakeEditor()

        def _run(args, **kwargs):
            assert _lock_owned(app), (
                "B-4: _config_mutation_lock must be acquired by the "
                "current thread BEFORE subprocess.run is called on Linux."
            )
            return editor.run(args, **kwargs)

        import subprocess as _subprocess

        monkeypatch.setattr(_subprocess, "run", _run)

        thread, errors = _run_open_config_in_thread(app)

        assert editor.opened.wait(timeout=5.0), "Editor should have been launched (subprocess.run called) within 5s."

        _assert_concurrent_set_config_blocks(app, editor)

        thread.join(timeout=5.0)
        assert not thread.is_alive(), "_open_config_file should have returned after the editor closed."
        assert errors == [], f"_open_config_file raised: {errors}"

        assert editor.call_args is not None
        assert editor.call_args[0] == "xdg-open", f"Expected 'xdg-open' command, got {editor.call_args[0]!r}"


class TestB4WindowsRuntime:
    """Runtime test for the Windows notepad branch (parity check).

    The Windows branch already held the lock pre-B-4. This test pins
    that behavior so a future refactor doesn't regress it.
    """

    def test_lock_held_during_editor_session(self, tmp_config_dir, monkeypatch):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, "windows")
        monkeypatch.setattr(
            "voice_typer.server.app._windows_open_with_default_app",
            lambda path: None,
        )
        monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

        editor = _FakeEditor()

        class _FakeProc:
            def __init__(self, args):
                self._args = args

            def wait(self):
                return editor.popen_wait(self._args)

        import subprocess as _subprocess

        def _popen(args, **kwargs):
            assert _lock_owned(app), (
                "SEC-audit-011: _config_mutation_lock must be acquired BEFORE subprocess.Popen is called on Windows."
            )
            return _FakeProc(args)

        monkeypatch.setattr(_subprocess, "Popen", _popen)

        thread, errors = _run_open_config_in_thread(app)

        assert editor.opened.wait(timeout=5.0), "Editor should have been launched (subprocess.Popen called) within 5s."

        _assert_concurrent_set_config_blocks(app, editor)

        thread.join(timeout=5.0)
        assert not thread.is_alive(), "_open_config_file should have returned after the editor closed."
        assert errors == [], f"_open_config_file raised: {errors}"


class TestB4ReloadPicksUpDiskChanges:
    """After the editor closes, ``_open_config_file`` must reload config.

    This is what makes the user's saved edits visible to the running
    app. Without the reload, the in-memory Config would diverge from
    disk after every edit.
    """

    def test_config_reloaded_after_macos_editor_closes(self, tmp_config_dir, monkeypatch):
        app = _make_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, "macos")

        from voice_typer.server.config import Config

        original_load = Config.load
        load_calls: list = []

        def _tracking_load(*args, **kwargs):
            result = original_load(*args, **kwargs)
            load_calls.append(result)
            return result

        monkeypatch.setattr(Config, "load", _tracking_load)

        editor = _FakeEditor()

        import subprocess as _subprocess

        def _run(args, **kwargs):
            result = editor.run(args, **kwargs)
            config_path = app.config.config_dir / "config.json"
            config_path.write_text(json.dumps({"show_notifications": False}), encoding="utf-8")
            return result

        monkeypatch.setattr(_subprocess, "run", _run)

        assert app.config.show_notifications is True

        app._open_config_file()

        assert len(load_calls) >= 1, (
            "B-4: Config.load() must be called after the editor closes so the user's saved edits are picked up."
        )
        assert app.config.show_notifications is False, (
            "B-4: after the editor closes, the in-memory config must reflect the user's saved edits on disk."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
