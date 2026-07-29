"""DJ-21: ``StreamingTextAssembler._seen_timestamps`` must have a hard
size cap so a runaway ``finalize()`` (which passes
``commit_horizon_seconds=math.inf`` and so bypasses the pruning path)
cannot grow the dedup set without bound.

Pre-fix, ``_words`` was bounded by a ``deque(maxlen=_MAX_WORDS=10000)``
but ``_seen_timestamps`` was a plain ``set`` with no maxlen — its only
eviction path was ``_prune_old_entries``, which short-circuits on
``math.isfinite(commit_horizon_seconds)``. ``finalize()`` passes
``math.inf``, so the set grew by one entry per unique (start, end)
tuple added during the tail-merge. The asymmetry between the bounded
deque and the unbounded set was a latent footgun.

The fix adds a ``_MAX_SEEN_TIMESTAMPS = 50000`` hard cap: when an
``_add_words_unlocked`` call would push the set over the cap, the set
is reset to a fresh empty set. Dedup is best-effort (a missed duplicate
just produces a duplicate word, which is rare and handled downstream by
the near-duplicate detector), so the reset is safe.
"""

from __future__ import annotations

import math

from voice_typer.server.streaming import StreamingTextAssembler, WordTiming


def _populate_set_directly(assembler: StreamingTextAssembler, count: int) -> None:
    """Pre-populate ``_seen_timestamps`` with ``count`` fake entries.

    Going through ``add_words`` for 50000+ entries is slow (each call
    runs the near-duplicate detector and writes to ``_word_key_index``).
    We bypass that by writing directly to the set under the lock —
    the DJ-21 cap check looks at ``len(self._seen_timestamps)`` so the
    source of the entries doesn't matter.
    """
    with assembler._lock:
        for i in range(count):
            assembler._seen_timestamps.add((float(i), float(i) + 0.5))


def test_seen_timestamps_hard_cap_prevents_unbounded_growth():
    """DJ-21: when ``_seen_timestamps`` exceeds ``_MAX_SEEN_TIMESTAMPS``,
    the next ``_add_words_unlocked`` call resets it to a fresh empty set.
    """
    assembler = StreamingTextAssembler()
    cap = StreamingTextAssembler._MAX_SEEN_TIMESTAMPS
    assert cap == 50000, "DJ-21: cap must be 50000 (per fix spec)"

    # Pre-populate the set to just over the cap (bypassing add_words
    # for speed — see helper docstring).
    _populate_set_directly(assembler, cap + 1)
    assert len(assembler._seen_timestamps) == cap + 1

    # The next add_words call must see the set over the cap and reset
    # it to a fresh set, then add the new timestamp.
    word = WordTiming("trigger", start_seconds=99999.0, end_seconds=99999.5)
    assembler.add_words([word], commit_horizon_seconds=math.inf)

    # After the reset + 1 new add, the set must be well under the cap.
    assert len(assembler._seen_timestamps) < cap, (
        f"DJ-21: _seen_timestamps must be reset when it exceeds the "
        f"{cap} cap; got len={len(assembler._seen_timestamps)} after "
        f"a call that started at len={cap + 1}."
    )
    # The set must contain the most recent entry (the reset happened
    # at the start of the call, then the new timestamp was added).
    assert len(assembler._seen_timestamps) >= 1, (
        "DJ-21: post-reset, the new timestamp must have been added to the fresh set."
    )


def test_seen_timestamps_below_cap_not_reset():
    """DJ-21 supplemental: the reset only fires when the cap is EXCEEDED.
    Below the cap, the set grows normally (no spurious reset).
    """
    assembler = StreamingTextAssembler()
    cap = StreamingTextAssembler._MAX_SEEN_TIMESTAMPS

    # Populate to a value well below the cap.
    _populate_set_directly(assembler, 100)
    assert len(assembler._seen_timestamps) == 100

    # Adding one more word must NOT trigger a reset.
    word = WordTiming("word", start_seconds=200.0, end_seconds=200.5)
    assembler.add_words([word], commit_horizon_seconds=math.inf)

    assert len(assembler._seen_timestamps) == 101, (
        "DJ-21: below the cap, _seen_timestamps must grow normally (no spurious reset)."
    )
    assert cap > 101, "DJ-21: test setup invariant — cap must be > 101"


def test_seen_timestamps_cap_resets_to_fresh_set():
    """DJ-21: the reset replaces the set with a fresh ``set()``
    instance (not a clear), so any external references to the OLD set
    don't see further mutations.

    This pins the implementation detail: ``self._seen_timestamps = set()``
    (rebind) rather than ``self._seen_timestamps.clear()`` (in-place).
    Both are functionally equivalent for the dedup logic, but the
    rebind is cheaper (no need to enumerate the old set's entries).
    """
    assembler = StreamingTextAssembler()
    cap = StreamingTextAssembler._MAX_SEEN_TIMESTAMPS

    _populate_set_directly(assembler, 10)
    original_set = assembler._seen_timestamps
    assert len(original_set) == 10

    # Push past the cap to trigger the reset.
    _populate_set_directly(assembler, cap + 1)
    assert len(assembler._seen_timestamps) == cap + 1
    # The original_set reference still points to the small 10-entry
    # set because _populate_set_directly uses .add on the SAME set
    # object (it didn't rebind). Now trigger the cap reset via add_words.
    word = WordTiming("trigger", start_seconds=99999.0, end_seconds=99999.5)
    assembler.add_words([word], commit_horizon_seconds=math.inf)

    # ``_seen_timestamps`` must now be a DIFFERENT object (the rebind
    # replaced it with a fresh set).
    assert assembler._seen_timestamps is not original_set, (
        "DJ-21: cap reset must rebind to a fresh set() (not clear in place)."
    )
    assert len(assembler._seen_timestamps) < cap


def test_seen_timestamps_cap_does_not_break_committed_text():
    """DJ-21: after a cap-triggered reset, the assembler still produces
    correct ``committed_text`` (dedup is best-effort — a missed
    duplicate just produces a duplicate word, which the near-duplicate
    detector handles).
    """
    assembler = StreamingTextAssembler()
    cap = StreamingTextAssembler._MAX_SEEN_TIMESTAMPS

    # Commit one real word BEFORE the cap reset.
    word_before = WordTiming("before_reset", start_seconds=1.0, end_seconds=1.5)
    assembler.add_words([word_before], commit_horizon_seconds=math.inf)

    # Fill past the cap.
    _populate_set_directly(assembler, cap + 10)

    # Commit one real word AFTER the cap reset.
    word_after = WordTiming("after_reset", start_seconds=2.0, end_seconds=2.5)
    assembler.add_words([word_after], commit_horizon_seconds=math.inf)

    text = assembler.committed_text
    assert "before_reset" in text, "DJ-21: words committed before the cap reset must remain in committed_text."
    assert "after_reset" in text, "DJ-21: words committed after the cap reset must be in committed_text."
