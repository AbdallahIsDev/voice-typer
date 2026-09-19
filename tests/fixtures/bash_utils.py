"""Shared ``bash``-availability probe for tests that run ``bash -n``."""

from __future__ import annotations

import shutil
import subprocess

_usable: bool | None = None


def bash_usable() -> bool:
    """True iff a *working* ``bash`` is on PATH."""
    global _usable
    if _usable is None:
        if shutil.which("bash") is None:
            _usable = False
        else:
            try:
                result = subprocess.run(
                    ["bash", "-c", "exit 0"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                _usable = result.returncode == 0
            except (OSError, subprocess.SubprocessError):
                _usable = False
    return _usable
