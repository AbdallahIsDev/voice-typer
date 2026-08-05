# scripts — dev/build tooling package.
#
# Marked as a regular package (not a namespace package) so
# ``from scripts.diagnostics import ...`` resolves reliably from the
# repo root when the repo root is on ``sys.path`` (pytest with the
# rootdir conftest). Without ``__init__.py``, ``scripts`` could be
# shadowed by an unrelated top-level ``scripts`` module (or silently
# fail to resolve in some import modes).
