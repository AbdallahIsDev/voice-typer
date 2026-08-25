"""SI-2 (Critical): ``Config.set_mutation_lock`` production wiring.

Finding SI-2: ``Config.set_mutation_lock()`` is defined at
``config.py:1083`` but was NEVER called in production.
``VoiceTyperApp.__init__`` created ``_config_mutation_lock =
threading.RLock()`` at ``app.py:416`` but never passed it to ``Config``.
All ~15 production ``app.config.save()`` call sites therefore ran
WITHOUT in-process serialization — only the IPC ``set_config`` path
(which manually acquired the lock around its own read-modify-save
sequence) was protected. A background mic-fallback ``save()`` could
interleave with an in-flight ``apply_config`` and persist a torn
snapshot.

These tests pin the wiring (BEHAVIORALLY — no ``inspect.getsource``):

1. ``VoiceTyperApp.__init__`` calls
   ``self.config.set_mutation_lock(self._config_mutation_lock)`` AFTER
   both ``self.config`` (``Config.load()`` at app.py:137) and
   ``self._config_mutation_lock`` (``threading.RLock()`` at app.py:416)
   are set. Verified by asserting the Config instance's
   ``_mutation_lock`` IS the app's ``_config_mutation_lock`` — same
   object identity (not a copy, not a fresh RLock).

2. The wiring survives a fresh ``Config()`` default-constructed in the
   ``Config.load()`` exception fallback branch (app.py:140) — i.e. when
   ``Config.load()`` raises, the default ``Config()`` still gets the
   lock wired in.

3. ``config_editor.ConfigEditorLauncher.launch`` re-wires
   ``set_mutation_lock`` on the freshly reloaded ``Config`` instance
   after ``type(self.app.config).load()`` (config_editor.py:341).
   ``Config.load()`` returns a brand-new object whose
   ``_mutation_lock`` instance attribute is unset (falls back to the
   ``ClassVar`` default of ``None`` — config.py:1081), so without the
   re-wiring every subsequent ``config.save()`` would run unlocked
   until the next app restart.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock


def _make_app(tmp_config_dir, monkeypatch):
    """Build a VoiceTyperApp with mocked hardware/GUI deps.

    Mirrors the ``app`` fixture in ``tests/app/conftest.py`` but inlined
    so this test file is self-contained (no fixture cross-deps).
    """
    monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    return instance


class TestSetMutationLockWiredInInit:
    """SI-2 acceptance criterion #1: ``__init__`` wires the lock."""

    def test_config_mutation_lock_is_app_lock(self, tmp_config_dir, monkeypatch):
        """``app.config._mutation_lock`` IS ``app._config_mutation_lock``.

        Same object identity — not a copy, not a freshly-constructed
        RLock. Without this wiring, ``Config._save_with_mutation_lock``
        (config.py:1188) reads ``self._mutation_lock`` (which falls back
        to the ``ClassVar`` default of ``None``) and skips locking —
        meaning every ``app.config.save()`` call site (~15 production
        callers) runs unlocked.
        """
        app = _make_app(tmp_config_dir, monkeypatch)

        assert hasattr(app, "_config_mutation_lock"), (
            "VoiceTyperApp.__init__ must create self._config_mutation_lock (threading.RLock) — see app.py:416."
        )
        assert isinstance(app._config_mutation_lock, type(threading.RLock())), (
            f"app._config_mutation_lock must be a threading.RLock instance — got {type(app._config_mutation_lock)!r}."
        )
        # core assertion: the Config instance holds the SAME lock
        # reference, not None and not a different RLock.
        assert app.config._mutation_lock is app._config_mutation_lock, (
            "SI-2 regression: app.config._mutation_lock is NOT "
            "app._config_mutation_lock. VoiceTyperApp.__init__ must call "
            "self.config.set_mutation_lock(self._config_mutation_lock) "
            "AFTER both self.config and self._config_mutation_lock are "
            "set. Without this wiring, Config.save() skips the in-process "
            "mutation lock (config.py:1188 reads _mutation_lock, falls "
            "back to None, and _save_with_mutation_lock short-circuits "
            "to _save_unlocked) — every production config.save() call "
            "site runs unlocked, allowing a background mic-fallback save "
            "to interleave with an in-flight apply_config and persist a "
            "torn snapshot."
        )

    def test_lock_is_an_rlock_not_plain_lock(self, tmp_config_dir, monkeypatch):
        """The wired lock must be an ``RLock`` (reentrant).

        ``Config._save_with_mutation_lock`` (config.py:1191) uses
        ``with lock:`` — a plain ``Lock`` would deadlock if the same
        thread already holds it (e.g. ``service.apply_config`` calls
        ``config.save()`` while inside ``with app._config_mutation_lock``).
        ``RLock`` allows the same thread to re-acquire, which is the
        contract the IPC ``set_config`` path depends on.
        """
        app = _make_app(tmp_config_dir, monkeypatch)
        lock = app.config._mutation_lock
        # RLock contract: a second acquire on the same thread succeeds
        # without blocking and must be released the same number of times.
        acquired = lock.acquire(blocking=False)
        try:
            assert acquired, (
                "First acquire of the wired mutation lock should succeed immediately — no other thread holds it."
            )
            nested = lock.acquire(blocking=False)
            try:
                assert nested, (
                    "SI-2: the wired mutation lock must be an RLock "
                    "(reentrant) so a thread already holding it (e.g. "
                    "service.apply_config) can call config.save() and "
                    "re-enter the same lock without deadlocking. A plain "
                    "Lock would deadlock here, breaking every "
                    "set_config → save path."
                )
            finally:
                lock.release()
        finally:
            lock.release()

    def test_wiring_survives_config_load_failure(self, tmp_config_dir, monkeypatch):
        """When ``Config.load()`` raises, the fallback ``Config()`` is
        also wired.

        VoiceTyperApp.__init__ (app.py:138-141) catches the exception,
        falls back to ``Config()`` defaults, and sets
        ``_config_load_failed = True``. The ``set_mutation_lock`` call
        must run on the fallback Config too — otherwise a corrupted
        ``config.json`` at startup would leave the entire session
        unlocked.
        """
        from voice_typer.server.config import Config

        def _raise(*_a, **_kw):
            raise RuntimeError("simulated Config.load() failure")

        monkeypatch.setattr(Config, "load", _raise)

        app = _make_app(tmp_config_dir, monkeypatch)

        # Sanity: the fallback path was taken.
        assert getattr(app, "_config_load_failed", False) is True, (
            "Test setup: Config.load() should have raised and the app should have fallen back to Config() defaults."
        )
        # even the fallback Config instance must be wired.
        assert app.config._mutation_lock is app._config_mutation_lock, (
            "SI-2 regression: when Config.load() raises and __init__ "
            "falls back to Config() defaults (app.py:140), the "
            "set_mutation_lock wiring must still run — otherwise a "
            "corrupted config.json at startup leaves the entire session "
            "unlocked. Every subsequent config.save() runs without the "
            "in-process mutation lock for the whole app lifetime."
        )


class TestSetMutationLockRewiredAfterReload:
    """SI-2 acceptance criterion #2: config_editor reload path re-wires.

    ``config_editor.ConfigEditorLauncher.launch`` (config_editor.py:341)
    replaces ``self.app.config`` with a freshly-loaded ``Config``
    instance. ``Config.load()`` returns a brand-new object whose
    ``_mutation_lock`` instance attribute is unset (falls back to the
    ``ClassVar`` default of ``None`` — config.py:1081). Without
    re-wiring, every subsequent ``config.save()`` runs unlocked until
    the next app restart.
    """

    def test_reload_rewires_mutation_lock(self, tmp_config_dir, monkeypatch):
        """After ``ConfigEditorLauncher.launch`` reloads config from disk,
        ``app.config._mutation_lock`` is STILL ``app._config_mutation_lock``.

        We don't run a real editor subprocess — instead we drive the
        launcher with a no-op platform launcher and assert the post-
        reload Config picks up the same lock reference. This is the
        behavior the SI-2 fix adds (the else-branch after the reload
        try/except).
        """
        app = _make_app(tmp_config_dir, monkeypatch)

        # Force the Linux branch so the launcher uses subprocess.run
        # which we can trivially monkeypatch into a no-op.
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: False)
        monkeypatch.setattr("voice_typer.server.platform_utils.is_linux", lambda: True)

        # No-op the actual editor subprocess so the launcher returns
        # immediately. The user "made no edits" so the on-disk config
        # is unchanged.
        import voice_typer.server.config_editor as ce_mod

        def _fake_run(*_a, **_kw):
            return None

        monkeypatch.setattr(ce_mod.subprocess, "run", _fake_run)

        # Pre-condition: lock is wired before the editor session.
        assert app.config._mutation_lock is app._config_mutation_lock

        # Drive the launcher. This calls Config.load() internally and
        # reassigns self.app.config.
        config_path = app.config.config_dir / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"hotkey": "<f6>"}))

        launcher = ce_mod.ConfigEditorLauncher(app)
        launcher.launch(config_path)

        # core assertion: the freshly-reloaded Config has the SAME
        # lock reference, not None and not a different RLock.
        assert app.config._mutation_lock is app._config_mutation_lock, (
            "SI-2 regression: after ConfigEditorLauncher.launch reloaded "
            "config from disk (config_editor.py:341 "
            "`self.app.config = type(self.app.config).load()`), the new "
            "Config instance's _mutation_lock is NOT app._config_mutation_lock. "
            "The reload path must call set_mutation_lock on the new Config "
            "so subsequent config.save() calls stay serialized — without "
            "it, every save() until the next app restart runs unlocked "
            "(Config._save_with_mutation_lock reads _mutation_lock, gets "
            "the ClassVar default of None, and short-circuits to "
            "_save_unlocked)."
        )

    def test_reload_does_not_share_lock_across_apps(self, tmp_config_dir, monkeypatch):
        """Two VoiceTyperApp instances must NOT share a mutation lock.

        ``Config.set_mutation_lock`` (config.py:1104-1107) deliberately
        stores the lock as an INSTANCE attribute (shadowing the
        ClassVar) so each Config instance can have its own lock. This
        test pins that the wiring doesn't accidentally stash the lock
        on the Config CLASS (which would leak across VoiceTyperApp
        instances in the same process — rare in production but possible
        in tests).
        """
        app1 = _make_app(tmp_config_dir, monkeypatch)
        # Wipe config.json so app2's Config.load() starts clean
        # (otherwise it inherits app1's writes).
        config_path = tmp_config_dir / "config.json"
        if config_path.exists():
            config_path.write_text(json.dumps({}))

        app2 = _make_app(tmp_config_dir, monkeypatch)

        # Each app's Config holds its OWN app's lock — not the other's.
        assert app1.config._mutation_lock is app1._config_mutation_lock
        assert app2.config._mutation_lock is app2._config_mutation_lock
        assert app1.config._mutation_lock is not app2.config._mutation_lock, (
            "SI-2: two VoiceTyperApp instances must not share a mutation "
            "lock — set_mutation_lock stores the reference per-Config-instance "
            "(config.py:1107 uses self.__dict__ so the ClassVar is shadowed "
            "per-instance). A shared class-level lock would serialize "
            "unrelated apps in the same process."
        )


class TestWiringOrderMatters:
    """SI-2 acceptance criterion #1 (ordering): the wiring call MUST run
    AFTER both ``self.config`` and ``self._config_mutation_lock`` exist.

    If the call runs before ``self.config`` is set, ``AttributeError``.
    If it runs before ``self._config_mutation_lock`` is created, it
    passes ``None`` to ``set_mutation_lock`` (which clears the lock —
    the opposite of what we want).
    """

    def test_wiring_runs_after_lock_creation(self, tmp_config_dir, monkeypatch):
        """Spy on ``Config.set_mutation_lock`` and assert it's called
        with the SAME RLock that ``VoiceTyperApp.__init__`` creates
        (not None, not a different object)."""
        from voice_typer.server.config import Config

        recorded: list = []
        original = Config.set_mutation_lock

        def _spy(self, lock):
            recorded.append(lock)
            return original(self, lock)

        monkeypatch.setattr(Config, "set_mutation_lock", _spy)

        app = _make_app(tmp_config_dir, monkeypatch)

        # set_mutation_lock was called at least once during __init__.
        assert len(recorded) >= 1, (
            "SI-2: Config.set_mutation_lock was never called during "
            "VoiceTyperApp.__init__. The wiring call "
            "`self.config.set_mutation_lock(self._config_mutation_lock)` "
            "is missing from app.py."
        )
        # The lock passed was NOT None (would clear the lock — opposite
        # of the fix intent).
        assert recorded[0] is not None, (
            "SI-2: Config.set_mutation_lock was called with None during "
            "VoiceTyperApp.__init__ — this CLEARS the lock (the opposite "
            "of the fix). The wiring must run AFTER "
            "self._config_mutation_lock = threading.RLock() so a real "
            "RLock reference is passed, not None."
        )
        # The lock passed IS the app's _config_mutation_lock (same object).
        assert recorded[0] is app._config_mutation_lock, (
            "SI-2: Config.set_mutation_lock was called with a different "
            "lock object than app._config_mutation_lock. The wiring must "
            "pass `self._config_mutation_lock` (the RLock created at "
            "app.py:416) so Config.save() acquires the SAME lock the IPC "
            "set_config path already uses."
        )
