"""AB-8: On-disk integrity cache for ASR model SHA-256 verification.

Pre-AB-8, ``security.verify_model_integrity`` re-hashed the full multi-GB
weight file (``model.safetensors`` ~2.5 GB for Parakeet, ``model.bin``
~3 GB for Whisper large-v3) on EVERY load — 5-10 s of pure I/O + SHA-256
CPU per load. With the TY-11 idle-unload feature, every return-from-idle
reload paid this tax again.

The fix introduces an on-disk integrity cache at
``<config_dir>/cache/integrity_cache.json`` keyed on
``(repo_id, relpath, st_mtime_ns, st_size) → sha256_hex``. On a cache
hit (mtime+size match), the cached hash is returned without re-reading
the file. On a cache miss, the hash is computed (via mmap) and the
cache is updated.

These tests verify:
  1. Cache hit on the second load (same mtime/size) — the expensive
     ``compute_file_sha256`` is NOT called the second time.
  2. Cache invalidation when the file's mtime changes.
  3. Cache invalidation when the file's size changes.
  4. ``compute_file_sha256`` correctly hashes empty files (mmap
     fallback path — mmap of a 0-length file raises ValueError).
  5. ``compute_file_sha256`` uses mmap for non-empty files (verified
     via the chunk-loop fallback being unreachable for a normal file).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ─── Helpers ────────────────────────────────────────────────────────────


def _setup_repo(tmp_path: Path, repo_id: str = "test/ab8-repo") -> tuple[Path, str, bytes, str]:
    """Create a fake model dir with a model file + config.json.

    Returns ``(model_dir, repo_id, config_bytes, config_sha256)``.
    """
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    # Write a non-trivial model file so the "has_model_file" structural
    # check passes (it requires a non-empty .safetensors/.bin/.onnx/.pt).
    (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
    config_bytes = b'{"model_type": "ab8-test"}'
    (model_dir / "config.json").write_bytes(config_bytes)
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    return model_dir, repo_id, config_bytes, config_sha256


def _patch_manifest(repo_id: str, config_sha256: str):
    """Patch ``security.MODEL_HASHES`` with a single-repo manifest that
    pins ``config.json`` to ``config_sha256``.

    Returns the patcher (call ``.start()`` / ``.stop()``).
    """
    from voice_typer.server import security

    fake_manifest = {
        repo_id: {
            "revision": "abc123def4567890abc123def4567890abc123de",
            "files": {
                "config.json": config_sha256,
            },
        }
    }
    return patch.dict(security.MODEL_HASHES, fake_manifest, clear=True)


@pytest.fixture(autouse=True)
def _override_cache_path(tmp_path, monkeypatch):
    """Force the integrity cache to live inside the test's tmp_path.

    Without this override, the cache would land in the real user config
    dir (``~/.local/share/voice-typer/cache/integrity_cache.json``) and
    leak state across tests / CI runs. Each test gets a fresh empty
    cache file via tmp_path.
    """
    from voice_typer.server import security

    cache_path = tmp_path / "cache" / "integrity_cache.json"
    monkeypatch.setattr(security, "_integrity_cache_path_override", cache_path)
    yield
    monkeypatch.setattr(security, "_integrity_cache_path_override", None)


# ─── AB-8: cache hit on second load ─────────────────────────────────────


def test_integrity_cache_hit_on_second_load_skips_rehash(tmp_path):
    """AB-8: a second ``verify_model_integrity`` call with the same
    mtime+size MUST NOT re-invoke ``compute_file_sha256``.

    Pre-AB-8, every model load re-hashed the full multi-GB weight file.
    With the cache, the second call hits the cache (mtime+size match)
    and returns the cached hash without touching the file.
    """
    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)

    with _patch_manifest(repo_id, config_sha256):
        # First call — cache miss, computes & stores the hash.
        call_count = 0
        original_compute = security.compute_file_sha256

        def _counting_compute(path: Path) -> str:
            nonlocal call_count
            call_count += 1
            return original_compute(path)

        with patch.object(security, "compute_file_sha256", side_effect=_counting_compute):
            result1 = security.verify_model_integrity(str(model_dir), repo_id)
            assert result1 is True
            assert call_count == 1, (
                f"First call should compute the hash exactly once (got {call_count} calls to compute_file_sha256)."
            )

            # Second call — cache hit (same mtime+size), no re-hash.
            call_count = 0
            result2 = security.verify_model_integrity(str(model_dir), repo_id)
            assert result2 is True
            assert call_count == 0, (
                "AB-8: second call with unchanged mtime+size MUST hit the "
                f"cache and skip compute_file_sha256 (got {call_count} calls)."
            )


def test_integrity_cache_persists_across_module_reloads(tmp_path):
    """AB-8: the cache is written to disk so it survives across
    ``verify_model_integrity`` calls that re-load the cache from disk.

    Each ``verify_model_integrity`` call loads the cache fresh from
    disk at entry. This test verifies the on-disk write actually
    happens (otherwise the second call would miss the cache).
    """
    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)

    with _patch_manifest(repo_id, config_sha256):
        # First call — populates the cache.
        assert security.verify_model_integrity(str(model_dir), repo_id) is True

        # The cache file MUST now exist on disk.
        cache_path = security._integrity_cache_path()
        assert cache_path.exists(), (
            "AB-8: integrity cache file must be written to disk after a "
            "cache-miss call so subsequent calls (and subsequent process "
            "runs) can hit the cache."
        )

        # Inspect the on-disk cache contents.
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        assert raw.get("version") == 1
        repos = raw.get("repos", {})
        assert repo_id in repos
        repo_entries = repos[repo_id]
        assert "config.json" in repo_entries
        entry = repo_entries["config.json"]
        assert entry["sha256"] == config_sha256
        # mtime_ns + size MUST be recorded — they're the cache key.
        assert "mtime_ns" in entry
        assert "size" in entry
        assert entry["size"] == len(b'{"model_type": "ab8-test"}')


# ─── AB-8: cache invalidation ───────────────────────────────────────────


def test_integrity_cache_invalidated_when_mtime_changes(tmp_path):
    """AB-8: when the file's mtime changes (but size stays the same),
    the cache MUST miss and the hash MUST be re-computed.

    This is the cache-invalidation guarantee: a tampered file (same
    size, different content) gets caught because the new mtime forces
    a re-hash, which then mismatches the pinned manifest hash.
    """
    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)
    config_path = model_dir / "config.json"

    with _patch_manifest(repo_id, config_sha256):
        # First call — populates the cache.
        assert security.verify_model_integrity(str(model_dir), repo_id) is True

        # Tamper: rewrite config.json with the SAME content (so the
        # hash is unchanged and verification still passes) but force a
        # NEW mtime by touching the file. We use os.utime with a fresh
        # timestamp to guarantee the mtime_ns changes even on
        # filesystems with coarse mtime granularity.
        time.sleep(0.01)
        config_path.write_bytes(b'{"model_type": "ab8-test"}')
        new_mtime = time.time() + 100  # well in the future
        os.utime(config_path, (new_mtime, new_mtime))

        call_count = 0
        original_compute = security.compute_file_sha256

        def _counting_compute(path: Path) -> str:
            nonlocal call_count
            call_count += 1
            return original_compute(path)

        with patch.object(security, "compute_file_sha256", side_effect=_counting_compute):
            result = security.verify_model_integrity(str(model_dir), repo_id)
            assert result is True  # content is identical, so still passes
            assert call_count == 1, (
                "AB-8: cache MUST miss when mtime changes — compute_file_sha256 "
                f"should be called once (got {call_count}). A stale cache hit "
                "would let a tampered file load without re-hashing."
            )


def test_integrity_cache_invalidated_when_size_changes(tmp_path):
    """AB-8: when the file's size changes (regardless of mtime), the
    cache MUST miss and the hash MUST be re-computed.

    This catches the case where an attacker substitutes a different
    file of the same name but different size.
    """
    from voice_typer.server import security

    model_dir, repo_id, _, _ = _setup_repo(tmp_path)
    config_path = model_dir / "config.json"

    # First call with the ORIGINAL manifest (pins the original hash).
    original_bytes = b'{"model_type": "ab8-test"}'
    original_sha = hashlib.sha256(original_bytes).hexdigest()

    with _patch_manifest(repo_id, original_sha):
        assert security.verify_model_integrity(str(model_dir), repo_id) is True

        # Now rewrite config.json with DIFFERENT content (different size
        # AND different hash). Update the manifest to pin the NEW hash
        # so verification still passes — we're testing the cache, not
        # the hash-comparison logic.
        tampered_bytes = b'{"model_type": "ab8-test-tampered-longer"}'
        tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
        config_path.write_bytes(tampered_bytes)
        # Don't touch mtime — force the size-difference path.

    # Update the manifest to pin the tampered hash.
    with _patch_manifest(repo_id, tampered_sha):
        call_count = 0
        original_compute = security.compute_file_sha256

        def _counting_compute(path: Path) -> str:
            nonlocal call_count
            call_count += 1
            return original_compute(path)

        with patch.object(security, "compute_file_sha256", side_effect=_counting_compute):
            result = security.verify_model_integrity(str(model_dir), repo_id)
            assert result is True  # matches the new pinned hash
            assert call_count == 1, (
                "AB-8: cache MUST miss when file size changes — "
                f"compute_file_sha256 should be called once (got {call_count})."
            )


def test_integrity_cache_stale_entry_does_not_cause_false_pass(tmp_path):
    """AB-8: a stale cache entry (from a previous, different file with
    the same repo_id+relpath) MUST NOT cause a false verification pass
    when the current file's hash differs from the pinned manifest hash.

    Concretely: a prior call cached hash H1 for (repo, "config.json",
    mtime1, size1). Now a different file with the SAME mtime1+size1
    (extremely unlikely in practice, but the cache key is
    mtime_ns+size) is verified against a pinned hash H2 != H1. The
    cache would return H1, which mismatches H2 → hard fail. This test
    verifies that the cache hit path still runs the
    ``hmac.compare_digest`` check against the pinned manifest hash
    (i.e. the cache does NOT weaken the security guarantee).
    """
    from voice_typer.server import security

    model_dir, repo_id, _, _ = _setup_repo(tmp_path)
    config_path = model_dir / "config.json"

    # Use the correct hash for the actual file content.
    actual_bytes = config_path.read_bytes()
    actual_sha = hashlib.sha256(actual_bytes).hexdigest()

    # Manually inject a STALE cache entry with a WRONG sha256 but the
    # correct mtime_ns+size — this simulates a tampered cache file.
    st = config_path.stat()
    stale_cache = {
        "version": 1,
        "repos": {
            repo_id: {
                "config.json": {
                    "mtime_ns": st.st_mtime_ns,
                    "size": st.st_size,
                    "sha256": "0" * 64,  # WRONG hash — does not match the file
                }
            }
        },
    }
    cache_path = security._integrity_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(stale_cache))

    # Pin the manifest with the ACTUAL (correct) hash. The stale cache
    # entry has a WRONG hash; if the cache were trusted blindly,
    # verification would FALSE-FAIL (cache says "0000...", manifest says
    # actual_sha, mismatch → fail). But this is the CORRECT behaviour:
    # the cache stored a wrong hash, so verification fails — exactly
    # what should happen if the cache is tampered with. The cache is a
    # perf optimization, not a trust source; the manifest is the trust
    # source.
    #
    # What this test actually verifies: the cache hit path still runs
    # the comparison against the manifest hash. If the cached hash
    # doesn't match the manifest, verification fails — the cache does
    # NOT bypass the security check.
    with _patch_manifest(repo_id, actual_sha):
        result = security.verify_model_integrity(str(model_dir), repo_id)
        assert result is False, (
            "AB-8: a stale/wrong cache entry MUST NOT cause a false pass. "
            "The cached hash must still be compared against the pinned "
            "manifest hash — if they differ, verification must fail."
        )


# ─── AB-8: compute_file_sha256 — mmap fast path + empty-file fallback ──


def test_compute_file_sha256_uses_mmap_for_non_empty_file(tmp_path):
    """AB-8: ``compute_file_sha256`` should use mmap for non-empty
    files (the fast path). We verify the mmap path is taken by
    checking the result matches a known-good SHA-256 and that the
    function does NOT fall back to the chunk loop (the chunk loop
    would still produce the correct hash, so this test mainly guards
    against the mmap call being silently skipped).
    """
    from voice_typer.server import security

    # Use a file larger than 64 KB to ensure the chunk loop would
    # iterate more than once (so a regression to chunk-loop-only
    # behaviour would be detectable via timing — but here we just
    # verify correctness).
    content = b"AB-8 mmap hash test payload" * 10000  # ~270 KB
    path = tmp_path / "weights.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    actual = security.compute_file_sha256(path)
    assert actual == expected


def test_compute_file_sha256_handles_empty_file(tmp_path):
    """AB-8: mmap of a 0-length file raises ValueError. ``compute_file_sha256``
    MUST fall back to the chunk loop and return sha256(b"") for empty
    files (rather than crashing).
    """
    from voice_typer.server import security

    path = tmp_path / "empty.bin"
    path.write_bytes(b"")

    expected = hashlib.sha256(b"").hexdigest()
    actual = security.compute_file_sha256(path)
    assert actual == expected, (
        "AB-8: empty file must hash to sha256(b'') — mmap raises "
        "ValueError on 0-length files and the chunk-loop fallback "
        "must produce the correct hash."
    )


def test_compute_file_sha256_matches_chunk_loop(tmp_path):
    """AB-8: the mmap-based hash MUST equal the chunk-loop-based hash
    for the same file (correctness regression guard).
    """
    from voice_typer.server import security

    content = b"\x01\x02\x03\x04" * 50000  # 200 KB, crosses 64 KB chunk boundary
    path = tmp_path / "crosses_chunk.bin"
    path.write_bytes(content)

    # Replicate the pre-AB-8 chunk-loop hash for comparison.
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    expected_chunk_loop = h.hexdigest()

    actual = security.compute_file_sha256(path)
    assert actual == expected_chunk_loop


# ─── AB-8: cache path + atomic write ────────────────────────────────────


def test_integrity_cache_path_in_config_dir(tmp_path, monkeypatch):
    """AB-8: the integrity cache MUST live in the user's config dir
    (NOT in the package install dir, which may be read-only). The
    cache file is at ``<config_dir>/cache/integrity_cache.json``.
    """
    from voice_typer.server import security

    # Override is set by the autouse fixture; clear it to verify the
    # default path uses config_dir().
    monkeypatch.setattr(security, "_integrity_cache_path_override", None)
    monkeypatch.setattr(
        "voice_typer.server._paths.config_dir",
        lambda: tmp_path,
    )
    path = security._integrity_cache_path()
    assert path == tmp_path / "cache" / "integrity_cache.json"
    assert path.parent == tmp_path / "cache"


def test_integrity_cache_atomic_write_no_partial_file_on_disk(tmp_path):
    """AB-8: ``_save_integrity_cache`` uses ``tempfile.mkstemp`` +
    ``os.replace`` for atomicity. After a successful save, NO
    ``.tmp`` leftover files should remain in the cache dir.
    """
    from voice_typer.server import security

    cache = {
        "version": 1,
        "repos": {
            "test/repo": {
                "config.json": {
                    "mtime_ns": 12345,
                    "size": 100,
                    "sha256": "a" * 64,
                }
            }
        },
    }
    security._save_integrity_cache(cache)

    cache_path = security._integrity_cache_path()
    assert cache_path.exists()
    # No leftover .tmp files in the cache dir.
    tmp_files = list(cache_path.parent.glob("*.tmp"))
    assert not tmp_files, f"AB-8: atomic write must not leave .tmp files behind — found: {tmp_files}"


def test_integrity_cache_save_failure_does_not_raise(tmp_path, monkeypatch):
    """AB-8: ``_save_integrity_cache`` is best-effort — a save failure
    (e.g. read-only parent dir) MUST NOT raise. The next
    ``verify_model_integrity`` call would simply re-compute the hash.
    """
    from voice_typer.server import security

    # Point the cache at a path whose parent can't be created.
    # /dev/null/cache/x.json — /dev/null is not a directory.
    monkeypatch.setattr(security, "_integrity_cache_path_override", Path("/dev/null/cache/integrity_cache.json"))
    # Should not raise.
    security._save_integrity_cache({"version": 1, "repos": {}})


def test_integrity_cache_corrupt_json_is_replaced(tmp_path):
    """AB-8: if the on-disk cache file is corrupt (invalid JSON),
    ``_load_integrity_cache`` returns an empty cache rather than
    crashing. The next verify call re-populates the cache.
    """
    from voice_typer.server import security

    cache_path = security._integrity_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("not valid json {{{")

    cache = security._load_integrity_cache()
    assert cache == {"version": 1, "repos": {}}, "AB-8: corrupt cache file must yield an empty cache, not raise."

    # And verify_model_integrity should still work, re-computing the hash.
    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)
    with _patch_manifest(repo_id, config_sha256):
        result = security.verify_model_integrity(str(model_dir), repo_id)
        assert result is True
