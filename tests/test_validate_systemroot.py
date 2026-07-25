"""CR-19 regression tests: ``_validate_systemroot`` fail-closed vs reset.

The previous implementation logged at ``error`` level for every
anomalous SystemRoot but always ``return``-ed, so startup continued
even when a malicious ``SystemRoot`` could be a DLL-injection vector.

The CR-19 fix introduces explicit fail-closed vs reset-to-default
decisions:

- Path traversal (``..``)             → ``sys.exit(1)`` (security)
- Unusual characters (``<>|"&'\\n\\r\\t``) → ``sys.exit(1)`` (security)
- Missing directory                   → reset to ``C:\\Windows`` + continue (usability)
- Missing ``System32\\notepad.exe``   → log warning + continue (not a hard blocker)

These tests mock ``is_windows()`` to True so the Windows-only code path
is exercised on the Linux CI runner, and set ``SYSTEMROOT`` (all-caps,
matching the env var name the function reads on Linux).

See:
- ``voice_typer/server/config.py:_validate_systemroot``
- ``review.md`` finding CR-19
"""

import os

import pytest


@pytest.fixture
def windows_env(monkeypatch):
    """Mock ``is_windows()`` to True so the SystemRoot validation runs.

    Yields the monkeypatch so individual tests can set additional env
    vars / patches.  Uses ``SYSTEMROOT`` (all-caps) which is what the
    function reads via ``os.environ.get("SYSTEMROOT", "")``.  On
    Windows env var names are case-insensitive (``SystemRoot`` and
    ``SYSTEMROOT`` are the same), but on the Linux CI runner they're
    distinct — we must use the all-caps form to actually exercise the
    validation code path.
    """
    from voice_typer.server import config

    monkeypatch.setattr(config, "is_windows", lambda: True)
    # Save and clear SYSTEMROOT so each test starts from a known state.
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    return monkeypatch


class TestSystemRootPathTraversalFailClosed:
    """CR-19: path-traversal in SystemRoot → ``sys.exit(1)`` (fail-closed)."""

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
        """On fail-closed exit, SystemRoot is NOT silently reset — the
        user must see the startup abort and investigate.  (Silently
        resetting would hide the attack.)"""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..\attacker")
        with pytest.raises(SystemExit):
            _validate_systemroot()
        # The malicious value should still be there (not silently reset).
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows\..\attacker"


class TestSystemRootUnusualCharsFailClosed:
    """CR-19: unusual characters in SystemRoot → ``sys.exit(1)`` (fail-closed)."""

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
    """Build a callable that mimics ``pathlib.Path`` for the specific
    calls made by ``_validate_systemroot``.

    The function uses ``Path(systemroot).is_dir()``,
    ``Path(r"C:\\Windows").is_dir()``, and
    ``Path(systemroot) / "System32" / "notepad.exe"`` then ``.exists()``.
    This fake controls each branch's return value via the keyword args.
    """
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
    """CR-19: missing directory → reset to ``C:\\Windows`` + continue."""

    def test_missing_dir_resets_to_default(self, windows_env, monkeypatch):
        """A SystemRoot pointing to a nonexistent directory should be
        reset to ``C:\\Windows`` and startup should CONTINUE (not exit).

        Rationale: a missing directory is a usability issue (e.g. user
        moved their Windows installation), not a direct security issue.
        Refusing to start would lock the user out of the app entirely.
        """
        from voice_typer.server import config

        user_root = r"C:\Nonexistent\Path\12345"
        windows_env.setenv("SYSTEMROOT", user_root)

        # Mock Path so:
        #   - the user-supplied path → is_dir()=False (missing)
        #   - C:\Windows             → is_dir()=True  (so reset kicks in)
        #   - notepad.exe            → exists()=True  (so we don't hit
        #                                            the notepad branch)
        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=False,
            default_is_dir=True,
            notepad_exists=True,
        )
        monkeypatch.setattr(config, "Path", fake_path)

        # Should NOT raise SystemExit — the function returns normally
        # after resetting SystemRoot to C:\Windows.
        config._validate_systemroot()

        # Verify SystemRoot was reset to the default.
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows"

    def test_missing_dir_does_not_exit(self, windows_env, monkeypatch):
        """Even if both the user-supplied path AND C:\\Windows are
        missing, the function must NOT sys.exit — it just leaves
        SystemRoot as-is and lets downstream Win32 APIs fail with
        their own diagnostics (usability fallback)."""
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
        # It's left as the user-supplied value.
        assert os.environ.get("SYSTEMROOT") == user_root


class TestSystemRootMissingNotepadContinues:
    """CR-19: missing ``System32\\notepad.exe`` → log warning + continue."""

    def test_missing_notepad_does_not_exit(self, windows_env, monkeypatch):
        """If SystemRoot exists but notepad.exe is missing, the
        function must NOT exit — just log a warning.  The caller is
        expected to use a hardcoded notepad fallback path."""
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
    """CR-19: ``_validate_systemroot`` is a no-op on non-Windows platforms.

    (Sanity check — the CR-19 fix preserves this behavior.)
    """

    def test_noop_on_posix(self, monkeypatch):
        """On Linux/macOS, the function returns immediately without
        touching the SystemRoot env var."""
        from voice_typer.server import config

        # Force is_windows to False (the default on Linux CI, but be explicit).
        monkeypatch.setattr(config, "is_windows", lambda: False)
        monkeypatch.setenv("SYSTEMROOT", "irrelevant-on-posix")

        # Must not raise.
        config._validate_systemroot()

        assert os.environ.get("SYSTEMROOT") == "irrelevant-on-posix"


class TestSystemRootEmptyValueContinues:
    """CR-19: empty SystemRoot value → log warning + return (no exit)."""

    def test_empty_systemroot_continues(self, windows_env):
        """An empty SystemRoot env var is unusual but not a direct
        attack vector.  The function logs a warning and returns
        without exiting."""
        from voice_typer.server.config import _validate_systemroot

        # Set SYSTEMROOT to an empty string (env var is set but empty).
        windows_env.setenv("SYSTEMROOT", "")

        # Must NOT raise SystemExit.
        _validate_systemroot()


# ──────────────────────────────────────────────────────────────────────────
# CFG-10: path-traversal check uses path COMPONENTS, not substring match
# ──────────────────────────────────────────────────────────────────────────


class TestCfg10PathTraversalComponentCheck:
    """CFG-10 (Low): the previous ``if ".." in systemroot:`` substring
    check produced false positives for any path containing the literal
    characters ``..`` inside a directory name (e.g. ``C:\\Win..dows`` or
    ``C:\\my..app\\System32``).  Such paths are unusual but LEGITIMATE
    on Windows (NTFS allows ``..`` in file/dir names; only the path
    component ``..`` is the traversal sentinel).

    The fix uses ``any(part == ".." for part in PureWindowsPath(systemroot).parts)``
    so only an actual ``..`` path component triggers the fail-closed
    exit.  ``PureWindowsPath`` (not ``Path``) is used so the parsing is
    correct on the Linux CI runner — ``PosixPath`` would treat
    backslashes as ordinary characters and never split a Windows path
    into components, masking the bug.

    These tests verify:
      1. Real traversal (``C:\\Windows\\..\\attacker``) STILL exits (no
         regression in the security check).
      2. False-positive paths (``C:\\Win..dows``) NO LONGER exit.
      3. The directory-exists check still runs after the traversal
         check passes (so a valid-structure but missing directory is
         still handled by the reset-to-default branch).
    """

    def test_real_traversal_mid_path_still_exits(self, windows_env):
        """``C:\\Windows\\..\\attacker`` has a real ``..`` component and
        must still trigger fail-closed (no security regression)."""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..\attacker")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_real_traversal_at_end_still_exits(self, windows_env):
        """``C:\\Windows\\..`` has a real ``..`` component (at the end)
        and must still trigger fail-closed."""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\Windows\..")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_real_traversal_multiple_still_exits(self, windows_env):
        """``C:\\..\\..\\attacker`` has multiple ``..`` components and
        must still trigger fail-closed."""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", r"C:\..\..\attacker")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_substring_in_dir_name_does_not_exit(self, windows_env, monkeypatch):
        """``C:\\Win..dows`` contains ``..`` as a SUBSTRING but not as a
        path component — must NOT trigger fail-closed.

        Before CFG-10, this would have exited (false positive).  The
        fix uses ``PureWindowsPath(systemroot).parts`` and checks for
        an exact ``..`` component, so this legitimate path is accepted.
        """
        from voice_typer.server import config

        user_root = r"C:\Win..dows"
        windows_env.setenv("SYSTEMROOT", user_root)

        # Mock Path so the directory-exists check passes (so we don't
        # hit the reset-to-default branch and confuse the test).  The
        # mock only needs to support is_dir() and the / operator +
        # exists() for the notepad_path check.
        fake_path = _make_fake_path(
            user_root=user_root,
            user_root_is_dir=True,  # directory exists
            default_is_dir=True,
            notepad_exists=True,
        )
        monkeypatch.setattr(config, "Path", fake_path)

        # Must NOT raise SystemExit — the path is unusual but legitimate.
        config._validate_systemroot()

        # SystemRoot was NOT reset (the directory "exists", so the
        # reset-to-default branch didn't fire either).
        assert os.environ.get("SYSTEMROOT") == user_root

    def test_substring_in_nested_dir_name_does_not_exit(self, windows_env, monkeypatch):
        """``C:\\my..app\\System32`` — ``..`` appears in a directory
        name but not as a path component.  Must NOT trigger fail-closed."""
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
        """``C:\\Windows\\file..exe`` — ``..`` in a filename (not a path
        component).  Must NOT trigger fail-closed."""
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
        """A SystemRoot that uses Unix-style ``..`` segments (e.g.
        ``/etc/../attacker``) — ``PureWindowsPath`` is lenient and
        treats ``/`` as a separator too, so this still triggers
        fail-closed.  (On Windows, ``\\`` is the canonical separator,
        but ``/`` is also accepted by NTFS and most Win32 APIs.)"""
        from voice_typer.server.config import _validate_systemroot

        windows_env.setenv("SYSTEMROOT", "/etc/../attacker")
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1

    def test_pure_windows_path_parses_backslashes_on_linux(self):
        """Sanity: ``PureWindowsPath`` correctly splits a Windows-style
        path on backslashes when running on the Linux CI runner.  This
        is the core invariant that lets CFG-10 work cross-platform.

        If this test fails, the CFG-10 fix would silently regress on
        Linux (PosixPath would treat backslashes as ordinary chars and
        never detect ``..`` components).
        """
        from pathlib import PureWindowsPath

        # Real traversal — ``..`` is a path component.
        parts = PureWindowsPath(r"C:\Windows\..\attacker").parts
        assert ".." in parts, f"PureWindowsPath should split on backslash; got parts={parts!r}"

        # False positive — ``..`` is inside a directory name, NOT a
        # path component.
        parts = PureWindowsPath(r"C:\Win..dows").parts
        assert ".." not in parts, (
            f"PureWindowsPath should NOT treat '..' inside a name as a component; got parts={parts!r}"
        )
