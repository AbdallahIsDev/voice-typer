"""Model integrity verification (SEC-audit-005) + download allowlists.

NOTE: see docs/code-notes/security-config.md#model-integrity-cache
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
    """Return the security package via call-time lookup (C-ARCH-2 patch surface)."""
    import voice_typer.server.security as _security

    return _security


# SEC-audit-005: pinned HF revisions + file hashes.


def _load_model_hashes() -> dict[str, dict[str, Any]]:
    """Load MODEL_HASHES from companion JSON; fall back to the hardcoded mirror."""
    json_path = Path(__file__).resolve().parent.parent / "model_hashes.json"
    if json_path.exists():
        try:
            # SEC-audit-005: secure read rejects symlink/reparse redirect of the manifest.
            raw = json.loads(_secure_read_text(json_path))
            # Filter out the _comment metadata key
            return {k: v for k, v in raw.items() if k != "_comment" and isinstance(v, dict)}
        except Exception as exc:
            log.warning("[SECURITY] Failed to load model_hashes.json: %s", exc)
    # Fallback mirrors model_hashes.json; parity enforced by tests.
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
        # Upstream fp16 ONNX export; hashes pin real shipped bytes.
        "grikdotnet/parakeet-tdt-0.6b-fp16": {
            "revision": "dc9871ec5ad84a420940077e76e8741b3609bf8b",
            "files": {
                "config.json": "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466",
                "encoder-model.fp16.onnx": "a2bdeeb99cb7e5548818e823127b33854dd0c26f5d0c8da91effdd895ea0e717",
                "decoder_joint-model.fp16.onnx": "b33a73b7c1d71b9d5a0911f5cb478be3dcbf79f53355c531ab1cd1dcd68ad8ef",
                "nemo128.onnx": "a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f",
                "vocab.txt": "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d",
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
        # MODEL_REGISTRY "tiny" (multilingual).
        "Systran/faster-whisper-tiny": {
            "revision": "d90ca5fe260221311c53c58e660288d3deb8d356",
            "files": {
                "config.json": "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8",
                "model.bin": "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
                "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
                "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
            },
        },
        # Turbo models map to mobiuslabsgmbh, not Systran.
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo": {
            "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
            "files": {
                "config.json": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
                "model.bin": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
                "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
                "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            },
        },
        # local + empty files: hard-FAIL until operators populate hashes.
        "qwen": {
            "revision": "local",
            "files": {},
        },
    }


MODEL_HASHES: dict[str, dict[str, Any]] = _load_model_hashes()


# NOTE: see docs/code-notes/security-config.md#model-integrity-cache
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
    """Load the integrity cache from disk. Returns empty cache on any error."""
    empty = {"version": _INTEGRITY_CACHE_VERSION, "repos": {}}
    try:
        path = _integrity_cache_path()
        if not path.exists():
            return empty
        raw_text = _secure_read_text(path)
        # defense-in-depth, re-tighten perms to 0o600 on every
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
        log.debug("[SECURITY] integrity cache load failed (%s), starting empty", exc)
        return empty


def _save_integrity_cache(cache: dict[str, Any]) -> None:
    """Atomically write the integrity cache to disk. Best-effort."""
    try:
        path = _integrity_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _security_pkg()._secure_atomic_write(path, json.dumps(cache), durability=False)
    except Exception as exc:
        log.debug("[SECURITY] integrity cache save failed (%s), cache will not persist", exc)


def compute_file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
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
            "[SECURITY] mmap hash failed for %s, falling back to chunk loop: %s",
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


def _hash_one_file(cache: dict[str, Any], repo_id: str, file_path: Path, relpath: str) -> tuple[str, bool]:
    """Hash one file with the integrity cache; return ``(digest, dirty)``."""
    try:
        st = file_path.stat()
        mtime_ns = st.st_mtime_ns
        size = st.st_size
    except OSError as exc:
        log.debug(
            "[SECURITY] stat failed for %s, computing uncached hash: %s",
            file_path,
            exc,
        )
        return _security_pkg().compute_file_sha256(file_path), False
    repos = cache.setdefault("repos", {})
    repo_entries = repos.setdefault(repo_id, {})
    entry = repo_entries.get(relpath)
    if (
        isinstance(entry, dict)
        and entry.get("mtime_ns") == mtime_ns
        and entry.get("size") == size
        and isinstance(entry.get("sha256"), str)
    ):
        return entry["sha256"], False
    digest = _security_pkg().compute_file_sha256(file_path)
    repo_entries[relpath] = {
        "mtime_ns": mtime_ns,
        "size": size,
        "sha256": digest,
    }
    return digest, True


def hash_file_cached(repo_id: str, file_path: Path, relpath: str) -> str:
    """Hash one file, reusing + updating the on-disk integrity cache."""
    cache = _load_integrity_cache()
    digest, dirty = _hash_one_file(cache, repo_id, file_path, relpath)
    if dirty:
        with _integrity_cache_lock:
            _save_integrity_cache(cache)
    return digest


def verify_model_integrity(local_dir: str, repo_id: str) -> bool:
    """SEC-audit-005: Verify downloaded model files against the manifest.

    Returns
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
    manifest_revision = manifest.get("revision")
    pinned_files = manifest.get("files", {})
    if manifest_revision == "local" and not pinned_files:
        log.error(
            "[SECURITY] Model integrity: hard-FAIL for local model %s, "
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
    with _integrity_cache_lock:
        cache = _load_integrity_cache()
    cache_dirty = False

    def _hash_with_cache(file_path: Path, relpath: str) -> str:
        """Return the SHA-256 of file_path, using the cache when possible."""
        nonlocal cache_dirty
        digest, dirty = _hash_one_file(cache, repo_id, file_path, relpath)
        if dirty:
            cache_dirty = True
            with _integrity_cache_lock:
                _save_integrity_cache(cache)
        return digest

    # SEC-audit-005: verify pinned file hashes when present.
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
                    "(expected %s..., got %s...), refusing to load tampered model",
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
        # No pinned hashes, log computed hashes for future audit.
        log.warning(
            "[SECURITY] Model integrity check is a NO-OP for %s, "
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


"""Shared model-integrity constants. SEC-audit-005 / CRIT-5 / SEC-2.

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
verification: which combined with CRIT-4 (load-on-warning) meant the
supply-chain gate was effectively disabled.

IMPORTANT: these allow-lists MUST stay in sync with the ``files`` dict
in ``model_hashes.json``.  When adding a new file pattern here, also
add its SHA-256 to ``model_hashes.json``; when removing a pattern,
remove the corresponding manifest entry.  The
``test_model_hashes_have_pinned_config_json`` regression test catches
the most common drift (config.json going missing); broader drift is
caught at runtime by ``verify_model_integrity()`` returning False.

(Session 7, Group 4): the original monolithic
``ALLOW_PATTERNS`` list included ``*.bin`` (a pickle-serialised
state-dict) which is a remote-code-execution vector.  Parakeet
ships ``model.safetensors`` only and never needs ``*.bin``; allowing
it created an injection surface where a compromised HF repo could ship
a malicious ``*.bin`` weights file that the user would pull into
their local cache (and that ``verify_model_integrity`` would then have
to either pin or ignore).  The list is now split per backend:

- ``ALLOW_PATTERNS_PARAKEET``: safetensors + config/tokenizer JSONs.
  No ``*.bin``.  Used by ``parakeet_engine.py`` and the Parakeet path
  of ``asr_setup.download_parakeet_weights``.
- ``ALLOW_PATTERNS_WHISPER``: keeps ``*.bin`` because CTranslate2
  (used by ``faster_whisper``) consumes the ``model.bin`` format
  natively.  Whisper weights are only ever loaded via CTranslate2
  and never via the pickle-based loader, so the risk is bounded.
  Used by ``transcription.py::_pre_download_model``.

"""

# SEC-audit-005: Allowlist of file patterns permitted in
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
ALLOW_PATTERNS_PARAKEET_ONNX: frozenset[str] = frozenset(
    {
        "*.onnx",
        "config.json",
        "tokenizer.json",
        "vocab.txt",
        "special_tokens_map.json",
        "generation_config.json",
    }
)

# SEC-audit-005: Allowlist for HuggingFace Whisper-family
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
    # Legacy vocabulary tables shipped by faster-whisper repos
    "vocabulary.json",
    "vocabulary.txt",
]
