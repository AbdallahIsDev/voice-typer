""", , , , , Linux installer path-probe tests."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

_REPO_ROOT = Path(__file__).resolve().parents[1]

POSTINST = _REPO_ROOT / "scripts" / "linux" / "postinst"
PRERM = _REPO_ROOT / "scripts" / "linux" / "prerm"
POSTINST_RPM = _REPO_ROOT / "scripts" / "linux" / "postinst.rpm"
PRERM_RPM = _REPO_ROOT / "scripts" / "linux" / "prerm.rpm"
POSTRM = _REPO_ROOT / "scripts" / "linux" / "postrm"
POSTRM_RPM = _REPO_ROOT / "scripts" / "linux" / "postrm.rpm"
POLKIT = _REPO_ROOT / "scripts" / "linux" / "lausu.polkit"

LINUX_SCRIPTS_DIR = _REPO_ROOT / "src-tauri" / "resources" / "linux-scripts"
LINUX_SCRIPTS_FILES = (
    "install_permissions.py",
    "uninstall_permissions.py",
    "99-lausu.rules",
    "00-lausu-capslock.conf",
    "lausu.polkit",
)

# Tauri config files that must list the linux-scripts
TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
TAURI_LINUX_X86_64 = _REPO_ROOT / "src-tauri" / "tauri.linux-x86_64.conf.json"
TAURI_LINUX_AARCH64 = _REPO_ROOT / "src-tauri" / "tauri.linux-aarch64.conf.json"

AUTOSTART_LAUNCHER = _REPO_ROOT / "voice_typer" / "server" / "autostart_launcher.py"
_AUTOSTART_PKG = _REPO_ROOT / "voice_typer" / "server" / "autostart"


def _launcher_text() -> str:
    parts = [AUTOSTART_LAUNCHER.read_text(encoding="utf-8")]
    parts.extend(p.read_text(encoding="utf-8") for p in sorted(_AUTOSTART_PKG.glob("*.py")))
    return "\n".join(parts)


# The 5 canonical candidate paths that the postinst / prerm probe loops
INSTALL_CANDIDATES = (
    "/usr/share/lausu/scripts/install_permissions.py",
    "/usr/lib/lausu/scripts/install_permissions.py",
    "/usr/lib/lausu/resources/scripts/install_permissions.py",
    "/usr/lib/lausu/resources/scripts/linux/install_permissions.py",
    "/usr/lib/lausu/resources/linux-scripts/install_permissions.py",
)
UNINSTALL_CANDIDATES = tuple(p.replace("install_permissions", "uninstall_permissions") for p in INSTALL_CANDIDATES)

# Tauri v2 resource path substrings (used by the simpler
TAURI_V2_PATH = "/usr/lib/lausu/resources/scripts"
TAURI_V2_PATH_NESTED = "/usr/lib/lausu/resources/scripts/linux"
TAURI_V2_LINUX_SCRIPTS = "/usr/lib/lausu/resources/linux-scripts"
LEGACY_PATH = "/usr/share/lausu/scripts"

# The polkit-stable path : the polkit policy hard-codes this path,
POLKIT_STABLE_PATH = "/usr/share/lausu/scripts/install_permissions.py"

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


def _has_probe_loop(text: str, script_name: str) -> bool:
    """Return True if ``text`` contains a probe loop over candidate"""
    has_for = "for " in text and "in" in text
    has_multiple_paths = (
        text.count(TAURI_V2_PATH) >= 1
        or text.count(TAURI_V2_PATH_NESTED) >= 1
        or text.count(TAURI_V2_LINUX_SCRIPTS) >= 1
    )
    has_legacy = text.count(LEGACY_PATH) >= 1
    return has_for and has_multiple_paths and has_legacy


class TestLinuxScriptsResourceDir:
    """the Tauri v2 bundle ships the keyboard-permission scripts."""

    def test_linux_scripts_dir_exists(self):
        """``src-tauri/resources/linux-scripts/`` exists as a directory."""
        assert LINUX_SCRIPTS_DIR.is_dir(), (
            f"{LINUX_SCRIPTS_DIR} must exist as a directory, the Tauri v2 "
            "bundle resources entry points here . Without this "
            "directory, every Linux Tauri install silently skips the "
            "keyboard permission setup (NF-R9-2 regression)."
        )

    @pytest.mark.parametrize("filename", LINUX_SCRIPTS_FILES)
    def test_linux_scripts_file_exists(self, filename):
        """Each of the 5 permission-setup files is present in ``linux-scripts/``."""
        path = LINUX_SCRIPTS_DIR / filename
        assert path.is_file(), (
            f"{path} missing, the Tauri v2 bundle must ship this file so "
            "the postinst probe loop finds it at "
            f"/usr/lib/lausu/resources/linux-scripts/{filename}."
        )

    @pytest.mark.parametrize("filename", LINUX_SCRIPTS_FILES)
    def test_linux_scripts_file_matches_canonical_source(self, filename):
        """The bundled copy in ``linux-scripts/`` matches the canonical source."""
        canonical = _REPO_ROOT / "scripts" / "linux" / filename
        bundled = LINUX_SCRIPTS_DIR / filename
        assert canonical.is_file(), f"canonical source missing: {canonical}"
        assert bundled.is_file(), f"bundled copy missing: {bundled}"
        assert canonical.read_bytes() == bundled.read_bytes(), (
            f"bundled {filename} differs from canonical source {canonical}, "
            "re-sync via `cp scripts/linux/<file> src-tauri/resources/linux-scripts/`."
        )


# Tauri config files list the linux-scripts resources ──


class TestTauriConfigResources:
    """``bundle.resources`` lists the linux-scripts files in all 3 Tauri configs."""

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
            f"bundle.resources in {conf_path.name} must be a list, got {type(resources)}"
        )
        for filename in LINUX_SCRIPTS_FILES:
            entry = f"resources/linux-scripts/{filename}"
            assert entry in resources, (
                f"{conf_path.name}: bundle.resources must list '{entry}' so "
                "the Tauri v2 bundle ships the keyboard-permission script "
                f". Current resources: {resources}"
            )

    def test_tauri_linux_x86_64_lists_native_key_listener(self):
        """(sanity): x86_64 Linux override lists ``linux-key-listener``."""
        conf = json.loads(TAURI_LINUX_X86_64.read_text())
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/linux-key-listener" in resources, (
            "tauri.linux-x86_64.conf.json must list "
            "'resources/native/linux-key-listener' so native hotkeys work "
            "on x86_64 Linux Tauri installs."
        )

    def test_tauri_linux_aarch64_lists_native_key_listener(self):
        """aarch64 Linux override lists ``linux-key-listener``."""
        conf = json.loads(TAURI_LINUX_AARCH64.read_text())
        resources = conf.get("bundle", {}).get("resources", [])
        assert "resources/native/linux-key-listener" in resources, (
            "tauri.linux-aarch64.conf.json must list "
            "'resources/native/linux-key-listener'  so native "
            "hotkeys work on aarch64 Linux Tauri installs. Tauri v2's "
            "platform-config merge REPLACES array values, without this "
            "entry, the aarch64 bundle ships without the native key "
            "listener binary."
        )


class TestPostinstProbeLoop:
    """postinst + postinst.rpm probe 5 candidate paths for install_permissions.py."""

    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_probes_all_5_candidate_paths(self, path, label):
        """The postinst probe loop checks all 5 canonical candidate paths."""
        assert path.is_file(), f"{label} missing at {path}"
        text = path.read_text()
        for candidate in INSTALL_CANDIDATES:
            assert candidate in text, (
                f"{label} must probe the candidate path {candidate} in its "
                "INSTALL_SCRIPT probe loop . The probe loop must "
                "check ALL 5 canonical paths so a Tauri v2 install is "
                "found regardless of which resource glob layout the "
                "bundler used."
            )

    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_runs_install_permissions_via_python3(self, path, label):
        """postinst invokes the found script via ``python3 \"$INSTALL_SCRIPT\"``."""
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
            "not found (non-fatal, package install must still succeed)."
        )
        assert "exit 0" in text, f"{label} must exit 0 when the script is not found (non-fatal)."

    @_skip_no_bash
    @pytest.mark.parametrize("path,label", [(POSTINST, "postinst"), (POSTINST_RPM, "postinst.rpm")])
    def test_postinst_is_bash_syntax_valid(self, path, label):
        """``bash -n <postinst>`` exits 0 (no syntax errors)."""
        assert _bash_syntax_ok(path), f"{label} has bash syntax errors"


class TestPrermProbeLoop:
    """prerm + prerm.rpm probe 5 candidate paths for uninstall_permissions.py."""

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_probes_all_5_candidate_paths(self, path, label):
        """The prerm probe loop checks all 5 canonical candidate paths."""
        assert path.is_file(), f"{label} missing at {path}"
        text = path.read_text()
        for candidate in UNINSTALL_CANDIDATES:
            assert candidate in text, (
                f"{label} must probe the candidate path {candidate} in its "
                "UNINSTALL_SCRIPT probe loop . The probe "
                "loop must mirror the postinst's 5-candidate shape so a "
                "Tauri v2 uninstall finds the cleanup script regardless "
                "of which resource glob layout was used at install time."
            )

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_runs_uninstall_permissions_via_python3(self, path, label):
        """prerm invokes the found script via ``python3 \"$UNINSTALL_SCRIPT\"``."""
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


# These exist so any external CI / reviewer referencing the older test


def test_postinst_rpm_has_probe_loop() -> None:
    """postinst.rpm must probe multiple candidate paths."""
    text = POSTINST_RPM.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "postinst.rpm"), (
        "postinst.rpm must probe the Tauri v2 resource path and the legacy "
        "path via a `for candidate in ...` loop. See  / Fix-R."
    )


def test_postinst_rpm_includes_tauri_v2_path() -> None:
    """The probe loop must include the canonical Tauri v2 resource path."""
    text = POSTINST_RPM.read_text(encoding="utf-8")
    assert TAURI_V2_PATH in text or TAURI_V2_PATH_NESTED in text or TAURI_V2_LINUX_SCRIPTS in text, (
        "postinst.rpm must include a Tauri v2 resource path as a probe candidate."
    )


def test_prerm_debian_has_probe_loop() -> None:
    """prerm (Debian) must probe multiple candidate paths."""
    text = PRERM.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "prerm"), (
        "prerm must probe the Tauri v2 resource path and the legacy "
        "path via a `for candidate in ...` loop. See  / Fix-R."
    )


def test_prerm_debian_includes_tauri_v2_uninstall_path() -> None:
    """The prerm probe loop must include the Tauri v2 path for uninstall_permissions.py."""
    text = PRERM.read_text(encoding="utf-8")
    assert TAURI_V2_PATH in text or TAURI_V2_PATH_NESTED in text or TAURI_V2_LINUX_SCRIPTS in text, (
        "prerm must include a Tauri v2 resource path as a probe candidate."
    )


def test_prerm_rpm_has_probe_loop() -> None:
    """prerm.rpm must probe multiple candidate paths."""
    text = PRERM_RPM.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "prerm.rpm"), (
        "prerm.rpm must probe the Tauri v2 resource path and the legacy "
        "path via a `for candidate in ...` loop. See  / Fix-R."
    )


def test_prerm_rpm_includes_tauri_v2_uninstall_path() -> None:
    """The prerm.rpm probe loop must include the Tauri v2 path for uninstall_permissions.py."""
    text = PRERM_RPM.read_text(encoding="utf-8")
    assert TAURI_V2_PATH in text or TAURI_V2_PATH_NESTED in text or TAURI_V2_LINUX_SCRIPTS in text, (
        "prerm.rpm must include a Tauri v2 resource path as a probe candidate."
    )


def test_postinst_debian_still_has_probe_loop() -> None:
    """Sanity: the Debian postinst (already fixed in NF-R9-2) must still"""
    text = POSTINST.read_text(encoding="utf-8")
    assert _has_probe_loop(text, "postinst"), "Debian postinst must retain the probe loop (NF-R9-2 regression guard)."


class TestPolkitStableSymlink:
    """polkit policy points at the polkit-stable path; postinst installs a symlink."""

    def test_polkit_points_at_polkit_stable_path(self):
        """The polkit ``exec.path`` annotation is the polkit-stable path."""
        assert POLKIT.is_file(), f"polkit policy missing at {POLKIT}"
        # The polkit XML is UTF-8; the default locale encoding on Windows
        text = POLKIT.read_text(encoding="utf-8")
        assert POLKIT_STABLE_PATH in text, (
            f"polkit policy must point at the polkit-stable path "
            f"{POLKIT_STABLE_PATH} . The postinst installs a "
            "symlink there so the polkit action resolves to a working "
            "script across Tauri v2 / AppImage installs. The  task "
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
            f"{POLKIT_STABLE_PATH}  so the symlink install step "
            "can target it."
        )
        # Must use `ln -sfn` (force, symbolic, no-deref) for idempotent
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
        assert re.search(r'!=\s*"\$POLKIT_STABLE_PATH"', text) or re.search(
            r'!=\s*"\$\{?POLKIT_STABLE_PATH\}?"', text
        ), (
            f"{label} must skip the symlink install when INSTALL_SCRIPT "
            "already equals POLKIT_STABLE_PATH (avoids clobbering an "
            "existing regular file at the polkit-stable path)."
        )


class TestPrermRemovesAutostart:
    """prerm + prerm.rpm remove the per-user autostart .desktop entry."""

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_removes_autostart_desktop(self, path, label):
        """prerm removes ``~/.config/autostart/lausu.desktop``."""
        assert path.is_file()
        text = path.read_text()
        # Must reference the autostart .desktop filename.
        assert "lausu.desktop" in text, (
            f"{label} must reference 'lausu.desktop' so the per-user autostart entry is removed on uninstall ."
        )
        # Must reference the autostart directory pattern.
        assert ".config/autostart" in text, f"{label} must reference the autostart directory '.config/autostart' ."
        # Must use `find -delete` or `rm -f` to actually remove the file.
        assert ("find" in text and "-delete" in text) or "rm -f" in text, (
            f"{label} must actually remove the autostart .desktop file (via `find -delete` or `rm -f`)."
        )


class TestPostrmPurgeSemantics:
    """postrm + postrm.rpm exist + handle ``purge`` semantics."""

    def test_postrm_exists(self):
        """``scripts/linux/postrm`` exists as a regular file."""
        assert POSTRM.is_file(), (
            f"scripts/linux/postrm missing at {POSTRM},  requires a "
            "postrm script for purge semantics (Debian convention: leave "
            "user data on remove, only purge on purge)."
        )

    def test_postrm_rpm_exists(self):
        """``scripts/linux/postrm.rpm`` exists as a regular file."""
        assert POSTRM_RPM.is_file(), (
            f"scripts/linux/postrm.rpm missing at {POSTRM_RPM},  "
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
        assert ".local/share/lausu" in text or ".config/lausu" in text or ".lausu" in text, (
            "postrm must remove the user data directory (~/.local/share/lausu/ or equivalent) on purge."
        )
        # Must use rm -rf for directory removal.
        assert "rm -rf" in text, "postrm must use `rm -rf` to remove user data dirs."

    def test_postrm_rpm_removes_user_data_on_uninstall(self):
        """postrm.rpm removes user data when $1 = 0 (full uninstall)."""
        text = POSTRM_RPM.read_text()
        # RPM %postun convention: $1 = 0 means full uninstall, $1 = 1 means upgrade.
        assert '"$1" = "0"' in text or '"$1" == "0"' in text, (
            "postrm.rpm must gate user-data removal on $1 = 0 (full uninstall). NOT on $1 = 1 (upgrade)."
        )
        assert "rm -rf" in text, "postrm.rpm must use `rm -rf` to remove user data dirs."

    def test_postrm_does_not_purge_on_remove(self):
        """postrm does NOT remove user data on the ``remove`` action."""
        text = POSTRM.read_text()
        assert "purge" in text, "postrm must handle the 'purge' dpkg action."
        assert "remove" in text, "postrm must handle the 'remove' dpkg state."
        # Find the `purge)` case-block start.
        purge_idx = text.find("purge)")
        assert purge_idx != -1, "postrm must have a `purge)` case block."
        # The `rm -rf` of user-data dirs MUST appear inside the purge
        rm_after_purge = text.find("rm -rf", purge_idx)
        assert rm_after_purge != -1, (
            "postrm must call `rm -rf` inside the `purge)` case block (i.e. at a position AFTER the `purge)` marker)."
        )
        # Sanity: ensure the `remove)` case block (or `remove|` pattern)


class TestAutostartLauncherTauriMode:
    """``autostart_launcher.py`` detects Tauri mode + spawns lausu-tauri."""

    def test_autostart_launcher_references_tauri_env_var(self):
        """The launcher references the ``VOICE_TYPER_TAURI`` env var."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = _launcher_text()
        assert "VOICE_TYPER_TAURI" in text, (
            "autostart_launcher.py must check the VOICE_TYPER_TAURI env var "
            " to detect Tauri mode. The Tauri Rust host sets this "
            "before spawning the Python sidecar so the launcher knows to "
            "spawn lausu-tauri."
        )

    def test_autostart_launcher_has_is_tauri_mode_helper(self):
        """The launcher defines a ``_is_tauri_mode()`` helper."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = _launcher_text()
        assert re.search(r"def\s+_is_tauri_mode\s*\(\s*\)\s*->\s*bool\s*:", text), (
            "autostart_launcher.py must define a `_is_tauri_mode() -> bool` "
            "helper  that checks both VOICE_TYPER_TAURI env var "
            "AND sys.executable basename."
        )

    def test_autostart_launcher_checks_sys_executable_basename(self):
        """The Tauri-mode helper also checks ``sys.executable`` basename."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = _launcher_text()
        # Must reference sys.executable AND the Tauri host binary name.
        assert "sys.executable" in text
        assert "lausu-tauri" in text, (
            "autostart_launcher.py must check that sys.executable basename "
            "contains 'lausu-tauri' as a fallback Tauri-mode signal."
        )

    def test_autostart_launcher_has_tauri_binary_helper(self):
        """The launcher defines a ``_tauri_binary()`` helper to locate the host binary."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = _launcher_text()
        assert re.search(r"def\s+_tauri_binary\s*\(\s*\)\s*->\s*str\s*\|\s*None\s*:", text), (
            "autostart_launcher.py must define a `_tauri_binary() -> str | None` "
            "helper  that looks up lausu-tauri on PATH and in "
            "well-known install dirs."
        )

    def test_autostart_launcher_has_spawn_tauri_host_helper(self):
        """The launcher defines a ``_spawn_tauri_host()`` helper."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = _launcher_text()
        assert re.search(r"def\s+_spawn_tauri_host\s*\(", text), (
            "autostart_launcher.py must define a `_spawn_tauri_host()` helper  that spawns the lausu-tauri binary."
        )

    def test_autostart_launcher_exits_1_when_tauri_binary_missing(self):
        """In Tauri mode, the launcher exits 1 if lausu-tauri is not found."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = _launcher_text()
        assert "_is_tauri_mode()" in text, "launch() must call _is_tauri_mode() to detect Tauri mode."
        assert "_spawn_tauri_host" in text, "launch() must call _spawn_tauri_host() in Tauri mode."
        # Post-predecessor cutover: fresh-start is Tauri-only; missing binary
        assert "return 1" in text, "launch() must return 1 when the Tauri binary is missing or spawn fails."
        assert "No Tauri binary" in text

    def test_autostart_launcher_spawns_tauri_host(self):
        """The launcher spawns the Tauri host and focuses a running app."""
        assert AUTOSTART_LAUNCHER.is_file()
        text = _launcher_text()
        assert "_spawn_tauri_host" in text
        assert "_focus_running_app" in text, (
            "autostart_launcher.py must keep _focus_running_app for the 'backend already running' case."
        )


# All 6 maintainer scripts covered by this test file.
_ALL_MAINTAINER_SCRIPTS = (
    (POSTINST, "postinst"),
    (PRERM, "prerm"),
    (POSTRM, "postrm"),
    (POSTINST_RPM, "postinst.rpm"),
    (PRERM_RPM, "prerm.rpm"),
    (POSTRM_RPM, "postrm.rpm"),
)


class TestPrewarmCleanupPortedToRpm:
    """``remove_prewarm_for_home`` is ported from prerm → prerm.rpm."""

    def test_prerm_rpm_defines_remove_prewarm_for_home(self):
        """prerm.rpm defines the ``remove_prewarm_for_home`` shell function."""
        text = PRERM_RPM.read_text(encoding="utf-8")
        assert re.search(r"remove_prewarm_for_home\s*\(\s*\)\s*\{", text), (
            "prerm.rpm must define a `remove_prewarm_for_home()` shell "
            "function, mirrors the Debian prerm's helper that "
            "disables + deletes the per-user prewarm systemd units. "
            "Without it, RPM uninstalls leave a stale systemd timer "
            "pointing at the now-deleted frozen prewarm binary."
        )

    def test_prerm_rpm_references_prewarm_unit_names(self):
        """prerm.rpm references the prewarm systemd unit names."""
        text = PRERM_RPM.read_text(encoding="utf-8")
        assert "lausu-prewarm.timer" in text, (
            "prerm.rpm must reference 'lausu-prewarm.timer'  so the systemd timer is disabled + removed on uninstall."
        )
        assert "lausu-prewarm.service" in text, (
            "prerm.rpm must reference 'lausu-prewarm.service'  "
            "so the systemd service is disabled + removed on uninstall."
        )

    def test_prerm_rpm_calls_remove_prewarm_for_home_in_user_loop(self):
        """prerm.rpm calls ``remove_prewarm_for_home`` inside the user loop + for /root."""
        text = PRERM_RPM.read_text(encoding="utf-8")
        # Called inside the getent passwd while-loop for every non-system user.
        assert 'remove_prewarm_for_home "$home_dir"' in text, (
            'prerm.rpm must call `remove_prewarm_for_home "$home_dir"` '
            "inside the user iteration loop, mirrors the Debian prerm."
        )
        # Also called for /root explicitly (rare sudo -E case).
        assert "remove_prewarm_for_home /root" in text, (
            "prerm.rpm must call `remove_prewarm_for_home /root` explicitly, mirrors the Debian prerm."
        )

    def test_prerm_debian_still_has_remove_prewarm_for_home(self):
        """Sanity: the Debian prerm (which the RPM port mirrors) still has the helper."""
        text = PRERM.read_text(encoding="utf-8")
        assert re.search(r"remove_prewarm_for_home\s*\(\s*\)\s*\{", text), (
            "prerm must still define `remove_prewarm_for_home()`, this is "
            "the source the RPM port mirrors. If this regressed, the RPM "
            "port would also need to be re-synced."
        )


class TestProcessTerminationBeforeCleanup:
    """prerm + prerm.rpm terminate lausu processes before file removal."""

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_has_pkill_voice_typer_tauri(self, path, label):
        """prerm + prerm.rpm send SIGTERM to ``lausu-tauri`` via pkill -x."""
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert re.search(r"pkill\s+-TERM\s+-x\s+lausu-tauri", text), (
            f"{label} must run `pkill -TERM -x lausu-tauri`  so "
            "the Tauri host binary receives SIGTERM before file removal. "
            "Without this, the running app may re-create the autostart / "
            "prewarm files that prerm is about to delete."
        )

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_has_pkill_python_sidecar(self, path, label):
        """prerm + prerm.rpm send SIGTERM to ``python-sidecar`` via pkill -f."""
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert re.search(r"pkill\s+-TERM\s+-f\s+'python-sidecar'", text), (
            f"{label} must run `pkill -TERM -f 'python-sidecar'`  so "
            "the frozen Python sidecar binary receives SIGTERM before file "
            "removal. The -f flag matches the full command line (the frozen "
            "sidecar runs as python-sidecar-x86_64-unknown-linux-gnu or "
            "similar; an exact -x match would miss the arch suffix)."
        )

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_has_sleep_1_after_pkill(self, path, label):
        """prerm + prerm.rpm sleep 1s after the pkill catch-all (lets processes exit)."""
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        # The `sleep 1` must appear AFTER the pkill -f 'python-sidecar'
        pkill_pos = text.find("pkill -TERM -f 'python-sidecar'")
        assert pkill_pos != -1, f"{label} must contain `pkill -TERM -f 'python-sidecar'`"
        after_pkill = text[pkill_pos:]
        assert re.search(r"sleep\s+1\b", after_pkill), (
            f"{label} must call `sleep 1` AFTER `pkill -TERM -f 'python-sidecar'` "
            " so the SIGTERM'd processes have time to actually exit "
            "and release file handles before the autostart / prewarm rm step."
        )

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_reads_backend_pid_lockfile(self, path, label):
        """prerm + prerm.rpm read the per-user ``backend.pid`` lockfile + send targeted SIGTERM."""
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "backend.pid" in text, (
            f"{label} must reference 'backend.pid', the single-"
            "instance lockfile written by single_instance.py at "
            "~/.local/share/lausu/backend.pid. Reading the PID "
            "lets the script send a targeted SIGTERM so the app runs "
            "its shutdown teardown (flush history DB, release audio, "
            "clear the PID file) before the catch-all pkill."
        )
        # Must use `kill -TERM` (targeted SIGTERM to the read PID).
        assert 'kill -TERM "$pid"' in text or 'kill -TERM "$root_pid"' in text, (
            f"{label} must send a targeted `kill -TERM` to the PID read "
            "from backend.pid . The pkill catch-all is a belt-and-"
            "suspenders fallback; the targeted SIGTERM is preferred so "
            "the app can run its teardown instead of being pkill'd."
        )

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_terminates_before_autostart_prewarm_cleanup(self, path, label):
        """Process termination (pkill) appears BEFORE the autostart / prewarm cleanup calls."""
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        pkill_pos = text.find("pkill -TERM -x lausu-tauri")
        # The autostart cleanup call inside the user loop.
        autostart_call_pos = text.find('remove_autostart_for_home "$home_dir"')
        assert pkill_pos != -1, f"{label} must contain `pkill -TERM -x lausu-tauri` "
        assert autostart_call_pos != -1, (
            f'{label} must call `remove_autostart_for_home "$home_dir"` '
            "(the autostart cleanup that the pkill must precede)."
        )
        assert pkill_pos < autostart_call_pos, (
            f"{label}: the `pkill -TERM -x lausu-tauri` call MUST "
            f'appear BEFORE `remove_autostart_for_home "$home_dir"` '
            "(terminate processes before file removal). "
            f"pkill_pos={pkill_pos}, autostart_call_pos={autostart_call_pos}."
        )

    @pytest.mark.parametrize("path,label", [(PRERM, "prerm"), (PRERM_RPM, "prerm.rpm")])
    def test_prerm_pkill_uses_or_true_for_set_e_safety(self, path, label):
        """pkill calls are guarded by ``|| true`` so ``set -e`` doesn't abort on no-match."""
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        # Every non-comment pkill -TERM line must be followed by `|| true`.
        non_comment_pkill_lines = []
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if re.search(r"pkill\s+-TERM", line):
                non_comment_pkill_lines.append(line)
        assert non_comment_pkill_lines, f"{label}: must contain at least one non-comment `pkill -TERM` line ."
        for line in non_comment_pkill_lines:
            assert "|| true" in line, (
                f"{label}: pkill line `{line.strip()}` must be guarded by "
                "`|| true`, pkill returns 1 when no processes "
                "match, and `set -e` would abort the uninstall otherwise."
            )


class TestDesktopDbAndIconCacheRefresh:
    """postinst + prerm refresh the desktop database + icon cache."""

    def test_postinst_update_desktop_database_inside_configure_case(self):
        """postinst runs ``update-desktop-database`` inside ``configure)`` (not after esac)."""
        text = POSTINST.read_text(encoding="utf-8")
        configure_pos = text.find("configure)")
        assert configure_pos != -1, "postinst must have a `configure)` case block."
        # Find the first `;;` after `configure)`, that's the end of the case.
        end_of_configure = text.find(";;", configure_pos)
        assert end_of_configure != -1, "postinst configure) case must end with `;;`."
        udd_pos = text.find("update-desktop-database", configure_pos)
        assert udd_pos != -1 and udd_pos < end_of_configure, (
            "postinst must call `update-desktop-database` INSIDE the "
            "`configure)` case block, i.e. between `configure)` "
            "and the closing `;;`. Pre- the call was AFTER the `esac`, "
            "so it ran on every dpkg action (including abort-upgrade)."
        )

    def test_postinst_has_gtk_update_icon_cache(self):
        """postinst calls ``gtk-update-icon-cache -q /usr/share/icons/hicolor``."""
        text = POSTINST.read_text(encoding="utf-8")
        assert re.search(r"gtk-update-icon-cache\s+-q\s+/usr/share/icons/hicolor", text), (
            "postinst must call `gtk-update-icon-cache -q /usr/share/icons/hicolor` "
            " so the hicolor icon is indexed by the desktop environment "
            "immediately after install. The icon ships in the bundle but isn't "
            "picked up until this runs."
        )
        # Verify the call is inside the configure) case.
        configure_pos = text.find("configure)")
        end_of_configure = text.find(";;", configure_pos)
        gtk_pos = text.find("gtk-update-icon-cache", configure_pos)
        assert gtk_pos != -1 and gtk_pos < end_of_configure, (
            "postinst: `gtk-update-icon-cache` MUST appear INSIDE the `configure)` case block ."
        )

    def test_postinst_does_not_call_update_desktop_database_outside_case(self):
        """postinst does NOT have an ``update-desktop-database`` call OUTSIDE the case block."""
        text = POSTINST.read_text(encoding="utf-8")
        # Find the `esac` that closes the case statement.
        esac_pos = text.rfind("esac")
        assert esac_pos != -1, "postinst must have an `esac` closing the case block."
        # Any `update-desktop-database` call AFTER `esac` is a regression.
        udd_after_esac = text.find("update-desktop-database", esac_pos)
        assert udd_after_esac == -1, (
            "postinst must NOT call `update-desktop-database` after the "
            "`esac`, the call must be INSIDE the `configure)` "
            "case block so it only runs on `apt install` / `apt configure`, "
            "not on abort-upgrade / abort-remove / abort-deconfigure."
        )

    def test_prerm_has_update_desktop_database_in_remove_case(self):
        """prerm calls ``update-desktop-database`` inside ``remove|deconfigure)``."""
        text = PRERM.read_text(encoding="utf-8")
        remove_pos = text.find("remove|deconfigure)")
        assert remove_pos != -1, "prerm must have a `remove|deconfigure)` case block."
        end_of_remove = text.find(";;", remove_pos)
        assert end_of_remove != -1, "prerm remove|deconfigure) case must end with `;;`."
        udd_pos = text.find("update-desktop-database", remove_pos)
        assert udd_pos != -1 and udd_pos < end_of_remove, (
            "prerm must call `update-desktop-database` INSIDE the "
            "`remove|deconfigure)` case block  so the launcher "
            "entry disappears from application menus immediately after "
            "removal (not after the next manual refresh or reboot)."
        )

    def test_prerm_has_gtk_update_icon_cache_in_remove_case(self):
        """prerm calls ``gtk-update-icon-cache`` inside ``remove|deconfigure)``."""
        text = PRERM.read_text(encoding="utf-8")
        remove_pos = text.find("remove|deconfigure)")
        end_of_remove = text.find(";;", remove_pos)
        gtk_pos = text.find("gtk-update-icon-cache", remove_pos)
        assert gtk_pos != -1 and gtk_pos < end_of_remove, (
            "prerm must call `gtk-update-icon-cache` INSIDE the "
            "`remove|deconfigure)` case block  so the hicolor "
            "icon disappears from application menus immediately after "
            "removal."
        )


class TestPostinstRpmGatedOnFirstInstall:
    """postinst.rpm body is wrapped in ``if [ \"$1\" -eq 1 ]; then ... fi``."""

    def test_postinst_rpm_has_first_install_gate(self):
        """postinst.rpm body is wrapped in ``if [ \"$1\" -eq 1 ]; then ... fi``."""
        text = POSTINST_RPM.read_text(encoding="utf-8")
        # The gate must use $1 -eq 1 (numeric comparison for RPM's $1 count).
        assert re.search(r'if\s+\[\s*"\$1"\s+-eq\s+1\s*\]\s*;\s*then', text), (
            'postinst.rpm must wrap its body in `if [ "$1" -eq 1 ]; then ... fi` '
            ", RPM %post passes $1=1 on first install, $1=2 on upgrade. "
            "The body should only run on first install (mirror prerm.rpm's "
            '`if [ "$1" = "0" ]` uninstall-gate pattern).'
        )

    def test_postinst_rpm_gate_closes_with_fi(self):
        """The ``if [ \"$1\" -eq 1 ]`` gate closes with ``fi`` before ``exit 0``."""
        text = POSTINST_RPM.read_text(encoding="utf-8")
        gate_match = re.search(r'if\s+\[\s*"\$1"\s+-eq\s+1\s*\]\s*;\s*then', text)
        assert gate_match is not None, 'postinst.rpm must have `if [ "$1" -eq 1 ]; then` .'
        # Find the matching `fi` AFTER the gate.
        gate_pos = gate_match.start()
        fi_pos = text.find("\nfi", gate_pos)
        assert fi_pos != -1, 'postinst.rpm: the `if [ "$1" -eq 1 ]; then` gate must be closed with `fi` .'
        # `exit 0` must come AFTER `fi` (so the script always exits 0,
        exit_pos = text.find("exit 0", fi_pos)
        assert exit_pos != -1, (
            "postinst.rpm: `exit 0` must come AFTER the closing `fi` of "
            "the $1 -eq 1 gate, the script must exit 0 whether "
            "the gate was taken (first install) or skipped (upgrade)."
        )

    def test_postinst_rpm_install_permissions_inside_gate(self):
        """The ``python3 \"$INSTALL_SCRIPT\"`` call is inside the gate (only on first install)."""
        text = POSTINST_RPM.read_text(encoding="utf-8")
        gate_match = re.search(r'if\s+\[\s*"\$1"\s+-eq\s+1\s*\]\s*;\s*then', text)
        assert gate_match is not None
        gate_pos = gate_match.start()
        fi_pos = text.find("\nfi", gate_pos)
        install_pos = text.find('python3 "$INSTALL_SCRIPT"', gate_pos)
        assert install_pos != -1 and install_pos < fi_pos, (
            'postinst.rpm: the `python3 "$INSTALL_SCRIPT"` call must be '
            'INSIDE the `if [ "$1" -eq 1 ]; then ... fi` gate  '
            "so install_permissions.py only runs on first install, not "
            "on upgrade."
        )


class TestMaintainerScriptsExecutable:
    """all 6 maintainer scripts are chmod 0755 (executable)."""

    @pytest.mark.parametrize("path,label", _ALL_MAINTAINER_SCRIPTS)
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows filesystems do not store POSIX exec bits in stat(), validated on Linux CI",
    )
    def test_script_has_executable_bit_in_working_tree(self, path, label):
        """The working-tree file has the executable bit set (mode 0755)."""
        assert path.is_file(), f"{label} missing at {path}"
        mode = path.stat().st_mode
        # 0o100 = owner-execute; 0o010 = group-execute; 0o001 = other-execute.
        assert mode & 0o100, f"{label}: owner-execute bit not set (mode={oct(mode)})"
        assert mode & 0o010, f"{label}: group-execute bit not set (mode={oct(mode)})"
        assert mode & 0o001, f"{label}: other-execute bit not set (mode={oct(mode)})"

    @pytest.mark.parametrize("path,label", _ALL_MAINTAINER_SCRIPTS)
    def test_script_has_executable_bit_in_git_index(self, path, label):
        """The git index records the file as mode 100755 (executable)."""
        assert path.is_file(), f"{label} missing at {path}"
        # Resolve the path relative to the repo root for git ls-files.
        rel = path.relative_to(_REPO_ROOT)
        result = subprocess.run(
            ["git", "ls-files", "-s", str(rel)],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"`git ls-files -s {rel}` failed (exit {result.returncode}): {result.stderr}"
        output = result.stdout.strip()
        assert output, (
            f"{label}: `git ls-files -s {rel}` returned no output, the "
            "file may not be tracked by git. The git index mode cannot "
            "be verified."
        )
        # The first token is the index mode (e.g. `100755` or `100644`).
        index_mode = output.split()[0]
        assert index_mode == "100755", (
            f"{label}: git index mode is `{index_mode}` but MUST be "
            "`100755` . Without the executable bit in the git "
            "index, a fresh clone or `git checkout` resets the working-"
            "tree mode to 100644 (non-executable), silently breaking "
            "every Linux install / uninstall. Fix with "
            f"`git update-index --chmod=+x {rel}`."
        )

    @_skip_no_bash
    @pytest.mark.parametrize("path,label", _ALL_MAINTAINER_SCRIPTS)
    def test_all_maintainer_scripts_bash_syntax_valid(self, path, label):
        """``bash -n <script>`` exits 0 for all 6 maintainer scripts (no syntax errors)."""
        assert _bash_syntax_ok(path), f"{label} has bash syntax errors"
