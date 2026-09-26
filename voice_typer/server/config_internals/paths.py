"""Config path resolution helpers."""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

from voice_typer.server._fs_walk import find_symlink_in_tree as _find_symlink_in_tree

# APP_SLUG is imported lazily inside the functions that need it

log = logging.getLogger("voice_typer.server.config")

# cross-process lock for Config.save().  This is the canonical
_CONFIG_LOCK_TIMEOUT_SECONDS = 5

# In-process fairness gate for :func:`_acquire_config_lock`. The
_CONFIG_SAVE_PROCESS_LOCK = threading.Lock()


def _get_config_dir() -> Path:
    """Look up ``_config_dir`` via the ``voice_typer.server.config`` module"""
    from voice_typer.server import config as _cfg

    return _cfg._config_dir()


def _is_windows() -> bool:
    """Look up ``is_windows`` via the ``voice_typer.server.config`` module"""
    from voice_typer.server import config as _cfg

    return _cfg.is_windows()


def _is_macos() -> bool:
    """Look up ``is_macos`` via the ``voice_typer.server.config`` module"""
    from voice_typer.server import config as _cfg

    return _cfg.is_macos()


def _get_legacy_voice_typer_dir() -> Path:
    """Look up ``_legacy_voice_typer_dir`` via the ``voice_typer.server.config``"""
    from voice_typer.server import config as _cfg

    return _cfg._legacy_voice_typer_dir()


def _validate_path_safety(path: Path, parent: Path) -> Path:
    """SEC-005: prevents path traversal attacks when user-supplied env vars"""
    # use the robust commonpath-based containment check rather
    if not _is_path_within(path, parent):
        raise ValueError(f"Path traversal detected: {path} escapes {parent}")
    return path.resolve()


def _is_path_within(path: Path, root: Path, *, case_sensitive: bool | None = None) -> bool:
    """whether ``path`` is ``root`` itself or a descendant of it."""
    import os.path

    if case_sensitive is None:
        from voice_typer.server.platform_utils import is_macos, is_windows

        case_sensitive = not (is_windows() or is_macos())

    try:
        if case_sensitive:
            # Lexical normalization only. ``Path.resolve()`` follows the
            p_resolved = os.path.normpath(os.path.abspath(str(path)))
            r_resolved = os.path.normpath(os.path.abspath(str(root)))
        else:
            p_resolved = os.path.normpath(str(path.resolve()))
            r_resolved = os.path.normpath(str(root.resolve()))
    except (OSError, RuntimeError):
        # Path.resolve() can raise on some platforms if the path is
        return False
    if not case_sensitive:
        p_resolved = p_resolved.lower()
        r_resolved = r_resolved.lower()
    try:
        common = os.path.commonpath([p_resolved, r_resolved])
    except ValueError:
        # commonpath raises ValueError if the paths are on different
        return False
    return common == r_resolved


def _validate_import_path(dir_path: str) -> str:
    """validate that ``dir_path`` is within an allowed root.

    Returns the resolved path as a string.  Raises ``ValueError`` if
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
    """SEC-audit-011: Validate the SystemRoot environment variable on Windows."""
    # Look up ``Path`` via the config module attribute so test
    from voice_typer.server import config as _cfg

    Path = _cfg.Path  # noqa: PLW0642,N806, intentional local shadow of module-level Path

    if not _is_windows():
        return

    systemroot = os.environ.get("SYSTEMROOT", "")
    if not systemroot:
        # SystemRoot not set, unusual but not a direct attack vector
        log.warning("[CONFIG] SystemRoot environment variable is not set")
        return

    # Check for path traversal, fail-closed (security issue).
    if ".." in PureWindowsPath(systemroot).parts:
        log.error(
            "[CONFIG] SystemRoot contains path traversal ('..'): %s, "
            "possible DLL injection attack. ABORTING STARTUP (fail-closed).",
            systemroot,
        )
        sys.exit(1)

    # Check for unusual characters that could indicate tampering —
    import re

    if re.search(r'[<>|"&\'\n\r\t]', systemroot):
        log.error(
            "[CONFIG] SystemRoot contains unusual characters: %r, possible "
            "injection attack. ABORTING STARTUP (fail-closed).",
            systemroot,
        )
        sys.exit(1)

    # Verify the directory exists, reset to default + continue
    if not Path(systemroot).is_dir():
        default = r"C:\Windows"
        # The reset is conditional on the default ``C:\Windows`` actually
        if Path(default).is_dir():
            log.warning(
                "[CONFIG] SystemRoot does not point to an existing directory: %s, "
                "resetting to default C:\\Windows (usability fallback).",
                systemroot,
            )
            os.environ["SYSTEMROOT"] = default
        else:
            log.warning(
                "[CONFIG] SystemRoot does not point to an existing directory: %s "
                "and the default C:\\Windows is also not present, leaving "
                "SystemRoot as-is so downstream Win32 APIs emit their own "
                "diagnostics (usability fallback).",
                systemroot,
            )
        return

    # SEC-audit-011: Verify SystemRoot contains System32\notepad.exe.
    notepad_path = Path(systemroot) / "System32" / "notepad.exe"
    if not notepad_path.exists():
        log.warning(
            "[CONFIG] SystemRoot does not contain System32\\notepad.exe: %s, "
            "caller should use hardcoded fallback for notepad.",
            systemroot,
        )


@functools.lru_cache(maxsize=1)
def _config_dir() -> Path:
    """SEC-005: user-supplied env vars are validated for path traversal."""
    # lazy import to avoid the circular import described at the top
    from voice_typer.server._paths import APP_SLUG

    custom = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if custom:
        custom_path = Path(custom)
        # SEC-005: validate that custom path doesn't traverse above home
        try:
            _validate_path_safety(custom_path, Path.home())
        except ValueError:
            # Redact the raw env value (matches env_validation.py). The
            log.warning("[CONFIG] VOICE_TYPER_CONFIG_DIR=<redacted> path traversal detected")
            # Fall through to default paths
        else:
            return custom_path

    # check for legacy ~/.lausu first (migration
    legacy = _get_legacy_voice_typer_dir()
    if legacy.exists():
        return legacy

    # Platform-specific paths for new installations.
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_path = Path(appdata) / APP_SLUG
            # SEC-005: validate APPDATA-derived path
            try:
                _validate_path_safety(appdata_path, Path.home())
            except ValueError:
                # Redact: APPDATA embeds the OS username (SEC / privacy).
                log.warning("[CONFIG] APPDATA=<redacted> path traversal detected")
            else:
                return appdata_path
    elif _is_macos():
        return Path.home() / "Library" / "Application Support" / APP_SLUG
    else:
        # Linux / FreeBSD: honor XDG_DATA_HOME.
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            xdg_path = Path(xdg) / APP_SLUG
            # SEC-005: validate XDG_DATA_HOME-derived path
            try:
                _validate_path_safety(xdg_path, Path.home())
            except ValueError:
                # Redact: XDG_DATA_HOME may embed the OS username.
                log.warning("[CONFIG] XDG_DATA_HOME=<redacted> path traversal detected")
            else:
                return xdg_path
        return Path.home() / ".local" / "share" / APP_SLUG

    # Fallback for any platform where the above checks didn't return.
    return legacy


def _reset_config_dir_cache() -> None:
    """Test-only: clear the cached :func:`_config_dir` result."""
    # ``_config_dir`` may currently be patched by tests (the per-test
    with contextlib.suppress(AttributeError):
        _config_dir.cache_clear()


def _migrate_from_legacy():
    """One-time migration from old platform-specific location (e.g. %APPDATA%)."""
    # lazy import to avoid the circular import described at the top
    from voice_typer.server._paths import APP_SLUG

    if _is_windows():
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif _is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / APP_SLUG
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
            "[CONFIG] refusing to migrate legacy config %s -> %s, "
            "symlink detected at %s (symlinks are not allowed in the "
            "config dir; leaving legacy dir in place for manual review)",
            legacy,
            target,
            symlink,
        )
        return

    # The migration must be ATOMIC.  A direct
    staging = target.parent / (target.name + f".migrate-tmp-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    migrated = False
    try:
        shutil.copytree(legacy, staging)
        try:
            os.replace(staging, target)
            migrated = True
        except OSError as e:
            # A concurrent migration (another process racing the same
            if target.exists():
                log.warning(
                    "[CONFIG] config dir %s appeared during atomic migration "
                    "(%s), keeping the concurrently-created target",
                    target,
                    e,
                )
            else:
                raise
    finally:
        # drop our staging dir (and any stale siblings from dead processes)
        for candidate in target.parent.glob(target.name + ".migrate-tmp-*"):
            with contextlib.suppress(OSError):
                shutil.rmtree(candidate, ignore_errors=True)
    if migrated:
        log.info("[CONFIG] Migrated data from %s to %s", legacy, target)


@contextlib.contextmanager
def _acquire_config_lock(timeout: float | None = None):
    """Acquire the config.json save lock (in-process fair queue → OS lock)."""
    from voice_typer.server import config as _cfg_module

    effective = timeout if timeout is not None else _cfg_module._CONFIG_LOCK_TIMEOUT_SECONDS
    deadline = time.monotonic() + effective
    if not _CONFIG_SAVE_PROCESS_LOCK.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError(
            f"Config.save() could not acquire the in-process config save "
            f"lock within {effective}s -- aborting save to prevent "
            f"concurrent-write corruption."
        )
    try:
        # Hand the REMAINING budget to the cross-process lock so the
        remaining = max(0.05, deadline - time.monotonic())
        with _acquire_config_lock_cross_process(timeout=remaining):
            yield
    finally:
        _CONFIG_SAVE_PROCESS_LOCK.release()


@contextlib.contextmanager
def _acquire_config_lock_cross_process(timeout: float):
    """acquire an exclusive cross-process lock on config.json.lock."""
    import os as _os

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
