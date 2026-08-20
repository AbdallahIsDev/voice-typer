#!/usr/bin/env python3
"""Auto-update — GitHub Releases publisher (plan-runtime-pack-split.md §10.1).

Publishes the slim-core installer + pack onefile + ``pack-manifest.json``
as GitHub Release assets. Uses the ``gh`` CLI (preferred) or the GitHub
REST API as a fallback (when ``gh`` is unavailable / in CI without
``gh`` installed).

This script is the CI-side counterpart to the client-side
:mod:`voice_typer.server.service.update_check` module. The publisher
uploads the assets; the checker fetches ``pack-manifest.json`` from the
same release to decide whether a newer pack is available.

Asset naming convention (C-CI-13 — the new artifact-naming rule
introduced by the installer split):

  * Slim-core installer:
      - Windows: ``VoiceTyper-Setup-<version>.exe`` (NSIS)
      - macOS:   ``VoiceTyper-<version>.<arch>.app.tar.gz``
      - Linux:   ``voice-typer-<version>-<arch>.AppImage``
  * Pack onefile:     ``pack-<version>.zip``
  * Pack manifest:    ``pack-manifest.json`` (NOT versioned — the
    ``releases/latest/download/pack-manifest.json`` URL serves the
    latest release's manifest).

The pack onefile + manifest are platform-independent (the pack contains
platform-specific binaries inside, but the zip + manifest are the same
file shape across platforms). The slim-core installer IS platform-
specific — publish one per platform.

Usage (CI):

  python scripts/release/publish_pack_release.py \\
      --tag v1.2.3 \\
      --repo AbdallahIsDev/voice-typer \\
      --slim-core-windows dist/VoiceTyper-Setup-1.2.3.exe \\
      --slim-core-macos dist/VoiceTyper-1.2.3.arm64.app.tar.gz \\
      --slim-core-linux dist/voice-typer-1.2.3-x86_64.AppImage \\
      --pack-onefile dist/pack-1.2.3.zip \\
      --pack-manifest dist/pack-manifest.json \\
      --notes "Release notes for 1.2.3"

Or programmatically:

  from scripts.release.publish_pack_release import publish_release
  result = publish_release(
      tag="v1.2.3",
      assets=[Path("dist/pack-1.2.3.zip"), Path("dist/pack-manifest.json")],
      repo="AbdallahIsDev/voice-typer",
      notes="Release notes for 1.2.3",
  )

The script is idempotent: re-running with the same tag uploads any
missing assets and skips already-uploaded ones (``gh release upload
--clobber`` replaces existing assets with the same name). This lets CI
retry a partially-failed publish without manual cleanup.

SECURITY: this script does NOT sign the assets. Code signing is a
separate CI step (C-CI-11 — the existing 4 signing steps + the new
worker-exe signing). The publisher only uploads already-signed
artifacts. The pack's integrity is verified client-side via the
SHA-256 in ``pack-manifest.json`` (see
:func:`voice_typer.server.service.offline_pack.verify_offline_pack_or_skip`).

Exit codes:
  0 — success (all assets uploaded).
  1 — failure (``gh`` / API error, missing asset, etc.).
  2 — usage error (missing required args).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────

DEFAULT_REPO = "AbdallahIsDev/voice-typer"
DEFAULT_GH_CLI = "gh"

# Asset-name templates (C-CI-13). The publisher does NOT enforce these
# names — it uploads whatever paths the caller passes. The templates
# are documented here so CI workflows can construct the expected names
# consistently.
ASSET_NAME_TEMPLATES = {
    "slim_core_windows": "VoiceTyper-Setup-{version}.exe",
    "slim_core_macos": "VoiceTyper-{version}.{arch}.app.tar.gz",
    "slim_core_linux": "voice-typer-{version}-{arch}.AppImage",
    "pack_onefile": "pack-{version}.zip",
    "pack_manifest": "pack-manifest.json",  # NOT versioned
}


# ── Result types ────────────────────────────────────────────────────────


@dataclass
class PublishResult:
    """Outcome of a release publish attempt.

    Fields:
        success: True if all assets were uploaded (or already present).
        tag: the release tag (e.g. ``"v1.2.3"``).
        release_url: the GitHub release URL (``https://github.com/.../releases/tag/v1.2.3``).
        uploaded: list of asset names that were uploaded this run.
        skipped: list of asset names that were already present (idempotent re-run).
        errors: list of per-asset error messages (empty on full success).
        backend: which backend was used (``"gh"`` or ``"api"``).
    """

    success: bool
    tag: str
    release_url: str | None = None
    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backend: str = "gh"


# ── gh CLI backend ──────────────────────────────────────────────────────


def build_gh_create_command(
    tag: str,
    *,
    repo: str,
    notes: str | None,
    notes_file: Path | None,
    draft: bool,
    prerelease: bool,
    target: str | None,
    title: str | None,
) -> list[str]:
    """Build the ``gh release create`` argv.

    The command creates the release WITHOUT assets — assets are uploaded
    in a separate ``gh release upload`` step so a partial asset upload
    can be retried without recreating the release.

    ``--generate-notes`` is intentionally NOT used — the caller passes
    explicit ``--notes`` / ``--notes-file`` so the release notes are
    deterministic (GitHub's auto-generated notes include commit titles
    that may leak internal context).
    """
    cmd: list[str] = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repo,
    ]
    if draft:
        cmd.append("--draft")
    if prerelease:
        cmd.append("--prerelease")
    if target:
        cmd.extend(["--target", target])
    if title:
        cmd.extend(["--title", title])
    if notes_file is not None:
        cmd.extend(["--notes-file", str(notes_file)])
    elif notes is not None:
        cmd.extend(["--notes", notes])
    else:
        # No notes — use an empty string so GitHub doesn't auto-generate.
        cmd.extend(["--notes", ""])
    return cmd


def build_gh_upload_command(
    tag: str,
    assets: list[Path],
    *,
    repo: str,
    clobber: bool = True,
) -> list[str]:
    """Build the ``gh release upload`` argv.

    ``--clobber`` replaces existing assets with the same name (idempotent
    re-run). Without it, ``gh release upload`` fails on the first
    duplicate asset name.
    """
    cmd: list[str] = [
        "gh",
        "release",
        "upload",
        tag,
        *[str(a) for a in assets],
        "--repo",
        repo,
    ]
    if clobber:
        cmd.append("--clobber")
    return cmd


def run_gh(cmd: list[str], *, runner: callable | None = None) -> subprocess.CompletedProcess:
    """Run a ``gh`` command, returning the completed process.

    ``runner`` is injectable for testing (defaults to
    :func:`subprocess.run`). Tests substitute a fake to avoid spawning
    ``gh``.
    """
    if runner is None:
        runner = subprocess.run  # type: ignore[assignment]
    return runner(cmd, capture_output=True, text=True, timeout=300)  # type: ignore[call-arg]


def gh_release_exists(tag: str, *, repo: str, runner: callable | None = None) -> bool:
    """Return True if a release with *tag* already exists.

    Uses ``gh release view`` — exit 0 means the release exists, non-zero
    means it doesn't (or ``gh`` failed).
    """
    cmd = ["gh", "release", "view", tag, "--repo", repo]
    result = run_gh(cmd, runner=runner)
    return result.returncode == 0


def gh_release_url(tag: str, *, repo: str, runner: callable | None = None) -> str | None:
    """Return the GitHub release URL for *tag*, or ``None`` if it can't be resolved."""
    cmd = ["gh", "release", "view", tag, "--repo", repo, "--json", "url", "--jq", ".url"]
    result = run_gh(cmd, runner=runner)
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


# ── GitHub API backend (fallback when ``gh`` is unavailable) ────────────

# We use ``urllib.request`` (stdlib) to avoid adding a ``requests`` /
# ``httpx`` dependency just for the publisher. The publisher runs in CI
# where ``gh`` is the preferred backend; the API fallback is a safety
# net for environments without ``gh`` (e.g. a local dev machine).


def _api_request(
    method: str,
    url: str,
    *,
    token: str,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes]:
    """Make a GitHub API request via :mod:`urllib.request`.

    Returns ``(status_code, response_body_bytes)``. Raises ``OSError``
    on network failure.
    """
    import urllib.request

    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "voice-typer-release-publisher")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        # HTTPError is a subclass of URLError (OSError) — read the body
        # so the caller can surface the GitHub API error message. The
        # read is best-effort: we already have the HTTP status code (the
        # most important field); the body is just supplementary context.
        # ``exc.read()`` can raise ``OSError`` (socket closed / partial
        # body) or ``http.client.HTTPException`` (IncompleteRead) — we
        # suppress any failure so a flaky body read does not mask the
        # original HTTP error.
        body_bytes = b""
        with contextlib.suppress(Exception):
            body_bytes = exc.read()
        return exc.code, body_bytes


def api_create_release(
    tag: str,
    *,
    repo: str,
    token: str,
    notes: str | None,
    draft: bool,
    prerelease: bool,
    target: str | None,
    title: str | None,
) -> tuple[str | None, str | None]:
    """Create a release via the GitHub REST API.

    Returns ``(release_url, error_message)``. On success,
    ``error_message`` is ``None``. On failure, ``release_url`` is
    ``None`` and ``error_message`` describes the failure.
    """
    url = f"https://api.github.com/repos/{repo}/releases"
    payload: dict[str, object] = {
        "tag_name": tag,
        "name": title or tag,
        "body": notes or "",
        "draft": draft,
        "prerelease": prerelease,
    }
    if target:
        payload["target_commitish"] = target
    body = json.dumps(payload).encode("utf-8")
    status, resp_body = _api_request("POST", url, token=token, body=body)
    if status not in (200, 201):
        return None, f"GitHub API returned {status}: {resp_body.decode('utf-8', errors='replace')[:500]}"
    try:
        data = json.loads(resp_body)
    except json.JSONDecodeError:
        return None, f"GitHub API returned non-JSON response: {resp_body[:200]!r}"
    release_url = data.get("html_url")
    upload_url_template = data.get("upload_url", "")
    # The upload_url is a templated URL like
    # ``https://uploads.github.com/repos/.../releases/123/assets{?name,label}``.
    # Strip the template suffix so we can append ``?name=<asset>``.
    upload_url = upload_url_template.split("{", 1)[0] if upload_url_template else None
    return release_url, upload_url


def api_upload_asset(
    upload_url: str,
    asset: Path,
    *,
    token: str,
) -> tuple[bool, str | None]:
    """Upload a single asset via the GitHub REST API.

    Returns ``(success, error_message)``. On success, ``error_message``
    is ``None``.
    """
    import urllib.parse

    if not asset.exists():
        return False, f"asset not found: {asset}"
    asset_name = asset.name
    url = f"{upload_url}?name={urllib.parse.quote(asset_name)}"
    body = asset.read_bytes()
    # GitHub requires ``application/octet-stream`` for asset uploads.
    status, resp_body = _api_request("POST", url, token=token, body=body, content_type="application/octet-stream")
    if status not in (200, 201):
        body_snippet = resp_body.decode("utf-8", errors="replace")[:500]
        return False, f"GitHub API returned {status} for {asset_name}: {body_snippet}"
    return True, None


# ── Asset validation ────────────────────────────────────────────────────


def validate_assets(assets: list[Path]) -> list[str]:
    """Return a list of error messages for missing / invalid assets.

    An asset is invalid if:
      * it does not exist (broken CI artifact path).
      * it is empty (0 bytes — a failed build produced an empty file).
      * it is a directory (caller passed a dir instead of a file).
    """
    errors: list[str] = []
    for asset in assets:
        if not asset.exists():
            errors.append(f"asset not found: {asset}")
            continue
        if asset.is_dir():
            errors.append(f"asset is a directory, not a file: {asset}")
            continue
        if asset.stat().st_size == 0:
            errors.append(f"asset is empty (0 bytes): {asset}")
    return errors


# ── Main publish flow ───────────────────────────────────────────────────


def publish_release(
    tag: str,
    assets: list[Path],
    *,
    repo: str = DEFAULT_REPO,
    notes: str | None = None,
    notes_file: Path | None = None,
    draft: bool = False,
    prerelease: bool = False,
    target: str | None = None,
    title: str | None = None,
    gh_cli: str = DEFAULT_GH_CLI,
    runner: callable | None = None,
    token: str | None = None,
    backend: str | None = None,
) -> PublishResult:
    """Publish a GitHub Release with the given assets.

    Args:
        tag: the release tag (e.g. ``"v1.2.3"``).
        assets: list of asset file paths to upload.
        repo: the ``owner/name`` repo slug.
        notes: release notes body (markdown). Mutually exclusive with
            ``notes_file``.
        notes_file: path to a release-notes file. Mutually exclusive
            with ``notes``.
        draft: create as a draft release (not visible to users).
        prerelease: mark as a pre-release.
        target: target commitish (branch / SHA) for the release.
        title: release title (defaults to the tag).
        gh_cli: the ``gh`` CLI binary name (defaults to ``"gh"``).
        runner: injectable subprocess runner for testing (defaults to
            :func:`subprocess.run`).
        token: GitHub API token (for the API backend). Falls back to
            the ``GH_TOKEN`` / ``GITHUB_TOKEN`` env var.
        backend: force a specific backend (``"gh"`` or ``"api"``).
            ``None`` (default) auto-selects: ``"gh"`` if the ``gh`` CLI
            is available, else ``"api"``.

    Returns:
        :class:`PublishResult`. Never raises — all errors are captured
        in ``result.errors``. The caller checks ``result.success``.
    """
    # Validate assets first — fail fast on missing/empty files.
    asset_errors = validate_assets(assets)
    if asset_errors:
        return PublishResult(
            success=False,
            tag=tag,
            errors=asset_errors,
            backend=backend or "gh",
        )

    # Auto-select backend if not forced.
    if backend is None:
        gh_path = shutil.which(gh_cli) if runner is None else DEFAULT_GH_CLI
        backend = "gh" if gh_path else "api"
        log.info("[RELEASE] auto-selected backend: %s", backend)

    if backend == "gh":
        return _publish_via_gh(
            tag,
            assets,
            repo=repo,
            notes=notes,
            notes_file=notes_file,
            draft=draft,
            prerelease=prerelease,
            target=target,
            title=title,
            gh_cli=gh_cli,
            runner=runner,
        )
    if backend == "api":
        return _publish_via_api(
            tag,
            assets,
            repo=repo,
            notes=notes,
            draft=draft,
            prerelease=prerelease,
            target=target,
            title=title,
            token=token,
        )
    return PublishResult(
        success=False,
        tag=tag,
        errors=[f"unknown backend: {backend!r} (expected 'gh' or 'api')"],
        backend=backend,
    )


def _publish_via_gh(
    tag: str,
    assets: list[Path],
    *,
    repo: str,
    notes: str | None,
    notes_file: Path | None,
    draft: bool,
    prerelease: bool,
    target: str | None,
    title: str | None,
    gh_cli: str,
    runner: callable | None,
) -> PublishResult:
    """Publish via the ``gh`` CLI backend."""
    # Create the release if it doesn't exist (idempotent).
    if gh_release_exists(tag, repo=repo, runner=runner):
        log.info("[RELEASE] release %s already exists — uploading assets only", tag)
    else:
        cmd = build_gh_create_command(
            tag,
            repo=repo,
            notes=notes,
            notes_file=notes_file,
            draft=draft,
            prerelease=prerelease,
            target=target,
            title=title,
        )
        cmd[0] = gh_cli  # respect the configured binary name
        result = run_gh(cmd, runner=runner)
        if result.returncode != 0:
            return PublishResult(
                success=False,
                tag=tag,
                errors=[f"gh release create failed (exit {result.returncode}): {result.stderr.strip()}"],
                backend="gh",
            )

    # Upload assets (``--clobber`` makes it idempotent).
    upload_cmd = build_gh_upload_command(tag, assets, repo=repo, clobber=True)
    upload_cmd[0] = gh_cli
    upload_result = run_gh(upload_cmd, runner=runner)
    if upload_result.returncode != 0:
        return PublishResult(
            success=False,
            tag=tag,
            release_url=gh_release_url(tag, repo=repo, runner=runner),
            errors=[f"gh release upload failed (exit {upload_result.returncode}): {upload_result.stderr.strip()}"],
            backend="gh",
        )

    return PublishResult(
        success=True,
        tag=tag,
        release_url=gh_release_url(tag, repo=repo, runner=runner),
        uploaded=[a.name for a in assets],
        backend="gh",
    )


def _publish_via_api(
    tag: str,
    assets: list[Path],
    *,
    repo: str,
    notes: str | None,
    draft: bool,
    prerelease: bool,
    target: str | None,
    title: str | None,
    token: str | None,
) -> PublishResult:
    """Publish via the GitHub REST API backend (fallback when ``gh`` is unavailable)."""
    if token is None:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return PublishResult(
            success=False,
            tag=tag,
            errors=[
                "GitHub API backend requires a token — set GH_TOKEN / GITHUB_TOKEN env var "
                "or pass --token. (The gh CLI backend authenticates via `gh auth login`.)"
            ],
            backend="api",
        )

    # Create the release.
    # ``api_create_release`` returns ``(release_url, upload_url)`` on
    # success and ``(None, error_message)`` on failure. The two cases
    # are distinguished by whether ``release_url`` is ``None``.
    release_url, upload_url_or_err = api_create_release(
        tag,
        repo=repo,
        token=token,
        notes=notes,
        draft=draft,
        prerelease=prerelease,
        target=target,
        title=title,
    )
    upload_url: str | None = None
    if release_url is not None:
        # Success — ``upload_url_or_err`` is actually the upload_url.
        upload_url = upload_url_or_err
    else:
        # Failure — ``upload_url_or_err`` is the error message. Check
        # whether the failure is the "release already exists" case
        # (GitHub returns 422 with ``already_exists`` in the body). If
        # so, re-fetch the release by tag to get its upload_url.
        err_msg = upload_url_or_err or "unknown API error creating release"
        if "already_exists" in err_msg.lower() or "already_exists" in err_msg:
            url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
            status, resp_body = _api_request("GET", url, token=token)
            if status != 200:
                return PublishResult(
                    success=False,
                    tag=tag,
                    errors=[f"failed to fetch existing release {tag}: status {status}"],
                    backend="api",
                )
            try:
                data = json.loads(resp_body)
            except json.JSONDecodeError:
                return PublishResult(
                    success=False,
                    tag=tag,
                    errors=["existing release returned non-JSON"],
                    backend="api",
                )
            release_url = data.get("html_url")
            upload_url = (data.get("upload_url") or "").split("{", 1)[0] or None
        else:
            return PublishResult(
                success=False,
                tag=tag,
                errors=[err_msg],
                backend="api",
            )

    if not upload_url:
        return PublishResult(
            success=False,
            tag=tag,
            errors=["release has no upload_url — cannot upload assets"],
            backend="api",
        )

    # Upload assets.
    errors: list[str] = []
    uploaded: list[str] = []
    for asset in assets:
        ok, err = api_upload_asset(upload_url, asset, token=token)
        if ok:
            uploaded.append(asset.name)
        else:
            errors.append(err or f"unknown error uploading {asset.name}")

    return PublishResult(
        success=len(errors) == 0,
        tag=tag,
        release_url=release_url,
        uploaded=uploaded,
        errors=errors,
        backend="api",
    )


# ── CLI ─────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Publish a GitHub Release with the slim-core installer + "
            "pack onefile + pack-manifest.json (plan-runtime-pack-split.md §10.1)."
        ),
    )
    parser.add_argument("--tag", required=True, help="Release tag (e.g. v1.2.3)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name repo slug")
    parser.add_argument(
        "--slim-core-windows",
        type=Path,
        default=None,
        help="Path to the Windows slim-core installer (.exe)",
    )
    parser.add_argument(
        "--slim-core-macos",
        type=Path,
        default=None,
        help="Path to the macOS slim-core installer (.app.tar.gz)",
    )
    parser.add_argument(
        "--slim-core-linux",
        type=Path,
        default=None,
        help="Path to the Linux slim-core installer (.AppImage)",
    )
    parser.add_argument(
        "--pack-onefile",
        type=Path,
        default=None,
        help="Path to the pack onefile (pack-<version>.zip)",
    )
    parser.add_argument(
        "--pack-manifest",
        type=Path,
        default=None,
        help="Path to pack-manifest.json",
    )
    parser.add_argument(
        "--asset",
        action="append",
        type=Path,
        default=[],
        help="Additional assets to upload (repeatable)",
    )
    parser.add_argument("--notes", default=None, help="Release notes body (markdown)")
    parser.add_argument(
        "--notes-file",
        type=Path,
        default=None,
        help="Path to release notes file (mutually exclusive with --notes)",
    )
    parser.add_argument("--draft", action="store_true", help="Create as draft")
    parser.add_argument("--prerelease", action="store_true", help="Mark as pre-release")
    parser.add_argument("--target", default=None, help="Target commitish (branch/SHA)")
    parser.add_argument("--title", default=None, help="Release title (defaults to tag)")
    parser.add_argument("--gh-cli", default=DEFAULT_GH_CLI, help="gh CLI binary name")
    parser.add_argument(
        "--backend",
        choices=["gh", "api"],
        default=None,
        help="Force a specific backend (default: auto-select)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub API token (for --backend=api; defaults to GH_TOKEN/GITHUB_TOKEN env var)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON (for CI parsing)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Returns 0 on success, 1 on failure, 2 on usage error.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    # Collect assets from the named flags + --asset repeats.
    assets: list[Path] = []
    for path in (
        args.slim_core_windows,
        args.slim_core_macos,
        args.slim_core_linux,
        args.pack_onefile,
        args.pack_manifest,
    ):
        if path is not None:
            assets.append(path)
    assets.extend(args.asset)

    if not assets:
        print("error: at least one asset is required", file=sys.stderr)
        return 2

    if args.notes is not None and args.notes_file is not None:
        print("error: --notes and --notes-file are mutually exclusive", file=sys.stderr)
        return 2

    result = publish_release(
        tag=args.tag,
        assets=assets,
        repo=args.repo,
        notes=args.notes,
        notes_file=args.notes_file,
        draft=args.draft,
        prerelease=args.prerelease,
        target=args.target,
        title=args.title,
        gh_cli=args.gh_cli,
        token=args.token,
        backend=args.backend,
    )

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        if result.success:
            print(f"✓ Published release {result.tag} ({result.backend} backend)")
            if result.release_url:
                print(f"  URL: {result.release_url}")
            for name in result.uploaded:
                print(f"  uploaded: {name}")
        else:
            print(f"✗ Failed to publish release {result.tag} ({result.backend} backend)", file=sys.stderr)
            for err in result.errors:
                print(f"  error: {err}", file=sys.stderr)

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ASSET_NAME_TEMPLATES",
    "DEFAULT_GH_CLI",
    "DEFAULT_REPO",
    "PublishResult",
    "api_create_release",
    "api_upload_asset",
    "build_gh_create_command",
    "build_gh_upload_command",
    "gh_release_exists",
    "gh_release_url",
    "main",
    "publish_release",
    "run_gh",
    "validate_assets",
]
