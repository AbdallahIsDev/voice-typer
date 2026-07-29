# ruff: noqa: A001, A002, N802, N803, N816
# RW-6 (pyrefly): stub package for `win32com` (Windows-only, shipped
# via the `pywin32` distribution).
#
# The runtime code only ever imports the `win32com.client` submodule
# (see `voice_typer/server/server_platform/autostart_windows.py::_create_windows_shortcut`).
# Declaring the package surface here lets pyrefly resolve the submodule
# import. All symbols are `Any` because the surrounding code is wrapped
# in `try/except ImportError` and is Windows-only.
from typing import Any

# Submodule re-export marker. The actual client API lives in
# `win32com/client.pyi` (next to this file).
client: Any

__all__: list[str]
