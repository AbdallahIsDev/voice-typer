"""MIG-1.6 Phase 0-M Gate Check 8 — prewarm LaunchAgent validation (macOS).

This test file validates the *structure* of the macOS prewarm scheduling
path from the Linux sandbox. The actual ``VALIDATE ON MACOS HOST``
commands documented below MUST be run on a real macOS host — these
tests only verify the code paths they reference behave as ADR-0020 §5
and the macos-validation-runbook §6.6/§6.7 require.

Coverage map (8 test functions, one per gate-check criterion):

1. ``test_resolve_prewarm_exe_finds_frozen_exe_on_macos`` (parametrized
   over both arches) — ``prewarm_resolver.resolve_prewarm_exe()`` finds
   the frozen ``prewarm-x86_64-apple-darwin`` /
   ``prewarm-aarch64-apple-darwin`` on macOS via the .app bundle
   resource path (``<app>.app/Contents/Resources/prewarm-<triple>``).
2. ``test_resolver_priority_env_var_then_bundle_resource_then_dev_fallback``
   — resolver priority order: ``VOICE_TYPER_PREWARM_EXE`` env var →
   Tauri bundle resource path (.app/Contents/Resources/) → dev fallback
   (Python module invocation).
3. ``test_macos_plist_path_under_library_launchagents`` —
   ``prewarm_scheduler_posix._macos_plist_path()`` returns
   ``~/Library/LaunchAgents/com.voicetyper.prewarm.plist`` (per-user,
   no admin elevation required).
4. ``test_plist_uses_run_at_load_true`` — the LaunchAgent plist
   ``_build_macos_plist()`` emits ``RunAtLoad=true`` (fires at every
   login — the macOS equivalent of Windows' LogonTrigger).
5. ``test_plist_program_arguments_uses_frozen_exe_not_python_module`` —
   when the frozen exe is available (env var set + resolver finds it),
   the plist's ``ProgramArguments`` array points at the frozen
   ``prewarm-<triple>`` binary directly (NOT
   ``python -m voice_typer.server.prewarm``). Mocks
   ``prewarm_resolver.resolve_prewarm_exe`` + ``os.environ``.
6. ``test_register_prewarm_macos_loads_plist_via_launchctl`` —
   ``_register_prewarm_macos()`` writes the plist to disk AND invokes
   ``launchctl load <plist>`` (the legacy API; ``launchctl bootstrap``
   is the newer macOS 10.10+ equivalent — both are acceptable, the
   production code uses ``launchctl load`` for compatibility). Mocks
   ``subprocess.run`` + ``pathlib.Path.home`` so no real launchctl call
   is made and no real home directory is touched.
7. ``test_prewarm_warms_ct2_cache_via_file_reads_not_imports`` —
   the warming pipeline reads CT2 model files via ``_warm_file``
   (sequential file reads), NOT by importing ``ctranslate2``
   (ADR-0020 §5: "frozen the same Nuitka way… warms the OS file cache").
8. ``test_prewarm_run_writes_pid_file_and_signals_completion_event`` —
   ``run()`` writes a PID file + invokes the completion-event lifecycle
   (``_create_completion_event`` / ``_signal_completion_event`` /
   ``_close_completion_event``) so the app's ``wait_for_prewarm()`` can
   wait without polling. On macOS the completion event is a no-op
   (returns ``None``) — the PID file is the cross-platform handshake.

Platform checks are monkeypatched to ``darwin`` so the macOS code paths
are exercised on the Linux test host. ``subprocess.run`` is mocked so
no real ``launchctl`` call is made.

VALIDATE ON MACOS HOST:
1. Launch Voice Typer (creates the LaunchAgent plist)
2. Run: launchctl list | grep voicetyper
   Expected: com.voicetyper.prewarm entry with PID
3. Examine ~/Library/LaunchAgents/com.voicetyper.prewarm.plist
   Expected: RunAtLoad=true; ProgramArguments=prewarm-<arch>-apple-darwin
4. Log out + log back in (OR run: launchctl kickstart -k gui/$(id -u)/com.voicetyper.prewarm)
5. Check ~/Library/Logs/voice-typer/prewarm.log for:
   - "[PREWARM] starting (trigger=...)"
   - "[PREWARM] Warming model: small.en"
   - "[PREWARM] complete (X.Xs)"
6. Verify CT2 model files are warm in ~/Library/Application Support/voice-typer/huggingface/hub/
Expected: prewarm completes within 30s of login; model cache warm
(Same behavior on both x86_64 and aarch64 — the arch is implicit in the binary name.)

References:
- ADR-0020 §5 (Prewarm packaging — frozen same Nuitka way as sidecar,
  bundled as bundle.resource NOT externalBin, launched by the platform
  scheduler via resolve_prewarm_exe()).
- docs/migration/macos-validation-runbook.md §6.6 (Prewarm LaunchAgent,
  gate point 7, BOTH arches).
- voice_typer/server/prewarm_resolver.py
- voice_typer/server/prewarm_scheduler_posix.py
- voice_typer/server/prewarm.py
- scripts/build/build_prewarm_macos.sh
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

# Pre-import xml.sax.saxutils BEFORE the autouse fixture sets
# sys.platform='darwin'. On macOS, urllib.request (imported at module
# level by xml.sax.saxutils) tries to `from _scproxy import ...` — a
# macOS-only stdlib extension that doesn't exist on the Linux test host.
# Pre-importing while sys.platform is still 'linux' caches the module in
# sys.modules so the `_scproxy` import is never re-triggered when
# `_build_macos_plist()` calls `from xml.sax.saxutils import escape`
# under the mocked 'darwin' platform.
import xml.sax.saxutils  # noqa: F401  (pre-import side effect)
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── macOS platform mocking fixture ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _force_macos(monkeypatch):
    """Pretend we're on macOS for every test in this module.

    The macOS prewarm scheduling path (``prewarm_scheduler_posix.py``)
    is guarded by ``is_macos()``. On the Linux test host, we
    monkeypatch ``sys.platform`` + the ``is_macos`` / ``is_windows`` /
    ``is_linux`` helpers (both in ``platform_utils`` and the bound
    names imported into ``prewarm_scheduler_posix`` /
    ``prewarm_resolver`` / ``prewarm``) to exercise the macOS code
    paths.

    ``prewarm_scheduler_posix.is_supported()`` is also mocked to
    ``True`` so the ``is_supported()``-guarded code paths execute.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    from voice_typer.server import (
        platform_utils,
        prewarm,
        prewarm_resolver,
        prewarm_scheduler_posix,
    )

    # Patch the source module.
    monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
    monkeypatch.setattr(platform_utils, "is_macos", lambda: True)
    monkeypatch.setattr(platform_utils, "is_linux", lambda: False)
    # Patch the bound names imported into each consumer module.
    for mod in (prewarm_scheduler_posix, prewarm_resolver, prewarm):
        if hasattr(mod, "is_windows"):
            monkeypatch.setattr(mod, "is_windows", lambda: False)
        if hasattr(mod, "is_macos"):
            monkeypatch.setattr(mod, "is_macos", lambda: True)
        if hasattr(mod, "is_linux"):
            monkeypatch.setattr(mod, "is_linux", lambda: False)
    # is_supported() checks is_macos() or is_linux() — both mocked, so
    # this is redundant but explicit.
    monkeypatch.setattr(prewarm_scheduler_posix, "is_supported", lambda: True)
    # Remove _MEIPASS so the resolver doesn't append a _MEIPASS candidate
    # (which could shadow the paths we set up in tmp_path).
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    # Clear the prewarm env overrides so tests start from a clean slate.
    monkeypatch.delenv("VOICE_TYPER_PREWARM_EXE", raising=False)
    monkeypatch.delenv("TAURI_SIDECAR", raising=False)


# ─── §1. prewarm_resolver.resolve_prewarm_exe() ──────────────────────────


@pytest.mark.parametrize(
    "machine,expected_triple",
    [
        ("x86_64", "x86_64-apple-darwin"),
        ("arm64", "aarch64-apple-darwin"),
    ],
    ids=["intel-x86_64", "apple-silicon-aarch64"],
)
def test_resolve_prewarm_exe_finds_frozen_exe_on_macos(monkeypatch, tmp_path, machine, expected_triple):
    """resolve_prewarm_exe() finds prewarm-<arch>-apple-darwin on macOS.

    ADR-0020 §5: the prewarm binary is frozen the same Nuitka way as
    the sidecar, into ``prewarm-<target-triple>``. On macOS the triple
    is ``<arch>-apple-darwin`` where ``<arch>`` is ``x86_64`` (Intel)
    or ``aarch64`` (Apple Silicon — NOT ``arm64``; ADR-0020 §4.1
    explicitly mandates the Rust target-triple naming convention).

    The resolver finds the frozen exe at the .app bundle resource path
    (``<app>.app/Contents/Resources/prewarm-<triple>``) when no env
    override is set. The resource path is derived from ``sys.argv[0]``
    (the .app/Contents/MacOS/<exe> path).
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver

    # Force platform.machine() to return the test arch.
    monkeypatch.setattr(_platform, "machine", lambda: machine)

    # Build a fake .app bundle structure so the resolver's macOS branch
    # (sys.argv[0] → parent.parent / "Resources" / prewarm-<triple>)
    # finds the frozen exe.
    app_dir = tmp_path / "Voice Typer.app"
    macos_dir = app_dir / "Contents" / "MacOS"
    resources_dir = app_dir / "Contents" / "Resources"
    macos_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    # Create a fake main executable at .app/Contents/MacOS/VoiceTyper
    # (so Path(sys.argv[0]).resolve() succeeds).
    fake_main_exe = macos_dir / "VoiceTyper"
    fake_main_exe.write_bytes(b"#!/bin/sh\n# fake Mach-O\n")

    # Create the frozen prewarm exe at the bundle resource path.
    frozen_exe = resources_dir / f"prewarm-{expected_triple}"
    # Mach-O 64-bit magic (feedfacf) — just a placeholder; the resolver
    # only checks .is_file(), not the file contents.
    frozen_exe.write_bytes(b"\xcf\xfa\xed\xfe")

    # Point sys.argv[0] at the fake main exe so _candidate_paths finds
    # the bundle resource path.
    monkeypatch.setattr(sys, "argv", [str(fake_main_exe)] + list(sys.argv[1:]))
    # Mock sys.executable to a non-existent tmp_path so the
    # exe_dir/name candidate doesn't shadow our frozen exe.
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result is not None
    assert result == str(frozen_exe), f"resolve_prewarm_exe() should find the frozen exe at {frozen_exe}, got {result}"
    # Verify the filename matches the expected macOS triple.
    assert f"prewarm-{expected_triple}" in result, (
        f"Frozen exe name must be 'prewarm-{expected_triple}' (matches the "
        f"Rust target triple for macOS {machine} per ADR-0020 §4.1), "
        f"got {result}"
    )


# ─── §2. Resolver priority order ─────────────────────────────────────────


def test_resolver_priority_env_var_then_bundle_resource_then_dev_fallback(monkeypatch, tmp_path):
    """Resolver priority: VOICE_TYPER_PREWARM_EXE → bundle resource → dev fallback.

    ADR-0020 §5 resolution order:
      1. ``VOICE_TYPER_PREWARM_EXE`` env var (preferred — set by the
         Tauri host at startup to ``resourceDir/prewarm-<triple>``).
      2. Tauri resource dir, heuristically:
         - macOS: ``<app>.app/Contents/Resources/prewarm-<triple>``
      3. Dev fallback: ``python -m voice_typer.server.prewarm``
        (source-tree dev).
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver

    monkeypatch.setattr(_platform, "machine", lambda: "arm64")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))

    # Build a fake .app bundle for the bundle-resource candidate.
    app_dir = tmp_path / "Voice Typer.app"
    macos_dir = app_dir / "Contents" / "MacOS"
    resources_dir = app_dir / "Contents" / "Resources"
    macos_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    fake_main_exe = macos_dir / "VoiceTyper"
    fake_main_exe.write_bytes(b"#!/bin/sh\n")
    bundle_exe = resources_dir / "prewarm-aarch64-apple-darwin"
    bundle_exe.write_bytes(b"\xcf\xfa\xed\xfe")
    monkeypatch.setattr(sys, "argv", [str(fake_main_exe)] + list(sys.argv[1:]))

    # ── Priority 1: VOICE_TYPER_PREWARM_EXE env var ────────────────────
    env_exe = tmp_path / "env-override-prewarm"
    env_exe.write_bytes(b"\xcf\xfa\xed\xfe")
    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(env_exe))

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result == str(env_exe), (
        f"Priority 1: VOICE_TYPER_PREWARM_EXE should win over the bundle resource path, got {result}"
    )

    # ── Priority 2: bundle resource path (env var unset) ──────────────
    monkeypatch.delenv("VOICE_TYPER_PREWARM_EXE")
    result = prewarm_resolver.resolve_prewarm_exe()
    assert result == str(bundle_exe), (
        f"Priority 2: bundle resource path should be used when env var is unset, got {result}"
    )

    # ── Priority 3: dev fallback (no frozen exe found) ────────────────
    # Remove the bundle exe and mock _candidate_paths to return only a
    # nonexistent path (avoids accidentally finding a real prewarm exe
    # next to sys.executable in the test env).
    bundle_exe.unlink()
    monkeypatch.setattr(
        prewarm_resolver,
        "_candidate_paths",
        lambda: [tmp_path / "nonexistent"],
    )

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result is not None
    assert " -m voice_typer.server.prewarm" in result, (
        f"Priority 3: dev fallback should be a Python module invocation, got {result}"
    )


# ─── §3. LaunchAgent plist path ──────────────────────────────────────────


def test_macos_plist_path_under_library_launchagents(monkeypatch, tmp_path):
    """_macos_plist_path() returns ~/Library/LaunchAgents/com.voicetyper.prewarm.plist.

    ADR-0020 §5 + PREWARM_LABEL: the LaunchAgent plist lives under
    ``~/Library/LaunchAgents/`` (per-user, no admin elevation required
    to write or load). The label is ``com.voicetyper.prewarm``
    (reverse-DNS naming convention required by launchd).

    Mocks ``pathlib.Path.home`` so the test doesn't touch the real
    home directory.
    """
    from voice_typer.server import prewarm_scheduler_posix

    # Mock Path.home() to return a tmp_path so the test doesn't touch
    # the real ~/Library/LaunchAgents/.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    plist_path = prewarm_scheduler_posix._macos_plist_path()

    expected = tmp_path / "Library" / "LaunchAgents" / "com.voicetyper.prewarm.plist"
    assert plist_path == expected, f"plist path should be {expected} (per-user, no admin), got {plist_path}"
    # Verify the label components.
    assert plist_path.name == "com.voicetyper.prewarm.plist", (
        f"plist filename must be 'com.voicetyper.prewarm.plist' (reverse-DNS label), got {plist_path.name}"
    )
    assert plist_path.parent.name == "LaunchAgents", (
        f"plist must live under LaunchAgents/, got parent {plist_path.parent.name}"
    )
    # Sanity: the PREWARM_LABEL constant matches.
    assert prewarm_scheduler_posix.PREWARM_LABEL == "com.voicetyper.prewarm"


# ─── §4. RunAtLoad=true ──────────────────────────────────────────────────


def test_plist_uses_run_at_load_true():
    """_build_macos_plist() emits RunAtLoad=true (fires at every login).

    ``RunAtLoad=true`` is the macOS LaunchAgent equivalent of the
    Windows ``LogonTrigger`` — it fires the job when the user logs in.
    Without it, the LaunchAgent would only fire on explicit
    ``launchctl load`` / ``launchctl start`` (not at login), defeating
    the purpose of prewarm.

    ``KeepAlive=false`` ensures prewarm is a one-shot (it doesn't
    respawn after exit — prewarm is a fire-once cache warmer, not a
    daemon).
    """
    from voice_typer.server import prewarm_scheduler_posix

    plist = prewarm_scheduler_posix._build_macos_plist()

    # RunAtLoad=true (fires at every login — the macOS equivalent of
    # Windows' LogonTrigger).
    assert "<key>RunAtLoad</key>" in plist, (
        "RunAtLoad key is missing — prewarm won't fire at login (its only reliable trigger on macOS)"
    )
    assert "<true/>" in plist, "RunAtLoad value must be <true/> (boolean true), not <false/> or a string"
    # KeepAlive=false (one-shot, not a daemon).
    assert "<key>KeepAlive</key>" in plist, "KeepAlive key is missing"
    # KeepAlive appears before RunAtLoad in the plist; both must be <false/>
    # for KeepAlive (so prewarm doesn't respawn) and <true/> for RunAtLoad.
    # Verify KeepAlive is false by checking the surrounding context.
    keepalive_idx = plist.index("<key>KeepAlive</key>")
    keepalive_value_section = plist[keepalive_idx : keepalive_idx + 60]
    assert "<false/>" in keepalive_value_section, (
        "KeepAlive must be <false/> — prewarm is a one-shot cache warmer, not a respawn-on-exit daemon"
    )
    # ProcessType=Background lowers priority (equivalent to Windows
    # PROCESS_MODE_BACKGROUND_BEGIN) so prewarm never disturbs the user.
    assert "<key>ProcessType</key>" in plist, "ProcessType key is missing"
    assert "<string>Background</string>" in plist, (
        "ProcessType must be 'Background' (lowers I/O + CPU priority so "
        "prewarm never competes with the user's real work)"
    )
    # Label must match PREWARM_LABEL.
    assert "<string>com.voicetyper.prewarm</string>" in plist, (
        "Label must be 'com.voicetyper.prewarm' (reverse-DNS naming required by launchd)"
    )


# ─── §5. ProgramArguments uses frozen exe (not python -m) ────────────────


def test_plist_program_arguments_uses_frozen_exe_not_python_module(monkeypatch, tmp_path):
    """When the frozen exe is available, the plist's ProgramArguments
    array points at the frozen prewarm-<triple> binary directly (NOT
    ``python -m voice_typer.server.prewarm``).

    ADR-0020 §5: when the resolver returns a frozen exe path (no
    ``-m voice_typer.server.prewarm`` module args), the
    ProgramArguments array has a single element — the frozen exe path
    itself. The frozen exe IS the module (Nuitka --onefile bundles
    the Python interpreter + module into a single Mach-O binary).

    This test sets ``VOICE_TYPER_PREWARM_EXE`` (so the env-var check
    in ``_prewarm_python`` / ``_prewarm_args`` passes) and mocks
    ``resolve_prewarm_exe`` to return a frozen exe path. It then
    builds the plist and asserts the ProgramArguments array contains
    the frozen exe path and does NOT contain
    ``voice_typer.server.prewarm`` (the dev-fallback module path).
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver, prewarm_scheduler_posix

    monkeypatch.setattr(_platform, "machine", lambda: "arm64")

    # Create a real frozen exe file (so the env var points at a real file).
    frozen_exe = tmp_path / "prewarm-aarch64-apple-darwin"
    frozen_exe.write_bytes(b"\xcf\xfa\xed\xfe")  # Mach-O 64-bit magic

    # Set the env vars that trigger the Tauri sidecar path.
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(frozen_exe))

    # Mock the resolver to return our frozen exe path (avoids the real
    # _candidate_paths lookup which might not find the file under the
    # mocked macOS platform on the Linux test host).
    monkeypatch.setattr(prewarm_resolver, "resolve_prewarm_exe", lambda: str(frozen_exe))

    plist = prewarm_scheduler_posix._build_macos_plist()

    # ── Verify the frozen exe path is in ProgramArguments ────────────
    assert str(frozen_exe) in plist, (
        f"Frozen exe path {frozen_exe} not found in plist — the "
        "ProgramArguments array must point at the frozen exe directly"
    )
    assert "prewarm-aarch64-apple-darwin" in plist, (
        "ProgramArguments must reference 'prewarm-aarch64-apple-darwin' (the frozen Nuitka binary name)"
    )

    # ── Verify the dev-fallback module path is NOT in the plist ──────
    # When the frozen exe is available, _prewarm_args() returns []
    # (no module args), so the ProgramArguments array has exactly one
    # <string> element (the frozen exe path).
    assert "voice_typer.server.prewarm" not in plist, (
        "plist contains 'voice_typer.server.prewarm' — when the frozen exe "
        "is available, ProgramArguments must NOT include the Python module "
        "path (the frozen exe IS the module, per ADR-0020 §5)"
    )
    # The plist must NOT contain " -m " as a separate arg (the dev-fallback
    # invocation is `python -m voice_typer.server.prewarm`).
    assert "<string>-m</string>" not in plist, (
        "plist contains '<string>-m</string>' — when the frozen exe is "
        "available, no '-m' arg should be present in ProgramArguments"
    )

    # ── Verify the ProgramArguments array has exactly one <string> ───
    # (the frozen exe path). The dev fallback would have three: python, -m,
    # voice_typer.server.prewarm.
    # Find the ProgramArguments array block.
    pa_start = plist.index("<key>ProgramArguments</key>")
    pa_end = plist.index("</array>", pa_start)
    pa_block = plist[pa_start:pa_end]
    string_count = pa_block.count("<string>")
    assert string_count == 1, (
        f"ProgramArguments array should have exactly 1 <string> element "
        f"(the frozen exe path) when the frozen exe is available, got "
        f"{string_count}: {pa_block}"
    )

    # RunAtLoad must still be true (the frozen-exe path doesn't change
    # the trigger type).
    assert "<key>RunAtLoad</key>" in plist
    assert "<true/>" in plist


# ─── §6. launchctl load ──────────────────────────────────────────────────


def test_register_prewarm_macos_loads_plist_via_launchctl(monkeypatch, tmp_path):
    """_register_prewarm_macos() writes the plist to disk AND invokes
    ``launchctl load <plist>``.

    ADR-0020 §5 + macos-validation-runbook §6.6: the LaunchAgent plist
    must be both (a) written to ``~/Library/LaunchAgents/`` (so launchd
    discovers it at the next login) AND (b) loaded immediately via
    ``launchctl load`` (so it takes effect this session without
    requiring a logout/login).

    The production code uses ``launchctl load`` (the legacy API) for
    backward compatibility with macOS < 10.10. On newer macOS,
    ``launchctl bootstrap gui/$(id -u) <plist>`` is the modern
    equivalent — both are acceptable; this test verifies ``launchctl
    load`` is used (matching the production code).

    Mocks ``subprocess.run`` (for the launchctl call) + ``Path.home``
    (so no real home directory is touched) + ``os.environ`` (clean
    slate). Verifies the plist file is actually created on disk at the
    expected path.
    """
    from voice_typer.server import prewarm_scheduler_posix

    # Mock Path.home() so the plist is written under tmp_path (not the
    # real ~/Library/LaunchAgents/).
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Capture subprocess.run calls.
    captured_calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured_calls.append(list(cmd))
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Call _register_prewarm_macos (no env var set → dev-fallback
    # plist; that's fine — we're testing the launchctl load call, not
    # the plist contents).
    result = prewarm_scheduler_posix._register_prewarm_macos()
    assert result is True, "_register_prewarm_macos() should return True on success"

    # ── Verify launchctl load was called with the plist path ─────────
    load_calls = [c for c in captured_calls if "launchctl" in c and "load" in c]
    assert len(load_calls) >= 1, f"Expected at least 1 'launchctl load' call, got {captured_calls}"
    load_cmd = load_calls[0]
    assert load_cmd[0] == "launchctl", f"First token must be 'launchctl', got {load_cmd[0]}"
    assert "load" in load_cmd, f"'load' subcommand missing from launchctl call: {load_cmd}"
    # The plist path must be the last positional arg.
    expected_plist = tmp_path / "Library" / "LaunchAgents" / "com.voicetyper.prewarm.plist"
    assert str(expected_plist) in load_cmd, (
        f"launchctl load must be called with the plist path {expected_plist}, got {load_cmd}"
    )

    # ── Verify the plist file was actually created on disk ───────────
    assert expected_plist.exists(), (
        f"plist file not created at {expected_plist} — _register_prewarm_macos "
        "must write the plist to disk so launchd discovers it at the next login"
    )
    plist_contents = expected_plist.read_text(encoding="utf-8")
    assert "com.voicetyper.prewarm" in plist_contents, (
        "plist file contents must include the Label 'com.voicetyper.prewarm'"
    )
    assert "<key>RunAtLoad</key>" in plist_contents, "plist file contents must include RunAtLoad=true"
    assert "<true/>" in plist_contents

    # ── Verify the plist has restrictive permissions (SEC-003) ───────
    # _register_prewarm_macos chmods the plist to 0o600 (owner-only) so
    # other users can't read or modify the launch configuration.
    # On the Linux test host, chmod works; verify the mode is 0o600.
    file_mode = expected_plist.stat().st_mode & 0o777
    assert file_mode == 0o600, f"plist file permissions must be 0o600 (owner-only, SEC-003), got {oct(file_mode)}"


# ─── §7. CT2 cache warming via file reads (not imports) ──────────────────


def test_prewarm_warms_ct2_cache_via_file_reads_not_imports(monkeypatch, tmp_path):
    """The warming pipeline reads CT2 model files via _warm_file (file reads),
    NOT by importing ctranslate2.

    ADR-0020 §5: the prewarm exe is frozen the same Nuitka way as the
    sidecar; its job is to warm the OS file cache (page in ~7 GB of
    torch + transformers + CT2 model weights) so the app's later
    ``import torch`` / ``from_pretrained()`` calls hit RAM instead of
    disk. The prewarm module docstring is explicit: "Read the installed
    torch + transformers package files into the OS page cache WITHOUT
    importing them."

    The CT2 model cache warming (the ``.bin`` / ``.safetensors`` files
    under the HF cache's ``models--Systran--faster-whisper-*`` dir) uses
    ``_warm_file`` — a sequential ``open(path, "rb")`` + ``read()`` loop
    that populates the standby list without executing any code. This test
    verifies that path is exercised and that ``ctranslate2`` is NOT
    imported by the warming pipeline.
    """
    from voice_typer.server import prewarm

    # Build a fake HF cache dir for the CT2 (faster-whisper) model.
    # Structure mirrors _run_warming_pipeline's expectations:
    #   <cache_dir>/snapshots/<hash>/model.bin
    cache_dir = tmp_path / "models--Systran--faster-whisper-small.en"
    snapshot_dir = cache_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    model_file = snapshot_dir / "model.bin"
    model_file.write_bytes(b"\x00" * 1024)  # 1 KB fake CT2 model weights

    # Mock _active_model_cache_dirs to return our fake cache dir
    # (skips the real HF cache lookup + Config.load()).
    monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [cache_dir])
    # Mock _warm_imports to no-op (avoids actual torch/faster_whisper imports
    # which would slow the test and might fail in the sandbox). The model
    # cache warming (the bulk of the CT2 cache work) is what we're testing.
    monkeypatch.setattr(prewarm, "_warm_imports", lambda: None)
    # Mock _mark_warmed to no-op (avoid sentinel file writes).
    monkeypatch.setattr(prewarm, "_mark_warmed", lambda _elapsed: None)

    # Spy on _warm_file to verify it's called on the model file.
    warmed_files: list[Path] = []
    original_warm_file = prewarm._warm_file

    def spy_warm_file(path: Path) -> int:
        warmed_files.append(path)
        return original_warm_file(path)

    monkeypatch.setattr(prewarm, "_warm_file", spy_warm_file)

    # Snapshot sys.modules BEFORE the pipeline runs so we can verify
    # ctranslate2 is not imported by the warming step.
    modules_before = set(sys.modules.keys())

    rc = prewarm._run_warming_pipeline(
        min_ram_mb=prewarm.DEFAULT_MIN_FREE_RAM_MB,
        force=True,
        t_start=time.perf_counter(),
    )

    # ── Verify _warm_file was called on the CT2 model file ───────────
    assert model_file in warmed_files, (
        f"_warm_file was not called on the CT2 model file {model_file} — warmed files: {warmed_files}"
    )

    # ── Verify ctranslate2 was NOT imported by the warming pipeline ──
    modules_after = set(sys.modules.keys())
    new_modules = modules_after - modules_before
    assert "ctranslate2" not in new_modules, (
        f"_run_warming_pipeline imported ctranslate2 (new modules: "
        f"{sorted(m for m in new_modules if 'trans' in m.lower() or 'ct2' in m.lower())}) "
        "— ADR-0020 §5 mandates file warming (sequential reads via "
        "_warm_file), NOT imports"
    )

    # ── Verify the pipeline succeeded ─────────────────────────────────
    assert rc == prewarm.EXIT_OK, f"_run_warming_pipeline should return EXIT_OK ({prewarm.EXIT_OK}), got {rc}"


# ─── §8. PID file + completion event ─────────────────────────────────────


def test_prewarm_run_writes_pid_file_and_signals_completion_event(monkeypatch):
    """run() writes a PID file + invokes the completion-event lifecycle
    so the app's wait_for_prewarm() can wait without polling.

    ADR-0009 Issue 4 + CPU-04: ``run()`` writes a PID file at the start
    of the warming phase (after all early-exit guards) and removes it in
    a finally block. It also invokes the PID-scoped completion-event
    lifecycle (``_create_completion_event`` / ``_signal_completion_event``
    / ``_close_completion_event``). On macOS, ``_create_completion_event``
    returns ``None`` (the Windows-only CreateEventW path is skipped) —
    the PID file is the cross-platform handshake. The functions are
    still called (with ``None`` on macOS) so the call-order contract is
    identical across platforms.

    The app's ``wait_for_prewarm()`` polls the PID file (via
    ``is_prewarm_running()``) and on Linux uses ``pidfd_open`` for a
    zero-CPU kernel wait. On macOS it degrades to the 1s poll loop.

    This test mocks all the side-effecting functions (logging, I/O
    priority, the warming pipeline itself) and tracks the PID file +
    completion event lifecycle to verify the correct call order.
    """
    from voice_typer.server import prewarm

    # Mock all the side-effecting functions called by run().
    monkeypatch.setattr(prewarm, "_setup_logging", lambda: None)
    monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
    # Mock the warming pipeline to succeed without doing real work.
    monkeypatch.setattr(prewarm, "_run_warming_pipeline", lambda *a, **kw: prewarm.EXIT_OK)

    # Track the PID file + completion event lifecycle.
    lifecycle: list[tuple] = []

    def _track_write_pid():
        lifecycle.append(("write_pid",))

    def _track_create_event(pid: int):
        lifecycle.append(("create_event", pid))
        # On macOS, _create_completion_event returns None (the Windows-only
        # CreateEventW path is skipped). Simulate this so the test mirrors
        # the real macOS behavior.
        return None

    def _track_signal(handle):
        lifecycle.append(("signal", handle))

    def _track_close(handle):
        lifecycle.append(("close", handle))

    def _track_remove_pid():
        lifecycle.append(("remove_pid",))

    monkeypatch.setattr(prewarm, "_write_pid_file", _track_write_pid)
    monkeypatch.setattr(prewarm, "_create_completion_event", _track_create_event)
    monkeypatch.setattr(prewarm, "_signal_completion_event", _track_signal)
    monkeypatch.setattr(prewarm, "_close_completion_event", _track_close)
    monkeypatch.setattr(prewarm, "_remove_pid_file", _track_remove_pid)

    # Also mock the early-exit guards so force=True path is exercised
    # cleanly (force=True skips them, but be defensive).
    monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
    monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)

    rc = prewarm.run(force=True, trigger="RunAtLoad")

    assert rc == prewarm.EXIT_OK, f"run() should return EXIT_OK, got {rc}"

    # ── Verify all lifecycle steps were called ────────────────────────
    assert ("write_pid",) in lifecycle, (
        "_write_pid_file was not called — the app's is_prewarm_running() would never see prewarm as running"
    )
    assert ("create_event", os.getpid()) in lifecycle, (
        f"_create_completion_event was not called with the current PID ({os.getpid()}); lifecycle: {lifecycle}"
    )
    # On macOS, _create_completion_event returns None, so signal/close
    # are called with None. The functions are still invoked (the call-
    # order contract is identical across platforms).
    assert ("signal", None) in lifecycle, (
        "_signal_completion_event was not called — even on macOS (where "
        "the event is a no-op), the function must still be invoked so the "
        f"call-order contract holds; lifecycle: {lifecycle}"
    )
    assert ("close", None) in lifecycle, (
        "_close_completion_event was not called — even on macOS (where "
        "the event is a no-op), the function must still be invoked; "
        f"lifecycle: {lifecycle}"
    )
    assert ("remove_pid",) in lifecycle, (
        "_remove_pid_file was not called — a stale PID file would make "
        "is_prewarm_running() return True for a dead process"
    )

    # ── Verify the call order ─────────────────────────────────────────
    # run() order (with force=True):
    #   1. _write_pid_file  (start of warming phase)
    #   2. _create_completion_event  (start of warming phase, after PID file)
    #   3. _run_warming_pipeline  (warming work — mocked)
    #   4. _signal_completion_event  (finally block)
    #   5. _close_completion_event  (finally block)
    #   6. _remove_pid_file  (finally block)
    write_pid_idx = lifecycle.index(("write_pid",))
    create_event_idx = lifecycle.index(("create_event", os.getpid()))
    signal_idx = lifecycle.index(("signal", None))
    close_idx = lifecycle.index(("close", None))
    remove_pid_idx = lifecycle.index(("remove_pid",))

    assert write_pid_idx < create_event_idx, (
        f"_write_pid_file must happen before _create_completion_event; lifecycle: {lifecycle}"
    )
    assert create_event_idx < signal_idx, (
        f"_create_completion_event must happen before _signal_completion_event; lifecycle: {lifecycle}"
    )
    # finally-block order: signal → close → remove_pid.
    assert signal_idx < close_idx < remove_pid_idx, (
        f"finally-block order must be: signal → close → remove_pid; lifecycle: {lifecycle}"
    )
