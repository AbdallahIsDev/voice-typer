"""Cross-engine chunk-seam parity: QwenEngine vs ParakeetEngine."""

from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_audio(seconds: float = 65.0, sample_rate: int = 16000) -> np.ndarray:
    """Deterministic non-silent audio long enough to trigger chunking."""
    n = int(seconds * sample_rate)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    return (0.1 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _qwen_merged(chunk_texts: list[str]) -> str:
    """Run QwenEngine's chunked path with the given per-chunk mock texts."""
    from voice_typer.server.qwen_engine import QwenEngine

    seconds = 40.0 if len(chunk_texts) == 2 else 65.0
    engine = QwenEngine(model_path="/fake/qwen/model")
    mock_model = MagicMock(name="qwen_model")
    mock_model.transcribe.side_effect = [[MagicMock(text=text)] for text in chunk_texts]
    engine._model = mock_model
    return engine.transcribe(_make_audio(seconds=seconds))


def _parakeet_merged(chunk_texts: list[str]) -> str:
    """Run ParakeetEngine's seam merge over the same chunk texts."""
    from voice_typer.server.parakeet_engine import ParakeetEngine

    engine = ParakeetEngine(device="cpu", language="en")
    return engine._merge_chunks(list(chunk_texts))


def _legacy_qwen_fork_merge(prev_text: str, curr_text: str, n: int = 3) -> str:
    """The removed Qwen-only fork (exact-case, punctuation-sensitive)."""
    prev_words = prev_text.split()
    curr_words = curr_text.split()
    if not prev_words or not curr_words:
        return curr_text
    max_k = min(n, len(prev_words), len(curr_words))
    for k in range(max_k, 0, -1):
        if prev_words[-k:] == curr_words[:k]:
            return " ".join(curr_words[k:])
    return curr_text


# Fixtures: (chunk texts as the mocked model decodes them).
PARITY_FIXTURES = [
    ("exact-overlap", ["the quick brown fox", "brown fox jumps over the lazy dog"]),
    ("mixed-case-punctuation", ["so this is The End.", "the end of the recording"]),
    # no overlap at all
    ("no-overlap", ["hello world", "foo bar baz"]),
    ("full-duplicate", ["the end of the story", "the story"]),
    ("empty-middle-chunk", ["first part here", "", "part here continues"]),
    ("three-chunk-cascade", ["a b c d e", "d e f g h", "f g h i j"]),
]


@pytest.mark.parametrize(
    "fixture_name,chunk_texts",
    PARITY_FIXTURES,
    ids=[name for name, _texts in PARITY_FIXTURES],
)
def test_qwen_and_parakeet_produce_identical_seams(fixture_name, chunk_texts):
    """Both local engines merge the same chunk texts to the same string."""
    assert _qwen_merged(chunk_texts) == _parakeet_merged(chunk_texts)


def test_mixed_case_fixture_defeated_the_removed_fork():
    """With the fork, \"The End.\" vs \"the end\" never matched (raw token"""
    prev, curr = "so this is The End.", "the end of the recording"
    fork_merged = prev + " " + _legacy_qwen_fork_merge(prev, curr)

    from voice_typer.server.asr_utils import merge_chunks

    canonical_merged = merge_chunks([prev, curr])
    assert canonical_merged != fork_merged
    assert canonical_merged == "so this is The End. of the recording"
    assert fork_merged == "so this is The End. the end of the recording"
