"""Native binary discovery.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
().  (session-1): adds SHA-256 manifest verification.
adds Windows arch-suffixed binary names
(Windows on ARM/aarch64 support).

This module owns:

- :data:`_BINARY_NAMES` — per-``(platform, machine)`` binary filename
  map (: per-arch native binaries). Implemented as
  class:`_ArchAwareBinaryNameMap` so pre- callers that index by
  bare platform string (``_BINARY_NAMES.get("linux")``) keep working
  via the legacy shim.
- data:`_LEGACY_BINARY_NAMES` — pre- non-arch-suffixed names,
  used both as a fallback for existing Tauri bundles (during the
  ``tauri.conf.json`` resource-list transition owned by IMPL-4) and
  as the backing store for the string-key shim on
  :class:`_ArchAwareBinaryNameMap`.
- :func:`get_native_binary_path` — find the native key-listener
  binary for the current platform (env var → dev mode → PyInstaller
  bundle).

Per-arch naming convention ()
-----------------------------------
- Linux:   ``linux-key-listener-x86_64`` /
  ``linux-key-listener-aarch64``
- Windows: ``windows-key-listener-x86_64.exe`` /
  ``windows-key-listener-aarch64.exe``
- macOS:   ``macos-key-listener`` (single universal binary produced
  via ``lipo`` of arm64 + x86_64 — no arch suffix).

The :func:`_normalize_machine` helper maps the various
``platform.machine()`` return values (``x86_64``/``amd64`` on x86_64,
``aarch64``/``arm64`` on ARM64, uppercase variants on Windows) to a
canonical arch token used as the dict key.
"""

import functools
import hashlib
import json
import logging
import os
import platform
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Legacy (non-arch-suffixed) names, kept as a backward-compat fallback
# for bundles that still ship the old single-arch binary under the
# pre- name. Probed ONLY when the arch-suffixed name is not found
# at a given candidate location, so it adds at most one extra
# ``is_file()`` call per lookup miss. Also used by
# :class:`_ArchAwareBinaryNameMap` to satisfy the legacy
# ``_BINARY_NAMES.get("<platform>")`` call site in pre- callers
# (e.g. existing tests that pin the contract by platform-string key).
# Once IMPL-4 / the primary agent updates ``src-tauri/tauri.conf.json``
# to ship the arch-suffixed names exclusively AND old bundles have
# aged out of the field, this map (and the fallback logic + string-key
# shim in :class:`_ArchAwareBinaryNameMap`) can be removed.
_LEGACY_BINARY_NAMES: dict[str, str] = {
    "darwin": "macos-key-listener",
    "win32": "windows-key-listener.exe",
    "linux": "linux-key-listener",
}

# bidirectional legacy <-> arch-suffixed name mapping used by
# :func:`get_expected_sha256` to find the right manifest entry
# regardless of which name form the binary on disk uses. The legacy
# non-arch-suffixed names (``linux-key-listener``,
# ``windows-key-listener.exe``) are the pre- names that were
# ALWAYS the x86_64 build (aarch64 builds did not exist pre-), so
# the legacy name maps to the x86_64 arch-suffixed name ONLY — never
# to the aarch64 name. macOS uses the same universal name for both
# forms (``macos-key-listener``), so it has no entry here. The build
# script (``scripts/build/compile_native.sh``) still emits the legacy
# names on Linux/Windows; the manifest (``binaries.json``) now carries
# BOTH name forms (with the same sha256 where the binary exists), so
# the direct :func:`get_expected_sha256` lookup usually succeeds
# without needing this fallback. The fallback exists as a defensive
# measure for future manifests that might drop one form.
_LEGACY_TO_ARCH_SUFFIX: dict[str, str] = {
    "linux-key-listener": "linux-key-listener-x86_64",
    "windows-key-listener.exe": "windows-key-listener-x86_64.exe",
}
_ARCH_SUFFIX_TO_LEGACY: dict[str, str] = {
    "linux-key-listener-x86_64": "linux-key-listener",
    "windows-key-listener-x86_64.exe": "windows-key-listener.exe",
}

# path to the SHA-256 manifest emitted by the build script
# (``scripts/build/compile_native.*``). Used by :func:`load_binary_manifest`
# to verify native binaries were not tampered with after install.
_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "native" / "binaries.json"

# arch-suffixed binary names. Windows on ARM (aarch64) requires
# a separate native binary build because the x86_64 MSVC toolchain
# cannot emit ARM64 code. The lookup key for Windows combines the
# platform (``win32``) with the architecture suffix returned by
# :func:`_windows_arch_suffix` (``x86_64`` or ``aarch64``). Non-Windows
# platforms do not have arch variants and use the bare ``sys.platform``
# value as the key.
_BINARY_NAMES_BY_PLATFORM_ARCH = {
    "darwin": "macos-key-listener",
    "linux": "linux-key-listener",
    "win32-x86_64": "windows-key-listener-x86_64.exe",
    "win32-aarch64": "windows-key-listener-aarch64.exe",
}


def _windows_arch_suffix() -> str:
    """Return the Windows binary arch suffix for the current CPU.

    Windows on ARM (aarch64) is now supported via a separate
    binary build (``windows-key-listener-aarch64.exe``). The x86_64
    build is renamed to ``windows-key-listener-x86_64.exe`` for
    clarity (and so the binary name uniquely identifies the target
    arch).

    ``platform.machine()`` returns:

      - ``'AMD64'`` on x86_64 Windows (Windows convention — the CPU
        vendor string, not the architecture string).
      - ``'ARM64'`` on aarch64 Windows.
      - ``'x86_64'`` / ``'aarch64'`` on Linux/macOS hosts (POSIX
        convention). On POSIX we don't actually need this function
        (the Windows branch is only entered when ``sys.platform ==
        'win32'``), but we normalize defensively anyway.

    Returns the suffix used in the binary filename:

      - ``'aarch64'`` for ARM64 hosts.
      - ``'x86_64'`` for everything else (AMD64, x64, x86_64, etc.).
    """
    machine = platform.machine().upper()
    if machine in ("ARM64", "AARCH64"):
        return "aarch64"
    # Default: AMD64, x86_64, x64, EM64T, etc. — all map to x86_64 suffix.
    return "x86_64"
class _ArchAwareBinaryNameMap(dict):
    """Dict keyed by ``(platform, machine)`` with a legacy string-key shim.

     mandates that ``_BINARY_NAMES`` be a ``dict[tuple[str, str],
    str]`` keyed by ``(platform, machine)``. This subclass satisfies
    that contract while *also* keeping the pre- string-key
    interface alive for backward compatibility:

    - ``_BINARY_NAMES[("linux", "x86_64")]`` → ``"linux-key-listener-x86_64"``
      (new arch-aware lookup — ).
    - ``_BINARY_NAMES.get("linux")`` → ``"linux-key-listener"``
      (legacy string-key lookup — delegates to
      :data:`_LEGACY_BINARY_NAMES`).

    The string-key shim is implemented only on ``__getitem__``,
    ``get``, and ``__contains__``; iteration and other dict operations
    only see the tuple keys (so ``len(_BINARY_NAMES)`` returns the
    arch-aware entry count, not the legacy count).
    """

    def __getitem__(self, key):
        if isinstance(key, tuple):
            return super().__getitem__(key)
        if isinstance(key, str):
            return _LEGACY_BINARY_NAMES[key]
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if isinstance(key, tuple):
            return super().__contains__(key)
        if isinstance(key, str):
            return key in _LEGACY_BINARY_NAMES
        return False


# Per-(platform, machine) binary filename map ().
#
# macOS ships a single universal binary (lipo of arm64 + x86_64) so
# the same filename ``macos-key-listener`` is used for both arches.
# Linux and Windows ship per-arch variants because neither OS has a
# lipo-style universal binary mechanism.
#
# Note: the dict literal is built with tuple keys ( contract);
# the :class:`_ArchAwareBinaryNameMap` subclass adds the legacy
# string-key shim on top so pre- callers that index by bare
# platform string keep working.
_BINARY_NAMES: dict[tuple[str, str], str] = _ArchAwareBinaryNameMap(
    {
        # macOS: universal binary covers both arm64 + x86_64.
        ("darwin", "x86_64"): "macos-key-listener",
        ("darwin", "arm64"): "macos-key-listener",
        ("darwin", "aarch64"): "macos-key-listener",
        # Windows: per-arch variants.
        ("win32", "x86_64"): "windows-key-listener-x86_64.exe",
        ("win32", "amd64"): "windows-key-listener-x86_64.exe",
        ("win32", "aarch64"): "windows-key-listener-aarch64.exe",
        ("win32", "arm64"): "windows-key-listener-aarch64.exe",
        # Linux: per-arch variants.
        ("linux", "x86_64"): "linux-key-listener-x86_64",
        ("linux", "amd64"): "linux-key-listener-x86_64",
        ("linux", "aarch64"): "linux-key-listener-aarch64",
        ("linux", "arm64"): "linux-key-listener-aarch64",
    }
)


def _normalize_machine(machine: str | None) -> str:
    """Normalize :func:`platform.machine` output to a canonical arch token.

    ``platform.machine()`` returns:

    - ``"x86_64"`` on Linux x86_64 and macOS Intel
    - ``"amd64"`` on Windows x86_64
    - ``"aarch64"`` on Linux ARM64
    - ``"arm64"`` on macOS Apple Silicon and Windows 11 ARM
    - ``"ARM64"`` (uppercase) on some Windows 11 ARM builds
    - ``"i386"`` / ``"i686"`` / ``"x86"`` on 32-bit hosts

    The normalization lowercases the input and folds aliases together
    so the ``_BINARY_NAMES`` lookup table only needs one entry per
    architecture family.
    """
    m = (machine or "").lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    if m in ("i386", "i686", "x86"):
        return "i686"
    return m  # unknown — caller will see no _BINARY_NAMES entry


def _candidate_binary_names() -> list[str]:
    """Return the candidate binary names for the current platform+arch.

    The arch-suffixed name () is preferred; the legacy
    non-arch-suffixed name is appended as a fallback so existing
    bundles keep working during the ``tauri.conf.json`` transition.
    The returned list is de-duplicated (macOS universal binary uses
    the same name for both arch and legacy, so it appears once).

    Returns an empty list if the platform is unknown.
    """
    machine = _normalize_machine(platform.machine())
    names: list[str] = []
    primary = _BINARY_NAMES.get((sys.platform, machine))
    if primary:
        names.append(primary)
    legacy = _LEGACY_BINARY_NAMES.get(sys.platform)
    if legacy and legacy not in names:
        names.append(legacy)
    return names


# ─── Binary discovery ──────────────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def get_native_binary_path() -> Path | None:
    """Find the native key-listener binary for the current platform+arch.

     (CACHED): the result is memoised with
    :func:`functools.lru_cache(maxsize=1)` so the 6-step lookup chain
    (env var → dev mode → PyInstaller onedir → ``_MEIPASS``) runs at
    most ONCE per process. Pre-fix, the function was called up to
    three times at startup (once per backend factory probe — see
    :func:`voice_typer.server.native_hotkeys.factory.create_native_backend`
    and
    :func:`voice_typer.server.native_hotkeys.factory.is_native_backend_available`,
    plus once from :class:`SubprocessHotkeyBackend.__init__` in
    ``base.py``), each call performing up to 6 ``Path.is_file()`` /
    ``os.stat`` probes — i.e. ~18 stats at boot for a result that
    cannot change within a single process.

    The function is PURE with respect to a single process: the
    platform (``sys.platform``), the architecture
    (``platform.machine()``), the candidate binary names
    (:data:`_BINARY_NAMES` / :data:`_LEGACY_BINARY_NAMES`), and the
    filesystem layout are all fixed for the lifetime of the process.
    The only inputs that COULD change are the
    ``VOICE_TYPER_NATIVE_BINARY`` / ``VOICE_TYPER_NATIVE_DIR`` env
    vars, but those are set by the Tauri host (or the user's shell)
    BEFORE the sidecar starts and do not change afterwards. Tests
    that need to simulate different env / platform / filesystem state
    MUST call :meth:`get_native_binary_path.cache_clear` (or use the
    ``clear_binary_path_cache`` autouse fixture in ``tests/conftest.py``)
    between scenarios — see ``tests/test_binary_path_caching.py`` for
    the pinning tests.

    Search order:
    1. ``VOICE_TYPER_NATIVE_BINARY`` env var (explicit override — single binary)
    2. ``VOICE_TYPER_NATIVE_DIR`` env var (ADR-0020 §7 — Tauri resource dir containing all native binaries)
    3. ``voice_typer/server/native/<binary-name>`` (dev mode — source tree)
    4. ``voice_typer/server/native/<binary-name>.exe`` (Windows dev mode)
    5. Next to the Python executable (PyInstaller onedir mode)
    6. Inside ``_MEIPASS`` (PyInstaller onefile mode)

    At each step (2–6) the arch-suffixed name () is tried first;
    if no file is found, the legacy non-arch-suffixed name is tried as
    a fallback ( transition — see :data:`_LEGACY_BINARY_NAMES`).

    Returns ``None`` if no binary is found.

    on Windows, the binary name is arch-suffixed
    (``windows-key-listener-x86_64.exe`` or
    ``windows-key-listener-aarch64.exe``) — see
    :func:`_windows_arch_suffix`. The legacy non-suffixed
    ``windows-key-listener.exe`` name in :data:`_BINARY_NAMES` is no
    longer looked up here; existing installs should rebuild via
    ``scripts/build/compile_native.ps1`` (which now emits the
    arch-suffixed name).

    callers SHOULD follow this with a call to
    :func:`verify_native_binary_or_skip` to verify the SHA-256 of the
    returned path against the manifest (``binaries.json``).
    """
    binary_names = _candidate_binary_names()
    if not binary_names:
        return None

    # 1. Explicit override (single binary path) — name-agnostic.
    env_path = os.environ.get("VOICE_TYPER_NATIVE_BINARY")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
    env_dir = os.environ.get("VOICE_TYPER_NATIVE_DIR")
    if env_dir:
        for binary_name in binary_names:
            candidate = Path(env_dir) / binary_name
            if candidate.is_file():
                return candidate

    # 3/4. Dev mode — alongside this package's source tree.  Use
    # ``__file__`` of *this* module (``native_hotkeys/binary_path.py``)
    # resolved up two parents (``native_hotkeys/`` → ``server/``) and
    # then into ``server/native/``.  This mirrors the original layout
    # where ``native_hotkeys.py`` lived directly in ``server/``.
    module_dir = Path(__file__).resolve().parent.parent / "native"
    for binary_name in binary_names:
        candidates = [
            module_dir / binary_name,
            # Some platforms may have a .exe suffix even in dev (cross-compile)
            module_dir / f"{binary_name}.exe",
        ]
        for c in candidates:
            if c.is_file():
                return c

    # 5. PyInstaller onedir: binary sits next to python executable.
    exe_dir = Path(sys.executable).resolve().parent
    for binary_name in binary_names:
        onedir_candidate = exe_dir / binary_name
        if onedir_candidate.is_file():
            return onedir_candidate

    # 6. PyInstaller onefile: binary extracted to _MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for binary_name in binary_names:
            meipass_candidate = Path(meipass) / "voice_typer" / "server" / "native" / binary_name
            if meipass_candidate.is_file():
                return meipass_candidate

    return None


def load_binary_manifest() -> dict | None:
    try:
        text = _MANIFEST_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.debug("[NATIVE-BINARY] No manifest at %s", _MANIFEST_PATH)
        return None
    except OSError as exc:
        log.warning("[NATIVE-BINARY] Failed to read manifest %s: %s", _MANIFEST_PATH, exc)
        return None
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("[NATIVE-BINARY] Malformed manifest %s: %s", _MANIFEST_PATH, exc)
        return None
    if not isinstance(manifest, dict):
        log.warning("[NATIVE-BINARY] Manifest %s is not a JSON object", _MANIFEST_PATH)
        return None
    return manifest


def _equivalent_manifest_names(binary_name: str) -> list[str]:
    """Return the ordered list of manifest keys to try for ``binary_name``.

    the manifest may be keyed by either the arch-suffixed name
    (``linux-key-listener-x86_64``) or the legacy non-suffixed name
    (``linux-key-listener``), depending on which form
    ``scripts/build/compile_native.sh`` emitted and which form
    ``scripts/build/update_native_manifests.py`` recorded. The shipped
    manifest (``binaries.json``) now carries BOTH forms as aliases (with
    the same sha256 where the binary exists), so the direct lookup
    usually succeeds. This helper returns the direct name first, then
    any equivalents (legacy <-> arch-suffixed x86_64 ONLY — aarch64 has
    no legacy equivalent because aarch64 builds are new in ), so
    :func:`get_expected_sha256` can still find the right entry if a
    future manifest drops one form.

    The returned list is de-duplicated while preserving order (the
    direct name is always first). macOS uses the same universal name
    for both forms (``macos-key-listener``), so it returns a
    single-element list.
    """
    candidates: list[str] = [binary_name]
    arch_equivalent = _LEGACY_TO_ARCH_SUFFIX.get(binary_name)
    if arch_equivalent:
        candidates.append(arch_equivalent)
    legacy_equivalent = _ARCH_SUFFIX_TO_LEGACY.get(binary_name)
    if legacy_equivalent:
        candidates.append(legacy_equivalent)
    # De-dup while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def get_expected_sha256(binary_name: str) -> str | None:
    """Look up the expected SHA-256 for a binary by its filename.

    ``binary_name`` MUST be the arch-suffixed name actually
    produced by ``scripts/build/compile_native.sh`` and discovered by
    :func:`get_native_binary_path` (e.g. ``linux-key-listener-x86_64``,
    ``windows-key-listener-aarch64.exe``, ``macos-key-listener``).
    Pre- the manifest was keyed by the legacy non-suffixed names
    (``linux-key-listener``, ``windows-key-listener.exe``), so every
    call with an arch-suffixed name returned ``None`` and
    :func:`verify_native_binary_or_skip` silently trusted the binary.
    The manifest is now keyed by the arch-suffixed names; this function
    is a plain dict lookup, so callers MUST pass the same name
    :func:`get_native_binary_path` returned (``path.name``).

    the build script (``compile_native.sh``) still emits the
    legacy non-suffixed names on Linux/Windows
    (``linux-key-listener``, ``windows-key-listener.exe``), so
    ``path.name`` may be EITHER form. The manifest now carries BOTH
    forms as aliases (with the same sha256), so the direct lookup
    succeeds either way. As a defensive fallback, if the direct lookup
    misses (or hits an empty sha256 entry), this function also tries
    the equivalent name (legacy <-> arch-suffixed x86_64) via
    :func:`_equivalent_manifest_names`. This keeps verification working
    even if a future manifest drops one form. aarch64 arch-suffixed
    names have NO legacy equivalent (aarch64 builds are new in ),
    so they only match their own manifest entry.
    """
    manifest = load_binary_manifest()
    if manifest is None:
        return None
    binaries = manifest.get("binaries", {})
    if not isinstance(binaries, dict):
        return None
    # try the direct name first, then equivalent names (legacy
    # <-> arch-suffixed x86_64). The first entry with a non-empty
    # sha256 wins; entries with empty sha256 are skipped (so a future
    # manifest that populates only one form still verifies correctly).
    for candidate_name in _equivalent_manifest_names(binary_name):
        entry = binaries.get(candidate_name)
        if not isinstance(entry, dict):
            continue
        sha = entry.get("sha256", "")
        if isinstance(sha, str) and sha:
            return sha.strip().lower()
    return None


def verify_native_binary(path: Path, expected_sha256: str) -> bool:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        log.warning("[NATIVE-BINARY] Failed to read %s: %s", path, exc)
        return False
    expected = expected_sha256.strip().lower()
    if actual != expected:
        log.error(
            "[NATIVE-BINARY] CHECKSUM MISMATCH for %s — expected %s, got %s. "
            "Refusing to use this binary; falling back to legacy backend.",
            path,
            expected,
            actual,
        )
        return False
    log.debug("[NATIVE-BINARY] Checksum OK for %s (%s)", path.name, actual)
    return True


def _is_trusted_path_override() -> bool:
    """+ : Return True only when BOTH conditions hold:

      1. The user has explicitly set the trusted-path confirmation env
         var ``VOICE_TYPER_NATIVE_TRUST=1``. This is a paired
         confirmation required in addition to the binary/dir env vars
         so that an attacker with env-var write access cannot silently
         disable the checksum gate by merely setting
         ``VOICE_TYPER_NATIVE_BINARY``.
      2. The user has set ``VOICE_TYPER_NATIVE_BINARY`` OR
         ``VOICE_TYPER_NATIVE_DIR`` (the actual override path/dir).

    The bypass is logged at WARNING ( — previously DEBUG which
    was invisible at default log levels, making the silent checksum
    bypass unauditable in production).
    """
    has_trust_flag = os.environ.get("VOICE_TYPER_NATIVE_TRUST") == "1"
    has_path_override = bool(os.environ.get("VOICE_TYPER_NATIVE_BINARY")) or bool(
        os.environ.get("VOICE_TYPER_NATIVE_DIR")
    )
    return has_trust_flag and has_path_override


def _env_specified_paths() -> list[Path]:
    """Return the list of paths the user explicitly trust-listed
    via ``VOICE_TYPER_NATIVE_BINARY`` / ``VOICE_TYPER_NATIVE_DIR``.

    Used by :func:`verify_native_binary_or_skip` to confirm that the
    discovered binary actually lives under an env-specified location
    (rather than being discovered via fallback search after the env
    override was set). This closes the  hole where setting
    ``VOICE_TYPER_NATIVE_BINARY=/nonexistent`` disabled verification
    for ANY binary found via fallback search.

    Returns an empty list if neither env var is set.
    """
    paths: list[Path] = []
    binary_env = os.environ.get("VOICE_TYPER_NATIVE_BINARY")
    if binary_env:
        paths.append(Path(binary_env))
    dir_env = os.environ.get("VOICE_TYPER_NATIVE_DIR")
    if dir_env:
        paths.append(Path(dir_env))
    return paths


def _path_matches_env_override(path: Path) -> bool:
    """Return True if ``path`` equals or lives under one of the
    env-specified paths from :func:`_env_specified_paths`.

    The check is path-prefix-based: a discovered path
    ``/opt/vt/native/linux-key-listener-x86_64`` matches an env override
    ``VOICE_TYPER_NATIVE_DIR=/opt/vt/native`` (the binary lives under
    the env dir). A discovered path
    ``/opt/vt/native/linux-key-listener-x86_64`` also matches an env
    override ``VOICE_TYPER_NATIVE_BINARY=/opt/vt/native/linux-key-listener-x86_64``
    (the binary equals the env path).
    """
    env_paths = _env_specified_paths()
    if not env_paths:
        return False
    resolved = path.resolve()
    for env_path in env_paths:
        env_resolved = env_path.resolve()
        # Exact match (VOICE_TYPER_NATIVE_BINARY case).
        if resolved == env_resolved:
            return True
        # Parent-dir match (VOICE_TYPER_NATIVE_DIR case): the discovered
        # binary lives directly inside the env dir. Use Path.is_relative_to
        # (Python 3.9+) which handles the parent/child relationship
        # correctly without string-prefix pitfalls (e.g. /opt/vt/native2
        # would NOT match /opt/vt/native).
        try:
            if resolved.is_relative_to(env_resolved):
                return True
        except AttributeError:
            # Python <3.9 fallback — not expected on 3.12, but defensive.
            try:
                resolved.relative_to(env_resolved)
                return True
            except ValueError:
                pass
    return False


def verify_native_binary_or_skip(path: Path) -> bool:
    #  + : trusted-path override now requires BOTH:
    #   1. ``VOICE_TYPER_NATIVE_TRUST=1`` (paired confirmation env var).
    #   2. The discovered ``path`` actually lives under (or equals) one
    #      of the env-specified paths. Setting the env vars alone no
    #      longer disables verification for binaries discovered via
    #      fallback search.
    if _is_trusted_path_override() and _path_matches_env_override(path):
        # elevated from DEBUG to WARNING so the bypass is
        # auditable at default log levels. The bypass is a security-
        # relevant event — operators SHOULD see it in the log without
        # having to enable DEBUG logging.
        log.warning(
            "[NATIVE-BINARY] Skipping checksum for %s — trusted-path "
            "override active (VOICE_TYPER_NATIVE_TRUST=1 + path matches "
            "env-specified location).",
            path,
        )
        return True
    expected = get_expected_sha256(path.name)
    if expected is None:
        # FAIL CLOSED. Previously this branch silently trusted
        # the binary (returned True with a debug log) whenever the
        # manifest entry was missing OR had an empty sha256. That made
        # the entire SHA-256 gate a no-op in any environment where the
        # manifest wasn't perfectly populated (e.g. dev trees without
        # cross-compiled Windows/macOS binaries, CI builds that hadn't
        # yet run scripts/build/update_native_manifests.py, or — as the
        #  reviewer found — pre- manifests keyed by the
        # legacy non-suffixed names while the build emitted
        # arch-suffixed names). A tampered binary could bypass
        # verification simply by being named something the manifest
        # didn't list. Now we refuse to use the binary and fall back
        # to the legacy backend instead. Production builds MUST
        # populate every manifest entry's sha256 via
        # scripts/build/update_native_manifests.py (run by CI after
        # compile_native.sh).
        log.error(
            "[NATIVE-BINARY] FAIL CLOSED for %s — no usable manifest entry "
            "(manifest missing, entry missing, or sha256 empty). "
            "Refusing to use this binary; falling back to legacy backend. "
            "Run scripts/build/update_native_manifests.py to populate the manifest.",
            path.name,
        )
        return False
    return verify_native_binary(path, expected)
