"""Filesystem walk helpers."""

from __future__ import annotations

import os


def find_symlink_in_tree(root: str | os.PathLike[str]) -> str | None:
    """Return the path of the first symlink found under ``root``, or"""
    root_path = os.fspath(root)
    for dirpath, dirnames, filenames in os.walk(root_path):
        for name in list(dirnames) + list(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                return full
    return None


__all__ = ["find_symlink_in_tree"]
