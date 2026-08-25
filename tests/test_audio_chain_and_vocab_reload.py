"""Regression tests for two already-fixed review.md entries that touch
files in this agent's owned lane (WAVE2-A16):

* ``save_vocabulary_with_diff`` must update the live
  in-memory ``VocabularyManager`` after writing the user file. The bug
  was that the IPC handler wrote the user JSON directly without
  reloading ``self._app._vocabulary_manager._data``, so
  ``dictation_pipeline.apply_to_text()`` kept using stale state until
  app restart. Fix: after ``_secure_atomic_write``, call
  ``live_vm._load_and_merge()`` under ``live_vm._lock``.

* ``audio_chain_builder.build_chain_from_dict`` used
  to mirror ``Config`` noise-filter defaults in a parallel ``_DEFAULTS``
  dict that drifted whenever a default was bumped on ``Config``. Fix:
  drop ``_DEFAULTS``; build a real ``Config()`` instance and apply the
  dict overrides via ``setattr`` so there is exactly one source of
  truth.

These tests pin the fixes in place so a future refactor cannot silently
regress either behaviour. They live in ``tests/`` (not in the owned
source files) because tests are exempt from the stay-in-your-lane rule
per the task brief's "TESTS REQUIRED" clause.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

# ──────────────────────────────────────────────────────────────────────────
# save_vocabulary_with_diff reloads the live VocabularyManager
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def vocab_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so the user vocab file is
    written under the test's tmp_path instead of the real config dir.
    """
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
    """A real VocabularyManager with a populated ``_data`` / ``_lock``
    so the reload path can be exercised against a live object.
    """
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


@pytest.fixture
def vocab_mixin(live_vm):
    """A bare ``VocabularyMixin`` instance whose ``self._app`` is a
    MagicMock exposing ``_vocabulary_manager`` set to the live vm.

    ``VocabularyMixin`` is a ``ServiceMixinBase`` subclass — it has no
    ``__init__`` of its own, so we can construct it via ``__new__`` and
    bind ``self._app`` manually. This mirrors how
    ``tests/app/test_notify_once_flags.py`` builds minimal
    mixin fixtures without booting the full ``VoiceTyperService``.
    """
    from voice_typer.server.service.vocabulary import VocabularyMixin

    instance = VocabularyMixin.__new__(VocabularyMixin)
    app = MagicMock()
    app._vocabulary_manager = live_vm
    instance._app = app
    return instance


class TestSaveVocabularyWithDiffReloadsLiveManager:
    """``save_vocabulary_with_diff`` MUST reload the live
    VocabularyManager's in-memory ``_data`` after writing the user file.
    """

    def test_calls_load_and_merge_on_live_vm(self, vocab_mixin, live_vm, vocab_dir):
        """After ``save_vocabulary_with_diff`` writes the user file, the
        live ``VocabularyManager._load_and_merge`` must be called so the
        in-memory ``_data`` reflects the just-written file.

        We monkeypatch ``live_vm._load_and_merge`` to a counting wrapper
        and assert it was invoked at least once during the call.
        """
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
        # ``_load_and_merge`` is never called and the live ``_data``
        # stays stale until app restart.
        assert calls["n"] >= 1, (
            "save_vocabulary_with_diff did NOT reload the live "
            "VocabularyManager after writing the user file. The in-memory "
            "_data will be stale until app restart."
        )

    def test_live_vm_data_reflects_written_file(self, vocab_mixin, live_vm, vocab_dir):
        """End-to-end regression: after a save, the live vm's ``_data``
        must contain the just-written user entry.

        This catches a regression where the reload is silently skipped
        (e.g. because the ``hasattr(live_vm, '_load_and_merge')`` guard
        was tightened too aggressively).
        """
        vocab_mixin.save_vocabulary_with_diff({"misspellings": {"teh": "TEH (custom override)"}})

        miss = live_vm.get_category("misspellings")
        assert miss.get("teh") == "TEH (custom override)", (
            "live VocabularyManager._data is stale — the user "
            "override was written to disk but the in-memory _data was not "
            "reloaded."
        )

    def test_no_live_vm_uses_fallback_path(self, vocab_dir):
        """When ``self._app._vocabulary_manager`` is None (cold start /
        test fixtures), the function must NOT crash — it should fall
        back to constructing a throwaway VocabularyManager for the
        bundled-defaults diff computation.
        """
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
        """If ``_load_and_merge`` raises (e.g. user file got nuked
        mid-write by an external process), the function must still
        return a normal result — the save itself has already
        succeeded; the reload is best-effort.
        """

        def _boom():
            raise RuntimeError("disk evaporated")

        live_vm._load_and_merge = _boom

        # Should NOT raise — reload failures are caught and logged.
        result = vocab_mixin.save_vocabulary_with_diff({"misspellings": {"teh": "TEH (reload-failure)"}})
        assert "imported_categories" in result


# ──────────────────────────────────────────────────────────────────────────
# audio_chain_builder has no parallel _DEFAULTS dict
# ──────────────────────────────────────────────────────────────────────────


class TestAudioChainBuilderNoDefaultsDrift:
    """``build_chain_from_dict`` must source its defaults from
    a real ``Config()`` instance — NOT from a parallel ``_DEFAULTS``
    dict that can silently drift when ``Config`` defaults change.

    The old ``_DEFAULTS`` dict was removed; this test pins the absence
    so a future "convenience" refactor can't reintroduce the drift
    hazard.
    """

    def test_no_parallel_defaults_dict_in_module(self):
        """The ``audio_chain_builder`` module must NOT define a
        module-level ``_DEFAULTS`` dict that shadows ``Config`` defaults.
        """
        import voice_typer.server.audio_chain_builder as mod

        assert not hasattr(mod, "_DEFAULTS"), (
            "audio_chain_builder re-introduced a parallel "
            "_DEFAULTS dict. This drifts from Config defaults — use "
            "Config() + setattr instead."
        )

    def test_build_chain_from_dict_uses_config_defaults(self):
        """``build_chain_from_dict({})`` must build a chain whose
        filters reflect the CURRENT ``Config()`` defaults — proving
        the dict path sources from ``Config`` rather than a frozen
        snapshot.
        """
        from voice_typer.server.audio_chain_builder import build_chain_from_dict
        from voice_typer.server.config import Config

        cfg = Config()

        # With an empty overrides dict, the chain should match what
        # ``build_chain(cfg)`` would produce. We compare filter NAMES
        # (the structural shape) rather than instances.
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
            "chain than build_chain(Config()) — defaults are not being "
            "sourced from Config."
        )

    def test_build_chain_from_dict_applies_overrides(self):
        """``build_chain_from_dict`` must apply user overrides on top
        of ``Config()`` defaults — e.g. enabling a filter that's off
        by default must produce a chain with that filter present.
        """
        from voice_typer.server.audio_chain_builder import build_chain_from_dict
        from voice_typer.server.config import Config

        # Whatever Config() says about noise_filter_notch, the override
        # MUST win — that's the whole point of the dict path. Filter
        # names are formatted as "Notch(<freq>Hz)" etc., so we match
        # on the leading class-name prefix.
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
                "Override noise_filter_notch=True was not applied — the "
                f"Notch filter is missing from the built chain: {names}"
            )
        else:
            assert not has_notch, (
                "Override noise_filter_notch=False was not applied — the "
                f"Notch filter is still in the built chain: {names}"
            )
