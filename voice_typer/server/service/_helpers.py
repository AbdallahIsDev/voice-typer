"""Shared module-level helpers extracted from the original ``service.py``.

These are pure functions (no ``self``) that the original
``voice_typer/server/service.py`` exposed at module scope. They are kept
in this private submodule so the mixin files can import them without a
circular import on the package ``__init__``, and so
``voice_typer.server.service`` can re-export them unchanged.
"""

from voice_typer.server._fs_walk import find_symlink_in_tree as _find_symlink_in_tree

__all__ = ["_find_symlink_in_tree"]
