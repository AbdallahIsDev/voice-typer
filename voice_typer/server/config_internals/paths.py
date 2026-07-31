"""Path-safety + config-dir resolution + cross-process config lock.

Extracted verbatim from ``voice_typer.server.config`` (
partial split).  Every public symbol here is re-exported from
``config.py`` so existing ``from voice_typer.server.config import
_config_dir`` callers keep working unchanged.

Contents:
- :func:`_validate_path_safety`     — path-traversal guard for user-supplied env vars.
- :func:`_is_path_within`           — robust cross-platform path-containment check.
- :func:`_validate_import_path`     — bounds-check for the ``import_model`` IPC handler.
- :func:`_validate_systemroot`      — Windows ``SystemRoot`` sanity check (fail-closed).
- :func:`_config_dir`               — platform-aware data-dir resolver (memoized).
- :func:`_reset_config_dir_cache`   — test-only cache-clear helper.
- :func:`_migrate_from_legacy`      — one-time v0 -> v1 config-dir move.
- :func:`_acquire_config_lock`      — cross-process ``config.json.lock`` context manager.

No circular imports: this module depends only on the stdlib,
``platform_utils``, and (lazily, inside ``_acquire_config_lock``) on
the ``voice_typer.server.config`` module attribute
``_CONFIG_LOCK_TIMEOUT_SECONDS`` so the test suite's
``monkeypatch.setattr("voice_typer.server.config._CONFIG_LOCK_TIMEOUT_SECONDS",
…)`` continues to take effect.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import sys
import time
from pathlib import Path, PureWindowsPath

log = logging.getLogger("voice_typer.server.config")

# cross-process lock for Config.save().  This is the canonical
# home for the constant; ``config.py`` re-exports it via
# ``from voice_typer.server.config_internals.paths import
# _CONFIG_LOCK_TIMEOUT_SECONDS`` so existing
# ``monkeypatch.setattr("voice_typer.server.config._CONFIG_LOCK_TIMEOUT_SECONDS",
# …)`` callers (see ``tests/test_config_save_lock.py``) keep working —
# :func:`_acquire_config_lock` looks the constant up via the
# ``voice_typer.server.config`` module attribute (lazy import) at
# call time so the monkeypatched value is honoured.
_CONFIG_LOCK_TIMEOUT_SECONDS = 5


def _get_config_dir() -> Path:
    """Look up ``_config_dir`` via the ``voice_typer.server.config`` module
    attribute so test monkeypatches on
    ``voice_typer.server.config._config_dir`` propagate to paths.py
    callers (e.g. :func:`_acquire_config_lock`, :func:`_validate_import_path`,
    :func:`_migrate_from_legacy`).

    In production ``config._config_dir is paths._config_dir`` (the same
    function object, re-exported via ``from ... import``), so this call
    resolves to the cached :func:`_config_dir`.  Under test, the patched
    lambda is invoked instead.

    Lazy import avoids a circular module-load (``config.py`` imports this
    module at the top of the file, before ``config`` itself is fully
    loaded).
    """
    from voice_typer.server import config as _cfg

    return _cfg._config_dir()


def _is_windows() -> bool:
    """Look up ``is_windows`` via the ``voice_typer.server.config`` module
    attribute so test monkeypatches on ``voice_typer.server.config.is_windows``
    (e.g. ``tests/test_validate_systemroot.py``,
    ``tests/regressions/security_test.py``) propagate to paths.py callers.

    In production ``config.is_windows is paths.is_windows`` (the same
    function object, re-exported via ``from ... import``).  Under test,
    the patched callable is invoked instead.
    """
    from voice_typer.server import config as _cfg

    return _cfg.is_windows()


def _is_macos() -> bool:
    """Look up ``is_macos`` via the ``voice_typer.server.config`` module
    attribute so test monkeypatches on ``voice_typer.server.config.is_macos``
    propagate to paths.py callers.  See :func:`_is_windows` for rationale.
    """
    from voice_typer.server import config as _cfg

    return _cfg.is_macos()


def _get_legacy_voice_typer_dir() -> Path:
    """Look up ``_legacy_voice_typer_dir`` via the ``voice_typer.server.config``
        module attribute.

        The literal ``Path.home() / ".voice-typer"`` expression lives in
        :func:`voice_typer.server.config._legacy_voice_typer_dir` (defined in
        ``config.py``) so the ``test_config_py_still_has_legacy_migration_probe``
        regression guard — which scans ``config.py`` for that exact pattern —
    continues to pass after the  split.  This shim lets
        :func:`_config_dir` invoke that helper without a top-level ``from
        voice_typer.server.config import ...`` (which would be a circular
        import at module-load time).
    """
    from voice_typer.server import config as _cfg

    return _cfg._legacy_voice_typer_dir()


def _validate_path_safety(path: Path, parent: Path) -> Path:
    """Resolve and validate that path stays within parent directory.

        SEC-005: prevents path traversal attacks when user-supplied env vars
        (VOICE_TYPER_CONFIG_DIR, XDG_DATA_HOME, etc.) contain ``..`` sequences
        that could escape the expected parent directory.

    fix: previously used ``str(resolved).startswith(str(parent_resolved))``
        which is the classic prefix-match bug — ``/home/userX/secret`` would
        be considered "within" ``/home/user`` because the string
        ``"/home/userX/secret"`` does start with ``"/home/user"``.  Now
        delegates to :func:`_is_path_within`, which uses
        :func:`os.path.commonpath` to respect directory boundaries and
        handles cross-drive Windows paths (returns ``False`` instead of
        raising ``ValueError``).
    """
    # use the robust commonpath-based containment check rather
    # than a naive str.startswith.  _is_path_within resolve()s both
    # sides, lower-cases on Windows/macOS (case-insensitive FS), and
    # returns False (not raise) for cross-drive paths.
    if not _is_path_within(path, parent):
        raise ValueError(f"Path traversal detected: {path} escapes {parent}")
    return path.resolve()


def _is_path_within(path: Path, root: Path, *, case_sensitive: bool | None = None) -> bool:
    """whether ``path`` is ``root`` itself or a descendant of it.

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

    ``case_sensitive`` lets callers override the platform auto-
        detection.  ``None`` (default) preserves the original behaviour —
        auto-detect via ``sys.platform`` (Windows + macOS -> case-
        insensitive, everything else -> case-sensitive).  Tests pass
        ``True`` / ``False`` explicitly so they don't depend on the global
        ``sys.platform`` value (which is fragile on Linux CI runners — the
        POSIX-only Python build always reports ``"linux"`` regardless of
        whether the test is trying to exercise the Windows branch).
        Production callers pass ``None`` and get the current behaviour.
    """
    import os.path

    try:
        p_resolved = str(path.resolve())
        r_resolved = str(root.resolve())
    except (OSError, RuntimeError):
        # Path.resolve() can raise on some platforms if the path is
        # not decodable; treat that as "not within".
        return False
    if case_sensitive is None:
        case_sensitive = sys.platform not in ("win32", "darwin")
    if not case_sensitive:
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
    """validate that ``dir_path`` is within an allowed root.

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
    import os
    import tempfile

    resolved = Path(dir_path).resolve()
    allowed_roots = [
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        (_get_config_dir() / "huggingface" / "hub").resolve(),
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


def _validate_systemroot() -> None:
    """SEC-audit-011: Validate the SystemRoot environment variable on Windows.

        The ``SystemRoot`` env var (e.g. ``C:\\Windows``) is used by Python's
        ``os.path`` module and various Win32 APIs to locate system DLLs.  An
        attacker who can set this variable before our process starts could
        redirect DLL lookups to a malicious directory.  This function verifies
        that ``SystemRoot`` points to an existing directory on Windows and
        rejects values that contain path traversal sequences or unusual
        characters.

        On non-Windows platforms, this is a no-op.

    fix — fail-closed vs reset-to-default decisions:
          - Path traversal (``..``)             → ``sys.exit(1)`` (security issue)
          - Unusual characters (``<>|"&'\\n\\r\\t``) → ``sys.exit(1)`` (security issue)
          - Missing directory                   → reset to ``C:\\Windows`` + continue (usability)
          - Missing ``System32\\notepad.exe``   → log warning + continue (not a hard blocker)

        Rationale: a malicious ``SystemRoot`` is a DLL-hijacking vector that
        could lead to arbitrary code execution with the user's privileges —
        better to refuse to start than to silently reset and continue.  A
        missing directory, on the other hand, is typically a misconfigured
        environment (e.g. a stripped-down Windows image) where the user can
        still benefit from the app starting with the default ``SystemRoot``.
    """
    # Look up ``Path`` via the config module attribute so test
    # monkeypatches on ``voice_typer.server.config.Path`` (see
    # ``tests/test_validate_systemroot.py`` and
    # ``tests/regressions/security_test.py``) propagate to this function
    # after the  split.  The local shadow is intentional.  Lazy
    # import avoids a circular module-load.
    from voice_typer.server import config as _cfg

    Path = _cfg.Path  # noqa: PLW0642,N806 — intentional local shadow of module-level Path

    if not _is_windows():
        return

    systemroot = os.environ.get("SYSTEMROOT", "")
    if not systemroot:
        # SystemRoot not set — unusual but not a direct attack vector
        # for our process.  Windows APIs may fail later; we just log.
        log.warning("[CONFIG] SystemRoot environment variable is not set")
        return

    # Check for path traversal — fail-closed (security issue).
    # A malicious SystemRoot pointing at an attacker-controlled directory
    # with ``..`` segments is a classic DLL-injection vector.  Refusing
    # to start is safer than silently resetting (the user would have no
    # indication that their SystemRoot was being tampered with).
    # H-11 (IMPROVE-2026-07-19): the previous check used naive substring
    # matching (`".." in systemroot`) which produced false positives on
    # legitimate Windows paths like `C:\Win..dows` or `C:\Windows\file..exe`.
    # fixed this by switching to `PureWindowsPath(systemroot).parts`
    # component check — only an actual `..` path SEGMENT is rejected, not
    # a `..` substring inside a directory/file name. Restoring that fix
    # (3 regression tests in tests/test_validate_systemroot.py now pass).
    if ".." in PureWindowsPath(systemroot).parts:
        log.error(
            "[CONFIG] SystemRoot contains path traversal ('..'): %s — "
            "possible DLL injection attack. ABORTING STARTUP (fail-closed).",
            systemroot,
        )
        sys.exit(1)

    # Check for unusual characters that could indicate tampering —
    # fail-closed (same rationale as the path-traversal branch above).
    import re

    if re.search(r'[<>|"&\'\n\r\t]', systemroot):
        log.error(
            "[CONFIG] SystemRoot contains unusual characters: %r — possible "
            "injection attack. ABORTING STARTUP (fail-closed).",
            systemroot,
        )
        sys.exit(1)

    # Verify the directory exists — reset to default + continue
    # (usability issue, not a direct security issue).  A user's
    # SystemRoot may be set to a path that no longer exists (e.g. they
    # moved their Windows installation) — refusing to start would lock
    # them out of the app entirely.  Resetting to the canonical default
    # lets the app start with a valid SystemRoot.
    if not Path(systemroot).is_dir():
        default = r"C:\Windows"
        # The reset is conditional on the default ``C:\Windows`` actually
        # existing as a directory. Previously the guard was dropped
        # ("Always set the default") with the rationale that
        # ``Path(default).is_dir()`` is a no-op on the Linux CI runner —
        # but that broke the fail-soft contract: when the default is ALSO
        # missing (e.g. a stripped-down Wine prefix, a broken OS install,
        # or the Linux sandbox) the function should NOT overwrite the
        # user-supplied value with a path that is just as broken. Leaving
        # the original value in place lets downstream Win32 APIs emit
        # their own diagnostics instead of pointing at a path the
        # runtime already knows is invalid.
        if Path(default).is_dir():
            log.warning(
                "[CONFIG] SystemRoot does not point to an existing directory: %s — "
                "resetting to default C:\\Windows (usability fallback).",
                systemroot,
            )
            os.environ["SYSTEMROOT"] = default
        else:
            log.warning(
                "[CONFIG] SystemRoot does not point to an existing directory: %s "
                "and the default C:\\Windows is also not present — leaving "
                "SystemRoot as-is so downstream Win32 APIs emit their own "
                "diagnostics (usability fallback).",
                systemroot,
            )
        return

    # SEC-audit-011: Verify SystemRoot contains System32\notepad.exe.
    # This is the canonical sanity check — every valid Windows
    # installation has notepad.exe in System32.  If it's missing, the
    # SystemRoot value is almost certainly invalid or tampered.
    #
    # Not a hard blocker — log warning + continue.  The caller is
    # expected to use a hardcoded fallback path for notepad specifically
    # (see ``system_handlers.py``).  Do NOT reset SystemRoot itself —
    # other system DLLs may still be valid even if notepad is missing.
    notepad_path = Path(systemroot) / "System32" / "notepad.exe"
    if not notepad_path.exists():
        log.warning(
            "[CONFIG] SystemRoot does not contain System32\\notepad.exe: %s — "
            "caller should use hardcoded fallback for notepad.",
            systemroot,
        )


@functools.lru_cache(maxsize=1)
def _config_dir() -> Path:
    """Get the voice-typer data directory.

    honors VOICE_TYPER_CONFIG_DIR env var.
    uses platform-appropriate paths instead of always
        ``~/.voice-typer``.  On Windows this is ``%APPDATA%/voice-typer``,
        on macOS ``~/Library/Application Support/voice-typer``, on Linux
        ``$XDG_DATA_HOME/voice-typer`` (falling back to
        ``~/.local/share/voice-typer``).  The legacy ``~/.voice-typer`` is
        still checked first for migration — existing users' data is
        automatically found and used.

        SEC-005: user-supplied env vars are validated for path traversal.

    the result is memoized for the process lifetime via
        :func:`functools.lru_cache`.  The function is deterministic w.r.t.
        ``os.environ`` + :func:`Path.home` + the existence of the legacy
        ``~/.voice-typer`` directory, all of which are stable for the
        process lifetime in production.  Caching eliminates the 30-50
        ``stat()`` syscalls (``Path.resolve`` + ``Path.exists``) that
        ``_validate_path_safety`` previously issued on each of the ~29
        call sites at startup and 3+ per :meth:`Config.save`.

        Tests that need to force re-resolution (e.g. after monkeypatching
        ``os.environ`` or :func:`Path.home`) should call
        :func:`_reset_config_dir_cache` — mirrors
        :func:`voice_typer.server.credential_store._reset_keyring_cache`.
    """
    custom = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if custom:
        custom_path = Path(custom)
        # SEC-005: validate that custom path doesn't traverse above home
        try:
            _validate_path_safety(custom_path, Path.home())
        except ValueError:
            log.warning("[CONFIG] VOICE_TYPER_CONFIG_DIR path traversal detected: %s", custom)
            # Fall through to default paths
        else:
            return custom_path

    # check for legacy ~/.voice-typer first (migration
    # path — existing users keep their data where it is).  The literal
    # ``Path.home() / ".voice-typer"`` lives in
    # :func:`voice_typer.server.config._legacy_voice_typer_dir` (kept
    # there so the ``test_config_py_still_has_legacy_migration_probe``
    # regression guard continues to pass after the  split); this
    # call goes through the config module via a lazy-import shim.
    legacy = _get_legacy_voice_typer_dir()
    if legacy.exists():
        return legacy

    # Platform-specific paths for new installations.
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_path = Path(appdata) / "voice-typer"
            # SEC-005: validate APPDATA-derived path
            try:
                _validate_path_safety(appdata_path, Path.home())
            except ValueError:
                log.warning("[CONFIG] APPDATA path traversal detected: %s", appdata)
            else:
                return appdata_path
    elif _is_macos():
        return Path.home() / "Library" / "Application Support" / "voice-typer"
    else:
        # Linux / FreeBSD: honor XDG_DATA_HOME.
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            xdg_path = Path(xdg) / "voice-typer"
            # SEC-005: validate XDG_DATA_HOME-derived path
            try:
                _validate_path_safety(xdg_path, Path.home())
            except ValueError:
                log.warning("[CONFIG] XDG_DATA_HOME path traversal detected: %s", xdg)
            else:
                return xdg_path
        return Path.home() / ".local" / "share" / "voice-typer"

    # Fallback for any platform where the above checks didn't return.
    return legacy


def _reset_config_dir_cache() -> None:
    """Test-only: clear the cached :func:`_config_dir` result.

    func:`_config_dir` is memoized via
        :func:`functools.lru_cache` so the filesystem/env probes it
        performs (``Path.resolve``, ``Path.exists``, ``os.path.commonpath``)
        run at most once per process.  Tests that change the inputs —
        ``VOICE_TYPER_CONFIG_DIR``, ``XDG_DATA_HOME``, ``APPDATA``,
        :func:`Path.home`, or the existence of the legacy
        ``~/.voice-typer`` directory — must call this helper to force
        re-resolution on the next :func:`_config_dir` invocation.

        Mirrors :func:`voice_typer.server.credential_store._reset_keyring_cache`
    so the two caches share the same reset convention ().
    """
    _config_dir.cache_clear()


def _find_symlink_in_tree(root):
    """return the path of the first symlink found under ``root``,
    or ``None`` if there are none.

    Mirrors :func:`voice_typer.server.service._helpers._find_symlink_in_tree`
    so the migration path uses the same poison-dir detection logic the
    ``import_model`` IPC handler relies on.  Inlined here (rather than
    imported) to avoid a circular dependency: ``service._helpers`` is a
    leaf module that imports from ``voice_typer.server.config``, and
    ``config`` imports this module (via ``config_internals.paths``) —
    so importing ``service._helpers`` from here would close a cycle.

    ``os.walk`` with the default ``followlinks=False`` does NOT descend
    into symlinked directories, but it DOES include them in
    ``dirnames`` — so both symlinked files and symlinked directories
    are detected by this check.
    """
    import os as _os

    for dirpath, dirnames, filenames in _os.walk(root):
        for name in list(dirnames) + list(filenames):
            full = _os.path.join(dirpath, name)
            if _os.path.islink(full):
                return full
    return None


def _migrate_from_legacy():
    """One-time migration from old platform-specific location (e.g. %APPDATA%).

    before ``shutil.copytree`` runs, scan the legacy tree for
        symlinks.  If any symlink is found (e.g. ``legacy/models/qwen`` ->
        ``~/.ssh/id_rsa`` planted by an attacker who got write access to
        the legacy dir), abort the migration with a WARNING and leave the
        legacy dir in place — copytree with the default ``symlinks=True``
        would have followed the link and copied arbitrary attacker-chosen
        content into the new config dir.  Mirrors the poison-dir rejection
        in :meth:`VoiceTyperService.import_model` via
        :func:`voice_typer.server.service._helpers._find_symlink_in_tree`.
    """
    if _is_windows():
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif _is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / "voice-typer"
    if not legacy.exists() or legacy.resolve() == _get_config_dir().resolve():
        return
    target = _get_config_dir()
    if target.exists():
        return
    import shutil

    # refuse to migrate a poisoned legacy tree.
    symlink = _find_symlink_in_tree(legacy)
    if symlink is not None:
        log.warning(
            "[CONFIG] refusing to migrate legacy config %s -> %s — "
            "symlink detected at %s (symlinks are not allowed in the "
            "config dir; leaving legacy dir in place for manual review)",
            legacy,
            target,
            symlink,
        )
        return

    shutil.copytree(legacy, target, dirs_exist_ok=True)
    log.info("[CONFIG] Migrated data from %s to %s", legacy, target)


@contextlib.contextmanager
def _acquire_config_lock(timeout: float | None = None):
    """acquire an exclusive cross-process lock on config.json.lock.

        Mirrors credential_store._acquire_migration_lock.  POSIX uses
        fcntl.flock(LOCK_EX) polled with LOCK_NB to enforce the timeout.
        Windows uses msvcrt.locking(LK_NBLCK) polled in a self-paced retry
    loop (: the previous ``LK_LOCK`` call blocked for ~10s
        internally, ignoring the caller's 5s deadline).  On timeout, raises
        TimeoutError (caught by Config.save() which returns False).

    a failure to even CREATE the lock file (e.g. read-only
        config dir, ENOSPC, ENOENT for a deleted parent) is now fatal —
        the previous "yield without lock" fallback silently raced against
    concurrent writers, which is exactly the corruption  was
        added to prevent.  We now raise ``TimeoutError`` so the caller
        aborts the save; the log level is elevated from DEBUG to WARNING
        so operators notice.
    """
    import os as _os

    if timeout is None:
        # Look up via the ``voice_typer.server.config`` module attribute
        # rather than reading ``_CONFIG_LOCK_TIMEOUT_SECONDS`` from this
        # module's globals.  ``tests/test_config_save_lock.py`` patches
        # ``voice_typer.server.config._CONFIG_LOCK_TIMEOUT_SECONDS`` to
        # shorten the timeout; reading from the config module ensures
        # the monkeypatched value is honoured.  The lazy import avoids a
        # circular module-load (config.py imports this module at the
        # top of the file, before ``config`` itself is fully loaded).
        from voice_typer.server import config as _cfg_module

        timeout = _cfg_module._CONFIG_LOCK_TIMEOUT_SECONDS

    lock_file = _get_config_dir() / "config.json.lock"
    with contextlib.suppress(OSError):
        _get_config_dir().mkdir(parents=True, exist_ok=True)

    if not _is_windows():
        import errno
        import fcntl

        try:
            fd = _os.open(str(lock_file), _os.O_CREAT | _os.O_RDWR, 0o600)
        except OSError as e:
            # refuse to proceed without the lock.  The previous
            # "yield without lock" fallback silently raced concurrent
            # writers; we now raise so the caller aborts the save.
            log.warning(
                "[CONFIG] could not create lock file %s (%s) -- aborting "
                "save (refusing to proceed without the cross-process lock)",
                lock_file,
                e,
            )
            raise TimeoutError(
                f"Config.save() could not create config.json.lock ({e}) -- "
                f"aborting save to prevent concurrent-write corruption."
            ) from e
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        if time.monotonic() >= deadline:
                            _os.close(fd)
                            raise TimeoutError(
                                f"Config.save() could not acquire config.json.lock "
                                f"within {timeout}s -- another process is holding the lock."
                            ) from e
                        time.sleep(0.05)
                        continue
                    # any other flock failure (e.g. EBADF) is
                    # also fatal — proceeding without the lock would
                    # race concurrent writers.
                    log.warning(
                        "[CONFIG] flock on %s failed (%s) -- aborting save "
                        "(refusing to proceed without the cross-process lock)",
                        lock_file,
                        e,
                    )
                    _os.close(fd)
                    raise TimeoutError(
                        f"Config.save() could not lock config.json.lock ({e}) -- "
                        f"aborting save to prevent concurrent-write corruption."
                    ) from e
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                _os.close(fd)
        except TimeoutError:
            raise
        except Exception:
            with contextlib.suppress(OSError):
                _os.close(fd)
            raise
    else:
        import msvcrt

        try:
            fd = _os.open(str(lock_file), _os.O_CREAT | _os.O_RDWR)
        except OSError as e:
            # (Windows branch): same fatal-on-create contract as
            # the POSIX branch above.  Yielding without the lock would
            # race concurrent writers on the same machine.
            log.warning(
                "[CONFIG] could not create lock file %s (%s) -- aborting "
                "save (refusing to proceed without the cross-process lock)",
                lock_file,
                e,
            )
            raise TimeoutError(
                f"Config.save() could not create config.json.lock ({e}) -- "
                f"aborting save to prevent concurrent-write corruption."
            ) from e
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    # LK_NBLCK (non-blocking) + self-paced
                    # retry loop mirrors the POSIX branch's
                    # LOCK_EX | LOCK_NB pattern.  The previous LK_LOCK
                    # call blocked for ~10s internally, ignoring the
                    # caller's 5s deadline; LK_NBLCK returns
                    # immediately so the loop honors ``deadline``
                    # exactly.
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as e:
                    if time.monotonic() >= deadline:
                        _os.close(fd)
                        raise TimeoutError(
                            f"Config.save() could not acquire config.json.lock "
                            f"within {timeout}s -- another process is holding the lock."
                        ) from e
                    time.sleep(0.05)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                _os.close(fd)
        except TimeoutError:
            raise
        except Exception:
            with contextlib.suppress(OSError):
                _os.close(fd)
            raise
