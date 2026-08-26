"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package.
The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestManifestInExists:
    """PLAT-036.

    The finding: no MANIFEST.in. Investigation: MANIFEST.in already
    exists at the repo root. This test pins that state so it's never
    accidentally deleted.
    """

    def test_manifest_in_exists(self):
        manifest = Path(__file__).resolve().parent.parent.parent / "MANIFEST.in"
        assert manifest.exists(), "PLAT-036: MANIFEST.in must exist at the repo root."

    def test_manifest_in_includes_key_files(self):
        # KEEP — pins  (MANIFEST.in includes critical data
        # files). A behavioral test would need to run `python setup.py
        # sdist` and inspect the archive, which is heavy; the file-content
        # check catches removal of the include directives directly.

        manifest = Path(__file__).resolve().parent.parent.parent / "MANIFEST.in"
        content = manifest.read_text()
        # Must include the critical data files
        assert "corrections.json" in content, "PLAT-036: MANIFEST.in must include corrections.json"
        assert "LICENSE" in content
        assert "README.md" in content


class TestWindowsManifestAsInvoker:
    """The finding: no requestedExecutionLevel manifest. Investigation:
    the manifest IS embedded via the .spec file, and a standalone
    voice-typer.manifest file exists with asInvoker. This test pins
    that state.
    """

    def test_manifest_source_is_embedded_in_spec(self):
        # the standalone scripts/build/voice-typer.manifest file
        # was REMOVED — its XML drifted from the .spec's copy. The .spec
        # now inlines the manifest XML as the single source of truth and
        # writes it to a temp file for PyInstaller. Verify the inlined
        # source is present in the .spec (the build-time manifest source).
        spec = Path(__file__).resolve().parent.parent.parent / "scripts" / "build" / "voice-typer.spec"
        content = spec.read_text()
        assert 'requestedExecutionLevel level="asInvoker"' in content, (
            "PLAT-037: the .spec's inlined manifest must declare requestedExecutionLevel asInvoker."
        )

    def test_manifest_declares_as_invoker(self):
        # KEEP — pins the asInvoker declaration.
        # A behavioral test would need to inspect the embedded manifest
        # in a built .exe (heavy Windows-only); the content check
        # catches removal of the asInvoker declaration directly.

        spec = Path(__file__).resolve().parent.parent.parent / "scripts" / "build" / "voice-typer.spec"
        content = spec.read_text()
        assert 'requestedExecutionLevel level="asInvoker"' in content, (
            "PLAT-037: manifest must declare requestedExecutionLevel asInvoker."
        )

    def test_spec_file_embeds_manifest(self):
        # KEEP — pins  (.spec file references the manifest).
        # A behavioral test would need to run PyInstaller and inspect the
        # built .exe resources (heavy); the file-content check catches
        # removal of the manifest reference in the .spec directly.

        spec = Path(__file__).resolve().parent.parent.parent / "scripts" / "build" / "voice-typer.spec"
        content = spec.read_text()
        assert "manifest" in content.lower(), "PLAT-037: .spec file must reference the manifest."


class TestPlatRunAutostartTaskHashed:
    """The finding: autostart task name was a fixed string
    "VoiceTyperAutostart" — two installs would conflict. Fix: append
    the install-path hash suffix (now in the canonical
    ``com.voicetyper.*`` reverse-DNS namespace).
    """

    def test_autostart_task_name_includes_hash_suffix(self):
        # KEEP — pins PLAT-RUN (autostart task name includes
        # install-path hash suffix). The sibling test_install_hash_suffix_returns_underscore_prefix
        # and test_two_different_executables_get_different_hashes test the
        # hash function behavior, but don't catch a regression where the
        # task name stops using the hash. Source-string check catches that.
        from voice_typer.server.server_platform import autostart as platform

        src = inspect.getsource(platform)
        assert "_install_hash_suffix" in src, "_install_hash_suffix helper must exist."
        # The task name must be an f-string that includes the hash
        assert (
            'f"com.voicetyper.autostart{_install_hash_suffix()}"' in src
            or "f'com.voicetyper.autostart{_install_hash_suffix()}'" in src
        ), "_APP_AUTOSTART_TASK_NAME must include the hash suffix."

    def test_install_hash_suffix_returns_underscore_prefix(self):
        """The hash suffix must start with '_' so the task name reads
        'com.voicetyper.autostart_a1b2c3d4'.
        """
        from voice_typer.server.server_platform import _install_hash_suffix

        suffix = _install_hash_suffix()
        # Must start with '_' (or be empty on failure)
        assert suffix == "" or suffix.startswith("_"), f"hash suffix must start with '_', got {suffix!r}"
        # Must be 9 chars: '_' + 8 hex chars (or empty)
        assert suffix == "" or len(suffix) == 9, f"hash suffix must be '_XXXXXXXX' (9 chars), got {suffix!r}"

    def test_two_different_executables_get_different_hashes(self):
        """Two different install paths must produce different hash suffixes."""
        from voice_typer.server.server_platform import _install_hash_suffix

        # The hash is derived from the STABLE install identifier (the
        # autostart launcher path), NOT sys.executable — sys.executable
        # differs between python.exe / pythonw.exe / the venv for the
        # SAME install, which caused the perpetual "autostart enabled but
        # disabled" re-registration loop. Different install DIRECTORIES
        # must still produce different hashes (PLAT-RUN multi-install).
        with patch(
            "voice_typer.server.server_platform.autostart._install_identifier",
            return_value="/path/to/install1/autostart_launcher.py",
        ):
            hash1 = _install_hash_suffix()
        with patch(
            "voice_typer.server.server_platform.autostart._install_identifier",
            return_value="/path/to/install2/autostart_launcher.py",
        ):
            hash2 = _install_hash_suffix()
        assert hash1 != hash2, "different install paths must produce different hashes"


class TestPlatWaylandSocketPermissions:
    """The finding: world-writable Unix socket (0o666) at
    /tmp/voice-typer-hotkey.sock with no authentication. Fix: restrict
    to 0o600 (owner-only).
    """

    def test_socket_chmod_is_owner_only(self):
        # KEEP — pins PLAT-WAYLAND (socket restricted to 0o600).
        # A behavioral test would need to start the socket server and
        # inspect the socket file's permissions, which requires a running
        # WaylandHotkey instance (heavy). Source-string check catches
        # reintroduction of group/other bits directly.
        from voice_typer.server import hotkeys

        src = inspect.getsource(hotkeys.WaylandHotkey._start_socket_server)
        # Must use stat.S_IRUSR | stat.S_IWUSR (0o600)
        assert "stat.S_IRUSR | stat.S_IWUSR" in src, "PLAT-WAYLAND: socket must be restricted to owner-only (0o600)"
        # Must NOT include group/other bits
        chmod_block = src.split("os.chmod")[1].split(")")[0] if "os.chmod" in src else ""
        assert "S_IRGRP" not in chmod_block, "PLAT-WAYLAND: socket must NOT be group-readable"
        assert "S_IWGRP" not in chmod_block, "PLAT-WAYLAND: socket must NOT be group-writable"
        assert "S_IROTH" not in chmod_block, "PLAT-WAYLAND: socket must NOT be world-readable"
        assert "S_IWOTH" not in chmod_block, "PLAT-WAYLAND: socket must NOT be world-writable"


class TestPlatHleakDeadCodeRemoved:
    """The finding: ``_close_mutex_handle`` was defined but never called
    (dead code). Fix: deleted the function.

    ``_instance_hash`` was ALSO dead code — it
    was kept initially under the claim that it was "used for PLAT-RUN",
    but verification showed it had zero call sites and used a different
    input (``os.path.dirname(os.path.abspath(__file__))``) than the
    actual mutex hash (``sys.executable``). It has been deleted too.
    """

    def test_close_mutex_handle_removed(self):
        from voice_typer.server import app

        assert not hasattr(app, "_close_mutex_handle"), "_close_mutex_handle must be removed (dead code)."

    def test_instance_hash_removed(self):
        """``_instance_hash`` was also dead code (zero call
        sites, different input than the actual mutex hash). It must be
        removed to avoid the maintenance hazard of a helper that looks
        like it's used but isn't.
        """
        from voice_typer.server import app

        assert not hasattr(app, "_instance_hash"), (
            "_instance_hash must be removed — it was dead code "
            "(zero call sites) and used a different input than the actual "
            "mutex hash (os.path.dirname(__file__) vs sys.executable)."
        )

    def test_mutex_name_is_fixed_string(self):
        """Mutex name is fixed (not sys.executable hash).

        KEEP — pins PLAT-HLEAK (mutex name is a fixed string,
        not derived from sys.executable hash). A behavioral test would
        need to spawn two processes with different sys.executable and
        observe the mutex collision, which is heavy; the source-string
        check catches reintroduction of the hash directly.
        """
        import inspect

        from voice_typer.server import app as app_mod

        # _ensure_single_instance is now a thin dispatcher; the
        # Windows mutex logic (which sets the mutex name) lives in
        # _ensure_windows_single_instance. Inspect that function so the
        # PLAT-HLEAK invariant is still pinned.
        src = inspect.getsource(app_mod._ensure_windows_single_instance)
        assert "VoiceTyperSingleInstance" in src, "Mutex name must contain VoiceTyperSingleInstance."
        assert "hashlib.sha256(sys.executable.encode())" not in src, "Mutex name must NOT depend on sys.executable."


class TestPlatPumpImportHoisted:
    """The finding: ``import win32gui`` ran on every 1ms iteration of the
    polling loop. Fix: hoist the import to before the loop, store
    ``PumpWaitingMessages`` in a local variable.
    """

    def test_import_hoisted_out_of_loop(self):
        # KEEP — pins PLAT-PUMP (win32gui import hoisted out of
        # the 1ms polling loop). A behavioral test would need to measure
        # import time per loop iteration, which is flaky; the source-string
        # check catches reintroduction of the in-loop import directly.
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey._run_polling_loop)
        # The import must be BEFORE the while loop
        while_idx = src.find("while not self._stop_event")
        import_idx = src.find("import win32gui")
        assert while_idx >= 0
        assert import_idx >= 0
        assert import_idx < while_idx, "'import win32gui' must be hoisted BEFORE the while loop, not inside it."

    def test_pump_messages_stored_in_local(self):
        """The PumpWaitingMessages function must be stored in a local
        variable (``_pump_messages``) and called via that variable
        inside the loop — not re-imported each iteration.

        KEEP — pins PLAT-PUMP (PumpWaitingMessages cached in a
        # local). Same rationale as test_import_hoisted_out_of_loop.
        """
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey._run_polling_loop)
        assert "_pump_messages = win32gui.PumpWaitingMessages" in src or "_pump_messages = None" in src, (
            "PumpWaitingMessages must be stored in _pump_messages local."
        )
        # Inside the loop, must call _pump_messages(), not win32gui.PumpWaitingMessages()
        loop_body = src[src.find("while not self._stop_event") :]
        assert "_pump_messages()" in loop_body, "loop body must call _pump_messages(), not re-import."


class TestWindowsPathMigrationCoverage:
    """The finding: Windows path migration tests incomplete (only source-
    inspection tests existed). Fix: add a functional test that creates
    files in the legacy location and verifies migration.
    """

    def test_migrate_from_legacy_function_exists(self):
        from voice_typer.server import config as cfg_mod

        assert hasattr(cfg_mod, "_migrate_from_legacy"), "PLAT-005: _migrate_from_legacy function must exist."

    def _run_migration(self, tmp_path: Path, monkeypatch, target: Path):
        """Drive ``_migrate_from_legacy`` for real on a Linux-style
        ``XDG_CONFIG_HOME`` layout.

        ``_migrate_from_legacy`` resolves the legacy location from the
        platform helpers (looked up via the ``config`` module attributes
        ``is_windows`` / ``is_macos``) and the env vars, then copies to
        :func:`_config_dir`.  We force the non-Windows/non-macOS branch
        and point ``XDG_CONFIG_HOME`` at ``tmp_path`` so the legacy dir
        is ``tmp_path/voice-typer``; the target is ``target`` (pinned via
        ``config._config_dir``).
        """
        from voice_typer.server import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "is_windows", lambda: False)
        monkeypatch.setattr(cfg_mod, "is_macos", lambda: False)
        monkeypatch.setattr(cfg_mod, "_config_dir", lambda: target)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # ensure a real legacy dir exists at tmp_path/voice-typer
        legacy_dir = tmp_path / "voice-typer"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "config.json").write_text('{"test": true}')
        (legacy_dir / "subdir").mkdir(exist_ok=True)
        (legacy_dir / "subdir" / "corrections.json").write_text("{}")
        assert not target.exists()
        cfg_mod._migrate_from_legacy()
        return legacy_dir

    def _staging_name(self, target: Path) -> str:
        """The PID-scoped staging dir name ``_migrate_from_legacy`` uses."""
        import os

        return target.name + f".migrate-tmp-{os.getpid()}"

    def test_migrate_copies_files_from_legacy_to_new(self, tmp_path, monkeypatch):
        """FI-13-A: ``_migrate_from_legacy`` copies the legacy tree into
        the target config dir (real execution, not just source inspection).
        """
        from voice_typer.server import config as cfg_mod

        target = tmp_path / "new"
        self._run_migration(tmp_path, monkeypatch, target)

        # the migrated files land in the target dir
        assert (target / "config.json").read_text() == '{"test": true}'
        assert (target / "subdir" / "corrections.json").read_text() == "{}"
        # the legacy dir is left in place (one-time copy, not move)
        assert (tmp_path / "voice-typer" / "config.json").exists()
        # no leftover staging dir
        assert not (target.parent / self._staging_name(target)).exists()
        # calling it again is a no-op (target now exists)
        cfg_mod._migrate_from_legacy()
        assert (target / "config.json").read_text() == '{"test": true}'

    def test_migrate_is_atomic_cleanup_on_failed_replace(self, tmp_path, monkeypatch):
        """FI-13-A: if the final ``os.replace`` fails, the migration must
        NOT leave a partially-populated target dir or a stale staging dir.
        """
        import voice_typer.server.config_internals.paths as paths_mod
        from voice_typer.server import config as cfg_mod

        target = tmp_path / "new"
        self._run_migration_prepare(tmp_path, monkeypatch, target)

        calls = []

        def _failing_replace(src, dst):
            calls.append((src, dst))
            raise OSError("simulated rename failure")

        monkeypatch.setattr(paths_mod.os, "replace", _failing_replace)
        with pytest.raises(OSError):
            cfg_mod._migrate_from_legacy()

        # os.replace was attempted exactly once, from the staging dir
        assert len(calls) == 1
        assert Path(calls[0][0]).name == self._staging_name(target)
        assert Path(calls[0][1]) == target
        # target was never created (atomicity — no partial migration)
        assert not target.exists()
        # the staging dir was cleaned up in the finally block
        assert not (target.parent / self._staging_name(target)).exists()
        # the legacy dir is untouched so a later retry can succeed
        assert (tmp_path / "voice-typer" / "config.json").read_text() == '{"test": true}'

    def test_migrate_keeps_concurrently_created_target(self, tmp_path, monkeypatch):
        """FI-13-A: if another process creates ``target`` between the
        guard and the rename (the race branch), the migration must NOT
        raise, must keep the concurrent target, and must clean up staging.
        """
        import voice_typer.server.config_internals.paths as paths_mod
        from voice_typer.server import config as cfg_mod

        target = tmp_path / "new"
        self._run_migration_prepare(tmp_path, monkeypatch, target)

        calls = []

        def _race_replace(src, dst):
            calls.append((src, dst))
            # the concurrent migration finishes BETWEEN the target.exists()
            # guard (which ran while target was absent) and this rename:
            # the target directory appears now.
            target.mkdir()
            (target / "concurrent.json").write_text('{"concurrent": true}')
            raise OSError("destination already exists")

        monkeypatch.setattr(paths_mod.os, "replace", _race_replace)
        # must not raise — the concurrent target is kept
        cfg_mod._migrate_from_legacy()

        assert len(calls) == 1
        # the concurrent result survives
        assert (target / "concurrent.json").read_text() == '{"concurrent": true}'
        # our staging copy was cleaned up
        assert not (target.parent / self._staging_name(target)).exists()

    def test_migrate_sweeps_stale_staging_from_dead_process(self, tmp_path, monkeypatch):
        """FI-13-A: a stale staging dir left by a crashed earlier process
        (different PID suffix) must be swept after a successful migration.
        """
        from voice_typer.server import config as cfg_mod

        target = tmp_path / "new"
        self._run_migration_prepare(tmp_path, monkeypatch, target)

        stale = target.parent / (target.name + ".migrate-tmp-999999")
        stale.mkdir()
        (stale / "partial.json").write_text("{}")

        cfg_mod._migrate_from_legacy()

        # migrated + stale staging swept
        assert (target / "config.json").read_text() == '{"test": true}'
        assert not stale.exists()
        assert not (target.parent / self._staging_name(target)).exists()

    def _run_migration_prepare(self, tmp_path: Path, monkeypatch, target: Path):
        """Same setup as ``_run_migration`` but returns WITHOUT invoking
        the migration (so the caller can inject a failing ``os.replace``
        before it runs)."""
        from voice_typer.server import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "is_windows", lambda: False)
        monkeypatch.setattr(cfg_mod, "is_macos", lambda: False)
        monkeypatch.setattr(cfg_mod, "_config_dir", lambda: target)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        legacy_dir = tmp_path / "voice-typer"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "config.json").write_text('{"test": true}')
        assert not target.exists()

    def test_config_dir_uses_platform_paths(self):
        """_config_dir must check VOICE_TYPER_CONFIG_DIR env var first,
        then fall back to platform-specific paths.

        KEEP — pins PLAT-005 (env var override). A behavioral
        # test would set VOICE_TYPER_CONFIG_DIR and verify the function
        # returns the env-var path, but the source-string check is
        # simpler and catches removal of the env var check directly.
        """
        from voice_typer.server import config as cfg_mod

        src = inspect.getsource(cfg_mod._config_dir)
        assert "VOICE_TYPER_CONFIG_DIR" in src, "PLAT-005: _config_dir must check VOICE_TYPER_CONFIG_DIR env var"


class TestWslDetectionLogic:
    """The finding: no WSL-specific tests. Fix: add a test that verifies
    the IME composition check (used in the polling loop) doesn't crash
    on WSL where win32 APIs aren't available.
    """

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: _is_ime_composing short-circuits to False when Win32 IME APIs are unavailable",
    )
    def test_ime_composition_check_returns_false_on_non_windows(self):
        """On non-Windows platforms, _is_ime_composing must return
        False without crashing.
        """
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        # Create a backend instance without full init
        backend = WindowsNativeHotkey.__new__(WindowsNativeHotkey)
        # On non-Windows, the method should return False
        assert backend._is_ime_composing() is False

    def test_polling_loop_handles_missing_win32gui(self):
        """The polling loop must not crash if win32gui is unavailable
        (e.g., on WSL where pywin32 isn't installed).

        KEEP — pins PLAT-020 (win32gui import guarded by
        try/except ImportError, _pump_messages defaults to None).
        A behavioral test would need to run on WSL (heavy, platform-
        specific); the source-string check catches removal of the guard.
        """
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey._run_polling_loop)
        # The import must be guarded by try/except ImportError
        assert "except ImportError" in src, (
            "win32gui import must be guarded by try/except ImportError so the loop doesn't crash on WSL."
        )
        # _pump_messages must default to None (no crash when win32gui missing)
        assert "_pump_messages = None" in src
