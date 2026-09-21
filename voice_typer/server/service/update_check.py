"""Auto-update check against GitHub Releases (user-initiated/silent check)."""

from __future__ import annotations

import json
import logging
import os as _os
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import urlparse

from voice_typer.server.branding import APP_NAME, APP_REPO
from voice_typer.server.service import offline_pack
from voice_typer.server.service.offline_pack import (
    OfflinePackManifest,
    assert_offline_pack_url_allowed,
    proxy_env,
    require_offline_pack_consent,
)

if TYPE_CHECKING:
    from voice_typer.server.config import Config

log = logging.getLogger(__name__)


# Stable GitHub Releases URL for the pack manifest. GitHub serves the
DEFAULT_OFFLINE_PACK_MANIFEST_URL = f"https://github.com/{APP_REPO}/releases/latest/download/pack-manifest.json"


def _resolve_manifest_url(manifest_url: str | None) -> str:
    """Return the manifest URL, honoring the ``VT_PACK_MANIFEST_URL`` env override."""
    if manifest_url is not None:
        return manifest_url
    env = _os.environ.get("VT_PACK_MANIFEST_URL")
    if env:
        return env
    return DEFAULT_OFFLINE_PACK_MANIFEST_URL


# 1 MiB cap on the remote manifest. Real pack-manifest.json is <2 KB
MAX_MANIFEST_BYTES = 1 * 1024 * 1024


class UpdateCheckResult(TypedDict, total=False):
    """Outcome of a pack-version check."""

    success: bool
    checked_at: int
    local_version: str | None
    remote_version: str | None
    update_available: bool
    download_triggered: bool
    consent_required: bool
    error: str
    reason: str


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a dotted version string into a tuple of ints."""
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
            # Non-numeric segment (e.g. ``"1.2.x"``), treat as 0.
            parts.append(0)
    return tuple(parts) if parts else (0,)


def is_newer_version(remote: str, local: str) -> bool:
    """Return True if *remote* is strictly newer than *local*.

    Equal versions return False (no update needed). Shorter tuples pad
    with zeros: ``1.2`` == ``1.2.0``. Non-numeric segments are treated
    as 0 (defensive, a malformed version should NOT trigger a
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


class _SSRFAwareRedirectHandler(urllib.request.HTTPRedirectHandler):
    """``HTTPRedirectHandler`` subclass that re-validates each 3xx hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Re-validate ``newurl`` through :func:`assert_offline_pack_url_allowed`."""
        try:
            assert_offline_pack_url_allowed(newurl)
        except ValueError as exc:
            # Convert ``ValueError`` (raised by ``assert_url_allowed``)
            raise RuntimeError(
                f"SSRF block on redirect target (refusing to follow "
                f"{code} redirect to a non-allowlisted / private IP "
                f"target): {exc}"
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Upper bound on the launch-time remote-manifest fetch. The interactive
LAUNCH_MANIFEST_TIMEOUT_S = 8.0


def _http_get_manifest(url: str, *, max_bytes: int = MAX_MANIFEST_BYTES, timeout: float = 30.0) -> str:
    """Default HTTP transport, fetches *url* and returns the body as text."""
    proxies = proxy_env()
    if proxies:
        proxy_handler = urllib.request.ProxyHandler(proxies)
        opener = urllib.request.build_opener(_SSRFAwareRedirectHandler(), proxy_handler)
    else:
        opener = urllib.request.build_opener(_SSRFAwareRedirectHandler())
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"{APP_NAME}/pack-update-checker", "Accept": "application/json"},
    )
    with opener.open(req, timeout=timeout) as resp:
        status = resp.getcode()
        if status != 200:
            raise RuntimeError(f"unexpected HTTP status {status} for {url}")
        # Read in chunks; abort if the running total exceeds max_bytes.
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
                    f"(read {total} bytes so far), refusing to continue "
                    f"reading to prevent unbounded memory consumption"
                )
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")


def _is_missing_manifest_404(exc: BaseException) -> bool:
    """True when *exc* is an HTTP 404 from the manifest fetch."""
    if getattr(exc, "code", None) == 404:
        return True
    text = str(exc)
    return "HTTP Error 404" in text or "HTTP status 404" in text


def fetch_remote_manifest(
    url: str,
    *,
    http_get: Callable[..., str] | None = None,
    max_bytes: int = MAX_MANIFEST_BYTES,
    timeout: float = 30.0,
) -> OfflinePackManifest | None:
    """Fetch + validate the remote ``pack-manifest.json``."""
    # SSRF gate first, refuse to fetch from a private/disallowed host
    try:
        assert_offline_pack_url_allowed(url)
    except ValueError as exc:
        log.warning("[UPDATE] SSRF block on manifest URL: %s", exc)
        return None

    if http_get is None:
        http_get = _http_get_manifest
    try:
        # The default transport accepts a ``timeout``; injected test
        if http_get is _http_get_manifest:
            body = http_get(url, max_bytes=max_bytes, timeout=timeout)
        else:
            body = http_get(url, max_bytes=max_bytes)
    except (OSError, RuntimeError) as exc:
        # Strip the scheme so the line stays short; host + path are the
        if _is_missing_manifest_404(exc):
            log.info(
                "[UPDATE] remote pack manifest not published yet (%s): %s",
                exc,
                url.split("://", 1)[-1],
            )
        else:
            log.warning(
                "[UPDATE] Manifest fetch failed (%s): %s",
                exc,
                url.split("://", 1)[-1],
            )
        return None

    # Parse + validate via the shared schema validator (the SAME
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        log.warning("[UPDATE] remote manifest from %s is not valid JSON", url)
        return None
    manifest = offline_pack.validate_offline_pack_manifest_dict(data, source=url)
    if manifest is None:
        log.warning("[UPDATE] remote manifest from %s failed schema validation", url)
        return None
    return manifest


def _local_offline_pack_version(root: Path | None = None) -> str | None:
    """Return the locally-installed pack version, or ``None`` if none."""
    base = offline_pack._default_offline_pack_root() if root is None else root
    if not base.exists():
        return None
    best: str | None = None
    try:
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.endswith(".new") or entry.name.endswith(".trash"):
                # Staging dirs left by a crashed install and trash dirs
                continue
            version = entry.name
            try:
                if offline_pack.offline_pack_exists(version, root=root) and (
                    best is None or is_newer_version(version, best)
                ):
                    best = version
            except Exception:  # defensive: a single corrupt dir must not abort the scan
                log.debug("[UPDATE] offline_pack_exists check failed for %s", version, exc_info=True)
    except OSError:
        log.debug("[UPDATE] pack root scan failed", exc_info=True)
        return None
    return best


# Per-version in-flight download guard (§8.13 / §8.16).
_ACTIVE_PACK_DOWNLOADS: set[str] = set()
_ACTIVE_PACK_DOWNLOADS_LOCK = threading.Lock()


def _trigger_background_download(
    *,
    manifest: OfflinePackManifest,
    manifest_url: str,
    config: Config | None,
    event_bus: ModuleType | None,
    root: Path | None,
    http_get: Callable[..., Any] | None,
) -> bool:
    """Trigger a background download of the pack via :mod:`pack`."""
    version = manifest["version"]

    # Always-on product decision: gate is a no-op; kept for call-site uniformity.
    require_offline_pack_consent(config, version=version)

    # Dedupe: if a download for this version is already in flight (started
    with _ACTIVE_PACK_DOWNLOADS_LOCK:
        if version in _ACTIVE_PACK_DOWNLOADS:
            log.info(
                "[UPDATE] pack %s download already in flight, skipping duplicate trigger",
                version,
            )
            return False
        _ACTIVE_PACK_DOWNLOADS.add(version)

    # Everything that can still fail AFTER registration (mkdir on a full
    try:
        # Construct the pack-download URL. The manifest lives at
        parsed = urlparse(manifest_url)
        # Strip ``pack-manifest.json`` from the path; append the pack asset name.
        path = parsed.path
        # ``path`` looks like ``/owner/repo/releases/latest/download/pack-manifest.json``
        dir_path = path.rsplit("/", 1)[0] if "/" in path else ""
        pack_asset_name = f"pack-{manifest['version']}.zip"
        pack_url = f"{parsed.scheme}://{parsed.netloc}{dir_path}/{pack_asset_name}"

        dest = offline_pack.offline_pack_partial_path(manifest["version"], root=root)
        # Ensure the version directory exists (``download_offline_pack_with_resume``
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _bg() -> None:
            try:
                # §8.8 disk gate: refuse to even START a ~200 MB download
                offline_pack.check_offline_pack_disk_space(dest.parent)
                # §8.13 cross-process lock: serialize the partial-file
                with offline_pack.OfflinePackLock(version, root=root):
                    # Post-lock installed re-check: the version
                    if offline_pack.offline_pack_exists(version, root=root):
                        log.info(
                            "[UPDATE] pack %s already installed (post-lock re-check), skipping the redundant download",
                            version,
                        )
                        return
                    downloaded = offline_pack.download_offline_pack_with_resume(
                        pack_url,
                        dest,
                        expected_sha256=manifest["sha256"],
                        version=version,
                        event_bus=event_bus,
                        http_get=http_get,
                    )
                    if downloaded:
                        # Install stage: extract the verified archive
                        offline_pack.install_offline_pack(
                            dest,
                            version,
                            manifest,
                            root=root,
                            event_bus=event_bus,
                        )
            except Exception:  # background thread must not propagate
                log.exception(
                    "[UPDATE] background pack download failed for %s",
                    version,
                )
            finally:
                # Release the in-flight guard so a LATER trigger (or the next
                with _ACTIVE_PACK_DOWNLOADS_LOCK:
                    _ACTIVE_PACK_DOWNLOADS.discard(version)

        thread = threading.Thread(
            target=_bg,
            name=f"pack-update-download-{manifest['version']}",
            daemon=True,
        )
        thread.start()
    except BaseException:
        # Late failure AFTER registration (mkdir, thread spawn) —
        with _ACTIVE_PACK_DOWNLOADS_LOCK:
            _ACTIVE_PACK_DOWNLOADS.discard(version)
        raise
    log.info(
        "[UPDATE] background download started for pack %s from %s",
        manifest["version"],
        pack_url,
    )
    return True


def check_offline_pack_update(
    config: Config | None,
    event_bus: ModuleType | None,
    *,
    http_get: Callable[..., Any] | None = None,
    manifest_url: str | None = None,
    local_version: str | None = None,
    root: Path | None = None,
    trigger_download: bool = True,
    manifest_timeout: float = 30.0,
) -> UpdateCheckResult:
    """Check whether a newer pack version is available; optionally trigger download.

    Pack auto-update is always-on: consent never blocks the manifest
    fetch or the background download (user product decision).

    Steps:
      1. Resolve the manifest URL (param > ``VT_PACK_MANIFEST_URL`` env >
         :data:`DEFAULT_OFFLINE_PACK_MANIFEST_URL`).
      2. Resolve the local pack version (param > scan the pack root).
      3. Fetch + validate the remote manifest via
         :func:`fetch_remote_manifest` (SSRF-gated, max-bytes-capped,
         ``manifest_timeout``-bounded).
      4. Compare versions via :func:`is_newer_version`.
      5. If a newer version is available AND ``trigger_download=True``,
         call :func:`_trigger_background_download`.

    Returns an :data:`UpdateCheckResult`. Never raises, all errors are
    caught and returned as ``{"success": False, "error": ..., "reason": ...}``.
    """
    import time

    url = _resolve_manifest_url(manifest_url)

    # Default local_version to a scan of the pack root.
    if local_version is None:
        try:
            local_version = _local_offline_pack_version(root=root)
        except Exception:  # defensive: pack-root scan must not abort the check
            log.exception("[UPDATE] local pack scan failed")
            local_version = None

    try:
        remote_manifest = fetch_remote_manifest(url, http_get=http_get, timeout=manifest_timeout)
    except Exception:  # fetch_remote_manifest is supposed to return None on failure, but catch defensively
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
    update_available = local_version is None or is_newer_version(remote_version, local_version)

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
    except Exception as exc:  # defensive: the download trigger must not abort the check
        log.exception("[UPDATE] failed to trigger background download")
        result["success"] = False
        result["error"] = f"failed to trigger download: {exc}"
        result["reason"] = "download_trigger_failed"

    return result


def handle_check_offline_pack_update_ipc(
    app: Any,
    data: dict | None,
    *,
    http_get: Callable[..., Any] | None = None,
    manifest_url: str | None = None,
    local_version: str | None = None,
    root: Path | None = None,
    trigger_download: bool = True,
) -> dict[str, Any]:
    """Thin IPC handler wrapper around :func:`check_offline_pack_update`.

    Returns a plain ``dict`` (not a TypedDict) for IPC serialization —
    """
    config = getattr(app, "config", None) if app is not None else None
    # Typed resolution: the app may expose ``event_bus`` as an
    event_bus: ModuleType | None = getattr(app, "event_bus", None) if app is not None else None
    if event_bus is None and app is not None:
        # Fall back to the module-level event_bus (some service objects
        try:
            from voice_typer.server import event_bus as _event_bus_module

            event_bus = _event_bus_module
        except ImportError:
            pass
    result = check_offline_pack_update(
        config,
        event_bus,
        http_get=http_get,
        manifest_url=manifest_url,
        local_version=local_version,
        root=root,
        trigger_download=trigger_download,
    )
    return dict(result)


__all__ = [
    "DEFAULT_OFFLINE_PACK_MANIFEST_URL",
    "MAX_MANIFEST_BYTES",
    "UpdateCheckResult",
    "check_offline_pack_update",
    "fetch_remote_manifest",
    "handle_check_offline_pack_update_ipc",
    "is_newer_version",
]
