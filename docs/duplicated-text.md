# Duplicated Text — Known Causes and Workarounds

## Status

Dictation sometimes produces doubled text (e.g. "... do this **this**
today ..." or "... do this **do this** today ..."). The root cause is
timestamp drift in the hidden streaming transcription path: when the
same audio is transcribed twice (once by the streaming windows, once by
the finalize tail-merge), Whisper's word timestamps can shift by more
than 0.25 s between runs, which defeats the near-duplicate filter and
lets the same word be committed twice.

The four most likely root causes are ranked below. Most users hit cause
#1 or #2; causes #3–#5 are diagnostic stepping stones for harder cases.
If you just want to **stop the bug now**, jump to [Workarounds](#workarounds).

## Causes

| # | Cause | Location | Probability | Impact |
|---|-------|----------|-------------|--------|
| 1 | Tail merge timestamp drift | `streaming.py` → `_finalize_impl()` | HIGH | Words in the overlap region are duplicated when Whisper timestamps drift > 0.25 s between the streaming pass and the finalize tail pass. |
| 2 | Window overlap dedup gaps | `streaming.py` → `AudioWindowPlanner.next_window()` | MED-HIGH | The same audio is transcribed twice; dedup misses drifted timestamps when the boundary search fails to find a silence boundary. |
| 3 | VAD segment boundary split | `transcription.py` → `_transcribe_words_unlocked()` | MED | Whisper's internal VAD splits audio into segments; the last word of segment N may be repeated as the first word of segment N+1 (known faster-whisper behavior with `vad_filter=True` + `speech_pad_ms=200`). |
| 4 | Text cleanup limited to 4-token phrases | `text_cleanup.py` → `_remove_adjacent_duplicate_phrases()` | MED | Phrase duplicates longer than 4 tokens (e.g. "I want to do this **I want to do this**") are NOT caught. |
| 5 | Streaming fallback to batch transcribe | `streaming.py` → `_finalize_impl()` exception handler | LOW | If the tail merge raises and the code falls back to `transcribe_with_fallback(full_audio)`, the entire audio is transcribed from scratch as a batch — partial streaming commits may then duplicate the batch result. |

### Detail — cause #1 (tail merge timestamp drift)

The critical flow when streaming transcription is enabled:

1. During recording, streaming transcribes overlapping audio windows.
   Words with `end_seconds > commit_horizon` (the "unsafe tail") are
   held back.
2. When recording stops, `finalize(full_audio)` is called:
   - It takes a snapshot of committed words and `last_committed_time`.
   - It transcribes the tail audio from
     `last_committed_time - 3.0 s` (3 s overlap).
   - It filters: `new_tail_words = [w for w in words if w.end_seconds > merge_boundary]`.

The bug: Whisper produces slightly different word timestamps on
different runs (even for the same audio). The tail transcription re-runs
Whisper on the overlap region, and words from there might get timestamps
that differ by more than 0.25 s from their streaming timestamps.

Concrete example of the duplication:

- Streaming window 2 transcribed "this" with `start=12.0, end=12.5` →
  added to `_words`; `_seen_timestamps` records `(12.0, 12.5)`.
- Finalize tail transcribes the same audio; Whisper produces "this"
  with `start=11.7, end=12.2` (drifted by 0.3 s).
- Timestamp-key `(11.7, 12.2)` is NOT in `_seen_timestamps`.
- Near-duplicate check: `|11.7 - 12.0| = 0.3 > 0.25` → **miss**.
- DUPLICATE ADDED.
- Final result: "... do this **this** today ..." (or worse: "... do
  this **do this** today ..." if multiple words drift).

The 0.25 s threshold is too tight for Whisper's timestamp variability,
especially on small models.

### Detail — cause #2 (window planner boundary mismatch)

The planner uses `_choose_boundary()` to find a silent boundary for the
window end. But:

1. `_choose_boundary()` only searches 1 second
   (`search_seconds = min(1.0, ...)`) for a silence boundary.
2. If no quiet boundary is found, it returns `requested_end_seconds`
   exactly — meaning the windows overlap by exactly
   `left_overlap_seconds` (3 s).
3. Same audio content gets transcribed twice, and the dedup relies
   entirely on Whisper producing identical timestamps.

Window 1 transcribes `audio[0:12 s]` → text for `t=0–12 s`.
Window 2 transcribes `audio[9:17 s]` → text for `t=9–17 s`.
The overlap region `[9 s:12 s]` is transcribed both times. If Whisper
shifts timestamps by more than 0.25 s between runs — which it
frequently does — the same words from the overlap region get added
twice.

### Detail — cause #3 (VAD segment boundary split)

When `word_timestamps=True` (used by streaming), Whisper's internal VAD
splits audio into segments. At segment boundaries:

- The last word of segment N might be repeated as the first word of
  segment N+1.
- This is a known faster-whisper behavior when `vad_filter=True` with
  `speech_pad_ms=200`.
- `speech_pad_ms=200` adds 200 ms of extra context around each VAD
  segment, which can cause boundary words to appear in two adjacent
  segments.

The streaming path uses `word_timestamps=True, without_timestamps=False`.
The final path uses `without_timestamps=True`. These are different
transcribe modes — Whisper can produce different text for the same
audio depending on whether word timestamps are enabled.

### Detail — cause #4 (text-cleanup gaps)

| Function | What it catches | What it misses |
|----------|-----------------|----------------|
| `_remove_adjacent_duplicate_phrases` | Exact phrase repeats up to 4 tokens | Repeats > 4 words; repeats with punctuation variation; non-adjacent repeats |
| `_remove_near_duplicate_words` | One word is substring of adjacent (both ≥ 4 chars, length diff ≤ 2) | Substring matches where words are 1–3 chars; diff > 2 chars |
| `_clean_self_corrections` | Prefix/suffix overlap (e.g. "talk talking") | Doesn't catch exact word duplicates like "the the" — only handles prefix/suffix variants |

Critical gap: `_remove_adjacent_duplicate_phrases` is capped at 4
tokens. If a 5-token phrase gets duplicated (e.g. "I want to do this
**I want to do this**"), it won't be caught.

## Workarounds

- **Set `VOICE_TYPER_STREAMING=0`** to disable the hidden streaming
  transcription path entirely. This forces every recording to use the
  single-pass batch transcribe path (the same one that powers
  `without_timestamps=True`), which has no overlap region and therefore
  no overlap-region duplication. This is the highest-signal workaround:
  if the duplication disappears with streaming disabled, the bug is
  cause #1 or #2 above.
  - Trade-off: you lose the streaming UX win (no partial-transcription
    feedback during recording) and the first-word latency goes up
    slightly (no warm window). For most users this is acceptable.
- **Switch to a larger model** (e.g. `medium.en` or `large-v3`) — the
  timestamp drift is most pronounced on `tiny`/`base`/`small` models.
  Larger models produce more stable timestamps, which narrows the
  drift band below the 0.25 s near-duplicate threshold.
- **Reduce background noise** — Whisper's VAD splits more aggressively
  on noisy audio, which widens the segment-boundary split window
  (cause #3). A quieter input signal reduces VAD-induced segment
  splits.
- **Avoid rapid stop/start cycles** — the streaming tail-merge
  exception handler (cause #5) only fires when the tail merge raises,
  which is rare but slightly more likely under rapid stop/start
  pressure. A 1-second pause between recordings eliminates this path.

## Fix roadmap (for contributors)

When you're ready to fix this properly (instead of working around it),
the recommended starting points are:

1. **Increase the near-duplicate threshold** from 0.25 s to 0.5 s in
   `streaming.py`'s `_has_near_duplicate_unlocked()`. This widens the
   dedup window enough to absorb typical Whisper drift without letting
   through genuine adjacent repeats.
2. **Add text-level dedup in `_finalize_impl()`** — after `add_words`,
   compare the new tail words against `snapshot_committed_text` and
   discard any new words that form a duplicate of the last N committed
   words. This is a backstop that catches cause #1 even when the
   timestamp-level check misses.
3. **Investigate VAD segment-boundary dedup** for cause #3 — the
   segment-boundary repeat is a known faster-whisper behavior; a
   text-level dedup pass that drops the first word of segment N+1 if
   it exactly matches the last word of segment N would close the gap.
