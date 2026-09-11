"""Filesystem walk helpers shared by security-relevant paths.

:func:`find_symlink_in_tree` is the single implementation of the
symlink-poisoning directory scan used by BOTH:

* the model-import path (:meth:`VoiceTyperService.import_model` —
  rejects user-supplied model directories that contain symlinks),
* the legacy-config migration (:func:`voice_typer.server.config_internals.paths._migrate_from_legacy`
 , rejects a poisoned legacy tree before ``shutil.copytree`` runs).

Both sites guard the same attack class (e.g. ``legacy/models/qwen`` →
``~/.ssh/id_rsa`` planted by an attacker with write access to the
source directory). The function is stdlib-only (``os``) and lives in
this leaf module so the two consumers can import it without a
circular dependency: ``service._helpers`` sits under a package that
imports ``config``, and ``config`` imports ``config_internals.paths``.
"""

from __future__ import annotations

import os


def find_symlink_in_tree(root: str | os.PathLike[str]) -> str | None:
    """Return the path of the first symlink found under ``root``, or
    ``None`` if there are none.

    ``os.walk`` with the default ``followlinks=False`` does NOT descend
    into symlinked directories, but it DOES include them in
    ``dirnames``, so both symlinked files and symlinked directories
    are detected by this check.

    Hardening note: this function must stay the SINGLE copy, a future
    fix (dangling-symlink handling, mid-walk permission errors) applied
    to one site silently misses the other if the copies fork again.
    """
    root_path = os.fspath(root)
    for dirpath, dirnames, filenames in os.walk(root_path):
        for name in list(dirnames) + list(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                return full
    return None


__all__ = ["find_symlink_in_tree"]
