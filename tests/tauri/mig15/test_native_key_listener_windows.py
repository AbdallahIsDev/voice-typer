"""MIG-1.5 Phase 0-W Gate Check 9 — native ``windows-key-listener.exe``.

Validates that the native Windows hotkey binary (built by
``scripts/build/compile_native.ps1`` from
``voice_typer/server/native/windows-key-listener.c``) is correctly
bundled, discovered, spawned, and wire-protocol compatible with the
Python sidecar's ``native_hotkeys.SubprocessHotkeyBackend`` /
``WindowsHookHotkey``.

ADR-0020 §6.4 mandates KEEPING the native binary (do NOT switch to
``tauri-plugin-global-shortcut`` — it lacks key suppression +
modifier-only hotkeys). The binary is spawned as a subprocess by the
PYTHON SIDECAR (not by the Tauri host); Tauri only ships it as a
``bundle.resource``. The sidecar discovers it via
``VOICE_TYPER_NATIVE_DIR`` (set by the Tauri host to
``resourceDir/native/``) or falls through to dev-mode / PyInstaller
paths. See ``native_hotkeys.get_native_binary_path`` for the full
6-step lookup chain.

These tests run on any platform (Linux sandbox included) — they mock
``subprocess.Popen``, ``pathlib.Path.exists``, and the
``is_windows()``/``is_macos()``/``is_linux()`` platform predicates so
the Windows code path is exercised without a real Windows host. The
actual ``WH_KEYBOARD_LL`` hook + key suppression + modifier-only
detection can only be validated on a real Windows host — see the
"VALIDATE ON WINDOWS HOST" block below.

VALIDATE ON WINDOWS HOST:
    1. Launch Voice Typer
    2. Press F8 (default dictation hotkey) — verify dictation starts
    3. Press F8 again — verify dictation stops + transcribed text pastes
    4. Press ESC — verify dictation cancels
    5. Check log for:
       - "[HOTKEY] spawning native listener: windows-key-listener.exe"
       - "[HOTKEY] native listener ready (pid=...)"
       - "[HOTKEY] dictation hotkey pressed (F8)"
    6. Verify the hotkey is SUPPRESSED (F8 doesn't reach the foreground
       app — e.g. doesn't trigger F8 in browser dev tools)
    7. Verify modifier-only hotkeys work (e.g. Caps Lock alone, if
       configured)
    Expected: hotkey responds within 50ms; key suppression works;
    modifier-only works

    PowerShell verification commands (from runbook §6.8):

        tasklist | findstr /I "windows-key-listener"
        # Expected: windows-key-listener.exe in the process list while
        # the app is running.

        Get-Content "$env:APPDATA\\voice-typer\\logs\\sidecar.log" -Tail 200 |
            Select-String "hotkey|native|KEY_DOWN|toggle"

    Pass criteria (runbook §6.8):
        - ``windows-key-listener.exe`` is in Task Manager while the app
          runs.
        - Pressing the configured hotkey toggles dictation (bubble
          appears + recording starts; second press stops + pastes).
        - No ``native binary not found`` errors in ``sidecar.log``.

Wire protocol note (implementation gap — see report):
    The task brief mentioned JSON-lines ``{"event":"hotkey","id":"dictation"}``,
    but the ACTUAL wire protocol (native_hotkeys.py module docstring +
    windows-key-listener.c) is line-delimited TEXT, not JSON:

        READY                  # emitted once after init succeeds
        KEY_DOWN:<Name>        # non-modifier key pressed
        KEY_UP:<Name>          # non-modifier key released
        MOD_DOWN:<Name>        # modifier pressed
        MOD_UP:<Name>          # modifier released
        ERROR:<message>        # fatal error

    These tests validate the ACTUAL text wire protocol, not the
    hypothetical JSON form.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Project paths ──────────────────────────────────────────────────────────

# test file: <root>/tests/tauri/mig15/test_native_key_listener_windows.py
# parents[0]=mig15, [1]=tauri, [2]=tests, [3]=voice-typer (project root)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
COMPILE_NATIVE_PS1 = PROJECT_ROOT / "scripts" / "build" / "compile_native.ps1"
WINDOWS_KEY_LISTENER_C = PROJECT_ROOT / "voice_typer" / "server" / "native" / "windows-key-listener.c"
# Phase 4.5 /  — ``native_hotkeys.py`` was split into a package at
# ``voice_typer/server/native_hotkeys/`` with one submodule per concern
# (base / mac_backend / windows_backend / linux_backend / factory / ...).
# The ``__init__.py`` re-exports every public name.  Tests below that
# source-inspect specific symbols now point at the submodule that actually
# defines them (e.g. ``WindowsHookHotkey`` → ``windows_backend.py``).
NATIVE_HOTKEYS_PKG = PROJECT_ROOT / "voice_typer" / "server" / "native_hotkeys"
NATIVE_HOTKEYS_PY = NATIVE_HOTKEYS_PKG / "__init__.py"
NATIVE_HOTKEYS_BASE_PY = NATIVE_HOTKEYS_PKG / "base.py"
NATIVE_HOTKEYS_WINDOWS_PY = NATIVE_HOTKEYS_PKG / "windows_backend.py"
NATIVE_HOTKEYS_MAC_PY = NATIVE_HOTKEYS_PKG / "mac_backend.py"
NATIVE_HOTKEYS_LINUX_PY = NATIVE_HOTKEYS_PKG / "linux_backend.py"
ADR_0020 = PROJECT_ROOT / "docs" / "adr" / "0020-desktop-runtime-migration-analysis.md"
RUNBOOK = PROJECT_ROOT / "docs" / "migration" / "windows-validation-runbook.md"

NATIVE_RESOURCE_PATH = "resources/native/windows-key-listener.exe"


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def windows_env(monkeypatch):
    """Patch the platform predicates + sys.platform to look like Windows.

    ``WindowsHookHotkey._validate_platform`` calls ``is_windows()``
    from the ``native_hotkeys`` module namespace, and
    ``get_native_binary_path`` reads ``sys.platform`` to pick the
    binary name from ``_BINARY_NAMES``. Both must be patched together
    for the Windows code path to be exercisable from a Linux sandbox.
    """
    from voice_typer.server import native_hotkeys

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
    return native_hotkeys


# ─── §1. Tauri bundle resources ─────────────────────────────────────────────


class TestTauriBundleResources:
    """Verify ``tauri.conf.json`` ships the native listener as a resource."""

    def test_tauri_conf_json_exists(self):
        """The Tauri config file must exist (sanity check)."""
        assert TAURI_CONF.is_file(), f"Missing Tauri config: {TAURI_CONF}"

    def test_tauri_conf_bundles_windows_native_listener(self):
        """``resources/native/windows-key-listener.exe`` must be in bundle.resources.

        ADR-0020 §7 lists this exact path in the ``bundle.resources``
        array. Without it, the production MSI/NSIS bundle won't ship
        the binary and the sidecar will log ``native binary not found``
        at startup (runbook §6.8 fail scenario).
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
            assert "windows-key-listener" not in ext, (
                f"windows-key-listener must NOT be in externalBin "
                f"(Tauri must not spawn it — ADR-0020 §6.4). Found: {ext}"
            )

    def test_tauri_conf_also_bundles_macos_and_linux_listeners(self):
        """All three platform binaries are bundled (cross-platform ship).

        ADR-0020 §7 lists all three. This is a sanity check that the
        Windows entry isn't alone — confirms the resources array is
        the cross-platform native-listener block.
        """
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/macos-key-listener" in resources
        assert "resources/native/linux-key-listener" in resources


# ─── §2. Subprocess spawn ───────────────────────────────────────────────────


class TestSubprocessSpawn:
    """Verify ``SubprocessHotkeyBackend._spawn_process`` uses ``subprocess.Popen`` correctly."""

    def test_spawn_uses_subprocess_popen(self, windows_env, monkeypatch, tmp_path):
        """``_spawn_process`` must call ``subprocess.Popen`` (not ``run``/``call``/``check_output``).

        ``Popen`` is required because the sidecar needs a long-lived
        child process whose stdout is streamed line-by-line by the
        reader thread.
        """
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
        """The hotkey spec string (e.g. ``<f8>``) is passed as ``argv[1]``.

        ``_spawn_process`` builds ``cmd = [str(binary_path), self.hotkey_str]``.
        The native binary parses ``argv[1]`` to know which hotkey to
        watch + suppress. This is NOT stdin, NOT JSON — it's a plain
        pynput-style spec string as argv[1].
        """
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
        """stdout=PIPE, stderr=STDOUT, stdin=DEVNULL.

        stdout MUST be piped — the reader thread reads line-delimited
        wire-protocol events (READY / KEY_DOWN / MOD_DOWN / ERROR) from
        it. stderr is redirected to stdout so error output is visible
        in the same stream. stdin is DEVNULL (the binary doesn't read
        commands from stdin — it's event-driven via the hook).
        """
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
        assert kwargs.get("stdout") == subprocess.PIPE, (
            "stdout must be PIPE — reader thread streams wire-protocol lines"
        )
        assert kwargs.get("stderr") == subprocess.STDOUT, (
            "stderr must redirect to stdout so errors surface in the wire stream"
        )
        assert kwargs.get("stdin") == subprocess.DEVNULL, (
            "stdin must be DEVNULL — the binary is event-driven, not command-driven"
        )

    def test_spawn_uses_create_no_window_on_windows(self, windows_env, monkeypatch, tmp_path):
        """On Windows, ``creationflags`` includes ``CREATE_NO_WINDOW``.

        Without this, spawning the .exe would pop up a console window
        on every launch (and on every restart-after-crash). The
        ``is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW")``
        guard means Linux test runs pass ``creationflags=0``.
        """
        backend = windows_env.WindowsHookHotkey("<f8>")
        fake_bin = tmp_path / "windows-key-listener.exe"
        fake_bin.write_text("dummy")
        backend._binary_path = fake_bin
        backend._stop_event.set()

        # CREATE_NO_WINDOW only exists on Windows. Inject a sentinel so
        # the hasattr() check passes and we can verify the flag is used.
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
        """If ``Popen`` raises ``OSError``, ``_spawn_process`` raises ``RuntimeError``.

        This covers the "binary disappeared mid-restart" path — the
        reader loop catches the RuntimeError and notifies the adapter
        via ``_on_permanent_failure_callback``.
        """
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


# ─── §3. Binary discovery ───────────────────────────────────────────────────


class TestBinaryDiscovery:
    """Verify the binary is discovered via ``VOICE_TYPER_NATIVE_DIR`` or dev/bundle paths."""

    def test_voice_typer_native_dir_lookup_finds_windows_binary(self, windows_env, monkeypatch, tmp_path):
        """``VOICE_TYPER_NATIVE_DIR`` (Tauri dev/prod) points at the bundle's native dir.

        ADR-0020 §7: Tauri sets ``VOICE_TYPER_NATIVE_DIR`` to
        ``resourceDir/native/``. The sidecar's
        ``get_native_binary_path`` checks this env var (step 2 of the
        6-step lookup chain) and returns
        ``<dir>/windows-key-listener.exe`` on Windows.
        """
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
        """``VOICE_TYPER_NATIVE_BINARY`` (single-file override) beats ``_DIR``.

        Lookup step 1 (explicit single-binary override) beats step 2
        (Tauri resource dir). This lets a developer point at a custom
        build without unset-ing the Tauri env var.
        """
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
        """Production layout: ``resourceDir/native/windows-key-listener.exe``.

        Tauri extracts ``bundle.resources`` entries to ``resourceDir``
        preserving the relative path, so
        ``resources/native/windows-key-listener.exe`` (the tauri.conf
        entry) lands at ``<resourceDir>/resources/native/windows-key-listener.exe``.

        The runbook §6.8 fail scenario ("native binary not found")
        happens when Tauri didn't set ``VOICE_TYPER_NATIVE_DIR`` or
        the file isn't in the bundle. This test simulates the
        happy-path layout.
        """
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
        """Without env vars, lookup falls through to the dev source-tree path.

        Lookup step 3/4: ``voice_typer/server/native/<binary>``. In dev
        mode (running from source), the freshly-compiled binary sits
        here. This test mocks ``Path.is_file`` so the dev path
        "exists" without requiring a real compiled binary in the
        sandbox.
        """
        monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
        monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

        # The dev-mode path is <module_dir>/native/windows-key-listener.exe.
        # After the Phase 4.5 split, the package lives at
        # ``voice_typer/server/native_hotkeys/``; production code in
        # ``native_hotkeys/binary_path.py`` resolves the dev path via
        # ``Path(__file__).resolve().parent.parent / "native"`` — i.e.
        # the package's *parent* directory (``voice_typer/server/``).
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

    See the ``native_hotkeys`` module docstring + the
    ``windows-key-listener.c`` header comment for the canonical
    definition.
    """

    def test_ready_line_sets_ready_event(self, windows_env):
        """``READY`` unblocks ``start()`` (which waits on ``_ready_event``)."""
        backend = windows_env.WindowsHookHotkey("<f8>")
        assert not backend._ready_event.is_set()
        backend._handle_line("READY")
        assert backend._ready_event.is_set()
        assert not backend._failed

    def test_error_line_marks_failed_and_unblocks(self, windows_env):
        """``ERROR:<msg>`` marks the backend failed + unblocks ``start()``.

        The adapter's ``_on_error_callback`` is then invoked so the
        sidecar can classify the error (e.g. show a permission
        prompt).
        """
        backend = windows_env.WindowsHookHotkey("<f8>")
        backend._handle_line("ERROR:hook install denied")
        assert backend._failed
        assert backend._error_message == "hook install denied"
        assert backend._ready_event.is_set()  # unblocks start()

    def test_key_down_f8_fires_dictation_callback(self, windows_env):
        """``KEY_DOWN:F8`` fires the press callback for the ``<f8>`` hotkey.

        This is the primary dictation-toggle path: the native binary's
        ``WH_KEYBOARD_LL`` hook detects F8, emits ``KEY_DOWN:F8`` on
        stdout, the reader thread hands it to ``_handle_line``, which
        matches it against the registered spec and fires the callback.
        """
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

        # V alone — no fire.
        backend._handle_line("KEY_DOWN:V")
        assert fired == []

        # Hold Ctrl+Alt, then press V — fire.
        backend._handle_line("MOD_DOWN:Ctrl")
        backend._handle_line("MOD_DOWN:Alt")
        backend._handle_line("KEY_DOWN:V")
        assert fired == ["press"]


# ─── §5. Key suppression (WH_KEYBOARD_LL returns non-zero) ──────────────────


class TestKeySuppression:
    """Verify the C source implements key suppression.

    ADR-0020 §6.4 table: Windows key suppression works because the
    ``WH_KEYBOARD_LL`` callback returns non-zero (instead of calling
    ``CallNextHookEx``), which swallows the keystroke so the
    foreground app never sees it. This is the critical feature
    ``tauri-plugin-global-shortcut`` lacks.

    These are source-inspection tests — the actual suppression can
    only be validated on a Windows host (runbook §6.8 step 6).
    """

    def test_windows_key_listener_c_source_exists(self):
        assert WINDOWS_KEY_LISTENER_C.is_file(), f"Missing C source: {WINDOWS_KEY_LISTENER_C}"

    def test_c_source_uses_wh_keyboard_ll_hook(self):
        """The C source must install a ``WH_KEYBOARD_LL`` low-level hook.

        ADR-0020 §6.4: "Win ✅ (``WH_KEYBOARD_LL`` returns non-zero)".
        ``WH_KEYBOARD_LL`` is the only Win32 hook that allows
        out-of-process key suppression. ``RegisterHotKey`` and
        ``GetAsyncKeyState`` polling cannot suppress.
        """
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "WH_KEYBOARD_LL" in src, (
            "windows-key-listener.c must use WH_KEYBOARD_LL (the only "
            "Win32 hook that supports out-of-process key suppression)"
        )
        # SetWindowsHookEx installs the hook.
        assert "SetWindowsHookEx" in src, "windows-key-listener.c must call SetWindowsHookEx to install the hook"

    def test_c_source_has_should_suppress_keydown_function(self):
        """The C source has a ``should_suppress_keydown`` decision function.

        This function returns 1 (swallow) or 0 (pass through) for each
        keydown. The hook proc checks it before deciding whether to
        call ``CallNextHookEx`` (pass through) or return non-zero
        (swallow).
        """
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "should_suppress_keydown" in src, (
            "windows-key-listener.c must define should_suppress_keydown() — "
            "the decision function that returns 1 to swallow a keystroke"
        )

    def test_c_source_swallows_matched_key_with_return_1(self):
        """The hook proc returns 1 (non-zero) to suppress matched keystrokes.

        Returning non-zero from a ``WH_KEYBOARD_LL`` callback (instead
        of calling ``CallNextHookEx``) prevents the keystroke from
        reaching the foreground app. This is the key-suppression
        mechanism ADR-0020 §6.4 preserves.
        """
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        # The hook proc checks should_suppress_keydown and returns 1.
        assert "should_suppress_keydown" in src
        assert "return 1" in src, (
            "The WH_KEYBOARD_LL hook proc must `return 1` (non-zero) to "
            "suppress a matched keystroke — calling CallNextHookEx would "
            "pass it through to the foreground app"
        )

    def test_c_source_caps_lock_suppression(self):
        """Caps Lock hotkey suppresses the OS caps-state toggle.

        When the hotkey is ``<caps_lock>``, the binary swallows the
        CapsLock keydown so Windows doesn't toggle caps state. This is
        the ``is_caps_lock_only && vk == VK_CAPITAL`` branch in
        ``should_suppress_keydown``.
        """
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "VK_CAPITAL" in src or "VK_CAPITAL" in src, (
            "windows-key-listener.c must reference VK_CAPITAL for Caps Lock suppression"
        )
        assert "caps_lock" in src or "CapsLock" in src, (
            "windows-key-listener.c must handle the caps_lock / CapsLock hotkey"
        )


# ─── §6. Modifier-only hotkeys ──────────────────────────────────────────────


class TestModifierOnlyHotkeys:
    """Verify the backend supports modifier-only hotkeys (e.g. ``<alt>``, ``<caps_lock>``).

    ADR-0020 §6.4 table: "Modifier-only hotkeys (bare ``Caps Lock``,
    ``Alt``, ``Fn``) | Win ✅". The Tauri plugin only partially
    supports these on Windows. This is the second critical feature
    preserved by keeping the native binary.
    """

    def test_modifier_only_alt_fires_on_mod_down(self, windows_env):
        """``<alt>`` fires on ``MOD_DOWN:Alt`` (no main key needed).

        The matching logic in ``_try_match`` checks
        ``parsed["is_modifier_only"]`` and fires when the held
        modifiers exactly equal the required set.
        """
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
        assert fired == []  # NOT fired — Ctrl is held too

    def test_caps_lock_hotkey_fires_on_key_down(self, windows_env):
        """``<caps_lock>`` is a single-key hotkey that fires on ``KEY_DOWN:CapsLock``.

        Note: CapsLock is a non-modifier key in this codebase (not a
        modifier-only hotkey). The "modifier-only" terminology refers
        to ``<alt>``, ``<ctrl>``, ``<shift>``, ``<win>``. CapsLock is
        special because the C binary suppresses the OS caps-toggle —
        see ``TestKeySuppression.test_c_source_caps_lock_suppression``.
        """
        backend = windows_env.WindowsHookHotkey("<caps_lock>")
        assert backend._parsed is not None
        assert backend._parsed["main_key"] == "CapsLock"
        assert backend._parsed["is_caps_lock"] is True

        fired: list[str] = []
        backend._callback = lambda: fired.append("press")
        backend._handle_line("KEY_DOWN:CapsLock")
        assert fired == ["press"]

    def test_win_modifier_canonicalizes_to_cmd(self, windows_env):
        """``<win>`` (Windows key) is accepted as a modifier-only hotkey.

        The wire protocol emits ``MOD_DOWN:Win`` on Windows; the
        matching logic canonicalizes ``Win`` → ``cmd`` so the same
        spec works cross-platform (``<cmd>`` on macOS, ``<super>`` on
        Linux).
        """
        backend = windows_env.WindowsHookHotkey("<win>")
        assert backend._parsed is not None
        assert "cmd" in backend._parsed["modifiers"]
        assert backend._parsed["is_modifier_only"] is True

    def test_c_source_supports_modifier_only_specs(self):
        """The C source parses modifier-only specs (empty main key).

        ``windows-key-listener.c`` has an ``is_modifier_only`` field
        in its ``HotkeySpec`` struct and a parsing branch for specs
        like ``<alt>`` (no main key).
        """
        src = WINDOWS_KEY_LISTENER_C.read_text(encoding="utf-8")
        assert "is_modifier_only" in src, (
            "windows-key-listener.c must support modifier-only hotkeys (is_modifier_only field in HotkeySpec struct)"
        )


# ─── §7. Sidecar ownership (NOT the Tauri host) ─────────────────────────────


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

    def test_native_hotkeys_module_defines_subprocess_backend(self):
        """``native_hotkeys`` defines ``SubprocessHotkeyBackend`` + ``WindowsHookHotkey``.

        After the Phase 4.5 split, ``SubprocessHotkeyBackend`` lives in
        ``native_hotkeys/base.py`` and ``WindowsHookHotkey`` lives in
        ``native_hotkeys/windows_backend.py``.  ``subprocess.Popen`` is
        invoked from ``base.py``'s ``_spawn_process``.
        """
        base_src = NATIVE_HOTKEYS_BASE_PY.read_text(encoding="utf-8")
        win_src = NATIVE_HOTKEYS_WINDOWS_PY.read_text(encoding="utf-8")
        assert "class SubprocessHotkeyBackend" in base_src, (
            "native_hotkeys/base.py must define SubprocessHotkeyBackend (the base class "
            "that spawns the native binary via subprocess.Popen)"
        )
        assert "class WindowsHookHotkey" in win_src, (
            "native_hotkeys/windows_backend.py must define WindowsHookHotkey (the Windows subclass)"
        )
        assert "subprocess.Popen" in base_src, "native_hotkeys/base.py must use subprocess.Popen to spawn the binary"

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
        """Runbook §6.8 documents the native listener spawns from the sidecar."""
        assert RUNBOOK.is_file()
        src = RUNBOOK.read_text(encoding="utf-8")
        assert "windows-key-listener" in src, "Runbook §6.8 must document the windows-key-listener gate"
        # The runbook references native_hotkeys.py (the sidecar module).
        assert "native_hotkeys" in src, (
            "Runbook must reference native_hotkeys.py (the sidecar module that owns the listener)"
        )


# ─── §8. Build script (compile_native.ps1) ──────────────────────────────────


class TestCompileNativeScript:
    """Verify ``compile_native.ps1`` exists and compiles the C binary.

    Source-inspection tests — the actual compilation requires a
    Windows host with MSVC or MinGW installed.
    """

    def test_compile_native_ps1_exists(self):
        """The PowerShell build script must exist."""
        assert COMPILE_NATIVE_PS1.is_file(), f"Missing compile_native.ps1: {COMPILE_NATIVE_PS1}"

    def test_compile_native_ps1_compiles_c_source(self):
        """The script compiles ``windows-key-listener.c`` → ``windows-key-listener.exe``."""
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "windows-key-listener.c" in src, "compile_native.ps1 must reference the C source file"
        assert "windows-key-listener.exe" in src, "compile_native.ps1 must produce windows-key-listener.exe"

    def test_compile_native_ps1_supports_msvc_and_mingw(self):
        """The script supports both MSVC (``cl.exe``) and MinGW (``gcc``).

        ADR-0020 §3: the toolchain is ``stable-x86_64-pc-windows-msvc``
        (MSVC ABI). MinGW is supported as a fallback for developers
        without Visual Studio Build Tools.
        """
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "cl.exe" in src or "cl" in src, "compile_native.ps1 must support MSVC (cl.exe)"
        assert "gcc" in src or "gcc.exe" in src, "compile_native.ps1 must support MinGW (gcc) as a fallback"

    def test_compile_native_ps1_links_user32(self):
        """The script links ``user32.lib`` (required for ``WH_KEYBOARD_LL``).

        ``SetWindowsHookEx``, ``CallNextHookEx``, ``UnhookWindowsHookEx``
        all live in user32.dll. Without linking user32, the binary
        won't build.
        """
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "user32" in src.lower(), "compile_native.ps1 must link user32.lib (required for WH_KEYBOARD_LL APIs)"

    def test_compile_native_ps1_has_check_mode(self):
        """The script supports a ``-Check`` mode for toolchain verification.

        Used by CI / dev setup to fail fast if neither cl.exe nor
        MinGW gcc is on PATH, before attempting a full build.
        """
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "Check" in src, "compile_native.ps1 must support a -Check mode for toolchain verification"

    def test_compile_native_ps1_sets_win32_winnt_vista(self):
        """The script defines ``_WIN32_WINNT=0x0600`` (Vista+).

        ``WH_KEYBOARD_LL`` requires Windows Vista+ (0x0600). The C
        source header comment confirms this. The build script must
        pass the define so the Win32 headers expose the API.
        """
        src = COMPILE_NATIVE_PS1.read_text(encoding="utf-8")
        assert "_WIN32_WINNT" in src, "compile_native.ps1 must define _WIN32_WINNT (WH_KEYBOARD_LL requires Vista+)"
