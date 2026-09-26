"""AB-8: On-disk integrity cache for ASR model SHA-256 verification."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


def _setup_repo(tmp_path: Path, repo_id: str = "test/ab8-repo") -> tuple[Path, str, bytes, str]:
    """Create a fake model dir with a model file + config.json."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    # Write a non-trivial model file so the "has_model_file" structural
    (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
    config_bytes = b'{"model_type": "ab8-test"}'
    (model_dir / "config.json").write_bytes(config_bytes)
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    return model_dir, repo_id, config_bytes, config_sha256


def _patch_manifest(repo_id: str, config_sha256: str):
    """pins ``config.json`` to ``config_sha256``."""
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
    """Force the integrity cache to live inside the test's tmp_path."""
    from voice_typer.server import security

    cache_path = tmp_path / "cache" / "integrity_cache.json"
    monkeypatch.setattr(security, "_integrity_cache_path_override", cache_path)
    yield
    monkeypatch.setattr(security, "_integrity_cache_path_override", None)


def test_integrity_verdict_rehashes_on_second_load(tmp_path):
    """The load verdict always reads bytes; unchanged mtime+size MUST NOT skip the hash."""
    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)

    with _patch_manifest(repo_id, config_sha256):
        # First call, computes & stores the hash.
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

            # Second call, metadata unchanged, verdict still re-hashes.
            call_count = 0
            result2 = security.verify_model_integrity(str(model_dir), repo_id)
            assert result2 is True
            assert call_count == 1, (
                "The verdict path MUST re-hash file bytes on every call "
                f"(got {call_count} calls). A metadata-only cache hit lets a "
                "user-privileged writer substitute bytes while preserving "
                "mtime+size and pass verification without reading the file."
            )


def test_integrity_verdict_ignores_forged_cache_entry(tmp_path):
    """A forged cache entry (matching mtime/size, pinned sha) MUST NOT pass tampered bytes."""
    import os

    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)
    config_path = model_dir / "config.json"

    with _patch_manifest(repo_id, config_sha256):
        assert security.verify_model_integrity(str(model_dir), repo_id) is True

        st = config_path.stat()
        orig_mtime_ns = st.st_mtime_ns

        tampered = b'{"model_type": "ab8-tesu"}'
        assert len(tampered) == st.st_size
        config_path.write_bytes(tampered)
        os.utime(config_path, ns=(orig_mtime_ns, orig_mtime_ns))

        forged_cache = {
            "version": 1,
            "repos": {
                repo_id: {
                    "config.json": {
                        "mtime_ns": orig_mtime_ns,
                        "size": st.st_size,
                        "sha256": config_sha256,
                    }
                }
            },
        }
        cache_path = security._integrity_cache_path()
        cache_path.write_text(json.dumps(forged_cache))

        call_count = 0
        original_compute = security.compute_file_sha256

        def _counting_compute(path: Path) -> str:
            nonlocal call_count
            call_count += 1
            return original_compute(path)

        with patch.object(security, "compute_file_sha256", side_effect=_counting_compute):
            result = security.verify_model_integrity(str(model_dir), repo_id)
            assert result is False, (
                "The verdict MUST read file bytes and fail on tampered "
                "content even when a forged cache entry matches mtime+size "
                "and carries the pinned sha."
            )
            assert call_count == 1, "The verdict MUST hash bytes, not trust the cache entry."


def test_integrity_cache_persists_across_module_reloads(tmp_path):
    """AB-8: the cache is written to disk so it survives across"""
    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)

    with _patch_manifest(repo_id, config_sha256):
        # First call, populates the cache.
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
        assert "mtime_ns" in entry
        assert "size" in entry
        assert entry["size"] == len(b'{"model_type": "ab8-test"}')


def test_integrity_cache_invalidated_when_mtime_changes(tmp_path):
    """AB-8: when the file's mtime changes (but size stays the same),"""
    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)
    config_path = model_dir / "config.json"

    with _patch_manifest(repo_id, config_sha256):
        # First call, populates the cache.
        assert security.verify_model_integrity(str(model_dir), repo_id) is True

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
                "AB-8: cache MUST miss when mtime changes, compute_file_sha256 "
                f"should be called once (got {call_count}). A stale cache hit "
                "would let a tampered file load without re-hashing."
            )


def test_integrity_cache_invalidated_when_size_changes(tmp_path):
    """cache MUST miss and the hash MUST be re-computed."""
    from voice_typer.server import security

    model_dir, repo_id, _, _ = _setup_repo(tmp_path)
    config_path = model_dir / "config.json"

    # First call with the ORIGINAL manifest (pins the original hash).
    original_bytes = b'{"model_type": "ab8-test"}'
    original_sha = hashlib.sha256(original_bytes).hexdigest()

    with _patch_manifest(repo_id, original_sha):
        assert security.verify_model_integrity(str(model_dir), repo_id) is True

        # AND different hash). Update the manifest to pin the NEW hash
        tampered_bytes = b'{"model_type": "ab8-test-tampered-longer"}'
        tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
        config_path.write_bytes(tampered_bytes)
        # Don't touch mtime, force the size-difference path.

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
                "AB-8: cache MUST miss when file size changes, "
                f"compute_file_sha256 should be called once (got {call_count})."
            )


def test_integrity_cache_stale_entry_does_not_cause_false_pass(tmp_path):
    """A stale cache entry never decides the verdict; fresh bytes are compared to the manifest."""
    from voice_typer.server import security

    model_dir, repo_id, _, _ = _setup_repo(tmp_path)
    config_path = model_dir / "config.json"

    # Use the correct hash for the actual file content.
    actual_bytes = config_path.read_bytes()
    actual_sha = hashlib.sha256(actual_bytes).hexdigest()

    st = config_path.stat()
    stale_cache = {
        "version": 1,
        "repos": {
            repo_id: {
                "config.json": {
                    "mtime_ns": st.st_mtime_ns,
                    "size": st.st_size,
                    "sha256": "0" * 64,  # WRONG hash, does not match the file
                }
            }
        },
    }
    cache_path = security._integrity_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(stale_cache))

    # Pin the manifest with the ACTUAL (correct) hash. The verdict reads
    # bytes, so a correct file passes despite the stale entry, and the
    # entry is refreshed to the real digest.
    with _patch_manifest(repo_id, actual_sha):
        result = security.verify_model_integrity(str(model_dir), repo_id)
        assert result is True, (
            "The verdict compares fresh file bytes against the pinned "
            "manifest hash; a stale cache entry must neither pass a "
            "tampered file nor fail a correct one."
        )
        refreshed = json.loads(cache_path.read_text(encoding="utf-8"))
        assert refreshed["repos"][repo_id]["config.json"]["sha256"] == actual_sha


def test_compute_file_sha256_uses_mmap_for_non_empty_file(tmp_path):
    """AB-8: ``compute_file_sha256`` should use mmap for non-empty"""
    from voice_typer.server import security

    # Use a file larger than 64 KB to ensure the chunk loop would
    content = b"AB-8 mmap hash test payload" * 10000  # ~270 KB
    path = tmp_path / "weights.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    actual = security.compute_file_sha256(path)
    assert actual == expected


def test_compute_file_sha256_handles_empty_file(tmp_path):
    """AB-8: mmap of a 0-length file raises ValueError. ``compute_file_sha256``"""
    from voice_typer.server import security

    path = tmp_path / "empty.bin"
    path.write_bytes(b"")

    expected = hashlib.sha256(b"").hexdigest()
    actual = security.compute_file_sha256(path)
    assert actual == expected, (
        "AB-8: empty file must hash to sha256(b''), mmap raises "
        "ValueError on 0-length files and the chunk-loop fallback "
        "must produce the correct hash."
    )


def test_compute_file_sha256_matches_chunk_loop(tmp_path):
    """AB-8: the mmap-based hash MUST equal the chunk-loop-based hash"""
    from voice_typer.server import security

    content = b"\x01\x02\x03\x04" * 50000  # 200 KB, crosses 64 KB chunk boundary
    path = tmp_path / "crosses_chunk.bin"
    path.write_bytes(content)

    # Replicate the pre- chunk-loop hash for comparison.
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


def test_integrity_cache_path_in_config_dir(tmp_path, monkeypatch):
    """AB-8: the integrity cache MUST live in the user's config dir"""
    from voice_typer.server import security

    monkeypatch.setattr(security, "_integrity_cache_path_override", None)
    monkeypatch.setattr(
        "voice_typer.server._paths.config_dir",
        lambda: tmp_path,
    )
    path = security._integrity_cache_path()
    assert path == tmp_path / "cache" / "integrity_cache.json"
    assert path.parent == tmp_path / "cache"


def test_integrity_cache_atomic_write_no_partial_file_on_disk(tmp_path):
    """AB-8: ``_save_integrity_cache`` uses ``tempfile.mkstemp`` +"""
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
    assert not tmp_files, f"AB-8: atomic write must not leave .tmp files behind, found: {tmp_files}"


def test_integrity_cache_save_failure_does_not_raise(tmp_path, monkeypatch):
    """AB-8: ``_save_integrity_cache`` is best-effort, a save failure"""
    from voice_typer.server import security

    # Point the cache at a path whose parent can't be created.
    monkeypatch.setattr(security, "_integrity_cache_path_override", Path("/dev/null/cache/integrity_cache.json"))
    # Should not raise.
    security._save_integrity_cache({"version": 1, "repos": {}})


def test_integrity_cache_corrupt_json_is_replaced(tmp_path):
    """AB-8: if the on-disk cache file is corrupt (invalid JSON),"""
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="FR-28 POSIX O_NOFOLLOW test, Windows uses reparse-point rejection",
)
def test_load_integrity_cache_rejects_symlink(tmp_path):
    """FR-28: a symlink planted at ``integrity_cache.json`` MUST NOT be"""
    from voice_typer.server import security

    cache_path = security._integrity_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    malicious_cache = {
        "version": 1,
        "repos": {
            "evil/repo": {
                "config.json": {
                    "mtime_ns": 1,
                    "size": 1,
                    "sha256": "a" * 64,
                }
            }
        },
    }

    attacker_file = tmp_path / "attacker_cache.json"
    attacker_file.write_text(json.dumps(malicious_cache), encoding="utf-8")
    try:
        os.symlink(attacker_file, cache_path)
    except OSError:
        pytest.skip("Cannot create symlinks on this system")

    # Sanity check: the symlink really does point at the attacker file.
    assert cache_path.is_symlink(), "test setup: cache_path must be a symlink"

    cache = security._load_integrity_cache()
    assert cache == {"version": 1, "repos": {}}, (
        "FR-28: _load_integrity_cache must reject a symlink at the cache "
        "path and return the empty cache. Got the attacker-controlled "
        f"payload instead: {cache}. This means Path.read_text (symlink-"
        "following) is being used instead of _secure_read_text (O_NOFOLLOW)."
    )


def test_load_integrity_cache_normal_file_still_works(tmp_path):
    """FR-28 regression: the switch to ``_secure_read_text`` must NOT"""
    from voice_typer.server import security

    cache_path = security._integrity_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": 1,
        "repos": {
            "test/repo": {
                "config.json": {
                    "mtime_ns": 999,
                    "size": 42,
                    "sha256": "b" * 64,
                }
            }
        },
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    # Ensure perms are loose enough that the chmod-to-0o600 path is
    os.chmod(cache_path, 0o644)

    cache = security._load_integrity_cache()
    assert cache == payload, (
        "FR-28: a regular (non-symlink) cache file must still load "
        f"correctly after the switch to _secure_read_text. Got: {cache}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="FR-28 POSIX permission check, Windows ignores POSIX permission bits",
)
def test_load_integrity_cache_tightens_perms_to_0o600_posix(tmp_path):
    """cache file to ``0o600`` after a successful read."""
    from voice_typer.server import security

    cache_path = security._integrity_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": 1,
        "repos": {
            "test/repo": {
                "config.json": {
                    "mtime_ns": 999,
                    "size": 42,
                    "sha256": "b" * 64,
                }
            }
        },
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    # Ensure perms are loose enough that the chmod-to-0o600 path is
    os.chmod(cache_path, 0o644)

    security._load_integrity_cache()

    mode = cache_path.stat().st_mode & 0o777
    assert mode == 0o600, (
        "FR-28: _load_integrity_cache should chmod the cache file to "
        f"0o600 after a successful read (defense-in-depth). Got 0o{mode:o}."
    )


# _save_integrity_cache uses _secure_atomic_write ─────────────


def test_save_integrity_cache_uses_secure_atomic_write(tmp_path, monkeypatch):
    """``_secure_atomic_write`` (which provides the FR-50 ``owned_fd``"""
    from voice_typer.server import security

    calls: list[tuple] = []

    real_secure_atomic_write = security._secure_atomic_write

    def _spy(path, content, *, durability=True):
        calls.append((str(path), content, durability))
        # Delegate to the real implementation so the file is actually
        return real_secure_atomic_write(path, content, durability=durability)

    monkeypatch.setattr(security, "_secure_atomic_write", _spy)

    cache = {"version": 1, "repos": {}}
    security._save_integrity_cache(cache)

    assert len(calls) == 1, (
        f"FR-30: _save_integrity_cache must call _secure_atomic_write exactly once. Got {len(calls)} calls."
    )
    path_arg, content_arg, durability_arg = calls[0]
    assert durability_arg is False, (
        "FR-30: _save_integrity_cache must pass durability=False to "
        "_secure_atomic_write to preserve the no-fsync cache-write "
        f"behaviour (cache is a perf optimization, not security-critical "
        f"state). Got durability={durability_arg}."
    )
    # The content must be the JSON-serialized cache.
    assert json.loads(content_arg) == cache, (
        "FR-30: _save_integrity_cache must pass json.dumps(cache) as the "
        f"content to _secure_atomic_write. Got: {content_arg!r}"
    )
    # The path must be the integrity cache path.
    assert path_arg == str(security._integrity_cache_path()), (
        f"FR-30: _save_integrity_cache must pass the integrity cache path. Got: {path_arg}"
    )


def test_failed_verify_persists_computed_hashes(tmp_path):
    """A verify that FAILS must still persist the digests it computed."""
    from voice_typer.server import security

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)
    tampered = b'{"model_type": "tampered"}'
    (model_dir / "config.json").write_bytes(tampered)
    with _patch_manifest(repo_id, config_sha256):
        assert security.verify_model_integrity(str(model_dir), repo_id) is False

    cache_path = tmp_path / "cache" / "integrity_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = cache["repos"][repo_id]["config.json"]
    assert entry["sha256"] == hashlib.sha256(tampered).hexdigest(), (
        "failed verify must persist the computed digest for the next pass to hit instead of re-hashing."
    )


def test_asr_setup_failure_details_hit_cache_not_rehash(tmp_path):
    """The verdict re-hashes bytes; the failure-details pass then hits the cache it refreshed."""
    from voice_typer.server import security
    from voice_typer.server.asr_setup import _verify_model_integrity

    model_dir, repo_id, _, config_sha256 = _setup_repo(tmp_path)
    (model_dir / "config.json").write_bytes(b'{"model_type": "tampered"}')
    with _patch_manifest(repo_id, config_sha256):
        ok, details = _verify_model_integrity(repo_id, str(model_dir))
        assert ok is False
        assert details["failed_file"] == "config.json"

        # Second details pass over the SAME bytes: the verdict re-hashes
        # once, the details path adds zero further compute calls.
        calls: list = []
        original_compute = security.compute_file_sha256

        def _counting_compute(path: Path) -> str:
            calls.append(path)
            return original_compute(path)

        with patch.object(security, "compute_file_sha256", side_effect=_counting_compute):
            ok2, details2 = _verify_model_integrity(repo_id, str(model_dir))
        assert ok2 is False
        assert details2["failed_file"] == "config.json"
        assert len(calls) == 1, (
            f"failure-details path re-hashed {max(len(calls) - 1, 0)} file(s) instead of "
            "hitting the integrity cache refreshed by the verdict."
        )
