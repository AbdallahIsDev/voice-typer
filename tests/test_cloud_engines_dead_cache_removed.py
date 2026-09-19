"""FR-6 (P4-A1): regression test: the dead cached-engine"""

from __future__ import annotations

import inspect

import pytest


class TestCachedEngineInfrastructureRemoved:
    """FR-6: every element of the dead cached-engine infrastructure"""

    @pytest.mark.parametrize(
        "name",
        [
            "_CACHED_ENGINES",
            "_CACHED_ENGINES_LOCK",
            "register_cached_cloud_engine",
            "get_cached_cloud_engine",
            "clear_cached_engine",
            "clear_all_cached_engines",
        ],
    )
    def test_name_absent_from_module_namespace(self, name: str):
        """a module attribute. If this test fails, someone re-introduced"""
        from voice_typer.server import cloud_engines

        assert not hasattr(cloud_engines, name), (
            f"FR-6 regression: cloud_engines.{name} was re-introduced. The "
            "cached-engine infrastructure was deliberately removed as dead "
            "code (zero production callers). Re-introduction requires wiring "
            "invalidation into config_applier.set_config for "
            "openai_api_key / groq_api_key / deepgram_api_key / "
            "cloud_api_key, see the FR-6 docblock at the top of "
            "cloud_engines.py."
        )

    def test_removed_names_not_importable(self):
        """``from voice_typer.server.cloud_engines import <name>``"""
        from voice_typer.server import cloud_engines

        for name in (
            "register_cached_cloud_engine",
            "get_cached_cloud_engine",
            "clear_cached_engine",
            "clear_all_cached_engines",
        ):
            assert not hasattr(cloud_engines, name), f"FR-6 regression: cloud_engines.{name} was re-introduced."
            # ``getattr`` on a module with a missing attribute raises
            _sentinel = object()
            assert getattr(cloud_engines, name, _sentinel) is _sentinel, (
                f"FR-6 regression: cloud_engines.{name} resolved to a real "
                "attribute, the dead cached-engine infrastructure was "
                "re-introduced."
            )

    def test_source_does_not_mention_cache_infrastructure(self):
        """removed cache (defensive against a partial revert that leaves"""
        import voice_typer.server.cloud_engines as cloud_engines

        source = inspect.getsource(cloud_engines)
        for symbol in (
            "_CACHED_ENGINES",
            "register_cached_cloud_engine",
            "get_cached_cloud_engine",
            "clear_cached_engine",
            "clear_all_cached_engines",
        ):
            occurrences = source.count(symbol)
            # The  docblock references each symbol 1x; the test
            assert occurrences <= 3, (
                f"FR-6 regression: symbol {symbol!r} appears {occurrences} "
                "times in cloud_engines.py source. Expected ≤3 (docblock + "
                "incidental mentions). A higher count suggests the dead "
                "cache was re-introduced, see the FR-6 docblock for "
                "the re-introduction requirements."
            )

    def test_no_module_level_dict_cache_state(self):
        """FR-6: the module MUST NOT carry a module-level mutable"""
        import voice_typer.server.cloud_engines as cloud_engines

        source = inspect.getsource(cloud_engines)
        for line in source.splitlines():
            stripped = line.lstrip()
            # Skip indented (in-class / in-function) lines.
            if stripped is not line:
                continue
            if stripped.startswith("_CACHED"):
                pytest.fail(
                    f"FR-6 regression: module-level line {stripped!r} "
                    "introduces a _CACHED* attribute. The cached-engine "
                    "infrastructure was removed as dead code, see the "
                    "FR-6 docblock for re-introduction requirements."
                )


class TestPerTranscriptionLifecycleDocumented:
    """CloudEngine lifecycle is per-transcription, so a future contributor"""

    def test_source_documents_per_transcription_lifecycle(self):
        import voice_typer.server.cloud_engines as cloud_engines

        source = inspect.getsource(cloud_engines)
        assert "per-transcription" in source, (
            "cloud_engines.py must document that "
            "CloudEngine lifecycle is per-transcription (so future "
            "contributors know the cache was deleted on purpose)."
        )
        # source per C-STYLE-1); the prose assertion above is sufficient.
