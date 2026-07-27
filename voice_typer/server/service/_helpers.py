"""Shared module-level helpers extracted from the original ``service.py``.

These are pure functions (no ``self``) that the original
``voice_typer/server/service.py`` exposed at module scope. They are kept
in this private submodule so the mixin files can import them without a
circular import on the package ``__init__``, and so
``voice_typer.server.service`` can re-export them unchanged.
"""


def _find_symlink_in_tree(root):
    """RW-5: return the path of the first symlink found under ``root``,
    or ``None`` if there are none.

    Used by :meth:`VoiceTyperService.import_model` to reject poisoned
    model dirs that contain symlinks (e.g. a symlink to
    ``~/.ssh/id_rsa``).  HuggingFace hub cache dirs never legitimately
    contain symlinks at the *source* side — the hub uses symlinks
    inside its own cache (``snapshots/<rev>/...`` → ``blobs/<hash>``),
    but a user-supplied import directory is expected to contain real
    files only.

    ``os.walk`` with the default ``followlinks=False`` does NOT descend
    into symlinked directories, but it DOES include them in
    ``dirnames`` — so both symlinked files and symlinked directories
    are detected by this check.
    """
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames) + list(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                return full
    return None
