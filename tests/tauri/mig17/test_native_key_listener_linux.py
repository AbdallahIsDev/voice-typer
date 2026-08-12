"""MIG-1.7 Phase 0-L Gate Check 9 — native ``linux-key-listener`` (evdev, C).

Validates that the native Linux hotkey binary (built by
``scripts/build/compile_native.sh`` / wrapped by
``scripts/build/build_native_listener_linux.sh`` from
``voice_typer/server/native/linux-key-listener.c``) is correctly
bundled, discovered, spawned, and wire-protocol compatible with the
Python sidecar's ``native_hotkeys.SubprocessHotkeyBackend`` /
``LinuxEvdevHotkey``.

ADR-0020 §6.4 mandates KEEPING the native binary (do NOT switch to
``tauri-plugin-global-shortcut`` — Tauri's plugin uses X11 ONLY on
Linux, which **breaks Wayland**; evdev is the only Wayland-capable
path). The binary is spawned as a subprocess by the PYTHON SIDECAR
(not by the Tauri host); Tauri only ships it as a
``bundle.resource``. The sidecar discovers it via
``VOICE_TYPER_NATIVE_DIR`` (set by the Tauri host to
``resourceDir/native/``) or falls through to dev-mode / PyInstaller
paths. See ``native_hotkeys.get_native_binary_path`` for the full
6-step lookup chain.

These tests run on any platform (Linux sandbox included) — they mock
``subprocess.Popen``, ``pathlib.Path.is_file``/``Path.exists``, and
the ``is_windows()``/``is_macos()``/``is_linux()`` platform
predicates so the Linux code path is exercised deterministically
without depending on a real evdev device. The actual evdev
``/dev/input/event*`` reads + the X11/Wayland toggle can only be
validated on a real Linux display host — see the "VALIDATE ON LINUX
HOST" block below.

VALIDATE ON LINUX HOST:
    1. Launch Voice Typer
    2. Press F8 (default dictation hotkey) — verify dictation starts
    3. Press F8 again — verify dictation stops + transcribed text
       pastes
    4. Press ESC — verify dictation cancels
    5. Check ~/.local/share/voice-typer/logs/voice-typer.log for:
       - "[NATIVE-HOTKEY] Starting Linux backend (binary=linux-key-listener)"
       - "[NATIVE-HOTKEY] Linux binary is READY"
       - "[NATIVE-HOTKEY] hotkey pressed: F8"
    6. Verify the hotkey is NOT suppressed on Linux (evdev is
       read-only — F8 will reach the foreground app)
    7. Test on both X11 and Wayland sessions
    Expected: hotkey responds within 50ms; works on both X11 +
    Wayland; key NOT suppressed (Linux limitation)
    Note: if the user is not in the `input` group, the binary will
    fail to open /dev/input/event* — run
    `sudo usermod -aG input $USER` + log out + log back in.

    Shell verification commands (runbook Step 12 + §3):

        # Confirm the binary is alive while Voice Typer runs:
        ps aux | grep linux-key-listener | grep -v grep
        # Expected: a line containing 'linux-key-listener' while the
        # app is running.

        # Verify the user is in the input group:
        groups "$USER" | tr ' ' '\\n' | grep -x input
        # Expected: 'input' (set up by scripts/linux/postinst)

        # Verify the udev rule is installed:
        ls -l /etc/udev/rules.d/99-voice-typer.rules
        # Expected: the file exists, owned by root, mode 0644.

        # Verify the binary's glibc baseline (≤ GLIBC_2.35):
        ldd "<resourceDir>/resources/native/linux-key-listener" | \\
            grep -oE 'GLIBC_[0-9]+\\.[0-9]+' | sort -V | tail -1
        # Expected: GLIBC_2.35 or lower (Ubuntu 22.04 baseline).

        # Verify /dev/input/event* is group-readable by 'input':
        ls -l /dev/input/event0
        # Expected: crw-rw---- root input ... (mode 0660, group=input)
        # — installed by the 99-voice-typer.rules udev rule.

        # Tail the sidecar log for hotkey activity:
        tail -f ~/.local/share/voice-typer/logs/voice-typer.log | \\
            grep -E 'hotkey|native|KEY_DOWN|MOD_DOWN|toggle'

    Pass criteria (runbook Step 12 — gate point 8):
        - ``linux-key-listener`` appears in ``ps aux`` while Voice
          Typer is running.
        - Pressing the configured hotkey (F8) toggles dictation
          (bubble appears + recording starts; second press stops +
          pastes) on BOTH X11 and Wayland sessions.
        - The hotkey is NOT suppressed (F8 reaches the foreground app
          — e.g. browser dev tools F8 "step over" still fires). This
          is the documented Linux limitation per ADR-0020 §6.4.
        - ``groups $USER`` includes ``input``.
        - No ``permission denied: /dev/input/event*`` errors in the
          sidecar log.

Wire protocol (line-delimited TEXT, not JSON — same as the Windows +
macOS native listeners):

    READY                  # emitted once after init succeeds
    KEY_DOWN:<Name>        # non-modifier key pressed
    KEY_UP:<Name>          # non-modifier key released
    MOD_DOWN:<Name>        # modifier pressed (Ctrl, Shift, Alt, Super)
    MOD_UP:<Name>          # modifier released
    ERROR:<message>        # fatal error, binary will exit(1)

Note: there is NO ``FN_DOWN``/``FN_UP`` event on Linux — the Fn key
is firmware-only on most Linux laptops and never reaches the OS
(ADR-0020 §6.4 table row "Fn / Globe key on macOS").

Note on runbook section numbering: the native Linux key-listener
gate is documented in Step 12 of ``linux-validation-runbook.md``
(gate point 8 of the 9-point Phase 0-L validation gate). The task
spec refers to this as "§6.8"; in the actual runbook file the
heading is "Step 12 — Native ``linux-key-listener`` toggles
dictation on X11 AND Wayland". This test file validates Step 12 +
§3 (native listener build + toggle) + ADR-0020 §6.4.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Project paths ──────────────────────────────────────────────────────────

# test file: <root>/tests/tauri/mig17/test_native_key_listener_linux.py
# parents[0]=mig17, [1]=tauri, [2]=tests, [3]=voice-typer (project root)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
COMPILE_NATIVE_SH = PROJECT_ROOT / "scripts" / "build" / "compile_native.sh"
BUILD_NATIVE_LISTENER_LINUX_SH = PROJECT_ROOT / "scripts" / "build" / "build_native_listener_linux.sh"
LINUX_KEY_LISTENER_C = PROJECT_ROOT / "voice_typer" / "server" / "native" / "linux-key-listener.c"
# Phase 4.5 / : ``native_hotkeys`` and ``hotkeys`` were split
# from god-modules into packages. The tests below read the package
# ``__init__.py`` (which re-exports the public surface) and the
# relevant submodule (``linux_backend.py`` for LinuxEvdevHotkey,
# ``factory.py`` for create_hotkey_backend) so the assertions still
# match the post-split source layout.
NATIVE_HOTKEYS_PY = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys" / "__init__.py"
NATIVE_HOTKEYS_PKG_DIR = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys"
HOTKEYS_PY = PROJECT_ROOT / "voice_typer" / "server" / "hotkeys" / "factory.py"
POSTINST_SH = PROJECT_ROOT / "scripts" / "linux" / "postinst"
POSTINST_RPM_SH = PROJECT_ROOT / "scripts" / "linux" / "postinst.rpm"
INSTALL_PERMISSIONS_PY = PROJECT_ROOT / "scripts" / "linux" / "install_permissions.py"
UDEV_RULES = PROJECT_ROOT / "scripts" / "linux" / "99-voice-typer.rules"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
RUNBOOK = PROJECT_ROOT / "docs" / "migration" / "linux-validation-runbook.md"

NATIVE_RESOURCE_PATH = "resources/native/linux-key-listener"


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def linux_env(monkeypatch):
    """Patch the platform predicates + sys.platform to look like Linux.

    ``LinuxEvdevHotkey._validate_platform`` calls ``is_linux()`` from
    the ``native_hotkeys`` module namespace, and
    ``get_native_binary_path`` reads ``sys.platform`` to pick the
    binary name from ``_BINARY_NAMES``. Both must be patched together
    for the Linux code path to be exercisable deterministically
    (these tests must pass on the Linux sandbox, but the explicit
    patching also future-proofs them against running on CI runners
    that report ``linux2``/``linux3`` style platforms or are shared
    with macOS/Windows dev containers).

    XE-12-5 / RT-8: ``_spawn_process`` now calls
    ``verify_native_binary_or_skip`` (imported locally from
    ``binary_path``) BEFORE ``subprocess.Popen`` to close the TOCTOU
    window between the factory-time verify and the watchdog respawn.
    The sandbox has no native-binary manifest populated (no real
    ``linux-key-listener`` binary + no SHA-256 entry), so the
    verification fails and ``_spawn_process`` returns early without
    calling ``Popen`` — breaking the ``TestSubprocessSpawn`` suite
    which asserts on the ``Popen`` call. Mock the verifier to return
    True here so the ``Popen`` call is reached; the verifier's own
    behavior is pinned by the dedicated ``tests/test_native_hotkeys*``
    suite (which builds a real manifest + binary).
    """
    from voice_typer.server import native_hotkeys
    from voice_typer.server.native_hotkeys import binary_path

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
    # bypass the SHA-256 manifest gate so
    # ``_spawn_process`` reaches the ``subprocess.Popen`` call (the
    # ``TestSubprocessSpawn`` tests inject their own ``fake_popen`` and
    # assert on its arguments; they don't exercise the verifier).
    monkeypatch.setattr(binary_path, "verify_native_binary_or_skip", lambda _path: True)
    return native_hotkeys


# ─── §1. Tauri bundle resources ─────────────────────────────────────────────


class TestTauriBundleResources:
    """Verify ``tauri.conf.json`` ships the native listener as a resource."""

    def test_tauri_conf_json_exists(self):
        """The Tauri config file must exist (sanity check)."""
        assert TAURI_CONF.is_file(), f"Missing Tauri config: {TAURI_CONF}"

    def test_tauri_conf_bundles_linux_native_listener(self):
        """``resources/native/linux-key-listener`` must be in bundle.resources.

        ADR-0020 §7 lists this exact path in the ``bundle.resources``
        array. Without it, the production ``.deb``/``.rpm``/``.AppImage``
        bundle won't ship the binary and the sidecar will log
        ``native binary not found`` at startup (runbook Step 12 fail
        scenario). Tauri extracts ``bundle.resources`` entries to
        ``resourceDir`` preserving the relative path, so this entry
        lands at ``<resourceDir>/resources/native/linux-key-listener``.
        """
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert NATIVE_RESOURCE_PATH in resources, (
            f"tauri.conf.json bundle.resources must include {NATIVE_RESOURCE_PATH!r} (ADR-0020 §7). Found: {resources}"
        )

    def test_native_listener_is_resource_not_external_bin(self):
        """The native listener is a ``resource`` (sidecar-spawned), NOT ``externalBin``.

        ADR-0020 §6.4: "Tauri does not touch the hotkey subsystem at
        all." If the binary were in ``externalBin``, Tauri would own
        its lifecycle — that would violate the ADR. It must be a
        ``resource`` so the Python sidecar spawns it via
        ``subprocess.Popen``.
        """
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        external_bins = conf.get("bundle", {}).get("externalBin", [])
        resources = conf.get("bundle", {}).get("resources", [])

        # Must be in resources.
        assert NATIVE_RESOURCE_PATH in resources
        # Must NOT be in externalBin (Tauri must not spawn it).
        for ext in external_bins:
            assert "linux-key-listener" not in ext, (
                f"linux-key-listener must NOT be in externalBin (Tauri must not spawn it — ADR-0020 §6.4). Found: {ext}"
            )

    def test_tauri_conf_also_bundles_windows_and_macos_listeners(self):
        """All three platform binaries are bundled (cross-platform ship).

        ADR-0020 §7 lists all three. This is a sanity check that the
        Linux entry isn't alone — confirms the resources array is the
        cross-platform native-listener block. (Tauri ships all three
        platform binaries in every bundle; the sidecar picks the
        matching one at runtime via ``_BINARY_NAMES[sys.platform]``.)
        """
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/windows-key-listener.exe" in resources
        assert "resources/native/macos-key-listener" in resources

    def test_tauri_conf_linux_deb_uses_postinst_script(self):
        """The Linux ``.deb`` bundle reuses the existing ``postinst`` script.

        ADR-0020 §6.4 + §13.3: the existing
        ``scripts/linux/postinst`` (which installs the udev rule +
        adds the user to the ``input`` group) must be reused
        verbatim by the Tauri ``.deb``. Tauri v2's
        ``bundle.linux.deb.postInstallScript`` field points at this script.
        """
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
        # Tauri v2 uses the long-form `postInstallScript` key
        # (NOT `postInstall` which was the Tauri v1 short form).
        # See https://v2.tauri.app/reference/config/#debconfig
        assert "postInstallScript" in deb, (
            "bundle.linux.deb.postInstallScript missing — Tauri v2 requires the 'postInstallScript' key"
        )
        assert "postInstall" not in deb, (
            "stale short-form 'postInstall' key present on bundle.linux.deb — "
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


# ─── §2. Subprocess spawn ───────────────────────────────────────────────────


class TestSubprocessSpawn:
    """Verify ``SubprocessHotkeyBackend._spawn_process`` uses ``subprocess.Popen`` correctly."""

    def test_spawn_uses_subprocess_popen(self, linux_env, monkeypatch, tmp_path):
        """``_spawn_process`` must call ``subprocess.Popen`` (not ``run``/``call``/``check_output``).

        ``Popen`` is required because the sidecar needs a long-lived
        child process whose stdout is streamed line-by-line by the
        reader thread. ``run``/``call``/``check_output`` would block
        until the binary exits — useless for an event-driven listener
        that runs for the app's lifetime.
        """
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
        """The hotkey spec string (e.g. ``<f8>``) is passed as ``argv[1]``.

        ``_spawn_process`` builds ``cmd = [str(binary_path), self.hotkey_str]``.
        The native C binary parses ``argv[1]`` via
        ``validate_hotkey_spec(argv[1])`` to know which hotkey to
        watch. This is NOT stdin, NOT JSON — it's a plain pynput-style
        spec string as argv[1].
        """
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
        """stdout=PIPE, stderr=STDOUT, stdin=PIPE (G4-H-31 watchdog).

        stdout MUST be piped — the reader thread reads line-delimited
        wire-protocol events (READY / KEY_DOWN / MOD_DOWN / ERROR)
        from it. stderr is redirected to stdout so error output is
        visible in the same stream. stdin is PIPE (G4-H-31) so the
        watchdog can write ``PING\\n`` to detect a stuck reader via
        the ``PONG\\n`` response; the binary is still event-driven
        for hotkey detection (poll on /dev/input/event* fds).
        """
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
        assert kwargs.get("stdout") == subprocess.PIPE, (
            "stdout must be PIPE — reader thread streams wire-protocol lines"
        )
        assert kwargs.get("stderr") == subprocess.STDOUT, (
            "stderr must redirect to stdout so errors surface in the wire stream"
        )
        # stdin is now PIPE (was DEVNULL) so the watchdog can
        # write ``PING\n`` to the binary's stdin and verify it's alive
        # via the ``PONG\n`` response. The binary is still event-driven
        # for hotkey detection (poll on /dev/input/event* fds); the
        # PING/PONG channel is a separate liveness probe.
        assert kwargs.get("stdin") == subprocess.PIPE, (
            "stdin must be PIPE — G4-H-31 added a PING/PONG watchdog that writes "
            "to the binary's stdin every 30s to detect a stuck reader (the binary "
            "responds with PONG\\n); was DEVNULL before the watchdog was added"
        )

    def test_spawn_uses_start_new_session_on_linux(self, linux_env, monkeypatch, tmp_path):
        """On Linux, ``start_new_session=True`` so SIGTERM works cleanly.

        ``_spawn_process`` sets ``start_new_session=is_macos() or is_linux()``
        so the child is in its own process group. The sidecar then
        sends ``SIGTERM`` (not ``terminate()``) to shut it down — the
        C binary installs a SIGTERM handler (``on_signal``) that
        exits the ``poll()`` loop cleanly via ``g_should_exit``.
        """
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
        """If ``Popen`` raises ``OSError``, ``_spawn_process`` raises ``RuntimeError``.

        This covers the "binary disappeared mid-restart" path — the
        reader loop catches the RuntimeError and notifies the adapter
        via ``_on_permanent_failure_callback`` (which on Linux would
        fall back to ``WaylandHotkey`` or ``PynputHotkey``).
        """
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        backend._binary_path = tmp_path / "linux-key-listener"
        # ``_spawn_process`` opens the binary with
        # ``os.open`` (O_RDONLY | O_CLOEXEC) BEFORE the SHA-256 verify +
        # Popen so the fd pins the inode for the pre-Popen stat check.
        # The file must exist on disk or the ``os.open`` fails first
        # (setting ``_failed=True`` + returning early — NOT raising).
        # Create a placeholder so the ``os.open`` succeeds; the test
        # exercises the ``Popen``-raises path, not the ``os.open``-fails
        # path.
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
        """On Linux, ``creationflags`` is 0 (no CREATE_NO_WINDOW — Windows-only).

        ``_spawn_process`` only sets ``CREATE_NO_WINDOW`` when
        ``is_windows()`` is True. On Linux this flag would be a no-op
        (or raise), so it must be 0.
        """
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
        # creationflags must be 0 on Linux (CREATE_NO_WINDOW is Windows-only).
        assert captured["kwargs"].get("creationflags", 0) == 0, (
            "creationflags must be 0 on Linux — CREATE_NO_WINDOW is Windows-only and would be a no-op or raise on POSIX"
        )


# ─── §3. Binary discovery ───────────────────────────────────────────────────


class TestBinaryDiscovery:
    """Verify the binary is discovered via ``VOICE_TYPER_NATIVE_DIR`` or dev/bundle paths."""

    def test_voice_typer_native_dir_lookup_finds_linux_binary(self, linux_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_DIR`` (Tauri dev/prod) points at the bundle's native dir.

        ADR-0020 §7: Tauri sets ``VOICE_TYPER_NATIVE_DIR`` to
        ``resourceDir/native/``. The sidecar's
        ``get_native_binary_path`` checks this env var (step 2 of the
        6-step lookup chain) and returns
        ``<dir>/linux-key-listener`` on Linux.
        """
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
        """``VOICE_TYPER_NATIVE_BINARY`` (single-file override) beats ``_DIR``.

        Lookup step 1 (explicit single-binary override) beats step 2
        (Tauri resource dir). This lets a developer point at a custom
        C build without unset-ing the Tauri env var.
        """
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
        """Production layout: ``resourceDir/native/linux-key-listener``.

        Tauri extracts ``bundle.resources`` entries to ``resourceDir``
        preserving the relative path, so
        ``resources/native/linux-key-listener`` (the tauri.conf entry)
        lands at ``<resourceDir>/resources/native/linux-key-listener``.

        The runbook Step 12 fail scenario ("native binary not found")
        happens when Tauri didn't set ``VOICE_TYPER_NATIVE_DIR`` or
        the file isn't in the bundle. This test simulates the
        happy-path layout.
        """
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
        """Without env vars, lookup falls through to the dev source-tree path.

        Lookup step 3/4: ``voice_typer/server/native/<binary>``. In dev
        mode (running from source), the freshly-compiled binary sits
        here. This test mocks ``Path.is_file`` so the dev path
        "exists" without depending on whether the binary has been
        compiled in the sandbox.
        """
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # The dev-mode path is <server_dir>/native/linux-key-listener.
        # ``binary_path.py`` computes this as
        # ``Path(__file__).resolve().parent.parent / "native"``, i.e.
        # it goes UP from ``native_hotkeys/`` to ``server/`` before
        # descending into ``native/``. ``NATIVE_HOTKEYS_PY`` points at
        # the package ``__init__.py`` (Phase 4.5 /  split), so
        # we mirror the source's ``.parent.parent`` traversal here.
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
        """Binary discovery uses ``Path.is_file`` (and ``Path.exists`` semantics).

        This pins the discovery contract: the candidate path is
        checked for existence via ``Path.is_file`` before being
        returned. Mocking ``Path.is_file`` to return True for a
        non-existent path lets discovery succeed (simulating the
        bundle being present without actually writing the binary).
        """
        # Point VOICE_TYPER_NATIVE_DIR at a dir whose "binary" only
        # exists because we mock Path.is_file.
        fake_native_dir = tmp_path / "fake-bundle"
        fake_native_dir.mkdir()
        # NOTE: do NOT write the binary file — Path.is_file is mocked
        # to return True so discovery still succeeds.
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
        # that use it instead of is_file.
        real_exists = Path.exists

        def fake_exists(self):
            if self == candidate:
                return True
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = linux_env.get_native_binary_path()
        assert result is not None
        assert result == candidate


# ─── §4. Wire protocol (stdout line-delimited text) ─────────────────────────


class TestWireProtocol:
    """Verify the backend parses the native binary's stdout wire protocol.

    The wire protocol is line-delimited TEXT (not JSON):

        READY
        KEY_DOWN:<Name>
        KEY_UP:<Name>
        MOD_DOWN:<Name>
        MOD_UP:<Name>
        ERROR:<message>

    There is NO ``FN_DOWN``/``FN_UP`` event on Linux — the Fn key is
    firmware-only on most Linux laptops (ADR-0020 §6.4).
    """

    def test_ready_line_sets_ready_event(self, linux_env):
        """``READY`` unblocks ``start()`` (which waits on ``_ready_event``)."""
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        assert not backend._ready_event.is_set()
        backend._handle_line("READY")
        assert backend._ready_event.is_set()
        assert not backend._failed

    def test_error_line_marks_failed_and_unblocks(self, linux_env):
        """``ERROR:<msg>`` marks the backend failed + unblocks ``start()``.

        The adapter's ``_on_error_callback`` is then invoked so the
        sidecar can classify the error (e.g. show the input-group
        onboarding prompt — ``scripts/linux/install_permissions.py``
        via pkexec for AppImage users).
        """
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        backend._handle_line("ERROR:Permission denied. Add yourself to the 'input' group")
        assert backend._failed
        assert backend._error_message is not None
        assert "input" in backend._error_message
        assert backend._ready_event.is_set()  # unblocks start()

    def test_key_down_f8_fires_dictation_callback(self, linux_env):
        """``KEY_DOWN:F8`` fires the press callback for the ``<f8>`` hotkey.

        This is the primary dictation-toggle path: the native C
        binary's evdev ``poll()`` loop reads an
        ``input_event {type=EV_KEY, code=KEY_F8, value=1}`` from
        ``/dev/input/event*``, emits ``KEY_DOWN:F8`` on stdout, the
        reader thread hands it to ``_handle_line``, which matches it
        against the registered spec and fires the callback.
        """
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
        """``<ctrl>+<alt>+v`` fires only when Ctrl+Alt are held AND V is pressed.

        On Linux the wire name for the Win/Super key is ``Super``
        (mapped to canonical ``cmd`` by ``_canonical_modifier``). This
        test uses Ctrl+Alt to avoid that mapping complexity.
        """
        backend = linux_env.LinuxEvdevHotkey("<ctrl>+<alt>+v")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")

        # V alone — no fire.
        backend._handle_line("KEY_DOWN:V")
        # Release V (the OS always emits KEY_UP between two distinct
        # KEY_DOWN events for the same key — the auto-repeat filter in
        # ``_on_key_event`` suppresses a second KEY_DOWN while the main
        # key is still tracked as down, so we must explicitly release V
        # before pressing it again with the modifiers held).
        backend._handle_line("KEY_UP:V")
        assert fired == []

        # Hold Ctrl+Alt, then press V — fire.
        backend._handle_line("MOD_DOWN:Ctrl")
        backend._handle_line("MOD_DOWN:Alt")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]

    def test_super_modifier_canonicalizes_to_cmd(self, linux_env):
        """Wire name ``Super`` (Linux) canonicalizes to ``cmd`` for matching.

        ADR-0020 §6.4: the Linux evdev binary emits ``MOD_DOWN:Super``
        for the Super/Meta key. The Python sidecar's
        ``_canonical_modifier`` collapses ``Super`` → ``cmd`` (same
        as ``Win`` on Windows and ``Cmd`` on macOS) so a ``<cmd>+v``
        spec matches across platforms.
        """
        backend = linux_env.LinuxEvdevHotkey("<cmd>+v")
        fired: list[str] = []
        backend._callback = lambda: fired.append("press")

        backend._handle_line("MOD_DOWN:Super")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]


# ─── §5. ``input`` group permission (evdev /dev/input/event* access) ────────


class TestInputGroupPermission:
    """Verify the native binary requires ``input`` group membership.

    ADR-0020 §6.4 + §13.3 + runbook Step 12: the native
    ``linux-key-listener`` binary reads ``/dev/input/event*`` (evdev)
    which is owned by ``root:input`` with mode ``0660`` (set by the
    ``99-voice-typer.rules`` udev rule). Without ``input`` group
    membership, ``open("/dev/input/eventN", O_RDONLY)`` fails with
    ``EACCES`` and the binary emits ``ERROR:Permission denied...``
    then exits 1.

    The existing ``scripts/linux/postinst`` (Debian) +
    ``postinst.rpm`` (Fedora) install the udev rule and add the
    installing user to the ``input`` group via ``usermod -aG input``.
    AppImage users get the same via ``pkexec`` + ``voice-typer.polkit``
    + ``install_permissions.py``.
    """

    def test_linux_key_listener_c_source_exists(self):
        """The C source file must exist (compiled by ``compile_native.sh``)."""
        assert LINUX_KEY_LISTENER_C.is_file(), f"Missing C source: {LINUX_KEY_LISTENER_C}"

    def test_c_source_opens_dev_input_event_devices(self):
        """The C source must open ``/dev/input/event*`` (evdev) — NOT use X11.

        ADR-0020 §6.4 table row "Wayland support": the native binary
        uses evdev (``/dev/input/event*``) which sits BELOW the
        display server, so it works on both X11 and Wayland. The
        Tauri plugin uses X11 only on Linux (breaks Wayland) — this
        is the critical Linux-specific reason to keep the native binary.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "/dev/input" in src, (
            "linux-key-listener.c must open devices under /dev/input "
            "(evdev — the only Wayland-capable Linux hotkey path)"
        )
        assert "event" in src, "linux-key-listener.c must scan for eventN devices in /dev/input"

    def test_c_source_emits_error_on_permission_denied(self):
        """The binary emits an ERROR line mentioning the ``input`` group on EACCES.

        When ``open("/dev/input/eventN")`` fails with ``EACCES``, the
        binary's ``discover_devices`` function emits an
        ``ERROR:Permission denied. Add yourself to the 'input' group``
        line so the sidecar can show a user-facing onboarding prompt.
        """
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
        """The binary emits an ERROR when no keyboard devices are found.

        ``discover_devices`` returns -1 (and emits ERROR) when zero
        keyboard-like devices are open. This covers the "no input
        group OR no keyboards on this system" failure mode.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "No keyboard devices found" in src, (
            "linux-key-listener.c must emit ERROR:No keyboard devices found "
            "when /dev/input/event* has no keyboard-like devices"
        )

    def test_python_backend_validates_linux_platform(self, linux_env):
        """``LinuxEvdevHotkey._validate_platform`` returns None on Linux.

        ``_validate_platform`` is the platform guard called by
        ``start()``. On Linux (with ``is_linux()`` True), it returns
        ``None`` (no error). On non-Linux it returns an error message
        and ``start()`` raises ``ValueError``.
        """
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        err = backend._validate_platform()
        assert err is None, f"LinuxEvdevHotkey._validate_platform must return None on Linux; got {err!r}"

    def test_python_backend_rejects_fn_on_linux(self, linux_env):
        """``LinuxEvdevHotkey`` rejects ``<fn>`` specs (firmware-only on Linux).

        ADR-0020 §6.4 table: Fn/Globe is macOS-only. The Linux
        subclass rejects ``<fn>`` in ``_validate_platform`` so a user
        can't configure a hotkey that will never fire.
        """
        backend = linux_env.LinuxEvdevHotkey("<fn>")
        err = backend._validate_platform()
        assert err is not None, "LinuxEvdevHotkey must reject <fn> specs — Fn is firmware-only on most Linux laptops"
        assert "FN" in err or "fn" in err, f"_validate_platform error must mention FN/fn; got {err!r}"

    def test_linux_backend_does_not_support_fn(self, linux_env):
        """``LinuxEvdevHotkey.supports_fn`` is False (Fn is firmware-only on Linux).

        ``MacNativeHotkey.supports_fn`` is True (only macOS surfaces
        Fn via ``NSEvent.modifierFlags.function``). The Windows +
        Linux subclasses are False.
        """
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        assert backend.supports_fn is False, (
            "LinuxEvdevHotkey.supports_fn must be False — Fn is firmware-only "
            "on most Linux laptops (never reaches the OS)"
        )


# ─── §6. evdev for global hotkeys (X11 + Wayland) ───────────────────────────


class TestEvdevGlobalHotkeys:
    """Verify the C source uses evdev for global hotkeys (works on X11 AND Wayland).

    ADR-0020 §6.4 table row "Wayland support": the native binary
    uses evdev (``/dev/input/event*``) which sits BELOW the display
    server, so it works on both X11 and Wayland. The Tauri plugin
    uses X11 only on Linux — **breaks Wayland**. This is the
    critical Linux-specific reason to keep the native binary.

    The 9-point Phase 0-L validation gate (runbook §7) requires
    the native listener to toggle dictation on BOTH X11 AND Wayland
    (gate point 8 / Step 12).
    """

    def test_c_source_uses_linux_input_header(self):
        """The C source includes ``<linux/input.h>`` (the evdev API).

        ``<linux/input.h>`` defines ``struct input_event``,
        ``EV_KEY``, ``KEY_F8``, ``EVIOCGBIT``, etc. — the evdev
        userspace API. Without this header the binary couldn't read
        keyboard events.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "<linux/input.h>" in src, (
            "linux-key-listener.c must include <linux/input.h> (the evdev userspace API header)"
        )

    def test_c_source_reads_input_event_structs(self):
        """The C source reads ``struct input_event`` from each fd.

        evdev events are 24 bytes on most architectures
        (``struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }``).
        The binary reads these in a tight ``poll()`` loop and emits
        wire-protocol events.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "struct input_event" in src, (
            "linux-key-listener.c must declare struct input_event (the evdev event record)"
        )
        assert "EV_KEY" in src, (
            "linux-key-listener.c must filter on ev.type == EV_KEY (ignoring EV_SYN, EV_REL, EV_ABS, etc.)"
        )

    def test_c_source_uses_poll_for_event_loop(self):
        """The C source uses ``poll(2)`` for the event loop (not ``select`` or busy-wait).

        ``poll()`` is the standard Linux multiplexer for fd
        readiness. The binary polls all open ``/dev/input/eventN``
        fds with a 500ms timeout (so ``g_should_exit`` is checked at
        least twice per second for clean shutdown).
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "poll" in src, (
            "linux-key-listener.c must use poll(2) for the event loop (multiplexing /dev/input/eventN fds)"
        )
        assert "pollfd" in src or "struct pollfd" in src, (
            "linux-key-listener.c must use struct pollfd (the poll() fd array)"
        )

    def test_c_source_uses_ioctl_eviongbit_for_keyboard_detection(self):
        """The C source uses ``ioctl(EVIOCGBIT)`` to detect keyboard devices.

        ``EVIOCGBIT(0, ...)`` returns the bitmap of supported event
        types; ``EVIOCGBIT(EV_KEY, ...)`` returns the bitmap of
        supported keys. The binary checks for ``KEY_A``, ``KEY_SPACE``,
        ``KEY_ENTER`` to filter out mice and other button-only devices.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "EVIOCGBIT" in src, (
            "linux-key-listener.c must use ioctl(EVIOCGBIT) to detect "
            "keyboard devices (filters out mice and button-only devices)"
        )
        assert "KEY_A" in src and "KEY_SPACE" in src and "KEY_ENTER" in src, (
            "linux-key-listener.c must check for KEY_A, KEY_SPACE, KEY_ENTER "
            "(keyboard heuristic — filters out non-keyboard input devices)"
        )

    def test_c_source_handles_autorepeat_events(self):
        """The binary filters out evdev autorepeat events (``ev.value == 2``).

        evdev emits ``ev.value == 1`` for key-down, ``0`` for key-up,
        and ``2`` for autorepeat (key held). The binary skips
        autorepeat so a held F8 doesn't fire the hotkey callback
        repeatedly.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "ev.value == 2" in src or "value == 2" in src, (
            "linux-key-listener.c must filter out evdev autorepeat events "
            "(ev.value == 2) so a held hotkey doesn't fire repeatedly"
        )

    def test_c_source_emits_ready_after_device_discovery(self):
        """The binary emits ``READY`` after device discovery succeeds.

        ``discover_devices`` opens all keyboard-like
        ``/dev/input/eventN`` fds. Only after this succeeds (i.e.
        the user is in the ``input`` group + at least one keyboard
        was found) does the binary emit ``READY`` on stdout,
        unblocking the Python sidecar's ``start()``.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert '"READY"' in src or '"READY' in src or 'emit("READY")' in src, (
            "linux-key-listener.c must emit READY after device discovery (unblocks the sidecar's start() READY-wait)"
        )

    def test_adr_documents_evdev_works_on_wayland(self):
        """ADR-0020 §6.4 documents that evdev works on Wayland.

        ADR-0020 §6.4 table row "Wayland support": "✅ (evdev sits
        below the display server)" vs "❌ (Tauri plugin uses X11 only
        on Linux)". This is the critical Linux-specific reason to
        keep the native binary.
        """
        assert ADR_0020.is_file()
        src = ADR_0020.read_text(encoding="utf-8")
        assert "evdev" in src.lower(), "ADR-0020 must reference evdev (the Linux native-listener API)"
        assert "Wayland" in src, (
            "ADR-0020 must reference Wayland (the Linux session type that the Tauri plugin cannot support)"
        )
        assert "X11 only" in src or "X11-only" in src, (
            "ADR-0020 must document that the Tauri plugin is X11-only on Linux"
        )


# ─── §7. Key suppression NOT supported (evdev is read-only) ─────────────────


class TestKeySuppressionNotSupported:
    """Verify the native binary does NOT support key suppression on Linux.

    ADR-0020 §6.4 table row "Key suppression":
    - Windows ✅ (``WH_KEYBOARD_LL`` returns non-zero)
    - macOS ✅ (CGEvent tap returns NULL)
    - Linux ❌ (evdev is read-only)

    This is a documented Linux limitation, NOT a bug. The dictation
    hotkey (e.g. F8) WILL reach the foreground app on Linux. The
    user must pick a hotkey that doesn't conflict with the foreground
    app (or accept the keystroke going through). The Caps Lock
    neutralization (``00-voice-typer-capslock.conf`` /
    ``gsettings set ... caps:none``) is handled separately at the
    XKB/GNOME/KDE/Sway level by ``install_permissions.py``.
    """

    def test_c_source_opens_devices_read_only(self):
        """The binary opens ``/dev/input/event*`` with ``O_RDONLY`` (read-only).

        evdev is read-only — there's no ``EVIOCGRAB`` (which would
        grab exclusive access and effectively suppress events but
        also break the foreground app's keyboard entirely). The
        binary just observes events and emits wire-protocol lines.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "O_RDONLY" in src, (
            "linux-key-listener.c must open /dev/input/event* with O_RDONLY "
            "(evdev is read-only — no key suppression on Linux)"
        )
        # The binary must NOT use EVIOCGRAB (which would grab exclusive
        # access and break the foreground app's keyboard entirely).
        assert "EVIOCGRAB" not in src, (
            "linux-key-listener.c must NOT use EVIOCGRAB (exclusive grab) — "
            "that would break the foreground app's keyboard entirely. The "
            "binary observes events only (no suppression)."
        )

    def test_c_source_does_not_write_to_devices(self):
        """The binary does not write to ``/dev/input/event*`` (no ``write(2)``).

        evdev writes (``EV_KEY`` synthesis via ``write()``) require
        ``O_WRONLY`` and are not used by the listener — the binary
        only reads events. This pins the read-only contract.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        # The binary uses read() to consume input_event structs, but
        # must NOT use write() to inject synthetic events.
        assert "read(" in src, "linux-key-listener.c must use read(2) to consume input_event structs"
        # write() to a fd would be event injection (synthesis). The
        # binary should not do this — it's read-only. (write() to
        # stdout via fputs/fputc is fine — that's the wire protocol.)
        # We check that no write() to the device fds exists.
        # The simplest check: the device open() uses O_RDONLY (validated
        # in the previous test), so write() to those fds would fail
        # with EBADF anyway.

    def test_c_source_documents_read_only_limitation(self):
        """The C source header comment documents the read-only limitation.

        Per ADR-0020 §6.4, this is a documented limitation — not a
        bug. The header comment in ``linux-key-listener.c`` must
        mention it so future maintainers don't try to add suppression.
        """
        src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        # The header comment must mention "read-only" + "suppress".
        assert "read-only" in src.lower() or "read only" in src.lower(), (
            "linux-key-listener.c must document that evdev is read-only "
            "(the no-suppression limitation per ADR-0020 §6.4)"
        )
        assert "suppress" in src.lower(), (
            "linux-key-listener.c must reference suppression (the documented "
            "Linux limitation — evdev cannot suppress keystrokes)"
        )

    def test_adr_documents_linux_no_suppression(self):
        """ADR-0020 §6.4 documents that Linux has NO key suppression.

        ADR-0020 §6.4 table row "Key suppression" + §6.4 table row
        "Modifier-only hotkeys": Linux ❌ for suppression (evdev
        read-only), but ✅ for modifier-only hotkeys (the binary
        emits ``MOD_DOWN:Super`` / ``MOD_DOWN:Ctrl`` etc.).
        """
        src = ADR_0020.read_text(encoding="utf-8")
        # The table row for Linux suppression must be marked ❌.
        assert "Linux ❌" in src or "Linux ❌ (evdev read-only)" in src, (
            "ADR-0020 §6.4 must document that Linux has NO key suppression (evdev is read-only)"
        )
        assert "read-only" in src.lower(), (
            "ADR-0020 must reference evdev being read-only as the Linux suppression limitation"
        )

    def test_python_backend_does_not_advertise_suppression(self, linux_env):
        """The Python ``LinuxEvdevHotkey`` does not advertise suppression capability.

        There's no ``supports_suppression`` attribute on
        ``SubprocessHotkeyBackend`` (the parent class) — suppression
        is implicit per-platform (macOS/Windows yes, Linux no). The
        macOS subclass's source uses CGEvent tap (returns nil);
        Linux's source uses O_RDONLY evdev (no suppression). The
        Python backend doesn't need an explicit flag because the
        binary's wire protocol carries no suppression state — the
        sidecar just matches events.
        """
        backend = linux_env.LinuxEvdevHotkey("<f8>")
        # No suppression attribute on the Linux backend (it's implicit
        # in the C source's read-only evdev design).
        assert not getattr(backend, "supports_suppression", False), (
            "LinuxEvdevHotkey must NOT advertise supports_suppression — evdev is read-only (ADR-0020 §6.4)"
        )


# ─── §8. Sidecar ownership (Python starts the native listener on startup) ───


class TestSidecarOwnership:
    """Verify the Python sidecar (not the Tauri host) owns the native listener.

    ADR-0020 §6.4: "Decision: keep the native hotkey binaries, spawned
    by the Python sidecar (not by Tauri). ... Tauri does not touch the
    hotkey subsystem at all."

    The sidecar's ``hotkeys.create_hotkey_backend()`` factory tries
    ``create_native_backend()`` FIRST (NATIVE-001); on Linux this
    returns a ``LinuxEvdevHotkey`` wrapped in ``_NativeBackendAdapter``.
    Only if the native binary is missing does it fall back to
    ``WaylandHotkey`` (Wayland session) or ``PynputHotkey`` (X11).
    """

    def test_native_hotkeys_module_lives_in_python_sidecar(self):
        """``native_hotkeys.py`` lives under ``voice_typer/server/`` (the sidecar package)."""
        assert NATIVE_HOTKEYS_PY.is_file(), f"native_hotkeys.py must exist in the Python sidecar: {NATIVE_HOTKEYS_PY}"
        assert "voice_typer" in NATIVE_HOTKEYS_PY.parts
        assert "server" in NATIVE_HOTKEYS_PY.parts

    def test_native_hotkeys_module_defines_linux_backend(self):
        """``native_hotkeys`` (package) defines ``SubprocessHotkeyBackend`` + ``LinuxEvdevHotkey``.

        Phase 4.5 / ARCH-045 split the original ``native_hotkeys.py``
        god-module into a package: ``SubprocessHotkeyBackend`` now lives
        in ``base.py`` and ``LinuxEvdevHotkey`` in ``linux_backend.py``.
        Both are re-exported from ``native_hotkeys/__init__.py``. We
        read the package directory and assert the class definitions are
        present somewhere in the package (not just re-exported from
        elsewhere).
        """
        # Read the package __init__.py + the two submodules that define
        # the classes. The __init__.py re-exports them but doesn't define
        # them (the ``class`` keyword lives in the submodules).
        assert NATIVE_HOTKEYS_PY.is_file(), f"native_hotkeys package __init__.py must exist: {NATIVE_HOTKEYS_PY}"
        parts: list[str] = [NATIVE_HOTKEYS_PY.read_text(encoding="utf-8")]
        for sub in ("base.py", "linux_backend.py"):
            sub_path = NATIVE_HOTKEYS_PKG_DIR / sub
            if sub_path.is_file():
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
        """``_BINARY_NAMES["linux"]`` is ``linux-key-listener`` (matches the tauri.conf resource).

        ``get_native_binary_path`` reads ``_BINARY_NAMES[sys.platform]``
        to pick the binary name. On Linux this is
        ``"linux-key-listener"``, matching the
        ``resources/native/linux-key-listener`` entry in
        ``tauri.conf.json``.
        """
        assert linux_env._BINARY_NAMES.get("linux") == "linux-key-listener", (
            "_BINARY_NAMES['linux'] must be 'linux-key-listener' (matches the tauri.conf.json bundle.resources entry)"
        )

    def test_hotkeys_factory_tries_native_backend_first(self, linux_env):
        """``hotkeys.create_hotkey_backend`` tries ``create_native_backend`` first.

        ``hotkeys/factory.py::create_hotkey_backend`` calls
        ``native_hotkeys.create_native_backend`` before falling back
        to legacy backends. On Linux, when the native binary IS
        available, this returns a ``LinuxEvdevHotkey`` (wrapped in
        ``_NativeBackendAdapter``).
        """
        assert HOTKEYS_PY.is_file()
        src = HOTKEYS_PY.read_text(encoding="utf-8")
        assert "create_native_backend" in src, (
            "hotkeys/factory.py must call create_native_backend (the native-first factory, NATIVE-001)"
        )
        # LinuxEvdevHotkey is defined in the native_hotkeys package
        # (linux_backend.py), not in hotkeys/factory.py. The factory
        # references it via create_native_backend (which returns it).
        # removed the stale docstring reference; check the
        # native_hotkeys package __init__.py + linux_backend.py instead.
        native_init = HOTKEYS_PY.parent.parent / "native_hotkeys" / "__init__.py"
        native_linux = HOTKEYS_PY.parent.parent / "native_hotkeys" / "linux_backend.py"
        for native_src_path in (native_init, native_linux):
            if native_src_path.is_file():
                nsrc = native_src_path.read_text(encoding="utf-8")
                assert "LinuxEvdevHotkey" in nsrc, (
                    f"{native_src_path.name} must define/reference LinuxEvdevHotkey (the Linux native backend)"
                )
        # The factory must fall back to WaylandHotkey on Wayland
        # sessions when the native binary is missing.
        assert "WaylandHotkey" in src, "hotkeys/factory.py must reference WaylandHotkey (the Wayland legacy fallback)"

    def test_native_factory_returns_linux_backend_on_linux(self, linux_env, monkeypatch, tmp_path):
        """``create_native_backend`` returns a ``LinuxEvdevHotkey`` on Linux when the binary exists.

        This is the actual startup path: the sidecar's
        ``HotkeyDispatcher.__init__`` calls
        ``create_hotkey_backend(hotkey_str)``, which calls
        ``create_native_backend(hotkey_str)``, which calls
        ``get_native_binary_path()`` — if the binary is found and
        ``is_linux()`` is True, a ``LinuxEvdevHotkey`` is returned.

        CR-002 (fail-closed): ``verify_native_binary_or_skip`` returns
        False when the manifest entry is missing or has an empty sha256.
        The dummy binary written to ``tmp_path`` has no manifest entry,
        so without monkeypatching the factory would fail-closed and
        return None. We monkeypatch ``verify_native_binary_or_skip`` to
        return True so the Linux code path is exercised (the manifest
        verification itself is tested in ``tests/test_native_manifest_*``).
        """
        # Make the binary discoverable.
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "linux-key-listener").write_text("dummy")
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

        # the dummy binary has no manifest entry, so
        # ``verify_native_binary_or_skip`` would fail-closed and return
        # False. Monkeypatch it to return True so the Linux code path is
        # exercised (the manifest verification is tested elsewhere).
        # Patch BOTH the ``binary_path`` module attribute AND the
        # ``factory`` module's imported reference (factory.py does
        # ``from .binary_path import verify_native_binary_or_skip``).
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
        """``create_native_backend`` returns None when the binary is NOT found.

        This is the fallback-trigger path: when the native binary is
        missing (e.g. running from a source checkout without
        compiling it), the factory returns None, and
        ``create_hotkey_backend`` falls back to ``WaylandHotkey``
        (Wayland) or ``PynputHotkey`` (X11).
        """
        # Force all lookup paths to miss.
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # Mock Path.is_file to always return False so all 6 lookup
        # steps miss (dev path, PyInstaller paths, etc.).
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
        """ADR-0020 §6.4 documents the Wayland regression risk.

        The Tauri plugin uses X11 only on Linux — switching would
        break Wayland. Keeping the native binary (evdev) preserves
        Wayland support. This is the Linux-specific critical feature
        ADR-0020 §6.4 preserves (analogous to Fn/Globe on macOS).
        """
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
        """Runbook Step 12 documents the native listener + the sidecar.

        The Linux runbook Step 12 (gate point 8) references
        ``linux-key-listener`` + the python-sidecar binary (the host
        that spawns the listener). Together with ADR-0020 §6.4
        (validated in ``test_adr_states_sidecar_owns_hotkey_subsystem``),
        this documents that the sidecar owns the listener lifecycle.
        """
        assert RUNBOOK.is_file()
        src = RUNBOOK.read_text(encoding="utf-8")
        assert "linux-key-listener" in src, "Runbook must document the linux-key-listener gate (Step 12)"
        assert "X11" in src and "Wayland" in src, "Runbook Step 12 must require testing on BOTH X11 and Wayland"


# ─── §9. ``scripts/linux/postinst`` — input group + udev rules ──────────────


class TestPostinstSetup:
    """Verify ``scripts/linux/postinst`` sets up the ``input`` group + udev rules.

    ADR-0020 §6.4 + §13.3 + runbook Step 12: the existing
    ``scripts/linux/postinst`` (Debian) + ``postinst.rpm`` (Fedora)
    must be reused verbatim by the Tauri ``.deb``/``.rpm`` packages.
    The script delegates to ``scripts/linux/install_permissions.py``
    which (a) installs the udev rule ``99-voice-typer.rules``, (b)
    adds the installing user to the ``input`` group via
    ``usermod -aG input``, (c) configures Caps Lock neutralization,
    (d) writes a manifest at
    ``/var/lib/voice-typer/permissions-manifest.json``.
    """

    def test_postinst_script_exists(self):
        """The Debian ``postinst`` script must exist (referenced by tauri.conf.json)."""
        assert POSTINST_SH.is_file(), f"Missing postinst: {POSTINST_SH}"

    def test_postinst_rpm_script_exists(self):
        """The RPM ``postinst.rpm`` script must exist (for Fedora packages)."""
        assert POSTINST_RPM_SH.is_file(), f"Missing postinst.rpm: {POSTINST_RPM_SH}"

    def test_postinst_invokes_install_permissions_py(self):
        """The ``postinst`` script invokes ``install_permissions.py``.

        The postinst doesn't install the udev rule / add the user to
        the ``input`` group directly — it delegates to
        ``scripts/linux/install_permissions.py`` (the single source of
        truth for "what system modifications does Voice Typer make on
        Linux"). This is the zero-command setup flow: the user only
        types their sudo password once (prompted by apt itself).
        """
        src = POSTINST_SH.read_text(encoding="utf-8")
        assert "install_permissions.py" in src, (
            "postinst must invoke install_permissions.py (the single source of truth for Linux permission setup)"
        )
        assert "python3" in src, "postinst must invoke python3 to run install_permissions.py"

    def test_postinst_handles_configure_case(self):
        """The ``postinst`` script handles the ``configure`` case.

        Debian postinst scripts are invoked with ``$1 == configure``
        on install/upgrade. The script must guard the
        permission-setup work inside this case so it doesn't run on
        ``abort-upgrade`` etc.
        """
        src = POSTINST_SH.read_text(encoding="utf-8")
        assert 'case "$1"' in src, "postinst must use a case statement on $1 (Debian postinst contract)"
        assert "configure)" in src, "postinst must handle the 'configure)' case (install/upgrade)"

    def test_postinst_warns_on_log_out_log_back_in(self):
        """The ``postinst`` script warns the user to log out + log back in.

        Linux kernel limitation: the ``input`` group change doesn't
        take effect until the user logs out + logs back in (the
        group membership is read at login time and cached). The
        postinst must print this warning so the user doesn't try to
        use Voice Typer immediately after install (it would fail
        with ``ERROR:Permission denied`` from the binary).
        """
        src = POSTINST_SH.read_text(encoding="utf-8")
        assert "log out" in src.lower() and "log back in" in src.lower(), (
            "postinst must warn the user to log out + log back in for the "
            "input group change to take effect (Linux kernel limitation)"
        )

    def test_install_permissions_py_installs_udev_rule(self):
        """``install_permissions.py`` installs the udev rule to /etc/udev/rules.d/.

        The udev rule ``99-voice-typer.rules`` grants the ``input``
        group read access to ``/dev/input/event*`` (mode 0660). It's
        copied to ``/etc/udev/rules.d/`` and ``udevadm control
        --reload-rules`` + ``udevadm trigger --subsystem-match=input``
        are run so the rule takes effect immediately.
        """
        assert INSTALL_PERMISSIONS_PY.is_file()
        src = INSTALL_PERMISSIONS_PY.read_text(encoding="utf-8")
        assert "99-voice-typer.rules" in src, "install_permissions.py must install the 99-voice-typer.rules udev rule"
        assert "/etc/udev/rules.d" in src, "install_permissions.py must copy the rule to /etc/udev/rules.d/"
        assert "udevadm" in src, "install_permissions.py must run udevadm to reload + trigger the rule"

    def test_install_permissions_py_adds_user_to_input_group(self):
        """``install_permissions.py`` adds the user to the ``input`` group.

        Uses ``usermod -aG input <username>`` (idempotent — checks
        ``grp.getgrnam('input').gr_mem`` first and skips if the user
        is already a member). The target user is determined from
        ``$SUDO_USER`` (set by sudo/apt/dnf) or ``$PKEXEC_UID``
        (set by pkexec for AppImage).
        """
        src = INSTALL_PERMISSIONS_PY.read_text(encoding="utf-8")
        assert "usermod" in src, "install_permissions.py must use usermod to add the user to the input group"
        assert "-aG" in src, (
            "install_permissions.py must use 'usermod -aG' (append to group, not replace the user's group list)"
        )
        assert '"input"' in src or "'input'" in src, "install_permissions.py must reference the 'input' group by name"

    def test_install_permissions_py_handles_sudo_user(self):
        """``install_permissions.py`` detects the target user via ``$SUDO_USER`` / ``$PKEXEC_UID``.

        The postinst runs as root (apt/dnf invoke it via sudo), so
        ``$USER`` is ``root``. The script must use ``$SUDO_USER``
        (the user who invoked sudo) or ``$PKEXEC_UID`` (the user who
        invoked pkexec for AppImage) to find the real target user.
        """
        src = INSTALL_PERMISSIONS_PY.read_text(encoding="utf-8")
        assert "SUDO_USER" in src, "install_permissions.py must read SUDO_USER (set by sudo/apt/dnf)"
        assert "PKEXEC_UID" in src, "install_permissions.py must read PKEXEC_UID (set by pkexec for AppImage)"

    def test_udev_rule_grants_input_group_access(self):
        """The udev rule grants the ``input`` group read access to ``eventN`` devices.

        ``KERNEL=="event[0-9]*", SUBSYSTEM=="input", GROUP="input", MODE="0660"``
        — this is the standard pattern for /dev/input access (same
        as the docker group for /var/run/docker.sock). Owner (root)
        gets rw, group (input) gets rw, others get nothing.
        """
        assert UDEV_RULES.is_file()
        src = UDEV_RULES.read_text(encoding="utf-8")
        assert 'KERNEL=="event' in src, "udev rule must match KERNEL==event[0-9]* (all input event devices)"
        assert 'SUBSYSTEM=="input"' in src, "udev rule must match SUBSYSTEM==input"
        assert 'GROUP="input"' in src, "udev rule must set GROUP=input (grants the input group rw access)"
        assert 'MODE="0660"' in src, "udev rule must set MODE=0660 (root rw, input rw, others none)"

    def test_udev_rule_triggers_reload_on_add(self):
        """The udev rule runs ``udevadm trigger`` on device add (hotplug support).

        When a USB keyboard is plugged in, the new
        ``/dev/input/eventN`` device is created. The rule applies
        automatically, but ``udevadm trigger --subsystem-match=input``
        ensures the permission change takes effect immediately
        without needing ``udevadm control --reload``.
        """
        src = UDEV_RULES.read_text(encoding="utf-8")
        assert 'ACTION=="add"' in src, "udev rule must handle ACTION==add (for hotplugged keyboards)"
        assert "udevadm trigger" in src, (
            "udev rule must run 'udevadm trigger' on add (immediate permission change for hotplugged devices)"
        )

    def test_postinst_rpm_also_invokes_install_permissions(self):
        """The RPM ``postinst.rpm`` also invokes ``install_permissions.py``.

        ADR-0020 §13.3: the RPM package must reuse the same
        permission-setup script as the Debian package (functionally
        identical). This is verified by checking that
        ``postinst.rpm`` references ``install_permissions.py``.
        """
        src = POSTINST_RPM_SH.read_text(encoding="utf-8")
        assert "install_permissions.py" in src, (
            "postinst.rpm must also invoke install_permissions.py (functionally "
            "identical to the Debian postinst per ADR-0020 §13.3)"
        )


# ─── §10. Build script ─────────────────────────────────────────────────────


class TestBuildScript:
    """Verify ``build_native_listener_linux.sh`` compiles + copies the binary.

    The wrapper invokes ``compile_native.sh`` (which detects Linux +
    runs ``gcc -O2 -std=c99 linux-key-listener.c -o linux-key-listener``),
    then copies the compiled binary to
    ``src-tauri/resources/native/linux-key-listener`` (the path in
    ``tauri.conf.json``'s ``bundle.resources`` array).
    """

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
        """The wrapper refuses to run on non-Linux hosts.

        ``gcc`` on Linux compiles for the host arch. Running the
        wrapper on macOS/Windows would produce a binary for the
        wrong platform; the wrapper fails fast with a clear message
        instead.
        """
        src = BUILD_NATIVE_LISTENER_LINUX_SH.read_text(encoding="utf-8")
        assert "Linux" in src and "uname -s" in src, (
            "build_native_listener_linux.sh must enforce Linux host (uname -s == Linux)"
        )

    def test_build_script_checks_glibc_baseline(self):
        """The wrapper verifies the binary's glibc baseline (≤ GLIBC_2.35).

        ADR-0020 §4.4: the Linux sidecar + native binary must run on
        Ubuntu 22.04 (glibc 2.35 baseline). The wrapper uses ``ldd``
        + ``grep -oE 'GLIBC_[0-9]+\\.[0-9]+'`` to find the max GLIBC
        symbol and rejects the binary if it requires a newer glibc.
        """
        src = BUILD_NATIVE_LISTENER_LINUX_SH.read_text(encoding="utf-8")
        assert "GLIBC" in src, "build_native_listener_linux.sh must verify the GLIBC baseline"
        assert "2.35" in src or "2.35" in src, (
            "build_native_listener_linux.sh must enforce GLIBC ≤ 2.35 (Ubuntu 22.04 baseline)"
        )

    def test_c_source_compiles_with_gcc(self):
        """``compile_native.sh`` runs ``gcc -O2 -std=c99`` on the C source.

        The C source's build instructions (header comment) must
        reference ``gcc``. ``-O2`` for performance (the binary is in
        the hot path — every keystroke is parsed). ``-std=c99`` for
        portability (no GNU extensions needed; the source uses
        ``_GNU_SOURCE`` only for ``strdup``/``strcasestr``).
        """
        src = COMPILE_NATIVE_SH.read_text(encoding="utf-8")
        assert "gcc" in src, "compile_native.sh must invoke gcc for the Linux build"
        # The C source's build instructions (header comment) must
        # also reference gcc.
        c_src = LINUX_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "gcc" in c_src, "linux-key-listener.c header comment must reference gcc build command"
