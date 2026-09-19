"""Runtime pack schema, paths, manifest validation, and integrity checks."""

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


class OfflinePackFileEntry(TypedDict):
    """One file entry inside a ``pack-manifest.json``."""

    name: str
    sha256: str
    size: int


class OfflinePackManifest(TypedDict):
    """``pack-manifest.json`` schema (§4.6)."""

    version: str
    sha256: str
    files: list[OfflinePackFileEntry]
    min_proto_version: int


# Pack size budget per §5.3: 180 MB compressed + 450 MB unpacked = 630 MB
OFFLINE_PACK_COMPRESSED_MB = 180
OFFLINE_PACK_UNPACKED_MB = 450
OFFLINE_PACK_REQUIRED_MB = OFFLINE_PACK_COMPRESSED_MB + OFFLINE_PACK_UNPACKED_MB  # 630 MB total

# Per-file size cap (defense-in-depth, §5.5, §8.8). The pack total is
OFFLINE_PACK_MAX_PER_FILE_BYTES = 500 * 1024 * 1024  # 500 MB


# GitHub rate limit (§8.7): exponential backoff 1s, 2s, 4s, one sleep
OFFLINE_PACK_RATE_LIMIT_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0)
OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS = 3


# Lock acquisition is blocking with a short timeout, the second
OFFLINE_PACK_LOCK_TIMEOUT_S = 30.0
OFFLINE_PACK_LOCK_POLL_S = 0.25


# The manifest's ``version`` is interpolated RAW into pack paths
_PACK_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class OfflinePackConsentRequiredError(RuntimeError):
    """Raised when a pack download is attempted without"""

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


def _default_offline_pack_root() -> Path:
    """Resolve the per-platform default pack root directory."""
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
    """Return ``<pack-root>/<version>/`` for the given pack version."""
    base = root if root is not None else _default_offline_pack_root()
    return base / version


def offline_pack_manifest_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-manifest.json`` for *version*."""
    return offline_pack_dir_for_version(version, root=root) / "pack-manifest.json"


def offline_pack_partial_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-<version>.partial`` (§8.1 resume)."""
    return offline_pack_dir_for_version(version, root=root) / f"pack-{version}.partial"


def offline_pack_lock_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-<version>.lock`` (§8.13 dual-instance)."""
    base = root if root is not None else _default_offline_pack_root()
    return base / f"pack-{version}.lock"


def fallback_offline_pack_root() -> Path | None:
    """Return the fallback pack root when the primary is write-blocked (§8.11)."""
    system = platform.system()
    if system == "Windows":
        roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(roaming) / "voice-typer" / "runtime-pack"
    home = Path.home()
    if str(home) in ("", "."):
        return None
    return home / ".voice-typer" / "runtime-pack"


def load_offline_pack_manifest(manifest_path: Path) -> OfflinePackManifest | None:
    """Load + structurally validate ``pack-manifest.json``.

    Returns ``None`` when the file is missing or malformed (fail-closed
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
    """Structurally validate a parsed ``pack-manifest.json`` object."""
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
    """Verify the pack for *version* against ``pack-manifest.json``."""
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
    """Verify every manifest-declared file in *pack_dir* (fail-closed)."""
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
    """
    manifest_path = offline_pack_manifest_path(version, root=root)
    if not manifest_path.exists():
        return False
    manifest = load_offline_pack_manifest(manifest_path)
    if manifest is None:
        return False
    pack_root = offline_pack_dir_for_version(version, root=root)
    return all((pack_root / entry["name"]).exists() for entry in manifest["files"])


def _safe_pack_member_name(name: str) -> bool:
    """Return True when *name* is a safe in-pack relative member name."""
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    first = name.split("/", 1)[0]
    if ":" in first:
        return False
    return ".." not in PurePosixPath(name).parts


def verify_offline_pack_signature_macos(path: Path) -> bool | None:
    """Verify the worker exe's notarization + Developer ID (§8.18).

    Returns True / False when verification ran; ``None`` when
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
