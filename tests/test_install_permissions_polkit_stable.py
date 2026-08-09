"""Tests for the polkit-stable path self-install logic in
``scripts/linux/install_permissions.py``.

Background: the polkit policy (``scripts/linux/voice-typer.polkit``)
hard-codes ``/usr/share/voice-typer/scripts/install_permissions.py`` as
the ``org.freedesktop.policykit.exec.path`` annotation. Polkit requires
an absolute, stable path — it does not change across AppImage versions
or .deb / .rpm upgrades.

For Debian / RPM installs, the package's ``postinst`` creates a symlink
at the polkit-stable path pointing to the actually-installed script.

For AppImage installs, no ``postinst`` runs. Without the self-install
logic in ``install_permissions.py``, the polkit-stable path would never
exist on AppImage installs, and ``pkexec
com.voicetyper.install-permissions`` would silently fail.

These tests verify the self-install logic:

1. ``setup_polkit_stable_path()`` is callable and idempotent.
2. ``_is_running_from_appimage()`` correctly detects AppImage mount paths.
3. ``_install_polkit_policy()`` is idempotent.
4. The ``--setup-system-paths`` CLI flag refuses non-root.
5. The script still refuses non-root for the default install path.
6. The script is valid Python (AST parses).
7. Constants point at the canonical polkit-stable + polkit policy paths.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

# Python 3.10/3.11 compatibility: ``pathlib.PurePath._parse_args`` looks up
# ``cls._flavour``, an attribute that exists only on the CONCRETE flavour
# classes (``WindowsPath`` / ``PosixPath``), not on ``Path`` itself. A
# subclass defined directly as ``class FakePath(Path)`` therefore breaks
# construction on 3.10/3.11 (``AttributeError: type object 'FakePath' has
# no attribute '_flavour'``). Python 3.12+ rewrote pathlib (``PathBase``)
# so direct subclasses work there. ``type(Path())`` yields the platform's
# concrete Path class, so fake-path subclasses work on every supported
# Python version.
_CONCRETE_PATH = type(Path())

# Resolve the canonical install_permissions.py path (tests/ → repo root →
# scripts/linux/install_permissions.py).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SCRIPT = _REPO_ROOT / "scripts" / "linux" / "install_permissions.py"


def _load_install_permissions_module():
    """Load ``scripts/linux/install_permissions.py`` as an isolated module.

    The script lives outside the ``voice_typer`` package, so we load it
    via ``importlib.util.spec_from_file_location`` instead of a regular
    ``import``. The module is registered under a unique name to avoid
    collisions with any future ``install_permissions`` package.
    """
    spec = importlib.util.spec_from_file_location("install_permissions_under_test", _INSTALL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ip_module():
    """Load the install_permissions.py module once per test module run."""
    if not _INSTALL_SCRIPT.is_file():
        pytest.skip("install_permissions.py not found (not a Linux build)")
    return _load_install_permissions_module()


# ─── Smoke tests ───────────────────────────────────────────────────────────


class TestScriptValidity:
    """The script must be valid Python and refuse non-root callers."""

    def test_script_compiles(self):
        """The script AST-parses without syntax errors."""
        if not _INSTALL_SCRIPT.is_file():
            pytest.skip("install_permissions.py not found")
        with open(_INSTALL_SCRIPT) as f:
            ast.parse(f.read())

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="Linux-only test (script uses grp / pwd modules)",
    )
    def test_script_refuses_non_root(self):
        """Running the script as non-root exits 1 with 'must run as root'."""
        if not _INSTALL_SCRIPT.is_file():
            pytest.skip("install_permissions.py not found")
        import subprocess

        result = subprocess.run(
            [sys.executable, str(_INSTALL_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 1
        assert "must run as root" in result.stdout.lower() or "must run as root" in result.stderr.lower()

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="Linux-only test",
    )
    def test_setup_system_paths_refuses_non_root(self):
        """The --setup-system-paths flag also refuses non-root callers."""
        if not _INSTALL_SCRIPT.is_file():
            pytest.skip("install_permissions.py not found")
        import subprocess

        result = subprocess.run(
            [sys.executable, str(_INSTALL_SCRIPT), "--setup-system-paths"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 1
        assert "must run as root" in result.stdout.lower() or "must run as root" in result.stderr.lower()


# ─── Constants ─────────────────────────────────────────────────────────────


class TestConstants:
    """Polkit-stable path + polkit policy path constants are correct."""

    def test_polkit_stable_path_constant(self, ip_module):
        """``POLKIT_STABLE_PATH`` points at the canonical polkit-stable path."""
        # as_posix() keeps the assertion path-separator agnostic: on
        # Windows, str(Path('/usr/share/...')) renders with backslashes.
        assert ip_module.POLKIT_STABLE_PATH.as_posix() == ("/usr/share/voice-typer/scripts/install_permissions.py")

    def test_polkit_policy_dest_constant(self, ip_module):
        """``POLKIT_POLICY_DEST`` points at the canonical polkit actions dir.

        The policy filename uses the same ``com.voicetyper.*`` RDNN root
        as the action ID (``com.voicetyper.install-permissions``) and the
        ``com.voicetyper.desktop`` Tauri bundle identifier (finding #54).
        """
        assert ip_module.POLKIT_POLICY_DEST.as_posix() == ("/usr/share/polkit-1/actions/com.voicetyper.policy")

    def test_polkit_policy_source_exists(self, ip_module):
        """``POLKIT_POLICY_SOURCE`` (sibling voice-typer.polkit) exists.

        The polkit policy file is bundled as a Tauri resource sibling of
        install_permissions.py. If it's missing, the self-install logic
        can't install the polkit policy.
        """
        assert ip_module.POLKIT_POLICY_SOURCE.is_file(), (
            f"voice-typer.polkit should exist alongside install_permissions.py at {ip_module.POLKIT_POLICY_SOURCE}"
        )

    def test_appimage_mount_prefix_constant(self, ip_module):
        """``_APPIMAGE_MOUNT_PREFIX`` is the AppImage squashfs mount prefix."""
        assert ip_module._APPIMAGE_MOUNT_PREFIX == "/tmp/.mount_"


# ─── AppImage detection ───────────────────────────────────────────────────


class TestAppImageDetection:
    """``_is_running_from_appimage()`` correctly detects AppImage mounts."""

    def test_returns_false_for_repo_path(self, ip_module):
        """When run from the repo source tree, returns False."""
        # The module was loaded from scripts/linux/install_permissions.py
        # (a real path in the repo, not an AppImage mount).
        assert ip_module._is_running_from_appimage() is False

    def test_returns_true_for_appimage_mount_path(self, ip_module, monkeypatch):
        """When ``__file__`` resolves under /tmp/.mount_/, returns True."""
        # Monkey-patch Path.resolve to return an AppImage mount path.
        from pathlib import Path as OriginalPath

        fake_path = "/tmp/.mount_VoiceT_y12345/usr/lib/voice-typer/resources/linux-scripts/install_permissions.py"

        class FakePath(_CONCRETE_PATH):
            def resolve(self, strict=False):
                return OriginalPath(fake_path)

        # Patch the Path class used inside the module.
        monkeypatch.setattr(ip_module, "Path", FakePath)
        assert ip_module._is_running_from_appimage() is True

    def test_returns_false_for_usr_lib_path(self, ip_module, monkeypatch):
        """When ``__file__`` resolves under /usr/lib/, returns False."""
        from pathlib import Path as OriginalPath

        fake_path = "/usr/lib/voice-typer/resources/linux-scripts/install_permissions.py"

        class FakePath(_CONCRETE_PATH):
            def resolve(self, strict=False):
                return OriginalPath(fake_path)

        monkeypatch.setattr(ip_module, "Path", FakePath)
        assert ip_module._is_running_from_appimage() is False


# ─── setup_polkit_stable_path ─────────────────────────────────────────────


def _symlinks_supported() -> bool:
    """Return True iff ``os.symlink`` works on this host.

    Windows requires Developer Mode or an elevated shell to create
    symlinks (WinError 1314 otherwise). The polkit-stable symlink is
    a Linux-installer concern; on hosts without symlink privileges the
    symlink-specific tests are skipped (the copy / no-clobber paths
    are still exercised).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        try:
            os.symlink(td, os.path.join(td, "probe"))
        except (OSError, NotImplementedError):
            return False
        return True


_skip_no_symlink = pytest.mark.skipif(
    not _symlinks_supported(),
    reason="symlink creation not supported on this host (Windows without Developer Mode / admin)",
)


class TestSetupPolkitStablePath:
    """``setup_polkit_stable_path()`` behavior."""

    def test_skips_when_non_root(self, ip_module, monkeypatch, capsys):
        """When called as non-root, logs a warning and returns."""
        monkeypatch.setattr(ip_module, "is_root", lambda: False)
        ip_module.setup_polkit_stable_path()
        captured = capsys.readouterr()
        assert "non-root" in captured.out.lower()

    def test_no_op_when_already_at_polkit_stable_path(self, ip_module, monkeypatch, tmp_path):
        """When __file__ IS at the polkit-stable path, only installs polkit policy."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)

        # Mock _install_polkit_policy to track calls.
        polkit_calls = []

        def fake_install_polkit_policy():
            polkit_calls.append(True)

        monkeypatch.setattr(ip_module, "_install_polkit_policy", fake_install_polkit_policy)

        # Mock Path.samefile to return True (we're at the polkit-stable path).
        class SameFilePath(_CONCRETE_PATH):
            def samefile(self, other):
                return True

        monkeypatch.setattr(ip_module, "Path", SameFilePath)

        # Mock POLKIT_STABLE_PATH to a real path that exists.
        monkeypatch.setattr(ip_module, "POLKIT_STABLE_PATH", tmp_path / "install_permissions.py")

        ip_module.setup_polkit_stable_path()
        assert len(polkit_calls) == 1, "Should call _install_polkit_policy exactly once"

    @_skip_no_symlink
    def test_creates_symlink_for_stable_install(self, ip_module, monkeypatch, tmp_path):
        """For stable install paths (non-AppImage), creates a symlink."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)
        monkeypatch.setattr(ip_module, "_is_running_from_appimage", lambda: False)
        monkeypatch.setattr(ip_module, "_install_polkit_policy", lambda: None)

        # Use tmp_path as the polkit-stable dir.
        stable_dir = tmp_path / "polkit-stable"
        stable_path = stable_dir / "install_permissions.py"
        monkeypatch.setattr(ip_module, "POLKIT_STABLE_DIR", stable_dir)
        monkeypatch.setattr(ip_module, "POLKIT_STABLE_PATH", stable_path)

        # Mock Path to avoid samefile raising (polkit-stable path doesn't exist yet).
        from pathlib import Path as OriginalPath

        real_script = OriginalPath(_INSTALL_SCRIPT).resolve()

        class TestPath(_CONCRETE_PATH):
            def samefile(self, other):
                raise FileNotFoundError("polkit-stable path doesn't exist yet")

        monkeypatch.setattr(ip_module, "Path", TestPath)

        ip_module.setup_polkit_stable_path()

        assert stable_path.is_symlink(), f"Expected symlink at {stable_path}"
        assert os.readlink(stable_path) == str(real_script)

    def test_copies_for_appimage(self, ip_module, monkeypatch, tmp_path):
        """For AppImage runs, copies the script (not symlink) to the polkit-stable path."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)
        monkeypatch.setattr(ip_module, "_is_running_from_appimage", lambda: True)
        monkeypatch.setattr(ip_module, "_install_polkit_policy", lambda: None)

        stable_dir = tmp_path / "polkit-stable"
        stable_path = stable_dir / "install_permissions.py"
        monkeypatch.setattr(ip_module, "POLKIT_STABLE_DIR", stable_dir)
        monkeypatch.setattr(ip_module, "POLKIT_STABLE_PATH", stable_path)

        from pathlib import Path as OriginalPath

        class TestPath(_CONCRETE_PATH):
            def samefile(self, other):
                raise FileNotFoundError("polkit-stable path doesn't exist yet")

        monkeypatch.setattr(ip_module, "Path", TestPath)

        ip_module.setup_polkit_stable_path()

        assert stable_path.is_file(), f"Expected regular file at {stable_path}"
        assert not stable_path.is_symlink(), "AppImage copy should NOT be a symlink"
        # The copy should be byte-identical to the source.
        assert stable_path.read_bytes() == OriginalPath(_INSTALL_SCRIPT).resolve().read_bytes()

    def test_does_not_clobber_existing_regular_file(self, ip_module, monkeypatch, tmp_path):
        """For stable installs, does NOT clobber an existing regular file at the polkit-stable path."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)
        monkeypatch.setattr(ip_module, "_is_running_from_appimage", lambda: False)
        monkeypatch.setattr(ip_module, "_install_polkit_policy", lambda: None)

        stable_dir = tmp_path / "polkit-stable"
        stable_dir.mkdir()
        stable_path = stable_dir / "install_permissions.py"
        # Pre-create a regular file (simulating a legacy Electron install).
        stable_path.write_text("# legacy install_permissions.py\n")
        original_content = stable_path.read_text()

        monkeypatch.setattr(ip_module, "POLKIT_STABLE_DIR", stable_dir)
        monkeypatch.setattr(ip_module, "POLKIT_STABLE_PATH", stable_path)

        class TestPath(_CONCRETE_PATH):
            def samefile(self, other):
                raise FileNotFoundError("don't short-circuit")

        monkeypatch.setattr(ip_module, "Path", TestPath)

        ip_module.setup_polkit_stable_path()

        # The regular file should NOT be clobbered.
        assert stable_path.read_text() == original_content
        assert not stable_path.is_symlink()

    @_skip_no_symlink
    def test_idempotent_symlink_creation(self, ip_module, monkeypatch, tmp_path):
        """Re-running setup with an existing correct symlink is a no-op."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)
        monkeypatch.setattr(ip_module, "_is_running_from_appimage", lambda: False)

        polkit_calls = []
        monkeypatch.setattr(ip_module, "_install_polkit_policy", lambda: polkit_calls.append(True))

        stable_dir = tmp_path / "polkit-stable"
        stable_dir.mkdir()
        stable_path = stable_dir / "install_permissions.py"

        from pathlib import Path as OriginalPath

        real_script = OriginalPath(_INSTALL_SCRIPT).resolve()

        # Pre-create the correct symlink.
        os.symlink(str(real_script), stable_path)

        monkeypatch.setattr(ip_module, "POLKIT_STABLE_DIR", stable_dir)
        monkeypatch.setattr(ip_module, "POLKIT_STABLE_PATH", stable_path)

        class TestPath(_CONCRETE_PATH):
            def samefile(self, other):
                raise FileNotFoundError("don't short-circuit")

        monkeypatch.setattr(ip_module, "Path", TestPath)

        ip_module.setup_polkit_stable_path()

        # Symlink should still point to the same target.
        assert stable_path.is_symlink()
        assert os.readlink(stable_path) == str(real_script)
        # _install_polkit_policy should have been called (early return path).
        assert len(polkit_calls) == 1


# ─── _install_polkit_policy ───────────────────────────────────────────────


class TestInstallPolkitPolicy:
    """``_install_polkit_policy()`` behavior."""

    def test_skips_when_source_missing(self, ip_module, monkeypatch, tmp_path, capsys):
        """When the source polkit file doesn't exist, logs a warning and returns."""
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_SOURCE", tmp_path / "nonexistent.polkit")
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", tmp_path / "com.voicetyper.policy")
        ip_module._install_polkit_policy()
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_installs_when_dest_missing(self, ip_module, monkeypatch, tmp_path):
        """When the destination doesn't exist, copies the source."""
        source = tmp_path / "voice-typer.polkit"
        source.write_text("<policyconfig>test</policyconfig>")
        dest = tmp_path / "com.voicetyper.policy"

        monkeypatch.setattr(ip_module, "POLKIT_POLICY_SOURCE", source)
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", dest)

        ip_module._install_polkit_policy()

        assert dest.is_file()
        assert dest.read_text() == source.read_text()

    def test_idempotent_when_dest_matches(self, ip_module, monkeypatch, tmp_path, capsys):
        """When the destination already matches the source, no-op."""
        source = tmp_path / "voice-typer.polkit"
        source.write_text("<policyconfig>test</policyconfig>")
        dest = tmp_path / "com.voicetyper.policy"
        dest.write_text(source.read_text())

        # Track shutil.copy2 calls.
        copy_calls = []
        original_copy2 = ip_module.shutil.copy2

        def tracking_copy2(src, dst, **kwargs):
            copy_calls.append((src, dst))
            return original_copy2(src, dst, **kwargs)

        monkeypatch.setattr(ip_module.shutil, "copy2", tracking_copy2)

        monkeypatch.setattr(ip_module, "POLKIT_POLICY_SOURCE", source)
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", dest)

        ip_module._install_polkit_policy()

        assert len(copy_calls) == 0, "Should NOT copy when destination already matches"

    def test_overwrites_when_dest_differs(self, ip_module, monkeypatch, tmp_path):
        """When the destination differs from the source, overwrites."""
        source = tmp_path / "voice-typer.polkit"
        source.write_text("<policyconfig>new</policyconfig>")
        dest = tmp_path / "com.voicetyper.policy"
        dest.write_text("<policyconfig>old</policyconfig>")

        monkeypatch.setattr(ip_module, "POLKIT_POLICY_SOURCE", source)
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", dest)

        ip_module._install_polkit_policy()

        assert dest.read_text() == source.read_text()


# ─── _remove_polkit_policies ──────────────────────────────────────────────


class TestRemovePolkitPolicies:
    """``_remove_polkit_policies()`` behavior (uninstall-time cleanup)."""

    def test_legacy_policy_dest_points_at_legacy_path(self, ip_module):
        """The legacy constant targets the pre-Tauri Electron policy filename."""
        assert ip_module.LEGACY_POLKIT_POLICY_DEST.as_posix() == ("/usr/share/polkit-1/actions/org.voice-typer.policy")

    def test_removes_current_and_legacy_policies(self, ip_module, monkeypatch, tmp_path, capsys):
        """Both the current and legacy policy files are removed."""
        current = tmp_path / "com.voicetyper.policy"
        legacy = tmp_path / "org.voice-typer.policy"
        current.write_text("<policyconfig>current</policyconfig>")
        legacy.write_text("<policyconfig>legacy</policyconfig>")

        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", current)
        monkeypatch.setattr(ip_module, "LEGACY_POLKIT_POLICY_DEST", legacy)

        ip_module._remove_polkit_policies()

        assert not current.exists()
        assert not legacy.exists()
        captured = capsys.readouterr()
        assert "com.voicetyper.policy" in captured.out
        assert "org.voice-typer.policy" in captured.out

    def test_noop_when_absent(self, ip_module, monkeypatch, tmp_path):
        """Missing policy files are a silent no-op (no error)."""
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", tmp_path / "no-current.policy")
        monkeypatch.setattr(ip_module, "LEGACY_POLKIT_POLICY_DEST", tmp_path / "no-legacy.policy")

        ip_module._remove_polkit_policies()  # must not raise

    def test_tolerates_oserror(self, ip_module, monkeypatch, tmp_path, capsys):
        """A failing unlink logs a non-fatal warning and continues to the next file."""
        current = tmp_path / "com.voicetyper.policy"
        legacy = tmp_path / "org.voice-typer.policy"
        current.write_text("x")
        legacy.write_text("y")

        def _failing_unlink(self, *args, **kwargs):
            raise PermissionError("simulated EACCES on unlink")

        monkeypatch.setattr(ip_module.Path, "unlink", _failing_unlink)

        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", current)
        monkeypatch.setattr(ip_module, "LEGACY_POLKIT_POLICY_DEST", legacy)

        ip_module._remove_polkit_policies()  # must not raise

        captured = capsys.readouterr()
        assert "failed to remove polkit policy" in captured.out
        assert "non-fatal" in captured.out


# ─── main() CLI flag handling ─────────────────────────────────────────────


class TestMainCliFlags:
    """``main()`` correctly dispatches CLI flags."""

    def test_main_calls_setup_for_setup_system_paths_flag(self, ip_module, monkeypatch):
        """``--setup-system-paths`` flag calls setup_polkit_stable_path."""
        monkeypatch.setattr(sys, "argv", ["install_permissions.py", "--setup-system-paths"])
        monkeypatch.setattr(ip_module, "is_root", lambda: True)

        calls = []

        def fake_setup():
            calls.append(True)

        monkeypatch.setattr(ip_module, "setup_polkit_stable_path", fake_setup)

        ip_module.main()
        assert len(calls) == 1, "main() should call setup_polkit_stable_path() for --setup-system-paths"

    def test_main_refuses_non_root_for_setup_flag(self, ip_module, monkeypatch):
        """``--setup-system-paths`` as non-root exits 1."""
        monkeypatch.setattr(sys, "argv", ["install_permissions.py", "--setup-system-paths"])
        monkeypatch.setattr(ip_module, "is_root", lambda: False)

        with pytest.raises(SystemExit) as exc_info:
            ip_module.main()
        assert exc_info.value.code == 1

    def test_main_calls_install_for_no_flags(self, ip_module, monkeypatch):
        """No flags calls install()."""
        monkeypatch.setattr(sys, "argv", ["install_permissions.py"])
        monkeypatch.setattr(ip_module, "is_root", lambda: True)

        install_calls = []

        def fake_install():
            install_calls.append(True)

        monkeypatch.setattr(ip_module, "install", fake_install)

        ip_module.main()
        assert len(install_calls) == 1

    def test_main_calls_uninstall_for_uninstall_flag(self, ip_module, monkeypatch):
        """``--uninstall`` flag calls uninstall()."""
        monkeypatch.setattr(sys, "argv", ["install_permissions.py", "--uninstall"])

        uninstall_calls = []

        def fake_uninstall():
            uninstall_calls.append(True)

        monkeypatch.setattr(ip_module, "uninstall", fake_uninstall)

        ip_module.main()
        assert len(uninstall_calls) == 1


# ─── uninstall() polkit cleanup ──────────────────────────────────────────


class TestUninstallRemovesPolkitPolicies:
    """``uninstall()`` removes the polkit policy files it (or a legacy
    installer) placed in the polkit actions directory."""

    def test_uninstall_removes_current_and_legacy_policies(self, ip_module, monkeypatch, tmp_path):
        """``uninstall()`` unlinks both the current and legacy policy files."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)

        current = tmp_path / "com.voicetyper.policy"
        legacy = tmp_path / "org.voice-typer.policy"
        current.write_text("x")
        legacy.write_text("y")
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", current)
        monkeypatch.setattr(ip_module, "LEGACY_POLKIT_POLICY_DEST", legacy)

        # Stub the other uninstall side effects (paths under tmp_path so
        # nothing touches the real system).
        monkeypatch.setattr(ip_module, "UDEV_RULE_PATH", tmp_path / "no-udev")
        monkeypatch.setattr(ip_module, "XKB_CONF_PATH", tmp_path / "no-xkb")
        monkeypatch.setattr(ip_module, "MANIFEST_PATH", tmp_path / "no-manifest.json")
        monkeypatch.setattr(ip_module, "run", lambda *a, **k: None)

        ip_module.uninstall()

        assert not current.exists()
        assert not legacy.exists()

    def test_uninstall_removes_legacy_policy_even_without_manifest(self, ip_module, monkeypatch, tmp_path):
        """The legacy policy is removed even when the manifest is missing
        (upgraded system whose installer never wrote a manifest)."""
        monkeypatch.setattr(ip_module, "is_root", lambda: True)

        legacy = tmp_path / "org.voice-typer.policy"
        legacy.write_text("y")
        monkeypatch.setattr(ip_module, "LEGACY_POLKIT_POLICY_DEST", legacy)
        monkeypatch.setattr(ip_module, "POLKIT_POLICY_DEST", tmp_path / "no-current.policy")

        monkeypatch.setattr(ip_module, "UDEV_RULE_PATH", tmp_path / "no-udev")
        monkeypatch.setattr(ip_module, "XKB_CONF_PATH", tmp_path / "no-xkb")
        monkeypatch.setattr(ip_module, "MANIFEST_PATH", tmp_path / "no-manifest.json")
        monkeypatch.setattr(ip_module, "run", lambda *a, **k: None)

        ip_module.uninstall()

        assert not legacy.exists()


# ─── Bundled copy sync ────────────────────────────────────────────────────


class TestBundledCopySync:
    """The bundled copy at ``src-tauri/resources/linux-scripts/`` must
    match the canonical source at ``scripts/linux/``.

    The Tauri v2 bundle ships the bundled copy; if it diverges from the
    canonical source, fixes to the canonical source don't ship in the
    next Tauri build.
    """

    def test_bundled_copy_matches_canonical_source(self):
        """The bundled install_permissions.py is byte-identical to the canonical source."""
        canonical = _REPO_ROOT / "scripts" / "linux" / "install_permissions.py"
        bundled = _REPO_ROOT / "src-tauri" / "resources" / "linux-scripts" / "install_permissions.py"
        assert canonical.is_file(), f"canonical source missing: {canonical}"
        assert bundled.is_file(), f"bundled copy missing: {bundled}"
        assert canonical.read_bytes() == bundled.read_bytes(), (
            "bundled install_permissions.py differs from canonical source — "
            "re-sync via `cp scripts/linux/install_permissions.py "
            "src-tauri/resources/linux-scripts/install_permissions.py`."
        )
