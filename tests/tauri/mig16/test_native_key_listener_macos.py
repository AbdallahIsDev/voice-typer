"""native ``macos-key-listener`` (Swift)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
COMPILE_NATIVE_SH = PROJECT_ROOT / "scripts" / "build" / "compile_native.sh"
BUILD_NATIVE_LISTENER_MACOS_SH = PROJECT_ROOT / "scripts" / "build" / "build_native_listener_macos.sh"
MACOS_KEY_LISTENER_SWIFT = PROJECT_ROOT / "voice_typer" / "server" / "native" / "macos-key-listener.swift"
# Phase 4.5 /: ``native_hotkeys.py`` was split into a package at
NATIVE_HOTKEYS_PKG = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys"
NATIVE_HOTKEYS_PY = NATIVE_HOTKEYS_PKG / "__init__.py"
NATIVE_HOTKEYS_BASE_PY = NATIVE_HOTKEYS_PKG / "base.py"
NATIVE_HOTKEYS_MAC_PY = NATIVE_HOTKEYS_PKG / "mac_backend.py"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
RUNBOOK = PROJECT_ROOT / "docs" / "migration" / "macos-validation-runbook.md"

NATIVE_RESOURCE_PATH = "resources/native/macos-key-listener"


@pytest.fixture
def macos_env(monkeypatch):
    """Patch the platform predicates + sys.platform to look like macOS."""
    from voice_typer.server import native_hotkeys

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
    return native_hotkeys


class TestTauriBundleResources:
    """Verify ``tauri.conf.json`` ships the native listener as a resource."""

    def test_tauri_conf_json_exists(self):
        """The Tauri config file must exist (sanity check)."""
        assert TAURI_CONF.is_file(), f"Missing Tauri config: {TAURI_CONF}"

    def test_tauri_conf_bundles_macos_native_listener(self):
        """``resources/native/macos-key-listener`` must be in bundle.resources."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert NATIVE_RESOURCE_PATH in resources, (
            f"tauri.conf.json bundle.resources must include {NATIVE_RESOURCE_PATH!r} (ADR-0020 §7). Found: {resources}"
        )

    def test_native_listener_is_resource_not_external_bin(self):
        """The native listener is a ``resource`` (sidecar-spawned), NOT ``externalBin``."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        external_bins = conf.get("bundle", {}).get("externalBin", [])
        resources = conf.get("bundle", {}).get("resources", [])

        # Must be in resources.
        assert NATIVE_RESOURCE_PATH in resources
        # Must NOT be in externalBin (Tauri must not spawn it).
        for ext in external_bins:
            assert "macos-key-listener" not in ext, (
                f"macos-key-listener must NOT be in externalBin (Tauri must not spawn it, ADR-0020 §6.4). Found: {ext}"
            )

    def test_tauri_conf_also_bundles_windows_and_linux_listeners(self):
        """All three platform binaries are bundled (cross-platform ship)."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/windows-key-listener.exe" in resources
        assert "resources/native/linux-key-listener" in resources


class TestSubprocessSpawn:
    """Verify ``SubprocessHotkeyBackend._spawn_process`` uses ``subprocess.Popen`` correctly."""

    @pytest.fixture(autouse=True)
    def _verifiable_dummy_manifest(self, monkeypatch, tmp_path):
        """Point the SHA-256 manifest at a tmp manifest matching the dummy binary."""
        import hashlib

        from voice_typer.server.native_hotkeys import binary_path as bp

        manifest = {
            "version": 1,
            "binaries": {
                "macos-key-listener": {
                    "sha256": hashlib.sha256(b"dummy").hexdigest(),
                    "version": "1.0.0",
                }
            },
        }
        manifest_path = tmp_path / "binaries.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        monkeypatch.setattr(bp, "_MANIFEST_PATH", manifest_path)

    def test_spawn_uses_subprocess_popen(self, macos_env, monkeypatch, tmp_path):
        """``_spawn_process`` must call ``subprocess.Popen`` (not ``run``/``call``/``check_output``)."""
        backend = macos_env.MacNativeHotkey("<f8>")
        # Inject a fake binary path so we don't depend on discovery.
        fake_bin = tmp_path / "macos-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        # Pre-set the stop event so the reader thread exits immediately.
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            proc = MagicMock()
            proc.poll.return_value = None  # still running
            proc.stdout.readline.return_value = b""  # EOF
            return proc

        monkeypatch.setattr(macos_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured.get("cmd") is not None, "subprocess.Popen was not called"
        assert captured["cmd"][0] == str(fake_bin)

    def test_spawn_passes_hotkey_spec_as_argv1(self, macos_env, monkeypatch, tmp_path):
        """The hotkey spec string (e.g. ``<f8>``) is passed as ``argv[1]``."""
        backend = macos_env.MacNativeHotkey("<f8>")
        fake_bin = tmp_path / "macos-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            # Mirror the sibling tests' richer proc: _spawn_process
            proc = MagicMock()
            proc.poll.return_value = None  # still running
            proc.stdout.readline.return_value = b""  # EOF
            return proc

        monkeypatch.setattr(macos_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["cmd"][1] == "<f8>", (
            "Hotkey spec must be argv[1] (the native binary parses it to decide which key to watch + suppress)."
        )

    def test_spawn_pipes_stdout_for_wire_protocol(self, macos_env, monkeypatch, tmp_path):
        """stdout=PIPE, stderr=STDOUT, stdin=PIPE."""
        backend = macos_env.MacNativeHotkey("<f8>")
        fake_bin = tmp_path / "macos-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            # Mirror the sibling tests' richer proc: _spawn_process
            proc = MagicMock()
            proc.poll.return_value = None  # still running
            proc.stdout.readline.return_value = b""  # EOF
            return proc

        monkeypatch.setattr(macos_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        kwargs = captured["kwargs"]
        assert kwargs.get("stdout") == subprocess.PIPE, "stdout must be PIPE, reader thread streams wire-protocol lines"
        assert kwargs.get("stderr") == subprocess.STDOUT, (
            "stderr must redirect to stdout so errors surface in the wire stream"
        )
        assert kwargs.get("stdin") == subprocess.PIPE, (
            "stdin must be PIPE, the watchdog writes PING to the child's "
            "stdin and reads PONG from stdout (liveness protocol)"
        )

    def test_spawn_uses_start_new_session_on_macos(self, macos_env, monkeypatch, tmp_path):
        """On macOS, ``start_new_session=True`` so SIGTERM works cleanly."""
        backend = macos_env.MacNativeHotkey("<f8>")
        fake_bin = tmp_path / "macos-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(macos_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["kwargs"].get("start_new_session") is True, (
            "start_new_session must be True on macOS so SIGTERM cleanly "
            "shuts down the Swift binary via its DispatchSource handler"
        )

    def test_spawn_failure_raises_runtime_error(self, macos_env, monkeypatch, tmp_path):
        """If ``Popen`` raises ``OSError``, ``_spawn_process`` raises ``RuntimeError``."""
        backend = macos_env.MacNativeHotkey("<f8>")
        fake_bin = tmp_path / "macos-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        def raising_popen(cmd, **kwargs):
            raise OSError("Executable not found")

        monkeypatch.setattr(macos_env.subprocess, "Popen", raising_popen)

        with pytest.raises(RuntimeError, match="Failed to spawn"):
            backend._spawn_process()
        assert backend._failed is True
        assert backend._error_message is not None


class TestBinaryDiscovery:
    """Verify the binary is discovered via ``VOICE_TYPER_NATIVE_DIR`` or dev/bundle paths."""

    def test_voice_typer_native_dir_lookup_finds_macos_binary(self, macos_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_DIR`` (Tauri dev/prod) points at the bundle's native dir."""
        native_dir = tmp_path / "resources" / "native"
        native_dir.mkdir(parents=True)
        binary = native_dir / "macos-key-listener"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = macos_env.get_native_binary_path()
        assert result is not None
        assert result.name == "macos-key-listener"
        assert result.parent == native_dir

    def test_voice_typer_native_binary_env_takes_precedence(self, macos_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_BINARY`` (single-file override) beats ``_DIR``."""
        single = tmp_path / "custom-listener"
        single.write_text("dummy")

        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "macos-key-listener").write_text("dummy")

        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(single))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = macos_env.get_native_binary_path()
        assert result == single

    def test_production_bundle_resource_path_layout(self, macos_env, monkeypatch, tmp_path):
        """Production layout: ``resourceDir/native/macos-key-listener``."""
        # Simulate Tauri's resourceDir layout.
        resource_dir = tmp_path / "resourceDir"
        # Tauri preserves the relative path from the resources array entry.
        native_subdir = resource_dir / "resources" / "native"
        native_subdir.mkdir(parents=True)
        binary = native_subdir / "macos-key-listener"
        binary.write_text("dummy")

        # Tauri host sets VOICE_TYPER_NATIVE_DIR to the native subdir.
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_subdir))

        result = macos_env.get_native_binary_path()
        assert result is not None
        assert result == binary

    def test_dev_mode_falls_through_to_source_tree(self, macos_env, monkeypatch, tmp_path):
        """Without env vars, lookup falls through to the dev source-tree path."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # The dev-mode path is <module_dir>/native/macos-key-listener.
        module_dir = NATIVE_HOTKEYS_PKG.parent
        expected_dev_path = module_dir / "native" / "macos-key-listener"

        real_is_file = Path.is_file

        def fake_is_file(self):
            if self == expected_dev_path:
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)

        result = macos_env.get_native_binary_path()
        assert result is not None
        assert result == expected_dev_path


class TestWireProtocol:
    """Verify the backend parses the native binary's stdout wire protocol."""

    def test_ready_line_sets_ready_event(self, macos_env):
        """``READY`` unblocks ``start()`` (which waits on ``_ready_event``)."""
        backend = macos_env.MacNativeHotkey("<f8>")
        assert not backend._ready_event.is_set()
        backend._handle_line("READY")
        assert backend._ready_event.is_set()
        assert not backend._failed

    def test_error_line_marks_failed_and_unblocks(self, macos_env):
        """``ERROR:<msg>`` marks the backend failed + unblocks ``start()``."""
        backend = macos_env.MacNativeHotkey("<f8>")
        backend._handle_line("ERROR:Accessibility permission required")
        assert backend._failed
        assert backend._error_message == "Accessibility permission required"
        assert backend._ready_event.is_set()  # unblocks start()

    def test_key_down_f8_fires_dictation_callback(self, macos_env):
        """``KEY_DOWN:F8`` fires the press callback for the ``<f8>`` hotkey."""
        backend = macos_env.MacNativeHotkey("<f8>")
        fired: list[str] = []
        backend._callback = lambda: fired.append("dictation-toggle")
        backend._handle_line("KEY_DOWN:F8")
        assert fired == ["dictation-toggle"]

    def test_key_up_f8_fires_release_callback(self, macos_env):
        """``KEY_UP:F8`` fires the release callback (push-to-talk mode)."""
        backend = macos_env.MacNativeHotkey("<f8>")
        released: list[str] = []
        backend._on_release_callback = lambda: released.append("release")
        backend._handle_line("KEY_UP:F8")
        assert released == ["release"]

    def test_wrong_key_does_not_fire(self, macos_env):
        """``KEY_DOWN:F2`` must NOT fire for an ``<f8>`` hotkey."""
        backend = macos_env.MacNativeHotkey("<f8>")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("KEY_DOWN:F2")
        assert fired == []

    def test_combo_requires_all_modifiers(self, macos_env):
        """``<cmd>+<alt>+v`` fires only when Cmd+Alt are held AND V is pressed."""
        backend = macos_env.MacNativeHotkey("<cmd>+<alt>+v")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")

        # V alone, no fire.
        backend._handle_line("KEY_DOWN:V")
        assert fired == []

        backend._handle_line("KEY_UP:V")

        # Hold Cmd+Alt, then press V, fire.
        backend._handle_line("MOD_DOWN:Cmd")
        backend._handle_line("MOD_DOWN:Alt")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]

    def test_fn_down_fires_for_fn_only_hotkey(self, macos_env):
        """``<fn>`` (Fn/Globe key) fires on ``FN_DOWN``, macOS-only wire event."""
        backend = macos_env.MacNativeHotkey("<fn>")
        assert backend._parsed is not None
        assert backend._parsed["is_fn_only"] is True

        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("FN_DOWN")
        assert fired == ["press"]


class TestAccessibilityPermission:
    """Verify the Swift source requires Accessibility permission."""

    def test_macos_key_listener_swift_source_exists(self):
        assert MACOS_KEY_LISTENER_SWIFT.is_file(), f"Missing Swift source: {MACOS_KEY_LISTENER_SWIFT}"

    def test_swift_source_uses_cgevent_tap(self):
        """The Swift source must create a CGEventTap (``CGEvent.tapCreate``)."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "CGEvent.tapCreate" in src, (
            "macos-key-listener.swift must call CGEvent.tapCreate (the "
            "CGEventTap that gives both key-up delivery + key suppression)"
        )
        assert ".defaultTap" in src, (
            "macos-key-listener.swift must use .defaultTap (not .listenOnly) "
            "— .defaultTap is what enables key suppression but requires "
            "Accessibility permission"
        )

    def test_swift_source_emits_error_on_missing_accessibility(self):
        """The binary emits ``ERROR:Accessibility permission required`` when tapCreate fails."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "Accessibility permission required" in src, (
            "macos-key-listener.swift must emit ERROR:Accessibility permission "
            "required when CGEvent.tapCreate returns nil (missing Accessibility grant)"
        )

    def test_swift_source_supports_skip_accessibility_check_env(self):
        """The binary supports ``VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1`` for CI smoke tests."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK" in src, (
            "macos-key-listener.swift must support VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1 for CI smoke tests"
        )


class TestCGEventTap:
    """Verify the Swift source installs a CGEventTap for global hotkey capture."""

    def test_swift_source_creates_session_event_tap(self):
        """The tap is created on ``.cgSessionEventTap`` (per-user session events)."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert ".cgSessionEventTap" in src, (
            "macos-key-listener.swift must use .cgSessionEventTap "
            "(the per-user session event tap for global hotkey capture)"
        )

    def test_swift_source_handles_keydown_and_keyup(self):
        """The tap subscribes to both ``.keyDown`` and ``.keyUp``."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "CGEventType.keyDown" in src, (
            "macos-key-listener.swift must subscribe to CGEventType.keyDown (for suppression)"
        )
        assert "CGEventType.keyUp" in src, (
            "macos-key-listener.swift must subscribe to CGEventType.keyUp "
            "(for reliable key-up delivery, NSEvent global monitors miss keyUp)"
        )

    def test_swift_source_installs_run_loop_source(self):
        """The tap is scheduled on the main run loop via ``CFRunLoopAddSource``."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "CFMachPortCreateRunLoopSource" in src, (
            "macos-key-listener.swift must create a run loop source for the tap"
        )
        assert "CFRunLoopAddSource" in src, "macos-key-listener.swift must add the tap source to the run loop"

    def test_swift_source_handles_tap_disabled_events(self):
        """The tap re-enables itself on ``tapDisabledByTimeout`` / ``tapDisabledByUserInput``."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert ".tapDisabledByTimeout" in src, (
            "macos-key-listener.swift must handle tapDisabledByTimeout "
            "(the OS can disable the tap; the binary must re-enable it)"
        )
        assert ".tapDisabledByUserInput" in src, "macos-key-listener.swift must handle tapDisabledByUserInput"

    def test_swift_source_also_uses_nsevent_monitors(self):
        """The binary uses NSEvent global monitors alongside the CGEventTap."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "addGlobalMonitorForEvents" in src, (
            "macos-key-listener.swift must install NSEvent global monitors "
            "(.flagsChanged for FN/modifiers, .keyDown for non-modifier keys)"
        )
        assert ".flagsChanged" in src, (
            "macos-key-listener.swift must monitor .flagsChanged "
            "(the only NSEvent that surfaces Fn/Globe + modifier transitions)"
        )
        assert ".keyDown" in src, "macos-key-listener.swift must monitor .keyDown (for non-modifier key-down emission)"


class TestFnGlobeKey:
    """Verify the Swift source supports the Fn / Globe key."""

    def test_swift_source_detects_fn_via_modifier_flag(self):
        """Fn is detected via ``NSEvent.modifierFlags.contains(.function)``."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert ".function" in src, (
            "macos-key-listener.swift must detect Fn via "
            "NSEvent.modifierFlags.contains(.function) (bit 23), NOT keyCode == 63"
        )

    def test_swift_source_emits_fn_down_and_fn_up(self):
        """The binary emits ``FN_DOWN`` / ``FN_UP`` edge-detected wire events."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert '"FN_DOWN"' in src or '"FN_DOWN' in src or 'emit("FN_DOWN")' in src, (
            "macos-key-listener.swift must emit FN_DOWN on Fn press (edge-detected)"
        )
        assert '"FN_UP"' in src or '"FN_UP' in src or 'emit("FN_UP")' in src, (
            "macos-key-listener.swift must emit FN_UP on Fn release (edge-detected)"
        )

    def test_python_backend_supports_fn_only_hotkey(self, macos_env):
        """The Python backend parses ``<fn>`` as a Fn-only hotkey."""
        backend = macos_env.MacNativeHotkey("<fn>")
        assert backend._parsed is not None
        assert backend._parsed["is_fn_only"] is True
        assert "fn" in backend._parsed["modifiers"]

        fired: list[str] = []
        released: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._on_release_callback = lambda: released.append("release")

        # FN_DOWN → press callback fires.
        backend._handle_line("FN_DOWN")
        assert fired == ["press"]

        # FN_UP → release callback fires (push-to-talk mode).
        backend._handle_line("FN_UP")
        assert released == ["release"]

    def test_macos_native_backend_supports_fn(self, macos_env):
        """``MacNativeHotkey.supports_fn`` is True (only macOS supports Fn)."""
        backend = macos_env.MacNativeHotkey("<fn>")
        assert backend.supports_fn is True, (
            "MacNativeHotkey.supports_fn must be True, Fn/Globe is the "
            "default macOS hotkey (config._default_hotkey_for_platform)"
        )

    def test_windows_backend_rejects_fn(self, macos_env):
        """``WindowsHookHotkey`` rejects ``<fn>`` specs (firmware-only on Windows)."""
        backend = macos_env.WindowsHookHotkey("<fn>")
        # NOTE: is_windows() is patched False by macos_env fixture, so this
        macos_env.is_windows = lambda: True
        try:
            err = backend._validate_platform()
        finally:
            macos_env.is_windows = lambda: False
        assert err is not None
        assert "FN" in err or "fn" in err, "WindowsHookHotkey must reject <fn> specs, Fn is firmware-only on Windows"


class TestKeySuppression:
    """Verify the Swift source implements key suppression."""

    def test_swift_source_has_should_suppress_keydown_function(self):
        """The Swift source has a ``shouldSuppressKeyDown`` decision function."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "shouldSuppressKeyDown" in src, (
            "macos-key-listener.swift must define shouldSuppressKeyDown(), "
            "the decision function that returns true to swallow a keystroke"
        )

    def test_swift_source_returns_nil_to_swallow(self):
        """The CGEventTap callback returns ``nil`` to suppress matched keystrokes."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "return nil" in src, (
            "The CGEventTap callback must `return nil` to suppress a matched "
            "keystroke, returning the event would pass it through to the "
            "foreground app"
        )

    def test_swift_source_swallows_caps_lock_toggle(self):
        """Caps Lock hotkey suppresses the OS caps-state toggle."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "CapsLock" in src or "caps_lock" in src, "macos-key-listener.swift must handle the CapsLock hotkey"
        assert "57" in src, (
            "macos-key-listener.swift must reference keyCode 57 (CapsLock) for caps-state-toggle suppression"
        )

    def test_swift_source_swallows_keyup_for_suppressed_keydown(self):
        """The binary swallows the matching keyUp for a suppressed keyDown."""
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "suppressedKeyCode" in src, (
            "macos-key-listener.swift must track suppressedKeyCode so the "
            "matching keyUp is also swallowed (no orphan keyUp)"
        )


class TestSidecarOwnership:
    """Verify the Python sidecar (not the Tauri host) owns the native listener."""

    def test_native_hotkeys_module_lives_in_python_sidecar(self):
        """``native_hotkeys.py`` lives under ``voice_typer/server/`` (the sidecar package)."""
        assert NATIVE_HOTKEYS_PY.is_file(), f"native_hotkeys.py must exist in the Python sidecar: {NATIVE_HOTKEYS_PY}"
        # Confirm it's under voice_typer/server/ (the sidecar package).
        assert "voice_typer" in NATIVE_HOTKEYS_PY.parts
        assert "server" in NATIVE_HOTKEYS_PY.parts

    def test_native_hotkeys_module_defines_macos_backend(self):
        """``native_hotkeys`` defines ``SubprocessHotkeyBackend`` + ``MacNativeHotkey``."""
        import voice_typer.server.native_hotkeys.base as base_mod
        from voice_typer.server.native_hotkeys._core import SubprocessHotkeyBackend as CoreBackend

        mac_src = NATIVE_HOTKEYS_MAC_PY.read_text(encoding="utf-8")
        # The facade re-export resolves to the REAL class (defined in _core).
        assert base_mod.SubprocessHotkeyBackend is CoreBackend, (
            "native_hotkeys/base.py must re-export SubprocessHotkeyBackend "
            "(resolved from native_hotkeys._core, the base class that "
            "spawns the native binary via subprocess.Popen)"
        )
        assert "class MacNativeHotkey" in mac_src, (
            "native_hotkeys/mac_backend.py must define MacNativeHotkey (the macOS subclass)"
        )
        assert "subprocess.Popen" in (NATIVE_HOTKEYS_PKG / "_core.py").read_text(encoding="utf-8"), (
            "native_hotkeys/_core.py must use subprocess.Popen to spawn the binary"
        )

    def test_adr_states_sidecar_owns_hotkey_subsystem(self):
        """ADR-0020 §6.4 explicitly states Tauri does not touch the hotkey subsystem."""
        assert ADR_0020.is_file()
        src = ADR_0020.read_text(encoding="utf-8")
        # The ADR must state the sidecar (not Tauri) owns the hotkey subsystem.
        assert "Python sidecar" in src or "sidecar" in src.lower(), (
            "ADR-0020 must reference the Python sidecar as the hotkey owner"
        )
        # And it must explicitly say Tauri does NOT touch hotkeys.
        assert "Tauri does not touch the hotkey subsystem" in src or ("does not touch" in src and "hotkey" in src), (
            "ADR-0020 §6.4 must state 'Tauri does not touch the hotkey subsystem'"
        )

    def test_adr_mandates_keeping_native_binary(self):
        """ADR-0020 §6.4 mandates KEEPING the native binary (do NOT switch to the Tauri plugin)."""
        src = ADR_0020.read_text(encoding="utf-8")
        assert "tauri-plugin-global-shortcut" in src, (
            "ADR-0020 must reference tauri-plugin-global-shortcut (the rejected alternative)"
        )
        assert "keep the native" in src.lower() or "do NOT switch" in src, (
            "ADR-0020 must mandate keeping the native binary"
        )

    def test_adr_documents_fn_globe_key_regression(self):
        """ADR-0020 §6.4 documents the Fn/Globe key regression risk."""
        src = ADR_0020.read_text(encoding="utf-8")
        assert "Fn" in src and "Globe" in src, (
            "ADR-0020 §6.4 must reference the Fn/Globe key (the macOS default hotkey)"
        )
        assert "NSEvent.modifierFlags.function" in src or "modifierFlags" in src, (
            "ADR-0020 §6.4 must document that the Swift binary detects Fn via "
            "NSEvent.modifierFlags.function (the Tauri plugin cannot)"
        )

    def test_tauri_does_not_spawn_native_listener_directly(self):
        """Tauri's ``externalBin`` must NOT list the native listener."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        external_bins = conf.get("bundle", {}).get("externalBin", [])
        for ext in external_bins:
            assert "key-listener" not in ext, (
                f"Native key-listener must NOT be in externalBin (Tauri must not spawn it). Found: {ext}"
            )

    def test_runbook_documents_sidecar_ownership(self):
        """Runbook §6.7 documents the native listener + the sidecar."""
        assert RUNBOOK.is_file()
        src = RUNBOOK.read_text(encoding="utf-8")
        assert "macos-key-listener" in src, "Runbook must document the macos-key-listener gate"
        # The runbook references ADR-0020 §6.4 (which mandates sidecar
        assert "6.4" in src or "python-sidecar" in src, (
            "Runbook must reference ADR-0020 §6.4 or python-sidecar "
            "(the sidecar binary that owns the listener lifecycle)"
        )


class TestUniversalOrPerArch:
    """Verify the binary is universal OR per-arch."""

    def test_build_native_listener_macos_sh_exists(self):
        """The macOS build wrapper script must exist."""
        assert BUILD_NATIVE_LISTENER_MACOS_SH.is_file(), f"Missing build script: {BUILD_NATIVE_LISTENER_MACOS_SH}"

    def test_build_script_invokes_compile_native_sh(self):
        """The wrapper invokes ``compile_native.sh`` (which runs ``swiftc``)."""
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "compile_native.sh" in src, "build_native_listener_macos.sh must invoke compile_native.sh"
        assert "swiftc" in src or "compile_native.sh" in src, (
            "build_native_listener_macos.sh must (transitively) invoke swiftc"
        )

    def test_build_script_copies_binary_to_tauri_resources(self):
        """The wrapper copies the compiled binary to ``src-tauri/resources/native/``."""
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "src-tauri/resources/native" in src, (
            "build_native_listener_macos.sh must copy the binary to "
            "src-tauri/resources/native/ (the tauri.conf.json bundle.resources path)"
        )
        assert "macos-key-listener" in src, "build_native_listener_macos.sh must reference macos-key-listener"

    def test_build_script_codesigns_ad_hoc(self):
        """The wrapper ad-hoc codesigns the binary."""
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "codesign" in src, (
            "build_native_listener_macos.sh must codesign the binary (ad-hoc at minimum, Developer ID for distribution)"
        )

    def test_build_script_enforces_macos_host(self):
        """The wrapper refuses to run on non-macOS hosts."""
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "Darwin" in src or "uname -s" in src, (
            "build_native_listener_macos.sh must enforce macOS host (uname -s == Darwin)"
        )

    def test_swift_source_compiles_to_host_arch(self):
        """``compile_native.sh`` runs ``swiftc -O`` for the host arch."""
        src = COMPILE_NATIVE_SH.read_text(encoding="utf-8")
        assert "swiftc" in src, "compile_native.sh must invoke swiftc for the macOS build"
        # The Swift source's build instructions (header comment) must
        swift_src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "swiftc" in swift_src, "macos-key-listener.swift header must document the swiftc build command"
        assert "Cocoa" in swift_src, "macos-key-listener.swift must import Cocoa (NSEvent, NSApplication)"
        assert "CoreGraphics" in swift_src, "macos-key-listener.swift must import CoreGraphics (CGEvent tap)"
