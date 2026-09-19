"""Tauri binary discovery, integrity verification and spawn.

The Tauri path is the production launcher shape post-cutover: locate
the native ``voice-typer-tauri`` binary at a well-known install path
(or the ``VT_TAURI_BINARY`` env override), verify it against
``tauri-binaries.json`` (fail closed), then spawn it directly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

from voice_typer.server.autostart._spawn import _spawn_login_child
from voice_typer.server.autostart._spawn_env import (
    _launcher_child_env,
    _spawn_flags,
)
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_macos, is_windows

# C-CROSS-3: explicit dotted logger name: see log_files.py for why
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _client_dir_exists() -> bool:
    """Return True if the client directory (with package.json) exists.

    Used as a dev-checkout detection heuristic by ``_is_tauri_mode``.
    """
    from pathlib import Path

    client_dir = Path(__file__).resolve().parent.parent.parent.parent / "voice_typer" / "client"
    return client_dir.is_dir() and (client_dir / "package.json").exists()


# Well-known install paths per OS, in DISCOVERY ORDER. Tokens:
_TAURI_LAUNCHER_INSTALL_PATHS: dict[str, tuple[str, ...]] = {
    "windows": (
        r"%LOCALAPPDATA%\Programs\{APP}\voice-typer-tauri.exe",
        r"%PROGRAMFILES%\{APP}\voice-typer-tauri.exe",
    ),
    "macos": (
        "/Applications/{APP}.app/Contents/MacOS/voice-typer-tauri",
        "{HOME}/Applications/{APP}.app/Contents/MacOS/voice-typer-tauri",
    ),
    "linux": (
        "/usr/bin/voice-typer-tauri",
        "/usr/local/bin/voice-typer-tauri",
        "{HOME}/.local/bin/voice-typer-tauri",
    ),
}


def _expand_tauri_install_template(template: str) -> Path | None:
    """Expand one :data:`_TAURI_LAUNCHER_INSTALL_PATHS` template into a Path.

    Returns ``None`` when a required env var is unavailable, that
    candidate is then skipped (mirrors the pre-refactor behavior where
    a missing ``LOCALAPPDATA`` simply didn't contribute a candidate).
    """
    if "%LOCALAPPDATA%" in template:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if not local_appdata:
            return None
        template = template.replace("%LOCALAPPDATA%", local_appdata)
    elif "%PROGRAMFILES%" in template:
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        template = template.replace("%PROGRAMFILES%", program_files)
    elif "{HOME}" in template:
        template = template.replace("{HOME}", str(Path.home()))
    return Path(template.replace("{APP}", APP_NAME))


def _tauri_binary() -> str | None:
    """Return the path to the installed Voice Typer Tauri binary, or ``None``.

    The Tauri cutover ships a native binary (built from
    ``src-tauri/Cargo.toml`` → ``voice-typer-tauri``). This helper
    locates that binary so the autostart launcher can spawn it directly
    at login; without it, autostart-at-login would silently break.

    Lookup order:

    1. ``VT_TAURI_BINARY`` env var, explicit override used by
       installers / users that place the binary at a non-standard path.
    2. Well-known install paths per OS, in the order listed in
       :data:`_TAURI_LAUNCHER_INSTALL_PATHS` (which is pinned to the
       ``_install_paths`` of ``tauri-binaries.json`` by the drift test
       ``tests/tauri/test_config_script_drift.py``: the manifest is
       the single source of truth for both the path set and the
       discovery priority).

    On POSIX the candidate must additionally be executable
    (``os.access(..., X_OK)``), a stale non-executable file at one of
    these paths shouldn't fool us into thinking Tauri is installed.

    Returns ``None`` in dev checkouts and CI environments where the
    Tauri binary hasn't been installed system-wide; the launcher then
    falls back to the legacy predecessor path.
    """
    env_path = os.environ.get("VT_TAURI_BINARY")
    if env_path and Path(env_path).is_file():
        log.debug("[AUTOSTART] _tauri_binary: using VT_TAURI_BINARY env override: %s", env_path)
        return env_path

    candidates: list[Path] = []
    if is_windows():
        platform_key = "windows"
    elif is_macos():
        platform_key = "macos"
    else:  # Linux / other POSIX
        platform_key = "linux"
    for template in _TAURI_LAUNCHER_INSTALL_PATHS[platform_key]:
        cand = _expand_tauri_install_template(template)
        if cand is not None:
            candidates.append(cand)

    for cand in candidates:
        if not cand.is_file():
            continue
        if not is_windows() and not os.access(cand, os.X_OK):
            continue
        log.debug("[AUTOSTART] _tauri_binary: resolved Tauri binary at install path: %s", cand)
        return str(cand)
    log.debug("[AUTOSTART] _tauri_binary: no Tauri binary found at any install path (dev/CI mode)")
    return None


def _is_tauri_mode() -> bool:
    """Return ``True`` if the launcher should spawn the Tauri binary.

    Tauri mode is active when ANY of the following holds:

    - ``VOICE_TYPER_TAURI=1`` (or the legacy alias ``VT_TAURI_AUTOSTART=1``)
      is set in the env (explicit opt-in by the Tauri Rust host before
      spawning the Python sidecar, or by the autostart registration
      when registering the launcher entry under a Tauri install), OR
    - the basename of ``sys.executable`` contains ``voice-typer-tauri``
      (we are already running inside the Tauri sidecar process), OR
    - a Tauri binary is found at a known install path.
    """
    from voice_typer.server import autostart_launcher as _pkg

    if os.environ.get("VOICE_TYPER_TAURI") == "1":
        return True
    if os.environ.get("VT_TAURI_AUTOSTART") == "1":
        return True
    # also detect Tauri mode from sys.executable basename —
    exe_basename = os.path.basename(sys.executable).lower()
    if "voice-typer-tauri" in exe_basename:
        return True
    # Tauri binary exists → Tauri mode.
    return _pkg._tauri_binary() is not None


def _tauri_manifest_path() -> Path | None:
    """Locate ``tauri-binaries.json`` for installed/frozen layouts.

    The manifest is written by
    ``scripts/build/update_tauri_manifests.py`` during CI and read
    back at runtime. The launcher looks in order (first hit wins):

    1. An explicit ``VT_TAURI_MANIFEST`` env override (used by
       installers that place the manifest at a non-standard path, and
       by tests).
    2. Exe-adjacent candidates for frozen/installed layouts (the
       ``__file__``-relative repo root is garbage inside a Nuitka
       onefile temp extraction, so the frozen sidecar dir is checked
       first): ``Path(sys.executable).parent``,
       ``<exe-dir>/resources/``, then the parent chain up 3 levels.
    3. ``Path(__file__)`` parents chain (dev checkout: the canonical
       committed ``<repo-root>/tauri-binaries.json``, mirrors
       ``tests/test_tauri_binaries_manifest.py``).
    4. ``~/.voice-typer/tauri-binaries.json`` installed copy, if ever
       shipped.

    Returns ``None`` only after all miss; the caller
    (``verify_tauri_binary_or_skip``) then fails closed.
    """
    override = os.environ.get("VT_TAURI_MANIFEST")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        log.warning("[AUTOSTART] VT_TAURI_MANIFEST set but not a file: %s", override)
    # (2) Exe-adjacent: frozen sidecar dir + resources subdir + parent
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except (OSError, RuntimeError):
        exe_dir = None
    if exe_dir is not None:
        for candidate in (
            exe_dir / "tauri-binaries.json",
            exe_dir / "resources" / "tauri-binaries.json",
        ):
            try:
                if candidate.is_file():
                    log.debug("[AUTOSTART] _tauri_manifest_path: found exe-adjacent manifest: %s", candidate)
                    return candidate
            except OSError:
                continue
        try:
            exe_parents = list(exe_dir.parents)[:3]
        except (OSError, RuntimeError):
            exe_parents = []
        for parent in exe_parents:
            candidate = parent / "tauri-binaries.json"
            try:
                if candidate.is_file():
                    log.debug("[AUTOSTART] _tauri_manifest_path: found exe-parent manifest: %s", candidate)
                    return candidate
            except OSError:
                continue
    # (3) Dev checkout: walk the __file__ parents chain (superset of
    try:
        file_parents = Path(__file__).resolve().parents
    except (OSError, RuntimeError):
        file_parents = ()
    for parent in file_parents:
        candidate = parent / "tauri-binaries.json"
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    # (4) Installed copy in the user config dir, if ever shipped.
    try:
        home_candidate = Path.home() / ".voice-typer" / "tauri-binaries.json"
        if home_candidate.is_file():
            log.debug("[AUTOSTART] _tauri_manifest_path: found config-dir manifest: %s", home_candidate)
            return home_candidate
    except (OSError, RuntimeError):
        pass
    return None


def _tauri_manifest_key() -> str:
    """Compute the per-(platform, arch) manifest key for this machine.

    Mirrors the contract documented in ``tauri-binaries.json``
    ``_manifest_loader_contract``: ``<platform>-<arch>`` where
    platform is ``platform.system().lower()`` (with ``darwin``
    collapsed to ``macos``) and arch is ``platform.machine().lower()``
    (with ``amd64`` normalized to ``x86_64``); macOS uses the single
    key ``macos`` (universal binary).
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return "macos"
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        arch = machine
    return f"{system}-{arch}"


def verify_tauri_binary_or_skip(path: str | Path) -> bool:
    """Verify the Tauri host binary against ``tauri-binaries.json``.

    Implements the ``_manifest_loader_contract`` documented in
    ``tauri-binaries.json`` (fail-closed semantics, mirroring
    ``voice_typer/server/native_hotkeys/binary_path.py::verify_native_binary_or_skip``):

    - If the manifest cannot be located → FAIL CLOSED (return False).
    - If the binary name has no manifest entry → FAIL CLOSED.
    - If the per-(platform, arch) sha256 sub-key is missing or empty
      → FAIL CLOSED (production builds MUST populate every sub-key
      via ``scripts/build/update_tauri_manifests.py``; an empty hash
      means the binary was not built by the release pipeline and must
      not be trusted).
    - Otherwise hash the binary with SHA-256 and compare
      (``hmac.compare_digest``); on mismatch → FAIL CLOSED.

    The ``VT_TAURI_BINARY`` env override (``_tauri_binary``) is NOT a
    bypass: this helper is called with the resolved path regardless of
    where it came from, so an attacker cannot use the env var to
    sidestep the integrity gate.
    """
    binary = Path(path)
    manifest_path = _tauri_manifest_path()
    if manifest_path is None:
        log.error(
            "[AUTOSTART] FAIL CLOSED: tauri-binaries.json not found; "
            "refusing to spawn %s. Run scripts/build/update_tauri_manifests.py "
            "to generate the manifest.",
            binary,
        )
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception("[AUTOSTART] FAIL CLOSED: cannot read manifest %s", manifest_path)
        return False
    entry = (data.get("binaries") or {}).get(binary.name)
    if not isinstance(entry, dict):
        log.error(
            "[AUTOSTART] FAIL CLOSED: no manifest entry for %s in %s.",
            binary.name,
            manifest_path,
        )
        return False
    sha256_dict = entry.get("sha256")
    if not isinstance(sha256_dict, dict):
        log.error(
            "[AUTOSTART] FAIL CLOSED: manifest entry %s has no per-arch sha256 dict.",
            binary.name,
        )
        return False
    key = _tauri_manifest_key()
    expected = sha256_dict.get(key)
    if not expected:
        log.error(
            "[AUTOSTART] FAIL CLOSED: no sha256 for %s/%s (manifest entry %s) "
            "— binary not built by the release pipeline. Run "
            "scripts/build/update_tauri_manifests.py to populate it.",
            key,
            binary.name,
            manifest_path,
        )
        return False
    try:
        # Chunked read (8 MiB): the host binary is tens of MB, a
        digest = hashlib.sha256()
        with open(binary, "rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
    except OSError:
        log.exception("[AUTOSTART] FAIL CLOSED: cannot hash %s", binary)
        return False
    if not hmac.compare_digest(actual, expected):
        log.error(
            "[AUTOSTART] FAIL CLOSED: SHA-256 mismatch for %s (expected %s, "
            "got %s), binary tampered or stale; refusing to spawn.",
            binary,
            expected,
            actual,
        )
        return False
    log.debug("[AUTOSTART] Tauri binary %s verified against %s", binary, manifest_path)
    return True


def _spawn_tauri_host(binary: str, hidden: bool = False) -> subprocess.Popen | None:
    """Spawn the Tauri host binary (``voice-typer-tauri``) with ``VT_START_HIDDEN`` if *hidden*.

        The Tauri app's ``tauri-plugin-single-instance`` plugin (declared
        in ``src-tauri/tauri.conf.json``) handles the focus / fresh-start
        distinction itself: a second spawn of the same binary causes the
        first instance to be focused and the second to exit. So we always
        spawn the full Tauri binary, the single-instance plugin does the rest.

        Returns the child process on success, or ``None`` on failure (the
    caller logs and exits 1, no silent predecessor fallback).
    """
    from voice_typer.server import autostart_launcher as _pkg

    # Fail-closed integrity gate: the Tauri host binary MUST
    if not _pkg.verify_tauri_binary_or_skip(binary):
        log.error(
            "[AUTOSTART] refusing to spawn Tauri binary %s, integrity verification failed (fail-closed).",
            binary,
        )
        return None
    # ``_launcher_child_env`` force-disables ANSI colour + npm notices
    env = _launcher_child_env()
    if hidden:
        env["VT_START_HIDDEN"] = "1"
    # The [ENV] audit line is emitted once by ``_spawn_login_child``
    sk: dict = {}
    sk.update(_pkg._tauri_log_files())
    sk.update(_spawn_flags(hidden=hidden))
    return _spawn_login_child(
        [binary],
        env=env,
        spawn_kwargs=sk,
        describe=f"tauri app {binary} (hidden={hidden})",
    )


def launch_tauri_frontend_standalone(binary: str, *, port: int, token: str) -> int | None:
    """Spawn the Tauri host in STANDALONE adopted mode (MO-110).

    Called from the backend's standalone-mode startup
    (``ipc/entrypoint.py``): the user ran ``voice-typer`` from a
    terminal, the backend picked a port + generated a token, and the
    frontend host must connect to US instead of spawning its own
    backend. The adopt contract is the env trio the launcher exports:

    - ``VT_PYTHON_PORT``: the port this backend is listening on.
    - ``VT_IPC_TOKEN``: the session token the host must send as the
      first WS auth line.
    - ``VOICE_TYPER_IPC_TOKEN``: the same token under the backend's
      auth env-var name.

    The Tauri host's ``adopted_backend_env`` (``sidecar/spawn.rs``)
    reads these and attaches (no spawn, supervisor disabled). The
    binary is verified against ``tauri-binaries.json`` (fail-closed,
    same as autostart) before spawning.

    Returns the child PID on success, ``None`` on failure (the caller
    falls back to the predecessor launcher path).
    """
    from voice_typer.server import autostart_launcher as _pkg

    if not _pkg.verify_tauri_binary_or_skip(binary):
        log.error(
            "[LAUNCHER] refusing to spawn Tauri frontend %s, integrity verification failed (fail-closed).",
            binary,
        )
        return None
    # ``_launcher_child_env`` force-disables ANSI colour + npm notices
    env = _launcher_child_env()
    env["VT_PYTHON_PORT"] = str(port)
    env["VT_IPC_TOKEN"] = token
    from voice_typer.server._paths import IPC_TOKEN_ENV_VAR

    env[IPC_TOKEN_ENV_VAR] = token
    sk: dict = {}
    sk.update(_pkg._tauri_log_files())
    sk.update(_spawn_flags(hidden=False))
    child = _spawn_login_child(
        [binary],
        env=env,
        spawn_kwargs=sk,
        describe=f"tauri frontend (standalone adopt) {binary}",
    )
    if child is None:
        return None
    return getattr(child, "pid", None)


# Backward-compat alias, older test imports use the previous name.
_launch_tauri_app = _spawn_tauri_host
