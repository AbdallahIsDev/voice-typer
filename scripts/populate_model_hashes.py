#!/usr/bin/env python3
"""Populate file-level SHA-256 hashes in voice_typer/server/model_hashes.json.

SEC-audit-005 / Task 7 (Round 6): The manifest previously pinned only the
immutable HuggingFace commit SHA per repo (the ``revision`` field) and, at
most, a single ``config.json`` hash in the ``files`` dict.  This script
extends the manifest so that *every* file in each pinned revision has a
SHA-256 digest recorded.  ``voice_typer.server.security.verify_model_integrity``
then hard-fails on any local-file hash mismatch, giving end-to-end
supply-chain integrity for downloaded models.

Design (no large downloads required):
    HuggingFace stores large model weights in Git LFS.  The git tree at a
    pinned revision references *LFS pointer files* (small text blobs of the
    form::

        version https://git-lfs.github.com/spec/v1
        oid sha256:<hex>
        size <bytes>

    ) rather than the multi-gigabyte weights themselves.  The
    ``oid sha256:`` field is *exactly* the SHA-256 of the real file content,
    so we can record it without downloading the weights.

    For non-LFS files (``config.json``, ``README.md``, ``.gitattributes``,
    ``tokenizer.json`` if small enough to be in git, etc.) we fetch the raw
    blob via the ``/raw/{revision}/{path}`` endpoint and compute the SHA-256
    locally.

    The script is therefore fast (a few HTTP round-trips per repo, no large
    downloads) and safe to run in CI on every release.

Usage::

    python scripts/populate_model_hashes.py [--dry-run] [--repo REPO]

Options:
    --dry-run      Compute hashes and print a diff but do not write the file.
    --repo REPO    Only process the named repo (may be repeated).  Default:
                   every HuggingFace repo in model_hashes.json (``qwen`` is
                   skipped because it is a local model).

Exit codes:
    0  manifest updated (or --dry-run with no changes)
    1  irreversible error (network failure, parse error, schema violation)
    2  --dry-run AND changes detected (useful for CI gates)

The script is idempotent: re-running with no upstream changes is a no-op.
It NEVER modifies the ``revision`` field of any entry — only the ``files``
dict.  It preserves the ``_comment`` metadata key verbatim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve the repo root regardless of CWD so the script works in CI checkout.
REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "voice_typer" / "server" / "model_hashes.json"
SECURITY_PATH = REPO_ROOT / "voice_typer" / "server" / "security.py"

# HuggingFace endpoints.  ``raw`` returns the git blob at the pinned revision
# (LFS pointer for LFS-tracked files, real content otherwise).
HF_TREE_URL = "https://huggingface.co/api/models/{repo}/tree/{revision}?recursive=true"
HF_RAW_URL = "https://huggingface.co/{repo}/raw/{revision}/{path}"

# The LFS pointer file begins with this magic line.
LFS_POINTER_MAGIC = b"version https://git-lfs.github.com/spec/v1"

# HTTP timeouts / retries.  We never download large files, so 30s is plenty.
HTTP_TIMEOUT = 30.0
HTTP_RETRIES = 3
HTTP_BACKOFF = 1.5  # seconds, multiplied by attempt index
USER_AGENT = "voice-typer-populate-hashes/1.0"

# Skip repos whose ``revision`` is not a HuggingFace commit SHA.  Currently
# only ``qwen`` (a local model with ``revision == "local"``) is skipped.
LOCAL_REVISION_VALUES = {"local"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get(url: str) -> bytes:
    """Fetch ``url`` with retries; raise on final failure."""
    last_exc: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_exc = exc
            # 404 is terminal — retrying won't help.
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
            if attempt < HTTP_RETRIES - 1:
                time.sleep(HTTP_BACKOFF * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _list_repo_files(repo: str, revision: str) -> list[dict[str, Any]]:
    """Return the file entries from the HF tree API for ``repo`` @ ``revision``."""
    url = HF_TREE_URL.format(repo=repo, revision=revision)
    raw = _http_get(url)
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Tree API for {repo}@{revision} returned non-JSON: {raw[:200]!r}"
        ) from exc
    if not isinstance(data, list):
        raise RuntimeError(
            f"Tree API for {repo}@{revision} returned {type(data).__name__}, expected list"
        )
    # The tree API includes directory entries (``type == "directory"``) — skip
    # them; we only hash files.
    return [entry for entry in data if entry.get("type") == "file"]


def _fetch_file_sha256(repo: str, revision: str, path: str) -> str:
    """Compute the SHA-256 of file ``path`` at ``repo``@``revision``.

    Uses the LFS pointer's ``oid`` field for LFS-managed files (no large
    download), otherwise downloads the raw blob and hashes it.
    """
    url = HF_RAW_URL.format(repo=repo, revision=revision, path=path)
    blob = _http_get(url)

    if blob.startswith(LFS_POINTER_MAGIC):
        # LFS pointer file — parse the ``oid sha256:<hex>`` line.
        for line in blob.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("oid sha256:"):
                oid = line[len("oid sha256:"):]
                if len(oid) == 64 and all(c in "0123456789abcdef" for c in oid):
                    return oid
                raise RuntimeError(
                    f"LFS pointer for {repo}/{path} has malformed oid: {oid!r}"
                )
        raise RuntimeError(
            f"LFS pointer for {repo}/{path} is missing the oid sha256 line: {blob!r}"
        )

    # Regular file — hash the raw bytes.
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Manifest update
# ---------------------------------------------------------------------------

def _is_hf_repo(entry: dict[str, Any]) -> bool:
    """True if the entry represents a HuggingFace repo (not a local model)."""
    revision = entry.get("revision", "")
    return revision not in LOCAL_REVISION_VALUES and bool(revision)


def _ordered_files(files: dict[str, str]) -> dict[str, str]:
    """Return ``files`` sorted by path for stable diffs."""
    return {k: files[k] for k in sorted(files)}


# ---------------------------------------------------------------------------
# security.py fallback sync
# ---------------------------------------------------------------------------

# The hardcoded fallback dict in security.py must mirror model_hashes.json
# exactly (required by test_model_hashes_fallback_matches_json).  We anchor
# the rewrite on the comment that introduces the fallback to make it robust
# to indentation changes.
_FALLBACK_ANCHOR = (
    "# Hardcoded fallback — mirrors model_hashes.json so that even if the JSON\n"
)


def _format_fallback_literal(manifest: dict[str, Any]) -> str:
    """Render the fallback dict as Python source (matches security.py style).

    Excludes ``_comment`` and the ``qwen`` local-model entry, mirroring the
    pre-existing convention.  Lines that would exceed ruff's 120-char limit
    get a trailing ``# noqa: E501`` so the generated source stays lint-clean
    (some pinned paths — e.g. ``.eval_results/open_asr_leaderboard.yaml`` —
    plus their 64-char hash cannot fit on one line at any indentation).
    """
    ruff_line_length = 120
    lines: list[str] = ["    return {"]
    for repo_id, entry in manifest.items():
        if repo_id.startswith("_") or repo_id == "qwen":
            continue
        if not isinstance(entry, dict) or not _is_hf_repo(entry):
            continue
        lines.append(f'        "{repo_id}": {{')
        lines.append(f'            "revision": "{entry["revision"]}",')
        lines.append('            "files": {')
        files = entry.get("files", {})
        for path, digest in files.items():
            line = f'                "{path}": "{digest}",'
            if len(line) > ruff_line_length:
                line = f'{line}  # noqa: E501'
            lines.append(line)
        lines.append("            },")
        lines.append("        },")
    lines.append("    }")
    return "\n".join(lines) + "\n"


def _sync_security_fallback(manifest: dict[str, Any]) -> bool:
    """Rewrite the hardcoded fallback dict in security.py to mirror ``manifest``.

    Returns True if the file was modified.
    """
    src = SECURITY_PATH.read_text(encoding="utf-8")
    # Find the anchor comment, then the next multi-line ``return {`` after it.
    # We require ``"    return {\n"`` (4-space indent + newline after ``{``)
    # to avoid matching the single-line dict-comprehension return inside the
    # ``if json_path.exists()`` block above.
    anchor_idx = src.find(_FALLBACK_ANCHOR)
    if anchor_idx == -1:
        raise RuntimeError(
            "Could not find fallback anchor comment in security.py — "
            "has the file been refactored? The script needs the marker:\n"
            + _FALLBACK_ANCHOR
        )
    return_idx = src.find("    return {\n", anchor_idx)
    if return_idx == -1:
        raise RuntimeError(
            "Could not find multi-line 'return {' after fallback anchor in security.py"
        )
    # The fallback dict's closing brace is the first ``\n    }\n`` after the
    # return — inner per-repo and per-files braces either have more leading
    # whitespace (8 or 12 spaces) or are followed by a comma, so they never
    # match this pattern.
    close_idx = src.find("\n    }\n", return_idx)
    if close_idx == -1:
        raise RuntimeError(
            "Could not find closing brace of fallback dict in security.py"
        )
    # Replace [return_idx : close_idx + len("\n    }")] with the new literal.
    # The new literal already ends with "    }\n", so we slice off the
    # matched "\n    }\n" entirely from the original.
    new_literal = _format_fallback_literal(manifest)
    new_src = src[:return_idx] + new_literal + src[close_idx + len("\n    }\n"):]
    if new_src == src:
        return False
    SECURITY_PATH.write_text(new_src, encoding="utf-8")
    return True


def populate_manifest(
    manifest_path: Path = MANIFEST_PATH,
    *,
    only_repos: list[str] | None = None,
    dry_run: bool = False,
    sync_security_fallback: bool = True,
) -> int:
    """Populate the ``files`` dict for every HuggingFace repo in the manifest.

    When ``sync_security_fallback`` is True (default), the hardcoded fallback
    dict in ``voice_typer/server/security.py`` is also rewritten so it stays
    in lock-step with the JSON manifest (required by the
    ``test_model_hashes_fallback_matches_json`` regression test).

    Returns the number of repos whose ``files`` dict changed.
    """
    original_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original_text)

    # Preserve _comment and insertion order of top-level keys.
    comment = manifest.get("_comment")

    changed_repos = 0
    for repo_id, entry in manifest.items():
        if repo_id.startswith("_"):
            continue
        if not isinstance(entry, dict):
            print(f"[WARN] {repo_id}: entry is not a dict, skipping", file=sys.stderr)
            continue
        if not _is_hf_repo(entry):
            print(f"[SKIP] {repo_id}: local model (revision={entry.get('revision')!r})")
            continue
        if only_repos and repo_id not in only_repos:
            continue

        revision = entry["revision"]
        print(f"[INFO] {repo_id} @ {revision[:12]}… : enumerating files")
        try:
            files = _list_repo_files(repo_id, revision)
        except Exception as exc:
            print(
                f"[ERROR] {repo_id}: failed to list files at {revision}: {exc}",
                file=sys.stderr,
            )
            raise

        new_files: dict[str, str] = {}
        for f in files:
            path = f["path"]
            try:
                digest = _fetch_file_sha256(repo_id, revision, path)
            except Exception as exc:
                print(
                    f"[ERROR] {repo_id}/{path}: failed to fetch/hash: {exc}",
                    file=sys.stderr,
                )
                raise
            new_files[path] = digest
            print(f"        {path:50s} sha256={digest[:16]}…")

        old_files = entry.get("files", {})
        # Sort for deterministic output.
        new_files = _ordered_files(new_files)
        if new_files != old_files:
            changed_repos += 1
            entry["files"] = new_files
            added = set(new_files) - set(old_files)
            removed = set(old_files) - set(new_files)
            changed = {
                k for k in set(old_files) & set(new_files)
                if old_files[k] != new_files[k]
            }
            if added:
                print(f"        + {len(added)} new file(s)")
            if removed:
                print(f"        - {len(removed)} removed file(s): {sorted(removed)}")
            if changed:
                print(f"        ~ {len(changed)} changed hash(es): {sorted(changed)}")
        else:
            print("        (no change)")

    # Re-serialize preserving _comment position (it was the first key in the
    # source file).  We rebuild the dict with _comment first, then all other
    # keys in their original order.
    output: dict[str, Any] = {}
    if comment is not None:
        output["_comment"] = comment
    for k, v in manifest.items():
        if k == "_comment":
            continue
        output[k] = v

    new_text = json.dumps(output, indent=4, ensure_ascii=False) + "\n"
    manifest_changed = new_text != original_text

    if dry_run:
        if manifest_changed:
            print("[DRY-RUN] Manifest would be updated. Diff (first 60 lines):")
            import difflib
            diff = difflib.unified_diff(
                original_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=str(manifest_path),
                tofile=str(manifest_path) + " (new)",
                n=2,
            )
            for i, line in enumerate(diff):
                if i >= 60:
                    print("... (truncated)")
                    break
                print(line, end="")
        else:
            print("[DRY-RUN] Manifest already up to date.")
        if sync_security_fallback:
            print("[DRY-RUN] security.py fallback sync would also be attempted.")
        return changed_repos

    if manifest_changed:
        manifest_path.write_text(new_text, encoding="utf-8")
        print(f"[OK] Wrote {manifest_path} ({changed_repos} repo(s) updated)")
    else:
        print(f"[INFO] {manifest_path} already up to date.")

    if sync_security_fallback:
        try:
            if _sync_security_fallback(manifest):
                print(f"[OK] Synced fallback dict in {SECURITY_PATH}")
            else:
                print(f"[INFO] Fallback dict in {SECURITY_PATH} already in sync")
        except Exception as exc:
            print(
                f"[ERROR] Failed to sync security.py fallback: {exc}",
                file=sys.stderr,
            )
            raise

    return changed_repos


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate file-level SHA-256 hashes in model_hashes.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute hashes and print a diff but do not write the file.",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="REPO",
        help="Only process the named repo (may be repeated).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
        help=f"Path to model_hashes.json (default: {MANIFEST_PATH}).",
    )
    parser.add_argument(
        "--no-security-sync",
        action="store_true",
        help="Do NOT update the hardcoded fallback dict in security.py "
             "(default: keep it in sync with the JSON manifest).",
    )
    args = parser.parse_args(argv)

    try:
        changed = populate_manifest(
            manifest_path=args.manifest,
            only_repos=args.repo or None,
            dry_run=args.dry_run,
            sync_security_fallback=not args.no_security_sync,
        )
    except Exception as exc:
        print(f"[FATAL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.dry_run and changed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
