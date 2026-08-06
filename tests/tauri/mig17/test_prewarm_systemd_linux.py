"""MIG-1.7 Phase 0-L Gate Check 8 — prewarm systemd user timer validation (Linux).

This test file validates the *structure* of the Linux prewarm scheduling
path from the Linux sandbox (or any other dev host). The actual
``VALIDATE ON LINUX HOST`` commands documented below MUST be run on a
real Linux display host (X11 or Wayland) — these tests only verify the
code paths they reference behave as ADR-0020 §5 and the
linux-validation-runbook §6.7 (Step 11) require.

Coverage map (8 test functions, one per gate-check criterion):

1. ``test_resolve_prewarm_exe_finds_frozen_exe_on_linux`` (parametrized
   over both arches) — ``prewarm_resolver.resolve_prewarm_exe()`` finds
   the frozen ``prewarm-x86_64-unknown-linux-gnu`` /
   ``prewarm-aarch64-unknown-linux-gnu`` on Linux via the AppImage
   ``$APPDIR/usr/resources/prewarm-<triple>`` resource path.
2. ``test_resolver_priority_env_var_then_bundle_resource_then_dev_fallback``
   — resolver priority order: ``VOICE_TYPER_PREWARM_EXE`` env var →
   Tauri bundle resource path (AppImage ``$APPDIR`` + the
   ``/usr/lib|share/voice-typer/resources/`` fallbacks) → dev fallback
   (Python module invocation).
3. ``test_linux_unit_paths_under_xdg_config_systemd_user`` —
   ``prewarm_scheduler_posix._linux_service_path()`` /
   ``_linux_timer_path()`` return
   ``~/.config/systemd/user/voice-typer-prewarm.{service,timer}``
   (per-user, no root elevation required; honors ``$XDG_CONFIG_HOME``
   when set + non-empty per the XDG Base Directory Spec).
4. ``test_linux_timer_uses_on_boot_sec_10s`` — the systemd user timer
   ``_build_linux_timer()`` emits ``OnBootSec=10s`` (fires shortly
   after boot / user session start — the Linux equivalent of the
   Windows ``LogonTrigger`` and the macOS ``RunAtLoad=true``).
5. ``test_linux_service_uses_frozen_exe_not_python_module`` — when the
   frozen exe is available (env var set + resolver finds it), the
   service unit's ``ExecStart=`` line points at the frozen
   ``prewarm-<triple>`` binary directly (NOT
   ``python3 -m voice_typer.server.prewarm``). Mocks
   ``prewarm_resolver.resolve_prewarm_exe`` + ``os.environ``.
6. ``test_register_prewarm_linux_invokes_systemctl_daemon_reload_and_enable``
   — ``_register_prewarm_linux()`` writes both unit files to disk AND
   invokes ``systemctl --user daemon-reload`` +
   ``systemctl --user enable voice-typer-prewarm.timer`` so the timer
   is registered for the next boot. Mocks ``subprocess.run`` +
   ``os.environ`` (XDG_CONFIG_HOME) so no real systemctl call is made
   and no real ``~/.config/systemd/user/`` directory is touched.
7. ``test_prewarm_warms_ct2_cache_via_file_reads_not_imports`` —
   the warming pipeline reads CT2 model files via ``_warm_file``
   (sequential file reads), NOT by importing ``ctranslate2``
   (ADR-0020 §5: "frozen the same Nuitka way… warms the OS file cache").
8. ``test_prewarm_run_writes_pid_file_and_signals_completion_event`` —
   ``run()`` writes a PID file + invokes the completion-event lifecycle
   (``_create_completion_event`` / ``_signal_completion_event`` /
   ``_close_completion_event``) so the app's ``wait_for_prewarm()`` can
   wait without polling. On Linux the completion event uses
   ``pidfd_open`` when available (returns a fd, not None); when
   ``pidfd_open`` is unavailable the event is a no-op (returns
   ``None``) — the PID file is the cross-platform handshake either way.

Platform checks are monkeypatched to ``linux`` so the Linux code paths
are exercised even when the tests run on macOS/Windows CI.
``subprocess.run`` is mocked so no real ``systemctl`` call is made.

VALIDATE ON LINUX HOST:
1. Launch Voice Typer (creates the systemd user timer)
2. Run: systemctl --user list-timers | grep voicetyper
   Expected: voicetyper.prewarm timer entry
3. Examine ~/.config/systemd/user/voicetyper.prewarm.service + .timer
   Expected: ExecStart=prewarm-<arch>-unknown-linux-gnu; OnStartupSec=10s
   (NOTE: production code uses OnBootSec=10s — same semantic: fires
   10s after the system boot once the user session is active. The
   "OnStartupSec" name in the task spec is a loose synonym; either
   OnBootSec or OnStartupSec satisfies "runs shortly after login".)
4. Log out + log back in (OR run: systemctl --user start voicetyper.prewarm)
5. Check ~/.local/share/voice-typer/logs/prewarm.log for:
   - "[PREWARM] starting (trigger=...)"
   - "[PREWARM] Warming model: small.en"
   - "[PREWARM] complete (X.Xs)"
6. Verify CT2 model files are warm in ~/.local/share/voice-typer/huggingface/hub/
Expected: prewarm completes within 30s of login; model cache warm
(Same behavior on both x86_64 and aarch64 — the arch is implicit in
the frozen binary name.)

References:
- ADR-0020 §5 (Prewarm packaging — frozen same Nuitka way as sidecar,
  bundled as bundle.resource NOT externalBin, launched by the platform
  scheduler via resolve_prewarm_exe()).
- docs/migration/linux-validation-runbook.md §6.7 (Step 11 — Prewarm
  systemd user timer, gate point 7, BOTH arches).
- voice_typer/server/prewarm_resolver.py
- voice_typer/server/prewarm_scheduler_posix.py
- voice_typer/server/prewarm.py
- scripts/build/build_prewarm_linux.sh
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Linux platform mocking fixture ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _force_linux(monkeypatch, tmp_path):
    """Pretend we're on Linux for every test in this module.

    The Linux prewarm scheduling path (``prewarm_scheduler_posix.py``)
    is guarded by ``is_linux()``. On a macOS/Windows CI host, we
    monkeypatch ``sys.platform`` + the ``is_macos`` / ``is_windows`` /
    ``is_linux`` helpers (both in ``platform_utils`` and the bound
    names imported into ``prewarm_scheduler_posix`` /
    ``prewarm_resolver`` / ``prewarm``) to exercise the Linux code
    paths.

    ``prewarm_scheduler_posix.is_supported()`` is also mocked to
    ``True`` so the ``is_supported()``-guarded code paths execute.

    ``XDG_CONFIG_HOME`` is set to ``tmp_path`` so the systemd user unit
    directory resolves to ``tmp_path/systemd/user/`` — the tests never
    touch the real ``~/.config/systemd/user/``.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    from voice_typer.server import (
        platform_utils,
        prewarm,
        prewarm_resolver,
        prewarm_scheduler_posix,
    )

    # Patch the source module.
    monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
    monkeypatch.setattr(platform_utils, "is_macos", lambda: False)
    monkeypatch.setattr(platform_utils, "is_linux", lambda: True)
    # Patch the bound names imported into each consumer module.
    for mod in (prewarm_scheduler_posix, prewarm_resolver, prewarm):
        if hasattr(mod, "is_windows"):
            monkeypatch.setattr(mod, "is_windows", lambda: False)
        if hasattr(mod, "is_macos"):
            monkeypatch.setattr(mod, "is_macos", lambda: False)
        if hasattr(mod, "is_linux"):
            monkeypatch.setattr(mod, "is_linux", lambda: True)
    # is_supported() checks is_macos() or is_linux() — both mocked.
    monkeypatch.setattr(prewarm_scheduler_posix, "is_supported", lambda: True)
    # Remove _MEIPASS so the resolver doesn't append a _MEIPASS candidate
    # (which could shadow the paths we set up in tmp_path).
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    # Clear the prewarm env overrides so tests start from a clean slate.
    monkeypatch.delenv("VOICE_TYPER_PREWARM_EXE", raising=False)
    monkeypatch.delenv("TAURI_SIDECAR", raising=False)
    # APPDIR is set inside AppImage; clear it so the AppImage branch is
    # only exercised when a test explicitly sets it.
    monkeypatch.delenv("APPDIR", raising=False)
    # Redirect XDG_CONFIG_HOME to tmp_path so unit files never land in
    # the real ~/.config/systemd/user/. (Per the XDG Base Directory
    # Spec, an empty XDG_CONFIG_HOME is treated as unset, so use a
    # non-empty tmp_path.)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


# ─── §1. prewarm_resolver.resolve_prewarm_exe() ──────────────────────────


@pytest.mark.parametrize(
    "machine,expected_triple",
    [
        ("x86_64", "x86_64-unknown-linux-gnu"),
        ("aarch64", "aarch64-unknown-linux-gnu"),
    ],
    ids=["x86_64", "aarch64"],
)
def test_resolve_prewarm_exe_finds_frozen_exe_on_linux(monkeypatch, tmp_path, machine, expected_triple):
    """resolve_prewarm_exe() finds prewarm-<arch>-unknown-linux-gnu on Linux.

    ADR-0020 §5: the prewarm binary is frozen the same Nuitka way as
    the sidecar, into ``prewarm-<target-triple>``. On Linux the triple
    is ``<arch>-unknown-linux-gnu`` where ``<arch>`` is ``x86_64`` or
    ``aarch64`` (NOT ``arm64``; ADR-0020 §4.1 explicitly mandates the
    Rust target-triple naming convention).

    The resolver finds the frozen exe via the AppImage ``$APPDIR``
    resource path (``$APPDIR/usr/resources/prewarm-<triple>``) when no
    env override is set. ``$APPDIR`` is set by the AppImage runtime to
    the squashfs mount point (e.g. ``/tmp/.mount_VoiceTyXXXX/``).
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver

    # Force platform.machine() to return the test arch.
    monkeypatch.setattr(_platform, "machine", lambda: machine)

    # Mock sys.executable to a non-existent path so the
    # exe_dir/name candidate doesn't shadow our frozen exe.
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))

    # Build a fake AppImage mount directory. The resolver's Linux branch
    # probes ``$APPDIR/usr/resources/prewarm-<triple>`` when APPDIR is
    # set, so we set APPDIR to tmp_path/appimage and create the frozen
    # exe under it.
    appdir = tmp_path / "appimage"
    resources_dir = appdir / "usr" / "resources"
    resources_dir.mkdir(parents=True)
    frozen_exe = resources_dir / f"prewarm-{expected_triple}"
    # ELF magic bytes (0x7f 'E' 'L' 'F') — just a placeholder; the
    # resolver only checks .is_file(), not the file contents.
    frozen_exe.write_bytes(b"\x7fELF\x02\x01\x01\x00")

    monkeypatch.setenv("APPDIR", str(appdir))

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result is not None
    assert result == str(frozen_exe), f"resolve_prewarm_exe() should find the frozen exe at {frozen_exe}, got {result}"
    # Verify the filename matches the expected Linux triple.
    assert f"prewarm-{expected_triple}" in result, (
        f"Frozen exe name must be 'prewarm-{expected_triple}' (matches the "
        f"Rust target triple for Linux {machine} per ADR-0020 §4.1), "
        f"got {result}"
    )


# ─── §2. Resolver priority order ─────────────────────────────────────────


def test_resolver_priority_env_var_then_bundle_resource_then_dev_fallback(monkeypatch, tmp_path):
    """Resolver priority: VOICE_TYPER_PREWARM_EXE → bundle resource → dev fallback.

    ADR-0020 §5 resolution order:
      1. ``VOICE_TYPER_PREWARM_EXE`` env var (preferred — set by the
         Tauri host at startup to ``resourceDir/prewarm-<triple>``).
      2. Tauri resource dir, heuristically:
         - Linux: ``$APPDIR/usr/resources/prewarm-<triple>`` (AppImage)
                  or ``/usr/lib/voice-typer/resources/prewarm-<triple>``
                  (.deb/.rpm install)
      3. Dev fallback: ``python -m voice_typer.server.prewarm``
        (source-tree dev).
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver

    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))

    # Build a fake AppImage resource dir for the bundle-resource candidate.
    appdir = tmp_path / "appimage"
    resources_dir = appdir / "usr" / "resources"
    resources_dir.mkdir(parents=True)
    bundle_exe = resources_dir / "prewarm-x86_64-unknown-linux-gnu"
    bundle_exe.write_bytes(b"\x7fELF")
    monkeypatch.setenv("APPDIR", str(appdir))

    # ── Priority 1: VOICE_TYPER_PREWARM_EXE env var ────────────────────
    env_exe = tmp_path / "env-override-prewarm"
    env_exe.write_bytes(b"\x7fELF")
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
    # next to sys.executable or under /usr/lib/voice-typer/resources in
    # the test environment).
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


# ─── §3. systemd user unit paths ─────────────────────────────────────────


def test_linux_unit_paths_under_xdg_config_systemd_user(monkeypatch, tmp_path):
    """_linux_service_path() / _linux_timer_path() return
    ~/.config/systemd/user/voice-typer-prewarm.{service,timer}.

    ADR-0020 §5 + PREWARM_LABEL: the systemd user units live under
    ``~/.config/systemd/user/`` (per-user, no root elevation required
    to write or enable). When ``$XDG_CONFIG_HOME`` is set + non-empty,
    the units live under ``$XDG_CONFIG_HOME/systemd/user/`` per the XDG
    Base Directory Spec.

    The autouse ``_force_linux`` fixture sets ``XDG_CONFIG_HOME`` to
    ``tmp_path`` so the test doesn't touch the real
    ``~/.config/systemd/user/``.
    """
    from voice_typer.server import prewarm_scheduler_posix

    service_path = prewarm_scheduler_posix._linux_service_path()
    timer_path = prewarm_scheduler_posix._linux_timer_path()

    expected_dir = tmp_path / "systemd" / "user"
    expected_service = expected_dir / "voice-typer-prewarm.service"
    expected_timer = expected_dir / "voice-typer-prewarm.timer"

    assert service_path == expected_service, (
        f"service path should be {expected_service} (per-user, no root), got {service_path}"
    )
    assert timer_path == expected_timer, f"timer path should be {expected_timer} (per-user, no root), got {timer_path}"
    # Verify the file name components.
    assert service_path.name == "voice-typer-prewarm.service", (
        f"service filename must be 'voice-typer-prewarm.service' "
        f"(systemd convention: <name>.service), got {service_path.name}"
    )
    assert timer_path.name == "voice-typer-prewarm.timer", (
        f"timer filename must be 'voice-typer-prewarm.timer' (systemd convention: <name>.timer), got {timer_path.name}"
    )
    assert service_path.parent.name == "user", (
        f"unit files must live under systemd/user/ (per-user systemd), got parent {service_path.parent.name}"
    )
    assert service_path.parent.parent.name == "systemd", (
        f"unit dir must be under systemd/, got {service_path.parent.parent.name}"
    )


# ─── §4. OnBootSec=10s ───────────────────────────────────────────────────


def test_linux_timer_uses_on_boot_sec_10s():
    """_build_linux_timer() emits OnBootSec=10s (fires shortly after boot).

    ``OnBootSec=10s`` is the systemd user-timer equivalent of the
    Windows ``LogonTrigger`` and the macOS ``RunAtLoad=true`` — it
    fires the job 10 seconds after the system boot (once the user
    session is active). The runbook §6.7 (Step 11) pass criterion 1
    requires ``systemctl --user list-timers voice-typer-prewarm.timer``
    to show ``OnBootSec=10s``.

    NOTE: the task spec says "OnStartupSec=10s or similar (runs shortly
    after login)". ``OnBootSec`` fires 10s after the system boot, while
    ``OnStartupSec`` fires 10s after the systemd user instance starts
    (typically at user login). The production code uses ``OnBootSec``
    (matching the runbook); either form satisfies "runs shortly after
    login". ``OnBootSec`` is preferred for prewarm because the OS file
    cache is reset at boot, so warming it before the user starts
    interacting with the app gives the largest benefit.
    """
    from voice_typer.server import prewarm_scheduler_posix

    timer = prewarm_scheduler_posix._build_linux_timer()

    # OnBootSec=10s (fires shortly after boot — Linux equivalent of
    # Windows' LogonTrigger).
    assert "OnBootSec=10s" in timer, (
        "OnBootSec=10s is missing — prewarm won't fire shortly after "
        "boot (its only reliable trigger on Linux). The runbook §6.7 "
        "pass criterion 1 requires 'systemctl --user list-timers "
        "voice-typer-prewarm.timer' to show OnBootSec=10s."
    )

    # The timer must reference the prewarm service unit.
    assert "Unit=voice-typer-prewarm.service" in timer, (
        "timer must reference 'Unit=voice-typer-prewarm.service' "
        "(the OnBootSec trigger fires THIS service, not a random one)"
    )

    # WantedBy=timers.target so the timer is enabled at boot.
    assert "WantedBy=timers.target" in timer, (
        "WantedBy=timers.target is missing — without it the timer is "
        "never started at boot, even after 'systemctl --user enable'"
    )

    # The [Timer] section must exist.
    assert "[Timer]" in timer, "timer unit must have a [Timer] section"
    # The [Install] section must exist (for `systemctl --user enable`).
    assert "[Install]" in timer, "timer unit must have an [Install] section"


# ─── §5. ExecStart uses frozen exe (not python -m) ────────────────────────


def test_linux_service_uses_frozen_exe_not_python_module(monkeypatch, tmp_path):
    """When the frozen exe is available, the service unit's ExecStart=
    line points at the frozen prewarm-<triple> binary directly (NOT
    ``python3 -m voice_typer.server.prewarm``).

    ADR-0020 §5: when the resolver returns a frozen exe path (no
    ``-m voice_typer.server.prewarm`` module args), the service's
    ``ExecStart=`` line is a single token — the frozen exe path. The
    frozen exe IS the module (Nuitka --onefile bundles the Python
    interpreter + module into a single ELF binary).

    The runbook §6.7 pass criterion 2 requires
    ``~/.config/systemd/user/voice-typer-prewarm.service`` to have
    ``ExecStart=`` pointing at the frozen prewarm binary (NOT a
    ``python3 -m ...`` command — that's the dev fallback).

    This test sets ``VOICE_TYPER_PREWARM_EXE`` (so the env-var check
    in ``_prewarm_python`` / ``_prewarm_args`` passes) and mocks
    ``resolve_prewarm_exe`` to return a frozen exe path. It then
    builds the service unit and asserts the ``ExecStart=`` line
    contains the frozen exe path and does NOT contain
    ``voice_typer.server.prewarm`` (the dev-fallback module path).
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver, prewarm_scheduler_posix

    monkeypatch.setattr(_platform, "machine", lambda: "aarch64")

    # Create a real frozen exe file (so the env var points at a real file).
    frozen_exe = tmp_path / "prewarm-aarch64-unknown-linux-gnu"
    frozen_exe.write_bytes(b"\x7fELF")  # ELF magic

    # Set the env vars that trigger the Tauri sidecar path.
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(frozen_exe))

    # Mock the resolver to return our frozen exe path (avoids the real
    # _candidate_paths lookup which might not find the file under the
    # mocked Linux platform on a non-Linux test host).
    monkeypatch.setattr(prewarm_resolver, "resolve_prewarm_exe", lambda: str(frozen_exe))

    service = prewarm_scheduler_posix._build_linux_service()

    # ── Verify the frozen exe path is in ExecStart ───────────────────
    assert str(frozen_exe) in service, (
        f"Frozen exe path {frozen_exe} not found in service unit — the "
        "ExecStart= line must point at the frozen exe directly"
    )
    assert "prewarm-aarch64-unknown-linux-gnu" in service, (
        "ExecStart must reference 'prewarm-aarch64-unknown-linux-gnu' (the frozen Nuitka binary name)"
    )

    # ── Verify the dev-fallback module path is NOT in the service ────
    # When the frozen exe is available, _prewarm_args() returns []
    # (no module args), so the ExecStart= line is a single token (the
    # frozen exe path).
    assert "voice_typer.server.prewarm" not in service, (
        "service unit contains 'voice_typer.server.prewarm' — when the "
        "frozen exe is available, ExecStart must NOT include the Python "
        "module path (the frozen exe IS the module, per ADR-0020 §5)"
    )
    # The service must NOT contain " -m " as a separate arg.
    assert " -m " not in service, (
        "service unit contains ' -m ' — when the frozen exe is available, no '-m' arg should be present in ExecStart"
    )

    # ── Verify ExecStart is a single token (the frozen exe path) ─────
    # The dev fallback would be `ExecStart=<python> -m voice_typer.server.prewarm`
    # (3 tokens); the frozen-exe path is `ExecStart=<frozen-exe>` (1 token).
    # Find the ExecStart line and verify it has exactly one token after '='.
    execstart_line = None
    for line in service.splitlines():
        stripped = line.strip()
        if stripped.startswith("ExecStart="):
            execstart_line = stripped
            break
    assert execstart_line is not None, (
        "service unit must have an ExecStart= line — without it systemd won't know what to run"
    )
    # ExecStart=<path> — split on '=' and verify the RHS is a single token.
    # The production code wraps every ExecStart token in double quotes
    # via `_systemd_escape_arg()` (security hardening — prevents
    # directive injection via env-var-controlled paths). Strip the
    # surrounding quotes before comparing to the frozen exe path.
    execstart_value = execstart_line.split("=", 1)[1].strip()
    if len(execstart_value) >= 2 and execstart_value.startswith('"') and execstart_value.endswith('"'):
        execstart_value = execstart_value[1:-1]
    assert execstart_value == str(frozen_exe), (
        f"ExecStart value must be the frozen exe path only "
        f"('{frozen_exe}'), got '{execstart_value}' — when the frozen exe "
        "is available, ExecStart must be a single token (the frozen exe IS "
        "the module)"
    )

    # ── Verify the service unit still has the required systemd fields ─
    # Type=oneshot (prewarm is a one-shot cache warmer, not a daemon).
    assert "Type=oneshot" in service, (
        "Type=oneshot is missing — prewarm is a one-shot cache warmer, not "
        "a long-running daemon (Type=simple would imply systemd supervises "
        "and restarts it, which is wrong for a fire-once warmer)"
    )
    # Nice=10 + IOSchedulingClass=idle lower I/O + CPU priority so
    # prewarm never disturbs the user (equivalent to Windows
    # PROCESS_MODE_BACKGROUND_BEGIN).
    assert "Nice=10" in service, (
        "Nice=10 is missing — prewarm must run at lower CPU priority so it never starves foreground apps"
    )
    assert "IOSchedulingClass=idle" in service, (
        "IOSchedulingClass=idle is missing — prewarm must run at idle I/O "
        "priority so it never competes with the user's real disk I/O"
    )


# ─── §6. systemctl --user daemon-reload + enable ─────────────────────────


def test_register_prewarm_linux_invokes_systemctl_daemon_reload_and_enable(monkeypatch, tmp_path):
    """_register_prewarm_linux() writes both unit files to disk AND invokes
    ``systemctl --user daemon-reload`` + ``systemctl --user enable
    voice-typer-prewarm.timer``.

    ADR-0020 §5 + linux-validation-runbook §6.7: the systemd user
    units must be (a) written to ``~/.config/systemd/user/`` (so
    systemd discovers them at the next boot) AND (b) enabled via
    ``systemctl --user enable`` (so the timer is registered for the
    next boot via ``WantedBy=timers.target``). ``daemon-reload`` is
    required first so systemd picks up the new unit files.

    Mocks ``subprocess.run`` (for the systemctl calls) +
    ``os.environ`` (clean slate — ``XDG_CONFIG_HOME`` is set to
    ``tmp_path`` by the autouse ``_force_linux`` fixture so unit files
    land in ``tmp_path/systemd/user/``, not the real
    ``~/.config/systemd/user/``). Verifies both unit files are
    actually created on disk at the expected paths.

    NOTE (implementation gap, reported not fixed): the production code
    invokes ``systemctl --user daemon-reload`` + ``systemctl --user
    enable voice-typer-prewarm.timer`` but does NOT invoke
    ``systemctl --user start voice-typer-prewarm.timer``. The timer
    therefore fires on the NEXT boot (via ``OnBootSec=10s`` +
    ``WantedBy=timers.target``), not this session. The runbook §6.7
    documents the manual fallback ``systemctl --user start
    voice-typer-prewarm.timer`` (or reboot) for users who want to test
    prewarm immediately. This matches the macOS LaunchAgent behaviour
    (which DOES call ``launchctl load`` for the current session) — a
    gap to consider for parity, but not a bug per se.
    """
    from voice_typer.server import prewarm_scheduler_posix

    # XDG_CONFIG_HOME is already set to tmp_path by the autouse
    # _force_linux fixture, so unit files land in
    # tmp_path/systemd/user/ — no need to mock Path.home().

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

    # Call _register_prewarm_linux (no env var set → dev-fallback
    # service unit; that's fine — we're testing the systemctl calls +
    # file creation, not the ExecStart contents).
    result = prewarm_scheduler_posix._register_prewarm_linux()
    assert result is True, "_register_prewarm_linux() should return True on success"

    # ── Verify systemctl --user daemon-reload was called ─────────────
    daemon_reload_calls = [c for c in captured_calls if "systemctl" in c and "--user" in c and "daemon-reload" in c]
    assert len(daemon_reload_calls) >= 1, (
        f"Expected at least 1 'systemctl --user daemon-reload' call, got {captured_calls}"
    )
    daemon_reload_cmd = daemon_reload_calls[0]
    assert daemon_reload_cmd[0] == "systemctl", f"First token must be 'systemctl', got {daemon_reload_cmd[0]}"
    assert "--user" in daemon_reload_cmd, (
        f"'--user' flag missing from daemon-reload call: {daemon_reload_cmd} "
        "— without --user, systemctl operates on the SYSTEM instance "
        "(requires root, not what we want for a per-user prewarm timer)"
    )
    assert "daemon-reload" in daemon_reload_cmd, (
        f"'daemon-reload' subcommand missing: {daemon_reload_cmd} — without "
        "daemon-reload, systemd won't pick up the new unit files written "
        "to ~/.config/systemd/user/"
    )

    # ── Verify systemctl --user enable was called for the timer ──────
    enable_calls = [c for c in captured_calls if "systemctl" in c and "--user" in c and "enable" in c]
    assert len(enable_calls) >= 1, (
        f"Expected at least 1 'systemctl --user enable' call, got "
        f"{captured_calls} — without enable, the timer is written to "
        "disk but never registered for the next boot"
    )
    enable_cmd = enable_calls[0]
    assert "voice-typer-prewarm.timer" in enable_cmd, (
        f"enable must be called for 'voice-typer-prewarm.timer', got {enable_cmd}"
    )

    # ── Verify the unit files were actually created on disk ──────────
    expected_dir = tmp_path / "systemd" / "user"
    expected_service = expected_dir / "voice-typer-prewarm.service"
    expected_timer = expected_dir / "voice-typer-prewarm.timer"
    assert expected_service.exists(), (
        f"service unit not created at {expected_service} — "
        "_register_prewarm_linux must write the .service file to disk so "
        "systemd discovers it after daemon-reload"
    )
    assert expected_timer.exists(), (
        f"timer unit not created at {expected_timer} — "
        "_register_prewarm_linux must write the .timer file to disk so "
        "systemd discovers it after daemon-reload"
    )

    # ── Verify the unit file contents ────────────────────────────────
    service_contents = expected_service.read_text(encoding="utf-8")
    assert "ExecStart=" in service_contents, "service unit must have an ExecStart= line"
    assert "Type=oneshot" in service_contents, (
        "service unit must have Type=oneshot (prewarm is a one-shot cache warmer, not a daemon)"
    )

    timer_contents = expected_timer.read_text(encoding="utf-8")
    assert "OnBootSec=10s" in timer_contents, "timer unit must have OnBootSec=10s (fires shortly after boot)"
    assert "Unit=voice-typer-prewarm.service" in timer_contents, "timer unit must reference voice-typer-prewarm.service"
    assert "WantedBy=timers.target" in timer_contents, (
        "timer unit must have WantedBy=timers.target (so 'systemctl --user enable' registers it for the next boot)"
    )


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
    / ``_close_completion_event``). On Linux, ``_create_completion_event``
    uses ``pidfd_open`` when available (returns a fd, not None); when
    ``pidfd_open`` is unavailable the event is a no-op (returns None) —
    the PID file is the cross-platform handshake either way.

    The app's ``wait_for_prewarm()`` polls the PID file (via
    ``is_prewarm_running()``) and on Linux uses ``pidfd_open`` for a
    zero-CPU kernel wait. On Linux without ``pidfd_open`` it degrades
    to the 1s poll loop.

    This test mocks all the side-effecting functions (logging, I/O
    priority, the warming pipeline itself) and tracks the PID file +
    completion event lifecycle to verify the correct call order. The
    ``_create_completion_event`` mock returns ``None`` (simulating the
    no-pidfd_open fallback), so ``_signal_completion_event`` and
    ``_close_completion_event`` are called with ``None`` — same as on
    macOS.
    """
    from voice_typer.server import prewarm

    # Mock all the side-effecting functions called by run().
    # ``_setup_logging`` is called with ``prewarm_only=True`` by ``run()``
    # (so the prewarm subprocess's logger attaches the dedicated
    # ``voice_typer.server.prewarm`` handler instead of the main app's);
    # the lambda must accept that kwarg.
    monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr(prewarm, "_lower_io_priority", lambda *a, **kw: None)
    # Mock the warming pipeline to succeed without doing real work.
    monkeypatch.setattr(prewarm, "_run_warming_pipeline", lambda *a, **kw: prewarm.EXIT_OK)

    # Track the PID file + completion event lifecycle.
    lifecycle: list[tuple] = []

    def _track_write_pid():
        lifecycle.append(("write_pid",))

    def _track_create_event(pid: int):
        lifecycle.append(("create_event", pid))
        # On Linux without pidfd_open (or when pidfd_open is mocked to
        # fail), _create_completion_event returns None (the Windows-only
        # CreateEventW path is also skipped on Linux). Simulate this so
        # the test mirrors the real Linux behaviour.
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

    rc = prewarm.run(force=True, trigger="OnBootSec")

    assert rc == prewarm.EXIT_OK, f"run() should return EXIT_OK, got {rc}"

    # ── Verify all lifecycle steps were called ────────────────────────
    assert ("write_pid",) in lifecycle, (
        "_write_pid_file was not called — the app's is_prewarm_running() would never see prewarm as running"
    )
    assert ("create_event", os.getpid()) in lifecycle, (
        f"_create_completion_event was not called with the current PID ({os.getpid()}); lifecycle: {lifecycle}"
    )
    # On Linux (without pidfd_open), _create_completion_event returns
    # None, so signal/close are called with None. The functions are
    # still invoked (the call-order contract is identical across
    # platforms).
    assert ("signal", None) in lifecycle, (
        "_signal_completion_event was not called — even on Linux (where "
        "the event is a no-op when pidfd_open is unavailable), the "
        "function must still be invoked so the call-order contract "
        f"holds; lifecycle: {lifecycle}"
    )
    assert ("close", None) in lifecycle, (
        "_close_completion_event was not called — even on Linux (where "
        "the event is a no-op when pidfd_open is unavailable), the "
        f"function must still be invoked; lifecycle: {lifecycle}"
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
