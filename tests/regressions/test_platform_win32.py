"""The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestManifestInExists:
    """PLAT-036."""

    def test_manifest_in_exists(self):
        manifest = Path(__file__).resolve().parent.parent.parent / "MANIFEST.in"
        assert manifest.exists(), "PLAT-036: MANIFEST.in must exist at the repo root."

    def test_manifest_in_includes_key_files(self):
        # KEEP, pins  (MANIFEST.in includes critical data

        manifest = Path(__file__).resolve().parent.parent.parent / "MANIFEST.in"
        content = manifest.read_text()
        # Must include the critical data files
        assert "corrections.json" in content, "PLAT-036: MANIFEST.in must include corrections.json"
        assert "LICENSE" in content
        assert "README.md" in content


class TestWindowsManifestAsInvoker:
    """The finding: no requestedExecutionLevel manifest. Investigation:"""

    def test_manifest_source_is_embedded_in_spec(self):
        spec = Path(__file__).resolve().parent.parent.parent / "scripts" / "build" / "lausu.spec"
        content = spec.read_text()
        assert 'requestedExecutionLevel level="asInvoker"' in content, (
            "PLAT-037: the .spec's inlined manifest must declare requestedExecutionLevel asInvoker."
        )

    def test_manifest_declares_as_invoker(self):
        # KEEP, pins the asInvoker declaration.

        spec = Path(__file__).resolve().parent.parent.parent / "scripts" / "build" / "lausu.spec"
        content = spec.read_text()
        assert 'requestedExecutionLevel level="asInvoker"' in content, (
            "PLAT-037: manifest must declare requestedExecutionLevel asInvoker."
        )

    def test_spec_file_embeds_manifest(self):
        # KEEP, pins  (.spec file references the manifest).

        spec = Path(__file__).resolve().parent.parent.parent / "scripts" / "build" / "lausu.spec"
        content = spec.read_text()
        assert "manifest" in content.lower(), "PLAT-037: .spec file must reference the manifest."


class TestPlatRunAutostartTaskHashed:
    """The finding: autostart task name was a fixed string"""

    def test_autostart_task_name_includes_hash_suffix(self):
        # KEEP, pins PLAT-RUN (autostart task name includes
        from voice_typer.server.server_platform import autostart as platform

        src = inspect.getsource(platform)
        assert "_install_hash_suffix" in src, "_install_hash_suffix helper must exist."
        # PLAT-RUN: the task name must include the per-install hash so
        # two installs never collide. Asserted on the RUNTIME value (the
        # name is built from the canonical ``com.Lausu.autostart`` root +
        # the hash), so a comment/reflow cannot satisfy it.
        name = platform._APP_AUTOSTART_TASK_NAME
        suffix = platform._install_hash_suffix()
        assert name.startswith("com.Lausu.autostart"), name
        assert suffix == "" or name == f"com.Lausu.autostart{suffix}", name

    def test_install_hash_suffix_returns_underscore_prefix(self):
        """The hash suffix must start with '_' so the task name reads"""
        from voice_typer.server.server_platform import _install_hash_suffix

        suffix = _install_hash_suffix()
        # Must start with '_' (or be empty on failure)
        assert suffix == "" or suffix.startswith("_"), f"hash suffix must start with '_', got {suffix!r}"
        # Must be 9 chars: '_' + 8 hex chars (or empty)
        assert suffix == "" or len(suffix) == 9, f"hash suffix must be '_XXXXXXXX' (9 chars), got {suffix!r}"

    def test_two_different_executables_get_different_hashes(self):
        """Two different install paths must produce different hash suffixes."""
        from voice_typer.server.server_platform import _install_hash_suffix

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
    """The finding: world-writable Unix socket (0o666) at"""

    def test_socket_chmod_is_owner_only(self):
        # KEEP, pins PLAT-WAYLAND (socket restricted to 0o600).
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
    """The finding: ``_close_mutex_handle`` was defined but never called"""

    def test_close_mutex_handle_removed(self):
        from voice_typer.server import app

        assert not hasattr(app, "_close_mutex_handle"), "_close_mutex_handle must be removed (dead code)."

    def test_instance_hash_removed(self):
        """sites, different input than the actual mutex hash). It must be"""
        from voice_typer.server import app

        assert not hasattr(app, "_instance_hash"), (
            "_instance_hash must be removed, it was dead code "
            "(zero call sites) and used a different input than the actual "
            "mutex hash (os.path.dirname(__file__) vs sys.executable)."
        )

    def test_mutex_name_is_fixed_string(self):
        """
        Mutex name is fixed (not sys.executable hash).
        KEEP, pins PLAT-HLEAK (mutex name is a fixed string,
        """
        import inspect

        from voice_typer.server import app as app_mod

        # PLAT-HLEAK invariant is still pinned.
        src = inspect.getsource(app_mod._ensure_windows_single_instance)
        assert "LausuSingleInstance" in src, "Mutex name must contain LausuSingleInstance."
        assert "hashlib.sha256(sys.executable.encode())" not in src, "Mutex name must NOT depend on sys.executable."


class TestPlatPumpImportHoisted:
    """polling loop. Fix: hoist the import to before the loop, store"""

    def test_import_hoisted_out_of_loop(self):
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey._run_polling_loop)
        # The import must be BEFORE the while loop
        while_idx = src.find("while not self._stop_event")
        import_idx = src.find("import win32gui")
        assert while_idx >= 0
        assert import_idx >= 0
        assert import_idx < while_idx, "'import win32gui' must be hoisted BEFORE the while loop, not inside it."

    def test_pump_messages_stored_in_local(self):
        """The PumpWaitingMessages function must be stored in a local"""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey._run_polling_loop)
        assert "_pump_messages = win32gui.PumpWaitingMessages" in src or "_pump_messages = None" in src, (
            "PumpWaitingMessages must be stored in _pump_messages local."
        )
        # Inside the loop, must call _pump_messages(), not win32gui.PumpWaitingMessages()
        loop_body = src[src.find("while not self._stop_event") :]
        assert "_pump_messages()" in loop_body, "loop body must call _pump_messages(), not re-import."


class TestWindowsPathMigrationCoverage:
    """The finding: Windows path migration tests incomplete (only source-"""

    def test_migrate_from_legacy_function_exists(self):
        from voice_typer.server import config as cfg_mod

        assert hasattr(cfg_mod, "_migrate_from_legacy"), "PLAT-005: _migrate_from_legacy function must exist."

    def _run_migration(self, tmp_path: Path, monkeypatch, target: Path):
        """Drive ``_migrate_from_legacy`` for real on a Linux-style"""
        from voice_typer.server import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "is_windows", lambda: False)
        monkeypatch.setattr(cfg_mod, "is_macos", lambda: False)
        monkeypatch.setattr(cfg_mod, "_config_dir", lambda: target)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        legacy_dir = tmp_path / "lausu"
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
        """FI-13-A: ``_migrate_from_legacy`` copies the legacy tree into"""
        from voice_typer.server import config as cfg_mod

        target = tmp_path / "new"
        self._run_migration(tmp_path, monkeypatch, target)

        assert (target / "config.json").read_text() == '{"test": true}'
        assert (target / "subdir" / "corrections.json").read_text() == "{}"
        assert (tmp_path / "lausu" / "config.json").exists()
        assert not (target.parent / self._staging_name(target)).exists()
        cfg_mod._migrate_from_legacy()
        assert (target / "config.json").read_text() == '{"test": true}'

    def test_migrate_is_atomic_cleanup_on_failed_replace(self, tmp_path, monkeypatch):
        """FI-13-A: if the final ``os.replace`` fails, the migration must"""
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

        assert len(calls) == 1
        assert Path(calls[0][0]).name == self._staging_name(target)
        assert Path(calls[0][1]) == target
        assert not target.exists()
        assert not (target.parent / self._staging_name(target)).exists()
        assert (tmp_path / "lausu" / "config.json").read_text() == '{"test": true}'

    def test_migrate_keeps_concurrently_created_target(self, tmp_path, monkeypatch):
        """guard and the rename (the race branch), the migration must NOT"""
        import voice_typer.server.config_internals.paths as paths_mod
        from voice_typer.server import config as cfg_mod

        target = tmp_path / "new"
        self._run_migration_prepare(tmp_path, monkeypatch, target)

        calls = []

        def _race_replace(src, dst):
            calls.append((src, dst))
            target.mkdir()
            (target / "concurrent.json").write_text('{"concurrent": true}')
            raise OSError("destination already exists")

        monkeypatch.setattr(paths_mod.os, "replace", _race_replace)
        cfg_mod._migrate_from_legacy()

        assert len(calls) == 1
        assert (target / "concurrent.json").read_text() == '{"concurrent": true}'
        assert not (target.parent / self._staging_name(target)).exists()

    def test_migrate_sweeps_stale_staging_from_dead_process(self, tmp_path, monkeypatch):
        """FI-13-A: a stale staging dir left by a crashed earlier process"""
        from voice_typer.server import config as cfg_mod

        target = tmp_path / "new"
        self._run_migration_prepare(tmp_path, monkeypatch, target)

        stale = target.parent / (target.name + ".migrate-tmp-999999")
        stale.mkdir()
        (stale / "partial.json").write_text("{}")

        cfg_mod._migrate_from_legacy()

        assert (target / "config.json").read_text() == '{"test": true}'
        assert not stale.exists()
        assert not (target.parent / self._staging_name(target)).exists()

    def _run_migration_prepare(self, tmp_path: Path, monkeypatch, target: Path):
        """Same setup as ``_run_migration`` but returns WITHOUT invoking"""
        from voice_typer.server import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "is_windows", lambda: False)
        monkeypatch.setattr(cfg_mod, "is_macos", lambda: False)
        monkeypatch.setattr(cfg_mod, "_config_dir", lambda: target)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        legacy_dir = tmp_path / "lausu"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "config.json").write_text('{"test": true}')
        assert not target.exists()

    @pytest.mark.real_config_dir  # inspects the REAL resolver's source; never writes
    def test_config_dir_uses_platform_paths(self):
        """
        _config_dir must check VOICE_TYPER_CONFIG_DIR env var first,
        KEEP, pins PLAT-005 (env var override). A behavioral
        """
        from voice_typer.server import config as cfg_mod

        src = inspect.getsource(cfg_mod._config_dir)
        assert "VOICE_TYPER_CONFIG_DIR" in src, "PLAT-005: _config_dir must check VOICE_TYPER_CONFIG_DIR env var"


class TestWslDetectionLogic:
    """The finding: no WSL-specific tests. Fix: add a test that verifies"""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: _is_ime_composing short-circuits to False when Win32 IME APIs are unavailable",
    )
    def test_ime_composition_check_returns_false_on_non_windows(self):
        """On non-Windows platforms, _is_ime_composing must return"""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        # Create a backend instance without full init
        backend = WindowsNativeHotkey.__new__(WindowsNativeHotkey)
        # On non-Windows, the method should return False
        assert backend._is_ime_composing() is False

    def test_polling_loop_handles_missing_win32gui(self):
        """
        The polling loop must not crash if win32gui is unavailable
        KEEP, pins PLAT-020 (win32gui import guarded by
        """
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        src = inspect.getsource(WindowsNativeHotkey._run_polling_loop)
        # The import must be guarded by try/except ImportError
        assert "except ImportError" in src, (
            "win32gui import must be guarded by try/except ImportError so the loop doesn't crash on WSL."
        )
        # _pump_messages must default to None (no crash when win32gui missing)
        assert "_pump_messages = None" in src
