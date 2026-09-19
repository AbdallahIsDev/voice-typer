"""native ``windows-key-listener.exe``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
COMPILE_NATIVE_PS1 = PROJECT_ROOT / "scripts" / "build" / "compile_native.ps1"
WINDOWS_KEY_LISTENER_C = PROJECT_ROOT / "voice_typer" / "server" / "native" / "windows-key-listener.c"
# Phase 4.5 /: ``native_hotkeys.py`` was split into a package at
NATIVE_HOTKEYS_PKG = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys"
NATIVE_HOTKEYS_PY = NATIVE_HOTKEYS_PKG / "__init__.py"
NATIVE_HOTKEYS_BASE_PY = NATIVE_HOTKEYS_PKG / "base.py"
NATIVE_HOTKEYS_CORE_PY = NATIVE_HOTKEYS_PKG / "_core.py"
NATIVE_HOTKEYS_WINDOWS_PY = NATIVE_HOTKEYS_PKG / "windows_backend.py"
NATIVE_HOTKEYS_MAC_PY = NATIVE_HOTKEYS_PKG / "mac_backend.py"
NATIVE_HOTKEYS_LINUX_PY = NATIVE_HOTKEYS_PKG / "linux_backend.py"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
RUNBOOK = PROJECT_ROOT / "docs" / "migration" / "windows-validation-runbook.md"

NATIVE_RESOURCE_PATH = "resources/native/windows-key-listener.exe"


@pytest.fixture
def windows_env(monkeypatch):
    """Patch the platform predicates + sys.platform to look like Windows."""
    from voice_typer.server import native_hotkeys

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
    return native_hotkeys


class TestTauriBundleResources:
    """Verify ``tauri.conf.json`` ships the native listener as a resource."""

    def test_tauri_conf_json_exists(self):
        """The Tauri config file must exist (sanity check)."""
        assert TAURI_CONF.is_file(), f"Missing Tauri config: {TAURI_CONF}"

    def test_tauri_conf_bundles_windows_native_listener(self):
        """``resources/native/windows-key-listener.exe`` must be in bundle.resources."""
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
            assert "windows-key-listener" not in ext, (
                f"windows-key-listener must NOT be in externalBin "
                f"(Tauri must not spawn it, ADR-0020 §6.4). Found: {ext}"
            )

    def test_tauri_conf_also_bundles_macos_and_linux_listeners(self):
        """All three platform binaries are bundled (cross-platform ship)."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/macos-key-listener" in resources
        assert "resources/native/linux-key-listener" in resources


class TestSubprocessSpawn:
    """Verify ``SubprocessHotkeyBackend._spawn_process`` uses ``subprocess.Popen`` correctly."""

    @pytest.fixture(autouse=True)
    def _trusted_binary(self, monkeypatch):
        """Bypass SHA-256 verification for the dummy test binaries."""
        import voice_typer.server.native_hotkeys.binary_path as bp

        monkeypatch.setattr(bp, "verify_native_binary_or_skip", lambda path: True)

    def test_spawn_uses_subprocess_popen(self, windows_env, monkeypatch, tmp_path):
        """``_spawn_process`` must call ``subprocess.Popen`` (not ``run``/``call``/``check_output``)."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        # Inject a fake binary path so we don't depend on discovery.
        fake_bin = tmp_path / "windows-key-listener.exe"
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

        monkeypatch.setattr(windows_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured.get("cmd") is not None, "subprocess.Popen was not called"
        assert captured["cmd"][0] == str(fake_bin)

    def test_spawn_passes_hotkey_spec_as_argv1(self, windows_env, monkeypatch, tmp_path):
        """The hotkey spec string (e.g. ``<f8>``) is passed as ``argv[1]``."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        fake_bin = tmp_path / "windows-key-listener.exe"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return MagicMock()

        monkeypatch.setattr(windows_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["cmd"][1] == "<f8>", (
            "Hotkey spec must be argv[1] (the native binary parses it to decide which key to watch + suppress)."
        )

    def test_spawn_pipes_stdout_for_wire_protocol(self, windows_env, monkeypatch, tmp_path):
        """stdout=PIPE, stderr=STDOUT, stdin=DEVNULL."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        fake_bin = tmp_path / "windows-key-listener.exe"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(windows_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        kwargs = captured["kwargs"]
        assert kwargs.get("stdout") == subprocess.PIPE, "stdout must be PIPE, reader thread streams wire-protocol lines"
        assert kwargs.get("stderr") == subprocess.STDOUT, (
            "stderr must redirect to stdout so errors surface in the wire stream"
        )
        assert kwargs.get("stdin") == subprocess.PIPE, (
            "stdin must be PIPE, the liveness watchdog writes PING\\n to it "
            "(the binary answers PONG; see _watchdog_loop)"
        )

    def test_spawn_uses_create_no_window_on_windows(self, windows_env, monkeypatch, tmp_path):
        """On Windows, ``creationflags`` includes ``CREATE_NO_WINDOW``."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        fake_bin = tmp_path / "windows-key-listener.exe"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        sentinel = 0x08000000
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", sentinel, raising=False)

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(windows_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["kwargs"].get("creationflags") == sentinel, (
            "creationflags must include CREATE_NO_WINDOW on Windows so no console window pops up alongside the listener"
        )

    def test_spawn_failure_raises_runtime_error(self, windows_env, monkeypatch, tmp_path):
        """If ``Popen`` raises ``OSError``, ``_spawn_process`` raises ``RuntimeError``."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        backend._binary_path = tmp_path / "windows-key-listener.exe"
        backend._stop_event.set()

        def raising_popen(cmd, **kwargs):
            raise OSError("Executable not found")

        monkeypatch.setattr(windows_env.subprocess, "Popen", raising_popen)

        with pytest.raises(RuntimeError, match="Failed to spawn"):
            backend._spawn_process()
        assert backend._failed is True
        assert backend._error_message is not None


class TestBinaryDiscovery:
    """Verify the binary is discovered via ``VOICE_TYPER_NATIVE_DIR`` or dev/bundle paths."""

    def test_voice_typer_native_dir_lookup_finds_windows_binary(self, windows_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_DIR`` (Tauri dev/prod) points at the bundle's native dir."""
        native_dir = tmp_path / "resources" / "native"
        native_dir.mkdir(parents=True)
        binary = native_dir / "windows-key-listener.exe"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = windows_env.get_native_binary_path()
        assert result is not None
        assert result.name == "windows-key-listener.exe"
        assert result.parent == native_dir

    def test_voice_typer_native_binary_env_takes_precedence(self, windows_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_BINARY`` (single-file override) beats ``_DIR``."""
        single = tmp_path / "custom-listener.exe"
        single.write_text("dummy")

        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "windows-key-listener.exe").write_text("dummy")

        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(single))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = windows_env.get_native_binary_path()
        assert result == single

    def test_production_bundle_resource_path_layout(self, windows_env, monkeypatch, tmp_path):
        """Production layout: ``resourceDir/native/windows-key-listener.exe``."""
        # Simulate Tauri's resourceDir layout.
        resource_dir = tmp_path / "resourceDir"
        # Tauri preserves the relative path from the resources array entry.
        native_subdir = resource_dir / "resources" / "native"
        native_subdir.mkdir(parents=True)
        binary = native_subdir / "windows-key-listener.exe"
        binary.write_text("dummy")

        # Tauri host sets VOICE_TYPER_NATIVE_DIR to the native subdir.
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_subdir))

        result = windows_env.get_native_binary_path()
        assert result is not None
        assert result == binary

    def test_dev_mode_falls_through_to_source_tree(self, windows_env, monkeypatch, tmp_path):
        """Without env vars, lookup falls through to the dev source-tree path."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # The dev-mode path is <module_dir>/native/windows-key-listener.exe.
        module_dir = NATIVE_HOTKEYS_PKG.parent
        expected_dev_path = module_dir / "native" / "windows-key-listener.exe"

        real_is_file = Path.is_file

        def fake_is_file(self):
            if self == expected_dev_path:
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)

        result = windows_env.get_native_binary_path()
        assert result is not None
        assert result == expected_dev_path


class TestWireProtocol:
    """Verify the backend parses the native binary's stdout wire protocol."""

    def test_ready_line_sets_ready_event(self, windows_env):
        """``READY`` unblocks ``start()`` (which waits on ``_ready_event``)."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        assert not backend._ready_event.is_set()
        backend._handle_line("READY")
        assert backend._ready_event.is_set()
        assert not backend._failed

    def test_error_line_marks_failed_and_unblocks(self, windows_env):
        """``ERROR:<msg>`` marks the backend failed + unblocks ``start()``."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        backend._handle_line("ERROR:hook install denied")
        assert backend._failed
        assert backend._error_message == "hook install denied"
        assert backend._ready_event.is_set()  # unblocks start()

    def test_key_down_f8_fires_dictation_callback(self, windows_env):
        """``KEY_DOWN:F8`` fires the press callback for the ``<f8>`` hotkey."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        fired: list[str] = []
        backend._callback = lambda: fired.append("dictation-toggle")
        backend._handle_line("KEY_DOWN:F8")
        assert fired == ["dictation-toggle"]

    def test_key_up_f8_fires_release_callback(self, windows_env):
        """``KEY_UP:F8`` fires the release callback (push-to-talk mode)."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        released: list[str] = []
        backend._on_release_callback = lambda: released.append("release")
        backend._handle_line("KEY_UP:F8")
        assert released == ["release"]

    def test_wrong_key_does_not_fire(self, windows_env):
        """``KEY_DOWN:F2`` must NOT fire for an ``<f8>`` hotkey."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("KEY_DOWN:F2")
        assert fired == []

    def test_combo_requires_all_modifiers(self, windows_env):
        """``<ctrl>+<alt>+v`` fires only when Ctrl+Alt are held AND V is pressed."""
        backend = windows_env.WindowsHookHotkey("<ctrl>+<alt>+v")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")

        # V alone, no fire.
        backend._handle_line("KEY_DOWN:V")
        assert fired == []
        backend._handle_line("KEY_UP:V")

        # Hold Ctrl+Alt, then press V, fire.
        backend._handle_line("MOD_DOWN:Ctrl")
        backend._handle_line("MOD_DOWN:Alt")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]


class TestKeySuppression:
    """Verify the C source implements key suppression."""

    def test_windows_key_listener_c_source_exists(self):
        assert WINDOWS_KEY_LISTENER_C.is_file(), f"Missing C source: {WINDOWS_KEY_LISTENER_C}"

    def test_c_source_uses_wh_keyboard_ll_hook(self):
        """The C source must install a ``WH_KEYBOARD_LL`` low-level hook."""
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "WH_KEYBOARD_LL" in src, (
            "windows-key-listener.c must use WH_KEYBOARD_LL (the only "
            "Win32 hook that supports out-of-process key suppression)"
        )
        # SetWindowsHookEx installs the hook.
        assert "SetWindowsHookEx" in src, "windows-key-listener.c must call SetWindowsHookEx to install the hook"

    def test_c_source_has_should_suppress_keydown_function(self):
        """The C source has a ``should_suppress_keydown`` decision function."""
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "should_suppress_keydown" in src, (
            "windows-key-listener.c must define should_suppress_keydown(), "
            "the decision function that returns 1 to swallow a keystroke"
        )

    def test_c_source_swallows_matched_key_with_return_1(self):
        """The hook proc returns 1 (non-zero) to suppress matched keystrokes."""
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        # The hook proc checks should_suppress_keydown and returns 1.
        assert "should_suppress_keydown" in src
        assert "return 1" in src, (
            "The WH_KEYBOARD_LL hook proc must `return 1` (non-zero) to "
            "suppress a matched keystroke, calling CallNextHookEx would "
            "pass it through to the foreground app"
        )

    def test_c_source_caps_lock_suppression(self):
        """Caps Lock hotkey suppresses the OS caps-state toggle."""
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "VK_CAPITAL" in src or "VK_CAPITAL" in src, (
            "windows-key-listener.c must reference VK_CAPITAL for Caps Lock suppression"
        )
        assert "caps_lock" in src or "CapsLock" in src, (
            "windows-key-listener.c must handle the caps_lock / CapsLock hotkey"
        )


class TestModifierOnlyHotkeys:
    """Verify the backend supports modifier-only hotkeys (e.g. ``<alt>``, ``<caps_lock>``)."""

    def test_modifier_only_alt_fires_on_mod_down(self, windows_env):
        """``<alt>`` fires on ``MOD_DOWN:Alt`` (no main key needed)."""
        backend = windows_env.WindowsHookHotkey("<alt>")
        assert backend._parsed is not None
        assert backend._parsed["is_modifier_only"] is True

        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("MOD_DOWN:Alt")
        assert fired == ["press"]

    def test_modifier_only_alt_does_not_fire_with_extra_modifiers(self, windows_env):
        """``<alt>`` must NOT fire if Ctrl is also held (extra modifier)."""
        backend = windows_env.WindowsHookHotkey("<alt>")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")

        backend._handle_line("MOD_DOWN:Ctrl")  # extra modifier
        backend._handle_line("MOD_DOWN:Alt")
        assert fired == []  # NOT fired, Ctrl is held too

    def test_caps_lock_hotkey_fires_on_key_down(self, windows_env):
        """``<caps_lock>`` is a single-key hotkey that fires on ``KEY_DOWN:CapsLock``."""
        backend = windows_env.WindowsHookHotkey("<caps_lock>")
        assert backend._parsed is not None
        assert backend._parsed["main_key"] == "CapsLock"
        assert backend._parsed["is_caps_lock"] is True

        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("KEY_DOWN:CapsLock")
        assert fired == ["press"]

    def test_win_modifier_canonicalizes_to_cmd(self, windows_env):
        """``<win>`` (Windows key) is accepted as a modifier-only hotkey."""
        backend = windows_env.WindowsHookHotkey("<win>")
        assert backend._parsed is not None
        assert "cmd" in backend._parsed["modifiers"]
        assert backend._parsed["is_modifier_only"] is True

    def test_c_source_supports_modifier_only_specs(self):
        """The C source parses modifier-only specs (empty main key)."""
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "is_modifier_only" in src, (
            "windows-key-listener.c must support modifier-only hotkeys (is_modifier_only field in HotkeySpec struct)"
        )


class TestSidecarOwnership:
    """Verify the Python sidecar (not the Tauri host) owns the native listener."""

    def test_native_hotkeys_module_lives_in_python_sidecar(self):
        """``native_hotkeys.py`` lives under ``voice_typer/server/`` (the sidecar package)."""
        assert NATIVE_HOTKEYS_PY.is_file(), f"native_hotkeys.py must exist in the Python sidecar: {NATIVE_HOTKEYS_PY}"
        # Confirm it's under voice_typer/server/ (the sidecar package).
        assert "voice_typer" in NATIVE_HOTKEYS_PY.parts
        assert "server" in NATIVE_HOTKEYS_PY.parts

    def test_native_hotkeys_module_defines_subprocess_backend(self):
        """``native_hotkeys`` defines ``SubprocessHotkeyBackend`` + ``WindowsHookHotkey``."""
        base_src = NATIVE_HOTKEYS_BASE_PY.read_text(encoding="utf-8")
        core_src = NATIVE_HOTKEYS_CORE_PY.read_text(encoding="utf-8")
        win_src = NATIVE_HOTKEYS_WINDOWS_PY.read_text(encoding="utf-8")
        assert "SubprocessHotkeyBackend" in base_src, (
            "native_hotkeys/base.py must re-export SubprocessHotkeyBackend (the base class "
            "that spawns the native binary via subprocess.Popen)"
        )
        assert "class SubprocessHotkeyBackend" in core_src, (
            "native_hotkeys/_core.py must define SubprocessHotkeyBackend (the base class "
            "that spawns the native binary via subprocess.Popen)"
        )
        assert "class WindowsHookHotkey" in win_src, (
            "native_hotkeys/windows_backend.py must define WindowsHookHotkey (the Windows subclass)"
        )
        assert "subprocess.Popen" in core_src, "native_hotkeys/_core.py must use subprocess.Popen to spawn the binary"

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

    def test_tauri_does_not_spawn_native_listener_directly(self):
        """Tauri's ``externalBin`` must NOT list the native listener."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        external_bins = conf.get("bundle", {}).get("externalBin", [])
        for ext in external_bins:
            assert "key-listener" not in ext, (
                f"Native key-listener must NOT be in externalBin (Tauri must not spawn it). Found: {ext}"
            )

    def test_runbook_documents_sidecar_ownership(self):
        """Runbook §6.8 documents the native listener spawns from the sidecar."""
        assert RUNBOOK.is_file()
        src = RUNBOOK.read_text(encoding="utf-8")
        assert "windows-key-listener" in src, "Runbook §6.8 must document the windows-key-listener gate"
        # The runbook references native_hotkeys.py (the sidecar module).
        assert "native_hotkeys" in src, (
            "Runbook must reference native_hotkeys.py (the sidecar module that owns the listener)"
        )


class TestCompileNativeScript:
    """Verify ``compile_native.ps1`` exists and compiles the C binary."""

    def test_compile_native_ps1_exists(self):
        """The PowerShell build script must exist."""
        assert COMPILE_NATIVE_PS1.is_file(), f"Missing compile_native.ps1: {COMPILE_NATIVE_PS1}"

    def test_compile_native_ps1_compiles_c_source(self):
        """The script compiles ``windows-key-listener.c`` → ``windows-key-listener.exe``."""
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "windows-key-listener.c" in src, "compile_native.ps1 must reference the C source file"
        assert "windows-key-listener.exe" in src, "compile_native.ps1 must produce windows-key-listener.exe"

    def test_compile_native_ps1_supports_msvc_and_mingw(self):
        """The script supports both MSVC (``cl.exe``) and MinGW (``gcc``)."""
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "cl.exe" in src or "cl" in src, "compile_native.ps1 must support MSVC (cl.exe)"
        assert "gcc" in src or "gcc.exe" in src, "compile_native.ps1 must support MinGW (gcc) as a fallback"

    def test_compile_native_ps1_links_user32(self):
        """The script links ``user32.lib`` (required for ``WH_KEYBOARD_LL``)."""
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "user32" in src.lower(), "compile_native.ps1 must link user32.lib (required for WH_KEYBOARD_LL APIs)"

    def test_compile_native_ps1_has_check_mode(self):
        """The script supports a ``-Check`` mode for toolchain verification."""
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "Check" in src, "compile_native.ps1 must support a -Check mode for toolchain verification"

    def test_compile_native_ps1_sets_win32_winnt_vista(self):
        """The script defines ``_WIN32_WINNT=0x0600`` (Vista+)."""
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "_WIN32_WINNT" in src, "compile_native.ps1 must define _WIN32_WINNT (WH_KEYBOARD_LL requires Vista+)"
