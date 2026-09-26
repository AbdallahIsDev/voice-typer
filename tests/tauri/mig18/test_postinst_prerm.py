"""
Phase 1 + ADR-0020 §13.3: postinst / prerm script content validation.
KNOWN GAPS (report, do not fix)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

_REPO_ROOT = Path(__file__).resolve().parents[3]

POSTINST = _REPO_ROOT / "scripts" / "linux" / "postinst"
PRERM = _REPO_ROOT / "scripts" / "linux" / "prerm"
POSTINST_RPM = _REPO_ROOT / "scripts" / "linux" / "postinst.rpm"
PRERM_RPM = _REPO_ROOT / "scripts" / "linux" / "prerm.rpm"
INSTALL_PERMISSIONS = _REPO_ROOT / "scripts" / "linux" / "install_permissions.py"
UNINSTALL_PERMISSIONS = _REPO_ROOT / "scripts" / "linux" / "uninstall_permissions.py"
UDEV_RULE = _REPO_ROOT / "scripts" / "linux" / "99-lausu.rules"

# The canonical udev rule install path (referenced by install_permissions.py).
UDEV_RULE_INSTALL_PATH = "/etc/udev/rules.d/99-lausu.rules"

# Skip bash -n syntax checks on hosts without a USABLE bash. A mere
_skip_no_bash = pytest.mark.skipif(
    not bash_usable(),
    reason="bash not available or not usable on this host (cannot run `bash -n` syntax check)",
)


def _bash_syntax_ok(path: Path) -> bool:
    """Return True iff ``bash -n <path>`` exits 0 (syntax-valid bash script)."""
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


def test_postinst_exists():
    """``scripts/linux/postinst`` exists as a regular file in the repo."""
    assert POSTINST.is_file(), (
        f"scripts/linux/postinst missing at {POSTINST}, the Debian .deb "
        f"maintainer script must exist (ADR-0020 §13.3 reuses it)."
    )


@_skip_no_bash
def test_postinst_is_bash_syntax_valid():
    """``bash -n scripts/linux/postinst`` exits 0 (no syntax errors)."""
    assert _bash_syntax_ok(POSTINST)


def test_postinst_adds_user_to_input_group():
    """``postinst`` adds the installing user to the ``input`` group."""
    assert POSTINST.is_file()
    assert INSTALL_PERMISSIONS.is_file(), (
        f"install_permissions.py missing at {INSTALL_PERMISSIONS}, postinst "
        f"delegates the usermod call to this helper (single source of truth)."
    )
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")

    usermod_pattern = re.compile(r"usermod\s+-aG\s+input\b|usermod\"\s*,\s*\"-aG\"\s*,\s*INPUT_GROUP")
    gpasswd_pattern = re.compile(r"gpasswd\s+-a\s+\S+\s+input\b")
    assert usermod_pattern.search(install_text) or gpasswd_pattern.search(install_text), (
        "install_permissions.py must add the user to the 'input' group via "
        "`usermod -aG input <user>` (or `gpasswd -a <user> input`). The "
        "postinst delegates to this helper; without this call the bundled "
        "linux-key-listener cannot read /dev/input/event*."
    )

    # Defensive: confirm the postinst actually invokes install_permissions.py
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


def test_postinst_installs_udev_rule():
    """``postinst`` installs the udev rule at ``/etc/udev/rules.d/99-lausu.rules``."""
    assert INSTALL_PERMISSIONS.is_file()
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")
    assert UDEV_RULE_INSTALL_PATH in install_text, (
        f"install_permissions.py must install the udev rule to "
        f"{UDEV_RULE_INSTALL_PATH} (the canonical udev rules.d path)."
    )

    # The udev rule source file must exist in the repo (install_permissions.py
    assert UDEV_RULE.is_file(), (
        f"99-lausu.rules missing at {UDEV_RULE}, install_permissions.py "
        f"ships this file so it can be copied to /etc/udev/rules.d/."
    )


def test_udev_rule_grants_input_group_and_mode_0660():
    """The udev rule grants ``GROUP=\"input\"`` + ``MODE=\"0660\"`` for ``/dev/input/event*``."""
    assert UDEV_RULE.is_file(), f"99-lausu.rules missing at {UDEV_RULE}"
    rule_text = UDEV_RULE.read_text(encoding="utf-8")

    # Match the event-device rule.  We require ALL of:
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
        'udev rule must set MODE="0660" (owner rw, group rw, others none), the standard /dev/input access pattern.'
    )


def test_postinst_triggers_udevadm_reload_and_trigger():
    """``postinst`` runs ``udevadm control --reload-rules`` + ``udevadm trigger``."""
    assert INSTALL_PERMISSIONS.is_file()
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")

    assert re.search(r"udevadm.*control.*--reload-rules", install_text), (
        "install_permissions.py must run `udevadm control --reload-rules` "
        "to reload the udev ruleset after installing 99-lausu.rules."
    )
    assert re.search(r"udevadm.*trigger.*--subsystem-match=input", install_text), (
        "install_permissions.py must run `udevadm trigger "
        "--subsystem-match=input` to re-evaluate the rule against existing "
        "/dev/input/event* devices immediately (no reboot required)."
    )


def test_postinst_warns_user_to_logout_and_login():
    """``postinst`` warns the user to log out + log back in."""
    assert POSTINST.is_file()
    postinst_text = POSTINST.read_text(encoding="utf-8")

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
    assert "input" in postinst_text, (
        "postinst's log-out warning must mention the 'input' group by name "
        "so the user understands the reason for the re-login."
    )


def test_prerm_exists():
    """``scripts/linux/prerm`` exists as a regular file in the repo."""
    assert PRERM.is_file(), (
        f"scripts/linux/prerm missing at {PRERM}, the Debian .deb "
        f"maintainer script must exist (ADR-0020 §13.3 reuses it)."
    )


@_skip_no_bash
def test_prerm_is_bash_syntax_valid():
    """``bash -n scripts/linux/prerm`` exits 0 (no syntax errors)."""
    assert _bash_syntax_ok(PRERM)


def test_prerm_removes_udev_rule():
    """``prerm`` removes the udev rule at ``/etc/udev/rules.d/99-lausu.rules``."""
    assert PRERM.is_file()
    assert UNINSTALL_PERMISSIONS.is_file(), (
        f"uninstall_permissions.py missing at {UNINSTALL_PERMISSIONS}, prerm delegates the cleanup to this helper."
    )

    prerm_text = PRERM.read_text(encoding="utf-8")
    assert "uninstall_permissions.py" in prerm_text, (
        "prerm must reference uninstall_permissions.py (the helper that removes the udev rule + restores the backup)."
    )
    assert re.search(r'python3\s+"\$UNINSTALL_SCRIPT"', prerm_text), (
        "prerm must run uninstall_permissions.py via "
        '`python3 "$UNINSTALL_SCRIPT"` (the script path is assigned to '
        "the UNINSTALL_SCRIPT shell variable earlier in the script)."
    )

    uninstall_text = UNINSTALL_PERMISSIONS.read_text(encoding="utf-8")
    assert "--uninstall" in uninstall_text, (
        "uninstall_permissions.py must delegate to install_permissions.py "
        "with the --uninstall flag (single source of truth for cleanup)."
    )
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")
    # The uninstall path must remove the udev rule.  We look for either
    assert (
        "UDEV_RULE_PATH" in install_text and re.search(r"UDEV_RULE_PATH.*\.unlink\(\)", install_text)
    ) or UDEV_RULE_INSTALL_PATH in install_text, (
        "install_permissions.py's uninstall path must unlink the udev rule at "
        f"{UDEV_RULE_INSTALL_PATH} (via UDEV_RULE_PATH.unlink() or equivalent)."
    )


def test_prerm_does_not_remove_user_from_input_group():
    """``prerm`` does NOT remove the user from the ``input`` group."""
    assert PRERM.is_file()
    prerm_text = PRERM.read_text(encoding="utf-8")

    # 1) prerm header comment must document the "do not remove from input
    assert "input" in prerm_text.lower(), (
        "prerm must mention 'input' in its header comment (documenting the "
        "decision NOT to remove the user from the input group, other apps "
        "may rely on it)."
    )

    # 2) Neither prerm nor uninstall_permissions.py must invoke
    uninstall_text = UNINSTALL_PERMISSIONS.read_text(encoding="utf-8")
    install_text = INSTALL_PERMISSIONS.read_text(encoding="utf-8")
    cleanup_text = prerm_text + "\n" + uninstall_text + "\n" + install_text

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
        "(that would remove the user from the input group, other apps may "
        "rely on it). The user-facing NOTE may *mention* `gpasswd -d` as a "
        "manual command, but the script itself must not execute it."
    )

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


def test_postinst_rpm_exists():
    """``scripts/linux/postinst.rpm`` exists as a regular file in the repo."""
    assert POSTINST_RPM.is_file(), (
        f"scripts/linux/postinst.rpm missing at {POSTINST_RPM}, the RPM "
        f"%post maintainer script must exist (ADR-0020 §13.3 reuses the "
        f"parallel .rpm scripts alongside the .deb ones)."
    )


def test_prerm_rpm_exists():
    """``scripts/linux/prerm.rpm`` exists as a regular file in the repo."""
    assert PRERM_RPM.is_file(), (
        f"scripts/linux/prerm.rpm missing at {PRERM_RPM}, the RPM %preun "
        f"maintainer script must exist (ADR-0020 §13.3 reuses the parallel "
        f".rpm scripts alongside the .deb ones)."
    )


@_skip_no_bash
def test_postinst_rpm_is_bash_syntax_valid():
    """``bash -n scripts/linux/postinst.rpm`` exits 0 (no syntax errors)."""
    assert _bash_syntax_ok(POSTINST_RPM)


@_skip_no_bash
def test_prerm_rpm_is_bash_syntax_valid():
    """``bash -n scripts/linux/prerm.rpm`` exits 0 (no syntax errors)."""
    assert _bash_syntax_ok(PRERM_RPM)


def test_postinst_rpm_delegates_to_same_install_permissions_helper():
    """``postinst.rpm`` delegates to the same ``install_permissions.py`` as the .deb postinst."""
    assert POSTINST_RPM.is_file()
    rpm_text = POSTINST_RPM.read_text(encoding="utf-8")
    assert "install_permissions.py" in rpm_text, (
        "postinst.rpm must reference install_permissions.py (the same helper "
        "used by the Debian postinst, single source of truth)."
    )
    assert re.search(r'python3\s+"\$INSTALL_SCRIPT"', rpm_text), (
        "postinst.rpm must run install_permissions.py via "
        '`python3 "$INSTALL_SCRIPT"` (same invocation pattern as the '
        "Debian postinst)."
    )


def test_prerm_rpm_delegates_to_same_uninstall_permissions_helper():
    """``prerm.rpm`` delegates to the same ``uninstall_permissions.py`` as the .deb prerm."""
    assert PRERM_RPM.is_file()
    rpm_text = PRERM_RPM.read_text(encoding="utf-8")
    assert "uninstall_permissions.py" in rpm_text, (
        "prerm.rpm must reference uninstall_permissions.py (the same helper "
        "used by the Debian prerm, single source of truth)."
    )
    assert re.search(r'python3\s+"\$UNINSTALL_SCRIPT"', rpm_text), (
        "prerm.rpm must run uninstall_permissions.py via "
        '`python3 "$UNINSTALL_SCRIPT"` (same invocation pattern as the '
        "Debian prerm)."
    )
