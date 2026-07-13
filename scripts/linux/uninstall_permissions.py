#!/usr/bin/env python3
"""Voice Typer — Linux keyboard permission uninstaller.

Thin wrapper around ``install_permissions.py --uninstall``. Kept as a
separate script so package managers can reference it directly in prerm
scripts without passing arguments.

Called by:
- Debian ``prerm`` (as root, during ``apt remove voice-typer``)
- RPM ``%preun`` (as root, during ``dnf remove voice-typer``)

Exit codes: same as install_permissions.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Delegate to install_permissions.py --uninstall
installer_path = Path(__file__).resolve().parent / "install_permissions.py"
if not installer_path.is_file():
    print("[voice-typer-permissions] ERROR: install_permissions.py not found", file=sys.stderr)
    sys.exit(1)

# Use exec to replace this process — cleaner than subprocess for a wrapper
os.execv(sys.executable, [sys.executable, str(installer_path), "--uninstall"])
