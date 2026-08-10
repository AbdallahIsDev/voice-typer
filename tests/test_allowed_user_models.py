"""CR-38: regression tests for ``ALLOWED_USER_MODELS`` validation.

The previous ``ALLOWED_USER_MODELS`` set was ``{"tiny.en", "small.en",
"medium.en", "qwen", "parakeet"}`` — only English-only Whisper variants
plus qwen + parakeet. But ``OnboardingController.MODEL_OPTIONS``
explicitly offers the multilingual variants ``"tiny"``, ``"small"``,
``"medium"`` (no ``.en`` suffix), and the renderer's model catalog
(``model_registry.MODEL_REGISTRY``) defines the same.

``Config.load()`` enforces ``if data.get("model_size") not in
ALLOWED_USER_MODELS: data["model_size"] = "small.en"`` — so a user who
picked ``"small"`` (multilingual) in onboarding had their choice
silently reset to ``"small.en"`` (English-only) on the next launch.
Non-English users lost their language support after the first restart
with no signal beyond a debug log line.

The fix extends ``ALLOWED_USER_MODELS`` to include the multilingual
variants. The regression test asserts every name in
``OnboardingController.MODEL_OPTIONS`` is in ``ALLOWED_USER_MODELS``
so the allowlist can't drift from the onboarding picker.
"""

from __future__ import annotations

import json

import pytest


class TestAllowedUserModels:
    """CR-38: ALLOWED_USER_MODELS must include all multilingual
    variants offered in OnboardingController.MODEL_OPTIONS."""

    def test_multilingual_models_in_allowed_set(self):
        """The multilingual Whisper variants (no .en suffix) must be
        in ALLOWED_USER_MODELS."""
        from voice_typer.server.config_validators import ALLOWED_USER_MODELS

        assert "tiny" in ALLOWED_USER_MODELS, (
            "CR-38 regression: 'tiny' (multilingual) is not in "
            "ALLOWED_USER_MODELS — non-English users who pick this "
            "model in onboarding silently get English-only Whisper "
            "after the first restart."
        )
        assert "small" in ALLOWED_USER_MODELS, (
            "CR-38 regression: 'small' (multilingual) is not in "
            "ALLOWED_USER_MODELS — non-English users who pick this "
            "model in onboarding silently get English-only Whisper "
            "after the first restart."
        )
        assert "medium" in ALLOWED_USER_MODELS, (
            "CR-38 regression: 'medium' (multilingual) is not in "
            "ALLOWED_USER_MODELS — non-English users who pick this "
            "model in onboarding silently get English-only Whisper "
            "after the first restart."
        )

    def test_every_onboarding_model_option_is_allowed(self):
        """Every name in OnboardingController.MODEL_OPTIONS must be in
        ALLOWED_USER_MODELS — otherwise Config.load() silently resets
        the user's choice to 'small.en' on the next launch.

        This is the durable regression test: if a new model is added
        to MODEL_OPTIONS but not to ALLOWED_USER_MODELS, this test
        fails. The two must stay in sync.
        """
        from voice_typer.server.config_validators import ALLOWED_USER_MODELS
        from voice_typer.server.onboarding import OnboardingController

        model_option_names = {option["name"] for option in OnboardingController.MODEL_OPTIONS}
        disallowed = model_option_names - ALLOWED_USER_MODELS
        assert not disallowed, (
            "CR-38 regression: these OnboardingController.MODEL_OPTIONS "
            f"names are NOT in ALLOWED_USER_MODELS: {disallowed}. "
            "Config.load() would silently reset each to 'small.en' on "
            "the next launch — non-English users lose language support."
        )

    @pytest.mark.parametrize("model_size", ["tiny", "small", "medium", "tiny.en", "small.en", "medium.en", "parakeet"])
    def test_load_preserves_multilingual_model_choice(self, tmp_config_dir, model_size):
        """Config.load() must preserve the user's model choice when
        it's a valid multilingual variant — it must NOT silently
        reset to 'small.en'."""
        from voice_typer.server.config import Config

        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"model_size": model_size}))

        c = Config.load()
        assert c.model_size == model_size, (
            f"CR-38 regression: Config.load() reset model_size from "
            f"'{model_size}' to '{c.model_size}' — the multilingual "
            "variants must be preserved (non-English users depend on them)."
        )

    def test_load_still_normalizes_truly_unsupported_models(self, tmp_config_dir):
        """Sanity check: Config.load() still normalizes truly
        unsupported model names to 'small.en' (CR-38 fix did not
        disable validation entirely — it just extended the allowlist
        to include the multilingual variants)."""
        from voice_typer.server.config import Config

        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"model_size": "unsupported-model-name"}))

        c = Config.load()
        assert c.model_size == "small.en", (
            f"Config.load() should normalize truly unsupported model names to 'small.en', but got '{c.model_size}'."
        )
