"""Path resolution helpers for config/cache dirs."""

from __future__ import annotations

import sys  # noqa: F401  # kept for test patches (tests monkeypatch `_paths.sys.platform`)
from collections.abc import Callable
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

# These constants are the single source of truth for values that were
LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
LOOPBACK_HOST: str = "127.0.0.1"
DEFAULT_LLM_API_URL: str = "https://api.openai.com/v1/chat/completions"
DEFAULT_LLM_MODEL: str = "gpt-4o-mini"

# Machine-readable application slug used for config-dir names, keyring
APP_SLUG: str = "lausu"

# Machine-readable product identity: the reverse-DNS root that names every
# OS-visible artifact (HKCU Run key, scheduled-task URI, Startup .bat,
# polkit action namespace, macOS LaunchAgents, credential-store service).
# Deliberately INDEPENDENT of ``branding.APP_NAME``: renaming the display
# brand must never silently rename the entries an existing install left
# behind, or the legacy sweeps stop recognizing them (AGENTS.md C-BRAND-1
# exempts internal OS/API identifiers from the branding constant).
APP_RDNN_ROOT: str = "com.Lausu"

# PascalCase token inside the OS-visible names (``Lausu_<hash>`` Run key,
# ``LausuAutostart<hash>`` task, ``Lausu*.bat``), derived so the two
# identity tokens can never drift apart.
APP_IDENTIFIER: str = APP_RDNN_ROOT.rsplit(".", 1)[-1]

# duplicated in ``autostart_launcher.py`` (``IPC_PORT = 9876``),
IPC_PORT: int = 9876

# Canonical name of the env var that carries the per-launch
IPC_TOKEN_ENV_VAR: str = "VOICE_TYPER_IPC_TOKEN"


# Lazy resolver for :func:`voice_typer.server.config._config_dir`.
class _ConfigDirResolver:
    """Identity-stable holder for the lazy ``_config_dir`` callable."""

    __slots__ = ("_cached", "_override")

    def __init__(self) -> None:
        self._cached: Callable[[], Path] | None = None
        self._override: Callable[[], Path] | None = None

    def __call__(self) -> Path:
        if self._override is not None:
            return self._override()
        if self._cached is None:
            # Imported lazily to keep ``_paths`` cold-import cheap. The
            from voice_typer.server.config import _config_dir as _real_config_dir

            self._cached = _real_config_dir
        return self._cached()

    def override(self, fn: Callable[[], Path] | None) -> None:
        """Pin (or clear) an explicit resolver, used by tests."""
        self._override = fn


_config_dir = _ConfigDirResolver()


def _resolve_config_dir() -> Callable[[], Path]:
    """Return the cached ``_config_dir`` callable, importing on first use."""
    resolver = _config_dir
    if isinstance(resolver, _ConfigDirResolver):
        # An explicit test override short-circuits the lazy import
        if resolver._override is not None:
            return resolver._override
        # Touch the cache through the holder so ``_cached`` is populated
        resolver()
        cached = resolver._cached
        if cached is not None:
            return cached
        # Defensive: the holder guarantees ``_cached`` after ``__call__``.
        raise RuntimeError("config-dir resolver did not initialize")
    # A test fixture rebound the module attribute to a plain callable
    return resolver


def config_dir() -> Path:
    """The canonical lausu data directory."""
    return _resolve_config_dir()()


# O3: transient runtime state (pid files, lockfiles, session markers)
RUN_SUBDIR = "run"


def run_dir() -> Path:
    """Path to the ``run/`` subdir holding transient runtime state (O3)."""
    return config_dir() / RUN_SUBDIR


def prewarm_launchagent_log() -> Path:
    """Path to the legacy macOS LaunchAgent's prewarm log file."""
    return _resolve_config_dir()() / "prewarm-launchagent.log"


def autostart_log() -> Path:
    """Path to the macOS LaunchAgent's autostart log file."""
    return _resolve_config_dir()() / "autostart.log"


def venv_pythonw() -> Path:
    """Path to the venv's pythonw.exe (Windows) or python (Unix)."""
    if is_windows():
        # ``_resolve_config_dir()()`` returns ``Any`` (lazy resolver);
        return Path(_resolve_config_dir()()) / "venv" / "Scripts" / "pythonw.exe"
    return Path(_resolve_config_dir()()) / "venv" / "bin" / "python"


def legacy_hf_cache_dir() -> Path:
    """Used as a defensive last-resort fallback in"""
    return Path.home() / ".lausu" / "huggingface"


def hf_cache_dir() -> Path:
    """See :func:`legacy_hf_cache_dir` for the defensive fallback used"""
    return _resolve_config_dir()() / "huggingface"


def user_data_dir() -> Path:
    """Path to the canonical user data directory (root of all user data)."""
    return _resolve_config_dir()()
