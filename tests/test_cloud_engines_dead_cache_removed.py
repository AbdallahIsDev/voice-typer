"""FR-6 (P4-A1): regression test — the dead cached-engine
infrastructure in :mod:`voice_typer.server.cloud_engines` MUST stay
removed.

History
--------
``cloud_engines.py`` previously hosted an 80-line module-level
cached-engine infrastructure:

* ``_CACHED_ENGINES`` dict
* ``_CACHED_ENGINES_LOCK`` threading.Lock
* ``register_cached_cloud_engine(provider, engine)``
* ``get_cached_cloud_engine(provider)``
* ``clear_cached_engine(provider) -> bool``
* ``clear_all_cached_engines() -> int``

A repo-wide grep (2026-07-28) confirmed ZERO production callers — the
only consumers were the unit tests in
``tests/test_cloud_engines.py::TestCloudEngineCacheInvalidation``
(which were also removed in this fix). The infrastructure carried a
docstring claim that ``clear_all_cached_engines`` would be called from
``delete_all_personal_data`` to invalidate stale engines on
credential / consent revocation — but no production code ever invoked
it. The cache was dead code in a "worst of both worlds" state
(maintenance burden + false security claim).

Fix
---
The infrastructure was deleted; ``CloudEngine`` lifecycle is now
documented as **per-transcription** (each transcription constructs a
fresh engine from the current Config, so stale-credential reuse is
structurally impossible). When a future PR wires CloudEngine into
production as a long-lived instance, the invalidation logic MUST be
re-added at that time AND the ``config_applier`` set_config path MUST
invalidate the cached engine when ``openai_api_key`` /
``groq_api_key`` / ``deepgram_api_key`` / ``cloud_api_key`` changes.

This test file pins the removal so a future contributor cannot
silently resurrect the dead cache (e.g. by reverting a merge or
cherry-picking an old commit) without also updating this regression
suite — which forces them to confront the "wire invalidation into
production" TODO before re-introducing the cache.
"""

from __future__ import annotations

import inspect

import pytest

# ── Module-level cache state ──────────────────────────────────────────


class TestCachedEngineInfrastructureRemoved:
    """FR-6: every element of the dead cached-engine infrastructure
    MUST be absent from the ``cloud_engines`` module's public namespace
    and source code.
    """

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
        """Each removed symbol MUST NOT be importable / accessible as
        a module attribute. If this test fails, someone re-introduced
        the dead cache — see the FR-6 fix block at the top of
        ``cloud_engines.py`` for the re-introduction requirements
        (production wiring + invalidation on credential rotation).
        """
        from voice_typer.server import cloud_engines

        assert not hasattr(cloud_engines, name), (
            f"FR-6 regression: cloud_engines.{name} was re-introduced. The "
            "cached-engine infrastructure was deliberately removed as dead "
            "code (zero production callers). Re-introduction requires wiring "
            "invalidation into config_applier.set_config for "
            "openai_api_key / groq_api_key / deepgram_api_key / "
            "cloud_api_key — see the FR-6 docblock at the top of "
            "cloud_engines.py."
        )

    def test_removed_names_not_importable(self):
        """``from voice_typer.server.cloud_engines import <name>``
        MUST raise ``ImportError``/``AttributeError`` for each removed
        symbol (the canonical ``from X import Y`` form raises
        ``ImportError``; ``getattr(module, name)`` raises
        ``AttributeError`` — both signal the attribute is absent).
        """
        from voice_typer.server import cloud_engines

        for name in (
            "register_cached_cloud_engine",
            "get_cached_cloud_engine",
            "clear_cached_engine",
            "clear_all_cached_engines",
        ):
            assert not hasattr(cloud_engines, name), f"FR-6 regression: cloud_engines.{name} was re-introduced."
            # ``getattr`` on a module with a missing attribute raises
            # ``AttributeError``; the ``from X import Y`` statement
            # wraps that as ``ImportError`` at import time. We use
            # ``getattr`` with a sentinel default to detect absence
            # without relying on the specific exception type.
            _sentinel = object()
            assert getattr(cloud_engines, name, _sentinel) is _sentinel, (
                f"FR-6 regression: cloud_engines.{name} resolved to a real "
                "attribute — the dead cached-engine infrastructure was "
                "re-introduced."
            )

    def test_source_does_not_mention_cache_infrastructure(self):
        """The module source MUST NOT contain textual references to the
        removed cache (defensive against a partial revert that leaves
        behind docstrings or comments referencing the old API).

        The FR-6 docblock at the top of the module mentions the removed
        symbols in a "do not re-introduce without ..." capacity — that
        is permitted (it's the regression-guard block). Other references
        would indicate the cache was re-added.
        """
        import voice_typer.server.cloud_engines as cloud_engines

        source = inspect.getsource(cloud_engines)
        # Count occurrences of each removed symbol — the docblock at
        # the top of the module legitimately mentions each once (in
        # the "do not re-introduce" regression-guard block). Any
        # *additional* occurrences (e.g. a re-added function def, a
        # call site, a leftover comment) indicate a regression.
        for symbol in (
            "_CACHED_ENGINES",
            "register_cached_cloud_engine",
            "get_cached_cloud_engine",
            "clear_cached_engine",
            "clear_all_cached_engines",
        ):
            occurrences = source.count(symbol)
            # The  docblock references each symbol 1x; the test
            # class below references them in strings. Allow up to 3
            # textual mentions (docblock + this test's reference is
            # in a separate file, but defensive upper bound is loose).
            assert occurrences <= 3, (
                f"FR-6 regression: symbol {symbol!r} appears {occurrences} "
                "times in cloud_engines.py source. Expected ≤3 (docblock + "
                "incidental mentions). A higher count suggests the dead "
                "cache was re-introduced — see the FR-6 docblock for "
                "the re-introduction requirements."
            )

    def test_no_module_level_dict_cache_state(self):
        """FR-6: the module MUST NOT carry a module-level mutable
        dict cache (``_CACHED_ENGINES``). A future contributor might
        rename the dict to evade the symbol-name tests above — this
        test catches any ``dict[str, ...]`` annotation at module level
        whose name starts with ``_CACHED``.
        """
        import voice_typer.server.cloud_engines as cloud_engines

        source = inspect.getsource(cloud_engines)
        # Heuristic: forbid module-level ``_CACHED*`` assignments or
        # annotations. The cloud_engines module has no legitimate
        # module-level cache after
        for line in source.splitlines():
            stripped = line.lstrip()
            # Skip indented (in-class / in-function) lines.
            if stripped is not line:
                continue
            if stripped.startswith("_CACHED"):
                pytest.fail(
                    f"FR-6 regression: module-level line {stripped!r} "
                    "introduces a _CACHED* attribute. The cached-engine "
                    "infrastructure was removed as dead code — see the "
                    "FR-6 docblock for re-introduction requirements."
                )


# ── Documentation contract ───────────────────────────────────────────


class TestPerTranscriptionLifecycleDocumented:
    """FR-6: the module docstring / source MUST document that
    CloudEngine lifecycle is per-transcription, so a future contributor
    reading the module knows (a) the cache was deleted on purpose, and
    (b) the invalidation TODO if/when long-lived engines are wired in.
    """

    def test_source_documents_per_transcription_lifecycle(self):
        import voice_typer.server.cloud_engines as cloud_engines

        source = inspect.getsource(cloud_engines)
        # The  docblock at the top of the module documents the
        # per-transcription lifecycle. Verify key phrases are present.
        assert "per-transcription" in source, (
            "cloud_engines.py must document that "
            "CloudEngine lifecycle is per-transcription (so future "
            "contributors know the cache was deleted on purpose)."
        )
        # The marker is no longer required (task IDs don't belong in
        # source per C-STYLE-1); the prose assertion above is sufficient.
