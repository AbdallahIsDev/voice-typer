"""Tests for the model registry (NEW-MODEL-001).

Verifies that:
- All Whisper variants (including the new turbo + distilled models)
  are present in the registry.
- ``get_model_metadata`` returns correctly-typed fields.
- ``get_all_models`` returns a list (not a dict).
- ``get_models_by_backend`` filters correctly.
- The turbo model has the expected metadata (size, speed, accuracy).
- Distil models are marked ``is_distilled=True`` and use the
  ``distil-whisper`` backend.

These tests are pure — no network, no file I/O, no model downloads.
Importing ``voice_typer.server.model_registry`` must be side-effect
free (no HuggingFace calls, no GPU init).
"""

from voice_typer.server.model_registry import (
    MODEL_REGISTRY,
    ModelMetadata,
    get_all_models,
    get_model_metadata,
    get_models_by_backend,
)

# ── Expected variants ───────────────────────────────────────────────
#
# Source of truth: the spec in Task 9 (P6).  Any drift between this
# list and MODEL_REGISTRY is a regression.

_EXPECTED_WHISPER_VARIANTS = {
    # Original Whisper variants (Systran/faster-whisper-*)
    "tiny.en", "tiny", "base.en", "base",
    "small.en", "small", "medium.en", "medium",
    "large-v1", "large-v2", "large-v3", "large",
    # NEW-MODEL-001: turbo + distilled
    "large-v3-turbo", "turbo",
    "distil-large-v3", "distil-medium.en",
}


class TestModelRegistryContainsAllWhisperVariants:
    """test_model_registry_contains_all_whisper_variants."""

    def test_model_registry_contains_all_whisper_variants(self):
        """Every variant in _EXPECTED_WHISPER_VARIANTS must be a key in
        MODEL_REGISTRY.  Extra keys are allowed (forward-compat) but
        missing keys are a regression."""
        missing = _EXPECTED_WHISPER_VARIANTS - set(MODEL_REGISTRY.keys())
        assert not missing, (
            f"MODEL_REGISTRY missing required variants: {sorted(missing)}"
        )

    def test_model_registry_has_at_least_16_entries(self):
        """Sanity check: 12 original + 4 new = 16 minimum."""
        assert len(MODEL_REGISTRY) >= 16, (
            f"Expected >=16 models, got {len(MODEL_REGISTRY)}"
        )


class TestGetModelMetadataReturnsCorrectFields:
    """test_get_model_metadata_returns_correct_fields."""

    def test_get_model_metadata_returns_correct_fields(self):
        """Every required field is present with the correct type."""
        meta = get_model_metadata("small.en")
        assert meta is not None, "small.en should be in the registry"
        # Required fields per the ModelMetadata dataclass.
        assert isinstance(meta.name, str) and meta.name == "small.en"
        assert isinstance(meta.download_size_mb, int) and meta.download_size_mb > 0
        assert isinstance(meta.required_vram_mb, int) and meta.required_vram_mb > 0
        assert isinstance(meta.backend, str) and meta.backend
        assert isinstance(meta.multilingual, bool)
        # supported_languages is Optional[list[str]]: either None or a
        # list of strings.
        assert meta.supported_languages is None or (
            isinstance(meta.supported_languages, list)
            and all(isinstance(x, str) for x in meta.supported_languages)
        )
        assert isinstance(meta.description, str) and meta.description
        assert isinstance(meta.repo_id, str) and "/" in meta.repo_id  # "org/name"
        assert isinstance(meta.is_distilled, bool)
        assert meta.speed_rating in ("fast", "medium", "slow")
        assert meta.accuracy_rating in ("low", "medium", "high")

    def test_get_model_metadata_returns_none_for_unknown(self):
        """Unknown model names return None — never raise."""
        assert get_model_metadata("not-a-real-model") is None
        assert get_model_metadata("") is None

    def test_metadata_is_frozen(self):
        """Registry entries are immutable so they can be safely shared
        across threads (IPC + service layer) without copying."""
        meta = get_model_metadata("large-v3")
        assert meta is not None
        try:
            meta.download_size_mb = 1  # type: ignore[misc]
        except Exception:
            # frozen dataclass raises FrozenInstanceError on setattr.
            return
        # If we get here, the dataclass is NOT frozen — that's a bug.
        raise AssertionError(
            "ModelMetadata should be frozen=True but setattr succeeded"
        )


class TestGetAllModelsReturnsList:
    """test_get_all_models_returns_list."""

    def test_get_all_models_returns_list(self):
        """get_all_models returns a list of ModelMetadata, not a dict."""
        all_models = get_all_models()
        assert isinstance(all_models, list), (
            f"Expected list, got {type(all_models).__name__}"
        )
        assert len(all_models) >= 16
        for m in all_models:
            assert isinstance(m, ModelMetadata), (
                f"Expected ModelMetadata, got {type(m).__name__}"
            )

    def test_get_all_models_preserves_registry_order(self):
        """The list is in the same order as MODEL_REGISTRY.values()
        (the renderer renders in this order — tiny first, distil last)."""
        all_models = get_all_models()
        registry_order = list(MODEL_REGISTRY.values())
        assert [m.name for m in all_models] == [m.name for m in registry_order]


class TestGetModelsByBackendFiltersCorrectly:
    """test_get_models_by_backend_filters_correctly."""

    def test_get_models_by_backend_filters_correctly(self):
        """get_models_by_backend returns only models with the matching
        backend string."""
        whisper_models = get_models_by_backend("whisper")
        assert all(m.backend == "whisper" for m in whisper_models), (
            "Found non-whisper backend in whisper filter"
        )
        # Must include the turbo variants (which use backend="whisper").
        whisper_names = {m.name for m in whisper_models}
        assert "large-v3-turbo" in whisper_names
        assert "turbo" in whisper_names

        distil_models = get_models_by_backend("distil-whisper")
        assert all(m.backend == "distil-whisper" for m in distil_models), (
            "Found non-distil backend in distil-whisper filter"
        )
        distil_names = {m.name for m in distil_models}
        assert distil_names == {"distil-large-v3", "distil-medium.en"}

    def test_get_models_by_backend_returns_empty_for_unknown(self):
        """Unknown backends return an empty list (never None)."""
        result = get_models_by_backend("nonexistent-backend")
        assert result == []

    def test_get_models_by_backend_returns_list_type(self):
        """Return type is always list, even when empty."""
        result = get_models_by_backend("whisper")
        assert isinstance(result, list)


class TestTurboModelHasCorrectMetadata:
    """test_turbo_model_has_correct_metadata."""

    def test_turbo_model_has_correct_metadata(self):
        """The ``turbo`` (alias) and ``large-v3-turbo`` entries match
        the spec: 809 MB download, 2000 MB VRAM, multilingual, fast,
        high accuracy."""
        for name in ("large-v3-turbo", "turbo"):
            meta = get_model_metadata(name)
            assert meta is not None, f"{name} missing from registry"
            assert meta.download_size_mb == 809, (
                f"{name}: expected download_size_mb=809, got {meta.download_size_mb}"
            )
            assert meta.required_vram_mb == 2000, (
                f"{name}: expected required_vram_mb=2000, got {meta.required_vram_mb}"
            )
            assert meta.multilingual is True, (
                f"{name}: expected multilingual=True"
            )
            assert meta.supported_languages is None, (
                f"{name}: expected supported_languages=None (all languages)"
            )
            assert meta.backend == "whisper", (
                f"{name}: expected backend='whisper', got {meta.backend!r}"
            )
            assert meta.speed_rating == "fast", (
                f"{name}: expected speed_rating='fast', got {meta.speed_rating!r}"
            )
            assert meta.accuracy_rating == "high", (
                f"{name}: expected accuracy_rating='high', got {meta.accuracy_rating!r}"
            )
            assert meta.is_distilled is False, (
                f"{name}: turbo is NOT a distilled variant"
            )
            assert meta.repo_id == "Systran/faster-whisper-large-v3-turbo", (
                f"{name}: expected Systran/faster-whisper-large-v3-turbo, "
                f"got {meta.repo_id}"
            )

    def test_turbo_alias_and_large_v3_turbo_share_repo_id(self):
        """``turbo`` is an alias for ``large-v3-turbo`` — same repo_id
        and download_size_mb."""
        turbo = get_model_metadata("turbo")
        full = get_model_metadata("large-v3-turbo")
        assert turbo is not None and full is not None
        assert turbo.repo_id == full.repo_id
        assert turbo.download_size_mb == full.download_size_mb

    def test_turbo_size_matches_model_size_mb(self):
        """The registry's download_size_mb must match the
        ``_MODEL_SIZE_MB`` table in transcription.py so the disk-space
        pre-check and the renderer's UI agree."""
        from voice_typer.server.transcription import _MODEL_SIZE_MB
        for name in ("large-v3-turbo", "turbo"):
            meta = get_model_metadata(name)
            assert meta is not None
            assert _MODEL_SIZE_MB[name] == meta.download_size_mb, (
                f"{name}: _MODEL_SIZE_MB={_MODEL_SIZE_MB[name]} but "
                f"registry download_size_mb={meta.download_size_mb}"
            )


class TestDistilModelsMarkedAsDistilled:
    """test_distil_models_marked_as_distilled."""

    def test_distil_models_marked_as_distilled(self):
        """``distil-large-v3`` and ``distil-medium.en`` have
        ``is_distilled=True`` and ``backend="distil-whisper"``."""
        for name in ("distil-large-v3", "distil-medium.en"):
            meta = get_model_metadata(name)
            assert meta is not None, f"{name} missing from registry"
            assert meta.is_distilled is True, (
                f"{name}: expected is_distilled=True"
            )
            assert meta.backend == "distil-whisper", (
                f"{name}: expected backend='distil-whisper', got {meta.backend!r}"
            )

    def test_distil_models_use_distil_repo_prefix(self):
        """Distil models use the ``Systran/faster-distil-whisper-*``
        repo prefix (NOT ``Systran/faster-whisper-*``)."""
        for name, expected_repo in [
            ("distil-large-v3", "Systran/faster-distil-whisper-large-v3"),
            ("distil-medium.en", "Systran/faster-distil-whisper-medium.en"),
        ]:
            meta = get_model_metadata(name)
            assert meta is not None
            assert meta.repo_id == expected_repo, (
                f"{name}: expected {expected_repo!r}, got {meta.repo_id!r}"
            )

    def test_distil_medium_en_is_english_only(self):
        """``distil-medium.en`` is English-only (not multilingual)."""
        meta = get_model_metadata("distil-medium.en")
        assert meta is not None
        assert meta.multilingual is False
        assert meta.supported_languages == ["en"]

    def test_distil_large_v3_is_multilingual(self):
        """``distil-large-v3`` is multilingual (distilled from
        large-v3 which is multilingual)."""
        meta = get_model_metadata("distil-large-v3")
        assert meta is not None
        assert meta.multilingual is True
        assert meta.supported_languages is None

    def test_non_distil_models_not_marked_distilled(self):
        """Standard Whisper variants are NOT marked as distilled."""
        for name in ("tiny.en", "small.en", "medium.en", "large-v3",
                     "large-v3-turbo", "turbo"):
            meta = get_model_metadata(name)
            assert meta is not None
            assert meta.is_distilled is False, (
                f"{name}: should NOT be marked is_distilled"
            )
