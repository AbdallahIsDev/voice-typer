"""Tests for the model registry.

Verifies that:
- The catalog was pruned (2026-08-15) to the three Whisper variants
  (``tiny`` default, ``large-v3``, ``large-v3-turbo``) plus
  ``parakeet`` and ``qwen`` — the removed variants (tiny.en, base*,
  small*, medium*, large-v1/v2, turbo alias, distil-*) are GONE from
  the registry.
- ``get_model_metadata`` returns correctly-typed fields.
- ``get_all_models`` returns a list (not a dict).
- ``get_models_by_backend`` filters correctly.
- ``DEFAULT_MODEL_SIZE`` is a valid registry entry (the config
  dataclass default + load-time coercion reset target reference it).
- The turbo model has the expected metadata (size, speed, accuracy).
- Every entry carries a ``network_behavior`` field.

These tests are pure — no network, no file I/O, no model downloads.
Importing ``voice_typer.server.model_registry`` must be side-effect
free (no HuggingFace calls, no GPU init).
"""

from voice_typer.server.model_registry import (
    DEFAULT_MODEL_SIZE,
    MODEL_REGISTRY,
    ModelMetadata,
    get_all_models,
    get_model_metadata,
    get_models_by_backend,
)

# ── Expected catalog ─────────────────────────────────────────────────
#
# The Whisper family was pruned 2026-08-15 to the three multilingual
# variants the user wants: `tiny` (default), `large-v3`, and
# `large-v3-turbo` (`large-v3` was restored at the user's request the
# same day). Parakeet + Qwen are non-Whisper backends and remain.

_EXPECTED_WHISPER_VARIANTS = {"tiny", "large-v3", "large-v3-turbo"}

_REMOVED_WHISPER_VARIANTS = {
    "tiny.en",
    "base.en",
    "base",
    "small.en",
    "small",
    "medium.en",
    "medium",
    "large-v1",
    "large-v2",
    "large",
    "turbo",
    "distil-large-v3",
    "distil-medium.en",
}


class TestModelRegistryContainsWhisperVariants:
    """The pruned catalog contains exactly the three kept Whisper models."""

    def test_model_registry_contains_kept_whisper_variants(self):
        """Every variant in _EXPECTED_WHISPER_VARIANTS must be a key in
        MODEL_REGISTRY."""
        missing = _EXPECTED_WHISPER_VARIANTS - set(MODEL_REGISTRY.keys())
        assert not missing, f"MODEL_REGISTRY missing required variants: {sorted(missing)}"

    def test_removed_whisper_variants_are_gone(self):
        """None of the removed variants may appear in the registry (and
        ``get_model_metadata`` returns None for them)."""
        present = _REMOVED_WHISPER_VARIANTS & set(MODEL_REGISTRY.keys())
        assert not present, (
            f"Removed whisper variants still in MODEL_REGISTRY: {sorted(present)}. "
            "The catalog was pruned to tiny + large-v3 + large-v3-turbo."
        )
        for name in _REMOVED_WHISPER_VARIANTS:
            assert get_model_metadata(name) is None, f"{name} should have been removed from the registry"

    def test_registry_has_five_entries(self):
        """tiny + large-v3 + large-v3-turbo + parakeet + qwen = 5 entries."""
        assert len(MODEL_REGISTRY) == 5, f"Expected 5 models, got {len(MODEL_REGISTRY)}: {sorted(MODEL_REGISTRY)}"

    def test_default_model_size_is_a_valid_registry_entry(self):
        """DEFAULT_MODEL_SIZE must reference a model that exists (the
        config dataclass default + load-time reset target rely on it)."""
        assert DEFAULT_MODEL_SIZE in MODEL_REGISTRY, (
            f"DEFAULT_MODEL_SIZE={DEFAULT_MODEL_SIZE!r} is not a registry key — "
            "changing the default requires an entry that stays in MODEL_REGISTRY."
        )


class TestGetModelMetadataReturnsCorrectFields:
    """test_get_model_metadata_returns_correct_fields."""

    def test_get_model_metadata_returns_correct_fields(self):
        """Every required field is present with the correct type."""
        meta = get_model_metadata("tiny")
        assert meta is not None, "tiny should be in the registry"
        assert isinstance(meta.name, str) and meta.name == "tiny"
        assert isinstance(meta.download_size_mb, int) and meta.download_size_mb > 0
        assert isinstance(meta.required_vram_mb, int) and meta.required_vram_mb > 0
        assert isinstance(meta.backend, str) and meta.backend
        assert isinstance(meta.multilingual, bool)
        # supported_languages is Optional[list[str]]: either None or a
        # list of strings.
        assert meta.supported_languages is None or (
            isinstance(meta.supported_languages, list) and all(isinstance(x, str) for x in meta.supported_languages)
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

    def test_display_name_sets_detailed_model_names(self):
        """The Models page shows a detailed name under the family header:
        parakeet renders as ``Parakeet-TDT-0.6b-V3`` and qwen as ``Qwen-3``;
        whisper variants have no display_name and fall back to the bare
        name in the renderer."""
        assert get_model_metadata("parakeet").display_name == "Parakeet-TDT-0.6b-V3"
        assert get_model_metadata("qwen").display_name == "Qwen-3"
        for name in ("tiny", "large-v3", "large-v3-turbo"):
            assert get_model_metadata(name).display_name is None

    def test_to_dict_carries_display_name(self):
        """``to_dict()`` (the ``get_model_catalog`` IPC payload) includes
        ``display_name`` so the renderer can render the detailed name."""
        d = get_model_metadata("parakeet").to_dict()
        assert d["display_name"] == "Parakeet-TDT-0.6b-V3"

    def test_metadata_is_frozen(self):
        """Registry entries are immutable so they can be safely shared
        across threads (IPC + service layer) without copying."""
        meta = get_model_metadata("tiny")
        assert meta is not None
        try:
            meta.download_size_mb = 1  # type: ignore[misc]
        except Exception:
            # frozen dataclass raises FrozenInstanceError on setattr.
            return
        # If we get here, the dataclass is NOT frozen — that's a bug.
        raise AssertionError("ModelMetadata should be frozen=True but setattr succeeded")


class TestGetAllModelsReturnsList:
    """test_get_all_models_returns_list."""

    def test_get_all_models_returns_list(self):
        """get_all_models returns a list of ModelMetadata, not a dict."""
        all_models = get_all_models()
        assert isinstance(all_models, list), f"Expected list, got {type(all_models).__name__}"
        assert len(all_models) == len(MODEL_REGISTRY)
        for m in all_models:
            assert isinstance(m, ModelMetadata), f"Expected ModelMetadata, got {type(m).__name__}"

    def test_get_all_models_preserves_registry_order(self):
        """The list is in the same order as MODEL_REGISTRY.values()
        (the renderer renders in this order — tiny first)."""
        all_models = get_all_models()
        registry_order = list(MODEL_REGISTRY.values())
        assert [m.name for m in all_models] == [m.name for m in registry_order]


class TestGetModelsByBackendFiltersCorrectly:
    """test_get_models_by_backend_filters_correctly."""

    def test_get_models_by_backend_filters_correctly(self):
        """get_models_by_backend returns only models with the matching
        backend string."""
        whisper_models = get_models_by_backend("whisper")
        assert all(m.backend == "whisper" for m in whisper_models), "Found non-whisper backend in whisper filter"
        whisper_names = {m.name for m in whisper_models}
        assert whisper_names == {"tiny", "large-v3", "large-v3-turbo"}, (
            f"whisper backend must be exactly tiny + large-v3 + large-v3-turbo, got {sorted(whisper_names)}"
        )

        # distil-whisper backend has no models after the prune.
        distil_models = get_models_by_backend("distil-whisper")
        assert distil_models == [], "distil-whisper backend should have zero models after the catalog prune"

    def test_get_models_by_backend_returns_empty_for_unknown(self):
        """Unknown backends return an empty list (never None)."""
        result = get_models_by_backend("nonexistent-backend")
        assert result == []

    def test_get_models_by_backend_returns_list_type(self):
        """Return type is always list, even when empty."""
        result = get_models_by_backend("whisper")
        assert isinstance(result, list)


class TestLargeV3HasCorrectMetadata:
    """test_large-v3_has_correct_metadata."""

    def test_large_v3_has_correct_metadata(self):
        """The ``large-v3`` entry matches: 3000 MB download, 4096 MB
        VRAM, multilingual, slow, high accuracy."""
        meta = get_model_metadata("large-v3")
        assert meta is not None, "large-v3 missing from registry"
        assert meta.download_size_mb == 3000, f"expected download_size_mb=3000, got {meta.download_size_mb}"
        assert meta.required_vram_mb == 4096, f"expected required_vram_mb=4096, got {meta.required_vram_mb}"
        assert meta.multilingual is True, "expected multilingual=True"
        assert meta.supported_languages is None, "expected supported_languages=None (all languages)"
        assert meta.backend == "whisper", f"expected backend='whisper', got {meta.backend!r}"
        assert meta.speed_rating == "slow", f"expected speed_rating='slow', got {meta.speed_rating!r}"
        assert meta.accuracy_rating == "high", f"expected accuracy_rating='high', got {meta.accuracy_rating!r}"
        assert meta.is_distilled is False, "large-v3 is NOT a distilled variant"
        assert meta.repo_id == "Systran/faster-whisper-large-v3", (
            f"expected Systran/faster-whisper-large-v3, got {meta.repo_id}"
        )

    def test_large_v3_size_matches_model_size_mb(self):
        """The registry's download_size_mb must match the
        ``_MODEL_SIZE_MB`` table in asr_utils.py so the disk-space
        pre-check and the renderer's UI agree."""
        from voice_typer.server.asr_utils import _MODEL_SIZE_MB

        meta = get_model_metadata("large-v3")
        assert meta is not None
        assert _MODEL_SIZE_MB["large-v3"] == meta.download_size_mb, (
            f"_MODEL_SIZE_MB={_MODEL_SIZE_MB['large-v3']} but registry download_size_mb={meta.download_size_mb}"
        )


class TestLargeV3TurboHasCorrectMetadata:
    """test_large-v3-turbo_has_correct_metadata."""

    def test_large_v3_turbo_has_correct_metadata(self):
        """The ``large-v3-turbo`` entry matches: 809 MB download, 2000
        MB VRAM, multilingual, fast, high accuracy."""
        meta = get_model_metadata("large-v3-turbo")
        assert meta is not None, "large-v3-turbo missing from registry"
        assert meta.download_size_mb == 809, f"expected download_size_mb=809, got {meta.download_size_mb}"
        assert meta.required_vram_mb == 2000, f"expected required_vram_mb=2000, got {meta.required_vram_mb}"
        assert meta.multilingual is True, "expected multilingual=True"
        assert meta.supported_languages is None, "expected supported_languages=None (all languages)"
        assert meta.backend == "whisper", f"expected backend='whisper', got {meta.backend!r}"
        assert meta.speed_rating == "fast", f"expected speed_rating='fast', got {meta.speed_rating!r}"
        assert meta.accuracy_rating == "high", f"expected accuracy_rating='high', got {meta.accuracy_rating!r}"
        assert meta.is_distilled is False, "turbo is NOT a distilled variant"
        assert meta.repo_id == "Systran/faster-whisper-large-v3-turbo", (
            f"expected Systran/faster-whisper-large-v3-turbo, got {meta.repo_id}"
        )

    def test_turbo_size_matches_model_size_mb(self):
        """The registry's download_size_mb must match the
        ``_MODEL_SIZE_MB`` table in asr_utils.py so the disk-space
        pre-check and the renderer's UI agree."""
        from voice_typer.server.asr_utils import _MODEL_SIZE_MB

        meta = get_model_metadata("large-v3-turbo")
        assert meta is not None
        assert _MODEL_SIZE_MB["large-v3-turbo"] == meta.download_size_mb, (
            f"_MODEL_SIZE_MB={_MODEL_SIZE_MB['large-v3-turbo']} but registry download_size_mb={meta.download_size_mb}"
        )

    def test_turbo_alias_removed(self):
        """The ``turbo`` alias was removed with the catalog prune — only
        the explicit ``large-v3-turbo`` name remains."""
        assert get_model_metadata("turbo") is None, (
            "'turbo' alias should have been removed (only large-v3-turbo remains)"
        )


class TestRemovedVariantsNoLongerMarkedDistilled:
    """Post-prune: no distil entries exist, and the kept models are not
    marked distilled."""

    def test_distil_models_removed(self):
        """distil-large-v3 / distil-medium.en are gone from the registry."""
        for name in ("distil-large-v3", "distil-medium.en"):
            assert get_model_metadata(name) is None, f"{name} should have been removed"

    def test_kept_whisper_models_not_marked_distilled(self):
        """The kept Whisper variants are NOT marked as distilled."""
        for name in ("tiny", "large-v3", "large-v3-turbo"):
            meta = get_model_metadata(name)
            assert meta is not None
            assert meta.is_distilled is False, f"{name}: should NOT be marked is_distilled"


# network_behavior field ─────────────────────────────────


class TestModelMetadataHasNetworkBehaviorField:
    """every ``ModelMetadata`` carries a ``network_behavior``
    field that honestly declares the model's network activity."""

    _ALLOWED_VALUES = {
        "local-only",
        "downloads-on-first-use-consent-gated",
        "downloads-on-first-use-no-consent",
        "cloud-per-call",
    }

    def test_field_exists_on_dataclass(self):
        """``ModelMetadata`` declares a ``network_behavior`` field."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(ModelMetadata)}
        assert "network_behavior" in field_names, (
            f"ModelMetadata must declare a `network_behavior` field. Found fields: {sorted(field_names)}"
        )

    def test_default_is_local_only(self):
        """A dataclass constructed without ``network_behavior`` defaults
        to ``\"local-only\"`` — the safest assumption (offline)."""
        meta = ModelMetadata(
            name="probe",
            download_size_mb=1,
            required_vram_mb=1,
            backend="probe-backend",
            multilingual=False,
            supported_languages=["en"],
            description="probe",
            repo_id="probe/repo",
        )
        assert meta.network_behavior == "local-only", (
            "Default network_behavior must be 'local-only' (safest "
            "offline assumption); an entry that downloads must "
            "override explicitly so the catalog cannot silently "
            "misrepresent a download as offline."
        )

    def test_every_registry_entry_has_a_valid_value(self):
        """Every model in ``MODEL_REGISTRY`` sets ``network_behavior``
        to one of the allowed values."""
        for name, meta in MODEL_REGISTRY.items():
            assert isinstance(meta.network_behavior, str), (
                f"{name}: network_behavior must be a str, got {type(meta.network_behavior).__name__}"
            )
            assert meta.network_behavior in self._ALLOWED_VALUES, (
                f"{name}: network_behavior={meta.network_behavior!r} is not one of {sorted(self._ALLOWED_VALUES)}"
            )

    def test_whisper_backend_is_consent_gated(self):
        """The kept Whisper variants download from HuggingFace on first
        use and are consent-gated (the user clicks 'Download')."""
        for name in ("tiny", "large-v3", "large-v3-turbo"):
            meta = get_model_metadata(name)
            assert meta is not None, f"{name} missing from registry"
            assert meta.network_behavior == "downloads-on-first-use-consent-gated", (
                f"{name}: expected 'downloads-on-first-use-consent-gated' "
                f"(Whisper downloads from HF after user consent), got "
                f"{meta.network_behavior!r}"
            )

    def test_parakeet_is_consent_gated(self):
        """Parakeet downloads are gated on explicit consent."""
        meta = get_model_metadata("parakeet")
        assert meta is not None, "parakeet missing from registry"
        assert meta.network_behavior == "downloads-on-first-use-consent-gated", (
            "parakeet: expected 'downloads-on-first-use-consent-gated' "
            "(the ONNX migration made the download path require explicit "
            "consent)."
        )

    def test_qwen_is_local_only(self):
        """Qwen is local-only — the user must manually configure the
        model path in Settings."""
        meta = get_model_metadata("qwen")
        assert meta is not None, "qwen missing from registry"
        assert meta.network_behavior == "local-only", (
            f"qwen: expected 'local-only' (user supplies the model path manually), got {meta.network_behavior!r}"
        )
        assert "Auto-downloaded" not in meta.description, (
            f"qwen description must not say 'Auto-downloaded'. Got: {meta.description!r}"
        )
        assert "Requires manual model path setup" in meta.description, (
            f"qwen description must say 'Requires manual model path setup in Settings'. Got: {meta.description!r}"
        )

    def test_to_dict_includes_network_behavior(self):
        """``to_dict()`` (used for IPC transport to the renderer)
        includes ``network_behavior`` so the Models page can display it."""
        meta = get_model_metadata("tiny")
        assert meta is not None
        d = meta.to_dict()
        assert "network_behavior" in d, (
            "to_dict() must include network_behavior so the renderer "
            "can show the model's network behavior on the Models page."
        )
        assert d["network_behavior"] == meta.network_behavior
