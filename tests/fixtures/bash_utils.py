"""Shared ``bash``-availability probe for tests that run ``bash -n``.

GitHub Actions Windows runners resolve ``bash`` to
``C:\\Windows\\System32\\bash.exe`` — the WSL launcher stub — which
``shutil.which`` happily finds but which exits non-zero with "Windows
Subsystem for Linux has no installed distributions." when actually
invoked. Tests that gate on ``shutil.which("bash")`` alone therefore
RUN on those runners and fail. :func:`bash_usable` additionally probes
that ``bash -c 'exit 0'`` succeeds, so such tests can skip cleanly.
"""

from __future__ import annotations

import shutil
import subprocess

_usable: bool | None = None


def bash_usable() -> bool:
    """True iff a *working* ``bash`` is on PATH.

    The probe spawns a subprocess, so the result is cached after the
    first call (it must not run once per test).
    """
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
