"""Direct unit tests for the shared filesystem-walk helper."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from voice_typer.server._fs_walk import find_symlink_in_tree

# Windows needs the SeCreateSymbolicLink privilege (admin or Developer
_posix_symlink = pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlink semantics")


@_posix_symlink
def test_finds_symlinked_file(tmp_path: Path) -> None:
    """A symlinked file inside a nested dir is reported."""
    (tmp_path / "models" / "qwen").mkdir(parents=True)
    target = tmp_path / "outside.txt"
    target.write_text("victim")
    os.symlink(target, tmp_path / "models" / "qwen" / "weights.bin")

    found = find_symlink_in_tree(tmp_path)
    assert found is not None
    assert Path(found).is_symlink()


@_posix_symlink
def test_finds_symlinked_directory(tmp_path: Path) -> None:
    """A symlinked directory is reported (os.walk lists it in dirnames)."""
    target = tmp_path / ".ssh"
    target.mkdir()
    (tmp_path / "legacy").mkdir(parents=True)
    os.symlink(target, tmp_path / "legacy" / "data")

    found = find_symlink_in_tree(tmp_path)
    assert found is not None
    assert Path(found).is_symlink()


def test_clean_tree_returns_none(tmp_path: Path) -> None:
    """A tree with no symlinks yields ``None`` (pass condition)."""
    (tmp_path / "models" / "qwen").mkdir(parents=True)
    (tmp_path / "models" / "qwen" / "weights.bin").write_text("real")

    assert find_symlink_in_tree(tmp_path) is None


def test_empty_tree_returns_none(tmp_path: Path) -> None:
    """An empty (or missing) root is a safe ``None``."""
    assert find_symlink_in_tree(tmp_path) is None


def test_both_consumers_share_one_implementation() -> None:
    """The two production consumers resolve to the SAME function object."""
    from voice_typer.server import _fs_walk
    from voice_typer.server.config_internals import paths as config_paths
    from voice_typer.server.service import _helpers as service_helpers

    assert config_paths._find_symlink_in_tree is _fs_walk.find_symlink_in_tree
    assert service_helpers._find_symlink_in_tree is _fs_walk.find_symlink_in_tree
