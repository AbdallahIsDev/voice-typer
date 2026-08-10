"""FR-8: regression tests for prewarm autostart cleanup in the uninstaller
scripts of all three OSes.

The prewarm scheduler (``voice_typer/server/prewarm_scheduler_posix.py`` on
macOS/Linux and ``voice_typer/server/task_scheduler.py`` on Windows) registers
its OS-specific autostart entry under SEPARATE labels from the main-app
autostart entry:

  - macOS  : ``~/Library/LaunchAgents/com.voicetyper.prewarm.plist``
             (launchd label ``com.voicetyper.prewarm``).
  - Linux  : ``~/.config/systemd/user/voice-typer-prewarm.{service,timer}``
             (systemd user units).
  - Windows: Task Scheduler task ``com.voicetyper.prewarm`` (no
             ``Autostart`` suffix; no ``_<hash>`` suffix; canonical
             reverse-DNS namespace — pre-rename bare form was
             ``VoiceTyperPrewarm``).

Pre-fix (FR-8 PARTIAL state): the uninstaller scripts only cleaned the
main-app autostart labels (``com.voicetyper.plist``, ``voice-typer.desktop``,
``VoiceTyperAutostart*``). The prewarm entries survived uninstall and the OS
kept trying to launch the (now-deleted) frozen prewarm binary at every
login, spamming system logs with "command not found" / failed-unit errors.

Post-fix (this commit): all three uninstaller scripts now ALSO clean up the
prewarm entries. These tests grep each uninstaller for the prewarm patterns
and assert they're present. This is a static-content test (the scripts
themselves run only on their target OS at uninstall time, so we can't
execute them in the sandbox); the value is in catching a regression where
someone deletes the prewarm cleanup block (e.g. during a refactor) without
realising what it was for.

Why a new test file (not extending ``tests/test_uninstall_windows.py``)
-----------------------------------------------------------------------
``test_uninstall_windows.py`` is Windows-specific (it stubs ``winreg`` and
exercises ``autostart_windows._unregister_all_voicetyper_*``). FR-8 spans
all 3 OSes and is purely a static-text grep, so a focused cross-platform
test file is cleaner than wedging macOS/Linux greps into a Windows-only
fixture setup.
"""

from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

MACOS_UNINSTALL_SH = REPO_ROOT / "scripts" / "macos" / "uninstall.sh"
LINUX_PRERM = REPO_ROOT / "scripts" / "linux" / "prerm"
WINDOWS_UNINSTALL_BAT = REPO_ROOT / "scripts" / "windows" / "uninstall.bat"
WINDOWS_UNINSTALLER_NSH = REPO_ROOT / "scripts" / "windows" / "uninstaller.nsh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: pathlib.Path) -> str:
    """Read a script file as text (UTF-8). Fail loudly if missing — every
    test below depends on the file existing."""
    assert path.is_file(), f"Missing uninstaller script: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# macOS: scripts/macos/uninstall.sh
# ---------------------------------------------------------------------------


class TestMacosUninstallsPrewarmLaunchAgent:
    """FR-8 (macOS): the uninstall script must remove the prewarm
    LaunchAgent plist at
    ``~/Library/LaunchAgents/com.voicetyper.prewarm.plist`` and bootout the
    agent. This is SEPARATE from the main-app ``com.voicetyper.plist``
    cleanup."""

    def test_script_exists(self):
        assert MACOS_UNINSTALL_SH.is_file()

    def test_references_prewarm_plist_path(self):
        """The script must reference the prewarm plist filename
        ``com.voicetyper.prewarm.plist`` (not just the main-app
        ``com.voicetyper.plist``)."""
        text = _read(MACOS_UNINSTALL_SH)
        assert "com.voicetyper.prewarm.plist" in text, (
            "macOS uninstall.sh must reference 'com.voicetyper.prewarm.plist' "
            "(the prewarm LaunchAgent file). Without this, the prewarm agent "
            "survives uninstall and launchd keeps trying to launch the "
            "(now-deleted) frozen prewarm binary at every login."
        )

    def test_uses_launchctl_bootout_for_prewarm(self):
        """The script must call ``launchctl bootout`` against the prewarm
        label (``com.voicetyper.prewarm``) — the modern macOS 10.10+ way to
        unload a LaunchAgent. The legacy ``launchctl unload`` (or
        ``launchctl remove``) is acceptable as a fallback only."""
        text = _read(MACOS_UNINSTALL_SH)
        assert "launchctl bootout" in text and "com.voicetyper.prewarm" in text, (
            "macOS uninstall.sh must call `launchctl bootout` against the "
            "`com.voicetyper.prewarm` label. Without bootout, the agent "
            "lingers until next logout even after the plist is deleted."
        )

    def test_has_prewarm_unload_fallback(self):
        """The script must have a fallback unload path (either
        ``launchctl unload`` or ``launchctl remove``) for older macOS
        versions where ``bootout`` is unavailable. The fallback must be
        scoped to the prewarm label/plist (not just the main-app label)."""
        text = _read(MACOS_UNINSTALL_SH)
        # Extract the prewarm block (between PREWARM_PLIST definition and
        # the end of its `if ... fi` block). We just need to confirm a
        # fallback verb exists in the same block as the prewarm plist.
        assert "PREWARM_PLIST" in text, (
            "macOS uninstall.sh must define a PREWARM_PLIST variable for the "
            "prewarm cleanup block (mirrors the main-app PLIST_PATH pattern)."
        )
        assert "launchctl unload" in text or "launchctl remove" in text, (
            "macOS uninstall.sh must have a legacy launchctl fallback "
            "(unload or remove) for systems where `bootout` is unavailable."
        )

    def test_deletes_prewarm_plist_file(self):
        """The script must ``rm -f`` the prewarm plist file (not just
        bootout the agent — the file itself must be removed so launchd
        doesn't re-load it on next login)."""
        text = _read(MACOS_UNINSTALL_SH)
        assert 'rm -f "$PREWARM_PLIST"' in text, (
            'macOS uninstall.sh must `rm -f "$PREWARM_PLIST"` to delete '
            "the prewarm plist file (bootout alone is not enough — launchd "
            "re-reads LaunchAgents on next login)."
        )


# ---------------------------------------------------------------------------
# Linux: scripts/linux/prerm
# ---------------------------------------------------------------------------


class TestLinuxPrermCleansPrewarmSystemdUnits:
    """FR-8 (Linux): the Debian prerm must disable + remove the prewarm
    systemd user timer + service at
    ``~/.config/systemd/user/voice-typer-prewarm.{service,timer}`` for every
    non-system user. This is SEPARATE from the main-app
    ``voice-typer.desktop`` autostart cleanup."""

    def test_script_exists(self):
        assert LINUX_PRERM.is_file()

    def test_references_prewarm_timer_unit(self):
        """The prerm must reference the prewarm timer unit name
        ``voice-typer-prewarm.timer``."""
        text = _read(LINUX_PRERM)
        assert "voice-typer-prewarm.timer" in text, (
            "Linux prerm must reference 'voice-typer-prewarm.timer' (the "
            "prewarm systemd user timer). Without this, systemd --user "
            "keeps trying to launch the (now-deleted) frozen prewarm binary "
            "at every login."
        )

    def test_references_prewarm_service_unit(self):
        """The prerm must reference the prewarm service unit name
        ``voice-typer-prewarm.service`` (the unit the timer activates)."""
        text = _read(LINUX_PRERM)
        assert "voice-typer-prewarm.service" in text, (
            "Linux prerm must reference 'voice-typer-prewarm.service' (the "
            "prewarm systemd user service unit activated by the timer)."
        )

    def test_disables_prewarm_units_via_systemctl(self):
        """The prerm must call ``systemctl ... disable --now`` (or at
        least ``systemctl ... disable``) against the prewarm units —
        stopping the running timer + service is best-effort but the
        disable call is required so the timer doesn't fire again on next
        login."""
        text = _read(LINUX_PRERM)
        assert "systemctl" in text and "disable" in text, (
            "Linux prerm must call `systemctl ... disable` against the "
            "prewarm timer + service so systemd doesn't re-enable them on "
            "next login."
        )

    def test_deletes_prewarm_unit_files(self):
        """The prerm must ``rm -f`` the prewarm .service + .timer files
        from ``~/.config/systemd/user/`` for each user home (disable alone
        is not enough — the unit files must be removed)."""
        text = _read(LINUX_PRERM)
        assert "rm -f" in text, (
            "Linux prerm must `rm -f` the prewarm unit files (disable alone "
            "is not enough — the unit files would re-enable on a daemon "
            "reload)."
        )

    def test_iterates_multiple_user_homes(self):
        """The prerm must iterate over MULTIPLE user homes (not just the
        current user) because prerm runs as root and the prewarm units are
        per-user. The existing main-app cleanup iterates ``getent passwd``
        + ``/root``; the prewarm cleanup must use the same iteration (or
        an equivalent ``/home/*`` + ``/root`` sweep)."""
        text = _read(LINUX_PRERM)
        # The existing pattern uses `getent passwd` and explicit /root.
        # Either pattern is acceptable as long as it's not hardcoded to a
        # single user.
        assert "getent passwd" in text or "/home/*" in text, (
            "Linux prerm must iterate over multiple user homes (getent "
            "passwd or /home/* sweep) so prewarm units are cleaned for ALL "
            "users, not just the user who triggered the uninstall."
        )
        assert "/root" in text, (
            "Linux prerm must explicitly include /root in the prewarm "
            "cleanup sweep (the existing main-app autostart cleanup does "
            "the same — root may have run voice-typer via sudo -E)."
        )


# ---------------------------------------------------------------------------
# Windows: scripts/windows/uninstall.bat
# ---------------------------------------------------------------------------


class TestWindowsUninstallBatCleansPrewarmTask:
    """FR-8 (Windows): the .bat fallback sweep must (1) widen the Task
    Scheduler sweep to the union ``'VoiceTyper*','com.voicetyper*'`` so it
    catches the prewarm task (canonical ``com.voicetyper.prewarm`` and
    legacy ``VoiceTyperPrewarm``), and (2) explicitly delete the prewarm
    task under both names as belt-and-suspenders. The HKCU Run-key sweep
    must match both name prefixes too (``-like 'VoiceTyper*' -or -like
    'com.voicetyper*'``) so the prewarm Run-key entry (if any) is
    covered."""

    def test_script_exists(self):
        assert WINDOWS_UNINSTALL_BAT.is_file()

    def test_task_scheduler_sweep_widened_to_voicetyper_star(self):
        """The PowerShell Task Scheduler sweep must use the union
        ``'VoiceTyper*','com.voicetyper*'`` so it catches:
        - legacy ``VoiceTyperAutostart_<hash>`` + ``VoiceTyperPrewarm``
          tasks (pre-rename installs), AND
        - canonical ``com.voicetyper.autostart_<hash>`` +
          ``com.voicetyper.prewarm`` tasks (post-rename installs).
        We check the active ``Get-ScheduledTask -TaskName`` call (not the
        surrounding comments, which may legitimately reference the old
        narrower pattern when explaining the widening)."""
        text = _read(WINDOWS_UNINSTALL_BAT)
        assert "'VoiceTyper*','com.voicetyper*'" in text, (
            "Windows uninstall.bat's active `Get-ScheduledTask -TaskName` "
            "call must sweep the union 'VoiceTyper*','com.voicetyper*' so "
            "it covers both the legacy prewarm task 'VoiceTyperPrewarm' and "
            "the canonical 'com.voicetyper.prewarm' (plus the autostart "
            "tasks under both schemes)."
        )

    def test_explicit_prewarm_task_delete(self):
        """The .bat must explicitly call
        ``schtasks.exe /Delete /TN "com.voicetyper.prewarm" /F`` AND
        ``schtasks.exe /Delete /TN "VoiceTyperPrewarm" /F`` as a
        belt-and-suspenders (in case the wildcard sweep missed them)."""
        text = _read(WINDOWS_UNINSTALL_BAT)
        assert '/Delete /TN "com.voicetyper.prewarm"' in text, (
            "Windows uninstall.bat must call `schtasks /Delete /TN "
            '"com.voicetyper.prewarm" /F` explicitly (current canonical '
            "prewarm task name)."
        )
        assert '/Delete /TN "VoiceTyperPrewarm"' in text or "/Delete /TN VoiceTyperPrewarm" in text, (
            'Windows uninstall.bat must call `schtasks /Delete /TN "VoiceTyperPrewarm" /F` '
            "explicitly (legacy pre-rename prewarm task name)."
        )

    def test_hkcu_run_key_sweep_matches_both_name_prefixes(self):
        """The HKCU Run-key sweep must match both ``VoiceTyper*`` and
        ``com.voicetyper*`` value names (canonical + legacy). Pre-FR-8 the
        sweep only matched ``VoiceTyper*``; the canonical reverse-DNS
        check is new since the namespace rename. Guards against a future
        regression that narrows either side of the union."""
        text = _read(WINDOWS_UNINSTALL_BAT)
        assert "-like 'VoiceTyper*'" in text and "-like 'com.voicetyper*'" in text, (
            "Windows uninstall.bat's HKCU Run-key sweep must match value "
            "names with the union `-like 'VoiceTyper*' -or -like "
            "'com.voicetyper*'` (covers legacy VoiceTyper_* entries and "
            "canonical com.voicetyper.* entries)."
        )


# ---------------------------------------------------------------------------
# Windows: scripts/windows/uninstaller.nsh (NSIS macro)
# ---------------------------------------------------------------------------


class TestWindowsUninstallerNshCleansPrewarmTask:
    """FR-8 (Windows NSIS): the .nsh macro must (1) widen the Task
    Scheduler sweep to the union of the two wildcard patterns so it
    catches ``com.voicetyper.prewarm`` + legacy ``VoiceTyperPrewarm``, and
    (2) explicitly delete the prewarm task under BOTH names as
    belt-and-suspenders. The HKCU Run-key enum loop must compare both
    prefixes (13 chars against ``com.voicetyper``, 10 chars against
    ``VoiceTyper``)."""

    def test_script_exists(self):
        assert WINDOWS_UNINSTALLER_NSH.is_file()

    def test_task_scheduler_sweep_widened_to_voicetyper_star(self):
        """The NSIS PowerShell sweep must use the union
        ``$\'VoiceTyper*$\',$\'com.voicetyper*$\'`` (NSIS-quoted
        single-quotes), covering legacy ``VoiceTyperAutostart*`` /
        ``VoiceTyperPrewarm`` AND canonical ``com.voicetyper.*`` task
        names. We check the active ``Get-ScheduledTask -TaskName`` call
        (not the surrounding comments, which may legitimately reference
        the old narrower pattern when explaining the widening)."""
        text = _read(WINDOWS_UNINSTALLER_NSH)
        assert "$\\'VoiceTyper*$\\',$\\'com.voicetyper*$\\'" in text, (
            "Windows uninstaller.nsh's active `Get-ScheduledTask -TaskName` "
            "call must sweep the union 'VoiceTyper*','com.voicetyper*' "
            "(single-quotes escaped as $\\' inside the nsExec quoted "
            "string) so it covers the legacy prewarm task "
            "'VoiceTyperPrewarm' and the canonical 'com.voicetyper.prewarm'."
        )

    def test_explicit_prewarm_task_delete(self):
        """The .nsh must explicitly call
        ``schtasks.exe /Delete /TN "com.voicetyper.prewarm" /F`` AND
        ``schtasks.exe /Delete /TN "VoiceTyperPrewarm" /F`` as a
        belt-and-suspenders."""
        text = _read(WINDOWS_UNINSTALLER_NSH)
        assert "com.voicetyper.prewarm" in text and "VoiceTyperPrewarm" in text, (
            "Windows uninstaller.nsh must reference BOTH the canonical "
            "'com.voicetyper.prewarm' and legacy 'VoiceTyperPrewarm' task "
            "names explicitly (belt-and-suspenders deletes)."
        )
        # The NSIS macro uses $\" for escaped double-quotes; accept either
        # the escaped form or a bare form (defensive — future edits might
        # drop the quotes since the name has no spaces).
        assert "/Delete /TN" in text, (
            'Windows uninstaller.nsh must call `schtasks /Delete /TN "com.voicetyper.prewarm" /F` '
            'and `schtasks /Delete /TN "VoiceTyperPrewarm" /F` explicitly.'
        )

    def test_hkcu_run_key_enum_uses_both_name_prefixes(self):
        """The NSIS HKCU Run-key enum loop must compare the first 13 chars
        of each value name against ``com.voicetyper`` AND the first 10
        chars against ``VoiceTyper`` (covers canonical
        ``com.voicetyper.autostart_<hash>`` + prewarm values and legacy
        bare names). Guards against a future narrowing."""
        text = _read(WINDOWS_UNINSTALLER_NSH)
        assert "StrCpy $2 $1 13" in text and 'StrCmp $2 "com.voicetyper"' in text, (
            "Windows uninstaller.nsh must keep the 13-char 'com.voicetyper' "
            "prefix match in the HKCU Run-key enum loop (canonical "
            "reverse-DNS value names)."
        )
        assert "VoiceTyper" in text, (
            "Windows uninstaller.nsh must keep the 'VoiceTyper' 10-char "
            "prefix match for the HKCU Run-key enum loop (legacy "
            "VoiceTyper_* value names)."
        )


# ---------------------------------------------------------------------------
# Cross-cutting: no  / FIX-E / task-ID leakage in source
# ---------------------------------------------------------------------------


class TestNoTaskIdInScripts:
    """Constraint C-STYLE-1: no task IDs (FR-8, FIX-E, etc.) in source
    code. The session prefix belongs ONLY in metadata files
    (worklog.md, review.md). The comments in the uninstaller scripts must
    reference the FEATURE (prewarm) and the RATIONALE, not the fix
    ticket."""

    @pytest.mark.parametrize(
        "path",
        [MACOS_UNINSTALL_SH, LINUX_PRERM, WINDOWS_UNINSTALL_BAT, WINDOWS_UNINSTALLER_NSH],
    )
    def test_no_fix_e_or_fr_8_in_script(self, path):
        text = _read(path)
        # Constraint C-STYLE-1 forbids task IDs in source. The orchestrator
        # may use FIX-E /  in metadata; the scripts must NOT.
        for forbidden in ("FIX-E", "FR-8", "FR8"):
            assert forbidden not in text, (
                f"{path.name} contains '{forbidden}' — task IDs are "
                "forbidden in source code (CONSTRAINTS.md C-STYLE-1). "
                "Reference the feature ('prewarm') instead."
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
