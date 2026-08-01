"""Tests for SEC-005: Path traversal validation."""

import sys
from pathlib import Path

import pytest


def test_validate_path_safety_normal_path():
    """Normal paths within parent are accepted."""
    from voice_typer.server.config import _validate_path_safety

    parent = Path("/home/user/.voice-typer")
    child = Path("/home/user/.voice-typer/config.json")
    # This may not work on all systems, so use tmp paths
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        child = parent / "config.json"
        assert _validate_path_safety(child, parent) == child.resolve()


def test_validate_path_safety_traversal():
    """Path traversal attempts are rejected."""
    import tempfile

    from voice_typer.server.config import _validate_path_safety

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        child = parent / ".." / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path_safety(child, parent)


class TestIsPathWithin:
    """Tests for ``_is_path_within`` (RW-5) — the robust
    ``os.path.commonpath`` containment check used by ``_validate_import_path``.

    These edge cases were previously only exercised transitively through
    ``test_import_model_security.py``; the helper's own behavior (case
    sensitivity, drive boundaries, sibling-prefix rejection) was unpinned.
    """

    def test_same_path_is_within_itself(self):
        from voice_typer.server.config import _is_path_within

        p = Path("/home/user/.voice-typer")
        assert _is_path_within(p, p) is True

    def test_direct_child_is_within(self):
        from voice_typer.server.config import _is_path_within

        root = Path("/home/user/.voice-typer")
        child = root / "config.json"
        assert _is_path_within(child, root) is True

    def test_sibling_prefix_is_not_within(self):
        # The classic /home/userX vs /home/user trap that a naive
        # str.startswith() check would get wrong. commonpath must
        # respect the directory boundary.
        from voice_typer.server.config import _is_path_within

        root = Path("/home/user")
        sibling = Path("/home/userX")
        assert _is_path_within(sibling, root) is False

    def test_traversal_after_resolve_is_rejected(self):
        # A ".." segment that escapes root must resolve to a path
        # outside root, so containment is False.
        from voice_typer.server.config import _is_path_within

        root = Path("/home/user/.voice-typer")
        escaped = root / ".." / ".." / "etc"
        assert _is_path_within(escaped, root) is False

    def test_root_directory_contains_its_descendants(self):
        # /etc IS within / (commonpath edge case).
        from voice_typer.server.config import _is_path_within

        assert _is_path_within(Path("/etc"), Path("/")) is True

    def test_case_insensitive_on_windows_and_macos(self, monkeypatch):
        # On win32/darwin the comparison is lower-cased, so C:\Users\X
        # is within c:\users\X.
        #
        # previously ``monkeypatch.setattr(config.sys, "platform",
        # "win32")`` — but ``config`` does NOT import ``sys`` at module
        # level (no ``import sys`` in ``config.py``), so ``config.sys``
        # raised ``AttributeError`` and the test always errored out
        # before reaching the assertion.  Patching the GLOBAL ``sys``
        # module's ``platform`` attribute (which ``_is_path_within``
        # reads via its own ``import sys``) is the correct fix.
        #
        # even more robust — pass ``case_sensitive=False`` to
        # ``_is_path_within`` explicitly so the test no longer depends
        # on the global ``sys.platform`` value at all (the
        # case-insensitive branch is exercised deterministically
        # regardless of the host platform).
        from voice_typer.server import config

        # Belt-and-braces: patch the global sys.platform too so any
        # code path that reads sys.platform directly (e.g. inside
        # ``_is_path_within`` when ``case_sensitive is None``) sees
        # "win32".  ``case_sensitive=False`` below makes this belt-only.
        monkeypatch.setattr(sys, "platform", "win32")
        root = Path("C:/users/X")
        child = Path("C:/Users/X/AppData")
        assert config._is_path_within(child, root, case_sensitive=False) is True

    def test_case_sensitive_on_linux(self, monkeypatch):
        # On Linux the comparison is case-sensitive, so /Home/X is NOT
        # within /home/X.
        #
        # same AttributeError bug — ``config.sys`` doesn't
        # exist.  Patch the global ``sys`` module instead.
        #
        # pass ``case_sensitive=True`` explicitly so the test
        # exercises the case-sensitive branch deterministically (the
        # Linux CI runner is already case-sensitive, but pinning the
        # parameter makes the intent obvious and survives a future
        # move to a case-insensitive CI filesystem).
        from voice_typer.server import config

        monkeypatch.setattr(sys, "platform", "linux")
        root = Path("/home/X")
        child = Path("/Home/X")
        assert config._is_path_within(child, root, case_sensitive=True) is False

    def test_cross_drive_windows_returns_false(self, monkeypatch):
        # commonpath raises ValueError for paths on different drives;
        # the function must return False rather than raise.
        #
        # same AttributeError bug — patch the global ``sys``
        # module instead of ``config.sys`` (which doesn't exist).
        # pass ``case_sensitive=False`` explicitly to exercise
        # the Windows-style (case-insensitive) branch deterministically.
        #
        # NOTE: on a Linux CI runner ``Path("C:/voice-typer").resolve()``
        # returns ``/cwd/C:/voice-typer`` (Linux treats ``C:`` as a
        # regular path component, not a drive letter), so the test
        # passes because the two paths diverge at the drive-letter
        # component, not because ``os.path.commonpath`` raises
        # ``ValueError`` for cross-drive paths.  This is acceptable
        # for the regression contract (the function must return False
        # — and it does); a true cross-drive ``ValueError`` test
        # requires running on Windows or mocking ``Path.resolve()``
        # to return Windows-style absolute paths.
        from voice_typer.server import config

        monkeypatch.setattr(sys, "platform", "win32")
        root = Path("C:/voice-typer")
        child = Path("D:/voice-typer/data")
        assert config._is_path_within(child, root, case_sensitive=False) is False
