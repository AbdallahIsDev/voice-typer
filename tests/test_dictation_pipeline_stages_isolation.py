"""Stage-list isolation between dictation pipelines.

``dictation_stages.build_default_stages`` promises "a fresh list so
callers can mutate (insert/remove stages) without affecting other
pipelines". The orchestrator builds the 11-stage template once and
every ``DictationPipeline`` instance must receive its OWN list copy —
a shared mutable list would let one pipeline's (or test's)
insert/remove corrupt every other pipeline's run loop.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline
from voice_typer.server.dictation_stages import build_default_stages


def _make_pipeline() -> DictationPipeline:
    """Real ``__init__`` (not ``__new__``), the sharing lives there."""
    return DictationPipeline(MagicMock())


class TestStageListIsolation:
    def test_each_pipeline_gets_its_own_list_object(self):
        first = _make_pipeline()
        second = _make_pipeline()
        assert first._stages is not second._stages
        assert [s.name for s in first._stages] == [s.name for s in second._stages]

    def test_mutating_one_pipeline_stages_leaves_other_intact(self):
        first = _make_pipeline()
        second = _make_pipeline()
        before = [s.name for s in second._stages]
        first._stages.pop()
        first._stages.insert(0, first._stages[0])
        assert [s.name for s in second._stages] == before
        assert len(first._stages) == len(before)

    def test_factory_returns_fresh_list_each_call(self):
        assert build_default_stages() is not build_default_stages()
