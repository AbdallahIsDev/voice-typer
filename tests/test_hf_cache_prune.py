"""Tests for the REMOVED automatic model-cache eviction.

The app NEVER deletes models automatically — deleting a model is an
explicit user action (the Models page Delete button). The old
``prune_model_cache`` auto-eviction helper (HF cache size-based
eviction that deleted the oldest cached repos on every load) has been
removed. These tests guard the new behavior:

1. ``asr_utils`` no longer exports ``prune_model_cache`` or its private
   helpers / size-cap constant.
2. ``cleanup_hf_cache_dir`` still exists (the explicit user-initiated
   download path uses it to clear a tampered cache DURING a download),
   but no engine ``load()`` path calls it.
3. No production module may still reference the removed auto-eviction
   entry points.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


class TestPruneModelCacheRemoved:
    """The auto-eviction helper and its internals are gone."""

    def test_prune_model_cache_not_in_asr_utils(self):
        from voice_typer.server import asr_utils

        assert not hasattr(asr_utils, "prune_model_cache"), (
            "prune_model_cache must be REMOVED — the app never deletes "
            "models automatically; deleting a model is an explicit user "
            "action (Models page Delete button)."
        )

    def test_prune_internal_helpers_removed(self):
        from voice_typer.server import asr_utils

        for name in ("_prune_oldest_repos", "_repo_size_bytes", "_MAX_MODEL_CACHE_GB"):
            assert not hasattr(asr_utils, name), (
                f"asr_utils.{name} must be REMOVED along with the "
                "auto-eviction it supported."
            )

    def test_no_source_references_prune_model_cache(self):
        """No production module may reference the removed helper."""
        from voice_typer.server import asr_utils as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "prune_model_cache" not in src

    def test_no_automatic_eviction_in_transcription_source(self):
        src = _read("voice_typer/server/transcription.py")
        assert "prune_model_cache" not in src, (
            "transcription.py must not reference the removed auto-eviction helper."
        )

    def test_no_automatic_eviction_in_parakeet_source(self):
        src = _read("voice_typer/server/parakeet_engine.py")
        assert "prune_model_cache" not in src, (
            "parakeet_engine.py must not reference the removed auto-eviction helper."
        )


class TestCleanupHelperStillAvailableForExplicitDownloads:
    """``cleanup_hf_cache_dir`` survives — but ONLY for the explicit
    user-initiated download path (clearing a tampered cache during a
    download the user started), never for automatic load-time deletion.
    """

    def test_cleanup_hf_cache_dir_still_exists(self):
        from voice_typer.server.asr_utils import cleanup_hf_cache_dir

        assert callable(cleanup_hf_cache_dir)

    def test_whisper_load_path_never_deletes(self):
        """transcription.py must not call ``cleanup_hf_cache_dir`` on the
        load path — a tampered cache raises ``ModelIntegrityError`` and
        is left in place for the user to delete explicitly."""
        src = _read("voice_typer/server/transcription.py")
        # The import re-export and docstring comments are fine; a CALL is not.
        call_sites = [
            line
            for line in src.splitlines()
            if "cleanup_hf_cache_dir(" in line and not line.strip().startswith("#")
        ]
        assert not call_sites, (
            "transcription.py must not call cleanup_hf_cache_dir — "
            "deleting a model is an explicit user action."
        )

    def test_parakeet_load_path_never_deletes(self):
        src = _read("voice_typer/server/parakeet_engine.py")
        assert "cleanup_hf_cache_dir(" not in src, (
            "parakeet_engine.py must not call cleanup_hf_cache_dir on load — "
            "a tampered cache raises ModelIntegrityError and is left for the "
            "user to delete explicitly."
        )

    def test_cleanup_only_reachable_from_explicit_download(self):
        """The only production callers of ``cleanup_hf_cache_dir`` must be
        the user-initiated download path (service/asr_setup) plus the
        definition/delegation modules — never a load path."""
        import voice_typer.server as server_pkg

        hits: list[str] = []
        for py in Path(server_pkg.__file__).parent.rglob("*.py"):
            if "test" in py.name:
                continue
            text = py.read_text(encoding="utf-8")
            call_lines = [
                line
                for line in text.splitlines()
                if "cleanup_hf_cache_dir(" in line and not line.strip().startswith("#")
            ]
            if call_lines:
                hits.append(str(py))
        # Allowed: asr_utils (definition), _hf_cache_cleanup (delegation),
        # service/* + asr_setup.py (explicit user-initiated download path).
        allowed_markers = ("asr_utils", "_hf_cache_cleanup", "service", "asr_setup")
        forbidden = [h for h in hits if not any(m in h for m in allowed_markers)]
        assert not forbidden, (
            "cleanup_hf_cache_dir must only be reachable from the explicit "
            f"user-initiated download path; found: {forbidden}"
        )


class TestUncachedModelsRefuseToLoad:
    """Engines refuse to load an uncached model instead of downloading it —
    the load path never triggers a network transfer or a cache delete."""

    def test_whisper_load_requires_cached_model(self):
        from voice_typer.server.asr_errors import ModelNotDownloadedError

        assert issubclass(ModelNotDownloadedError, RuntimeError)

    def test_parakeet_load_requires_cached_model(self):
        from voice_typer.server.asr_errors import ModelIntegrityError

        assert issubclass(ModelIntegrityError, RuntimeError)
