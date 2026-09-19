"""
SEC-audit-011: Config editor mutation lock regression tests.
SEC-audit-011 fix holds the lock continuously from the pre-editor save
"""

from __future__ import annotations

import json
import subprocess
import threading
from unittest.mock import MagicMock

import pytest

from tests.fixtures.app_helpers import make_voice_typer_app

_ORIGINAL_RUN = subprocess.run


class TestSaveInsideLock:
    """Behavioral replacement for the source-string APP-3 tests."""

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_save_called_inside_lock(self, tmp_config_dir, monkeypatch, platform):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
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


class TestMacosLinuxCommandShape:
    """Behavioral replacement for the source-string command-shape tests."""

    def test_macos_uses_open_w(self, tmp_config_dir, monkeypatch):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
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
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
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


class TestReloadAfterEditor:
    """Behavioral replacement for the source-string reload-count test."""

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_config_reloaded_after_editor_closes(self, tmp_config_dir, monkeypatch, platform):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
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
            if not _is_editor_launch(args):
                return original_run(args, **kwargs)
            result = editor.run(args, **kwargs)
            config_path.write_text(json.dumps({"show_notifications": False}), encoding="utf-8")
            return result

        def _popen_wait_with_disk_write(args, **kwargs):
            result = editor.popen_wait(args, **kwargs)
            config_path.write_text(json.dumps({"show_notifications": False}), encoding="utf-8")
            return result

        import subprocess as _subprocess

        original_run = _subprocess.run

        if platform == "windows":

            class _FakeProc:
                def __init__(self, a):
                    self._a = a

                # ``wait`` now accepts an optional ``timeout`` kwarg
                def wait(self, timeout=None):
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


def _force_platform(monkeypatch, platform: str) -> None:
    """Monkeypatch the platform helpers in voice_typer.server.app."""
    flags = {
        "windows": (True, False, False),
        "macos": (False, True, False),
        "linux": (False, False, True),
    }
    win, mac, lin = flags[platform]
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: win)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: mac)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_linux", lambda: lin)


def _lock_owned(app) -> bool:
    lock = app._config_mutation_lock
    if hasattr(lock, "_is_owned"):
        return lock._is_owned()
    return True


class _FakeEditor:
    """Simulates an editor that stays open until signaled to close."""

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
        """Mimics Popen(args).wait(), same blocking semantics as run()."""
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

            # ``wait`` now accepts an optional ``timeout`` kwarg
            def wait(self, timeout=None):
                return editor.popen_wait(self._a)

        monkeypatch.setattr(_subprocess, "Popen", lambda a, **k: _FakeProc(a))
    else:
        original_run = _subprocess.run

        def _fake_run(args, **kwargs):
            if not _is_editor_launch(args):
                return original_run(args, **kwargs)
            return editor.run(args, **kwargs)

        monkeypatch.setattr(_subprocess, "run", _fake_run)


def _is_editor_launch(args) -> bool:
    """True when *args* is the editor-launch subprocess command."""
    return bool(args) and str(args[0]).lower() in ("open", "xdg-open")


def _run_open_config_in_thread(app):
    """Run ``app._open_config_file()`` in a background thread."""
    errors: list = []

    def _target():
        try:
            app._open_config_file()
        except Exception as exc:  # pragma: no cover, re-raised below
            errors.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread, errors


def _assert_concurrent_set_config_blocks_then_proceeds(app, editor):
    """Mimic a concurrent IPC ``set_config`` call (SEC-audit-011)."""
    import time as _time

    acquired = threading.Event()

    def _acquire_lock():
        with app._config_mutation_lock:
            acquired.set()

    setter_thread = threading.Thread(target=_acquire_lock, daemon=True)
    setter_thread.start()

    # Give the setter thread a moment to attempt the acquire. The lock
    _time.sleep(0.15)
    assert not acquired.is_set(), (
        "SEC-audit-011: a concurrent set_config call (acquiring "
        "_config_mutation_lock) must BLOCK while the config editor is "
        "open, but the lock was acquired within 0.15s, which means "
        "_open_config_file is NOT holding the lock for the full editor "
        "session (the pre-SEC-audit-011 'split-lock' relaxation)."
    )

    # Closing the editor lets the open-config thread release the lock,
    editor.close_event.set()

    assert acquired.wait(timeout=5.0), (
        "after the editor closes, the blocked set_config call must "
        "proceed and acquire _config_mutation_lock (the lock is "
        "released once the editor exits and the reload completes)."
    )
    setter_thread.join(timeout=2.0)
    assert not setter_thread.is_alive(), "setter thread should have exited"


class TestMacosRuntime:
    """Runtime test for the macOS ``open -W`` branch (SEC-audit-011)."""

    def test_lock_held_during_editor_session(self, tmp_config_dir, monkeypatch):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, "macos")

        editor = _FakeEditor()

        original_run = _ORIGINAL_RUN

        def _run(args, **kwargs):
            if not _is_editor_launch(args):
                return original_run(args, **kwargs)
            assert _lock_owned(app), (
                "SEC-audit-011: _config_mutation_lock must be HELD by "
                "the current thread when subprocess.run is called on "
                "macOS, the lock is acquired before the pre-editor "
                "save and held continuously through the editor wait and "
                "post-editor reload. Releasing it during the editor "
                "wait (the XV-3 'split-lock' relaxation) re-opens the "
                "TOCTOU race with a concurrent IPC set_config."
            )
            return editor.run(args, **kwargs)

        import subprocess as _subprocess

        monkeypatch.setattr(_subprocess, "run", _run)

        thread, errors = _run_open_config_in_thread(app)

        assert editor.opened.wait(timeout=5.0), "Editor should have been launched (subprocess.run called) within 5s."

        _assert_concurrent_set_config_blocks_then_proceeds(app, editor)

        thread.join(timeout=5.0)
        assert not thread.is_alive(), "_open_config_file should have returned after the editor closed."
        assert errors == [], f"_open_config_file raised: {errors}"

        assert editor.call_args is not None
        assert editor.call_args[0] == "open", f"Expected 'open' command, got {editor.call_args[0]!r}"
        assert "-W" in editor.call_args, (
            f"macOS path must use 'open -W' so the spawn blocks until the editor exits. Args were: {editor.call_args!r}"
        )


class TestLinuxRuntime:
    """Runtime test for the Linux ``xdg-open`` branch (SEC-audit-011)."""

    def test_lock_held_during_editor_session(self, tmp_config_dir, monkeypatch):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
        _force_platform(monkeypatch, "linux")

        editor = _FakeEditor()

        original_run = _ORIGINAL_RUN

        def _run(args, **kwargs):
            if not _is_editor_launch(args):
                return original_run(args, **kwargs)
            assert _lock_owned(app), (
                "SEC-audit-011: _config_mutation_lock must be HELD by "
                "the current thread when subprocess.run is called on "
                "Linux, the lock is acquired before the pre-editor "
                "save and held continuously through the editor wait and "
                "post-editor reload."
            )
            return editor.run(args, **kwargs)

        import subprocess as _subprocess

        monkeypatch.setattr(_subprocess, "run", _run)

        thread, errors = _run_open_config_in_thread(app)

        assert editor.opened.wait(timeout=5.0), "Editor should have been launched (subprocess.run called) within 5s."

        _assert_concurrent_set_config_blocks_then_proceeds(app, editor)

        thread.join(timeout=5.0)
        assert not thread.is_alive(), "_open_config_file should have returned after the editor closed."
        assert errors == [], f"_open_config_file raised: {errors}"

        assert editor.call_args is not None
        assert editor.call_args[0] == "xdg-open", f"Expected 'xdg-open' command, got {editor.call_args[0]!r}"


class TestWindowsRuntime:
    """Runtime test for the Windows notepad branch (SEC-audit-011)."""

    def test_lock_held_during_editor_session(self, tmp_config_dir, monkeypatch):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
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

            # ``wait`` now accepts an optional ``timeout`` kwarg
            def wait(self, timeout=None):
                return editor.popen_wait(self._args)

        import subprocess as _subprocess

        def _popen(args, **kwargs):
            assert _lock_owned(app), (
                "SEC-audit-011: _config_mutation_lock must be HELD by "
                "the current thread when subprocess.Popen is called on "
                "Windows, the lock is acquired before the pre-editor "
                "save and held continuously through the editor wait and "
                "post-editor reload."
            )
            return _FakeProc(args)

        monkeypatch.setattr(_subprocess, "Popen", _popen)

        thread, errors = _run_open_config_in_thread(app)

        assert editor.opened.wait(timeout=5.0), "Editor should have been launched (subprocess.Popen called) within 5s."

        _assert_concurrent_set_config_blocks_then_proceeds(app, editor)

        thread.join(timeout=5.0)
        assert not thread.is_alive(), "_open_config_file should have returned after the editor closed."
        assert errors == [], f"_open_config_file raised: {errors}"


class TestReloadPicksUpDiskChanges:
    """After the editor closes, ``_open_config_file`` must reload config."""

    def test_config_reloaded_after_macos_editor_closes(self, tmp_config_dir, monkeypatch):
        app = make_voice_typer_app(tmp_config_dir, monkeypatch)
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

        original_run = _subprocess.run

        def _run(args, **kwargs):
            if not _is_editor_launch(args):
                return original_run(args, **kwargs)
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


class TestEditorTimeouts:
    """every ``subprocess.Popen().wait()`` / ``subprocess.run()``"""

    def test_windows_notepad_fallback_passes_timeout_to_wait(self, monkeypatch):
        """Notepad fallback calls ``proc.wait(timeout=1800)``."""
        from voice_typer.server import config_editor

        # Force the Windows branch by stubbing the resolved helpers.
        monkeypatch.setattr(
            config_editor,
            "_resolve",
            lambda name, default: {
                "_windows_open_with_default_app": lambda path: None,
                "_windows_wait_for_process_exit": lambda handle: None,
                "_windows_close_process_handle": lambda handle: None,
                "_systemroot_notepad_path": lambda: "/C/Windows/System32/notepad.exe",
            }.get(name, default),
        )

        captured: dict = {}

        class _FakeProc:
            def __init__(self, args):
                self.args = args

            def wait(self, timeout=None):
                captured["timeout"] = timeout
                return 0

            def kill(self):
                captured["kill_called"] = True

        monkeypatch.setattr(config_editor.subprocess, "Popen", lambda a: _FakeProc(a))

        config_editor._launch_windows_editor("/tmp/config.json")

        assert captured["timeout"] == 1800, (
            f"notepad fallback must call proc.wait(timeout=1800) (30 minutes). Got timeout={captured['timeout']!r}."
        )
        assert "kill_called" not in captured, "kill() must NOT be called when wait() returns normally."

    def test_windows_notepad_fallback_kills_and_raises_on_timeout(self, monkeypatch):
        """Notepad fallback kills the proc and raises TimeoutError on timeout."""
        from voice_typer.server import config_editor

        monkeypatch.setattr(
            config_editor,
            "_resolve",
            lambda name, default: {
                "_windows_open_with_default_app": lambda path: None,
                "_windows_wait_for_process_exit": lambda handle: None,
                "_windows_close_process_handle": lambda handle: None,
                "_systemroot_notepad_path": lambda: "/C/Windows/System32/notepad.exe",
            }.get(name, default),
        )

        killed: list[bool] = []
        reaped: list[float] = []

        class _FakeProc:
            def __init__(self, args):
                self.args = args

            def wait(self, timeout=None):
                if timeout == 1800:
                    raise subprocess.TimeoutExpired(cmd=self.args, timeout=1800)
                # Second wait() (the reaper after kill()) returns normally.
                reaped.append(timeout)
                return 0

            def kill(self):
                killed.append(True)

        monkeypatch.setattr(config_editor.subprocess, "Popen", lambda a: _FakeProc(a))

        with pytest.raises(TimeoutError) as exc_info:
            config_editor._launch_windows_editor("/tmp/config.json")

        assert killed == [True], (
            "notepad fallback must call proc.kill() when "
            "wait() times out, so the editor process doesn't keep "
            "running in the background."
        )
        assert len(reaped) == 1, "notepad fallback must reap the killed process via a second proc.wait() call."
        msg = str(exc_info.value)
        assert "30-minute timeout" in msg, f"TimeoutError message must mention '30-minute timeout'. Got: {msg!r}"
        assert "config.json" in msg, f"TimeoutError message must mention the config path. Got: {msg!r}"
        assert "temporary file" in msg, (
            f"TimeoutError message must include the recovery hint about saving to a temporary file. Got: {msg!r}"
        )

    def test_macos_launch_passes_timeout_to_subprocess_run(self, monkeypatch):
        """macOS ``open -W`` is called with ``timeout=1800``."""
        from voice_typer.server import config_editor

        captured: dict = {}

        def _fake_run(args, **kwargs):
            captured["args"] = args
            captured["timeout"] = kwargs.get("timeout")
            captured["check"] = kwargs.get("check")
            return MagicMock(returncode=0)

        monkeypatch.setattr(config_editor.subprocess, "run", _fake_run)

        config_editor._launch_macos_editor("/tmp/config.json")

        assert captured["args"][0] == "open", f"macOS path must invoke 'open'; got {captured['args'][0]!r}"
        assert "-W" in captured["args"], f"B-4: macOS path must use 'open -W'. Args: {captured['args']!r}"
        assert captured["timeout"] == 1800, (
            f"macOS path must pass timeout=1800 (30 minutes) to subprocess.run. Got timeout={captured['timeout']!r}."
        )
        assert captured["check"] is False, "B-4: macOS path must keep check=False (don't raise on non-zero exit)."

    def test_macos_launch_raises_timeout_error_on_timeout(self, monkeypatch):
        """macOS path converts TimeoutExpired -> TimeoutError."""
        from voice_typer.server import config_editor

        def _fake_run(args, **kwargs):
            assert kwargs.get("timeout") == 1800, (
                f"macOS path must call subprocess.run with timeout=1800. Got: {kwargs.get('timeout')!r}"
            )
            raise subprocess.TimeoutExpired(cmd=args, timeout=1800)

        monkeypatch.setattr(config_editor.subprocess, "run", _fake_run)

        with pytest.raises(TimeoutError) as exc_info:
            config_editor._launch_macos_editor("/tmp/config.json")

        msg = str(exc_info.value)
        assert "30-minute timeout" in msg, f"macOS TimeoutError must mention '30-minute timeout'. Got: {msg!r}"

    def test_linux_launch_passes_timeout_to_subprocess_run(self, monkeypatch):
        """Linux ``xdg-open`` is called with ``timeout=1800``."""
        from voice_typer.server import config_editor

        captured: dict = {}

        def _fake_run(args, **kwargs):
            captured["args"] = args
            captured["timeout"] = kwargs.get("timeout")
            captured["check"] = kwargs.get("check")
            return MagicMock(returncode=0)

        monkeypatch.setattr(config_editor.subprocess, "run", _fake_run)

        config_editor._launch_linux_editor("/tmp/config.json")

        assert captured["args"][0] == "xdg-open", f"Linux path must invoke 'xdg-open'; got {captured['args'][0]!r}"
        assert captured["timeout"] == 1800, (
            f"Linux path must pass timeout=1800 (30 minutes) to subprocess.run. Got timeout={captured['timeout']!r}."
        )
        assert captured["check"] is False, "B-4: Linux path must keep check=False."

    def test_linux_launch_raises_timeout_error_on_timeout(self, monkeypatch):
        """Linux path converts TimeoutExpired -> TimeoutError."""
        from voice_typer.server import config_editor

        def _fake_run(args, **kwargs):
            assert kwargs.get("timeout") == 1800, (
                f"Linux path must call subprocess.run with timeout=1800. Got: {kwargs.get('timeout')!r}"
            )
            raise subprocess.TimeoutExpired(cmd=args, timeout=1800)

        monkeypatch.setattr(config_editor.subprocess, "run", _fake_run)

        with pytest.raises(TimeoutError) as exc_info:
            config_editor._launch_linux_editor("/tmp/config.json")

        msg = str(exc_info.value)
        assert "30-minute timeout" in msg, f"Linux TimeoutError must mention '30-minute timeout'. Got: {msg!r}"

    def test_launcher_swallows_non_timeout_exceptions(self, monkeypatch):
        """DR-19 contract preserved: non-timeout launch errors are still"""
        from voice_typer.server import config_editor

        def _raising_launcher(config_path):
            raise FileNotFoundError("editor binary not found")

        # Build a fake app capturing whether tray.notify was called.
        notify_calls: list = []

        class _FakeApp:
            def __init__(self):
                import threading

                self._config_mutation_lock = threading.Lock()
                self.config = MagicMock()
                self.config.save.return_value = True
                type(self.config).load = MagicMock(return_value=self.config)
                self.tray = MagicMock()
                self.tray.notify = lambda title, body: notify_calls.append((title, body))

        monkeypatch.setattr(
            config_editor,
            "_PLATFORM_LAUNCHERS",
            {"macos": _raising_launcher},
        )
        monkeypatch.setattr(config_editor, "_current_platform", lambda: "macos")

        launcher = config_editor.ConfigEditorLauncher(_FakeApp())
        # Must NOT raise, contract: non-timeout errors swallowed.
        launcher.launch("/tmp/config.json")

        # Tray must NOT be notified for a non-timeout launch error
        assert notify_calls == [], (
            f"DR-19: non-timeout launch errors must NOT trigger a tray notification. Got: {notify_calls}"
        )

    def test_launcher_propagates_timeout_to_tray_notification(self, monkeypatch):
        """when a launcher raises TimeoutError, the outer"""
        from voice_typer.server import config_editor

        def _timing_out_launcher(config_path):
            raise TimeoutError(f"Editor session for {config_path} exceeded the 30-minute timeout")

        notify_calls: list = []

        class _FakeApp:
            def __init__(self):
                import threading

                self._config_mutation_lock = threading.Lock()
                self.config = MagicMock()
                self.config.save.return_value = True
                type(self.config).load = MagicMock(return_value=self.config)
                self.tray = MagicMock()
                self.tray.notify = lambda title, body: notify_calls.append((title, body))

        monkeypatch.setattr(
            config_editor,
            "_PLATFORM_LAUNCHERS",
            {"linux": _timing_out_launcher},
        )
        monkeypatch.setattr(config_editor, "_current_platform", lambda: "linux")

        launcher = config_editor.ConfigEditorLauncher(_FakeApp())
        launcher.launch("/tmp/config.json")

        assert len(notify_calls) == 1, (
            f"a TimeoutError from the launcher must produce exactly one tray notification. Got: {notify_calls}"
        )
        title, body = notify_calls[0]
        assert "timed out" in body.lower(), f"tray notification body must mention 'timed out'. Got: {body!r}"
        assert "30" in body, f"tray notification body must mention the 30-minute timeout duration. Got: {body!r}"
        assert "temporary file" in body.lower(), (
            f"tray notification body must include the recovery hint about saving to a temporary file. Got: {body!r}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
