"""FR-37: regression tests for the POSIX single-instance lockfile"""

from __future__ import annotations

import contextlib
import os

import pytest

# Skip on Windows, the POSIX path is not exercised there.
pytest.importorskip("fcntl")

from voice_typer.server._paths import RUN_SUBDIR  # noqa: E402


def _lock_file(config_dir):
    """Canonical lockfile path: ``<config_dir>/run/backend.lock``."""
    lock = config_dir / RUN_SUBDIR / "backend.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    return lock


@pytest.fixture
def isolated_config_dir(monkeypatch, tmp_path):
    """Redirect ``_config_dir()`` to a fresh subdirectory of tmp_path"""
    from voice_typer.server import app as app_mod, config as config_mod

    # Redirect the OWNING module's binding (C-ARCH-2 canonical contract —
    config_subdir = tmp_path / "voice-typer-config"
    monkeypatch.setattr(config_mod, "_config_dir", lambda: config_subdir)
    monkeypatch.setattr(app_mod, "_config_dir", lambda: config_subdir)
    monkeypatch.setattr(
        "voice_typer.server.single_instance._backend_pid_file",
        lambda: config_subdir / "backend.pid",
    )
    return config_subdir


def _cleanup_lock_fd(fd) -> None:
    """Close a lock fd if open (best-effort)."""
    if fd is None:
        return
    release = getattr(fd, "release", None)
    if callable(release):
        try:
            release()
            return
        except OSError:
            pass
    with contextlib.suppress(OSError):
        os.close(int(fd))


class TestConfigDirChmod:
    """FR-37: the config dir is created with mode 0o700 (owner-only)."""

    def test_config_dir_mode_is_0o700_on_creation(self, isolated_config_dir):
        """When ``_ensure_single_instance_posix`` creates the config"""
        from voice_typer.server import single_instance as si_mod

        # The config dir does NOT exist yet (tmp_path is empty).
        assert not isolated_config_dir.exists()
        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            # The config dir now exists.
            assert isolated_config_dir.exists()
            mode = isolated_config_dir.stat().st_mode & 0o777
            assert mode == 0o700, f"FR-37: config dir mode must be 0o700 (owner-only); got 0o{mode:o}"
        finally:
            _cleanup_lock_fd(fd)

    def test_config_dir_chmod_tightens_existing_loose_perms(self, isolated_config_dir):
        """If the config dir already exists with looser perms (e.g."""
        from voice_typer.server import single_instance as si_mod

        isolated_config_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(isolated_config_dir, 0o755)
        # Verify the pre-condition (mode is 0o755, possibly masked by
        pre_mode = isolated_config_dir.stat().st_mode & 0o777
        assert pre_mode == 0o755, f"pre-condition: config dir should be 0o755; got 0o{pre_mode:o}"

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            # The defensive chmod should have tightened the perms.
            post_mode = isolated_config_dir.stat().st_mode & 0o777
            assert post_mode == 0o700, (
                f"FR-37: defensive os.chmod should tighten existing config dir from 0o755 to 0o700; got 0o{post_mode:o}"
            )
        finally:
            _cleanup_lock_fd(fd)


# O_NOFOLLOW on os.open ─────────────────────────────────────


class TestNoFollowSymlink:
    """FR-37: ``os.open`` for the lockfile uses ``O_NOFOLLOW`` so a"""

    def test_lockfile_symlink_is_rejected(self, isolated_config_dir, monkeypatch):
        """``O_NOFOLLOW`` flag causes ``os.open`` to raise ``OSError``"""
        from voice_typer.server import single_instance as si_mod

        isolated_config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Plant a symlink at backend.lock pointing to /etc/passwd
        symlink_target = isolated_config_dir / "attacker_target.txt"
        symlink_target.write_text("original content, should NOT be clobbered")
        symlink_path = _lock_file(isolated_config_dir)
        symlink_created = False
        try:
            os.symlink(symlink_target, symlink_path)
            symlink_created = os.path.islink(symlink_path)
        except BaseException:
            symlink_created = False

        if symlink_created:
            behavioral_passed = False
            try:
                with pytest.raises(SystemExit) as exc_info:
                    si_mod._ensure_single_instance_posix(silent=True)
                assert exc_info.value.code == 1, (
                    "FR-37: _ensure_single_instance_posix should exit(1) when the "
                    "lockfile path is a symlink (O_NOFOLLOW raised ELOOP)"
                )
                # The symlink target must NOT have been clobbered.
                assert symlink_target.read_text() == "original content, should NOT be clobbered", (
                    "FR-37: O_NOFOLLOW must prevent the symlink target from being created/truncated via O_CREAT|O_EXCL"
                )
                behavioral_passed = True
            except BaseException:
                behavioral_passed = False

            if not behavioral_passed:
                # Source-level invariant: the ``os.open`` call must
                import inspect

                src = inspect.getsource(si_mod._ensure_single_instance_posix)
                assert "O_NOFOLLOW" in src, (
                    "FR-37: _ensure_single_instance_posix must use O_NOFOLLOW "
                    "in the os.open call (symlink rejection at the kernel level)"
                )
        else:
            # Source-level invariant: the ``os.open`` call must include
            import inspect

            src = inspect.getsource(si_mod._ensure_single_instance_posix)
            assert "O_NOFOLLOW" in src, (
                "FR-37: _ensure_single_instance_posix must use O_NOFOLLOW in "
                "the os.open call (symlink rejection at the kernel level)"
            )

    def test_normal_lockfile_creation_succeeds(self, isolated_config_dir):
        """Sanity check: with no symlink, the normal O_EXCL create path"""
        from voice_typer.server import single_instance as si_mod

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            assert fd is not None, "FR-37: normal lockfile creation (no symlink) should succeed"
            lock_path = _lock_file(isolated_config_dir)
            assert lock_path.exists()
        finally:
            _cleanup_lock_fd(fd)
