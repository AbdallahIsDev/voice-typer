"""B-4: Config editor mutation lock regression tests.

The Windows notepad path in ``VoiceTyperApp._open_config_file`` has
always acquired ``_config_mutation_lock`` for the full editor session
so a concurrent IPC ``set_config`` call can't atomically overwrite
``config.json`` while Notepad is mid-edit (SEC-audit-011).

B-4 fixes the same TOCTOU race on the macOS (``open``) and Linux
(``xdg-open``) paths: they previously used non-blocking
``subprocess.Popen`` and did NOT acquire the lock, so a concurrent IPC
``set_config`` call (which goes through ``service.apply_config`` →
``with app._config_mutation_lock``) could silently overwrite the
user's manual edits while the editor was still open.

These tests pin the fix:

1. Source-level invariants (cross-platform): every platform branch
   must acquire ``_config_mutation_lock`` BEFORE spawning the editor
   and reload the config from disk AFTER the editor closes.

2. Runtime behavior (macOS, Linux, Windows): when the editor is open,
   a concurrent ``set_config`` call (mimicked by trying to acquire the
   same lock from another thread) blocks until the editor closes, then
   proceeds.
"""

from __future__ import annotations

import inspect
import threading
import time
from unittest.mock import MagicMock

import pytest

# ── Source-level invariants ────────────────────────────────────────────


class TestB4SourceInvariants:
    """Pin the source-level structure of _open_config_file.

    These tests survive even on platforms where the runtime tests
    below can't exercise the platform-specific branch (e.g. we can't
    really run ``open`` on Linux). They verify the fix is structurally
    present for every platform.
    """

    def _src(self) -> str:
        from voice_typer.server.app import VoiceTyperApp

        return inspect.getsource(VoiceTyperApp._open_config_file)

    def test_macos_branch_acquires_lock(self):
        src = self._src()
        # Find the macOS branch
        macos_idx = src.find("elif is_macos():")
        assert macos_idx != -1, "macOS branch must exist in _open_config_file"
        # Find the next branch (Linux `else:`) AFTER the macOS branch
        linux_idx = src.find("\n            else:", macos_idx)
        assert linux_idx != -1, "Linux else branch must exist after macOS branch"
        macos_block = src[macos_idx:linux_idx]
        assert "with self._config_mutation_lock:" in macos_block, (
            "B-4: macOS branch of _open_config_file must acquire "
            "_config_mutation_lock so a concurrent IPC set_config call "
            "can't overwrite config.json while the user is editing it."
        )
        assert "type(self.config).load()" in macos_block, (
            "B-4: macOS branch must reload the config from disk after "
            "the editor closes so any saved edits are picked up."
        )

    def test_linux_branch_acquires_lock(self):
        src = self._src()
        # The Linux branch is the trailing `else:` (after Windows + macOS).
        # Find the LAST `with self._config_mutation_lock:` in the source —
        # it must be inside the Linux block (the third one).
        lock_indices = []
        start = 0
        while True:
            idx = src.find("with self._config_mutation_lock:", start)
            if idx == -1:
                break
            lock_indices.append(idx)
            start = idx + 1
        assert len(lock_indices) >= 3, (
            "B-4: _open_config_file must acquire _config_mutation_lock in "
            "ALL THREE platform branches (Windows, macOS, Linux). Found "
            f"{len(lock_indices)} occurrences; expected >= 3."
        )

    def test_macos_branch_uses_open_w(self):
        """On macOS, ``open -W`` blocks until the editor exits.

        Vanilla ``open`` returns immediately after launching the editor,
        so calling ``proc.wait()`` on it would NOT block for the editor
        session. The ``-W`` flag is required to make the spawn block.
        """
        src = self._src()
        assert '"-W"' in src or "'-W'" in src, (
            "B-4: macOS branch must use 'open -W' so the spawn blocks "
            "until the editor exits (vanilla 'open' returns immediately)."
        )

    def test_macos_and_linux_branches_do_not_use_bare_popen(self):
        """The macOS/Linux branches must not use non-blocking Popen.

        B-4 explicitly replaces the non-blocking ``subprocess.Popen``
        pattern with ``subprocess.run`` (blocking). The Windows branch
        still uses ``Popen().wait()`` which is also blocking, so it's
        allowed.
        """
        src = self._src()
        macos_idx = src.find("elif is_macos():")
        linux_idx = src.find("\n            else:", macos_idx)
        # Linux block runs to the end of the try (the `except Exception`)
        except_idx = src.find("except Exception as e:", linux_idx)
        macos_block = src[macos_idx:linux_idx]
        linux_block = src[linux_idx:except_idx]
        assert "subprocess.Popen" not in macos_block, (
            "B-4: macOS branch must NOT use non-blocking subprocess.Popen; "
            "use subprocess.run (blocking) inside the lock instead."
        )
        assert "subprocess.Popen" not in linux_block, (
            "B-4: Linux branch must NOT use non-blocking subprocess.Popen; "
            "use subprocess.run (blocking) inside the lock instead."
        )

    def test_all_branches_reload_config_after_editor(self):
        """Every platform branch must reload config after the editor closes.

        This is what picks up the user's saved edits and (importantly)
        happens INSIDE the lock so the reload is consistent with the
        lock-release point.
        """
        src = self._src()
        reload_count = src.count("type(self.config).load()")
        assert reload_count >= 3, (
            "B-4: all three platform branches (Windows, macOS, Linux) "
            "must reload the config from disk after the editor closes. "
            f"Found {reload_count} occurrences; expected >= 3."
        )


# ── APP-3: config.save() must happen inside _config_mutation_lock ─────


class TestApp3SaveInsideLock:
    """APP-3: ``_open_config_file`` previously called
    ``self.config.save()`` OUTSIDE ``_config_mutation_lock`` (before the
    platform-specific ``with`` block). This opened a TOCTOU race:

    1. Our save() writes the in-memory config to disk.
    2. A concurrent IPC ``set_config`` call (which acquires
       ``_config_mutation_lock`` via ``service.apply_config``) writes
       its OWN version to disk via ``_secure_atomic_write``.
    3. The editor opens the file written by step 2, NOT by step 1 —
       so the user edits a config that doesn't include our pending
       in-memory changes.

    The fix moves ``self.config.save()`` INSIDE the
    ``with self._config_mutation_lock:`` block in each platform branch,
    so the save and the editor launch are atomic with respect to
    concurrent set_config calls.

    These tests pin the source-level invariant: there must be NO
    ``self.config.save()`` call OUTSIDE the lock, and EXACTLY ONE
    ``self.config.save()`` call INSIDE each platform branch's lock.
    """

    def _src(self) -> str:
        from voice_typer.server.app import VoiceTyperApp

        return inspect.getsource(VoiceTyperApp._open_config_file)

    def _strip_docstring(self, src: str) -> str:
        """Return ``src`` with the leading triple-quoted docstring AND
        all comment lines removed.

        Tests that count ``self.config.save()`` or
        ``type(self.config).load()`` occurrences must not match mentions
        in the docstring or in inline comments (which would inflate the
        count and let a real regression slip through).
        """
        doc_start = src.find('"""')
        if doc_start == -1:
            return self._strip_comments(src)
        doc_end = src.find('"""', doc_start + 3)
        assert doc_end != -1, "_open_config_file must close its docstring"
        body = src[doc_end + 3 :]
        return self._strip_comments(body)

    def _strip_comments(self, src: str) -> str:
        """Strip Python ``#`` comments from each line of ``src``.

        We deliberately do NOT use ``tokenize`` here because the source
        we receive from ``inspect.getsource`` is a string fragment, not
        a complete module. A line-by-line approach is sufficient and
        robust for the assertions we need to make.
        """
        out_lines = []
        for line in src.splitlines():
            # Naive: strip everything after the first '#' that isn't
            # inside a string literal. For our test source (which has
            # no string literals containing '#' on the same line as a
            # comment), this is correct.
            hash_idx = line.find("#")
            if hash_idx != -1:
                line = line[:hash_idx]
            out_lines.append(line)
        return "\n".join(out_lines)

    def test_no_save_call_outside_lock(self):
        """There must be NO ``self.config.save()`` call that runs
        unconditionally before the platform ``try`` block. The pre-fix
        code had:

            config_file = self.config.config_dir / "config.json"
            if not self.config.save():  # ← ran outside the lock
                log.warning(...)
            import subprocess
            try:
                if is_windows():
                    with self._config_mutation_lock:
                        ...

        After the fix, the save call is inside each branch's ``with``
        block. This test verifies the "outside the lock" call is gone.
        """
        src = self._src()
        body = self._strip_docstring(src)

        first_lock_idx = body.find("with self._config_mutation_lock:")
        assert first_lock_idx != -1, (
            "APP-3: _open_config_file must acquire _config_mutation_lock in at least one platform branch"
        )
        # Slice the body BEFORE the first lock-acquire — this is the
        # "outside the lock" region. There must be no save() call there.
        before_lock = body[:first_lock_idx]
        assert "self.config.save()" not in before_lock, (
            "APP-3: _open_config_file must NOT call self.config.save() "
            "outside _config_mutation_lock. The save must happen INSIDE "
            "each platform branch's ``with`` block so a concurrent IPC "
            "set_config call can't overwrite the file between our save "
            "and the editor launch (TOCTOU race)."
        )

    def test_save_call_inside_each_branch_lock(self):
        """Each platform branch's ``with self._config_mutation_lock:``
        block must contain a ``self.config.save()`` call.

        We split the source into the three branch blocks (Windows /
        macOS / Linux) and verify each block has both
        ``with self._config_mutation_lock:`` AND ``self.config.save()``.
        """
        src = self._src()
        # Windows branch starts at "if is_windows():"
        win_idx = src.find("if is_windows():")
        assert win_idx != -1, "Windows branch must exist in _open_config_file"
        macos_idx = src.find("elif is_macos():", win_idx)
        assert macos_idx != -1, "macOS branch must exist after Windows branch"
        # The Linux branch is the trailing ``else:`` after macOS.
        linux_idx = src.find("\n            else:", macos_idx)
        assert linux_idx != -1, "Linux else branch must exist after macOS branch"
        # The Linux block runs to the end of the try (the ``except Exception as e:``).
        except_idx = src.find("except Exception as e:", linux_idx)
        assert except_idx != -1, "outer try must have an except clause"

        win_block = src[win_idx:macos_idx]
        macos_block = src[macos_idx:linux_idx]
        linux_block = src[linux_idx:except_idx]

        for branch_name, block in [
            ("Windows", win_block),
            ("macOS", macos_block),
            ("Linux", linux_block),
        ]:
            assert "with self._config_mutation_lock:" in block, (
                f"APP-3: {branch_name} branch must acquire _config_mutation_lock (B-4 invariant, preserved by APP-3)"
            )
            assert "self.config.save()" in block, (
                f"APP-3: {branch_name} branch must call self.config.save() "
                f"INSIDE the _config_mutation_lock block so the save and "
                f"the editor launch are atomic with respect to concurrent "
                f"set_config calls (TOCTOU race)."
            )

    def test_save_call_count_matches_branch_count(self):
        """There must be exactly 3 ``self.config.save()`` calls in
        _open_config_file — one per platform branch. (Not 1 outside the
        lock + 0 inside, which was the pre-fix bug; not 4+ which would
        indicate a copy-paste duplication.)
        """
        src = self._src()
        body = self._strip_docstring(src)
        save_count = body.count("self.config.save()")
        assert save_count == 3, (
            "APP-3: _open_config_file must contain exactly 3 "
            "self.config.save() calls (one per platform branch, each "
            f"inside its _config_mutation_lock block). Got {save_count}; "
            "expected 3."
        )


# ── Runtime behavior ───────────────────────────────────────────────────


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
    instance.models._sync_registry_from_fields()
    return instance


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
        # Record whether the caller holds the lock at the moment we're
        # called — this is the core B-4 invariant.
        # We can't access the lock here directly; the test wires this up
        # via a closure that captures the app.
        self.opened.set()
        # Block until the test signals the editor to close. Use a short
        # timeout so a buggy test doesn't hang forever.
        self.close_event.wait(timeout=10.0)
        return MagicMock(returncode=0)

    def popen_wait(self, args, **kwargs):
        """Mimics Popen(args).wait() — same blocking semantics as run()."""
        self.call_count += 1
        self.call_args = args
        self.opened.set()
        self.close_event.wait(timeout=10.0)
        return MagicMock(returncode=0)


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

    # Give the setter thread a chance to (try to) acquire the lock.
    # If B-4 is in place, the editor holds the lock so the setter
    # should NOT be able to acquire it yet.
    time.sleep(0.15)
    assert not acquired.is_set(), (
        "B-4: a concurrent set_config call (acquiring _config_mutation_lock) "
        "must BLOCK while the config editor is open — but the lock was "
        "acquired immediately, which means _open_config_file is not holding "
        "the lock for the full editor session."
    )

    # Signal the editor to close — the setter thread should now be able
    # to acquire the lock.
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
        monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.is_macos", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.is_linux", lambda: False)

        editor = _FakeEditor()

        def _run(args, **kwargs):
            # Verify the lock is held when subprocess.run is called.
            assert app._config_mutation_lock._is_owned() if hasattr(app._config_mutation_lock, "_is_owned") else True, (
                "B-4: _config_mutation_lock must be acquired by the "
                "current thread BEFORE subprocess.run is called on macOS."
            )
            return editor.run(args, **kwargs)

        import subprocess as _subprocess

        monkeypatch.setattr(_subprocess, "run", _run)

        thread, errors = _run_open_config_in_thread(app)

        # Wait for the editor to actually be opened.
        assert editor.opened.wait(timeout=5.0), "Editor should have been launched (subprocess.run called) within 5s."

        # A concurrent set_config call must block.
        _assert_concurrent_set_config_blocks(app, editor)

        # The _open_config_file thread should now finish too (editor closed
        # → subprocess.run returns → reload → lock released → method exits).
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "_open_config_file should have returned after the editor closed."
        assert errors == [], f"_open_config_file raised: {errors}"

        # Verify 'open -W' was used (not vanilla 'open').
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
        monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.is_macos", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.is_linux", lambda: True)

        editor = _FakeEditor()

        def _run(args, **kwargs):
            # Lock must be held when subprocess.run is called.
            assert app._config_mutation_lock._is_owned() if hasattr(app._config_mutation_lock, "_is_owned") else True, (
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

        # Verify 'xdg-open' was used.
        assert editor.call_args is not None
        assert editor.call_args[0] == "xdg-open", f"Expected 'xdg-open' command, got {editor.call_args[0]!r}"


class TestB4WindowsRuntime:
    """Runtime test for the Windows notepad branch (parity check).

    The Windows branch already held the lock pre-B-4. This test pins
    that behavior so a future refactor doesn't regress it.
    """

    def test_lock_held_during_editor_session(self, tmp_config_dir, monkeypatch):
        app = _make_app(tmp_config_dir, monkeypatch)
        monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.is_macos", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.is_linux", lambda: False)

        # Force the validated-Notepad fallback path so the test exercises
        # Popen+wait under the lock instead of really launching an editor
        # via ShellExecuteEx on the host (which would block indefinitely).
        monkeypatch.setattr("voice_typer.server.app._windows_open_with_default_app", lambda path: None)

        # Make the notepad path appear to exist so we enter the
        # Popen+wait code path (instead of the os.startfile fallback).
        monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

        editor = _FakeEditor()

        class _FakeProc:
            def __init__(self, args):
                self._args = args

            def wait(self):
                return editor.popen_wait(self._args)

        import subprocess as _subprocess

        def _popen(args, **kwargs):
            # Lock must be held when Popen is called.
            assert app._config_mutation_lock._is_owned() if hasattr(app._config_mutation_lock, "_is_owned") else True, (
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
        monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.is_macos", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.is_linux", lambda: False)

        # Track whether Config.load() is called after the editor closes.
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

        # When the editor closes, write a marker to disk so the reload
        # picks up a different value than the in-memory config had.
        def _run(args, **kwargs):
            result = editor.run(args, **kwargs)
            # Simulate the user saving a new value to disk while the
            # editor was open. We do this BEFORE the reload (which
            # happens after subprocess.run returns).
            import json

            config_path = app.config.config_dir / "config.json"
            config_path.write_text(json.dumps({"show_notifications": False}), encoding="utf-8")
            return result

        monkeypatch.setattr(_subprocess, "run", _run)

        # Pre-condition: in-memory config has show_notifications=True
        # (the default). The user will "save" False to disk while editing.
        assert app.config.show_notifications is True

        app._open_config_file()

        # Config.load() must have been called after the editor closed.
        assert len(load_calls) >= 1, (
            "B-4: Config.load() must be called after the editor closes so the user's saved edits are picked up."
        )
        # The in-memory config should now reflect the disk state.
        assert app.config.show_notifications is False, (
            "B-4: after the editor closes, the in-memory config must reflect the user's saved edits on disk."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
