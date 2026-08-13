"""Auto-update — pack-version checker (plan-runtime-pack-split.md §10).

On launch, the slim core fetches the latest ``pack-manifest.json`` from
the GitHub Releases URL (with consent — §8.4). If the version is newer
than the local pack, a background download is triggered via
:mod:`voice_typer.server.service.pack` (Sub-agent 7's module — we call
its PUBLIC API only, never edit it).

This module is the auto-update counterpart to ``service/pack.py``.
``pack.py`` owns the *download* mechanics (resume, checksum, lock file,
proxy env, SSRF gate); this module owns the *check* mechanics (fetch the
remote manifest, compare versions, decide whether to trigger). The two
are intentionally separate so Sub-agent 7's pack downloader can be
tested / iterated without coupling to the version-check logic.

Security inheritance (per §10.1):
  * SSRF protection — delegates to
    :func:`voice_typer.server.service.pack.assert_pack_url_allowed`,
    which extends the runtime URL allowlist with the GitHub Releases
    hosts (``github.com`` / ``objects.githubusercontent.com`` /
    ``codeload.github.com``) AND inherits the IP-literal blocklist +
    DNS-rebinding defense from
    :func:`voice_typer.server.security.url_allowlist.assert_url_allowed`
    (the same SSRF defense tested by ``tests/test_http_safety_ssrf.py``).
  * Max-bytes limit — the remote manifest is downloaded via the injectable
    ``http_get`` transport, then parsed via
    :func:`voice_typer.server.secure_file_io._secure_read_text` with
    ``max_bytes=MAX_MANIFEST_BYTES`` (1 MiB — manifests are tiny JSON; a
    malicious server returning a multi-GB body would otherwise exhaust
    RAM before the JSON parser saw a single byte). Mirrors the cap
    pattern tested by ``tests/test_secure_file_io_max_bytes.py``.
  * Proxy support — :func:`voice_typer.server.service.pack.proxy_env`
    returns the ``HTTP_PROXY`` / ``HTTPS_PROXY`` env vars; the default
    ``_http_get_manifest`` transport passes them to
    ``urllib.request.urlopen`` via a ``ProxyHandler`` so corporate
    networks work (§8.6).
  * Consent gate — :func:`voice_typer.server.service.pack.require_runtime_pack_consent`
    raises :class:`PackConsentRequiredError` when
    ``config.runtime_pack_consent`` is False. The pack download phones
    home to GitHub Releases (revealing user IP to Microsoft), so it MUST
    be consent-gated (§8.4 / C-DATA-1).

C-DATA-1 NOTE: Pack download from GitHub Releases is NOT covered by the
existing 3 network-call categories in CONSTRAINTS.md (update checks,
cloud transcription, model downloads). The USER must extend category (3)
→ "runtime asset downloads" or add category (4). Agents cannot edit
CONSTRAINTS.md.

Public API:
  * :data:`UpdateCheckResult` — TypedDict returned by
    :func:`check_pack_update`.
  * :func:`check_pack_update` — main entry point. Fetches the remote
    manifest, compares versions, optionally triggers a background
    download via :func:`pack.download_pack_with_resume`.
  * :func:`handle_check_pack_update_ipc` — thin IPC handler wrapper
    around :func:`check_pack_update`. Returns a plain ``dict`` for IPC
    serialization. NOT auto-registered in ``ipc/registry.py`` — the
    wiring is left to whoever owns the registry (Sub-agent 7 or a
    future integration agent). The renderer-side ``useNetworkOnline``
    hook calls ``call("check_pack_update", {})``; if the command isn't
    registered yet, the call fails gracefully (caught + logged at
    debug).
  * :func:`fetch_remote_manifest` — pure helper that fetches + parses
    the remote manifest. Exposed for unit testing.
  * :func:`is_newer_version` — pure semver-ish comparison. Exposed for
    unit testing.
  * :data:`DEFAULT_PACK_MANIFEST_URL` — the stable GitHub Releases URL.
  * :data:`MAX_MANIFEST_BYTES` — the byte cap on the remote manifest
    (1 MiB).
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import urlparse

from voice_typer.server.branding import APP_NAME
from voice_typer.server.secure_file_io import _secure_read_text
from voice_typer.server.service import pack
from voice_typer.server.service.pack import (
    PackConsentRequiredError,
    PackManifest,
    assert_pack_url_allowed,
    proxy_env,
    require_runtime_pack_consent,
)

if TYPE_CHECKING:
    from voice_typer.server import event_bus as event_bus_module
    from voice_typer.server.config import Config

log = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

# Stable GitHub Releases URL for the pack manifest. GitHub serves the
# latest release's assets at ``/releases/latest/download/<asset>``. The
# manifest is a tiny JSON file published alongside the pack onefile +
# slim-core installer (see ``scripts/release/publish_pack_release.py``).
#
# The repo owner / name are hardcoded here as a sane default. Power
# users can override via the ``VT_PACK_MANIFEST_URL`` env var (mirrors
# the ``VT_PACK_ROOT`` override in ``pack._default_pack_root``). Tests
# inject ``manifest_url=`` directly.
import os as _os

_DEFAULT_REPO_OWNER = "AbdallahIsDev"
_DEFAULT_REPO_NAME = "voice-typer"
DEFAULT_PACK_MANIFEST_URL = (
    f"https://github.com/{_DEFAULT_REPO_OWNER}/{_DEFAULT_REPO_NAME}"
    "/releases/latest/download/pack-manifest.json"
)


def _resolve_manifest_url(manifest_url: str | None) -> str:
    """Return the manifest URL, honoring the ``VT_PACK_MANIFEST_URL`` env override.

    The env var is a test escape hatch + power-user override — production
    code SHOULD NOT document it (mirrors the ``VT_PACK_ROOT`` stance in
    ``pack._default_pack_root``).
    """
    if manifest_url is not None:
        return manifest_url
    env = _os.environ.get("VT_PACK_MANIFEST_URL")
    if env:
        return env
    return DEFAULT_PACK_MANIFEST_URL


# 1 MiB cap on the remote manifest. Real pack-manifest.json is <2 KB
# (one entry per file × ~50 files). 1 MiB is generous enough that a
# legitimate manifest with thousands of entries still passes, but small
# enough that a malicious server returning a multi-GB body is rejected
# before exhausting RAM. Mirrors the 16 MiB default cap in
# ``secure_file_io._DEFAULT_MAX_READ_BYTES`` but tightened for the
# manifest use case (the manifest is NOT a large file).
MAX_MANIFEST_BYTES = 1 * 1024 * 1024


# ── Result type ──────────────────────────────────────────────────────────


class UpdateCheckResult(TypedDict, total=False):
    """Outcome of a pack-version check.

    Mirrors :data:`voice_typer.server.service._download_helpers.DownloadOutcome`
    in shape (``success`` + optional fields) so the renderer's existing
    IPC result handling works without a special case.

    Fields:
        success: always present (bool). True if the check completed
            without error (regardless of whether an update was found).
        checked_at: epoch ms when the check ran.
        local_version: the local pack version (str) or ``None`` if no
            local pack is installed.
        remote_version: the remote manifest's version (str) or ``None``
            if the remote manifest could not be fetched.
        update_available: True if the remote version is newer than the
            local version (or local is missing).
        download_triggered: True if a background download was started.
        consent_required: present (True) when consent is missing — the
            renderer should show the consent dialog and retry.
        error: present on failure (str). Human-readable error message.
        reason: present on failure (str). Short machine-readable reason
            code (e.g. ``"network_error"``, ``"manifest_invalid"``,
            ``"ssrf_blocked"``).
    """

    success: bool
    checked_at: int
    local_version: str | None
    remote_version: str | None
    update_available: bool
    download_triggered: bool
    consent_required: bool
    error: str
    reason: str


# ── Version comparison ──────────────────────────────────────────────────


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a dotted version string into a tuple of ints.

    Handles ``"1.2.3"``, ``"v1.2.3"``, ``"1.2.3-rc1"`` (suffix ignored),
    and ``"1.2"`` (shorter tuples pad with zeros for comparison). Non-
    numeric segments are dropped (treated as 0). Returns ``(0,)`` for
    empty / unparseable strings so the comparison never raises.

    This is intentionally simple — pack versions follow a simple
    ``MAJOR.MINOR.PATCH`` scheme (no SemVer pre-release precedence). If
    the project later adopts full SemVer, swap this for
    :func:`packaging.version.parse` (already a transitive dep via
    ``huggingface_hub``).
    """
    if not v:
        return (0,)
    # Strip a leading ``v`` (GitHub release tags commonly use ``v1.2.3``).
    s = v.strip().lstrip("vV")
    # Drop any ``-suffix`` (pre-release / build metadata).
    if "-" in s:
        s = s.split("-", 1)[0]
    parts: list[int] = []
    for segment in s.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            # Non-numeric segment (e.g. ``"1.2.x"``) — treat as 0.
            parts.append(0)
    return tuple(parts) if parts else (0,)


def is_newer_version(remote: str, local: str) -> bool:
    """Return True if *remote* is strictly newer than *local*.

    Equal versions return False (no update needed). Shorter tuples pad
    with zeros: ``1.2`` == ``1.2.0``. Non-numeric segments are treated
    as 0 (defensive — a malformed version should NOT trigger a
    spurious update).

    Examples:
        >>> is_newer_version("1.2.3", "1.2.2")
        True
        >>> is_newer_version("1.2.3", "1.2.3")
        False
        >>> is_newer_version("v2.0.0", "1.9.9")
        True
        >>> is_newer_version("1.2", "1.2.0")
        False
    """
    r = _parse_version(remote)
    l_ = _parse_version(local)
    # Pad to equal length so ``(1, 2)`` compares equal to ``(1, 2, 0)``.
    n = max(len(r), len(l_))
    r_padded = r + (0,) * (n - len(r))
    l_padded = l_ + (0,) * (n - len(l_))
    return r_padded > l_padded


# ── HTTP transport (default; tests inject a fake) ────────────────────────


def _http_get_manifest(url: str, *, max_bytes: int = MAX_MANIFEST_BYTES) -> str:
    """Default HTTP transport — fetches *url* and returns the body as text.

    Uses ``urllib.request`` (no extra dep). Respects ``HTTP_PROXY`` /
    ``HTTPS_PROXY`` env vars (§8.6) via a ``ProxyHandler`` built from
    :func:`pack.proxy_env`. Enforces ``max_bytes`` by reading in chunks
    and aborting when the cap is exceeded (mirrors the chunked-read
    pattern in :func:`voice_typer.server.security.file_io._read_with_byte_limit`).

    The chunked read here is a DEFENSE-IN-DEPTH: ``urllib``'s
    ``urlopen`` does NOT enforce a body cap, so without this a
    malicious server could stream a multi-GB body. We read at most
    ``max_bytes + 1`` bytes; if the extra byte is non-empty, the body
    exceeded the cap and we raise.

    Raises:
        RuntimeError: if the HTTP status is not 200, or the body
            exceeds ``max_bytes``.
        OSError: if the connection fails (DNS, refused, timeout).
    """
    proxies = proxy_env()
    if proxies:
        proxy_handler = urllib.request.ProxyHandler(proxies)
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"{APP_NAME}/pack-update-checker", "Accept": "application/json"},
    )
    with opener.open(req, timeout=30) as resp:
        status = resp.getcode()
        if status != 200:
            raise RuntimeError(f"unexpected HTTP status {status} for {url}")
        # Read in chunks; abort if the running total exceeds max_bytes.
        # We allow exactly ``max_bytes`` bytes (matching the
        # ``_secure_read_text`` semantics: ``total > max_bytes`` raises).
        total = 0
        chunks: list[bytes] = []
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(
                    f"manifest exceeds max_bytes={max_bytes} "
                    f"(read {total} bytes so far) — refusing to continue "
                    f"reading to prevent unbounded memory consumption"
                )
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")


# ── Manifest fetch + parse ──────────────────────────────────────────────


def fetch_remote_manifest(
    url: str,
    *,
    http_get: callable | None = None,
    max_bytes: int = MAX_MANIFEST_BYTES,
) -> PackManifest | None:
    """Fetch + validate the remote ``pack-manifest.json``.

    Delegates SSRF protection to
    :func:`voice_typer.server.service.pack.assert_pack_url_allowed`
    (which extends the allowlist with GitHub hosts + inherits the
    IP-literal blocklist + DNS-rebinding defense from
    :func:`voice_typer.server.security.url_allowlist.assert_url_allowed`).

    Delegates parsing to :func:`voice_typer.server.service.pack.load_pack_manifest`
    so the remote manifest is validated against the SAME schema as the
    local manifest (a mismatch would let a malicious server ship a
    pack that bypasses the local integrity check).

    Writes the raw body to a temp file and reads it back via
    :func:`_secure_read_text` so the ``max_bytes`` cap is enforced by
    the SAME code path that protects config / vocabulary / templates
    reads (defense-in-depth — the chunked read in
    :func:`_http_get_manifest` is the first layer; the
    :func:`_secure_read_text` cap is the second layer in case the
    transport is swapped for one that doesn't enforce a cap).

    Returns ``None`` on any failure (network error, SSRF block, parse
    error, schema validation failure). The caller treats ``None`` as
    "no update info available; do not trigger a download".
    """
    # SSRF gate first — refuse to fetch from a private/disallowed host
    # even before we open a socket. This is the SAME check
    # ``download_pack_with_resume`` runs, so the check + download paths
    # can never disagree about whether a URL is safe.
    try:
        assert_pack_url_allowed(url)
    except ValueError as exc:
        log.warning("[UPDATE] SSRF block on manifest URL: %s", exc)
        return None

    if http_get is None:
        http_get = _http_get_manifest
    try:
        body = http_get(url, max_bytes=max_bytes)
    except (OSError, RuntimeError) as exc:
        log.warning("[UPDATE] failed to fetch remote manifest from %s: %s", url, exc)
        return None

    # Parse via the shared schema validator (reuses the local manifest
    # validation logic — a remote manifest with a missing/wrong-typed
    # field is rejected identically to a corrupt local manifest).
    try:
        json.loads(body)
    except json.JSONDecodeError:
        log.warning("[UPDATE] remote manifest from %s is not valid JSON", url)
        return None
    # Reuse pack's structural validator. ``load_pack_manifest`` expects
    # a path, so we round-trip through a temp file + ``_secure_read_text``
    # to enforce the max_bytes cap via the SAME code path as config /
    # vocabulary / templates reads (defense-in-depth).
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".json", delete=False
    ) as tmp:
        tmp.write(body.encode("utf-8", errors="replace"))
        tmp_path = Path(tmp.name)
    try:
        # The cap here is the SECOND layer (the first is the chunked
        # read in ``_http_get_manifest``). If a future transport
        # bypasses the chunked cap, this still catches an oversized
        # manifest.
        _secure_read_text(tmp_path, max_bytes=max_bytes)
        manifest = pack.load_pack_manifest(tmp_path)
    finally:
        with __import__("contextlib").suppress(OSError):
            tmp_path.unlink()
    if manifest is None:
        log.warning("[UPDATE] remote manifest from %s failed schema validation", url)
        return None
    return manifest


def _local_pack_version(root: Path | None = None) -> str | None:
    """Return the locally-installed pack version, or ``None`` if none.

    Scans ``<pack-root>/<version>/pack-manifest.json`` for every version
    directory under the pack root and returns the highest version that
    passes :func:`pack.pack_exists` (cheap existence check — no SHA-256
    hashing). The full checksum is run in the background by
    :class:`pack.BackgroundChecksum` on startup (§8.16); this function
    is the cheap launch-time check (§8.10).

    Returns ``None`` when:
      * the pack root does not exist (first launch).
      * no version directory contains a valid ``pack-manifest.json``.
      * every version directory fails the existence check (corrupt /
        partial downloads).
    """
    base = pack._default_pack_root() if root is None else root
    if not base.exists():
        return None
    best: str | None = None
    try:
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            version = entry.name
            try:
                if pack.pack_exists(version, root=root):
                    if best is None or is_newer_version(version, best):
                        best = version
            except Exception:  # noqa: BLE001 — defensive: a single corrupt dir must not abort the scan
                log.debug("[UPDATE] pack_exists check failed for %s", version, exc_info=True)
    except OSError:
        log.debug("[UPDATE] pack root scan failed", exc_info=True)
        return None
    return best


# ── Background download trigger ──────────────────────────────────────────


def _trigger_background_download(
    *,
    manifest: PackManifest,
    manifest_url: str,
    config: Config | None,
    event_bus: event_bus_module | None,
    root: Path | None,
    http_get: callable | None,
) -> bool:
    """Trigger a background download of the pack via :mod:`pack`.

    Calls :func:`pack.require_runtime_pack_consent` first — if consent
    is missing, raises :class:`PackConsentRequiredError` (the caller
    catches it and surfaces a consent dialog).

    Constructs the pack-download URL from the manifest URL + version
    (``<manifest_url's directory>/pack-<version>.zip``). This mirrors
    the asset-naming convention in
    ``scripts/release/publish_pack_release.py`` — the publisher uploads
    the pack as ``pack-<version>.zip`` alongside ``pack-manifest.json``.

    Runs :func:`pack.download_pack_with_resume` on a daemon thread so
    the caller (e.g. the IPC handler) is not blocked. The download
    publishes its own events (``pack_download_started`` /
    ``pack_download_progress`` / ``pack_download_completed`` /
    ``pack_download_failed``) via the event bus; the renderer's
    ``usePackDownload`` hook subscribes to those events.

    Returns ``True`` if the download thread was started. Returns
    ``False`` if consent is missing (the caller surfaces a consent
    dialog; the renderer retries after the user accepts).
    """
    # Consent gate first — mirrors the pattern in
    # ``ModelMixin._require_huggingface_consent`` and
    # ``pack.require_runtime_pack_consent``. The pack download phones
    # home to GitHub Releases (revealing user IP to Microsoft), so it
    # MUST be consent-gated (§8.4 / C-DATA-1).
    require_runtime_pack_consent(config, version=manifest["version"])

    # Construct the pack-download URL. The manifest lives at
    # ``.../releases/latest/download/pack-manifest.json`` (or a pinned
    # release ``.../releases/download/v1.2.3/pack-manifest.json``). The
    # pack onefile lives at the SAME directory under the name
    # ``pack-<version>.zip`` (see ``publish_pack_release.py``).
    parsed = urlparse(manifest_url)
    # Strip ``pack-manifest.json`` from the path; append the pack asset name.
    path = parsed.path
    # ``path`` looks like ``/owner/repo/releases/latest/download/pack-manifest.json``
    # or ``/owner/repo/releases/download/v1.2.3/pack-manifest.json``.
    # Replace the trailing ``/pack-manifest.json`` with ``/pack-<version>.zip``.
    dir_path = path.rsplit("/", 1)[0] if "/" in path else ""
    pack_asset_name = f"pack-{manifest['version']}.zip"
    pack_url = f"{parsed.scheme}://{parsed.netloc}{dir_path}/{pack_asset_name}"

    dest = pack.pack_partial_path(manifest["version"], root=root)
    # Ensure the version directory exists (``download_pack_with_resume``
    # opens ``dest`` with ``open("wb")`` / ``open("ab")`` — the parent
    # must exist).
    dest.parent.mkdir(parents=True, exist_ok=True)

    def _bg() -> None:
        try:
            pack.download_pack_with_resume(
                pack_url,
                dest,
                expected_sha256=manifest["sha256"],
                version=manifest["version"],
                event_bus=event_bus,
                http_get=http_get,
            )
        except PackConsentRequiredError:
            # Should not happen — consent was checked above — but
            # defensive: a race where the user revokes consent between
            # the check + the download.
            log.warning(
                "[UPDATE] consent revoked between check + download for pack %s",
                manifest["version"],
            )
        except Exception:  # noqa: BLE001 — background thread must not propagate
            log.exception(
                "[UPDATE] background pack download failed for %s",
                manifest["version"],
            )

    thread = threading.Thread(
        target=_bg,
        name=f"pack-update-download-{manifest['version']}",
        daemon=True,
    )
    thread.start()
    log.info(
        "[UPDATE] background download started for pack %s from %s",
        manifest["version"],
        pack_url,
    )
    return True


# ── Main entry point ────────────────────────────────────────────────────


def check_pack_update(
    config: Config | None,
    event_bus: event_bus_module | None,
    *,
    http_get: callable | None = None,
    manifest_url: str | None = None,
    local_version: str | None = None,
    root: Path | None = None,
    trigger_download: bool = True,
) -> UpdateCheckResult:
    """Check whether a newer pack version is available; optionally trigger download.

    Steps:
      1. Resolve the manifest URL (param > ``VT_PACK_MANIFEST_URL`` env >
         :data:`DEFAULT_PACK_MANIFEST_URL`).
      2. Fetch + validate the remote manifest via
         :func:`fetch_remote_manifest` (SSRF-gated, max-bytes-capped).
      3. Resolve the local pack version (param > scan the pack root).
      4. Compare versions via :func:`is_newer_version`.
      5. If a newer version is available AND ``trigger_download=True``,
         call :func:`_trigger_background_download` (consent-gated).

    Returns an :data:`UpdateCheckResult`. Never raises — all errors are
    caught and returned as ``{"success": False, "error": ..., "reason": ...}``.
    The ONLY exception is :class:`PackConsentRequiredError` from the
    consent gate, which is caught and returned as
    ``{"success": False, "consent_required": True, ...}`` so the
    renderer can surface a consent dialog (mirrors the
    ``ModelMixin._require_huggingface_consent`` pattern).
    """
    import time

    url = _resolve_manifest_url(manifest_url)

    # Default local_version to a scan of the pack root.
    if local_version is None:
        try:
            local_version = _local_pack_version(root=root)
        except Exception:  # noqa: BLE001 — defensive: pack-root scan must not abort the check
            log.exception("[UPDATE] local pack scan failed")
            local_version = None

    # ── Fetch the remote manifest ───────────────────────────────────────
    try:
        remote_manifest = fetch_remote_manifest(url, http_get=http_get)
    except Exception:  # noqa: BLE001 — fetch_remote_manifest is supposed to return None on failure, but catch defensively
        log.exception("[UPDATE] unexpected error fetching remote manifest")
        remote_manifest = None

    if remote_manifest is None:
        return {
            "success": False,
            "checked_at": int(time.time() * 1000),
            "local_version": local_version,
            "remote_version": None,
            "update_available": False,
            "download_triggered": False,
            "error": "failed to fetch remote manifest",
            "reason": "fetch_failed",
        }

    remote_version = remote_manifest["version"]
    update_available = (
        local_version is None or is_newer_version(remote_version, local_version)
    )

    result: UpdateCheckResult = {
        "success": True,
        "checked_at": int(time.time() * 1000),
        "local_version": local_version,
        "remote_version": remote_version,
        "update_available": update_available,
        "download_triggered": False,
    }

    if not update_available:
        log.info(
            "[UPDATE] pack is up-to-date (local=%s, remote=%s)",
            local_version,
            remote_version,
        )
        return result

    log.info(
        "[UPDATE] pack update available (local=%s, remote=%s)",
        local_version,
        remote_version,
    )

    if not trigger_download:
        return result

    # ── Trigger the background download (consent-gated) ────────────────
    try:
        download_started = _trigger_background_download(
            manifest=remote_manifest,
            manifest_url=url,
            config=config,
            event_bus=event_bus,
            root=root,
            http_get=http_get,
        )
        result["download_triggered"] = download_started
    except PackConsentRequiredError:
        log.warning(
            "[UPDATE] runtime_pack_consent not given — refusing to download pack %s",
            remote_version,
        )
        # Surface a consent_required event so the renderer can show the
        # consent dialog (mirrors the model-download consent flow).
        if event_bus is not None:
            try:
                event_bus.publish(
                    {
                        "type": "consent_required",
                        "data": {
                            "provider": "github",
                            "scope": "runtime_pack",
                            "model": remote_version,
                            "message": (
                                "Runtime pack consent required before downloading "
                                "the offline engine pack from GitHub Releases."
                            ),
                        },
                    }
                )
            except Exception:  # noqa: BLE001 — best-effort event publish
                log.debug("[UPDATE] consent_required event push failed", exc_info=True)
        result["success"] = False
        result["consent_required"] = True
        result["error"] = "Runtime pack consent required"
        result["reason"] = "consent_required"
    except Exception as exc:  # noqa: BLE001 — defensive: the download trigger must not abort the check
        log.exception("[UPDATE] failed to trigger background download")
        result["success"] = False
        result["error"] = f"failed to trigger download: {exc}"
        result["reason"] = "download_trigger_failed"

    return result


# ── IPC handler wrapper ─────────────────────────────────────────────────


def handle_check_pack_update_ipc(
    app: Any,
    data: dict | None,
    *,
    http_get: callable | None = None,
    manifest_url: str | None = None,
    local_version: str | None = None,
    root: Path | None = None,
    trigger_download: bool = True,
) -> dict[str, Any]:
    """Thin IPC handler wrapper around :func:`check_pack_update`.

    Returns a plain ``dict`` (not a TypedDict) for IPC serialization —
    mirrors the pattern in ``ModelMixin.download_model`` which converts
    the :data:`DownloadOutcome` TypedDict to a plain ``dict`` via
    ``dict(outcome)``.

    NOT auto-registered in ``ipc/registry.py`` — the wiring is left to
    whoever owns the registry (Sub-agent 7 or a future integration
    agent). The renderer-side ``useNetworkOnline`` hook calls
    ``call("check_pack_update", {})``; if the command isn't registered
    yet, the call fails gracefully (caught + logged at debug).

    Args:
        app: the :class:`VoiceTyperService` (or any object with
            ``config`` and ``event_bus`` attributes). ``None`` is
            tolerated — treated as "no config + no event bus" (the
            check still runs, but consent will fail + no events are
            published).
        data: optional IPC payload. Currently unused — the check takes
            no parameters from the renderer. Reserved for future use
            (e.g. ``{"force": true}`` to bypass the version comparison).
        http_get: injectable transport for testing. Defaults to
            ``None`` (uses :func:`_http_get_manifest`).
        manifest_url: override the manifest URL (testing / power-user).
        local_version: override the local pack version (testing).
        root: override the pack root (testing).
        trigger_download: if ``False``, only check — don't trigger a
            background download (testing / "check only" mode).
    """
    config = getattr(app, "config", None) if app is not None else None
    event_bus = getattr(app, "event_bus", None) if app is not None else None
    if event_bus is None and app is not None:
        # Fall back to the module-level event_bus (some service objects
        # expose it as a method / property rather than an attribute).
        try:
            from voice_typer.server import event_bus as _event_bus_module

            event_bus = _event_bus_module
        except ImportError:
            pass
    result = check_pack_update(
        config,
        event_bus,  # type: ignore[arg-type]
        http_get=http_get,
        manifest_url=manifest_url,
        local_version=local_version,
        root=root,
        trigger_download=trigger_download,
    )
    return dict(result)


__all__ = [
    "DEFAULT_PACK_MANIFEST_URL",
    "MAX_MANIFEST_BYTES",
    "UpdateCheckResult",
    "check_pack_update",
    "fetch_remote_manifest",
    "handle_check_pack_update_ipc",
    "is_newer_version",
]
