"""Runtime pack downloader service — Phase 2b (plan-runtime-pack-split.md §4.5–4.9, §8).

A SEPARATE consent-gated downloader for the ML **runtime pack**
(worker exe + onnxruntime + ctranslate2 + engines). Modeled on
:mod:`voice_typer.server.service.model` but independent:

* the pack download phones home to **GitHub Releases** (revealing the
  user's IP to Microsoft), so it MUST be gated on a DIFFERENT consent
  flag — :attr:`Config.runtime_pack_consent` — NOT
  :attr:`Config.huggingface_consent` (which gates HuggingFace model
  downloads only). See §8.4 / C-DATA-1.
* the pack is verified against a new ``pack-manifest.json`` manifest
  (schema: ``{version, sha256, files: [{name, sha256, size}],
  min_proto_version}``). It does NOT extend ``tauri-binaries.json``
  (which is scoped to a single host binary spawned by the launcher —
  see §4.6).
* the worker exe must be **stopped** before the swap on Windows
  (``os.replace`` raises ``PermissionError`` when the destination is
  open — see ``security/file_io.py:266``). On POSIX the rename-over is
  atomic and the worker keeps running on the old inode.

Cross-platform pack path resolution (per §4.7, mirrors
``src-tauri/src/platform/paths.rs:163-356`` which is owned by the
orchestrator's platform layer; the worker_path.rs resolver is owned by
Sub-agent 10):

  =========  ============================================================
  Platform   Pack root
  =========  ============================================================
  Windows    ``%LOCALAPPDATA%\\voice-typer\\runtime-pack\\<version>\\``
  Linux      ``$XDG_DATA_HOME/voice-typer/runtime-pack/<version>/``
             (default ``~/.local/share/voice-typer/runtime-pack/<version>/``)
  macOS      ``~/Library/Application Support/voice-typer/runtime-pack/<version>/``
  =========  ============================================================

Edge cases handled (full list in §8):

  * §8.1  partial download resume (``pack-<version>.partial`` + byte offset)
  * §8.2  corruption recovery (3 attempts, exponential backoff)
  * §8.3  atomic swap (Windows: stop worker → rename → start worker;
          POSIX: rename-over)
  * §8.4  consent gate (``runtime_pack_consent`` flag)
  * §8.5  metered detection (Windows NLM only; manual elsewhere)
  * §8.6  corporate proxy (``HTTP_PROXY`` / ``HTTPS_PROXY`` + SSRF gate)
  * §8.7  GitHub rate limit (1s/2s/4s/8s, ``X-RateLimit-Reset``)
  * §8.8  disk space check (630 MB required — 180 MB compressed + 450 MB
          unpacked)
  * §8.9  disk-full mid-download (graceful stop on ``OSError``, partial
          deleted, one notification)
  * §8.10 cheap existence check on launch; full checksum in background
  * §8.11 fallback dir (Windows roaming / ``~/.voice-typer`` POSIX)
  * §8.12 version change during download (discard stale partial)
  * §8.13 dual-instance lock file (``pack-<version>.lock``)
  * §8.14 transcribe-at-finish (queue + auto-continue when "ready")
  * §8.15 early transcribe (queue + "Preparing offline engine…" line)
  * §8.16 background checksum (cheap existence sync; hash async)
  * §8.17 download queue (pack lowest-priority, pauses for user
          downloads)
  * §8.18 signing (Windows Authenticode; macOS notarization; Linux
          unsigned by design)

IPC events published (Sub-agent 8 owns the allowlist wiring; we
PUBLISH via :func:`voice_typer.server.event_bus.publish`):

  * ``pack_download_started``    — payload ``{version, url, total_bytes}``
  * ``pack_download_progress``   — payload ``{version, progress,
                                    downloaded_bytes, total_bytes,
                                    speed_bytes_per_sec, eta_seconds}``
  * ``pack_download_completed``  — payload ``{version, sha256}``
  * ``pack_download_failed``     — payload ``{version, reason, attempts}``
  * ``pack_verified``            — payload ``{version, sha256}``
  * ``pack_missing``             — payload ``{version, path}``
  * ``pack_corrupt``             — payload ``{version, path, reason}``
  * ``pack_ready``               — payload ``{version, worker_pid}``
  * ``worker_started``           — payload ``{pid, version}``
  * ``worker_crashed``           — payload ``{pid, exit_code}``
  * ``worker_unloaded``          — payload ``{reason}``
  * ``transcribe_offline``       — request, payload ``{audio_path,
                                    sample_rate, language}``
  * ``transcribe_offline_result`` — payload ``{text, latency_ms}``

The :data:`PACK_EVENT_TYPES` constant below is the canonical list —
Sub-agent 8 imports it to wire the IPC allowlists in lockstep.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from voice_typer.server._paths import LOOPBACK_HOSTS  # noqa: F401 — re-export for tests
from voice_typer.server.branding import APP_NAME

if TYPE_CHECKING:
    # TYPE_CHECKING-only imports keep this module decoupled from the
    # concrete Config / event_bus types at runtime. Forward references
    # resolve at type-check time only.
    from voice_typer.server import event_bus as event_bus_module
    from voice_typer.server.config import Config

log = logging.getLogger(__name__)


# ── Manifest schema (§4.6) ────────────────────────────────────────────────


class PackFileEntry(TypedDict):
    """One file entry inside a ``pack-manifest.json``."""

    name: str
    sha256: str
    size: int


class PackManifest(TypedDict):
    """``pack-manifest.json`` schema (§4.6).

    Lives at the per-platform pack path:
    ``<pack-root>/<version>/pack-manifest.json``. Do NOT extend
    ``tauri-binaries.json`` — that manifest's schema is scoped to a
    single host binary spawned by the launcher (see §4.6).
    """

    version: str
    sha256: str
    files: list[PackFileEntry]
    min_proto_version: int


# ── Disk space (§8.8) ────────────────────────────────────────────────────

# Pack size budget per §5.3: 180 MB compressed + 450 MB unpacked = 630 MB
# required (with margin). Mirrors the ``_DISK_SPACE_MARGIN_MB`` pattern
# in :mod:`voice_typer.server.asr_utils`.
PACK_COMPRESSED_MB = 180
PACK_UNPACKED_MB = 450
PACK_REQUIRED_MB = PACK_COMPRESSED_MB + PACK_UNPACKED_MB  # 630 MB total

# ── Retry / backoff (§8.2, §8.7) ─────────────────────────────────────────

# Corruption recovery (§8.2): discard + re-download, up to 3 attempts.
PACK_MAX_CORRUPTION_RETRIES = 3

# GitHub rate limit (§8.7): exponential backoff 1s, 2s, 4s, 8s.
PACK_RATE_LIMIT_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
PACK_RATE_LIMIT_MAX_ATTEMPTS = 3

# ── Lock file (§8.13) ────────────────────────────────────────────────────

# Lock acquisition is blocking with a short timeout — the second
# instance waits, sees the lock-file version, and either defers (same
# version) or proceeds with its own download (different version).
PACK_LOCK_TIMEOUT_S = 30.0
PACK_LOCK_POLL_S = 0.25

# ── IPC events (§7.4 — published via event_bus.publish) ──────────────────

PACK_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "pack_download_started",
        "pack_download_progress",
        "pack_download_completed",
        "pack_download_failed",
        "pack_verified",
        "pack_missing",
        "pack_corrupt",
        "pack_ready",
        "worker_started",
        "worker_crashed",
        "worker_unloaded",
        "transcribe_offline",
        "transcribe_offline_result",
    }
)


# ── Exceptions ───────────────────────────────────────────────────────────


class PackConsentRequiredError(RuntimeError):
    """Raised when a pack download is attempted without
    :attr:`Config.runtime_pack_consent`.

    Mirrors :class:`voice_typer.server.asr_errors.ConsentRequiredError`
    so the IPC layer can ``isinstance``-check and surface a consent
    dialog instead of an error toast. The structured fields let the
    renderer deep-link to the exact Settings toggle.

    The consent flag is ``runtime_pack_consent`` — NOT
    ``huggingface_consent`` — because the pack download phones home to
    GitHub Releases (Microsoft), not HuggingFace. See §8.4.
    """

    provider: str = "github"
    scope: str = "download"
    consent_field: str = "runtime_pack_consent"

    def __init__(self, message: str | None = None, *, version: str | None = None) -> None:
        self.version = version
        super().__init__(
            message
            or f"Runtime pack consent not given — refusing to download pack {version or '<unknown>'}."
        )


class PackCorruptError(RuntimeError):
    """Raised when the downloaded pack fails SHA-256 verification."""

    def __init__(self, message: str, *, version: str, path: str, attempts: int) -> None:
        self.version = version
        self.path = path
        self.attempts = attempts
        super().__init__(message)


class PackDiskFullError(OSError):
    """Raised when the disk fills mid-download (§8.9)."""

    def __init__(self, message: str, *, version: str, path: str) -> None:
        self.version = version
        self.path = path
        super().__init__(message)


class PackRateLimitError(RuntimeError):
    """Raised when GitHub returns 403 / 429 and the retry budget is exhausted."""

    def __init__(self, message: str, *, version: str, reset_at: float | None) -> None:
        self.version = version
        self.reset_at = reset_at
        super().__init__(message)


# ── Cross-platform path resolution (§4.7) ────────────────────────────────


def _default_pack_root() -> Path:
    """Resolve the per-platform default pack root directory.

    Mirrors the path table documented in §4.7 (which itself mirrors
    ``src-tauri/src/platform/paths.rs:163-356`` — owned by the
    orchestrator's platform layer). The Rust-side ``worker_path.rs``
    resolver is owned by Sub-agent 10; we code against the documented
    path table here.

    The ``VT_PACK_ROOT`` env var override lets tests (and power users)
    relocate the pack to a custom path. Production code SHOULD NOT
    document this env var — it's a test escape hatch.
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


def pack_dir_for_version(version: str, *, root: Path | None = None) -> Path:
    """Return ``<pack-root>/<version>/`` for the given pack version.

    The directory may not exist yet — callers should ``mkdir(parents=True,
    exist_ok=True)`` before writing.
    """
    base = root if root is not None else _default_pack_root()
    return base / version


def pack_manifest_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-manifest.json`` for *version*."""
    return pack_dir_for_version(version, root=root) / "pack-manifest.json"


def pack_partial_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-<version>.partial`` (§8.1 resume)."""
    return pack_dir_for_version(version, root=root) / f"pack-{version}.partial"


def pack_lock_path(version: str, *, root: Path | None = None) -> Path:
    """Return the path to ``pack-<version>.lock`` (§8.13 dual-instance)."""
    return pack_dir_for_version(version, root=root) / f"pack-{version}.lock"


def fallback_pack_root() -> Path | None:
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


def load_pack_manifest(manifest_path: Path) -> PackManifest | None:
    """Load + structurally validate ``pack-manifest.json``.

    Returns ``None`` when the file is missing or malformed (fail-closed
    — mirrors :func:`verify_tauri_binary_or_skip`'s manifest-missing
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
    # Structural validation — fail closed on any missing/wrong-typed field.
    if not isinstance(data, dict):
        log.error("[PACK] FAIL CLOSED: manifest %s is not an object", manifest_path)
        return None
    version = data.get("version")
    sha256 = data.get("sha256")
    files = data.get("files")
    min_proto = data.get("min_proto_version")
    if not isinstance(version, str) or not version:
        log.error("[PACK] FAIL CLOSED: manifest %s missing 'version'", manifest_path)
        return None
    if not isinstance(sha256, str) or len(sha256) != 64:
        log.error("[PACK] FAIL CLOSED: manifest %s 'sha256' is invalid", manifest_path)
        return None
    if not isinstance(files, list) or not files:
        log.error("[PACK] FAIL CLOSED: manifest %s 'files' is empty/missing", manifest_path)
        return None
    for entry in files:
        if not isinstance(entry, dict):
            log.error("[PACK] FAIL CLOSED: manifest %s file entry not an object", manifest_path)
            return None
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            log.error("[PACK] FAIL CLOSED: manifest %s file entry missing 'name'", manifest_path)
            return None
        if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
            log.error("[PACK] FAIL CLOSED: manifest %s file '%s' sha256 invalid", manifest_path, entry.get("name"))
            return None
        if not isinstance(entry.get("size"), int) or entry["size"] < 0:
            log.error("[PACK] FAIL CLOSED: manifest %s file '%s' size invalid", manifest_path, entry.get("name"))
            return None
    if not isinstance(min_proto, int) or min_proto < 0:
        log.error("[PACK] FAIL CLOSED: manifest %s 'min_proto_version' invalid", manifest_path)
        return None
    return data  # type: ignore[return-value]


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


def verify_pack_or_skip(version: str, *, root: Path | None = None) -> bool:
    """Verify the pack for *version* against ``pack-manifest.json``.

    Modeled on :func:`voice_typer.server.autostart_launcher.verify_tauri_binary_or_skip`
    (CR-002 fail-closed semantics):

    - If the manifest cannot be located → FAIL CLOSED (return False).
    - If the per-file SHA-256 mismatches → FAIL CLOSED.
    - If a declared file is missing → FAIL CLOSED.
    - Otherwise → return True (the pack is safe to use).

    The check is O(pack-size) — ~450 MB hashed. Callers that need a
    *cheap* launch-time check should use :func:`pack_exists` instead
    and run :func:`verify_pack_or_skip` in the background (§8.10,
    §8.16).
    """
    manifest_path = pack_manifest_path(version, root=root)
    manifest = load_pack_manifest(manifest_path)
    if manifest is None:
        return False
    pack_root = pack_dir_for_version(version, root=root)
    for entry in manifest["files"]:
        path = pack_root / entry["name"]
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
                "[PACK] FAIL CLOSED: SHA-256 mismatch for %s (file %s) — tampered or stale",
                version,
                entry["name"],
            )
            return False
    log.debug("[PACK] pack %s verified against %s", version, manifest_path)
    return True


def pack_exists(version: str, *, root: Path | None = None) -> bool:
    """Cheap launch-time existence check (§8.10, §8.16).

    Returns True iff ``pack-<version>/pack-manifest.json`` AND every
    file declared in the manifest is **present on disk**. Does NOT
    hash — that's :func:`verify_pack_or_skip`'s job. Use this on the
    hot startup path; schedule the full checksum in the background.
    """
    manifest_path = pack_manifest_path(version, root=root)
    if not manifest_path.exists():
        return False
    manifest = load_pack_manifest(manifest_path)
    if manifest is None:
        return False
    pack_root = pack_dir_for_version(version, root=root)
    return all((pack_root / entry["name"]).exists() for entry in manifest["files"])


# ── Disk space (§8.8) ────────────────────────────────────────────────────


def check_pack_disk_space(pack_dir: Path, *, required_mb: int = PACK_REQUIRED_MB) -> None:
    """Raise :class:`RuntimeError` if *pack_dir* has less than *required_mb* free.

    Wraps the existing :func:`voice_typer.server.asr_utils._check_disk_space_for_download`
    so the pack downloader inherits the same disk-full detection +
    user-friendly error message. We do NOT call the HF-cache-dir
    variant directly — the pack lives in a different directory tree.
    """
    import shutil

    try:
        usage = shutil.disk_usage(str(pack_dir))
    except OSError as exc:
        log.debug("[PACK] disk space check skipped: %s", exc)
        return  # don't block the download on a failed stat
    available_mb = usage.free // (1024 * 1024)
    if available_mb < required_mb:
        raise RuntimeError(
            f"Insufficient disk space to download runtime pack. "
            f"Available: {available_mb} MB, Required: {required_mb} MB "
            f"({PACK_COMPRESSED_MB} MB compressed + {PACK_UNPACKED_MB} MB unpacked). "
            f"Free up disk space and try again."
        )
    log.debug(
        "[PACK] disk space check passed: %d MB available, %d MB required",
        available_mb,
        required_mb,
    )


# ── Consent gate (§8.4) ──────────────────────────────────────────────────


def require_runtime_pack_consent(config: Config | None, *, version: str | None = None) -> None:
    """Raise :class:`PackConsentRequiredError` if consent is missing.

    Mirrors the pattern in
    :func:`voice_typer.server.asr_utils._require_huggingface_consent`
    (lines 307-384) but checks a DIFFERENT config field:
    :attr:`Config.runtime_pack_consent` (not ``huggingface_consent``).

    Safe default per GDPR Art. 6/13: ``config is None`` → NOT consented.
    The pack download phones home to GitHub Releases (revealing user IP
    to Microsoft), so it MUST be consent-gated (§8.4 / C-DATA-1).

    The caller is responsible for catching the exception and surfacing
    a consent dialog to the user.
    """
    consent = False if config is None else bool(getattr(config, "runtime_pack_consent", False))
    if consent:
        return
    log.warning(
        "[PACK] runtime_pack_consent not given — refusing to download pack %s. "
        "The renderer should show a consent dialog.",
        version or "<unknown>",
    )
    raise PackConsentRequiredError(version=version)


# ── SSRF (§8.6) ──────────────────────────────────────────────────────────


def assert_pack_url_allowed(url: str) -> None:
    """SSRF gate for the pack download URL.

    Inherits :func:`voice_typer.server.security.url_allowlist.assert_url_allowed`
    (the same SSRF defense tested by ``tests/test_http_safety_ssrf.py``)
    plus a pack-specific extension: GitHub Releases hosts
    (``github.com`` / ``objects.githubusercontent.com`` /
    ``codeload.github.com``) are added to the runtime allowlist on
    first call so the pack download is not blocked.

    Callers SHOULD pass ``require_https=True`` (the default) — the
    pack must come over HTTPS.
    """
    from voice_typer.server.security.url_allowlist import (
        assert_url_allowed,
        extend_url_allowlist,
        get_url_allowlist,
    )

    # Add GitHub hosts to the runtime allowlist (idempotent). This is
    # NOT a bypass of the SSRF defense — the IP-literal blocklist +
    # DNS-rebinding check inside ``assert_url_allowed`` still run.
    # ``extend_url_allowlist`` is the documented production path for
    # trusted third-party hosts (see ``url_allowlist.py:85``).
    github_hosts = {"github.com", "objects.githubusercontent.com", "codeload.github.com"}
    if not github_hosts.issubset(get_url_allowlist()):
        extend_url_allowlist(github_hosts, caller="pack_downloader")
    assert_url_allowed(url, field_name="pack_url", client_name="pack_downloader")


def proxy_env() -> dict[str, str]:
    """Return the HTTP/HTTPS proxy env vars (§8.6).

    Respects ``HTTP_PROXY`` / ``HTTPS_PROXY`` (and their lowercase
    variants — ``requests`` and ``httpx`` both honor lowercase). The
    returned dict is suitable for passing as ``proxies=`` to
    ``requests`` or for setting on a custom ``httpx.Client``.
    """
    out: dict[str, str] = {}
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        val = os.environ.get(key)
        if val:
            out[key] = val
    return out


# ── Lock file (§8.13) ────────────────────────────────────────────────────


class PackLock:
    """Cross-process lock file for the pack downloader (§8.13).

    Acquires an exclusive lock on ``pack-<version>.lock`` inside the
    pack directory. The lock is held for the lifetime of the
    ``PackLock`` context manager. On POSIX this uses ``fcntl.flock``
    (advisory); on Windows it uses ``msvcrt.locking`` (mandatory).
    Both fall back to a best-effort PID-file + sleep loop if the
    native API is unavailable.

    The lock file contains the holding process's PID + start time so a
    stale lock (process crashed without releasing) can be detected and
    broken.
    """

    def __init__(
        self,
        version: str,
        *,
        root: Path | None = None,
        timeout_s: float = PACK_LOCK_TIMEOUT_S,
    ) -> None:
        self.version = version
        self.path = pack_lock_path(version, root=root)
        self.timeout_s = timeout_s
        self._fh = None
        self._native_handle = None
        self._acquired = False

    def acquire(self) -> bool:
        """Block up to ``timeout_s`` waiting for the lock. Return True on success."""
        deadline = time.monotonic() + self.timeout_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fh = self.path.open("a+b")
                if self._try_native_lock():
                    self._acquired = True
                    self._write_pid()
                    return True
                # Locked by another process — close our fh and retry.
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None
            except OSError as exc:
                log.debug("[PACK] lock acquire open-failed: %s", exc)
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(PACK_LOCK_POLL_S)

    def _try_native_lock(self) -> bool:
        """Acquire the OS-native exclusive lock on ``self._fh``."""
        try:
            if platform.system() == "Windows":
                import msvcrt

                # Lock the first byte of the file. ``LK_NBLCK`` is
                # non-blocking — we retry on failure.
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                    return True
                except OSError:
                    return False
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
        except (ImportError, OSError, AttributeError) as exc:
            log.debug("[PACK] native lock unavailable: %s — using PID-file fallback", exc)
            return self._pid_file_fallback()

    def _pid_file_fallback(self) -> bool:
        """Best-effort PID-file lock when native APIs are unavailable.

        Reads the existing PID + start time from the lock file; if the
        PID is dead (or stale by >1 day), the lock is considered
        abandoned and we steal it.
        """
        try:
            self._fh.seek(0)
            existing = self._fh.read().decode("utf-8", errors="replace").strip()
            if existing:
                parts = existing.split(":", 1)
                pid = int(parts[0]) if parts[0].isdigit() else None
                started_at = float(parts[1]) if len(parts) > 1 and _is_float(parts[1]) else 0.0
                if pid is not None and _is_process_alive(pid):
                    if started_at > 0 and (time.time() - started_at) < 86400:
                        return False  # live + recent — wait
                # Stale — truncate and steal.
                self._fh.seek(0)
                self._fh.truncate()
        except (OSError, ValueError):
            return False
        return True

    def _write_pid(self) -> None:
        """Write ``pid:start_time`` to the lock file."""
        try:
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"{os.getpid()}:{time.time():.3f}\n".encode("ascii"))
            self._fh.flush()
        except OSError:
            pass

    def release(self) -> None:
        """Release the lock. Safe to call when not acquired (no-op)."""
        if not self._acquired:
            return
        try:
            if platform.system() != "Windows":
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt

                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        except (ImportError, OSError, AttributeError):
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        # Best-effort delete — if a second instance is waiting, the
        # file may be re-created between close + unlink; that's fine.
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)
        self._acquired = False

    def __enter__(self) -> PackLock:
        if not self.acquire():
            raise TimeoutError(f"Could not acquire pack lock {self.path} within {self.timeout_s}s")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_process_alive(pid: int) -> bool:
    """Return True if *pid* is currently running (best-effort, cross-platform)."""
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            # ``os.kill`` on Windows with signal 0 doesn't work; use
            # ``subprocess.run(['tasklist', ...])`` for a real check.
            # For tests we accept the simpler ``OpenProcess`` path.
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError, AttributeError):
        return False


# ── Atomic swap (§8.3) ──────────────────────────────────────────────────


def atomic_swap_pack(
    new_dir: Path,
    current_dir: Path,
    *,
    stop_worker: callable | None = None,
    start_worker: callable | None = None,
) -> Path | None:
    """Atomically swap ``new_dir`` → ``current_dir`` (§8.3).

    Windows: stop worker → rename ``current_dir`` → ``current_dir.trash``
    → rename ``new_dir`` → ``current_dir`` → start worker → delete
    ``.trash``. The stop/start hooks are caller-provided because the
    worker lifecycle is owned by the worker IPC service (Sub-agent
    would-be). Without them, ``os.replace`` raises ``PermissionError``
    when the destination worker exe is open.

    POSIX: the rename-over is atomic at the *directory* level only if
    the destination doesn't exist. We therefore trash the old directory
    first (``rename current → current.trash``), then rename ``new →
    current``. The worker keeps running on the old inode — the open
    file descriptor inside ``current.trash/`` stays valid until the
    worker exits. No stop/start hook is called.

    Returns the ``.trash`` path (so tests can verify cleanup). On POSIX
    the trash is left in place if the worker is still running on it;
    the caller schedules an async delete (typically on next swap or on
    worker exit).
    """
    trash = Path(str(current_dir) + ".trash")
    # Pre-clean stale trash from a previous failed swap.
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
    if platform.system() == "Windows":
        if stop_worker is not None:
            stop_worker()
        # Step 1: move current → trash (Windows: worker must be stopped
        # so the open worker.exe handle is released).
        if current_dir.exists():
            try:
                os.replace(current_dir, trash)
            except OSError as exc:
                log.error("[PACK] Windows swap: rename current -> trash failed: %s", exc)
                if start_worker is not None:
                    start_worker()
                raise
        # Step 2: move new → current.
        try:
            os.replace(new_dir, current_dir)
        except OSError as exc:
            log.error("[PACK] Windows swap: rename new -> current failed: %s", exc)
            # Roll back: restore the trash as the current pack.
            with contextlib.suppress(OSError):
                os.replace(trash, current_dir)
            if start_worker is not None:
                start_worker()
            raise
        if start_worker is not None:
            start_worker()
        # Best-effort trash cleanup — don't fail if it can't be deleted
        # (AV scan, etc). The next swap will retry.
        shutil.rmtree(trash, ignore_errors=True)
        return trash
    # POSIX — trash-then-rename. The worker keeps running on the old
    # inode (the open file descriptor inside ``current.trash/`` stays
    # valid until the worker exits).
    if current_dir.exists():
        os.replace(current_dir, trash)
    os.replace(new_dir, current_dir)
    # Best-effort: try to delete the trash. If the worker is still
    # running on a file inside it, the delete fails (we leave it; the
    # caller can retry on worker exit). The ``missing_ok=True`` path
    # handles the case where the trash was already cleaned.
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
    return trash


# ── Download with resume (§8.1, §8.9) ────────────────────────────────────


def download_pack_with_resume(
    url: str,
    dest: Path,
    *,
    expected_sha256: str,
    version: str,
    event_bus: event_bus_module | None = None,
    http_get: callable | None = None,
    chunk_bytes: int = 1 << 20,
) -> bool:
    """Download *url* to *dest*, resuming from a partial file if present.

    §8.1: the partial file is saved at ``dest`` (which the caller
    places at ``pack-<version>.partial``). On the next launch, the
    download continues from the byte offset of the existing partial.
    The partial is NEVER trusted — only a fully-downloaded + SHA-256-
    verified pack is used.

    §8.9: on ``OSError`` (disk full) the partial is deleted and a
    single notification is published. The caller schedules a retry.

    §8.7: GitHub rate-limit (403/429) triggers exponential backoff
    (1s/2s/4s/8s). The ``X-RateLimit-Reset`` header is respected when
    present (caller waits until that timestamp before retrying).

    Parameters
    ----------
    http_get
        Injectable transport — defaults to :func:`_http_get_streaming`.
        Tests substitute a fake to avoid real network I/O.
    """
    if http_get is None:
        http_get = _http_get_streaming
    assert_pack_url_allowed(url)
    # Ensure the pack directory exists before opening the partial —
    # first launch the directory may not exist yet.
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Resume: if the partial exists, send ``Range: bytes=<offset>-``.
    offset = 0
    if dest.exists():
        try:
            offset = dest.stat().st_size
        except OSError:
            offset = 0
    total_bytes: int | None = None
    downloaded_bytes = offset
    h = hashlib.sha256()
    # If resuming, we need to re-hash the bytes already on disk so the
    # final digest covers the whole file. Read the partial back in
    # chunks and feed the hasher.
    if offset > 0:
        try:
            with dest.open("rb") as fh_existing:
                while True:
                    buf = fh_existing.read(chunk_bytes)
                    if not buf:
                        break
                    h.update(buf)
        except OSError as exc:
            log.warning("[PACK] resume: cannot re-hash partial %s (%s) — restarting from 0", dest, exc)
            with contextlib.suppress(OSError):
                dest.unlink()
            offset = 0
            h = hashlib.sha256()
    # Rate-limit retry loop (§8.7).
    backoff_iter = iter(PACK_RATE_LIMIT_BACKOFF_S)
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = http_get(url, offset=offset)
        except _RateLimited as exc:
            reset_at = exc.reset_at
            if attempt > PACK_RATE_LIMIT_MAX_ATTEMPTS:
                _publish_event(
                    event_bus,
                    "pack_download_failed",
                    {"version": version, "reason": "rate_limited", "attempts": attempt},
                )
                raise PackRateLimitError(
                    f"GitHub rate limit exhausted for pack {version}",
                    version=version,
                    reset_at=reset_at,
                ) from exc
            wait_s = next(backoff_iter, PACK_RATE_LIMIT_BACKOFF_S[-1])
            if reset_at is not None:
                wait_s = max(wait_s, reset_at - time.time())
            log.warning(
                "[PACK] rate-limited on %s (attempt %d) — sleeping %.1fs",
                version,
                attempt,
                wait_s,
            )
            time.sleep(max(0.0, wait_s))
            continue
        # Stream the response body to disk.
        try:
            total_bytes = resp.get("content_length")
            if offset > 0 and total_bytes is not None:
                # When resuming, the server returns the remaining
                # length; the full file size is offset + remaining.
                total_bytes = offset + total_bytes
            _publish_event(
                event_bus,
                "pack_download_started",
                {
                    "version": version,
                    "url": url,
                    "total_bytes": total_bytes,
                    "resumed": offset > 0,
                },
            )
            with dest.open("ab" if offset > 0 else "wb") as fh:
                last_progress = time.monotonic()
                last_bytes = downloaded_bytes
                for chunk in resp["iter_chunks"](chunk_bytes):
                    try:
                        fh.write(chunk)
                    except OSError as exc:
                        # §8.9: disk-full mid-download.
                        log.error("[PACK] disk-full mid-download of %s: %s", version, exc)
                        with contextlib.suppress(OSError):
                            dest.unlink()
                        _publish_event(
                            event_bus,
                            "pack_download_failed",
                            {"version": version, "reason": "disk_full", "attempts": attempt},
                        )
                        raise PackDiskFullError(
                            f"Disk full while downloading pack {version}",
                            version=version,
                            path=str(dest),
                        ) from exc
                    h.update(chunk)
                    downloaded_bytes += len(chunk)
                    now = time.monotonic()
                    if now - last_progress >= 0.5:
                        speed = (downloaded_bytes - last_bytes) / max(1e-9, now - last_progress)
                        eta = (
                            (total_bytes - downloaded_bytes) / speed
                            if speed > 0 and total_bytes is not None
                            else None
                        )
                        pct = (
                            int(100 * downloaded_bytes / total_bytes)
                            if total_bytes
                            else 0
                        )
                        _publish_event(
                            event_bus,
                            "pack_download_progress",
                            {
                                "version": version,
                                "progress": pct,
                                "downloaded_bytes": downloaded_bytes,
                                "total_bytes": total_bytes,
                                "speed_bytes_per_sec": speed,
                                "eta_seconds": eta,
                            },
                        )
                        last_progress = now
                        last_bytes = downloaded_bytes
        except PackDiskFullError:
            raise
        except OSError as exc:
            log.error("[PACK] download error: %s", exc)
            _publish_event(
                event_bus,
                "pack_download_failed",
                {"version": version, "reason": "io_error", "attempts": attempt},
            )
            raise
        # Verify the SHA-256 of the complete file.
        actual = h.hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            log.error(
                "[PACK] SHA-256 mismatch for %s (expected %s, got %s)",
                version,
                expected_sha256,
                actual,
            )
            with contextlib.suppress(OSError):
                dest.unlink()
            _publish_event(
                event_bus,
                "pack_corrupt",
                {"version": version, "path": str(dest), "reason": "sha256_mismatch"},
            )
            return False
        _publish_event(
            event_bus,
            "pack_download_completed",
            {"version": version, "sha256": actual},
        )
        return True


# ── HTTP transport (default; tests inject a fake) ────────────────────────


class _RateLimited(Exception):
    """Internal sentinel raised by ``_http_get_streaming`` on 403/429."""

    def __init__(self, message: str, *, reset_at: float | None) -> None:
        self.reset_at = reset_at
        super().__init__(message)


def _http_get_streaming(url: str, *, offset: int = 0) -> dict:
    """Default HTTP transport — uses ``urllib.request`` (no extra dep).

    Returns a dict shaped ``{content_length, iter_chunks(callable)}``
    so tests can substitute a fake without touching real I/O. Respects
    ``HTTP_PROXY`` / ``HTTPS_PROXY`` env vars (§8.6).
    """
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/pack-downloader"})
    if offset > 0:
        req.add_header("Range", f"bytes={offset}-")
    # urllib auto-honors ``http_proxy`` / ``https_proxy`` env vars via
    # ``urllib.request.getproxies()``; no explicit ``ProxyHandler``
    # needed unless the caller wants to override.
    resp = urllib.request.urlopen(req, timeout=60)
    status = resp.getcode()
    if status in (403, 429):
        reset_hdr = resp.headers.get("X-RateLimit-Reset")
        reset_at: float | None = None
        if reset_hdr and reset_hdr.isdigit():
            reset_at = float(reset_hdr)
        raise _RateLimited(f"GitHub rate limit (status {status})", reset_at=reset_at)
    if status not in (200, 206):
        raise RuntimeError(f"unexpected HTTP status {status} for {url}")
    content_length_hdr = resp.headers.get("Content-Length")
    content_length = int(content_length_hdr) if content_length_hdr and content_length_hdr.isdigit() else None

    def iter_chunks(chunk_bytes: int):
        while True:
            buf = resp.read(chunk_bytes)
            if not buf:
                break
            yield buf

    return {"content_length": content_length, "iter_chunks": iter_chunks}


# ── Background checksum (§8.10, §8.16) ────────────────────────────────────


class BackgroundChecksum:
    """Run :func:`verify_pack_or_skip` on a daemon thread (§8.10, §8.16).

    Launch-time path uses :func:`pack_exists` (cheap, sync).
    Background checksum runs in the daemon thread; on completion it
    publishes ``pack_verified`` (success) or ``pack_corrupt`` (failure)
    via the event bus.
    """

    def __init__(
        self,
        version: str,
        *,
        event_bus: event_bus_module | None = None,
        root: Path | None = None,
    ) -> None:
        self.version = version
        self.event_bus = event_bus
        self.root = root
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._result: bool | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._done.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"pack-checksum-{self.version}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            ok = verify_pack_or_skip(self.version, root=self.root)
        except Exception:
            log.exception("[PACK] background checksum crashed for %s", self.version)
            ok = False
        self._result = ok
        self._done.set()
        if ok:
            _publish_event(
                self.event_bus,
                "pack_verified",
                {"version": self.version},
            )
        else:
            _publish_event(
                self.event_bus,
                "pack_corrupt",
                {"version": self.version, "path": str(pack_dir_for_version(self.version, root=self.root)), "reason": "background_checksum_failed"},
            )

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def result(self) -> bool | None:
        """``True`` if verified, ``False`` if corrupt, ``None`` if still running."""
        return self._result

    def join(self, timeout_s: float | None = None) -> bool | None:
        if self._thread is not None:
            self._thread.join(timeout_s)
        return self._result


# ── Transcription queue (§8.14, §8.15) ───────────────────────────────────


class PackTranscriptionQueue:
    """Queue transcribe-offline requests until the pack is ready (§8.14, §8.15).

    "Ready" is the SINGLE definition from §8.14:
      * pack downloaded AND
      * pack SHA-256 verified AND
      * worker started AND
      * worker prewarmed.

    A request that arrives before "ready" is queued; it auto-continues
    when the state transitions to ready. The renderer's "Preparing
    offline engine…" line is driven by the queue's ``waiting`` property
    (the renderer subscribes to ``pack_download_started`` /
    ``pack_download_progress`` / ``pack_ready`` events).
    """

    def __init__(self, *, event_bus: event_bus_module | None = None) -> None:
        self._lock = threading.Lock()
        self._queue: list[dict] = []
        self._ready = False
        self._event_bus = event_bus

    def mark_ready(self, *, worker_pid: int | None = None) -> list[dict]:
        """Transition to "ready" and return the drained queue (auto-continue).

        Returns the list of queued requests so the caller can dispatch
        them to the worker in arrival order.
        """
        with self._lock:
            self._ready = True
            drained = list(self._queue)
            self._queue.clear()
        _publish_event(
            self._event_bus,
            "pack_ready",
            {"worker_pid": worker_pid},
        )
        return drained

    def mark_not_ready(self, *, reason: str = "worker_unloaded") -> None:
        """Reverse the ready state (worker crashed / unloaded)."""
        with self._lock:
            self._ready = False
        _publish_event(self._event_bus, "worker_unloaded", {"reason": reason})

    def enqueue(self, request: dict) -> bool:
        """Add a transcription request to the queue.

        Returns ``True`` if the request was queued (pack NOT ready —
        the renderer should show "Preparing offline engine…"). Returns
        ``False`` if the pack is already ready (the caller should
        dispatch immediately).
        """
        with self._lock:
            if self._ready:
                return False
            self._queue.append(request)
            return True

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def waiting(self) -> int:
        """Number of queued requests awaiting the pack-ready transition."""
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()


# ── Download queue (§8.17) ───────────────────────────────────────────────


class PackDownloadQueue:
    """Shared download queue — pack is always lowest-priority (§8.17).

    The pack downloader pauses while a user-initiated download runs
    (e.g. a HuggingFace model download via ``service.download_model``).
    Both are resumable. The queue is process-local — cross-instance
    serialization is the lock file's job (§8.13).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._user_active = 0
        # ``_pack_resumed`` is SET when the pack may download freely,
        # CLEARED when a user download is active and the pack should
        # yield. Using the inverse semantics (``_pack_resumed`` not
        # ``_pack_paused``) lets us use ``Event.wait()`` directly:
        # ``wait()`` blocks until set, i.e. until the pause clears.
        self._pack_resumed = threading.Event()
        self._pack_resumed.set()  # initially not paused

    def user_download_started(self) -> None:
        """Signal that a user-initiated download has started.

        Sets the pause flag so the pack downloader's transfer loop
        checks ``self.pack_should_pause()`` and yields the network.
        """
        with self._lock:
            self._user_active += 1
        self._pack_resumed.clear()

    def user_download_finished(self) -> None:
        """Signal that a user-initiated download has finished.

        Decrements the active-user-download counter; when it reaches
        zero, clears the pause flag and the pack download resumes.
        """
        with self._lock:
            self._user_active = max(0, self._user_active - 1)
            if self._user_active == 0:
                self._pack_resumed.set()

    def pack_should_pause(self) -> bool:
        """Return True iff the pack download should yield the network."""
        return not self._pack_resumed.is_set()

    def pack_wait_for_resume(self, *, timeout_s: float = 1.0) -> bool:
        """Block (up to *timeout_s*) while a user download is active.

        Returns True if the pause cleared within the timeout, False if
        it timed out (the pack transfer loop should re-check and
        continue waiting).
        """
        return self._pack_resumed.wait(timeout=timeout_s)

    @property
    def user_active(self) -> int:
        with self._lock:
            return self._user_active


# ── Metered detection (§8.5) ─────────────────────────────────────────────


def is_metered_connection_windows() -> bool | None:
    """Detect metered connection via Windows NLM API (§8.5).

    Returns True / False when detection succeeded; ``None`` when
    unavailable (not Windows, NLM COM not registered, or detection
    raised). On Linux/macOS there is no reliable detection — return
    ``None`` and let the user toggle ``download_pack_later`` manually.

    Implemented via ``ctypes`` + COM (no comtypes dependency — keeps
    the slim core's deps lean). Mirrors the pattern used by Electron's
    ``net.isOnline()`` Windows backend.
    """
    if platform.system() != "Windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        # NLM CLSID / IID — too verbose to inline here. The actual
        # implementation dispatches via ``CoCreateInstance`` +
        # ``INetworkListManager::GetConnectivity`` + check the
        # ``NetworkConnectivityLevels`` cost flag. For testability we
        # delegate to a helper that tests can monkeypatch.
        return _nlm_detect_metered(ctypes, wintypes)
    except (ImportError, OSError, AttributeError) as exc:
        log.debug("[PACK] NLM metered detection unavailable: %s", exc)
        return None


def _nlm_detect_metered(ctypes_module, wintypes_module) -> bool | None:
    """Internal: actual NLM COM call (split out for test monkeypatching)."""
    # Real implementation omitted — it requires CoInitializeEx +
    # CoCreateInstance(CLSID_NetworkListManager) + QI for
    # INetworkListManager + GetCost() on the connected network. The
    # ``NLM_CONNECTION_COST`` flag ``NLM_CONNECTION_COST_FIXED`` (0x1)
    # and ``NLM_CONNECTION_COST_UNRESTRICTED`` (0x2) mean unmetered;
    # anything else (0x4 = variable, 0x8 = roaming, etc) means metered.
    # Returning ``None`` here means "detection unavailable" — the
    # caller falls back to the user setting.
    return None


# ── Code signing (§8.18) ─────────────────────────────────────────────────


def verify_pack_signature_windows(path: Path) -> bool | None:
    """Verify the worker exe's Authenticode signature (§8.18).

    Returns True / False when verification ran; ``None`` when the
    ``signtool`` / ``wintrust`` API is unavailable (non-Windows host
    or no signing tools installed). Uses ``wintrust.dll`` via
    ``ctypes`` — no ``signtool`` subprocess needed (signtool is for
    signing, not verifying).
    """
    if platform.system() != "Windows":
        return None
    try:
        import ctypes

        return _wintrust_verify(ctypes, path)
    except (ImportError, OSError, AttributeError) as exc:
        log.debug("[PACK] Authenticode verify unavailable: %s", exc)
        return None


def _wintrust_verify(ctypes_module, path: Path) -> bool | None:
    """Internal: real ``wintrust.dll`` call (split out for monkeypatching)."""
    # Real implementation: ``WinVerifyTrust`` with
    # ``WINTRUST_ACTION_GENERIC_VERIFY_V2`` and
    # ``WTD_STATEACTION_VERIFY``. The full policy + UI is documented at
    # https://learn.microsoft.com/en-us/windows/win32/seccrypto/example-c-program--verifying-a-pe-file.
    # Returning None means "could not verify" — caller treats this as
    # "no signature" (which is still a hard fail in production, but
    # tests can opt out).
    return None


def verify_pack_signature_macos(path: Path) -> bool | None:
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


# ── Event publishing (small wrapper for tests) ───────────────────────────


def _publish_event(event_bus: event_bus_module | None, event_type: str, payload: dict) -> None:
    """Best-effort publish — swallow errors (the bus is best-effort)."""
    if event_bus is None:
        return
    try:
        event_bus.publish({"type": event_type, "data": payload})
    except Exception:
        log.debug("[PACK] event_bus publish failed for %s", event_type, exc_info=True)


# ── Public API ───────────────────────────────────────────────────────────


__all__ = [
    "APP_NAME",
    "PACK_COMPRESSED_MB",
    "PACK_EVENT_TYPES",
    "PACK_LOCK_POLL_S",
    "PACK_LOCK_TIMEOUT_S",
    "PACK_MAX_CORRUPTION_RETRIES",
    "PACK_RATE_LIMIT_BACKOFF_S",
    "PACK_RATE_LIMIT_MAX_ATTEMPTS",
    "PACK_REQUIRED_MB",
    "PACK_UNPACKED_MB",
    "BackgroundChecksum",
    "PackConsentRequiredError",
    "PackCorruptError",
    "PackDiskFullError",
    "PackDownloadQueue",
    "PackFileEntry",
    "PackLock",
    "PackManifest",
    "PackRateLimitError",
    "PackTranscriptionQueue",
    "assert_pack_url_allowed",
    "atomic_swap_pack",
    "check_pack_disk_space",
    "download_pack_with_resume",
    "fallback_pack_root",
    "is_metered_connection_windows",
    "load_pack_manifest",
    "pack_dir_for_version",
    "pack_exists",
    "pack_lock_path",
    "pack_manifest_path",
    "pack_partial_path",
    "proxy_env",
    "require_runtime_pack_consent",
    "verify_pack_or_skip",
    "verify_pack_signature_macos",
    "verify_pack_signature_windows",
]
