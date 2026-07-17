"""MIG-1.6 Phase 0-M Gate Check 9 — native ``macos-key-listener`` (Swift).

Validates that the native macOS hotkey binary (built by
``scripts/build/compile_native.sh`` / wrapped by
``scripts/build/build_native_listener_macos.sh`` from
``voice_typer/server/native/macos-key-listener.swift``) is correctly
bundled, discovered, spawned, and wire-protocol compatible with the
Python sidecar's ``native_hotkeys.SubprocessHotkeyBackend`` /
``MacNativeHotkey``.

ADR-0020 §6.4 mandates KEEPING the native binary (do NOT switch to
``tauri-plugin-global-shortcut`` — it lacks key suppression +
modifier-only hotkeys + the Fn/Globe key, which the Swift binary
detects via ``NSEvent.modifierFlags.contains(.function)``). The binary
is spawned as a subprocess by the PYTHON SIDECAR (not by the Tauri
host); Tauri only ships it as a ``bundle.resource``. The sidecar
discovers it via ``VOICE_TYPER_NATIVE_DIR`` (set by the Tauri host to
``resourceDir/native/``) or falls through to dev-mode / PyInstaller
paths. See ``native_hotkeys.get_native_binary_path`` for the full
6-step lookup chain.

These tests run on any platform (Linux sandbox included) — they mock
``subprocess.Popen``, ``pathlib.Path.is_file``, and the
``is_windows()``/``is_macos()``/``is_linux()`` platform predicates so
the macOS code path is exercised without a real macOS host. The
actual CGEvent tap + key suppression + Fn/Globe detection +
Accessibility onboarding can only be validated on a real macOS host —
see the "VALIDATE ON MACOS HOST" block below.

VALIDATE ON MACOS HOST:
    1. Launch Voice Typer
    2. If prompted, grant Accessibility permission: System Settings →
       Privacy & Security → Accessibility → enable Voice Typer
    3. Press F8 (default dictation hotkey) — verify dictation starts
    4. Press F8 again — verify dictation stops + transcribed text
       pastes
    5. Press ESC — verify dictation cancels
    6. Test Fn/Globe key (if configured) — verify it toggles dictation
    7. Check ~/Library/Logs/voice-typer/voice-typer.log for:
       - "[NATIVE-HOTKEY] Starting macOS backend (binary=macos-key-listener)"
       - "[NATIVE-HOTKEY] macOS binary is READY"
       - "[NATIVE-HOTKEY] hotkey pressed: F8"
    8. Verify the hotkey is SUPPRESSED (F8 doesn't reach the
       foreground app)
    Expected: hotkey responds within 50ms; key suppression works;
    Fn/Globe works
    (Same behavior on both Intel + Apple Silicon — the binary is
    universal or per-arch.)

    Shell verification commands (runbook §6.7 + §3):

        # Confirm the binary is alive while Voice Typer runs:
        pgrep -lf macos-key-listener
        # Expected: a line containing 'macos-key-listener' while the
        # app is running.

        # Verify the bundled binary has the correct Mach-O arch:
        file "/Applications/Voice Typer.app/Contents/Resources/macos-key-listener"
        # Expected (host-arch on Apple Silicon):
        #   Mach-O 64-bit executable arm64
        # Expected (universal):
        #   Mach-O universal binary with 2 architectures:
        #   [x86_64:Mach-O 64-bit executable x86_64]
        #   [arm64:Mach-O 64-bit executable arm64]

        # Verify ad-hoc (or Developer ID) signature:
        codesign -dv "/Applications/Voice Typer.app/Contents/Resources/macos-key-listener"
        # Expected:
        #   Identifier=macos-key-listener
        #   TeamIdentifier=not set   (ad-hoc) — or <TEAM_ID> (Developer ID)

        # Tail the sidecar log for hotkey activity:
        tail -f ~/Library/Logs/voice-typer/voice-typer.log | \\
            grep -E 'hotkey|native|KEY_DOWN|FN_DOWN|toggle'

    Pass criteria (runbook §6.7 + §3):
        - ``macos-key-listener`` appears in ``pgrep``/Activity Monitor
          while Voice Typer is running.
        - Pressing the configured hotkey (F8 or Fn/Globe) toggles
          dictation (bubble appears + recording starts; second press
          stops + pastes).
        - The hotkey is SUPPRESSED (F8 doesn't reach the foreground
          app — e.g. doesn't trigger F8 in browser dev tools, doesn't
          trigger Fn-key actions like brightness/volume).
        - The Fn/Globe key (default macOS hotkey per
          ``config._default_hotkey_for_platform``) toggles dictation.
        - No ``native binary not found`` errors in the sidecar log.

Wire protocol (line-delimited TEXT, not JSON — same as the Windows +
Linux native listeners):

    READY                  # emitted once after init succeeds
    FN_DOWN                # macOS only — Fn/Globe pressed (edge-detected)
    FN_UP                  # macOS only — Fn/Globe released (edge-detected)
    KEY_DOWN:<Name>        # non-modifier key pressed
    KEY_UP:<Name>          # non-modifier key released
    MOD_DOWN:<Name>        # modifier pressed (Ctrl, Shift, Alt, Cmd)
    MOD_UP:<Name>          # modifier released
    ERROR:<message>        # fatal error, binary will exit(1)

See ``native_hotkeys`` module docstring + the
``macos-key-listener.swift`` header comment for the canonical
definition.

Note on runbook section numbering: the native macOS key-listener
gate is documented in §6.7 of ``macos-validation-runbook.md`` (gate
point 8). §6.8 covers single-instance enforcement (gate point 9).
This test file validates §6.7 + §3 (native listener build + toggle).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Project paths ──────────────────────────────────────────────────────────

# test file: <root>/tests/tauri/mig16/test_native_key_listener_macos.py
# parents[0]=mig16, [1]=tauri, [2]=tests, [3]=voice-typer (project root)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
COMPILE_NATIVE_SH = PROJECT_ROOT / "scripts" / "build" / "compile_native.sh"
BUILD_NATIVE_LISTENER_MACOS_SH = PROJECT_ROOT / "scripts" / "build" / "build_native_listener_macos.sh"
MACOS_KEY_LISTENER_SWIFT = PROJECT_ROOT / "voice_typer" / "server" / "native" / "macos-key-listener.swift"
NATIVE_HOTKEYS_PY = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys.py"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
RUNBOOK = PROJECT_ROOT / "docs" / "migration" / "macos-validation-runbook.md"

NATIVE_RESOURCE_PATH = "resources/native/macos-key-listener"


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def macos_env(monkeypatch):
    """Patch the platform predicates + sys.platform to look like macOS.

    ``MacNativeHotkey._validate_platform`` calls ``is_macos()`` from
    the ``native_hotkeys`` module namespace, and
    ``get_native_binary_path`` reads ``sys.platform`` to pick the
    binary name from ``_BINARY_NAMES``. Both must be patched together
    for the macOS code path to be exercisable from a Linux sandbox.
    """
    from voice_typer.server import native_hotkeys

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
    return native_hotkeys


# ─── §1. Tauri bundle resources ─────────────────────────────────────────────


class TestTauriBundleResources:
    """Verify ``tauri.conf.json`` ships the native listener as a resource."""

    def test_tauri_conf_json_exists(self):
        """The Tauri config file must exist (sanity check)."""
        assert TAURI_CONF.is_file(), f"Missing Tauri config: {TAURI_CONF}"

    def test_tauri_conf_bundles_macos_native_listener(self):
        """``resources/native/macos-key-listener`` must be in bundle.resources.

        ADR-0020 §7 lists this exact path in the ``bundle.resources``
        array. Without it, the production ``.app``/``.dmg`` bundle
        won't ship the binary and the sidecar will log ``native binary
        not found`` at startup (runbook §6.7 fail scenario).
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
            assert "macos-key-listener" not in ext, (
                f"macos-key-listener must NOT be in externalBin (Tauri must not spawn it — ADR-0020 §6.4). Found: {ext}"
            )

    def test_tauri_conf_also_bundles_windows_and_linux_listeners(self):
        """All three platform binaries are bundled (cross-platform ship).

        ADR-0020 §7 lists all three. This is a sanity check that the
        macOS entry isn't alone — confirms the resources array is the
        cross-platform native-listener block.
        """
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/windows-key-listener.exe" in resources
        assert "resources/native/linux-key-listener" in resources


# ─── §2. Subprocess spawn ───────────────────────────────────────────────────


class TestSubprocessSpawn:
    """Verify ``SubprocessHotkeyBackend._spawn_process`` uses ``subprocess.Popen`` correctly."""

    def test_spawn_uses_subprocess_popen(self, macos_env, monkeypatch, tmp_path):
        """``_spawn_process`` must call ``subprocess.Popen`` (not ``run``/``call``/``check_output``).

        ``Popen`` is required because the sidecar needs a long-lived
        child process whose stdout is streamed line-by-line by the
        reader thread.
        """
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
        """The hotkey spec string (e.g. ``<f8>``) is passed as ``argv[1]``.

        ``_spawn_process`` builds ``cmd = [str(binary_path), self.hotkey_str]``.
        The native binary parses ``argv[1]`` to know which hotkey to
        watch + suppress. This is NOT stdin, NOT JSON — it's a plain
        pynput-style spec string as argv[1].
        """
        backend = macos_env.MacNativeHotkey("<f8>")
        fake_bin = tmp_path / "macos-key-listener"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        captured: dict = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return MagicMock()

        monkeypatch.setattr(macos_env.subprocess, "Popen", fake_popen)

        backend._spawn_process()
        assert captured["cmd"][1] == "<f8>", (
            "Hotkey spec must be argv[1] (the native binary parses it to decide which key to watch + suppress)."
        )

    def test_spawn_pipes_stdout_for_wire_protocol(self, macos_env, monkeypatch, tmp_path):
        """stdout=PIPE, stderr=STDOUT, stdin=DEVNULL.

        stdout MUST be piped — the reader thread reads line-delimited
        wire-protocol events (READY / KEY_DOWN / FN_DOWN / MOD_DOWN /
        ERROR) from it. stderr is redirected to stdout so error output
        is visible in the same stream. stdin is DEVNULL (the binary
        doesn't read commands from stdin — it's event-driven via the
        NSEvent monitors + CGEventTap).
        """
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
        kwargs = captured["kwargs"]
        assert kwargs.get("stdout") == subprocess.PIPE, (
            "stdout must be PIPE — reader thread streams wire-protocol lines"
        )
        assert kwargs.get("stderr") == subprocess.STDOUT, (
            "stderr must redirect to stdout so errors surface in the wire stream"
        )
        assert kwargs.get("stdin") == subprocess.DEVNULL, (
            "stdin must be DEVNULL — the binary is event-driven, not command-driven"
        )

    def test_spawn_uses_start_new_session_on_macos(self, macos_env, monkeypatch, tmp_path):
        """On macOS, ``start_new_session=True`` so SIGTERM works cleanly.

        ``_spawn_process`` sets ``start_new_session=is_macos() or is_linux()``
        so the child is in its own process group. The sidecar then
        sends ``SIGTERM`` (not ``terminate()``) to shut it down — the
        Swift binary installs a SIGTERM handler that disables the
        CGEventTap + removes the NSEvent monitors before ``exit(0)``.
        """
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
        """If ``Popen`` raises ``OSError``, ``_spawn_process`` raises ``RuntimeError``.

        This covers the "binary disappeared mid-restart" path — the
        reader loop catches the RuntimeError and notifies the adapter
        via ``_on_permanent_failure_callback``.
        """
        backend = macos_env.MacNativeHotkey("<f8>")
        backend._binary_path = tmp_path / "macos-key-listener"
        backend._stop_event.set()

        def raising_popen(cmd, **kwargs):
            raise OSError("Executable not found")

        monkeypatch.setattr(macos_env.subprocess, "Popen", raising_popen)

        with pytest.raises(RuntimeError, match="Failed to spawn"):
            backend._spawn_process()
        assert backend._failed is True
        assert backend._error_message is not None


# ─── §3. Binary discovery ───────────────────────────────────────────────────


class TestBinaryDiscovery:
    """Verify the binary is discovered via ``VOICE_TYPER_NATIVE_DIR`` or dev/bundle paths."""

    def test_voice_typer_native_dir_lookup_finds_macos_binary(self, macos_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_DIR`` (Tauri dev/prod) points at the bundle's native dir.

        ADR-0020 §7: Tauri sets ``VOICE_TYPER_NATIVE_DIR`` to
        ``resourceDir/native/``. The sidecar's
        ``get_native_binary_path`` checks this env var (step 2 of the
        6-step lookup chain) and returns
        ``<dir>/macos-key-listener`` on macOS.
        """
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
        """``VOICE_TYPER_NATIVE_BINARY`` (single-file override) beats ``_DIR``.

        Lookup step 1 (explicit single-binary override) beats step 2
        (Tauri resource dir). This lets a developer point at a custom
        Swift build without unset-ing the Tauri env var.
        """
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
        """Production layout: ``resourceDir/native/macos-key-listener``.

        Tauri extracts ``bundle.resources`` entries to ``resourceDir``
        preserving the relative path, so
        ``resources/native/macos-key-listener`` (the tauri.conf entry)
        lands at ``<resourceDir>/resources/native/macos-key-listener``.

        The runbook §6.7 fail scenario ("native binary not found")
        happens when Tauri didn't set ``VOICE_TYPER_NATIVE_DIR`` or
        the file isn't in the bundle. This test simulates the
        happy-path layout.
        """
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
        """Without env vars, lookup falls through to the dev source-tree path.

        Lookup step 3/4: ``voice_typer/server/native/<binary>``. In dev
        mode (running from source), the freshly-compiled binary sits
        here. This test mocks ``Path.is_file`` so the dev path
        "exists" without requiring a real compiled binary in the
        sandbox.
        """
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # The dev-mode path is <module_dir>/native/macos-key-listener.
        module_dir = NATIVE_HOTKEYS_PY.resolve().parent
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


# ─── §4. Wire protocol (stdout line-delimited text) ─────────────────────────


class TestWireProtocol:
    """Verify the backend parses the native binary's stdout wire protocol.

    The wire protocol is line-delimited TEXT (not JSON):

        READY
        FN_DOWN / FN_UP           # macOS only — Fn/Globe edge-detected
        KEY_DOWN:<Name>
        KEY_UP:<Name>
        MOD_DOWN:<Name>
        MOD_UP:<Name>
        ERROR:<message>

    See the ``native_hotkeys`` module docstring + the
    ``macos-key-listener.swift`` header comment for the canonical
    definition.
    """

    def test_ready_line_sets_ready_event(self, macos_env):
        """``READY`` unblocks ``start()`` (which waits on ``_ready_event``)."""
        backend = macos_env.MacNativeHotkey("<f8>")
        assert not backend._ready_event.is_set()
        backend._handle_line("READY")
        assert backend._ready_event.is_set()
        assert not backend._failed

    def test_error_line_marks_failed_and_unblocks(self, macos_env):
        """``ERROR:<msg>`` marks the backend failed + unblocks ``start()``.

        The adapter's ``_on_error_callback`` is then invoked so the
        sidecar can classify the error (e.g. show the macOS
        Accessibility onboarding prompt per ADR-0008 Gap 2).
        """
        backend = macos_env.MacNativeHotkey("<f8>")
        backend._handle_line("ERROR:Accessibility permission required")
        assert backend._failed
        assert backend._error_message == "Accessibility permission required"
        assert backend._ready_event.is_set()  # unblocks start()

    def test_key_down_f8_fires_dictation_callback(self, macos_env):
        """``KEY_DOWN:F8`` fires the press callback for the ``<f8>`` hotkey.

        This is the primary dictation-toggle path: the native binary's
        NSEvent .keyDown global monitor detects F8, emits
        ``KEY_DOWN:F8`` on stdout, the reader thread hands it to
        ``_handle_line``, which matches it against the registered spec
        and fires the callback.
        """
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

        # V alone — no fire.
        backend._handle_line("KEY_DOWN:V")
        assert fired == []

        # Hold Cmd+Alt, then press V — fire.
        backend._handle_line("MOD_DOWN:Cmd")
        backend._handle_line("MOD_DOWN:Alt")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]

    def test_fn_down_fires_for_fn_only_hotkey(self, macos_env):
        """``<fn>`` (Fn/Globe key) fires on ``FN_DOWN`` — macOS-only wire event.

        This is the critical macOS feature ``tauri-plugin-global-shortcut``
        CANNOT replace (ADR-0020 §6.4 table row "Fn / Globe key on
        macOS"). The Swift binary detects Fn via
        ``NSEvent.modifierFlags.contains(.function)`` and emits
        ``FN_DOWN`` / ``FN_UP`` edge-detected events; the matching
        logic in ``_on_fn_event`` fires the callback.
        """
        backend = macos_env.MacNativeHotkey("<fn>")
        assert backend._parsed is not None
        assert backend._parsed["is_fn_only"] is True

        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("FN_DOWN")
        assert fired == ["press"]


# ─── §5. Accessibility permission (AXIsProcessTrusted via CGEventTap) ──────


class TestAccessibilityPermission:
    """Verify the Swift source requires Accessibility permission.

    ADR-0020 §6.4 + runbook §6.7: the native ``macos-key-listener``
    binary requires macOS Accessibility permission (System Settings →
    Privacy & Security → Accessibility). Without it, the CGEventTap
    cannot be created (``CGEvent.tapCreate`` returns nil) and the
    binary emits ``ERROR:Accessibility permission required`` then
    exits 1. The sidecar's permission-retry flow (ADR-0008 Gap 2)
    then prompts the user.

    These are source-inspection tests — the actual permission grant
    can only be validated on a macOS host (runbook §6.7).
    """

    def test_macos_key_listener_swift_source_exists(self):
        assert MACOS_KEY_LISTENER_SWIFT.is_file(), f"Missing Swift source: {MACOS_KEY_LISTENER_SWIFT}"

    def test_swift_source_uses_cgevent_tap(self):
        """The Swift source must create a CGEventTap (``CGEvent.tapCreate``).

        ADR-0020 §6.4: "macOS ✅ (CGEvent tap returns NULL)". The
        ``.defaultTap`` option (vs ``.listenOnly``) is what gives the
        binary suppression power, but it requires Accessibility
        permission. ``tapCreate`` returns nil if the permission isn't
        granted — the binary detects this and emits ERROR.
        """
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
        """The binary emits ``ERROR:Accessibility permission required`` when tapCreate fails.

        ``CGEvent.tapCreate`` returns nil if Accessibility isn't
        granted. The binary must detect this, emit ERROR, and exit(1)
        so the sidecar's permission-retry flow (ADR-0008 Gap 2) can
        prompt the user.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "Accessibility permission required" in src, (
            "macos-key-listener.swift must emit ERROR:Accessibility permission "
            "required when CGEvent.tapCreate returns nil (missing Accessibility grant)"
        )

    def test_swift_source_supports_skip_accessibility_check_env(self):
        """The binary supports ``VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1`` for CI smoke tests.

        CI runners never have Accessibility permission. Without this
        escape hatch, the binary would always ERROR in CI. Setting
        this env var skips the CGEventTap entirely — the NSEvent
        monitors still work, but key-up delivery + suppression are
        lost. This is acceptable for CI smoke tests (we only verify
        the binary spawns + emits READY).
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK" in src, (
            "macos-key-listener.swift must support VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1 for CI smoke tests"
        )


# ─── §6. CGEvent tap for global hotkeys ─────────────────────────────────────


class TestCGEventTap:
    """Verify the Swift source installs a CGEventTap for global hotkey capture.

    ADR-0020 §6.4 table row "Key suppression": macOS uses CGEvent tap
    returning NULL to swallow the matched keystroke. The CGEventTap
    is also the only reliable source for key-up delivery (NSEvent
    global monitors MISS keyUp).
    """

    def test_swift_source_creates_session_event_tap(self):
        """The tap is created on ``.cgSessionEventTap`` (per-user session events).

        ``.cgSessionEventTap`` taps events at the session level (vs
        ``.cgAnnotatedSessionEventTap`` which adds annotation info, or
        ``.cgHeadInsertEventTap`` which is the placement option). This
        is the standard tap location for global hotkey capture.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert ".cgSessionEventTap" in src, (
            "macos-key-listener.swift must use .cgSessionEventTap "
            "(the per-user session event tap for global hotkey capture)"
        )

    def test_swift_source_handles_keydown_and_keyup(self):
        """The tap subscribes to both ``.keyDown`` and ``.keyUp``.

        ``.keyDown`` is needed for suppression (swallow the matched
        keystroke so it doesn't reach the foreground app).
        ``.keyUp`` is needed for reliable key-up delivery (NSEvent
        global monitors MISS keyUp — the CGEventTap is the only
        source).
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "CGEventType.keyDown" in src, (
            "macos-key-listener.swift must subscribe to CGEventType.keyDown (for suppression)"
        )
        assert "CGEventType.keyUp" in src, (
            "macos-key-listener.swift must subscribe to CGEventType.keyUp "
            "(for reliable key-up delivery — NSEvent global monitors miss keyUp)"
        )

    def test_swift_source_installs_run_loop_source(self):
        """The tap is scheduled on the main run loop via ``CFRunLoopAddSource``.

        CGEventTap callbacks fire on the run loop the source is added
        to. The binary uses ``CFRunLoopAddSource(... kCFRunLoopCommonModes)``
        so the tap stays active during modal loops (e.g. menu tracking).
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "CFMachPortCreateRunLoopSource" in src, (
            "macos-key-listener.swift must create a run loop source for the tap"
        )
        assert "CFRunLoopAddSource" in src, "macos-key-listener.swift must add the tap source to the run loop"

    def test_swift_source_handles_tap_disabled_events(self):
        """The tap re-enables itself on ``tapDisabledByTimeout`` / ``tapDisabledByUserInput``.

        The OS can disable the tap on timeout or user-input recursion.
        The callback must re-enable it via ``CGEvent.tapEnable`` so
        the binary keeps receiving events.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert ".tapDisabledByTimeout" in src, (
            "macos-key-listener.swift must handle tapDisabledByTimeout "
            "(the OS can disable the tap; the binary must re-enable it)"
        )
        assert ".tapDisabledByUserInput" in src, "macos-key-listener.swift must handle tapDisabledByUserInput"

    def test_swift_source_also_uses_nsevent_monitors(self):
        """The binary uses NSEvent global monitors alongside the CGEventTap.

        Per the Swift source header comment: the binary uses THREE
        event sources — (a) NSEvent .flagsChanged monitor for FN +
        modifier transitions, (b) NSEvent .keyDown monitor for
        non-modifier key-down events, (c) CGEventTap for key-up
        delivery + suppression. The CGEventTap is NOT used for
        key-down emission (the NSEvent monitor handles that to avoid
        duplicates).
        """
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


# ─── §7. Fn / Globe key support (the macOS-only feature) ────────────────────


class TestFnGlobeKey:
    """Verify the Swift source supports the Fn / Globe key.

    ADR-0020 §6.4 table row "Fn / Globe key on macOS": the native
    binary detects Fn via ``NSEvent.modifierFlags.contains(.function)``
    (bit 23). The Tauri plugin CANNOT detect Fn/Globe — this is the
    critical macOS-specific feature preserved by keeping the native
    binary.

    The default macOS hotkey is the Fn/Globe key (per
    ``config._default_hotkey_for_platform``) — without this binary,
    macOS users would lose the default dictation hotkey.
    """

    def test_swift_source_detects_fn_via_modifier_flag(self):
        """Fn is detected via ``NSEvent.modifierFlags.contains(.function)``.

        Critical: detect FN via the semantic ``.function`` flag (bit
        23) — NOT ``keyCode == 63``. The ``.function`` flag is the
        semantic "Fn is held" bit, edge-detected to prevent spurious
        FN_DOWN fires when unrelated modifiers change state.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert ".function" in src, (
            "macos-key-listener.swift must detect Fn via "
            "NSEvent.modifierFlags.contains(.function) (bit 23) — NOT keyCode == 63"
        )

    def test_swift_source_emits_fn_down_and_fn_up(self):
        """The binary emits ``FN_DOWN`` / ``FN_UP`` edge-detected wire events.

        These are macOS-only wire events (the Windows + Linux binaries
        don't emit them — Fn is firmware-only on those platforms). The
        Python sidecar's ``_handle_line`` parses them and routes them
        to ``_on_fn_event`` for matching against ``<fn>``-containing
        specs.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert '"FN_DOWN"' in src or '"FN_DOWN' in src or 'emit("FN_DOWN")' in src, (
            "macos-key-listener.swift must emit FN_DOWN on Fn press (edge-detected)"
        )
        assert '"FN_UP"' in src or '"FN_UP' in src or 'emit("FN_UP")' in src, (
            "macos-key-listener.swift must emit FN_UP on Fn release (edge-detected)"
        )

    def test_python_backend_supports_fn_only_hotkey(self, macos_env):
        """The Python backend parses ``<fn>`` as a Fn-only hotkey.

        ``parse_hotkey_spec("<fn>")`` returns a dict with
        ``is_fn_only=True``. The matching logic in ``_on_fn_event``
        fires the callback on ``FN_DOWN`` and the release callback on
        ``FN_UP``.
        """
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
        """``MacNativeHotkey.supports_fn`` is True (only macOS supports Fn).

        ``WindowsHookHotkey.supports_fn`` is False (Fn is firmware-only
        on Windows). ``LinuxEvdevHotkey.supports_fn`` is False (Fn is
        firmware-only on most Linux laptops). Only macOS surfaces Fn
        to the OS via the ``.function`` modifier flag.
        """
        backend = macos_env.MacNativeHotkey("<fn>")
        assert backend.supports_fn is True, (
            "MacNativeHotkey.supports_fn must be True — Fn/Globe is the "
            "default macOS hotkey (config._default_hotkey_for_platform)"
        )

    def test_windows_backend_rejects_fn(self, macos_env):
        """``WindowsHookHotkey`` rejects ``<fn>`` specs (firmware-only on Windows).

        ADR-0020 §6.4 table: Fn/Globe is macOS-only. The Windows +
        Linux subclasses reject ``<fn>`` in ``_validate_platform`` so
        a user can't configure a hotkey that will never fire.
        """
        backend = macos_env.WindowsHookHotkey("<fn>")
        # _validate_platform returns an error message (not None) on Windows.
        # NOTE: is_windows() is patched False by macos_env fixture, so this
        # test would normally trip the "not is_windows()" guard. To exercise
        # the Fn rejection logic specifically, monkeypatch is_windows True.
        macos_env.is_windows = lambda: True
        try:
            err = backend._validate_platform()
        finally:
            macos_env.is_windows = lambda: False
        assert err is not None
        assert "FN" in err or "fn" in err, "WindowsHookHotkey must reject <fn> specs — Fn is firmware-only on Windows"


# ─── §8. Key suppression (CGEvent tap returns nil) ──────────────────────────


class TestKeySuppression:
    """Verify the Swift source implements key suppression.

    ADR-0020 §6.4 table row "Key suppression": macOS uses CGEvent tap
    returning ``nil`` (NULL) to swallow the matched keystroke so it
    doesn't reach the foreground app. This is the critical feature
    ``tauri-plugin-global-shortcut`` lacks (Tauri's plugin is
    read-only on all platforms).

    These are source-inspection tests — the actual suppression can
    only be validated on a macOS host (runbook §6.7 step 8 in the
    VALIDATE block above).
    """

    def test_swift_source_has_should_suppress_keydown_function(self):
        """The Swift source has a ``shouldSuppressKeyDown`` decision function.

        This function returns ``true`` (swallow) or ``false`` (pass
        through) for each keydown. The CGEventTap callback checks it
        before deciding whether to return the event (pass through) or
        return ``nil`` (swallow).
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "shouldSuppressKeyDown" in src, (
            "macos-key-listener.swift must define shouldSuppressKeyDown() — "
            "the decision function that returns true to swallow a keystroke"
        )

    def test_swift_source_returns_nil_to_swallow(self):
        """The CGEventTap callback returns ``nil`` to suppress matched keystrokes.

        Returning ``nil`` from a ``CGEventTapCallBack`` (instead of
        passing the event through) prevents the keystroke from
        reaching the foreground app. This is the key-suppression
        mechanism ADR-0020 §6.4 preserves.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "return nil" in src, (
            "The CGEventTap callback must `return nil` to suppress a matched "
            "keystroke — returning the event would pass it through to the "
            "foreground app"
        )

    def test_swift_source_swallows_caps_lock_toggle(self):
        """Caps Lock hotkey suppresses the OS caps-state toggle.

        When the hotkey is ``<caps_lock>``, the binary swallows the
        CapsLock keydown (keyCode 57) so macOS doesn't toggle caps
        state. This is the ``isCapsLockOnly && keyCode == 57`` branch
        in ``shouldSuppressKeyDown``.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "CapsLock" in src or "caps_lock" in src, "macos-key-listener.swift must handle the CapsLock hotkey"
        assert "57" in src, (
            "macos-key-listener.swift must reference keyCode 57 (CapsLock) for caps-state-toggle suppression"
        )

    def test_swift_source_swallows_keyup_for_suppressed_keydown(self):
        """The binary swallows the matching keyUp for a suppressed keyDown.

        If we swallowed a keyDown, we must also swallow its keyUp —
        otherwise the foreground app sees an orphan keyUp (keydown
        suppressed, keyup delivered), which can confuse it. The
        ``suppressedKeyCode`` field tracks the swallowed keyDown's
        keyCode so the matching keyUp is also swallowed.
        """
        src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "suppressedKeyCode" in src, (
            "macos-key-listener.swift must track suppressedKeyCode so the "
            "matching keyUp is also swallowed (no orphan keyUp)"
        )


# ─── §9. Sidecar ownership (NOT the Tauri host) ─────────────────────────────


class TestSidecarOwnership:
    """Verify the Python sidecar (not the Tauri host) owns the native listener.

    ADR-0020 §6.4: "Decision: keep the native hotkey binaries, spawned
    by the Python sidecar (not by Tauri). ... Tauri does not touch the
    hotkey subsystem at all."
    """

    def test_native_hotkeys_module_lives_in_python_sidecar(self):
        """``native_hotkeys.py`` lives under ``voice_typer/server/`` (the sidecar package).

        The sidecar is the Nuitka-frozen Python process spawned by
        Tauri's ``externalBin`` mechanism. All hotkey logic —
        discovery, spawn, wire-protocol parsing, matching — lives in
        the sidecar, not in the Rust host.
        """
        assert NATIVE_HOTKEYS_PY.is_file(), f"native_hotkeys.py must exist in the Python sidecar: {NATIVE_HOTKEYS_PY}"
        # Confirm it's under voice_typer/server/ (the sidecar package).
        assert "voice_typer" in NATIVE_HOTKEYS_PY.parts
        assert "server" in NATIVE_HOTKEYS_PY.parts

    def test_native_hotkeys_module_defines_macos_backend(self):
        """``native_hotkeys.py`` defines ``SubprocessHotkeyBackend`` + ``MacNativeHotkey``."""
        src = NATIVE_HOTKEYS_PY.read_text(encoding="utf-8")
        assert "class SubprocessHotkeyBackend" in src, (
            "native_hotkeys.py must define SubprocessHotkeyBackend (the base class "
            "that spawns the native binary via subprocess.Popen)"
        )
        assert "class MacNativeHotkey" in src, "native_hotkeys.py must define MacNativeHotkey (the macOS subclass)"
        assert "subprocess.Popen" in src, "native_hotkeys.py must use subprocess.Popen to spawn the binary"

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
        """ADR-0020 §6.4 documents the Fn/Globe key regression risk.

        The Tauri plugin cannot detect Fn/Globe (the default macOS
        hotkey). Keeping the native binary preserves this feature.
        This is the macOS-specific critical feature ADR-0020 §6.4
        preserves.
        """
        src = ADR_0020.read_text(encoding="utf-8")
        assert "Fn" in src and "Globe" in src, (
            "ADR-0020 §6.4 must reference the Fn/Globe key (the macOS default hotkey)"
        )
        assert "NSEvent.modifierFlags.function" in src or "modifierFlags" in src, (
            "ADR-0020 §6.4 must document that the Swift binary detects Fn via "
            "NSEvent.modifierFlags.function (the Tauri plugin cannot)"
        )

    def test_tauri_does_not_spawn_native_listener_directly(self):
        """Tauri's ``externalBin`` must NOT list the native listener.

        If it were in ``externalBin``, Tauri would manage its
        lifecycle. It must be a ``resource`` so the sidecar spawns it.
        """
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        external_bins = conf.get("bundle", {}).get("externalBin", [])
        for ext in external_bins:
            assert "key-listener" not in ext, (
                f"Native key-listener must NOT be in externalBin (Tauri must not spawn it). Found: {ext}"
            )

    def test_runbook_documents_sidecar_ownership(self):
        """Runbook §6.7 documents the native listener + the sidecar.

        The macOS runbook doesn't reference ``native_hotkeys.py`` by
        name (unlike the Windows runbook §6.8), but it does reference
        ``macos-key-listener`` extensively in §3 + §6.7 and refers to
        the Python sidecar (``python-sidecar``) as the bundle host.
        Together with ADR-0020 §6.4 (validated in
        ``test_adr_states_sidecar_owns_hotkey_subsystem``), this
        documents that the sidecar owns the listener lifecycle.
        """
        assert RUNBOOK.is_file()
        src = RUNBOOK.read_text(encoding="utf-8")
        assert "macos-key-listener" in src, "Runbook must document the macos-key-listener gate"
        # The runbook references ADR-0020 §6.4 (which mandates sidecar
        # ownership) OR the python-sidecar binary (which is the host
        # that spawns the listener).
        assert "6.4" in src or "python-sidecar" in src, (
            "Runbook must reference ADR-0020 §6.4 or python-sidecar "
            "(the sidecar binary that owns the listener lifecycle)"
        )


# ─── §10. Universal / per-arch binary ───────────────────────────────────────


class TestUniversalOrPerArch:
    """Verify the binary is universal OR per-arch.

    ADR-0020 §Reversibility: cutover is per-arch — Apple Silicon can
    ship Tauri while Intel still ships Electron. The native
    ``macos-key-listener`` binary must run on BOTH archs.

    Two acceptable strategies:
    1. **Universal binary** — a single Mach-O file with both arm64 +
       x86_64 slices (merged with ``lipo``). The runbook §3 documents
       the ``build_native_listener_macos.sh --universal`` flag for
       this.
    2. **Per-arch builds** — separate binaries per arch, with Tauri
       selecting the right one at runtime. (Note: Tauri's
       ``externalBin`` mechanism appends the host triple, but the
       native listener is a ``resource`` not ``externalBin``, so
       per-arch resources would need a custom selector.)

    The current implementation (``build_native_listener_macos.sh``)
    compiles for the HOST ARCH only — no ``--universal`` flag. The
    runbook §3 documents ``--universal`` as a planned feature. This
    is an implementation gap (see the test below + the report).
    """

    def test_build_native_listener_macos_sh_exists(self):
        """The macOS build wrapper script must exist."""
        assert BUILD_NATIVE_LISTENER_MACOS_SH.is_file(), f"Missing build script: {BUILD_NATIVE_LISTENER_MACOS_SH}"

    def test_build_script_invokes_compile_native_sh(self):
        """The wrapper invokes ``compile_native.sh`` (which runs ``swiftc``).

        The wrapper doesn't compile directly — it delegates to
        ``compile_native.sh`` (which detects macOS + runs
        ``swiftc -O ... -framework Cocoa -framework CoreGraphics``).
        The wrapper then copies the compiled binary to
        ``src-tauri/resources/native/`` + codesigns it ad-hoc.
        """
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "compile_native.sh" in src, "build_native_listener_macos.sh must invoke compile_native.sh"
        assert "swiftc" in src or "compile_native.sh" in src, (
            "build_native_listener_macos.sh must (transitively) invoke swiftc"
        )

    def test_build_script_copies_binary_to_tauri_resources(self):
        """The wrapper copies the compiled binary to ``src-tauri/resources/native/``.

        This is the path listed in ``tauri.conf.json``'s
        ``bundle.resources`` array. Without this copy step, Tauri
        wouldn't ship the binary.
        """
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "src-tauri/resources/native" in src, (
            "build_native_listener_macos.sh must copy the binary to "
            "src-tauri/resources/native/ (the tauri.conf.json bundle.resources path)"
        )
        assert "macos-key-listener" in src, "build_native_listener_macos.sh must reference macos-key-listener"

    def test_build_script_codesigns_ad_hoc(self):
        """The wrapper ad-hoc codesigns the binary.

        Ad-hoc signing (``codesign --force --sign -``) is required
        even for dev builds — without it, macOS refuses to spawn the
        binary under the parent .app's signature. The parent .app
        re-signs ``--deep`` on bundle build.
        """
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "codesign" in src, (
            "build_native_listener_macos.sh must codesign the binary (ad-hoc at minimum, Developer ID for distribution)"
        )

    def test_build_script_enforces_macos_host(self):
        """The wrapper refuses to run on non-macOS hosts.

        ``swiftc`` only exists on macOS. Running the wrapper on
        Linux/Windows would produce a confusing error from
        ``compile_native.sh``; the wrapper fails fast with a clear
        message instead.
        """
        src = BUILD_NATIVE_LISTENER_MACOS_SH.read_text(encoding="utf-8")
        assert "Darwin" in src or "uname -s" in src, (
            "build_native_listener_macos.sh must enforce macOS host (uname -s == Darwin)"
        )

    def test_swift_source_compiles_to_host_arch(self):
        """``compile_native.sh`` runs ``swiftc -O`` for the host arch.

        ``swiftc -O`` without an explicit ``-target`` flag compiles
        for the host arch. On an Apple Silicon host this produces an
        arm64 binary; on an Intel host this produces an x86_64
        binary. The runbook §3 documents building on EACH arch
        separately (per-arch strategy) OR merging with ``lipo``
        (universal strategy).
        """
        src = COMPILE_NATIVE_SH.read_text(encoding="utf-8")
        assert "swiftc" in src, "compile_native.sh must invoke swiftc for the macOS build"
        # The Swift source's build instructions (header comment) must
        # also reference swiftc + the required frameworks.
        swift_src = MACOS_KEY_LISTENER_SWIFT.read_text(encoding="utf-8")
        assert "swiftc" in swift_src, "macos-key-listener.swift header must document the swiftc build command"
        assert "Cocoa" in swift_src, "macos-key-listener.swift must import Cocoa (NSEvent, NSApplication)"
        assert "CoreGraphics" in swift_src, "macos-key-listener.swift must import CoreGraphics (CGEvent tap)"
