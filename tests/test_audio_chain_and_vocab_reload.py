"""Regression tests for two already-fixed review.md entries that touch"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def vocab_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so the user vocab file is"""
    return tmp_config_dir


@pytest.fixture
def bundled(tmp_path):
    """Minimal bundled corrections.json used by VocabularyManager."""
    data = {
        "misspellings": {"teh": "the", "recieve": "receive"},
        "phrase_corrections": [["voice to 2 text", "voice to text"]],
        "extra_word_patterns": [["without whether", "whether"]],
        "technical_terms": {},
        "names": {},
        "products": {},
    }
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def live_vm(vocab_dir, bundled):
    """A real VocabularyManager with a populated ``_data`` / ``_lock``"""
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


@pytest.fixture
def vocab_mixin(live_vm):
    """MagicMock exposing ``_vocabulary_manager`` set to the live vm."""
    from voice_typer.server.service.vocabulary import VocabularyMixin

    instance = VocabularyMixin.__new__(VocabularyMixin)
    app = MagicMock()
    app._vocabulary_manager = live_vm
    instance._app = app
    return instance


class TestSaveVocabularyWithDiffReloadsLiveManager:
    """``save_vocabulary_with_diff`` MUST reload the live"""

    def test_calls_load_and_merge_on_live_vm(self, vocab_mixin, live_vm, vocab_dir):
        """in-memory ``_data`` reflects the just-written file."""
        original = live_vm._load_and_merge
        calls = {"n": 0}

        def _counting_load_and_merge():
            calls["n"] += 1
            return original()

        live_vm._load_and_merge = _counting_load_and_merge

        # Sanity: before the call, the counter is 0.
        assert calls["n"] == 0

        vocab_mixin.save_vocabulary_with_diff({"misspellings": {"teh": "TEH (custom)"}})

        # The reload MUST have happened. Without the fix,
        assert calls["n"] >= 1, (
            "save_vocabulary_with_diff did NOT reload the live "
            "VocabularyManager after writing the user file. The in-memory "
            "_data will be stale until app restart."
        )

    def test_live_vm_data_reflects_written_file(self, vocab_mixin, live_vm, vocab_dir):
        """End-to-end regression: after a save, the live vm's ``_data``"""
        vocab_mixin.save_vocabulary_with_diff({"misspellings": {"teh": "TEH (custom override)"}})

        miss = live_vm.get_category("misspellings")
        assert miss.get("teh") == "TEH (custom override)", (
            "live VocabularyManager._data is stale, the user "
            "override was written to disk but the in-memory _data was not "
            "reloaded."
        )

    def test_no_live_vm_uses_fallback_path(self, vocab_dir):
        """When ``self._app._vocabulary_manager`` is None (cold start /"""
        from voice_typer.server.service.vocabulary import VocabularyMixin

        instance = VocabularyMixin.__new__(VocabularyMixin)
        app = MagicMock()
        # Cold-start path: no live vm.
        app._vocabulary_manager = None
        instance._app = app

        # Should not raise.
        result = instance.save_vocabulary_with_diff({"misspellings": {"teh": "TEH (cold-start)"}})
        assert isinstance(result, dict)
        assert "imported_categories" in result

    def test_reload_failure_does_not_break_save(self, vocab_mixin, live_vm, vocab_dir):
        """If ``_load_and_merge`` raises (e.g. user file got nuked"""

        def _boom():
            raise RuntimeError("disk evaporated")

        live_vm._load_and_merge = _boom

        # Should NOT raise, reload failures are caught and logged.
        result = vocab_mixin.save_vocabulary_with_diff({"misspellings": {"teh": "TEH (reload-failure)"}})
        assert "imported_categories" in result


class TestAudioChainBuilderNoDefaultsDrift:
    """a real ``Config()`` instance, NOT from a parallel ``_DEFAULTS``"""

    def test_no_parallel_defaults_dict_in_module(self):
        """module-level ``_DEFAULTS`` dict that shadows ``Config`` defaults."""
        import voice_typer.server.audio_chain_builder as mod

        assert not hasattr(mod, "_DEFAULTS"), (
            "audio_chain_builder re-introduced a parallel "
            "_DEFAULTS dict. This drifts from Config defaults, use "
            "Config() + setattr instead."
        )

    def test_build_chain_from_dict_uses_config_defaults(self):
        """``build_chain_from_dict({})`` must build a chain whose"""
        from voice_typer.server.audio_chain_builder import build_chain_from_dict
        from voice_typer.server.config import Config

        cfg = Config()

        # With an empty overrides dict, the chain should match what
        chain_from_dict = build_chain_from_dict({})
        chain_from_config = build_chain_from_dict(
            {
                "noise_filter_highpass": cfg.noise_filter_highpass,
                "noise_filter_notch": cfg.noise_filter_notch,
                "noise_suppression_method": cfg.noise_suppression_method,
                "noise_filter_gate": cfg.noise_filter_gate,
                "noise_filter_eq": cfg.noise_filter_eq,
                "noise_filter_compressor": cfg.noise_filter_compressor,
                "noise_filter_limiter": cfg.noise_filter_limiter,
            }
        )

        assert chain_from_dict.filter_names == chain_from_config.filter_names, (
            "build_chain_from_dict({}) produced a different "
            "chain than build_chain(Config()), defaults are not being "
            "sourced from Config."
        )

    def test_build_chain_from_dict_applies_overrides(self):
        """``build_chain_from_dict`` must apply user overrides on top"""
        from voice_typer.server.audio_chain_builder import build_chain_from_dict
        from voice_typer.server.config import Config

        # Whatever Config() says about noise_filter_notch, the override
        cfg = Config()
        opposite = not cfg.noise_filter_notch

        chain = build_chain_from_dict(
            {
                "noise_filter_notch": opposite,
                "noise_filter_highpass": False,
                "noise_suppression_method": "none",
                "noise_filter_gate": False,
                "noise_filter_eq": False,
                "noise_filter_compressor": False,
                "noise_filter_limiter": False,
            }
        )
        names = chain.filter_names
        has_notch = any(n.startswith("Notch(") for n in names)
        if opposite:
            assert has_notch, (
                "Override noise_filter_notch=True was not applied, the "
                f"Notch filter is missing from the built chain: {names}"
            )
        else:
            assert not has_notch, (
                "Override noise_filter_notch=False was not applied, the "
                f"Notch filter is still in the built chain: {names}"
            )


class TestBuildChainFilterOrder:
    """Pins the ACTUAL construction order of ``build_chain``."""

    def test_notch_runs_before_highpass(self):
        """rumble filter)."""
        from voice_typer.server.audio_chain_builder import build_chain_from_dict

        chain = build_chain_from_dict(
            {
                "noise_filter_notch": True,
                "noise_filter_highpass": True,
                "noise_suppression_method": "none",
                "noise_filter_gate": False,
                "noise_filter_eq": False,
                "noise_filter_compressor": False,
                "noise_filter_limiter": False,
            }
        )
        names = chain.filter_names
        prefixes = [n.split("(")[0] for n in names]
        assert prefixes == ["Notch", "HighPass"], f"expected Notch before HighPass, got {names}"

    def test_full_chain_order_all_filters_enabled(self):
        """All filters on → the chain order must be"""
        from voice_typer.server.audio_chain_builder import build_chain_from_dict
        from voice_typer.server.config import Config

        cfg = Config()
        chain = build_chain_from_dict(
            {
                "noise_filter_notch": True,
                "noise_filter_highpass": cfg.noise_filter_highpass,
                "noise_suppression_method": cfg.noise_suppression_method,
                "noise_filter_gate": cfg.noise_filter_gate,
                "noise_filter_eq": cfg.noise_filter_eq,
                "noise_filter_compressor": cfg.noise_filter_compressor,
                "noise_filter_limiter": cfg.noise_filter_limiter,
            }
        )
        prefixes = [n.split("(")[0] for n in chain.filter_names]
        assert prefixes == [
            "Notch",
            "HighPass",
            "NoiseSuppressor",
            "NoiseGate",
            "EQ",
            "Compressor",
            "Limiter",
        ], f"unexpected chain order: {prefixes}"

    def test_build_chain_docstring_matches_actual_order(self):
        """code builds: notch FIRST (the doc drift this pins said \"after"""
        import inspect

        from voice_typer.server.audio_chain_builder import build_chain

        doc = inspect.getdoc(build_chain) or ""
        assert "Notch" in doc and "HighPass" in doc
        # The docstring's order line must list Notch before HighPass.
        order_line = next(
            (ln for ln in doc.splitlines() if "Notch" in ln and "→" in ln),
            None,
        )
        assert order_line is not None, (
            "build_chain docstring has no chain-order line naming Notch, "
            "it must document where the optional notch filter sits."
        )
        assert order_line.index("Notch") < order_line.index("HighPass"), (
            "build_chain docstring must list Notch BEFORE HighPass (the code appends the notch filter first)."
        )
        assert "after HighPass" not in doc, (
            "build_chain docstring still claims the notch is added after the high-pass, the code appends it first."
        )
