"""Regression tests for ``ALLOWED_USER_MODELS`` validation.

``ALLOWED_USER_MODELS`` is derived from
``model_registry.MODEL_REGISTRY`` (the canonical catalog). The catalog
was pruned 2026-08-15 to the three Whisper variants the user wants —
``tiny`` (the default), ``large-v3``, and ``large-v3-turbo`` — plus
``parakeet`` and ``qwen``. Every other legacy name (``tiny.en``,
``base``, ``small``, ``medium.en``, ``turbo``, distil-*) is now
invalid and gets reset to ``DEFAULT_MODEL_SIZE`` by ``Config.load()``.

The regression test asserts every name in
``OnboardingController.MODEL_OPTIONS`` is in ``ALLOWED_USER_MODELS``
so the allowlist can't drift from the onboarding picker.
"""

from __future__ import annotations

import json

import pytest
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE


class TestAllowedUserModels:
    """ALLOWED_USER_MODELS must include every model offered in
    OnboardingController.MODEL_OPTIONS."""

    def test_kept_whisper_models_in_allowed_set(self):
        """The three kept Whisper variants must be in ALLOWED_USER_MODELS."""
        from voice_typer.server.config_validators import ALLOWED_USER_MODELS

        assert "tiny" in ALLOWED_USER_MODELS, (
            "'tiny' (the default model) is not in ALLOWED_USER_MODELS — "
            "the default itself would be reset on load."
        )
        assert "large-v3" in ALLOWED_USER_MODELS, (
            "'large-v3' is not in ALLOWED_USER_MODELS — users "
            "who pick it would be silently reset."
        )
        assert "large-v3-turbo" in ALLOWED_USER_MODELS, (
            "'large-v3-turbo' is not in ALLOWED_USER_MODELS — users "
            "who pick it would be silently reset."
        )

    def test_removed_models_not_in_allowed_set(self):
        """Models removed by the 2026-08-15 catalog prune must NOT be
        selectable."""
        from voice_typer.server.config_validators import ALLOWED_USER_MODELS

        for removed in ("tiny.en", "small.en", "medium.en", "base", "small", "medium", "turbo", "distil-large-v3"):
            assert removed not in ALLOWED_USER_MODELS, (
                f"{removed!r} was removed from the catalog (2026-08-15) "
                "but is still in ALLOWED_USER_MODELS."
            )

    def test_every_onboarding_model_option_is_allowed(self):
        """Every name in OnboardingController.MODEL_OPTIONS must be in
        ALLOWED_USER_MODELS — otherwise Config.load() silently resets
        the user's choice on the next launch."""
        from voice_typer.server.config_validators import ALLOWED_USER_MODELS
        from voice_typer.server.onboarding import OnboardingController

        model_option_names = {option["name"] for option in OnboardingController.MODEL_OPTIONS}
        disallowed = model_option_names - ALLOWED_USER_MODELS
        assert not disallowed, (
            "OnboardingController.MODEL_OPTIONS names that are NOT in "
            f"ALLOWED_USER_MODELS: {disallowed}. Config.load() would "
            f"silently reset each to {DEFAULT_MODEL_SIZE!r} on the next launch."
        )

    @pytest.mark.parametrize("model_size", ["tiny", "large-v3", "large-v3-turbo", "parakeet", "qwen"])
    def test_load_preserves_valid_model_choice(self, tmp_config_dir, model_size):
        """Config.load() must preserve a valid model choice — it must
        NOT silently reset it."""
        from voice_typer.server.config import Config

        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"model_size": model_size}))

        c = Config.load()
        assert c.model_size == model_size, (
            f"Config.load() reset model_size from '{model_size}' to "
            f"'{c.model_size}' — valid choices must be preserved."
        )

    def test_load_still_normalizes_truly_unsupported_models(self, tmp_config_dir):
        """Sanity check: Config.load() still normalizes unsupported
        model names to the canonical DEFAULT_MODEL_SIZE."""
        from voice_typer.server.config import Config

        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"model_size": "unsupported-model-name"}))

        c = Config.load()
        assert c.model_size == DEFAULT_MODEL_SIZE, (
            f"Config.load() should normalize unsupported model names to "
            f"{DEFAULT_MODEL_SIZE!r}, but got '{c.model_size}'."
        )
