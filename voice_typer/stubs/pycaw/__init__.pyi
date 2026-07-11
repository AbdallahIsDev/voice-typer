# PYREFLY-001: stub for the `pycaw` package (Windows COM audio control).
# pycaw is declared in pyproject.toml with `sys_platform == 'win32'`, so
# it is never installed on the Linux/macOS CI runners and pyrefly reports
# `missing-import` on `from pycaw.pycaw import ...`. This stub declares
# the package surface; the actual symbols live in `pycaw.pycaw`.

__version__: str

# Re-export the submodule symbols for `from pycaw import AudioUtilities`.
# (The real pycaw/__init__.py does this; we mirror it so star-imports
# don't lose type info.)

__all__: list[str]
