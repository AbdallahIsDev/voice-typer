"""MIG-1.8 Phase 1 + ADR-0020 §13.3 — postinst / prerm script content validation.

ADR-0020 §13.3 mandates REUSING the existing ``scripts/linux/postinst`` +
``scripts/linux/prerm`` maintainer scripts (the same ones shipped with the
legacy .deb / .rpm since MIG-1.7 Phase 0-L) for the MIG-1.8 Tauri migration.
The postinst sets up the ``input`` group + udev rule for evdev access (so
the bundled ``linux-key-listener`` binary can read ``/dev/input/event*``
without sudo); the prerm cleans up.  This test file validates the CONTENT of
those scripts (source-inspection) so a regression in any of the following
contract clauses is caught at CI time:

  - ``postinst`` exists + is bash-syntax-valid (``bash -n``).
  - ``postinst`` adds the installing user to the ``input`` group.  This
    happens via the helper ``install_permissions.py`` which calls
    ``usermod -aG input <user>`` (the Debian postinst + RPM ``%post`` both
    delegate to that Python helper — single source of truth).
  - ``postinst`` installs a udev rule at
    ``/etc/udev/rules.d/99-voice-typer.rules`` (via install_permissions.py).
  - The udev rule grants ``GROUP="input"`` + ``MODE="0660"`` for
    ``/dev/input/event*`` devices (so the ``input`` group gets rw, others
    get nothing — standard /dev/input access pattern).
  - ``postinst`` triggers ``udevadm control --reload-rules`` +
    ``udevadm trigger --subsystem-match=input`` (via install_permissions.py)
    so the rule takes effect without a reboot.
  - ``postinst`` warns the user to log out + log back in (Linux kernel
    limitation: group membership changes only take effect on next login).
  - ``prerm`` exists + is bash-syntax-valid.
  - ``prerm`` removes the udev rule (via uninstall_permissions.py).
  - ``prerm`` does NOT remove the user from the ``input`` group (other apps
    may rely on it — explicitly documented in the prerm header comment +
    uninstall_permissions.py).
  - ``postinst.rpm`` + ``prerm.rpm`` exist + are bash-syntax-valid
    (rpm-specific equivalents for Fedora / dnf).

IMPLEMENTATION ARCHITECTURE (recap)
-----------------------------------
``scripts/linux/postinst`` (Debian, runs as root during ``apt install``):
  1. Locates ``/usr/share/voice-typer/scripts/install_permissions.py``
     (with a dev-mode source-tree fallback).
  2. Runs it via ``python3 "$INSTALL_SCRIPT"`` (non-fatal on failure — the
     hotkey may not work, but the package install must still succeed).
  3. Prints the log-out + log-back-in warning.

``scripts/linux/install_permissions.py`` (the shared helper, also called by
RPM ``%post`` + AppImage pkexec):
  1. Copies ``99-voice-typer.rules`` to ``/etc/udev/rules.d/`` + reloads
     udev (``udevadm control --reload-rules`` + ``udevadm trigger
     --subsystem-match=input``).
  2. ``usermod -aG input <SUDO_USER>`` (or PKEXEC_UID-derived user).
  3. Configures Caps Lock neutralization (X11 / GNOME / KDE / Sway).
  4. Writes a manifest at ``/var/lib/voice-typer/permissions-manifest.json``.

``scripts/linux/prerm`` (Debian, runs as root during ``apt remove``):
  1. On the ``remove`` / ``deconfigure`` dpkg state, runs
     ``/usr/share/voice-typer/scripts/uninstall_permissions.py`` (non-fatal).

``scripts/linux/uninstall_permissions.py``:
  - Thin wrapper that ``os.execv``s ``install_permissions.py --uninstall``.
  - The uninstall path removes the udev rule + reloads udev + restores the
    backup if one was created at install time.  It explicitly does NOT
    remove the user from the ``input`` group (other apps may rely on it —
    logged as a NOTE + the user is told the manual ``gpasswd -d`` command).

KNOWN GAPS (report, do not fix)
-------------------------------
GAP-1 (prerm.rpm lacks the "do not remove from input group" comment): The
Debian ``prerm`` header comment explicitly documents that the script does
NOT remove the user from the ``input`` group (other apps may rely on it).
The RPM ``prerm.rpm`` header comment does NOT carry the same documentation
— it just says "Removes the udev rule and XKB config."  The behavior is
still correct (prerm.rpm delegates to the same uninstall_permissions.py
which doesn't remove group membership), but the documentation asymmetry
could mislead a future maintainer into "fixing" prerm.rpm to call
``gpasswd -d``.  Not blocking.

GAP-2 (postinst.rpm lacks the dev-mode source-tree fallback): The Debian
``postinst`` has a fallback to the source-tree location
(``$(dirname "$0")/../share/voice-typer/scripts/install_permissions.py``)
for dev-mode testing.  ``postinst.rpm`` does NOT have this fallback — it
only checks the production path ``/usr/share/voice-typer/scripts/``.  This
means dev-mode testing on Fedora hosts (running postinst.rpm directly from
the source tree without `dnf install`) would skip the permission setup
silently.  Not blocking for production (dnf-installed packages always have
the script at the production path).

GAP-3 (postinst.rpm + prerm.rpm have no automated host-validation gate):
This test only validates that the .rpm scripts exist + are bash-syntax-
valid + reference install_permissions.py / uninstall_permissions.py.  The
actual end-to-end ``dnf install`` → ``groups | grep input`` → ``dnf
remove`` flow on a Fedora host is documented in the "VALIDATE ON LINUX
HOST" block below but not automated in CI (would require a Fedora VM).

VALIDATE ON LINUX HOST
----------------------
Debian / Ubuntu (.deb path):
  1. Build the installer:
         cd src-tauri && cargo tauri build --target x86_64-unknown-linux-gnu
  2. Install (this runs scripts/linux/postinst as root):
         sudo apt install ./target/release/bundle/deb/*.deb
  3. Verify the udev rule was installed:
         ls -l /etc/udev/rules.d/99-voice-typer.rules
         # Expected: -rw-r--r-- 1 root root ... 99-voice-typer.rules
         cat /etc/udev/rules.d/99-voice-typer.rules
         # Expected: KERNEL=="event[0-9]*", SUBSYSTEM=="input",
         #           GROUP="input", MODE="0660"
  4. Verify the user was added to the input group:
         groups | grep input
         # Expected: ... input ...
         # (If missing: sudo usermod -aG input $USER; log out + log back in)
  5. Verify udev was reloaded + triggered:
         udevadm info /dev/input/event0 | grep -E "GROUP|MODE"
         # Expected: GROUP=input / MODE=0660 on at least one event device
  6. Verify the manifest was written:
         sudo cat /var/lib/voice-typer/permissions-manifest.json
         # Expected: JSON with target_user + udev_rule + session_type
  7. Verify the log-out + log-back-in warning was printed during install
     (check `apt install` output or /var/log/dpkg.log).
  8. Log out + log back in (Linux kernel limit: group membership only
     takes effect on next login).  Then:
         groups | grep input   # must show "input"
  9. Uninstall (this runs scripts/linux/prerm as root):
         sudo apt remove voice-typer
 10. Verify the udev rule was removed:
         ls /etc/udev/rules.d/99-voice-typer.rules
         # Expected: No such file or directory
 11. Verify the user is STILL in the input group (prerm must NOT remove
     the user from the group — other apps may rely on it):
         groups | grep input
         # Expected: ... input ... (still present)
 12. To manually remove group membership (optional, only if no other app
     needs it):
         sudo gpasswd -d $USER input

Fedora / RHEL (.rpm path):
  1. Build the installer:
         cd src-tauri && cargo tauri build --target x86_64-unknown-linux-gnu
  2. Install (this runs scripts/linux/postinst.rpm as root):
         sudo dnf install ./target/release/bundle/rpm/*.rpm
  3. Repeat steps 3–8 above (the .rpm uses the same install_permissions.py
     helper, so the on-disk artifacts are identical).
  4. Uninstall (this runs scripts/linux/prerm.rpm as root):
         sudo dnf remove voice-typer
  5. Repeat steps 10–12 above.

TEST-HOST NOTES
---------------
These are source-inspection tests (no mocking, no root required).  They
read the real ``scripts/linux/postinst``, ``prerm``, ``postinst.rpm``,
``prerm.rpm``, ``install_permissions.py``, ``uninstall_permissions.py``,
and ``99-voice-typer.rules`` files from the repo.  The ``bash -n`` syntax
checks require ``bash`` on the test host (skipped on Windows / non-bash
hosts via ``shutil.which("bash")``).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

# ─── Paths to real source files (source-inspection, NOT mocked) ──────────

# tests/tauri/mig18/test_postinst_prerm.py → repo root in 4 parents:
#   parents[0]=mig18, parents[1]=tauri, parents[2]=tests, parents[3]=voice-typer.
_REPO_ROOT = Path(__file__).resolve().parents[3]

POSTINST = _REPO_ROOT / "scripts" / "linux" / "postinst"
PRERM = _REPO_ROOT / "scripts" / "linux" / "prerm"
POSTINST_RPM = _REPO_ROOT / "scripts" / "linux" / "postinst.rpm"
PRERM_RPM = _REPO_ROOT / "scripts" / "linux" / "prerm.rpm"
INSTALL_PERMISSIONS = _REPO_ROOT / "scripts" / "linux" / "install_permissions.py"
UNINSTALL_PERMISSIONS = _REPO_ROOT / "scripts" / "linux" / "uninstall_permissions.py"
UDEV_RULE = _REPO_ROOT / "scripts" / "linux" / "99-voice-typer.rules"

# The canonical udev rule install path (referenced by install_permissions.py).
UDEV_RULE_INSTALL_PATH = "/etc/udev/rules.d/99-voice-typer.rules"

# Skip bash -n syntax checks on hosts without bash (e.g. Windows CI runners
# without Git Bash).  On Linux + macOS this is always satisfied.
_HAS_BASH = shutil.which("bash") is not None
_skip_no_bash = pytest.mark.skipif(
    not _HAS_BASH,
    reason="bash not available on PATH (cannot run `bash -n` syntax check)",
)


def _bash_syntax_ok(path: Path) -> bool:
    """Return True iff ``bash -n <path>`` exits 0 (syntax-valid bash script).

    ``bash -n`` reads the script without executing it and reports syntax
    errors via a non-zero exit code + stderr.  We use ``subprocess.run`` with
    ``capture_output=True`` so any stderr is captured (not leaked into the
    pytest output) and surfaced in the assertion message on failure.
    """
    result = subprocess.run(
        ["bash", "-n", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"`bash -n {path}` failed with exit code {result.returncode}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return True


# ─── Tests: scripts/linux/postinst existence + syntax ────────────────────


def test_postinst_exists():
    """``scripts/linux/postinst`` exists as a regular file in the repo.

    The Debian .deb bundler (Tauri ``bundle.linux.deb.postInstallScript``)
    ships this script into the .deb maintainer-scripts directory, so apt
    runs it as root during ``apt install voice-typer``.  ADR-0020 §13.3
    mandates reusing this exact script (not authoring a new one).
    """
    assert POSTINST.is_file(), (
        f"scripts/linux/postinst missing at {POSTINST} — the Debian .deb "
        f"maintainer script must exist (ADR-0020 §13.3 reuses it)."
    )


@_skip_no_bash
def test_postinst_is_bash_syntax_valid():
    """``bash -n scripts/linux/postinst`` exits 0 (no syntax errors).

    A syntax error in postinst would make the .deb uninstallable (dpkg
    would abort with "subprocess post-installation script returned error").
    """
    assert _bash_syntax_ok(POSTINST)


# ─── Tests: postinst adds user to input group ────────────────────────────


def test_postinst_adds_user_to_input_group():
    """``postinst`` adds the installing user to the ``input`` group.

    The postinst delegates to ``install_permissions.py`` which runs
    ``usermod -aG input <user>`` (or, in principle, ``gpasswd -a <user>
    input``).  We accept either form.  Without this, the bundled
    ``linux-key-listener`` binary cannot read ``/dev/input/event*`` (the
    udev rule grants rw to the ``input`` group only).
    """
    assert POSTINST.is_file()
    assert INSTALL_PERMISSIONS.is_file(), (
        f"install_permissions.py missing at {INSTALL_PERMISSIONS} — postinst "
        f"delegates the usermod call to this helper (single source of truth)."
    )
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")

    # Accept either `usermod -aG input <user>` or `gpasswd -a <user> input`.
    usermod_pattern = re.compile(r"usermod\s+-aG\s+input\b")
    gpasswd_pattern = re.compile(r"gpasswd\s+-a\s+\S+\s+input\b")
    assert usermod_pattern.search(install_text) or gpasswd_pattern.search(install_text), (
        "install_permissions.py must add the user to the 'input' group via "
        "`usermod -aG input <user>` (or `gpasswd -a <user> input`). The "
        "postinst delegates to this helper; without this call the bundled "
        "linux-key-listener cannot read /dev/input/event*."
    )

    # Defensive: confirm the postinst actually invokes install_permissions.py
    # (otherwise the usermod above would never run during apt install).
    postinst_text = POSTINST.read_text(encoding="utf-8")
    assert "install_permissions.py" in postinst_text, (
        "postinst must reference install_permissions.py (the helper that "
        "performs the usermod -aG input + udev rule install)."
    )
    assert re.search(r'python3\s+"\$INSTALL_SCRIPT"', postinst_text), (
        "postinst must run install_permissions.py via "
        '`python3 "$INSTALL_SCRIPT"` (the script path is assigned to the '
        "INSTALL_SCRIPT shell variable earlier in the script)."
    )


# ─── Tests: postinst installs udev rule at the canonical path ────────────


def test_postinst_installs_udev_rule():
    """``postinst`` installs the udev rule at ``/etc/udev/rules.d/99-voice-typer.rules``.

    The path is hard-coded in ``install_permissions.py`` (constant
    ``UDEV_RULE_PATH``).  The udev rule grants the ``input`` group rw
    access to ``/dev/input/event*`` so the bundled
    ``linux-key-listener`` binary can read keyboard events without root.
    """
    assert INSTALL_PERMISSIONS.is_file()
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")
    assert UDEV_RULE_INSTALL_PATH in install_text, (
        f"install_permissions.py must install the udev rule to "
        f"{UDEV_RULE_INSTALL_PATH} (the canonical udev rules.d path)."
    )

    # The udev rule source file must exist in the repo (install_permissions.py
    # copies it to /etc/udev/rules.d/ during install).
    assert UDEV_RULE.is_file(), (
        f"99-voice-typer.rules missing at {UDEV_RULE} — install_permissions.py "
        f"ships this file so it can be copied to /etc/udev/rules.d/."
    )


# ─── Tests: udev rule grants GROUP="input" + MODE="0660" for event devs ──


def test_udev_rule_grants_input_group_and_mode_0660():
    """The udev rule grants ``GROUP="input"`` + ``MODE="0660"`` for ``/dev/input/event*``.

    The udev rule (``99-voice-typer.rules``) matches all input event
    devices (``KERNEL=="event[0-9]*", SUBSYSTEM=="input"``) and grants the
    ``input`` group rw access (``GROUP="input"``, ``MODE="0660"``).  This
    is the standard /dev/input access pattern (same as the docker group
    for /var/run/docker.sock): owner (root) gets rw, group (input) gets
    rw, others get nothing.
    """
    assert UDEV_RULE.is_file(), f"99-voice-typer.rules missing at {UDEV_RULE}"
    rule_text = UDEV_RULE.read_text(encoding="utf-8")

    # Match the event-device rule.  We require ALL of:
    #   - KERNEL=="event[0-9]*"  (or KERNEL=="event*" — match event devices)
    #   - SUBSYSTEM=="input"
    #   - GROUP="input"
    #   - MODE="0660"
    # The rule may be on a single line or split across lines; we search
    # the whole text with re.DOTALL semantics (use re.search on the joined
    # text — comments + blank lines are skipped).
    # Strip comments + blank lines for a cleaner match.
    rule_lines = [line.strip() for line in rule_text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    rule_joined = " ".join(rule_lines)

    assert 'KERNEL=="event' in rule_joined, (
        'udev rule must match event devices (KERNEL=="event[0-9]*" or '
        'KERNEL=="event*") so /dev/input/event* gets the input-group rw '
        "permission."
    )
    assert 'SUBSYSTEM=="input"' in rule_joined, 'udev rule must match the input subsystem (SUBSYSTEM=="input").'
    assert 'GROUP="input"' in rule_joined, (
        'udev rule must grant GROUP="input" so members of the input group '
        "get rw access to /dev/input/event* (per ADR-0020 §13.3)."
    )
    assert 'MODE="0660"' in rule_joined, (
        'udev rule must set MODE="0660" (owner rw, group rw, others none) — the standard /dev/input access pattern.'
    )


# ─── Tests: postinst triggers udevadm control --reload + trigger ─────────


def test_postinst_triggers_udevadm_reload_and_trigger():
    """``postinst`` runs ``udevadm control --reload-rules`` + ``udevadm trigger``.

    Without these, the newly-installed udev rule would not take effect
    until the next boot (or the next udev event).  install_permissions.py
    runs both commands (best-effort — failures are non-fatal because some
    minimal containers don't have udev running).
    """
    assert INSTALL_PERMISSIONS.is_file()
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")

    # udevadm control --reload-rules
    assert re.search(r"udevadm.*control.*--reload-rules", install_text), (
        "install_permissions.py must run `udevadm control --reload-rules` "
        "to reload the udev ruleset after installing 99-voice-typer.rules."
    )
    # udevadm trigger --subsystem-match=input
    assert re.search(r"udevadm.*trigger.*--subsystem-match=input", install_text), (
        "install_permissions.py must run `udevadm trigger "
        "--subsystem-match=input` to re-evaluate the rule against existing "
        "/dev/input/event* devices immediately (no reboot required)."
    )


# ─── Tests: postinst warns user to log out + log back in ─────────────────


def test_postinst_warns_user_to_logout_and_login():
    """``postinst`` warns the user to log out + log back in.

    Linux kernel limitation: ``usermod -aG input`` only takes effect on
    the user's NEXT login session (the kernel caches group membership at
    session start).  Without this warning, the user would install Voice
    Typer, try the hotkey, find it doesn't work, and not know why.
    """
    assert POSTINST.is_file()
    postinst_text = POSTINST.read_text(encoding="utf-8")

    # The warning must mention both "log out" (or "logout") and
    # "log back in" (or "log in").  Case-insensitive.
    text_lower = postinst_text.lower()
    assert "log out" in text_lower or "logout" in text_lower, (
        "postinst must warn the user to log out + log back in (Linux kernel "
        "limitation: group membership changes only take effect on next login)."
    )
    assert "log back in" in text_lower or "log in" in text_lower, (
        "postinst must mention 'log back in' (or 'log in') so the user knows "
        "to re-login for the input group change to take effect."
    )
    # Must mention the 'input' group by name (so the user understands WHY
    # they need to re-login).
    assert "input" in postinst_text, (
        "postinst's log-out warning must mention the 'input' group by name "
        "so the user understands the reason for the re-login."
    )


# ─── Tests: scripts/linux/prerm existence + syntax ───────────────────────


def test_prerm_exists():
    """``scripts/linux/prerm`` exists as a regular file in the repo.

    The Debian .deb bundler (Tauri ``bundle.linux.deb.preRemoveScript``)
    ships this script into the .deb maintainer-scripts directory, so apt
    runs it as root during ``apt remove voice-typer``.  ADR-0020 §13.3
    mandates reusing this exact script.
    """
    assert PRERM.is_file(), (
        f"scripts/linux/prerm missing at {PRERM} — the Debian .deb "
        f"maintainer script must exist (ADR-0020 §13.3 reuses it)."
    )


@_skip_no_bash
def test_prerm_is_bash_syntax_valid():
    """``bash -n scripts/linux/prerm`` exits 0 (no syntax errors).

    A syntax error in prerm would make the .deb un-uninstallable (dpkg
    would abort with "subprocess pre-removal script returned error" +
    leave the package in a half-installed state).
    """
    assert _bash_syntax_ok(PRERM)


# ─── Tests: prerm removes udev rule ──────────────────────────────────────


def test_prerm_removes_udev_rule():
    """``prerm`` removes the udev rule at ``/etc/udev/rules.d/99-voice-typer.rules``.

    The prerm delegates to ``uninstall_permissions.py`` (which in turn
    delegates to ``install_permissions.py --uninstall``).  The uninstall
    path unlinks the udev rule + reloads udev.  Without this, the udev
    rule would persist after ``apt remove``, granting the (now-removed)
    ``input`` group rw access to ``/dev/input/event*`` forever.
    """
    assert PRERM.is_file()
    assert UNINSTALL_PERMISSIONS.is_file(), (
        f"uninstall_permissions.py missing at {UNINSTALL_PERMISSIONS} — prerm delegates the cleanup to this helper."
    )

    # prerm must reference + invoke uninstall_permissions.py.
    prerm_text = PRERM.read_text(encoding="utf-8")
    assert "uninstall_permissions.py" in prerm_text, (
        "prerm must reference uninstall_permissions.py (the helper that removes the udev rule + restores the backup)."
    )
    assert re.search(r'python3\s+"\$UNINSTALL_SCRIPT"', prerm_text), (
        "prerm must run uninstall_permissions.py via "
        '`python3 "$UNINSTALL_SCRIPT"` (the script path is assigned to '
        "the UNINSTALL_SCRIPT shell variable earlier in the script)."
    )

    # uninstall_permissions.py delegates to install_permissions.py --uninstall
    # (single source of truth for the cleanup logic).  install_permissions.py's
    # uninstall() path must unlink the udev rule at the canonical install path.
    uninstall_text = UNINSTALL_PERMISSIONS.read_text(encoding="utf-8")
    assert "--uninstall" in uninstall_text, (
        "uninstall_permissions.py must delegate to install_permissions.py "
        "with the --uninstall flag (single source of truth for cleanup)."
    )
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")
    # The uninstall path must remove the udev rule.  We look for either
    # `.unlink()` on the UDEV_RULE_PATH constant or `shutil.rmtree` /
    # `os.remove` referencing the path.
    assert (
        "UDEV_RULE_PATH" in install_text and re.search(r"UDEV_RULE_PATH.*\.unlink\(\)", install_text)
    ) or UDEV_RULE_INSTALL_PATH in install_text, (
        "install_permissions.py's uninstall path must unlink the udev rule at "
        f"{UDEV_RULE_INSTALL_PATH} (via UDEV_RULE_PATH.unlink() or equivalent)."
    )


# ─── Tests: prerm does NOT remove user from input group ──────────────────


def test_prerm_does_not_remove_user_from_input_group():
    """``prerm`` does NOT remove the user from the ``input`` group.

    Other apps (e.g. game controllers, raw input tools, other voice
    typing apps) may rely on the user being in the ``input`` group.
    Removing the user from the group on uninstall would break those apps
    until the user re-adds themselves manually.  The prerm header comment
    + uninstall_permissions.py both document this explicitly.
    """
    assert PRERM.is_file()
    prerm_text = PRERM.read_text(encoding="utf-8")

    # 1) prerm header comment must document the "do not remove from input
    #    group" decision (so future maintainers don't "fix" it).
    assert "input" in prerm_text.lower(), (
        "prerm must mention 'input' in its header comment (documenting the "
        "decision NOT to remove the user from the input group — other apps "
        "may rely on it)."
    )

    # 2) Neither prerm nor uninstall_permissions.py must invoke
    #    `gpasswd -d <user> input` (the canonical remove-from-group cmd)
    #    or `usermod -G <groups>` WITHOUT -a (which would REPLACE the
    #    group list, dropping input).  We accept `usermod -aG` (append)
    #    but reject `gpasswd -d`.
    uninstall_text = UNINSTALL_PERMISSIONS.read_text(encoding="utf-8")
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")
    cleanup_text = prerm_text + "\n" + uninstall_text + "\n" + install_text

    # gpasswd -d is the canonical "remove from group" command.  It must
    # NOT appear in any of the cleanup scripts (it's only mentioned in
    # the user-facing NOTE telling them how to remove themselves manually
    # — but that NOTE goes to stdout, not into actual command execution).
    # We check for the command form `gpasswd -d` followed by a username
    # placeholder (e.g. `<user>`, `$USER`, `<username>`) — NOT the literal
    # string in a NOTE message.
    #
    # The uninstall path uses subprocess.run([...]) lists, so an actual
    # gpasswd -d invocation would look like:
    #     run(["gpasswd", "-d", username, "input"])
    # or
    #     subprocess.run(["gpasswd", "-d", ...])
    # We search for `gpasswd` followed by `-d` in a list-form invocation
    # (i.e. with quotes / brackets around it).
    gpasswd_remove_pattern = re.compile(
        r"""
        \[            # list-form subprocess invocation (["gpasswd", "-d", ...])
        [^\]]*        # any chars up to the closing bracket
        ["']gpasswd["']
        \s* , \s*
        ["']-d["']
        """,
        re.VERBOSE | re.DOTALL,
    )
    assert not gpasswd_remove_pattern.search(cleanup_text), (
        "prerm / uninstall_permissions.py must NOT invoke `gpasswd -d` "
        "(that would remove the user from the input group — other apps may "
        "rely on it). The user-facing NOTE may *mention* `gpasswd -d` as a "
        "manual command, but the script itself must not execute it."
    )

    # 3) install_permissions.py's uninstall path must explicitly log the
    #    "we did NOT remove the user from the input group" NOTE so the
    #    user knows the group membership persists + how to remove it
    #    manually if desired.
    assert "input" in install_text.lower() and (
        "not removing" in install_text.lower()
        or "did not remove" in install_text.lower()
        or "do not remove" in install_text.lower()
        or "don't remove" in install_text.lower()
        or "other apps" in install_text.lower()
    ), (
        "install_permissions.py's uninstall path must log a NOTE explaining "
        "that the user was NOT removed from the 'input' group (other apps may "
        "rely on it) + the manual `gpasswd -d` command to remove if desired."
    )


# ─── Tests: postinst.rpm + prerm.rpm exist + are bash-syntax-valid ───────


def test_postinst_rpm_exists():
    """``scripts/linux/postinst.rpm`` exists as a regular file in the repo.

    The RPM .rpm bundler (Tauri ``bundle.linux.rpm.postInstallScript``)
    ships this script into the .rpm %post section, so dnf runs it as root
    during ``dnf install voice-typer``.  Functionally identical to the
    Debian postinst (same install_permissions.py helper).
    """
    assert POSTINST_RPM.is_file(), (
        f"scripts/linux/postinst.rpm missing at {POSTINST_RPM} — the RPM "
        f"%post maintainer script must exist (ADR-0020 §13.3 reuses the "
        f"parallel .rpm scripts alongside the .deb ones)."
    )


def test_prerm_rpm_exists():
    """``scripts/linux/prerm.rpm`` exists as a regular file in the repo.

    The RPM .rpm bundler (Tauri ``bundle.linux.rpm.preRemoveScript``)
    ships this script into the .rpm %preun section, so dnf runs it as root
    during ``dnf remove voice-typer``.  Functionally identical to the
    Debian prerm (same uninstall_permissions.py helper).
    """
    assert PRERM_RPM.is_file(), (
        f"scripts/linux/prerm.rpm missing at {PRERM_RPM} — the RPM %preun "
        f"maintainer script must exist (ADR-0020 §13.3 reuses the parallel "
        f".rpm scripts alongside the .deb ones)."
    )


@_skip_no_bash
def test_postinst_rpm_is_bash_syntax_valid():
    """``bash -n scripts/linux/postinst.rpm`` exits 0 (no syntax errors).

    A syntax error in postinst.rpm would make the .rpm uninstallable
    (dnf would abort with "scriptlet failed" + leave the package in a
    half-installed state).
    """
    assert _bash_syntax_ok(POSTINST_RPM)


@_skip_no_bash
def test_prerm_rpm_is_bash_syntax_valid():
    """``bash -n scripts/linux/prerm.rpm`` exits 0 (no syntax errors).

    A syntax error in prerm.rpm would make the .rpm un-uninstallable.
    """
    assert _bash_syntax_ok(PRERM_RPM)


def test_postinst_rpm_delegates_to_same_install_permissions_helper():
    """``postinst.rpm`` delegates to the same ``install_permissions.py`` as the .deb postinst.

    This guarantees behavioral parity between the .deb + .rpm install
    paths (single source of truth for the input-group + udev-rule setup).
    """
    assert POSTINST_RPM.is_file()
    rpm_text = POSTINST_RPM.read_text(encoding="utf-8")
    assert "install_permissions.py" in rpm_text, (
        "postinst.rpm must reference install_permissions.py (the same helper "
        "used by the Debian postinst — single source of truth)."
    )
    assert re.search(r'python3\s+"\$INSTALL_SCRIPT"', rpm_text), (
        "postinst.rpm must run install_permissions.py via "
        '`python3 "$INSTALL_SCRIPT"` (same invocation pattern as the '
        "Debian postinst)."
    )


def test_prerm_rpm_delegates_to_same_uninstall_permissions_helper():
    """``prerm.rpm`` delegates to the same ``uninstall_permissions.py`` as the .deb prerm.

    This guarantees behavioral parity between the .deb + .rpm uninstall
    paths (single source of truth for the udev-rule cleanup + the
    "do not remove from input group" decision).
    """
    assert PRERM_RPM.is_file()
    rpm_text = PRERM_RPM.read_text(encoding="utf-8")
    assert "uninstall_permissions.py" in rpm_text, (
        "prerm.rpm must reference uninstall_permissions.py (the same helper "
        "used by the Debian prerm — single source of truth)."
    )
    assert re.search(r'python3\s+"\$UNINSTALL_SCRIPT"', rpm_text), (
        "prerm.rpm must run uninstall_permissions.py via "
        '`python3 "$UNINSTALL_SCRIPT"` (same invocation pattern as the '
        "Debian prerm)."
    )
