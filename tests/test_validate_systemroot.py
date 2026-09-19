"""regression tests: ``_validate_systemroot`` fail-closed vs reset."""

import os

import pytest


@pytest.fixture
def windows_env(monkeypatch):
    """Mock ``is_windows()`` to True so the SystemRoot validation runs."""
    from voice_typer.server import config

    monkeypatch.setattr(config, "is_windows", lambda: True)
    # Save and clear SYSTEMROOT so each test starts from a known state.
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    return monkeypatch


class TestSystemRootPathTraversalFailClosed:
    """path-traversal in SystemRoot → ``sys.exit(1)`` (fail-closed)."""

    def test_traversal_in_systemroot_exits(self, windows_env):
        """A SystemRoot containing ``..`` must abort startup (fail-closed)."""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..\..\attacker")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_traversal_at_end_of_path_exits(self, windows_env):
        """``..`` at the end of the path is also fail-closed."""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_traversal_does_not_reset_env_var(self, windows_env):
        """user must see the startup abort and investigate.  (Silently"""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..\attacker")
        with pytest.raises(SystemExit):
            _validate_systemroot()
        # The malicious value should still be there (not silently reset).
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows\..\attacker"


class TestSystemRootUnusualCharsFailClosed:
    """unusual characters in SystemRoot → ``sys.exit(1)`` (fail-closed)."""

    @pytest.mark.parametrize(
        "bad_char",
        ["<", ">", "|", '"', "&", "'", "\n", "\r", "\t"],
    )
    def test_unusual_char_exits(self, windows_env, bad_char):
        """Each unusual character in SystemRoot must abort startup."""
        from voice_typer.server.config import _validate_systemroot

        # Insert the unusual char in an otherwise-valid Windows path.
        bad_root = f"C:\\Win{bad_char}dows"
        windows_env.setenv("SYSTEMROOT", bad_root)
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1


def _make_fake_path(user_root, user_root_is_dir=False, default_is_dir=True, notepad_exists=True):
    """Build a callable that mimics ``pathlib.Path`` for the specific"""
    default_root = r"C:\Windows"

    class _FakePathInstance:
        def __init__(self, s):
            self._s = str(s)

        def __truediv__(self, other):
            # Support chained division: Path(systemroot) / "System32" / "notepad.exe"
            return _FakePathInstance(self._s + "\\" + str(other))

        def is_dir(self):
            if self._s == user_root:
                return user_root_is_dir
            if self._s == default_root:
                return default_is_dir
            return False

        def exists(self):
            # The notepad_path is Path(systemroot) / "System32" / "notepad.exe"
            if "notepad.exe" in self._s:
                return notepad_exists
            return False

        def __str__(self):
            return self._s

    return _FakePathInstance


class TestSystemRootMissingDirResetToDefault:
    """missing directory → reset to ``C:\\Windows`` + continue."""

    def test_missing_dir_resets_to_default(self, windows_env, monkeypatch):
        """A SystemRoot pointing to a nonexistent directory should be"""
        from voice_typer.server import config

        user_root = r"C:\Nonexistent\Path\12345"
        windows_env.setenv("SYSTEMROOT", user_root)

        # - the user-supplied path → is_dir()=False (missing)
        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=False,
            default_is_dir=True,
            notepad_exists=True,
        )
        monkeypatch.setattr(config, "Path", fake_path)

        # Should NOT raise SystemExit, the function returns normally
        config._validate_systemroot()

        # Verify SystemRoot was reset to the default.
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows"

    def test_missing_dir_does_not_exit(self, windows_env, monkeypatch):
        """Even if both the user-supplied path AND C:\\Windows are"""
        from voice_typer.server import config

        user_root = r"C:\Ghost\Path"
        windows_env.setenv("SYSTEMROOT", user_root)

        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=False,
            default_is_dir=False,  # C:\Windows also missing
            notepad_exists=False,
        )
        monkeypatch.setattr(config, "Path", fake_path)

        # Must NOT raise SystemExit.
        config._validate_systemroot()

        # SystemRoot was not reset (because C:\Windows also "missing").
        assert os.environ.get("SYSTEMROOT") == user_root


class TestSystemRootMissingNotepadContinues:
    """missing ``System32\\notepad.exe`` → log warning + continue."""

    def test_missing_notepad_does_not_exit(self, windows_env, monkeypatch):
        """function must NOT exit, just log a warning.  The caller is"""
        from voice_typer.server import config

        user_root = r"C:\Windows"
        windows_env.setenv("SYSTEMROOT", user_root)

        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=True,  # directory exists
            default_is_dir=True,
            notepad_exists=False,  # notepad.exe missing
        )
        monkeypatch.setattr(config, "Path", fake_path)

        # Must NOT raise SystemExit.
        config._validate_systemroot()

        # SystemRoot is left unchanged (not reset).
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows"


class TestSystemRootNoopOnPosix:
    """``_validate_systemroot`` is a no-op on non-Windows platforms."""

    def test_noop_on_posix(self, monkeypatch):
        """On Linux/macOS, the function returns immediately without"""
        from voice_typer.server import config

        # Force is_windows to False (the default on Linux CI, but be explicit).
        monkeypatch.setattr(config, "is_windows", lambda: False)
        monkeypatch.setenv("SYSTEMROOT", "irrelevant-on-posix")

        # Must not raise.
        config._validate_systemroot()

        assert os.environ.get("SYSTEMROOT") == "irrelevant-on-posix"


class TestSystemRootEmptyValueContinues:
    """empty SystemRoot value → log warning + return (no exit)."""

    def test_empty_systemroot_continues(self, windows_env):
        """An empty SystemRoot env var is unusual but not a direct"""
        from voice_typer.server.config import _validate_systemroot

        # Set SYSTEMROOT to an empty string (env var is set but empty).
        windows_env.setenv("SYSTEMROOT", "")

        # Must NOT raise SystemExit.
        _validate_systemroot()


class TestCfg10PathTraversalComponentCheck:
    """CFG-10 (Low): the previous ``if \"..\" in systemroot:`` substring"""

    def test_real_traversal_mid_path_still_exits(self, windows_env):
        """``C:\\Windows\\..\\attacker`` has a real ``..`` component and"""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..\attacker")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_real_traversal_at_end_still_exits(self, windows_env):
        """``C:\\Windows\\..`` has a real ``..`` component (at the end)"""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_real_traversal_multiple_still_exits(self, windows_env):
        """``C:\\..\\..\\attacker`` has multiple ``..`` components and"""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\..\..\attacker")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_substring_in_dir_name_does_not_exit(self, windows_env, monkeypatch):
        """path component, must NOT trigger fail-closed."""
        from voice_typer.server import config

        user_root = r"C:\Win..dows"
        windows_env.setenv("SYSTEMROOT", user_root)

        # Mock Path so the directory-exists check passes (so we don't
        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=True,  # directory exists
            default_is_dir=True,
            notepad_exists=True,
        )
        monkeypatch.setattr(config, "Path", fake_path)

        # Must NOT raise SystemExit, the path is unusual but legitimate.
        config._validate_systemroot()

        assert os.environ.get("SYSTEMROOT") == user_root

    def test_substring_in_nested_dir_name_does_not_exit(self, windows_env, monkeypatch):
        """``C:\\my..app\\System32``: ``..`` appears in a directory"""
        from voice_typer.server import config

        user_root = r"C:\my..app\System32"
        windows_env.setenv("SYSTEMROOT", user_root)

        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=True,
            default_is_dir=True,
            notepad_exists=True,
        )
        monkeypatch.setattr(config, "Path", fake_path)

        config._validate_systemroot()
        assert os.environ.get("SYSTEMROOT") == user_root

    def test_substring_with_extension_does_not_exit(self, windows_env, monkeypatch):
        """``C:\\Windows\\file..exe``: ``..`` in a filename (not a path"""
        from voice_typer.server import config

        user_root = r"C:\Windows\file..exe"
        windows_env.setenv("SYSTEMROOT", user_root)

        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=True,
            default_is_dir=True,
            notepad_exists=True,
        )
        monkeypatch.setattr(config, "Path", fake_path)

        config._validate_systemroot()
        assert os.environ.get("SYSTEMROOT") == user_root

    def test_unix_style_traversal_still_exits(self, windows_env):
        """A SystemRoot that uses Unix-style ``..`` segments (e.g."""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", "/etc/../attacker")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_pure_windows_path_parses_backslashes_on_linux(self):
        """Sanity: ``PureWindowsPath`` correctly splits a Windows-style"""
        from pathlib import PureWindowsPath

        # Real traversal: ``..`` is a path component.
        parts = PureWindowsPath(r"C:\Windows\..\attacker").parts
        assert ".." in parts, f"PureWindowsPath should split on backslash; got parts={parts!r}"

        parts = PureWindowsPath(r"C:\Win..dows").parts
        assert ".." not in parts, (
            f"PureWindowsPath should NOT treat '..' inside a name as a component; got parts={parts!r}"
        )
