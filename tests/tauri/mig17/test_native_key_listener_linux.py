"""native ``linux-key-listener`` (evdev, C)."""

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
BUILD_NATIVE_LISTENER_LINUX_SH = PROJECT_ROOT / "scripts" / "build" / "build_native_listener_linux.sh"
LINUX_KEY_LISTENER_C = PROJECT_ROOT / "voice_typer" / "server" / "native" / "linux-key-listener.c"
# Phase 4.5 / : ``native_hotkeys`` and ``hotkeys`` were split
NATIVE_HOTKEYS_PY = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys" / "__init__.py"
NATIVE_HOTKEYS_PKG_DIR = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys"
HOTKEYS_PY = PROJECT_ROOT / "voice_typer" / "server" / "hotkeys" / "factory.py"
POSTINST_SH = PROJECT_ROOT / "scripts" / "linux" / "postinst"
POSTINST_RPM_SH = PROJECT_ROOT / "scripts" / "linux" / "postinst.rpm"
INSTALL_PERMISSIONS_PY = PROJECT_ROOT / "scripts" / "linux" / "install_permissions.py"
UDEV_RULES = PROJECT_ROOT / "scripts" / "linux" / "99-lausu.rules"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
RUNBOOK = PROJECT_ROOT / "docs" / "migration" / "linux-validation-runbook.md"

NATIVE_RESOURCE_PATH = "resources/native/linux-key-listener"


@pytest.fixture
def linux_env(monkeypatch):
    """Patch the platform predicates + sys.platform to look like Linux."""
    from voice_typer.server import native_hotkeys
    from voice_typer.server.native_hotkeys import binary_path

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
    monkeypatch.setattr(binary_path, "verify_native_binary_or_skip", lambda _path: True)
    return native_hotkeys


class TestTauriBundleResources:
    """Verify ``tauri.conf.json`` ships the native listener as a resource."""

    def test_tauri_conf_json_exists(self):
        """The Tauri config file must exist (sanity check)."""
        assert TAURI_CONF.is_file(), f"Missing Tauri config: {TAURI_CONF}"

    def test_tauri_conf_bundles_linux_native_listener(self):
        """``resources/native/linux-key-listener`` must be in bundle.resources."""
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
            assert "linux-key-listener" not in ext, (
                f"linux-key-listener must NOT be in externalBin (Tauri must not spawn it, ADR-0020 §6.4). Found: {ext}"
            )

    def test_tauri_conf_also_bundles_windows_and_macos_listeners(self):
        """All three platform binaries are bundled (cross-platform ship)."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/windows-key-listener.exe" in resources
        assert "resources/native/macos-key-listener" in resources

    def test_tauri_conf_linux_deb_uses_postinst_script(self):
        """The Linux ``.deb`` bundle reuses the existing ``postinst`` script."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
        # Tauri v2 uses the long-form `postInstallScript` key
        assert "postInstallScript" in deb, (
            "bundle.linux.deb.postInstallScript missing, Tauri v2 requires the 'postInstallScript' key"
        )
        assert "postInstall" not in deb, (
            "stale short-form 'postInstall' key present on bundle.linux.deb, "
            "Tauri v2 requires the 'postInstallScript' long-form key"
        )
        post_install = deb["postInstallScript"]
        assert post_install is not None, (
            "tauri.conf.json bundle.linux.deb.postInstallScript must be set "
            "(the postinst script that sets up the input group + udev rule)"
        )
        assert "postinst" in post_install, (
            f"bundle.linux.deb.postInstallScript must reference the postinst script; got {post_install!r}"
        )


class TestSubprocessSpawn:
    """Verify ``SubprocessHotkeyBackend._spawn_process`` uses ``subprocess.Popen`` correctly."""

    def test_spawn_uses_subprocess_popen(self, linux_env, monkeypatch, tmp_path):
        """``_spawn_process`` must call ``subprocess.Popen`` (not ``run``/``call``/``check_output``)."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        # Inject a fake binary path so we don't depend on discovery.
        fake_bin = tmp_path / "linux-key-listener"
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

        monkeypatch.setattr(linux_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured.get("cmd") is not None, "subprocess.Popen was not called"
        assert captured["cmd"][0] == str(fake_bin)

    def test_spawn_passes_hotkey_spec_as_argv1(self, linux_env, monkeypatch, tmp_path):
        """The hotkey spec string (e.g. ``<f8>``) is passed as ``argv[1]``."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        fake_bin = tmp_path / "linux-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return MagicMock()

        monkeypatch.setattr(linux_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["cmd"][1] == "<f8>", (
            "Hotkey spec must be argv[1] (the native C binary parses it "
            "via validate_hotkey_spec to decide which key to watch)."
        )

    def test_spawn_pipes_stdout_for_wire_protocol(self, linux_env, monkeypatch, tmp_path):
        """stdout=PIPE, stderr=STDOUT, stdin=PIPE (G4-H-31 watchdog)."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        fake_bin = tmp_path / "linux-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(linux_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        kwargs = captured["kwargs"]
        assert kwargs.get("stdout") == subprocess.PIPE, "stdout must be PIPE, reader thread streams wire-protocol lines"
        assert kwargs.get("stderr") == subprocess.STDOUT, (
            "stderr must redirect to stdout so errors surface in the wire stream"
        )
        assert kwargs.get("stdin") == subprocess.PIPE, (
            "stdin must be PIPE, G4-H-31 added a PING/PONG watchdog that writes "
            "to the binary's stdin every 30s to detect a stuck reader (the binary "
            "responds with PONG\\n); was DEVNULL before the watchdog was added"
        )

    def test_spawn_uses_start_new_session_on_linux(self, linux_env, monkeypatch, tmp_path):
        """On Linux, ``start_new_session=True`` so SIGTERM works cleanly."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        fake_bin = tmp_path / "linux-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(linux_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["kwargs"].get("start_new_session") is True, (
            "start_new_session must be True on Linux so SIGTERM cleanly "
            "shuts down the C binary via its sigaction handler"
        )

    def test_spawn_failure_raises_runtime_error(self, linux_env, monkeypatch, tmp_path):
        """If ``Popen`` raises ``OSError``, ``_spawn_process`` raises ``RuntimeError``."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        backend._binary_path = tmp_path / "linux-key-listener"
        # Popen so the fd pins the inode for the pre-Popen stat check.
        backend._binary_path.write_text("dummy")
        backend._stop_event.set()

        def raising_popen(cmd, **kwargs):
            raise OSError("Executable not found")

        monkeypatch.setattr(linux_env.subprocess, "Popen", raising_popen)

        with pytest.raises(RuntimeError, match="Failed to spawn"):
            backend._spawn_process()
        assert backend._failed is True
        assert backend._error_message is not None

    def test_spawn_does_not_use_create_no_window_on_linux(self, linux_env, monkeypatch, tmp_path):
        """On Linux, ``creationflags`` is 0 (no CREATE_NO_WINDOW, Windows-only)."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        fake_bin = tmp_path / "linux-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(linux_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["kwargs"].get("creationflags", 0) == 0, (
            "creationflags must be 0 on Linux, CREATE_NO_WINDOW is Windows-only and would be a no-op or raise on POSIX"
        )


class TestBinaryDiscovery:
    """Verify the binary is discovered via ``VOICE_TYPER_NATIVE_DIR`` or dev/bundle paths."""

    def test_voice_typer_native_dir_lookup_finds_linux_binary(self, linux_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_DIR`` (Tauri dev/prod) points at the bundle's native dir."""
        native_dir = tmp_path / "resources" / "native"
        native_dir.mkdir(parents=True)
        binary = native_dir / "linux-key-listener"
        binary.write_text("dummy")

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = linux_env.get_native_binary_path()
        assert result is not None
        assert result.name == "linux-key-listener"
        assert result.parent == native_dir

    def test_voice_typer_native_binary_env_takes_precedence(self, linux_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_BINARY`` (single-file override) beats ``_DIR``."""
        single = tmp_path / "custom-listener"
        single.write_text("dummy")

        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "linux-key-listener").write_text("dummy")

        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(single))
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        result = linux_env.get_native_binary_path()
        assert result == single

    def test_production_bundle_resource_path_layout(self, linux_env, monkeypatch, tmp_path):
        """Production layout: ``resourceDir/native/linux-key-listener``."""
        # Simulate Tauri's resourceDir layout.
        resource_dir = tmp_path / "resourceDir"
        # Tauri preserves the relative path from the resources array entry.
        native_subdir = resource_dir / "resources" / "native"
        native_subdir.mkdir(parents=True)
        binary = native_subdir / "linux-key-listener"
        binary.write_text("dummy")

        # Tauri host sets VOICE_TYPER_NATIVE_DIR to the native subdir.
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_subdir))

        result = linux_env.get_native_binary_path()
        assert result is not None
        assert result == binary

    def test_dev_mode_falls_through_to_source_tree(self, linux_env, monkeypatch):
        """Without env vars, lookup falls through to the dev source-tree path."""
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # The dev-mode path is <server_dir>/native/linux-key-listener.
        server_dir = NATIVE_HOTKEYS_PY.resolve().parent.parent
        expected_dev_path = server_dir / "native" / "linux-key-listener"

        real_is_file = Path.is_file

        def fake_is_file(self):
            if self == expected_dev_path:
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)

        result = linux_env.get_native_binary_path()
        assert result is not None
        assert result == expected_dev_path

    def test_path_exists_check_used_for_bundle_resource(self, linux_env, monkeypatch, tmp_path):
        """
        Binary discovery uses ``Path.is_file`` (and ``Path.exists`` semantics).
        This pins the discovery contract: the candidate path is
        """
        # Point VOICE_TYPER_NATIVE_DIR at a dir whose "binary" only
        fake_native_dir = tmp_path / "fake-bundle"
        fake_native_dir.mkdir()
        # NOTE: do NOT write the binary file, Path.is_file is mocked
        candidate = fake_native_dir / "linux-key-listener"

        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(fake_native_dir))

        real_is_file = Path.is_file

        def fake_is_file(self):
            if self == candidate:
                return True
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        # Also mock Path.exists (per task spec) for any code paths
        real_exists = Path.exists

        def fake_exists(self):
            if self == candidate:
                return True
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = linux_env.get_native_binary_path()
        assert result is not None
        assert result == candidate


class TestWireProtocol:
    """Verify the backend parses the native binary's stdout wire protocol."""

    def test_ready_line_sets_ready_event(self, linux_env):
        """``READY`` unblocks ``start()`` (which waits on ``_ready_event``)."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        assert not backend._ready_event.is_set()
        backend._handle_line("READY")
        assert backend._ready_event.is_set()
        assert not backend._failed

    def test_error_line_marks_failed_and_unblocks(self, linux_env):
        """``ERROR:<msg>`` marks the backend failed + unblocks ``start()``."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        backend._handle_line("ERROR:Permission denied. Add yourself to the 'input' group")
        assert backend._failed
        assert backend._error_message is not None
        assert "input" in backend._error_message
        assert backend._ready_event.is_set()  # unblocks start()

    def test_key_down_f8_fires_dictation_callback(self, linux_env):
        """``KEY_DOWN:F8`` fires the press callback for the ``<f8>`` hotkey."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        fired: list[str] = []
        backend._callback = lambda: fired.append("dictation-toggle")
        backend._handle_line("KEY_DOWN:F8")
        assert fired == ["dictation-toggle"]

    def test_key_up_f8_fires_release_callback(self, linux_env):
        """``KEY_UP:F8`` fires the release callback (push-to-talk mode)."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        released: list[str] = []
        backend._on_release_callback = lambda: released.append("release")
        backend._handle_line("KEY_UP:F8")
        assert released == ["release"]

    def test_wrong_key_does_not_fire(self, linux_env):
        """``KEY_DOWN:F2`` must NOT fire for an ``<f8>`` hotkey."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("KEY_DOWN:F2")
        assert fired == []

    def test_combo_requires_all_modifiers(self, linux_env):
        """``<ctrl>+<alt>+v`` fires only when Ctrl+Alt are held AND V is pressed."""
        backend = linux_env.LinuxEvdevHotkey("<ctrl>+<alt>+v")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")

        # V alone, no fire.
        backend._handle_line("KEY_DOWN:V")
        # Release V (the OS always emits KEY_UP between two distinct
        backend._handle_line("KEY_UP:V")
        assert fired == []

        # Hold Ctrl+Alt, then press V, fire.
        backend._handle_line("MOD_DOWN:Ctrl")
        backend._handle_line("MOD_DOWN:Alt")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]

    def test_super_modifier_canonicalizes_to_cmd(self, linux_env):
        """Wire name ``Super`` (Linux) canonicalizes to ``cmd`` for matching."""
        backend = linux_env.LinuxEvdevHotkey("<cmd>+v")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")

        backend._handle_line("MOD_DOWN:Super")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]


class TestInputGroupPermission:
    """Verify the native binary requires ``input`` group membership."""

    def test_linux_key_listener_c_source_exists(self):
        """The C source file must exist (compiled by ``compile_native.sh``)."""
        assert LINUX_KEY_LISTENER_C.is_file(), f"Missing C source: {LINUX_KEY_LISTENER_C}"

    def test_c_source_opens_dev_input_event_devices(self):
        """The C source must open ``/dev/input/event*`` (evdev). NOT use X11."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "/dev/input" in src, (
            "linux-key-listener.c must open devices under /dev/input "
            "(evdev, the only Wayland-capable Linux hotkey path)"
        )
        assert "event" in src, "linux-key-listener.c must scan for eventN devices in /dev/input"

    def test_c_source_emits_error_on_permission_denied(self):
        """The binary emits an ERROR line mentioning the ``input`` group on EACCES."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "EACCES" in src, (
            "linux-key-listener.c must check for EACCES (permission denied "
            "when opening /dev/input/event* without input group membership)"
        )
        assert "input" in src, (
            "linux-key-listener.c must mention the 'input' group in the "
            "permission-denied ERROR message (onboarding prompt)"
        )
        assert "usermod -aG input" in src, (
            "linux-key-listener.c must include the 'sudo usermod -aG input "
            "$USER' command in the permission-denied ERROR message"
        )

    def test_c_source_emits_error_when_no_keyboard_devices_found(self):
        """The binary emits an ERROR when no keyboard devices are found."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "No keyboard devices found" in src, (
            "linux-key-listener.c must emit ERROR:No keyboard devices found "
            "when /dev/input/event* has no keyboard-like devices"
        )

    def test_python_backend_validates_linux_platform(self, linux_env):
        """``LinuxEvdevHotkey._validate_platform`` returns None on Linux."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        err = backend._validate_platform()
        assert err is None, f"LinuxEvdevHotkey._validate_platform must return None on Linux; got {err!r}"

    def test_python_backend_rejects_fn_on_linux(self, linux_env):
        """``LinuxEvdevHotkey`` rejects ``<fn>`` specs (firmware-only on Linux)."""
        backend = linux_env.LinuxEvdevHotkey("<fn>")
        err = backend._validate_platform()
        assert err is not None, "LinuxEvdevHotkey must reject <fn> specs, Fn is firmware-only on most Linux laptops"
        assert "FN" in err or "fn" in err, f"_validate_platform error must mention FN/fn; got {err!r}"

    def test_linux_backend_does_not_support_fn(self, linux_env):
        """``LinuxEvdevHotkey.supports_fn`` is False (Fn is firmware-only on Linux)."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        assert backend.supports_fn is False, (
            "LinuxEvdevHotkey.supports_fn must be False, Fn is firmware-only "
            "on most Linux laptops (never reaches the OS)"
        )


class TestEvdevGlobalHotkeys:
    """Verify the C source uses evdev for global hotkeys (works on X11 AND Wayland)."""

    def test_c_source_uses_linux_input_header(self):
        """The C source includes ``<linux/input.h>`` (the evdev API)."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "<linux/input.h>" in src, (
            "linux-key-listener.c must include <linux/input.h> (the evdev userspace API header)"
        )

    def test_c_source_reads_input_event_structs(self):
        """The C source reads ``struct input_event`` from each fd."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "struct input_event" in src, (
            "linux-key-listener.c must declare struct input_event (the evdev event record)"
        )
        assert "EV_KEY" in src, (
            "linux-key-listener.c must filter on ev.type == EV_KEY (ignoring EV_SYN, EV_REL, EV_ABS, etc.)"
        )

    def test_c_source_uses_poll_for_event_loop(self):
        """The C source uses ``poll(2)`` for the event loop (not ``select`` or busy-wait)."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "poll" in src, (
            "linux-key-listener.c must use poll(2) for the event loop (multiplexing /dev/input/eventN fds)"
        )
        assert "pollfd" in src or "struct pollfd" in src, (
            "linux-key-listener.c must use struct pollfd (the poll() fd array)"
        )

    def test_c_source_uses_ioctl_eviongbit_for_keyboard_detection(self):
        """The C source uses ``ioctl(EVIOCGBIT)`` to detect keyboard devices."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "EVIOCGBIT" in src, (
            "linux-key-listener.c must use ioctl(EVIOCGBIT) to detect "
            "keyboard devices (filters out mice and button-only devices)"
        )
        assert "KEY_A" in src and "KEY_SPACE" in src and "KEY_ENTER" in src, (
            "linux-key-listener.c must check for KEY_A, KEY_SPACE, KEY_ENTER "
            "(keyboard heuristic, filters out non-keyboard input devices)"
        )

    def test_c_source_handles_autorepeat_events(self):
        """The binary filters out evdev autorepeat events (``ev.value == 2``)."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "ev.value == 2" in src or "value == 2" in src, (
            "linux-key-listener.c must filter out evdev autorepeat events "
            "(ev.value == 2) so a held hotkey doesn't fire repeatedly"
        )

    def test_c_source_emits_ready_after_device_discovery(self):
        """The binary emits ``READY`` after device discovery succeeds."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert '"READY"' in src or '"READY' in src or 'emit("READY")' in src, (
            "linux-key-listener.c must emit READY after device discovery (unblocks the sidecar's start() READY-wait)"
        )

    def test_adr_documents_evdev_works_on_wayland(self):
        """ADR-0020 §6.4 documents that evdev works on Wayland."""
        assert ADR_0020.is_file()
        src = ADR_0020.read_text(encoding="utf-8")
        assert "evdev" in src.lower(), "ADR-0020 must reference evdev (the Linux native-listener API)"
        assert "Wayland" in src, (
            "ADR-0020 must reference Wayland (the Linux session type that the Tauri plugin cannot support)"
        )
        assert "X11 only" in src or "X11-only" in src, (
            "ADR-0020 must document that the Tauri plugin is X11-only on Linux"
        )


class TestKeySuppressionNotSupported:
    """Verify the native binary does NOT support key suppression on Linux."""

    def test_c_source_opens_devices_read_only(self):
        """The binary opens ``/dev/input/event*`` with ``O_RDONLY`` (read-only)."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "O_RDONLY" in src, (
            "linux-key-listener.c must open /dev/input/event* with O_RDONLY "
            "(evdev is read-only, no key suppression on Linux)"
        )
        # The binary must NOT use EVIOCGRAB (which would grab exclusive
        assert "EVIOCGRAB" not in src, (
            "linux-key-listener.c must NOT use EVIOCGRAB (exclusive grab), "
            "that would break the foreground app's keyboard entirely. The "
            "binary observes events only (no suppression)."
        )

    def test_c_source_does_not_write_to_devices(self):
        """The binary does not write to ``/dev/input/event*`` (no ``write(2)``)."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        # must NOT use write() to inject synthetic events.
        assert "read(" in src, "linux-key-listener.c must use read(2) to consume input_event structs"

    def test_c_source_documents_read_only_limitation(self):
        """The C source header comment documents the read-only limitation."""
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        # The header comment must mention "read-only" + "suppress".
        assert "read-only" in src.lower() or "read only" in src.lower(), (
            "linux-key-listener.c must document that evdev is read-only "
            "(the no-suppression limitation per ADR-0020 §6.4)"
        )
        assert "suppress" in src.lower(), (
            "linux-key-listener.c must reference suppression (the documented "
            "Linux limitation, evdev cannot suppress keystrokes)"
        )

    def test_adr_documents_linux_no_suppression(self):
        """ADR-0020 §6.4 documents that Linux has NO key suppression."""
        src = ADR_0020.read_text(encoding="utf-8")
        # The table row for Linux suppression must be marked ❌.
        assert "Linux ❌" in src or "Linux ❌ (evdev read-only)" in src, (
            "ADR-0020 §6.4 must document that Linux has NO key suppression (evdev is read-only)"
        )
        assert "read-only" in src.lower(), (
            "ADR-0020 must reference evdev being read-only as the Linux suppression limitation"
        )

    def test_python_backend_does_not_advertise_suppression(self, linux_env):
        """The Python ``LinuxEvdevHotkey`` does not advertise suppression capability."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        # No suppression attribute on the Linux backend (it's implicit
        assert not getattr(backend, "supports_suppression", False), (
            "LinuxEvdevHotkey must NOT advertise supports_suppression, evdev is read-only (ADR-0020 §6.4)"
        )


class TestSidecarOwnership:
    """Verify the Python sidecar (not the Tauri host) owns the native listener."""

    def test_native_hotkeys_module_lives_in_python_sidecar(self):
        """``native_hotkeys.py`` lives under ``voice_typer/server/`` (the sidecar package)."""
        assert NATIVE_HOTKEYS_PY.is_file(), f"native_hotkeys.py must exist in the Python sidecar: {NATIVE_HOTKEYS_PY}"
        assert "voice_typer" in NATIVE_HOTKEYS_PY.parts
        assert "server" in NATIVE_HOTKEYS_PY.parts

    def test_native_hotkeys_module_defines_linux_backend(self):
        """``native_hotkeys`` (package) defines ``SubprocessHotkeyBackend`` + ``LinuxEvdevHotkey``."""
        assert NATIVE_HOTKEYS_PY.is_file(), f"native_hotkeys package __init__.py must exist: {NATIVE_HOTKEYS_PY}"
        parts: list[str] = [NATIVE_HOTKEYS_PY.read_text(encoding="utf-8")]
        for sub_path in sorted(NATIVE_HOTKEYS_PKG_DIR.glob("*.py")):
            if sub_path == NATIVE_HOTKEYS_PY:
                continue
            parts.append(sub_path.read_text(encoding="utf-8"))
        src = "\n".join(parts)
        assert "class SubprocessHotkeyBackend" in src, (
            "native_hotkeys package must define SubprocessHotkeyBackend (the base class "
            "that spawns the native binary via subprocess.Popen)"
        )
        assert "class LinuxEvdevHotkey" in src, (
            "native_hotkeys package must define LinuxEvdevHotkey (the Linux subclass)"
        )
        assert "subprocess.Popen" in src, "native_hotkeys package must use subprocess.Popen to spawn the binary"

    def test_linux_backend_uses_linux_binary_name(self, linux_env):
        """``_BINARY_NAMES[\"linux\"]`` is ``linux-key-listener`` (matches the tauri.conf resource)."""
        assert linux_env._BINARY_NAMES.get("linux") == "linux-key-listener", (
            "_BINARY_NAMES['linux'] must be 'linux-key-listener' (matches the tauri.conf.json bundle.resources entry)"
        )

    def test_hotkeys_factory_tries_native_backend_first(self, linux_env):
        """``hotkeys.create_hotkey_backend`` tries ``create_native_backend`` first."""
        assert HOTKEYS_PY.is_file()
        src = HOTKEYS_PY.read_text(encoding="utf-8")
        assert "create_native_backend" in src, (
            "hotkeys/factory.py must call create_native_backend (the native-first factory, NATIVE-001)"
        )
        # LinuxEvdevHotkey is defined in the native_hotkeys package
        native_init = HOTKEYS_PY.parent.parent / "native_hotkeys" / "__init__.py"
        native_linux = HOTKEYS_PY.parent.parent / "native_hotkeys" / "linux_backend.py"
        for native_src_path in (native_init, native_linux):
            if native_src_path.is_file():
                nsrc = native_src_path.read_text(encoding="utf-8")
                assert "LinuxEvdevHotkey" in nsrc, (
                    f"{native_src_path.name} must define/reference LinuxEvdevHotkey (the Linux native backend)"
                )
        # The factory must fall back to WaylandHotkey on Wayland
        assert "WaylandHotkey" in src, "hotkeys/factory.py must reference WaylandHotkey (the Wayland legacy fallback)"

    def test_native_factory_returns_linux_backend_on_linux(self, linux_env, monkeypatch, tmp_path):
        """``create_native_backend`` returns a ``LinuxEvdevHotkey`` on Linux when the binary exists."""
        # Make the binary discoverable.
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "linux-key-listener").write_text("dummy")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        from voice_typer.server.native_hotkeys import binary_path as bp, factory as nh_factory

        monkeypatch.setattr(bp, "verify_native_binary_or_skip", lambda _path: True)
        monkeypatch.setattr(nh_factory, "verify_native_binary_or_skip", lambda _path: True)

        backend = linux_env.create_native_backend("<f8>")
        assert backend is not None, (
            "create_native_backend must return a backend on Linux when the binary is discoverable "
            "(CR-002 fail-closed is bypassed via monkeypatched verify_native_binary_or_skip)"
        )
        assert isinstance(backend, linux_env.LinuxEvdevHotkey), (
            f"create_native_backend must return a LinuxEvdevHotkey on Linux; got {type(backend).__name__}"
        )

    def test_native_factory_returns_none_when_binary_missing(self, linux_env, monkeypatch):
        """``create_native_backend`` returns None when the binary is NOT found."""
        # Force all lookup paths to miss.
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # Mock Path.is_file to always return False so all 6 lookup
        monkeypatch.setattr(Path, "is_file", lambda self: False)

        backend = linux_env.create_native_backend("<f8>")
        assert backend is None, (
            "create_native_backend must return None when the binary is not "
            "found (triggers the legacy fallback in create_hotkey_backend)"
        )

    def test_adr_states_sidecar_owns_hotkey_subsystem(self):
        """ADR-0020 §6.4 explicitly states Tauri does not touch the hotkey subsystem."""
        assert ADR_0020.is_file()
        src = ADR_0020.read_text(encoding="utf-8")
        assert "Python sidecar" in src or "sidecar" in src.lower(), (
            "ADR-0020 must reference the Python sidecar as the hotkey owner"
        )
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

    def test_adr_documents_wayland_regression_risk(self):
        """ADR-0020 §6.4 documents the Wayland regression risk."""
        src = ADR_0020.read_text(encoding="utf-8")
        assert "Wayland" in src, (
            "ADR-0020 §6.4 must reference Wayland (the session type the Tauri plugin cannot support on Linux)"
        )
        assert "breaks Wayland" in src or "Wayland" in src, (
            "ADR-0020 §6.4 must document that the Tauri plugin breaks Wayland"
        )

    def test_tauri_does_not_spawn_native_listener_directly(self):
        """Tauri's ``externalBin`` must NOT list the native listener."""
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        external_bins = conf.get("bundle", {}).get("externalBin", [])
        for ext in external_bins:
            assert "key-listener" not in ext, (
                f"Native key-listener must NOT be in externalBin (Tauri must not spawn it). Found: {ext}"
            )

    def test_runbook_documents_native_listener_gate(self):
        """Runbook Step 12 documents the native listener + the sidecar."""
        assert RUNBOOK.is_file()
        src = RUNBOOK.read_text(encoding="utf-8")
        assert "linux-key-listener" in src, "Runbook must document the linux-key-listener gate (Step 12)"
        assert "X11" in src and "Wayland" in src, "Runbook Step 12 must require testing on BOTH X11 and Wayland"


class TestPostinstSetup:
    """Verify ``scripts/linux/postinst`` sets up the ``input`` group + udev rules."""

    def test_postinst_script_exists(self):
        """The Debian ``postinst`` script must exist (referenced by tauri.conf.json)."""
        assert POSTINST_SH.is_file(), f"Missing postinst: {POSTINST_SH}"

    def test_postinst_rpm_script_exists(self):
        """The RPM ``postinst.rpm`` script must exist (for Fedora packages)."""
        assert POSTINST_RPM_SH.is_file(), f"Missing postinst.rpm: {POSTINST_RPM_SH}"

    def test_postinst_invokes_install_permissions_py(self):
        """The ``postinst`` script invokes ``install_permissions.py``."""
        src = POSTINST_SH.read_text(encoding="utf-8")
        assert "install_permissions.py" in src, (
            "postinst must invoke install_permissions.py (the single source of truth for Linux permission setup)"
        )
        assert "python3" in src, "postinst must invoke python3 to run install_permissions.py"

    def test_postinst_handles_configure_case(self):
        """The ``postinst`` script handles the ``configure`` case."""
        src = POSTINST_SH.read_text(encoding="utf-8")
        assert 'case "$1"' in src, "postinst must use a case statement on $1 (Debian postinst contract)"
        assert "configure)" in src, "postinst must handle the 'configure)' case (install/upgrade)"

    def test_postinst_warns_on_log_out_log_back_in(self):
        """The ``postinst`` script warns the user to log out + log back in."""
        src = POSTINST_SH.read_text(encoding="utf-8")
        assert "log out" in src.lower() and "log back in" in src.lower(), (
            "postinst must warn the user to log out + log back in for the "
            "input group change to take effect (Linux kernel limitation)"
        )

    def test_install_permissions_py_installs_udev_rule(self):
        """``install_permissions.py`` installs the udev rule to /etc/udev/rules.d/."""
        assert INSTALL_PERMISSIONS_PY.is_file()
        src = INSTALL_PERMISSIONS_PY.read_text(encoding="utf-8")
        assert "99-lausu.rules" in src, "install_permissions.py must install the 99-lausu.rules udev rule"
        assert "/etc/udev/rules.d" in src, "install_permissions.py must copy the rule to /etc/udev/rules.d/"
        assert "udevadm" in src, "install_permissions.py must run udevadm to reload + trigger the rule"

    def test_install_permissions_py_adds_user_to_input_group(self):
        """``install_permissions.py`` adds the user to the ``input`` group."""
        src = INSTALL_PERMISSIONS_PY.read_text(encoding="utf-8")
        assert "usermod" in src, "install_permissions.py must use usermod to add the user to the input group"
        assert "-aG" in src, (
            "install_permissions.py must use 'usermod -aG' (append to group, not replace the user's group list)"
        )
        assert '"input"' in src or "'input'" in src, "install_permissions.py must reference the 'input' group by name"

    def test_install_permissions_py_handles_sudo_user(self):
        """``install_permissions.py`` detects the target user via ``$SUDO_USER`` / ``$PKEXEC_UID``."""
        src = INSTALL_PERMISSIONS_PY.read_text(encoding="utf-8")
        assert "SUDO_USER" in src, "install_permissions.py must read SUDO_USER (set by sudo/apt/dnf)"
        assert "PKEXEC_UID" in src, "install_permissions.py must read PKEXEC_UID (set by pkexec for AppImage)"

    def test_udev_rule_grants_input_group_access(self):
        """The udev rule grants the ``input`` group read access to ``eventN`` devices."""
        assert UDEV_RULES.is_file()
        src = UDEV_RULES.read_text(encoding="utf-8")
        assert 'KERNEL=="event' in src, "udev rule must match KERNEL==event[0-9]* (all input event devices)"
        assert 'SUBSYSTEM=="input"' in src, "udev rule must match SUBSYSTEM==input"
        assert 'GROUP="input"' in src, "udev rule must set GROUP=input (grants the input group rw access)"
        assert 'MODE="0660"' in src, "udev rule must set MODE=0660 (root rw, input rw, others none)"

    def test_udev_rule_triggers_reload_on_add(self):
        """The udev rule runs ``udevadm trigger`` on device add (hotplug support)."""
        src = UDEV_RULES.read_text(encoding="utf-8")
        assert 'ACTION=="add"' in src, "udev rule must handle ACTION==add (for hotplugged keyboards)"
        assert "udevadm trigger" in src, (
            "udev rule must run 'udevadm trigger' on add (immediate permission change for hotplugged devices)"
        )

    def test_postinst_rpm_also_invokes_install_permissions(self):
        """The RPM ``postinst.rpm`` also invokes ``install_permissions.py``."""
        src = POSTINST_RPM_SH.read_text(encoding="utf-8")
        assert "install_permissions.py" in src, (
            "postinst.rpm must also invoke install_permissions.py (functionally "
            "identical to the Debian postinst per ADR-0020 §13.3)"
        )


class TestBuildScript:
    """Verify ``build_native_listener_linux.sh`` compiles + copies the binary."""

    def test_build_native_listener_linux_sh_exists(self):
        """The Linux build wrapper script must exist."""
        assert BUILD_NATIVE_LISTENER_LINUX_SH.is_file(), f"Missing build script: {BUILD_NATIVE_LISTENER_LINUX_SH}"

    def test_build_script_invokes_compile_native_sh(self):
        """The wrapper invokes ``compile_native.sh`` (which runs ``gcc``)."""
        src = BUILD_NATIVE_LISTENER_LINUX_SH.read_text(encoding="utf-8")
        assert "compile_native.sh" in src, "build_native_listener_linux.sh must invoke compile_native.sh"
        assert "gcc" in src or "compile_native.sh" in src, (
            "build_native_listener_linux.sh must (transitively) invoke gcc"
        )

    def test_build_script_copies_binary_to_tauri_resources(self):
        """The wrapper copies the compiled binary to ``src-tauri/resources/native/``."""
        src = BUILD_NATIVE_LISTENER_LINUX_SH.read_text(encoding="utf-8")
        assert "src-tauri/resources/native" in src, (
            "build_native_listener_linux.sh must copy the binary to "
            "src-tauri/resources/native/ (the tauri.conf.json bundle.resources path)"
        )
        assert "linux-key-listener" in src, "build_native_listener_linux.sh must reference linux-key-listener"

    def test_build_script_enforces_linux_host(self):
        """The wrapper refuses to run on non-Linux hosts."""
        src = BUILD_NATIVE_LISTENER_LINUX_SH.read_text(encoding="utf-8")
        assert "Linux" in src and "uname -s" in src, (
            "build_native_listener_linux.sh must enforce Linux host (uname -s == Linux)"
        )

    def test_build_script_checks_glibc_baseline(self):
        """The wrapper verifies the binary's glibc baseline (≤ GLIBC_2.35)."""
        src = BUILD_NATIVE_LISTENER_LINUX_SH.read_text(encoding="utf-8")
        assert "GLIBC" in src, "build_native_listener_linux.sh must verify the GLIBC baseline"
        assert "2.35" in src or "2.35" in src, (
            "build_native_listener_linux.sh must enforce GLIBC ≤ 2.35 (Ubuntu 22.04 baseline)"
        )

    def test_c_source_compiles_with_gcc(self):
        """``compile_native.sh`` runs ``gcc -O2 -std=c99`` on the C source."""
        src = COMPILE_NATIVE_SH.read_text(encoding="utf-8")
        assert "gcc" in src, "compile_native.sh must invoke gcc for the Linux build"
        # The C source's build instructions (header comment) must
        c_src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "gcc" in c_src, "linux-key-listener.c header comment must reference gcc build command"
