"""Model integrity verification (SEC-audit-005) + download allowlists.

Part of the :mod:`voice_typer.server.security` package (EO-23
consolidation). Merges the model-integrity half of the former
``voice_typer.server.security`` module (SHA-256 manifest verification,
on-disk integrity cache) with the file-pattern allowlists from the
former ``voice_typer.server._model_integrity`` module.

The PII/secret redaction helpers that previously shared the former
``security.py`` now live in :mod:`voice_typer.server.security.redaction`.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import mmap
import os
import threading
from pathlib import Path
from typing import Any

from voice_typer.server.security.file_io import _secure_read_text

log = logging.getLogger(__name__)


def _security_pkg():
    """Return the ``voice_typer.server.security`` package (call-time lookup).

    The integrity functions resolve ``MODEL_HASHES`` / ``compute_file_sha256``
    / ``_secure_atomic_write`` / ``_integrity_cache_path_override`` through
    the PACKAGE namespace at call time -- not through this module's own
    globals -- so existing tests that monkeypatch
    ``voice_typer.server.security.<name>`` keep working (the package
    ``__init__`` re-exports these symbols, and the re-export is what the
    tests patch). Same pattern as ``PersistedJSON.save``'s lazy
    ``voice_typer.server.config._secure_atomic_write`` lookup.
    """
    import voice_typer.server.security as _security

    return _security


# ─── SEC-audit-005: Model Integrity Verification ──────────────────────────

# Pinned revisions for HuggingFace model downloads.
# When a specific commit SHA is known, it should be recorded here so that
# snapshot_download() pins to an exact version instead of the mutable "main"
# branch.  This prevents supply-chain attacks where a compromised repo
# pushes a new commit with malicious model files.
#
# The canonical source is ``model_hashes.json`` in ``voice_typer/server/``
# (two levels up from this submodule — ``voice_typer/server/security/``).
# The JSON file can be updated by the release process without touching
# Python source code.  We fall back to a hardcoded dict if the JSON file is
# missing or unreadable (e.g. during unit tests in isolated envs).


def _load_model_hashes() -> dict[str, dict[str, Any]]:
    """Load MODEL_HASHES from the companion JSON file, with hardcoded fallback.

    the value type is widened from ``dict[str, str]`` to
    ``dict[str, Any]`` because each manifest entry mixes value kinds
    (``"revision": "main"`` is a str, ``"files": {filename: hash}`` is
    a nested dict).  The narrower annotation made
    ``manifest.get("files", {})`` infer as ``str`` and broke the
    downstream ``.items()`` call in both security.py and qwen_engine.py.
    """
    json_path = Path(__file__).resolve().parent.parent / "model_hashes.json"
    if json_path.exists():
        try:
            # use ``_secure_read_text`` (POSIX ``O_NOFOLLOW`` /
            # Windows reparse-point rejection) instead of ``Path.read_text``
            # so a symlink planted at ``model_hashes.json`` cannot redirect
            # the read to an attacker-controlled file and inject pinned
            # SHA-256 entries. On a symlink, ``_secure_read_text`` raises
            # ``OSError``/``ValueError`` which is caught below — the
            # hardcoded fallback then applies.
            raw = json.loads(_secure_read_text(json_path))
            # Filter out the _comment metadata key
            return {k: v for k, v in raw.items() if k != "_comment" and isinstance(v, dict)}
        except Exception as exc:
            log.warning("[SECURITY] Failed to load model_hashes.json: %s", exc)
    # Hardcoded fallback — mirrors model_hashes.json so that even if the JSON
    # file is missing or unreadable (e.g. isolated test env, broken install),
    # the pinned revisions are still enforced. SHAs fetched 2026-07-10 from
    # https://huggingface.co/api/models/<repo>/revision/main. These MUST be
    # kept in sync with model_hashes.json; the test_model_hashes_fallback_matches_json
    # regression test enforces this.
    return {
        "nvidia/parakeet-tdt-0.6b-v3": {
            "revision": "7c35754d166cca382ad1e53e68b01e7c575f3a1d",
            "files": {
                "config.json": "e747b85e1bdfd300c8b8ac63bac8dd5221f8fe9bc275b48d06c735fcd6971b6e",
                "generation_config.json": "b141de6ec6d7f982ece13f98f604e3fe1807ea9c0e839185d0ab7064604209d0",
                "model.safetensors": "3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11",
                "tokenizer.json": "bd321b096832a3f270bd3b2a88823957920f1a5c5ada71114a26ea729d0cbe91",
                "tokenizer_config.json": "0b2fe0037599ee335f0b972fa682bf0ece74e4ccfec755cb7daa3405d3d3e874",
            },
        },
        "Systran/faster-whisper-tiny.en": {
            "revision": "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
            "files": {
                "config.json": "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5",
                "model.bin": "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a",
                "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            },
        },
        "Systran/faster-whisper-small.en": {
            "revision": "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
            "files": {
                "config.json": "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae",
                "model.bin": "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a",
                "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            },
        },
        "Systran/faster-whisper-medium.en": {
            "revision": "a29b04bd15381511a9af671baec01072039215e3",
            "files": {
                "config.json": "4a1848ebabe7938d9797c15a2e8e4ce1d36e6fd4a43d096ae5955257c67c7962",
                "model.bin": "11b220779aea4c6f3ce9d2549c8a95ea869ed84066864b999531ef53e594fe5b",
                "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            },
        },
        "Systran/faster-whisper-large-v3": {
            "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
            "files": {
                "config.json": "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
                "model.bin": "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
                "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                "tokenizer.json": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
            },
        },
        # add the ``qwen`` entry to the hardcoded fallback so
        # a missing/corrupt ``model_hashes.json`` doesn't soft-pass
        # Qwen. ``revision: "local"`` + empty ``files`` triggers the
        # new hard-FAIL path in ``verify_model_integrity`` above; this
        # is intentional — operators must populate ``files`` with real
        # SHA-256 hashes before a local Qwen model can be loaded. The
        # empty dict mirrors the JSON file's ``"qwen"`` entry so the
        # ``test_model_hashes_fallback_matches_json`` regression test
        # (which enforces fallback/JSON parity) keeps passing.
        "qwen": {
            "revision": "local",
            "files": {},
        },
    }


MODEL_HASHES: dict[str, dict[str, Any]] = _load_model_hashes()


# ─── : On-disk integrity cache for SHA-256 verification ─────────────
#
# verify_model_integrity() is called UNCONDITIONALLY on every model load
# (cache hit AND miss). Pre-, this re-hashed the full multi-GB weight
# file (model.safetensors ~2.5 GB for Parakeet, model.bin ~3 GB for
# Whisper large-v3) on EVERY load — 5-10 s of pure I/O + SHA-256 CPU per
# load. The  idle-unload feature made this worse.
#
# The integrity cache is a JSON file at <config_dir>/cache/integrity_cache.json
# keyed on (repo_id, relpath, st_mtime_ns, st_size) -> sha256_hex. On a
# cache hit (mtime+size match), the cached hash is returned without
# re-reading the file.
#
# Security: the cache key includes mtime_ns + size. An attacker with
# write access to the HF cache would need to (a) modify the file, (b)
# restore the original mtime to nanosecond precision, AND (c) preserve
# the exact byte size — AND the cached hash still has to match the
# pinned manifest hash. So the cache does NOT weaken the security
# guarantee; it only skips the redundant re-hash of unchanged files.
_INTEGRITY_CACHE_VERSION = 1
_integrity_cache_lock = threading.Lock()
# Tests can override the cache path by setting this attribute.
_integrity_cache_path_override: Path | None = None


def _integrity_cache_path() -> Path:
    """Return the path to the on-disk integrity cache JSON file."""
    override = _security_pkg()._integrity_cache_path_override
    if override is not None:
        return override
    from voice_typer.server._paths import config_dir

    return config_dir() / "cache" / "integrity_cache.json"


def _load_integrity_cache() -> dict[str, Any]:
    """Load the integrity cache from disk. Returns empty cache on any error.

    uses ``_secure_read_text`` (POSIX ``O_NOFOLLOW`` / Windows
    reparse-point rejection) instead of ``Path.read_text`` so a symlink
    planted at ``<config_dir>/cache/integrity_cache.json`` cannot
    redirect the read to an arbitrary file and control the cached
    SHA-256 entries. On a symlink, ``_secure_read_text`` raises
    ``OSError`` (POSIX ``ELOOP``) / ``OSError`` (Windows reparse-point
    rejection), which is caught by the broad ``except Exception`` and
    falls through to the empty cache.
    """
    empty = {"version": _INTEGRITY_CACHE_VERSION, "repos": {}}
    try:
        path = _integrity_cache_path()
        if not path.exists():
            return empty
        raw_text = _secure_read_text(path)
        # defense-in-depth — re-tighten perms to 0o600 on every
        # successful read. Mirrors ``secure_file_io._chmod_owner_only``.
        # Best-effort; a read-only filesystem must not fail the load.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            return empty
        if raw.get("version") != _INTEGRITY_CACHE_VERSION:
            return empty
        repos = raw.get("repos", {})
        if not isinstance(repos, dict):
            return empty
        return {"version": _INTEGRITY_CACHE_VERSION, "repos": repos}
    except Exception as exc:
        log.debug("[SECURITY] integrity cache load failed (%s) — starting empty", exc)
        return empty


def _save_integrity_cache(cache: dict[str, Any]) -> None:
    """Atomically write the integrity cache to disk. Best-effort.

    delegates to ``_secure_atomic_write`` ( ``owned_fd``
    sentinel + explicit ``_chmod_owner_only`` + symlink-safe
    ``tempfile.mkstemp``) instead of a bare ``tempfile.mkstemp`` +
    ``os.fdopen`` + ``os.replace`` block. ``durability=False`` preserves
    the pre- no-fsync cache-write behaviour — the integrity cache
    is a perf optimization (skips re-hashing multi-GB model files), not
    security-critical state, so a power-loss window of a few seconds is
    acceptable (the next ``verify_model_integrity`` call re-computes
    any missing cache entry).
    """
    try:
        path = _integrity_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _security_pkg()._secure_atomic_write(path, json.dumps(cache), durability=False)
    except Exception as exc:
        log.debug("[SECURITY] integrity cache save failed (%s) — cache will not persist", exc)


def compute_file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file.

    uses mmap when possible for zero-copy hashing of large model
    files. Falls back to the 64 KB chunk loop on mmap failure (e.g.
    mmap of a 0-length file raises ValueError).
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                h.update(mm)
            finally:
                mm.close()
        return h.hexdigest()
    except (ValueError, OSError) as exc:
        log.debug(
            "[SECURITY] mmap hash failed for %s — falling back to chunk loop: %s",
            path,
            exc,
        )
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()


def verify_model_integrity(local_dir: str, repo_id: str) -> bool:
    """SEC-audit-005: Verify downloaded model files against the manifest.

    Computes SHA-256 hashes of all files in ``local_dir`` and compares
    them against the pinned hashes in ``MODEL_HASHES``.  If no hashes
    are pinned for a given file, a basic structural check is performed
    (file exists and is not empty) and the computed hash is logged at
    INFO level so it can be added to the manifest later.

    hashes are memoized in an on-disk integrity cache
    (``<config_dir>/cache/integrity_cache.json``) keyed on
    ``(repo_id, relpath, st_mtime_ns, st_size)``. On a cache hit, the
    cached hash is reused without re-reading the multi-GB weight file
    — saving 5-10 s of pure I/O+CPU on every model load. The cache is
    invalidated automatically when the file's mtime or size changes.

    Parameters
    ----------
    local_dir : str
        Path to the downloaded model directory.
    repo_id : str
        HuggingFace repository identifier (e.g. "nvidia/parakeet-tdt-0.6b-v3").

    Returns
    -------
    bool
        True if all verifications pass, False otherwise. Returns False
        on any pinned-hash mismatch (hard fail) so callers can refuse
        to load a tampered model.
    """
    model_path = Path(local_dir)
    if not model_path.exists():
        log.warning("[SECURITY] Model directory does not exist: %s", local_dir)
        return False

    manifest = _security_pkg().MODEL_HASHES.get(repo_id, {})

    # Check for at least one model file (safetensors, bin, or onnx)
    model_extensions = {".safetensors", ".bin", ".onnx", ".pt"}
    has_model_file = False
    for f in model_path.rglob("*"):
        if f.is_file() and f.suffix in model_extensions and f.stat().st_size > 0:
            has_model_file = True
            break

    if not has_model_file:
        log.warning(
            "[SECURITY] Model integrity check failed: no model files found in %s",
            local_dir,
        )
        return False

    # Check config.json exists
    config_json = model_path / "config.json"
    if not config_json.exists():
        log.warning(
            "[SECURITY] Model integrity check failed: config.json missing in %s",
            local_dir,
        )
        return False

    # hard-FAIL for local models with an empty ``files`` dict.
    # Pre-fix, ``verify_model_integrity`` soft-passed whenever the
    # manifest's ``files`` dict was empty (see the ``else`` branch
    # below). For HuggingFace repos this was acceptable because the
    # ``revision`` field is a SHA pin validated upstream by
    # ``snapshot_download``'s commit-pin; the empty-files state was a
    # "to-be-populated" placeholder. But for ``revision: "local"`` (the
    # Qwen model, loaded from a user-supplied local path), there is NO
    # upstream SHA pin — the soft-pass meant a tampered or substituted
    # Qwen model directory would load without any integrity check. The
    # fix: when ``revision == "local"`` AND ``files`` is empty, return
    # False (hard FAIL) so the caller refuses to load. Operators who
    # want to load a local Qwen model MUST populate the ``files`` dict
    # in ``model_hashes.json`` with the expected SHA-256 hashes (the
    # soft-pass branch below already logs them at INFO).
    manifest_revision = manifest.get("revision")
    pinned_files = manifest.get("files", {})
    if manifest_revision == "local" and not pinned_files:
        log.error(
            "[SECURITY] Model integrity: hard-FAIL for local model %s — "
            'model_hashes.json has "revision": "local" with empty "files". '
            "A local model has no upstream SHA pin, so the empty-files "
            "soft-pass would let a tampered directory load unchecked. "
            'Populate the "files" dict with the expected SHA-256 hashes '
            "(the INFO logs from a prior run with the correct model print "
            "them) to enable verification on the next run.",
            repo_id,
        )
        return False

    # load the integrity cache ONCE for the whole verification
    # call. Keyed on (repo_id, relpath, st_mtime_ns, st_size) -> sha256.
    # The cache lock is held only for load/save — NOT for hash
    # computation (which can take 5-10 s for a multi-GB weight file).
    with _integrity_cache_lock:
        cache = _load_integrity_cache()
    cache_dirty = False

    def _hash_with_cache(file_path: Path, relpath: str) -> str:
        """Return the SHA-256 of file_path, using the cache when possible."""
        nonlocal cache_dirty
        try:
            st = file_path.stat()
            mtime_ns = st.st_mtime_ns
            size = st.st_size
        except OSError as exc:
            log.debug(
                "[SECURITY] stat failed for %s — computing uncached hash: %s",
                file_path,
                exc,
            )
            return _security_pkg().compute_file_sha256(file_path)
        repos = cache.setdefault("repos", {})
        repo_entries = repos.setdefault(repo_id, {})
        entry = repo_entries.get(relpath)
        if (
            isinstance(entry, dict)
            and entry.get("mtime_ns") == mtime_ns
            and entry.get("size") == size
            and isinstance(entry.get("sha256"), str)
        ):
            return entry["sha256"]
        digest = _security_pkg().compute_file_sha256(file_path)
        repo_entries[relpath] = {
            "mtime_ns": mtime_ns,
            "size": size,
            "sha256": digest,
        }
        cache_dirty = True
        return digest

    # SEC-audit-005: Verify pinned file hashes if available.
    # The manifest entry for a repo can include a "files" dict mapping
    # relative file paths to expected SHA-256 hex digests. When present,
    # every pinned file MUST exist and match — a single mismatch fails
    # the integrity check (hard fail) so callers refuse to load a
    # tampered or corrupted model.
    #
    # When no files are pinned (the manifest only has "revision"), we
    # compute and log hashes for every file in the model directory at
    # INFO level. Operators can copy these logged hashes into
    # model_hashes.json to enable enforcement on the next run.
    #
    # this branch is now reachable ONLY for HuggingFace
    # repos (``revision`` is a 40-char SHA, validated upstream by
    # ``snapshot_download``). Local repos with empty files hit the
    # hard-FAIL branch above.
    if pinned_files:
        for filename, expected_hash in pinned_files.items():
            file_path = model_path / filename
            if not file_path.exists():
                log.warning(
                    "[SECURITY] Model integrity: pinned file %s missing in %s",
                    filename,
                    local_dir,
                )
                return False
            actual_hash = _hash_with_cache(file_path, filename)
            if not hmac.compare_digest(actual_hash, expected_hash):
                log.warning(
                    "[SECURITY] Model integrity: hash mismatch for %s in %s "
                    "(expected %s..., got %s...) — refusing to load tampered model",
                    filename,
                    local_dir,
                    expected_hash[:16],
                    actual_hash[:16],
                )
                return False
        log.info(
            "[SECURITY] Model integrity check passed for %s (%d pinned files verified)", repo_id, len(pinned_files)
        )
    else:
        # No pinned hashes — log computed hashes for future audit.
        # This is a soft pass; the structural checks above are the
        # hard gate that prevents loading completely wrong file types.
        # SEC-audit-005: emit a WARNING (not just INFO) so operators
        # notice that model integrity verification is effectively a
        # no-op for this repo. Pre-fix the empty-files state produced
        # zero enforcement but only an INFO log, which is invisible at
        # default log levels — operators had no way to know their
        # model_hashes.json was empty. The WARNING surfaces the issue
        # in normal logs without refusing to load (the structural
        # checks above are still enforced).
        log.warning(
            "[SECURITY] Model integrity check is a NO-OP for %s — "
            'model_hashes.json has empty "files" dict for this repo. '
            "Computed hashes are logged below; copy them into "
            'model_hashes.json under the repo\'s "files" field to '
            "enable enforcement on the next run.",
            repo_id,
        )
        for entry in model_path.rglob("*"):
            if not entry.is_file():
                continue
            try:
                rel = entry.relative_to(model_path).as_posix()
                h = _hash_with_cache(entry, rel)
                log.info("[SECURITY]   %s: sha256=%s", rel, h)
            except Exception as exc:
                log.debug("[SECURITY]   failed to hash %s: %s", entry, exc)

    # persist the cache once at the end (only if dirty).
    if cache_dirty:
        with _integrity_cache_lock:
            _save_integrity_cache(cache)

    return True


"""Shared model-integrity constants — SEC-audit-005 / CRIT-5 / SEC-2.

Single source of truth for the file-pattern allow-lists used by both
``parakeet_engine.py`` (download + verify path) and ``asr_setup.py``
(parakeet weight downloader) and ``transcription.py`` (Whisper weight
downloader).  Keeping the allow-lists in one module prevents the
copies in ``parakeet_engine.py``, ``asr_setup.py`` and
``transcription.py`` from drifting out of sync.

CRIT-5 / SEC-2 root cause: the manifest in ``model_hashes.json`` pinned
hashes for files that this allow-list omits (``.gitattributes``,
``README.md``, ``plots/asr.png``, ``.eval_results/open_asr_leaderboard.yaml``,
``parakeet-tdt-0.6b-v3.nemo``, ``processor_config.json``).
``verify_model_integrity()`` hard-fails if any pinned file is missing
from the downloaded snapshot, so every Parakeet download failed
verification — which combined with CRIT-4 (load-on-warning) meant the
supply-chain gate was effectively disabled.

IMPORTANT: these allow-lists MUST stay in sync with the ``files`` dict
in ``model_hashes.json``.  When adding a new file pattern here, also
add its SHA-256 to ``model_hashes.json``; when removing a pattern,
remove the corresponding manifest entry.  The
``test_model_hashes_have_pinned_config_json`` regression test catches
the most common drift (config.json going missing); broader drift is
caught at runtime by ``verify_model_integrity()`` returning False.

(Session 7 — Group 4): the original monolithic
``ALLOW_PATTERNS`` list included ``*.bin`` (a pickle-serialised
PyTorch state-dict) which is a remote-code-execution vector.  Parakeet
ships ``model.safetensors`` only and never needs ``*.bin``; allowing
it created an injection surface where a compromised HF repo could ship
a malicious ``pytorch_model.bin`` that the user would pull into their
local cache (and that ``verify_model_integrity`` would then have to
either pin or ignore).  The list is now split per backend:

- ``ALLOW_PATTERNS_PARAKEET`` — safetensors + config/tokenizer JSONs.
  No ``*.bin``.  Used by ``parakeet_engine.py`` and the Parakeet path
  of ``asr_setup.download_parakeet_weights``.
- ``ALLOW_PATTERNS_WHISPER`` — keeps ``*.bin`` because CTranslate2
  (used by ``faster_whisper``) consumes the ``model.bin`` format
  natively.  Whisper weights are only ever loaded via CTranslate2
  and never via ``torch.load`` (the pickle-vector path), so the
  risk is bounded.  Used by ``transcription.py::_pre_download_model``.

"""

# SEC-audit-005: Allowlist of file patterns permitted in
# HuggingFace Parakeet model downloads.  ``*.bin`` is intentionally
# OMITTED — Parakeet ships ``model.safetensors`` only, and the
# pickle-serialised ``*.bin`` format is a remote-code-execution vector
# if a compromised HF repo were to ship a malicious
# ``pytorch_model.bin``.  ``verify_model_integrity()`` hard-fails if a
# pinned file is missing, so every pattern here must also have a
# corresponding entry in the ``files`` dict of ``model_hashes.json``
# (or the structural check in ``verify_model_integrity()`` will pass
# but the pinned-files check will fail).
#
# Patterns are matched by ``fnmatch`` (HuggingFace's ``allow_patterns``
# argument uses ``fnmatch.filter``).  ``*.safetensors`` matches any
# top-level ``.safetensors`` file (e.g. ``model.safetensors`` and the
# shard files ``model-00001-of-00003.safetensors``).
ALLOW_PATTERNS_PARAKEET: list[str] = [
    "*.safetensors",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "preprocessor_config.json",
    "feature_extractor_config.json",
    "generation_config.json",
    "model.safetensors.index.json",
    "*.model",
]

# ONNX Runtime migration (PLAN_ONNX_INTEGRATION.md §3.5.4): allowlist
# for the ONNX Parakeet weights (``grikdotnet/parakeet-tdt-0.6b-fp16``
# via the ``onnx-asr`` library). The pre-migration ``ALLOW_PATTERNS_PARAKEET``
# above stays — it covers the torch/safetensors cache layout (still
# downloaded by users who haven't migrated to ONNX). This new constant
# covers the ONNX-specific files the ``onnx-asr`` library fetches:
# ``*.onnx`` for the encoder/decoder/joint ONNX graphs, plus the
# tokenizer + config JSONs required for decoding (TDT decoding needs
# the tokenizer + generation_config to map token IDs to text).
#
# Typed as ``frozenset`` (not ``list``) per §3.5.4 — the ONNX allowlist
# is consumed by the ``onnx-asr`` library's HF download path, which
# accepts any iterable of patterns; ``frozenset`` documents
# immutability + dedup intent and matches the convention used by
# ``onnx_asr.Model(...)``'s ``allow_patterns`` parameter.
#
# SECURITY: ``verify_model_integrity()`` hard-fails if a pinned file
# is missing, so every pattern here must also have a corresponding
# entry in the ``files`` dict of ``model_hashes.json`` for the
# ``grikdotnet/parakeet-tdt-0.6b-fp16`` repo (or the structural check
# passes but the pinned-files check fails).
ALLOW_PATTERNS_PARAKEET_ONNX: frozenset[str] = frozenset({
    "*.onnx",
    "config.json",
    "tokenizer.json",
    "vocab.txt",
    "special_tokens_map.json",
    "generation_config.json",
})

# SEC-audit-005: Allowlist for HuggingFace Whisper-family
# downloads (``Systran/faster-whisper-*``).  CTranslate2 loads model
# weights from ``model.bin`` — this is the native on-disk format for
# ``faster_whisper.WhisperModel`` and is NOT loaded via
# ``torch.load`` (the pickle-vector path), so the ``*.bin`` risk is
# bounded to "wrong weights → bad transcription" rather than "arbitrary
# code execution".  ``model_hashes.json`` pins the SHA-256 of every
# ``model.bin`` so a tampered file would be detected by
# ``verify_model_integrity()`` before ``WhisperModel.__init__`` is
# called.
ALLOW_PATTERNS_WHISPER: list[str] = [
    "*.safetensors",
    "*.bin",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "preprocessor_config.json",
    "feature_extractor_config.json",
    "generation_config.json",
    "model.safetensors.index.json",
    "*.model",
]
