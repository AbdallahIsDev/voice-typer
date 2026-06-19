Primary Cause (HIGH probability): Streaming Tail Merge Timestamp Drift
File:  voice_typer/server/streaming.py  —  _finalize_impl()  (line ~313)
The critical flow when streaming transcription is enabled:
1. During recording, streaming transcribes overlapping audio windows. Words with  end_seconds > commit_horizon  (the "unsafe tail") are held back.
2. When recording stops,  finalize(full_audio)  is called:
- It takes a snapshot of committed words and  last_committed_time 
- It transcribes the tail audio from  last_committed_time - 3.0s  (3s overlap)
- It filters:  new_tail_words = [word for word in words if word.end_seconds > merge_boundary] 
The bug: Whisper produces slightly different word timestamps on different runs (even for the same audio). The tail transcription re-runs Whisper on the overlap region, and words from there might get timestamps that differ by more than 0.25s from their streaming timestamps.
Concrete example of the duplication:
Streaming transcribes window at t=5-17s:
  "I" (end=10.2s), "want" (end=10.8s), "to" (end=11.3s), "do" (end=11.9s) 
  → all committed since end < 12-1.5=10.5... wait
 
Actually let me redo: 
  commit_horizon = window.end_seconds - right_guard_seconds (1.5s)
  
Window 1: 0-12s, commit_horizon = 10.5s
  "I want to do this today" 
  "today" at end=11.8s > 10.5s → held back (unsafe tail)
 
Window 2: 9-17s (3s overlap), commit_horizon = 15.5s
  "today" from this window: end=11.8s ≤ 15.5s → committed ✓
  "I want to do this today" 
  But "I" from window 2 has timestamps like start=9.1s, end=9.5s
  → near_duplicate check finds "I" from window 1 (start=9.0, end=9.4) within 0.25s → skipped ✓
 
Finalize: tail audio from last_committed_time - 3.0s (~12.3s - 3.0s = ~9.3s)
  Whisper transcribes the tail and returns words like:
  "I" (start=9.1, end=9.5) → end=9.5 > 12.3? NO → filtered out ✓
  "want" (start=9.6, end=10.1) → end=10.1 > 12.3? NO → filtered out ✓
  "to" (start=10.5, end=10.9) → end=10.9 > 12.3? NO → filtered out ✓
  "do" (start=11.2, end=11.7) → end=11.7 > 12.3? NO → filtered out ✓
  "this" (start=12.0, end=12.5) → end=12.5 > 12.3? YES → included!
  "today" (start=12.6, end=13.0) → end=13.0 > 12.3? YES → included!
Looks correct in theory. But Whisper timestamp drift changes everything:
Streaming window 2 transcribed "this" with end=12.5s
→ added to _words, _seen_timestamps has (12.0, 12.5)
 
Finalize tail transcribes the same audio from 9.3s
Whisper produces "this" with start=11.9, end=12.4 (drifted by 0.1s)
→ timestamp_key = (11.9, 12.4) → NOT in _seen_timestamps!
→ near_duplicate check: |11.9-12.0|=0.1 ≤ 0.25, |12.4-12.5|=0.1 ≤ 0.25 → detected ✓
 
But Whisper can drift by MORE than 0.25s:
Whisper produces "this" with start=11.7, end=12.2 (drifted by 0.3s)
→ |11.7-12.0|=0.3 > 0.25 → near_duplicate MISS!
→ DUPLICATE ADDED!
Final result: "... do this this today ..."
or worse: "... do this do this today ..." if multiple words drift
The 0.25s threshold is too tight for Whisper's timestamp variability, especially on small models.
────────────────────────────────────────────────────────────────────────────────
🚨 Secondary Cause (MEDIUM-HIGH probability): AudioWindowPlanner Boundary Mismatch
File:  voice_typer/server/streaming.py  —  AudioWindowPlanner.next_window()  (line ~64)
The planner uses  _choose_boundary()  to find a silent boundary for window end. But:
1.  _choose_boundary  only searches 1 second ( search_seconds = min(1.0, ...) ) for a silence boundary
2. If no quiet boundary is found, it returns  requested_end_seconds  exactly — meaning the windows overlap by exactly  left_overlap_seconds  (3s)
3. Same audio content gets transcribed twice, and the dedup relies entirely on Whisper producing identical timestamps
Window 1: audio[0:12s]  → transcripts t=0-12s
Window 2: audio[9:17s]  → transcripts t=9-17s (3s overlap)
The overlap region  [9s:12s]  is transcribed both times.  _add_words_unlocked  checks  _seen_timestamps  (exact round to 3 decimal places) and  _has_near_duplicate_unlocked  (0.25s window). If Whisper shifts timestamps by more than 0.25s between runs — which it frequently does — the same words from the overlap region get added twice.
────────────────────────────────────────────────────────────────────────────────
⚠️ Tertiary Cause (MEDIUM probability): Whisper VAD Segment Boundary Split
File:  voice_typer/server/transcription.py  —  _transcribe_words_unlocked()  (line ~318)
When  word_timestamps=True  (used by streaming), Whisper's internal VAD splits audio into segments. At segment boundaries:
- The last word of segment N might be repeated as the first word of segment N+1
- This is a known faster-whisper behavior when  vad_filter=True  with  speech_pad_ms=200 
- The  speech_pad_ms=200  adds 200ms of extra context around each VAD segment, which can cause boundary words to appear in two adjacent segments
The streaming path ( transcribe_words ) uses:
// python
word_timestamps=True,
without_timestamps=False,
While the final path ( transcribe  /  transcribe_with_fallback ) uses:
// python
without_timestamps=True,
These are different transcribe modes — Whisper can produce different text for the same audio depending on whether word timestamps are enabled.
────────────────────────────────────────────────────────────────────────────────
⚠️ Fourth Cause (MEDIUM probability): Text Cleanup Gaps
File:  voice_typer/server/text_cleanup.py 
┌────────────────────────────────────┬───────────────────────────────────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────┐
│ Function                           │ What it catches                                                   │ What it misses                                                                           │
├────────────────────────────────────┼───────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
│ _remove_adjacent_duplicate_phrases │ Exact phrase repeats up to 4 tokens                               │ Repeats >4 words; repeats with punctuation variation; non-adjacent repeats               │
│ _remove_near_duplicate_words       │ One word is substring of adjacent (both ≥4 chars, length diff ≤2) │ Substring matches where words are 1-3 chars; diff >2 chars                               │
│ _clean_self_corrections            │ Prefix/suffix overlap (e.g. "talk talking")                       │ Doesn't catch exact word duplicates like "the the" — only handles prefix/suffix variants │
└────────────────────────────────────┴───────────────────────────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────┘
Critical gap:  _remove_adjacent_duplicate_phrases  max 4 tokens. If a 5-token phrase gets duplicated (e.g. "I want to do this I want to do this"), it won't be caught.
────────────────────────────────────────────────────────────────────────────────
📋 Fifth Cause (LOW probability): Performance Throttling on Bubble Level
File:  voice_typer/server/app.py  —  _wire_waveform_bubble()  (line ~250)
The bubble level callback throttles to ~30 Hz. This is unrelated to text duplication — purely visual.
────────────────────────────────────────────────────────────────────────────────
📋 Sixth Cause (LOW probability): Rapid Stop/Start Cycle
File:  voice_typer/server/app.py  —  toggle_dictation()  →  _start_dictation()  →  _stop_dictation() 
-  toggle_dictation()  checks  not self._busy_event.is_set()  and returns early if busy
-  _start_dictation()  checks  self.recorder.recording  and returns early if already recording
- These guards prevent the most obvious race conditions
However, if the streaming tail merge errors out and falls back to  transcribe_with_fallback(full_audio) , the entire audio gets transcribed from scratch as a batch — meaning the streaming work was wasted, and the batch transcription might produce different text than what streaming partially committed.
────────────────────────────────────────────────────────────────────────────────
Summary Table
┌─────┬─────────────────────────────────────────┬────────────────────────────────────────────────────┬─────────────┬─────────────────────────────────────────────────────────────────────────┐
│ #   │ Cause                                   │ Location                                           │ Probability │ Impact                                                                  │
├─────┼─────────────────────────────────────────┼────────────────────────────────────────────────────┼─────────────┼─────────────────────────────────────────────────────────────────────────┤
│ 1   │ Tail merge timestamp drift              │ streaming.py:_finalize_impl                        │ HIGH        │ Words in overlap region duplicated when Whisper timestamps drift >0.25s │
│ 2   │ Window overlap dedup gaps               │ streaming.py:AudioWindowPlanner                    │ MED-HIGH    │ Same audio transcribed twice, dedup misses drifted timestamps           │
│ 3   │ VAD segment boundary split              │ transcription.py:_transcribe_words_unlocked        │ MED         │ Last word of segment repeated as first word of next segment             │
│ 4   │ Text cleanup limited to 4-token phrases │ text_cleanup.py:_remove_adjacent_duplicate_phrases │ MED         │ Longer phrase duplicates not caught                                     │
│ 5   │ Streaming fallback to batch transcribe  │ streaming.py:_finalize_impl exception handler      │ LOW         │ Full batch + streaming partial text could produce duplicates            │
└─────┴─────────────────────────────────────────┴────────────────────────────────────────────────────┴─────────────┴─────────────────────────────────────────────────────────────────────────┘
Most likely root cause for "I want to do this, I want to do this" pattern: Cause #1 or #2 — the tail merge produces slightly different word timestamps for the same audio, bypassing the 0.25s near-duplicate check, and the words get committed twice into the final text.
────────────────────────────────────────────────────────────────────────────────
Now you have a complete picture. When you're ready to fix, I'd recommend starting with:
1. Increase the near-duplicate threshold from 0.25s to 0.5s in  streaming.py:_has_near_duplicate_unlocked 
2. Add text-level dedup in  _finalize_impl  — after  add_words , compare against  snapshot_committed_text  and discard any new words that form a duplicate of the last N committed words