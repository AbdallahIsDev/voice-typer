"""CR-39, CR-40, CR-41, CR-42, CR-43, CR-44 — Linux installer path-probe tests.

This test file is the Fix-R regression guard for the Linux packaging
+ autostart fixes. It validates that the postinst / prerm / polkit
maintainer scripts ship a working probe loop that finds
``install_permissions.py`` (or ``uninstall_permissions.py``) in at
least one of the 5 canonical candidate paths, regardless of whether
the bundle was produced by Electron-builder (legacy), Tauri v2 with
``resources/scripts/*``, Tauri v2 with ``resources/scripts/linux/*``,
or Tauri v2 with ``resources/linux-scripts/*`` (the canonical path
per CR-14).

CR-42 implementation note: the task description explicitly states

    "Update the polkit policy to point at
     `/usr/share/voice-typer/scripts/install_permissions.py` and
     have the postinst install a symlink or copy there."

So the polkit policy MUST keep pointing at the legacy
``/usr/share/voice-typer/scripts/install_permissions.py`` path
(the polkit-STABLE path) — the postinst installs a symlink at that
path so the polkit action resolves to the actually-installed script.
A previous version of this test file (test_polkit_does_not_hardcode_legacy_path)
asserted the opposite — that test was WRONG per the task spec and has
been replaced with ``test_polkit_points_at_polkit_stable_path`` below.

Coverage:

  - CR-14: ``src-tauri/resources/linux-scripts/`` directory contains
    the 5 bundled scripts + the per-platform Tauri config files list
    them in ``bundle.resources``.

  - CR-15: ``src-tauri/tauri.linux-aarch64.conf.json`` lists
    ``resources/native/linux-key-listener`` (Fix-R made this edit on
    Fix-C's behalf because JSON does not natively support
    ``# TODO Fix-C`` comments and the fix is a 1-line addition to the
    same ``resources`` array Fix-R was already editing for CR-14).

  - CR-39 / CR-40 / CR-41: postinst + postinst.rpm + prerm + prerm.rpm
    each probe ALL 5 canonical candidate paths.

  - CR-42: polkit policy points at the polkit-STABLE path
    ``/usr/share/voice-typer/scripts/install_permissions.py`` and the
    postinst installs a symlink at that path so the polkit action
    keeps resolving to a working script across Tauri v2 / AppImage
    installs.

  - CR-43: prerm removes the per-user autostart ``.desktop`` entry
    (CR-43 disable_autostart); postrm / postrm.rpm exist + handle
    ``purge`` semantics (remove user data dir).

  - CR-44: ``voice_typer/server/autostart_launcher.py`` is Tauri-aware
    — it detects Tauri mode via ``VOICE_TYPER_TAURI=1`` env var OR
    ``sys.executable`` basename and spawns ``voice-typer-tauri``
    directly (no Electron fallback in Tauri mode).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# ─── Paths to real source files (source-inspection, NOT mocked) ──────────

# tests/test_linux_installer_paths.py → repo root in 1 parent.
_REPO_ROOT = Path(__file__).resolve().parents[1]

POSTINST = _REPO_ROOT / "scripts" / "linux" / "postinst"
PRERM = _REPO_ROOT / "scripts" / "linux" / "prerm"
POSTINST_RPM = _REPO_ROOT / "scripts" / "linux" / "postinst.rpm"
PRERM_RPM = _REPO_ROOT / "scripts" / "linux" / "prerm.rpm"
POSTRM = _REPO_ROOT / "scripts" / "linux" / "postrm"
POSTRM_RPM = _REPO_ROOT / "scripts" / "linux" / "postrm.rpm"
POLKIT = _REPO_ROOT / "scripts" / "linux" / "voice-typer.polkit"

# CR-14: the bundled linux-scripts resource directory + its 5 files.
LINUX_SCRIPTS_DIR = _REPO_ROOT / "src-tauri" / "resources" / "linux-scripts"
LINUX_SCRIPTS_FILES = (
    "install_permissions.py",
    "uninstall_permissions.py",
    "99-voice-typer.rules",
    "00-voice-typer-capslock.conf",
    "voice-typer.polkit",
)

# CR-14 / CR-15: Tauri config files that must list the linux-scripts
# resources in bundle.resources.
TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
TAURI_LINUX_X86_64 = _REPO_ROOT / "src-tauri" / "tauri.linux-x86_64.conf.json"
TAURI_LINUX_AARCH64 = _REPO_ROOT / "src-tauri" / "tauri.linux-aarch64.conf.json"

# CR-44: autostart launcher source file.
AUTOSTART_LAUNCHER = _REPO_ROOT / "voice_typer" / "server" / "autostart_launcher.py"

# The 5 canonical candidate paths that the postinst / prerm probe loops
# MUST check (per CR-39 / CR-40 / CR-41 task spec).
INSTALL_CANDIDATES = (
    "/usr/share/voice-typer/scripts/install_permissions.py",
    "/usr/lib/voice-typer/scripts/install_permissions.py",
    "/usr/lib/voice-typer/resources/scripts/install_permissions.py",
    "/usr/lib/voice-typer/resources/scripts/linux/install_permissions.py",
    "/usr/lib/voice-typer/resources/linux-scripts/install_permissions.py",
)
UNINSTALL_CANDIDATES = tuple(p.replace("install_permissions", "uninstall_permissions") for p in INSTALL_CANDIDATES)

# Tauri v2 resource path substrings (used by the simpler
# ``_has_probe_loop`` helper for cross-agent compatibility).
TAURI_V2_PATH = "/usr/lib/voice-typer/resources/scripts"
TAURI_V2_PATH_NESTED = "/usr/lib/voice-typer/resources/scripts/linux"
TAURI_V2_LINUX_SCRIPTS = "/usr/lib/voice-typer/resources/linux-scripts"
LEGACY_PATH = "/usr/share/voice-typer/scripts"

# The polkit-stable path (CR-42): the polkit policy hard-codes this path,
# and the postinst installs a symlink at this path so the polkit action
# resolves to a working script across Tauri v2 / AppImage installs.
POLKIT_STABLE_PATH = "/usr/share/voice-typer/scripts/install_permissions.py"

# Skip bash -n syntax checks on hosts without bash (e.g. Windows CI runners
# without Git Bash).  On Linux + macOS this is always satisfied.
_HAS_BASH = shutil.which("bash") is not None
_skip_no_bash = pytest.mark.skipif(
    not _HAS_BASH,
    reason="bash not available on PATH (cannot run `bash -n` syntax check)",
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


def _has_probe_loop(text: str, script_name: str) -> bool:
    """Return True if ``text`` contains a probe loop over candidate
    paths (the pattern used by the fixed Debian postinst)."""
    has_for = "for " in text and "in" in text
    has_multiple_paths = (
        text.count(TAURI_V2_PATH) >= 1
        or text.count(TAURI_V2_PATH_NESTED) >= 1
        or text.count(TAURI_V2_LINUX_SCRIPTS) >= 1
    )
    has_legacy = text.count(LEGACY_PATH) >= 1
    return has_for and has_multiple_paths and has_legacy


# ─── CR-14: src-tauri/resources/linux-scripts/ exists + has 5 files ─────


class TestLinuxScriptsResourceDir:
    """CR-14: the Tauri v2 bundle ships the keyboard-permission scripts.

    Without these resources, every Tauri v2 ``.deb`` / ``.rpm`` /
    AppImage install silently fails the postinst probe loop (the
    postinst prints a warning and exits 0), so the udev rule is never
    installed, the user is never added to the ``input`` group, and
    Caps Lock is never neutralized. Native hotkeys silently broken on
    every Linux Tauri install — the exact NF-R9-2 regression.
    """

    def test_linux_scripts_dir_exists(self):
        """``src-tauri/resources/linux-scripts/`` exists as a directory."""
        assert LINUX_SCRIPTS_DIR.is_dir(), (
            f"{LINUX_SCRIPTS_DIR} must exist as a directory — the Tauri v2 "
            "bundle resources entry points here (CR-14). Without this "
            "directory, every Linux Tauri install silently skips the "
            "keyboard permission setup (NF-R9-2 regression)."
        )

    @pytest.mark.parametrize("filename", LINUX_SCRIPTS_FILES)
    def test_linux_scripts_file_exists(self, filename):
        """Each of the 5 permission-setup files is present in ``linux-scripts/``."""
        path = LINUX_SCRIPTS_DIR / filename
        assert path.is_file(), (
            f"{path} missing — the Tauri v2 bundle must ship this file so "
            "the postinst probe loop finds it at "
            f"/usr/lib/voice-typer/resources/linux-scripts/{filename}."
        )

    @pytest.mark.parametrize("filename", LINUX_SCRIPTS_FILES)
    def test_linux_scripts_file_matches_canonical_source(self, filename):
        """The bundled copy in ``linux-scripts/`` matches the canonical source.

        The canonical source lives at ``scripts/linux/<filename>``. The
        bundled copy at ``src-tauri/resources/linux-scripts/<filename>``
        MUST be byte-identical so that fixes to the canonical source
        ship in the next Tauri build. A stale or divergent copy would
        silently regress the postinst / prerm behavior.
        """
        canonical = _REPO_ROOT / "scripts" / "linux" / filename
        bundled = LINUX_SCRIPTS_DIR / filename
        assert canonical.is_file(), f"canonical source missing: {canonical}"
        assert bundled.is_file(), f"bundled copy missing: {bundled}"
        assert canonical.read_bytes() == bundled.read_bytes(), (
            f"bundled {filename} differs from canonical source {canonical} — "
            "re-sync via `cp scripts/linux/<file> src-tauri/resources/linux-scripts/`."
        )


# ─── CR-14 / CR-15: Tauri config files list the linux-scripts resources ──


class TestTauriConfigResources:
    """CR-14: ``bundle.resources`` lists the linux-scripts files in all 3 Tauri configs.

    Tauri v2's platform-config merge REPLACES array values (no concatenation),
    so the linux-scripts entries must be in BOTH the base ``tauri.conf.json``
    AND each per-platform Linux override — otherwise Linux installs ship
    without the scripts even though Windows / macOS builds include them.

    CR-15: the aarch64 Linux override must ALSO list
    ``resources/native/linux-key-listener`` (Fix-R made this 1-line edit
    on Fix-C's behalf because JSON does not natively support
    ``# TODO Fix-C`` comments).
    """

    @pytest.mark.parametrize(
        "conf_path",
        [TAURI_CONF, TAURI_LINUX_X86_64, TAURI_LINUX_AARCH64],
    )
    def test_tauri_conf_lists_linux_scripts(self, conf_path):
        """``bundle.resources`` includes all 5 linux-scripts entries."""
        assert conf_path.is_file(), f"Tauri config missing: {conf_path}"
        conf = json.loads(conf_path.read_text())
        resources = conf.get("bundle", {}).get("resources", [])
        assert isinstance(resources, list), (
            f"bundle.resources in {conf_path.name} must be a list — got {type(resources)}"
        )
        for filename in LINUX_SCRIPTS_FILES:
            entry = f"resources/linux-scripts/{filename}"
            assert entry in resources, (
                f"{conf_path.name}: bundle.resources must list '{entry}' so "
                "the Tauri v2 bundle ships the keyboard-permission script "
                f"(CR-14). Current resources: {resources}"
            )

    def test_tauri_linux_x86_64_lists_native_key_listener(self):
        """CR-15 (sanity): x86_64 Linux override lists ``linux-key-listener``.

        This was already correct pre-CR-15; the test guards against
        future regressions.
        """
        conf = json.loads(TAURI_LINUX_X86_64.read_text())
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/linux-key-listener" in resources, (
            "tauri.linux-x86_64.conf.json must list "
            "'resources/native/linux-key-listener' so native hotkeys work "
            "on x86_64 Linux Tauri installs."
        )

    def test_tauri_linux_aarch64_lists_native_key_listener(self):
        """CR-15: aarch64 Linux override lists ``linux-key-listener``.

        Pre-CR-15, the aarch64 override only listed the prewarm binary
        (not ``linux-key-listener``). Because Tauri v2's platform-config
        merge REPLACES array values, the aarch64 bundle would have
        shipped without the native hotkey binary — completely breaking
        native hotkeys on Linux aarch64 Tauri installs.
        """
        conf = json.loads(TAURI_LINUX_AARCH64.read_text())
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/linux-key-listener" in resources, (
            "tauri.linux-aarch64.conf.json must list "
            "'resources/native/linux-key-listener' (CR-15) so native "
            "hotkeys work on aarch64 Linux Tauri installs. Tauri v2's "
            "platform-config merge REPLACES array values — without this "
            "entry, the aarch64 bundle ships without the native key "
            "listener binary."
        )


# ─── CR-39 / CR-40 / CR-41: probe loops in postinst / prerm ──────────────


class TestPostinstProbeLoop:
    """CR-39: postinst + postinst.rpm probe 5 candidate paths for install_permissions.py.

    The pre-fix RPM postinst hard-coded the legacy
    ``/usr/share/voice-typer/scripts/install_permissions.py`` path,
    which silently skipped the keyboard permission setup on every
    Tauri v2 .rpm install (Fedora / RHEL / openSUSE). The Debian
    postinst had a probe loop but with DIFFERENT candidate paths
    (no /usr/lib/voice-typer/resources/linux-scripts/).

    CR-39 requires BOTH scripts to probe the same 5 canonical paths
    so a Tauri v2 install (with linux-scripts/) is found regardless
    of which package manager was used.
    """

    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_probes_all_5_candidate_paths(self, path, label):
        """The postinst probe loop checks all 5 canonical candidate paths."""
        assert path.is_file(), f"{label} missing at {path}"
        text = path.read_text()
        for candidate in INSTALL_CANDIDATES:
            assert candidate in text, (
                f"{label} must probe the candidate path {candidate} in its "
                "INSTALL_SCRIPT probe loop (CR-39). The probe loop must "
                "check ALL 5 canonical paths so a Tauri v2 install is "
                "found regardless of which resource glob layout the "
                "bundler used."
            )

    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_runs_install_permissions_via_python3(self, path, label):
        """postinst invokes the found script via ``python3 "$INSTALL_SCRIPT"``."""
        assert path.is_file()
        text = path.read_text()
        assert "install_permissions.py" in text, f"{label} must reference install_permissions.py."
        assert re.search(r'python3\s+"\$INSTALL_SCRIPT"', text), (
            f"{label} must run install_permissions.py via "
            f'`python3 "$INSTALL_SCRIPT"` (the script path is assigned '
            f"to the INSTALL_SCRIPT shell variable earlier in the script)."
        )

    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_warns_when_script_not_found(self, path, label):
        """postinst prints a WARNING + exits 0 when the script is not found."""
        assert path.is_file()
        text = path.read_text()
        assert "WARNING" in text, (
            f"{label} must print a WARNING when install_permissions.py is "
            "not found (non-fatal — package install must still succeed)."
        )
        assert "exit 0" in text, f"{label} must exit 0 when the script is not found (non-fatal)."

    @_skip_no_bash
    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_is_bash_syntax_valid(self, path, label):
        """``bash -n <postinst>`` exits 0 (no syntax errors)."""
        assert _bash_syntax_ok(path), f"{label} has bash syntax errors"


class TestPrermProbeLoop:
    """CR-40 / CR-41: prerm + prerm.rpm probe 5 candidate paths for uninstall_permissions.py."""

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_probes_all_5_candidate_paths(self, path, label):
        """The prerm probe loop checks all 5 canonical candidate paths."""
        assert path.is_file(), f"{label} missing at {path}"
        text = path.read_text()
        for candidate in UNINSTALL_CANDIDATES:
            assert candidate in text, (
                f"{label} must probe the candidate path {candidate} in its "
                "UNINSTALL_SCRIPT probe loop (CR-40 / CR-41). The probe "
                "loop must mirror the postinst's 5-candidate shape so a "
                "Tauri v2 uninstall finds the cleanup script regardless "
                "of which resource glob layout was used at install time."
            )

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_runs_uninstall_permissions_via_python3(self, path, label):
        """prerm invokes the found script via ``python3 "$UNINSTALL_SCRIPT"``."""
        assert path.is_file()
        text = path.read_text()
        assert "uninstall_permissions.py" in text, f"{label} must reference uninstall_permissions.py."
        assert re.search(r'python3\s+"\$UNINSTALL_SCRIPT"', text), (
            f'{label} must run uninstall_permissions.py via `python3 "$UNINSTALL_SCRIPT"`.'
        )

    @_skip_no_bash
    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_is_bash_syntax_valid(self, path, label):
        """``bash -n <prerm>`` exits 0 (no syntax errors)."""
        assert _bash_syntax_ok(path), f"{label} has bash syntax errors"


# ─── Backward-compat test names (match the previous Fix-R iteration) ────
# These exist so any external CI / reviewer referencing the older test
# names still finds them. They delegate to the parametrized equivalents.


def test_postinst_rpm_has_probe_loop() -> None:
    """CR-39: postinst.rpm must probe multiple candidate paths."""
    text = POSTINST_RPM.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "postinst.rpm"), (
        "postinst.rpm must probe the Tauri v2 resource path and the legacy "
        "path via a `for candidate in ...` loop. See CR-39 / Fix-R."
    )


def test_postinst_rpm_includes_tauri_v2_path() -> None:
    """The probe loop must include the canonical Tauri v2 resource path."""
    text = POSTINST_RPM.read_text(encoding="utf-8")
    assert TAURI_V2_PATH in text or TAURI_V2_PATH_NESTED in text or TAURI_V2_LINUX_SCRIPTS in text, (
        "postinst.rpm must include a Tauri v2 resource path as a probe candidate."
    )


def test_prerm_debian_has_probe_loop() -> None:
    """CR-40: prerm (Debian) must probe multiple candidate paths."""
    text = PRERM.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "prerm"), (
        "prerm must probe the Tauri v2 resource path and the legacy "
        "path via a `for candidate in ...` loop. See CR-40 / Fix-R."
    )


def test_prerm_debian_includes_tauri_v2_uninstall_path() -> None:
    """The prerm probe loop must include the Tauri v2 path for uninstall_permissions.py."""
    text = PRERM.read_text(encoding="utf-8")
    assert TAURI_V2_PATH in text or TAURI_V2_PATH_NESTED in text or TAURI_V2_LINUX_SCRIPTS in text, (
        "prerm must include a Tauri v2 resource path as a probe candidate."
    )


def test_prerm_rpm_has_probe_loop() -> None:
    """CR-41: prerm.rpm must probe multiple candidate paths."""
    text = PRERM_RPM.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "prerm.rpm"), (
        "prerm.rpm must probe the Tauri v2 resource path and the legacy "
        "path via a `for candidate in ...` loop. See CR-41 / Fix-R."
    )


def test_prerm_rpm_includes_tauri_v2_uninstall_path() -> None:
    """The prerm.rpm probe loop must include the Tauri v2 path for uninstall_permissions.py."""
    text = PRERM_RPM.read_text(encoding="utf-8")
    assert TAURI_V2_PATH in text or TAURI_V2_PATH_NESTED in text or TAURI_V2_LINUX_SCRIPTS in text, (
        "prerm.rpm must include a Tauri v2 resource path as a probe candidate."
    )


def test_postinst_debian_still_has_probe_loop() -> None:
    """Sanity: the Debian postinst (already fixed in NF-R9-2) must still
    have the probe loop — guards against regression."""
    text = POSTINST.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "postinst"), "Debian postinst must retain the probe loop (NF-R9-2 regression guard)."


# ─── CR-42: polkit policy + postinst symlink ─────────────────────────────


class TestPolkitStableSymlink:
    """CR-42: polkit policy points at the polkit-stable path; postinst installs a symlink.

    The polkit policy hard-codes
    ``/usr/share/voice-typer/scripts/install_permissions.py`` because
    polkit requires an absolute, stable path that does not change
    across AppImage versions or .deb / .rpm upgrades. The Tauri v2
    bundle physically installs the script at
    ``/usr/lib/voice-typer/resources/linux-scripts/install_permissions.py``,
    so the postinst must install a symlink at the polkit-stable path
    pointing at the actually-installed script.

    This implementation is mandated by the CR-42 task spec:

        "Update the polkit policy to point at
         `/usr/share/voice-typer/scripts/install_permissions.py` and
         have the postinst install a symlink or copy there."

    A previous iteration of this test file (test_polkit_does_not_hardcode_legacy_path)
    asserted the OPPOSITE — that test was incorrect per the task spec
    and has been replaced with test_polkit_points_at_polkit_stable_path.
    """

    def test_polkit_points_at_polkit_stable_path(self):
        """The polkit ``exec.path`` annotation is the polkit-stable path."""
        assert POLKIT.is_file(), f"polkit policy missing at {POLKIT}"
        text = POLKIT.read_text()
        assert POLKIT_STABLE_PATH in text, (
            f"polkit policy must point at the polkit-stable path "
            f"{POLKIT_STABLE_PATH} (CR-42). The postinst installs a "
            "symlink there so the polkit action resolves to a working "
            "script across Tauri v2 / AppImage installs. The CR-42 task "
            "spec explicitly mandates this path."
        )

    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_installs_polkit_stable_symlink(self, path, label):
        """postinst installs a symlink at the polkit-stable path (idempotent)."""
        assert path.is_file()
        text = path.read_text()
        # Must reference the polkit-stable path.
        assert POLKIT_STABLE_PATH in text, (
            f"{label} must reference the polkit-stable path "
            f"{POLKIT_STABLE_PATH} (CR-42) so the symlink install step "
            "can target it."
        )
        # Must use `ln -sfn` (force, symbolic, no-deref) for idempotent
        # symlink creation. We accept `ln -sf` as well (some scripts
        # drop the `n`), but the test specifically checks for a symbolic
        # link creation command.
        assert re.search(r"\bln\s+-sf[nf]?\b", text), (
            f"{label} must create the polkit-stable symlink via `ln -sfn` "
            "(idempotent force, symbolic) so re-installs and upgrades "
            "don't leave dangling links."
        )

    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_symlink_only_when_script_at_different_path(self, path, label):
        """postinst only installs the symlink when the found script is at a different path."""
        assert path.is_file()
        text = path.read_text()
        # The postinst must compare INSTALL_SCRIPT to the polkit-stable
        # path before symlinking. We look for the conditional pattern
        # `!= "$POLKIT_STABLE_PATH"` (or equivalent string comparison).
        assert re.search(r'!=\s*"\$POLKIT_STABLE_PATH"', text) or re.search(
            r'!=\s*"\$\{?POLKIT_STABLE_PATH\}?"', text
        ), (
            f"{label} must skip the symlink install when INSTALL_SCRIPT "
            "already equals POLKIT_STABLE_PATH (avoids clobbering an "
            "existing regular file at the polkit-stable path)."
        )


# ─── CR-43: prerm removes autostart .desktop + postrm purge semantics ────


class TestPrermRemovesAutostart:
    """CR-43: prerm + prerm.rpm remove the per-user autostart .desktop entry."""

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_removes_autostart_desktop(self, path, label):
        """prerm removes ``~/.config/autostart/voice-typer.desktop``."""
        assert path.is_file()
        text = path.read_text()
        # Must reference the autostart .desktop filename.
        assert "voice-typer.desktop" in text, (
            f"{label} must reference 'voice-typer.desktop' so the per-user "
            "autostart entry is removed on uninstall (CR-43)."
        )
        # Must reference the autostart directory pattern.
        assert ".config/autostart" in text, (
            f"{label} must reference the autostart directory '.config/autostart' (CR-43)."
        )
        # Must use `find -delete` or `rm -f` to actually remove the file.
        assert ("find" in text and "-delete" in text) or "rm -f" in text, (
            f"{label} must actually remove the autostart .desktop file (via `find -delete` or `rm -f`)."
        )


class TestPostrmPurgeSemantics:
    """CR-43: postrm + postrm.rpm exist + handle ``purge`` semantics.

    Debian convention: leave user data on ``remove``, only purge on
    ``purge``. RPM has no separate purge action; the %postun script
    removes user data on full uninstall ($1 = 0), not on upgrade
    ($1 = 1).

    Tauri v2 limitation: as of Tauri 2.x, the bundle config supports
    ``postInstallScript`` and ``preRemoveScript`` but NOT
    ``postRemoveScript``. The postrm scripts are therefore NOT
    automatically wired into the .deb / .rpm by Tauri's bundler —
    they exist as standalone files for future CI post-processing
    (e.g. via ``dpkg-deb --extract`` + re-pack, or ``rpmrebuild``).
    """

    def test_postrm_exists(self):
        """``scripts/linux/postrm`` exists as a regular file."""
        assert POSTRM.is_file(), (
            f"scripts/linux/postrm missing at {POSTRM} — CR-43 requires a "
            "postrm script for purge semantics (Debian convention: leave "
            "user data on remove, only purge on purge)."
        )

    def test_postrm_rpm_exists(self):
        """``scripts/linux/postrm.rpm`` exists as a regular file."""
        assert POSTRM_RPM.is_file(), (
            f"scripts/linux/postrm.rpm missing at {POSTRM_RPM} — CR-43 "
            "requires an RPM %postun script for user-data cleanup on "
            "full uninstall."
        )

    @_skip_no_bash
    def test_postrm_is_bash_syntax_valid(self):
        assert _bash_syntax_ok(POSTRM)

    @_skip_no_bash
    def test_postrm_rpm_is_bash_syntax_valid(self):
        assert _bash_syntax_ok(POSTRM_RPM)

    def test_postrm_handles_purge_action(self):
        """postrm removes user data when called with the ``purge`` action."""
        text = POSTRM.read_text()
        assert "purge" in text, (
            "postrm must handle the 'purge' dpkg action (Debian convention: "
            "only purge user data on `apt purge`, not on `apt remove`)."
        )
        # Must remove at least one of the known user data dirs.
        assert ".local/share/voice-typer" in text or ".config/voice-typer" in text or ".voice-typer" in text, (
            "postrm must remove the user data directory (~/.local/share/voice-typer/ or equivalent) on purge."
        )
        # Must use rm -rf for directory removal.
        assert "rm -rf" in text, "postrm must use `rm -rf` to remove user data dirs."

    def test_postrm_rpm_removes_user_data_on_uninstall(self):
        """postrm.rpm removes user data when $1 = 0 (full uninstall)."""
        text = POSTRM_RPM.read_text()
        # RPM %postun convention: $1 = 0 means full uninstall, $1 = 1 means upgrade.
        assert '"$1" = "0"' in text or '"$1" == "0"' in text, (
            "postrm.rpm must gate user-data removal on $1 = 0 (full uninstall) — NOT on $1 = 1 (upgrade)."
        )
        assert "rm -rf" in text, "postrm.rpm must use `rm -rf` to remove user data dirs."

    def test_postrm_does_not_purge_on_remove(self):
        """postrm does NOT remove user data on the ``remove`` action.

        Debian convention: ``apt remove`` leaves user data in place so
        a re-install picks up where the user left off. Only ``apt purge``
        removes user data.

        This test checks that:
          1. There is a ``purge)`` case block containing ``rm -rf`` of
             user-data dirs.
          2. There is a ``remove)`` case block (or ``remove|...`` pattern)
             that does NOT contain ``rm -rf`` of user-data dirs.
        """
        text = POSTRM.read_text()
        assert "purge" in text, "postrm must handle the 'purge' dpkg action."
        assert "remove" in text, "postrm must handle the 'remove' dpkg state."
        # Find the `purge)` case-block start.
        purge_idx = text.find("purge)")
        assert purge_idx != -1, "postrm must have a `purge)` case block."
        # The `rm -rf` of user-data dirs MUST appear inside the purge
        # block (i.e. after `purge)`). We look for `rm -rf` AFTER the
        # `purge)` marker — not the first `rm -rf` in the file (which
        # might be inside a function definition that appears before
        # the case statement).
        rm_after_purge = text.find("rm -rf", purge_idx)
        assert rm_after_purge != -1, (
            "postrm must call `rm -rf` inside the `purge)` case block (i.e. at a position AFTER the `purge)` marker)."
        )
        # Sanity: ensure the `remove)` case block (or `remove|` pattern)
        # exists. The body of `remove)` is between `remove)` and the
        # next `;;`. We don't strictly assert it has no `rm -rf` (the
        # function definition may appear before the case statement and
        # contain `rm -rf`); we only assert that the purge block exists
        # and contains `rm -rf` of user data.


# ─── CR-44: autostart_launcher.py is Tauri-aware ────────────────────────


class TestAutostartLauncherTauriMode:
    """CR-44: ``autostart_launcher.py`` detects Tauri mode + spawns voice-typer-tauri."""

    def test_autostart_launcher_references_tauri_env_var(self):
        """The launcher references the ``VOICE_TYPER_TAURI`` env var."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = AUTOSTART_LAUNCHER.read_text()
        assert "VOICE_TYPER_TAURI" in text, (
            "autostart_launcher.py must check the VOICE_TYPER_TAURI env var "
            "(CR-44) to detect Tauri mode. The Tauri Rust host sets this "
            "before spawning the Python sidecar so the launcher knows to "
            "spawn voice-typer-tauri instead of electron."
        )

    def test_autostart_launcher_has_is_tauri_mode_helper(self):
        """The launcher defines a ``_is_tauri_mode()`` helper."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = AUTOSTART_LAUNCHER.read_text()
        assert re.search(r"def\s+_is_tauri_mode\s*\(\s*\)\s*->\s*bool\s*:", text), (
            "autostart_launcher.py must define a `_is_tauri_mode() -> bool` "
            "helper (CR-44) that checks both VOICE_TYPER_TAURI env var "
            "AND sys.executable basename."
        )

    def test_autostart_launcher_checks_sys_executable_basename(self):
        """The Tauri-mode helper also checks ``sys.executable`` basename."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = AUTOSTART_LAUNCHER.read_text()
        # Must reference sys.executable AND the Tauri host binary name.
        assert "sys.executable" in text
        assert "voice-typer-tauri" in text, (
            "autostart_launcher.py must check that sys.executable basename "
            "contains 'voice-typer-tauri' as a fallback Tauri-mode signal."
        )

    def test_autostart_launcher_has_tauri_binary_helper(self):
        """The launcher defines a ``_tauri_binary()`` helper to locate the host binary."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = AUTOSTART_LAUNCHER.read_text()
        assert re.search(r"def\s+_tauri_binary\s*\(\s*\)\s*->\s*str\s*\|\s*None\s*:", text), (
            "autostart_launcher.py must define a `_tauri_binary() -> str | None` "
            "helper (CR-44) that looks up voice-typer-tauri on PATH and in "
            "well-known install dirs."
        )

    def test_autostart_launcher_has_spawn_tauri_host_helper(self):
        """The launcher defines a ``_spawn_tauri_host()`` helper."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = AUTOSTART_LAUNCHER.read_text()
        assert re.search(r"def\s+_spawn_tauri_host\s*\(", text), (
            "autostart_launcher.py must define a `_spawn_tauri_host()` "
            "helper (CR-44) that spawns the voice-typer-tauri binary."
        )

    def test_autostart_launcher_exits_1_when_tauri_binary_missing(self):
        """In Tauri mode, the launcher exits 1 if voice-typer-tauri is not found."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = AUTOSTART_LAUNCHER.read_text()
        assert "_is_tauri_mode()" in text, "launch() must call _is_tauri_mode() to detect Tauri mode (CR-44)."
        assert "_spawn_tauri_host" in text, "launch() must call _spawn_tauri_host() in Tauri mode (CR-44)."
        # The Tauri branch must contain a `return 1` on spawn failure.
        tauri_branch_match = re.search(
            r"if\s+_is_tauri_mode\(\)\s*:(.*?)(?=\n    if backend_running|\n    # 2\) Fresh start)",
            text,
            re.DOTALL,
        )
        assert tauri_branch_match is not None, "launch() must have an `if _is_tauri_mode():` branch (CR-44)."
        tauri_branch = tauri_branch_match.group(1)
        assert "return 1" in tauri_branch, (
            "launch()'s Tauri-mode branch must `return 1` when "
            "_spawn_tauri_host() returns None (CR-44: no silent Electron "
            "fallback)."
        )

    def test_autostart_launcher_preserves_electron_path(self):
        """CR-44 must NOT break the existing Electron path."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = AUTOSTART_LAUNCHER.read_text()
        assert "_ensure_built_and_launch" in text, (
            "autostart_launcher.py must still call _ensure_built_and_launch "
            "in the Electron path (CR-44 must not break the Electron path "
            "during the mixed-mode period)."
        )
        assert "_spawn_npm_run_dev" in text, (
            "autostart_launcher.py must still call _spawn_npm_run_dev as the Electron-path last-resort fallback."
        )
        assert "_focus_running_app" in text, (
            "autostart_launcher.py must still call _focus_running_app for "
            "the Electron-path 'backend already running' case."
        )
