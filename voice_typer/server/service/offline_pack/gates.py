"""Pre-download policy gates: disk space, consent, SSRF allowlist, proxy."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server.asr_utils import _disk_space_error

from .core import (
    OFFLINE_PACK_COMPRESSED_MB,
    OFFLINE_PACK_REQUIRED_MB,
    OFFLINE_PACK_UNPACKED_MB,
)

if TYPE_CHECKING:
    from voice_typer.server.config import Config

log = logging.getLogger(__name__)


def check_offline_pack_disk_space(pack_dir: Path, *, required_mb: int = OFFLINE_PACK_REQUIRED_MB) -> None:
    """Raise :class:`RuntimeError` if *pack_dir* has less than *required_mb* free.

    Same disk-full detection + user-friendly error structure as
    :func:`voice_typer.server.asr_utils._check_disk_space_for_download`
    (the message builder is SHARED, :func:`asr_utils._disk_space_error` —
    so the two gates cannot drift); the pack check points at the pack
    directory tree rather than the HF cache and uses a fixed required
    budget instead of a per-model estimate.
    """
    try:
        usage = shutil.disk_usage(str(pack_dir))
    except OSError as exc:
        log.debug("[PACK] disk space check skipped: %s", exc)
        return  # don't block the download on a failed stat
    available_mb = usage.free // (1024 * 1024)
    if available_mb < required_mb:
        raise _disk_space_error(
            str(pack_dir),
            available_mb,
            required_mb,
            "runtime pack",
            detail=(f"{OFFLINE_PACK_COMPRESSED_MB} MB compressed + {OFFLINE_PACK_UNPACKED_MB} MB unpacked"),
        )
    log.debug(
        "[PACK] disk space check passed: %d MB available, %d MB required",
        available_mb,
        required_mb,
    )


def require_offline_pack_consent(config: Config | None, *, version: str | None = None) -> None:
    """No-op: pack downloads are always-on (user product decision).

    Kept as a call-site seam so update/download paths stay uniform, but
    it never raises. ``config.offline_pack_consent`` is forced True on
    load and is not set_config-able (not in the SEC-002 allowlist).
    C-DATA-1 category (4) permits the GitHub pack download regardless
    of a consent toggle; the user chose always-on with no disable path.
    """
    return


def assert_offline_pack_url_allowed(url: str) -> None:
    """SSRF gate for the pack download URL.

    Inherits :func:`voice_typer.server.security.url_allowlist.assert_url_allowed`
    (the same SSRF defense tested by ``tests/test_http_safety_ssrf.py``)
    plus a pack-specific extension: GitHub Releases hosts
    (``github.com`` / ``objects.githubusercontent.com`` /
    ``codeload.github.com``) are added to the runtime allowlist on
    first call so the pack download is not blocked.

    Callers SHOULD pass ``require_https=True`` (the default), the
    pack must come over HTTPS.
    """
    from voice_typer.server.security.url_allowlist import (
        assert_url_allowed,
        extend_url_allowlist,
        get_url_allowlist,
    )

    # Add GitHub hosts to the runtime allowlist (idempotent). This is
    github_hosts = {"github.com", "objects.githubusercontent.com", "codeload.github.com"}
    if not github_hosts.issubset(get_url_allowlist()):
        extend_url_allowlist(github_hosts, caller="pack_downloader")
    assert_url_allowed(url, field_name="pack_url", client_name="pack_downloader")


def proxy_env() -> dict[str, str]:
    """Return the HTTP/HTTPS proxy env vars (§8.6).

    Respects ``HTTP_PROXY`` / ``HTTPS_PROXY`` (and their lowercase
    variants, ``requests`` and ``httpx`` both honor lowercase). The
    returned dict is suitable for passing as ``proxies=`` to
    ``requests`` or for setting on a custom ``httpx.Client``.
    """
    out: dict[str, str] = {}
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        val = os.environ.get(key)
        if val:
            out[key] = val
    return out
