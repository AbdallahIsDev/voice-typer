"""Path-safety / path-traversal containment helpers.

CR-28 (config.py split): this module was extracted from
``voice_typer.server.config``.  The three functions here are
re-exported from ``config.py`` so existing call sites — including
``Config.load()`` (which calls ``_is_path_within`` for qwen_model_path /
corrections_path validation), ``model_handlers.import_model`` (which
calls ``_validate_import_path``), and the CR-17 regression test suite
— keep working unchanged.

Behavior is byte-level preserved from the originals in ``config.py``
(same signatures, same logic, same return values, same exception
behaviour).  The only structural change is that
``_validate_import_path`` now resolves ``_config_dir`` via a
function-level import to avoid a circular import with
``config_migration`` (which itself imports ``_validate_path_safety``
at module load time).
"""

import logging
import os
import os.path
import sys
from pathlib import Path

log = logging.getLogger("voice_typer.server.config")


def _validate_path_safety(path: Path, parent: Path) -> Path:
    """Resolve and validate that path stays within parent directory.

    SEC-005: prevents path traversal attacks when user-supplied env vars
    (VOICE_TYPER_CONFIG_DIR, XDG_DATA_HOME, etc.) contain ``..`` sequences
    that could escape the expected parent directory.

    CR-17 fix: previously used ``str(resolved).startswith(str(parent_resolved))``
    which is the classic prefix-match bug — ``/home/userX/secret`` would
    be considered "within" ``/home/user`` because the string
    ``"/home/userX/secret"`` does start with ``"/home/user"``.  Now
    delegates to :func:`_is_path_within`, which uses
    :func:`os.path.commonpath` to respect directory boundaries and
    handles cross-drive Windows paths (returns ``False`` instead of
    raising ``ValueError``).
    """
    # CR-17: use the robust commonpath-based containment check rather
    # than a naive str.startswith.  _is_path_within resolve()s both
    # sides, lower-cases on Windows/macOS (case-insensitive FS), and
    # returns False (not raise) for cross-drive paths.
    if not _is_path_within(path, parent):
        raise ValueError(f"Path traversal detected: {path} escapes {parent}")
    return path.resolve()


def _is_path_within(path: Path, root: Path) -> bool:
    """RW-5: whether ``path`` is ``root`` itself or a descendant of it.

    Cross-platform path-containment check used by
    :func:`_validate_import_path`.  Both arguments are ``resolve()``-d
    first so symlinks and ``..`` segments are canonicalized before
    comparison.

    On Windows and macOS the default filesystem is case-insensitive, so
    the comparison lower-cases both sides on those platforms; on Linux
    the comparison is case-sensitive (matching the filesystem).

    Uses :func:`os.path.commonpath` to correctly respect directory
    boundaries — ``/home/userX`` is NOT considered within
    ``/home/user`` (a naive ``str.startswith`` would incorrectly accept
    it).  ``commonpath`` also handles the root-directory edge case
    (``/etc`` IS within ``/``).
    """
    try:
        p_resolved = str(path.resolve())
        r_resolved = str(root.resolve())
    except (OSError, RuntimeError):
        # Path.resolve() can raise on some platforms if the path is
        # not decodable; treat that as "not within".
        return False
    if sys.platform in ("win32", "darwin"):
        p_resolved = p_resolved.lower()
        r_resolved = r_resolved.lower()
    try:
        common = os.path.commonpath([p_resolved, r_resolved])
    except ValueError:
        # commonpath raises ValueError if the paths are on different
        # drives (Windows) or if one is absolute and the other is not.
        # Either way, ``path`` cannot be within ``root``.
        return False
    return common == r_resolved


def _validate_import_path(dir_path: str) -> str:
    """RW-5: validate that ``dir_path`` is within an allowed root.

    Used by the ``import_model`` IPC handler to reject arbitrary
    filesystem paths the user did not pick via the file chooser.

    Allowed roots (the directory itself or a descendant):
      - the user's home directory — covers ``~/Downloads``,
        ``~/Documents``, the default HF cache at
        ``~/.cache/huggingface/hub``, etc.
      - the OS temp directory (``tempfile.gettempdir()``) — covers
        ``/tmp``, ``%TEMP%``, etc.
      - the app's own HF cache directory (``_config_dir() /
        "huggingface" / "hub"``) — so re-importing from the app's
        cache is allowed.
      - ``$HF_HOME`` if set — some users point this at a custom
        location (e.g. an external drive mounted under a non-home
        path).

    Returns the resolved path as a string.  Raises ``ValueError`` if
    the path is outside all allowed roots.
    """
    import tempfile

    # CR-28: function-level lookup of ``_config_dir`` from the config
    # module (NOT from ``config_migration`` directly) so tests that
    # monkeypatch ``config._config_dir`` (see
    # ``tests/test_import_model_security.py``) continue to drive the
    # allowed-roots check after the extraction.  We deliberately do NOT
    # use ``from voice_typer.server.config_migration import _config_dir``
    # here because that would bind to the original function object —
    # monkeypatch.setattr(config, "_config_dir", ...) replaces the
    # binding in ``config``'s namespace only, and a direct
    # ``config_migration`` import would miss the patch.
    from voice_typer.server import config as _cfg

    resolved = Path(dir_path).resolve()
    allowed_roots = [
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        (_cfg._config_dir() / "huggingface" / "hub").resolve(),
    ]
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        allowed_roots.append(Path(hf_home).resolve())
    for root in allowed_roots:
        if _is_path_within(resolved, root):
            return str(resolved)
    raise ValueError(
        f"Import path '{dir_path}' is outside the allowed roots (home directory, temp directory, or HF cache)."
    )
