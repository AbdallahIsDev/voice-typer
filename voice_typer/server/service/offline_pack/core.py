"""Runtime pack schema, paths, manifest validation, and integrity checks.

Foundation layer for the runtime-pack downloader. Network install and
lock lifecycles live in the sibling :mod:`.download` / :mod:`.install` /
:mod:`.lock` modules; this module is the shared, side-effect-light core.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import TypedDict, cast

log = logging.getLogger(__name__)


# ── Manifest schema (§4.6) ────────────────────────────────────────────────


class OfflinePackFileEntry(TypedDict):
    """One file entry inside a ``pack-manifest.json``."""

    name: str
    sha256: str
    size: int


class OfflinePackManifest(TypedDict):
    """``pack-manifest.json`` schema (§4.6).

    Lives at the per-platform pack path:
    ``<pack-root>/<version>/pack-manifest.json``. Do NOT extend
    ``tauri-binaries.json``: that manifest's schema is scoped to a
    single host binary spawned by the launcher (see §4.6).
    """

    version: str
    sha256: str
    files: list[OfflinePackFileEntry]
    min_proto_version: int


# ── Disk space (§8.8) ────────────────────────────────────────────────────

# Pack size budget per §5.3: 180 MB compressed + 450 MB unpacked = 630 MB
# required (with margin). Mirrors the ``_DISK_SPACE_MARGIN_MB`` pattern
# in :mod:`voice_typer.server.asr_utils`.
OFFLINE_PACK_COMPRESSED_MB = 180
OFFLINE_PACK_UNPACKED_MB = 450
OFFLINE_PACK_REQUIRED_MB = OFFLINE_PACK_COMPRESSED_MB + OFFLINE_PACK_UNPACKED_MB  # 630 MB total

# Per-file size cap (defense-in-depth, §5.5, §8.8). The pack total is
# ~530 MB compressed+unpacked; individual files are typically << 100 MB
# (the largest is the worker exe at ~80 MB). A 500 MB per-file cap
# rejects PATOLOGICAL entries (e.g. a 100 GB size field that would
# crash the disk-space check or be used as a DoS vector) while allowing
# any legitimate file in the pack. The cap is defense-in-depth —
# already mitigated by per-file SHA-256 verification (a malicious file
# with a wrong size field fails the hash check) + the 630 MB disk-space
# check (the pack download aborts when free space < 630 MB).
OFFLINE_PACK_MAX_PER_FILE_BYTES = 500 * 1024 * 1024  # 500 MB


# ── Retry / backoff (§8.7) ─────────────────────────────────────────

# GitHub rate limit (§8.7): exponential backoff 1s, 2s, 4s, one sleep
# per retry; with ``MAX_ATTEMPTS = 3`` exactly three sleeps can happen,
# so the tuple has exactly three entries (a longer tuple's tail would
# be unreachable).
OFFLINE_PACK_RATE_LIMIT_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0)
OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS = 3


# ── Lock file (§8.13) ────────────────────────────────────────────────────

# Lock acquisition is blocking with a short timeout, the second
# instance waits, sees the lock-file version, and either defers (same
# version) or proceeds with its own download (different version).
OFFLINE_PACK_LOCK_TIMEOUT_S = 30.0
OFFLINE_PACK_LOCK_POLL_S = 0.25


# ── Version-string safety (path-traversal defense) ────────────────────────

# The manifest's ``version`` is interpolated RAW into pack paths
# (``base / version`` for the pack dir, ``pack-<version>.partial`` /
# ``.lock`` filenames, the ``.new`` staging and ``.trash`` dirs). A
# version like ``../../x`` or an absolute path would escape the pack
# root, so the manifest loader only accepts versions that are a single
# safe path component: one leading alphanumeric, then alphanumerics /
# dots / underscores / hyphens. This mirrors the same fail-closed
# discipline the per-file size cap applies to untrusted manifest data
# (the manifest comes over the network, never trust path parts).
_PACK_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ── Exceptions ───────────────────────────────────────────────────────────


class OfflinePackConsentRequiredError(RuntimeError):
    """Raised when a pack download is attempted without
    :attr:`Config.offline_pack_consent`.

    Mirrors :class:`voice_typer.server.asr_errors.ConsentRequiredError`
    so the IPC layer can ``isinstance``-check and surface a consent
    dialog instead of an error toast. The structured fields let the
    renderer deep-link to the exact Settings toggle.

    The consent flag is ``offline_pack_consent``: NOT
    ``huggingface_consent``, because the pack download phones home to
    GitHub Releases (Microsoft), not HuggingFace. See §8.4.
    """

    provider: str = "github"
    scope: str = "download"
    consent_field: str = "offline_pack_consent"

    def __init__(self, message: str | None = None, *, version: str | None = None) -> None:
        self.version = version
        super().__init__(
            message or f"Runtime pack consent not given, refusing to download pack {version or '<unknown>'}."
        )


class OfflinePackDiskFullError(OSError):
    """Raised when the disk fills mid-download (§8.9)."""

    def __init__(self, message: str, *, version: str, path: str) -> None:
        self.version = version
        self.path = path
        super().__init__(message)


class OfflinePackRateLimitError(RuntimeError):
    """Raised when GitHub returns 403 / 429 and the retry budget is exhausted."""

    def __init__(self, message: str, *, version: str, reset_at: float | None) -> None:
        self.version = version
        self.reset_at = reset_at
        super().__init__(message)


# ── Cross-platform path resolution (§4.7) ────────────────────────────────


def _default_offline_pack_root() -> Path:
    """Resolve the per-platform default pack root directory.

    Mirrors the path table documented in §4.7 (which itself mirrors
    ``src-tauri/src/platform/paths.rs:163-356``: owned by the
    orchestrator's platform layer; the Rust ``worker_path.rs`` resolver
    owns the worker-binary side). We code against the documented path
    table here.

    The ``VT_PACK_ROOT`` env var override lets tests (and power users)
    relocate the pack to a custom path. Production code SHOULD NOT
    document this env var, it's a test escape hatch.
    """
    env = os.environ.get("VT_PACK_ROOT")
    if env:
        return Path(env)
    system = platform.system()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "voice-typer" / "runtime-pack"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "voice-typer" / "runtime-pack"
    # Linux / *BSD: respect XDG_DATA_HOME (default ~/.local/share).
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "voice-typer" / "runtime-pack"


def offline_pack_dir_for_version(version: str, *, root: Path | None = None) -> Path:
    """Return ``<pack-root>/<version>/`` for the given pack version.

    The directory may not exist yet, callers should ``mkdir(parents=True,
    exist_ok=True)`` before writing.
    """
    base = root if root is not None else _default_offline_pack_root()
    return base / version


def offline_pack_manifest_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-manifest.json`` for *version*."""
    return offline_pack_dir_for_version(version, root=root) / "pack-manifest.json"


def offline_pack_partial_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-<version>.partial`` (§8.1 resume)."""
    return offline_pack_dir_for_version(version, root=root) / f"pack-{version}.partial"


def offline_pack_lock_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-<version>.lock`` (§8.13 dual-instance).

    The lock file is a SIBLING of the version directory (it lives in the
    pack root, NOT inside ``<version>/``). ``atomic_swap_offline_pack``
    renames ``<version>/`` to ``<version>.trash``: a lock living inside
    the version dir would have its inode carried away at the swap
    instant, and the next instance's ``open()`` would create a fresh
    lock file and acquire instantly (the cross-process exclusion would
    silently stop excluding, and the second instance would re-download
    the ~200 MB pack). Sibling placement keeps the lock inode stable
    across swaps.
    """
    base = root if root is not None else _default_offline_pack_root()
    return base / f"pack-{version}.lock"


def fallback_offline_pack_root() -> Path | None:
    """Return the fallback pack root when the primary is write-blocked (§8.11).

    Windows: roaming AppData (``%APPDATA%\\voice-typer\\runtime-pack``).
    POSIX: ``~/.voice-typer/runtime-pack`` (mirrors the existing
    ``~/.voice-typer`` fallback used by ``single_instance.py`` and
    ``prewarm/paths.py``).

    Returns ``None`` when no fallback is available (extremely rare —
    usually means ``$HOME`` is unset).
    """
    system = platform.system()
    if system == "Windows":
        roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(roaming) / "voice-typer" / "runtime-pack"
    home = Path.home()
    if str(home) in ("", "."):
        return None
    return home / ".voice-typer" / "runtime-pack"


# ── Manifest helpers (§4.6, §8.2) ────────────────────────────────────────


def load_offline_pack_manifest(manifest_path: Path) -> OfflinePackManifest | None:
    """Load + structurally validate ``pack-manifest.json``.

     Returns ``None`` when the file is missing or malformed (fail-closed
    , mirrors :func:`verify_tauri_binary_or_skip`'s manifest-missing
     path in :mod:`voice_typer.server.autostart_launcher`). The caller
     MUST treat ``None`` as "do not trust the pack".
    """
    try:
        raw = Path(manifest_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        log.exception("[PACK] FAIL CLOSED: cannot read manifest %s", manifest_path)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.exception("[PACK] FAIL CLOSED: manifest %s is not valid JSON", manifest_path)
        return None
    return validate_offline_pack_manifest_dict(data, source=str(manifest_path))


def validate_offline_pack_manifest_dict(
    data: object,
    *,
    source: str = "<manifest>",
) -> OfflinePackManifest | None:
    """Structurally validate a parsed ``pack-manifest.json`` object.

    The dict-level half of :func:`load_offline_pack_manifest`, split so
    an already-parsed manifest (e.g. the remote manifest fetched by the
    update checker) can be validated WITHOUT a temp-file round-trip.
    Fail-closed on any missing/wrong-typed field: the caller MUST treat
    ``None`` as "do not trust the pack".
    """
    if not isinstance(data, dict):
        log.error("[PACK] FAIL CLOSED: manifest %s is not an object", source)
        return None
    version = data.get("version")
    sha256 = data.get("sha256")
    files = data.get("files")
    min_proto = data.get("min_proto_version")
    if not isinstance(version, str) or not version:
        log.error("[PACK] FAIL CLOSED: manifest %s missing 'version'", source)
        return None
    if not _PACK_VERSION_RE.fullmatch(version):
        # Defense-in-depth for path traversal: the version is used raw in
        # pack paths (dir, partial, lock, staging, trash). A ``../`` hop,
        # an absolute path, or a Windows drive anchor would write outside
        # the pack root, reject before any path is built from it.
        log.error(
            "[PACK] FAIL CLOSED: manifest %s 'version' %r is not a safe version string",
            source,
            version,
        )
        return None
    if not isinstance(sha256, str) or len(sha256) != 64:
        log.error("[PACK] FAIL CLOSED: manifest %s 'sha256' is invalid", source)
        return None
    if not isinstance(files, list) or not files:
        log.error("[PACK] FAIL CLOSED: manifest %s 'files' is empty/missing", source)
        return None
    for entry in files:
        if not isinstance(entry, dict):
            log.error("[PACK] FAIL CLOSED: manifest %s file entry not an object", source)
            return None
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            log.error("[PACK] FAIL CLOSED: manifest %s file entry missing 'name'", source)
            return None
        if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
            log.error("[PACK] FAIL CLOSED: manifest %s file '%s' sha256 invalid", source, entry.get("name"))
            return None
        if not isinstance(entry.get("size"), int) or entry["size"] < 0:
            log.error("[PACK] FAIL CLOSED: manifest %s file '%s' size invalid", source, entry.get("name"))
            return None
        if entry["size"] > OFFLINE_PACK_MAX_PER_FILE_BYTES:
            log.error(
                "[PACK] FAIL CLOSED: manifest %s file '%s' size %d exceeds per-file cap %d bytes",
                source,
                entry.get("name"),
                entry["size"],
                OFFLINE_PACK_MAX_PER_FILE_BYTES,
            )
            return None
    if not isinstance(min_proto, int) or min_proto < 0:
        log.error("[PACK] FAIL CLOSED: manifest %s 'min_proto_version' invalid", source)
        return None
    return cast(OfflinePackManifest, data)


# ── Integrity verification (§4.6, §8.2, §8.10, §8.16) ────────────────────


def _sha256_file(path: Path, *, chunk_bytes: int = 1 << 20) -> str:
    """Stream-hash *path* with SHA-256 (1 MB chunks)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            buf = fh.read(chunk_bytes)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def verify_offline_pack_or_skip(version: str, *, root: Path | None = None) -> bool:
    """Verify the pack for *version* against ``pack-manifest.json``.

    Modeled on :func:`voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip`
    (fail-closed semantics):

    - If the manifest cannot be located → FAIL CLOSED (return False).
    - If the per-file SHA-256 mismatches → FAIL CLOSED.
    - If a declared file is missing → FAIL CLOSED.
    - Otherwise → return True (the pack is safe to use).

    The check is O(pack-size), ~450 MB hashed. Callers that need a
    *cheap* launch-time check should use :func:`offline_pack_exists` instead
    and run :func:`verify_offline_pack_or_skip` in the background (§8.10,
    §8.16).
    """
    manifest_path = offline_pack_manifest_path(version, root=root)
    manifest = load_offline_pack_manifest(manifest_path)
    if manifest is None:
        return False
    pack_root = offline_pack_dir_for_version(version, root=root)
    if not _verify_manifest_files(pack_root, manifest, version=version):
        return False
    log.debug("[PACK] pack %s verified against %s", version, manifest_path)
    return True


def _verify_manifest_files(
    pack_dir: Path,
    manifest: OfflinePackManifest,
    *,
    version: str,
) -> bool:
    """Verify every manifest-declared file in *pack_dir* (fail-closed).

    Shared by :func:`verify_offline_pack_or_skip` (installed pack) and
    :func:`install_offline_pack` (staging dir, pre-swap) so both paths
    enforce the SAME integrity rules: a missing file or a SHA-256
    mismatch means the pack must not be trusted.
    """
    for entry in manifest["files"]:
        path = pack_dir / entry["name"]
        if not _safe_pack_member_name(entry["name"]):
            log.error(
                "[PACK] FAIL CLOSED: manifest entry %r escapes the pack dir",
                entry["name"],
            )
            return False
        if not path.exists():
            log.error(
                "[PACK] FAIL CLOSED: pack %s missing file %s (declared in manifest)",
                version,
                entry["name"],
            )
            return False
        try:
            actual = _sha256_file(path)
        except OSError:
            log.exception("[PACK] FAIL CLOSED: cannot hash %s", path)
            return False
        if not hmac.compare_digest(actual, entry["sha256"]):
            log.error(
                "[PACK] FAIL CLOSED: SHA-256 mismatch for %s (file %s), tampered or stale",
                version,
                entry["name"],
            )
            return False
    return True


def offline_pack_exists(version: str, *, root: Path | None = None) -> bool:
    """Cheap launch-time existence check (§8.10, §8.16).

    Returns True iff ``pack-<version>/pack-manifest.json`` AND every
    file declared in the manifest is **present on disk**. Does NOT
    hash, that's :func:`verify_offline_pack_or_skip`'s job. Use this on the
    hot startup path; schedule the full checksum in the background.
    """
    manifest_path = offline_pack_manifest_path(version, root=root)
    if not manifest_path.exists():
        return False
    manifest = load_offline_pack_manifest(manifest_path)
    if manifest is None:
        return False
    pack_root = offline_pack_dir_for_version(version, root=root)
    return all((pack_root / entry["name"]).exists() for entry in manifest["files"])


# ── Archive member safety (shared by extract + verify) ───────────────────


def _safe_pack_member_name(name: str) -> bool:
    """Return True when *name* is a safe in-pack relative member name.

    Rejects absolute paths (``/etc/passwd``), Windows drive anchors
    (``C:``), backslash separators (zip members use ``/``), and any
    ``..`` hop that would escape the pack directory (zip-slip /
    manifest-name traversal). Both the extraction step and the
    manifest-entry verification use this gate.
    """
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    first = name.split("/", 1)[0]
    if ":" in first:
        return False
    return ".." not in PurePosixPath(name).parts


# ── Code signing (§8.18) ─────────────────────────────────────────────────


def verify_offline_pack_signature_macos(path: Path) -> bool | None:
    """Verify the worker exe's notarization + Developer ID (§8.18).

    Returns True / False when verification ran; ``None`` when
    ``codesign`` / ``spctl`` is unavailable. Uses the macOS-bundled
    ``codesign`` and ``spctl`` CLIs (no extra dep).
    """
    if platform.system() != "Darwin":
        return None
    try:
        # codesign --verify --strict --verbose=2 <path>
        cp = subprocess.run(
            ["codesign", "--verify", "--strict", "--verbose=2", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if cp.returncode != 0:
            return False
        # spctl --assess --type execute --verbose <path>
        cp2 = subprocess.run(
            ["spctl", "--assess", "--type", "execute", "--verbose", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return cp2.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("[PACK] macOS codesign/spctl unavailable: %s", exc)
        return None

